"""Adaptador WordPress: leer la REST API publica durante el rastreo (#33,
decision 161).

Lo que WordPress dice que publica (entradas, paginas, tipos propios, adjuntos,
categorias y etiquetas) frente a lo que el rastreo encuentra. El spider lee y
guarda; el analisis cruza y avisa (`analysis/avisos_de_wordpress.py`).

**Por que en el spider y no en el worker antes del analisis.** Cada peticion
hereda lo que el rastreo ya hace bien y una descarga aparte tendria que
repetir: el UA y la huella TLS del navegador (decisiones 11 y 132; un WAF
tipo F5 bloquea a `urllib`), el proxy, las cabeceras y credenciales del job
solo hacia el sitio (114), robots.txt en modo `respect`, el `Crawl-delay` (81)
y el `Retry-After` (111). Y corre a la vez que el rastreo, no despues: no
alarga el job. Nunca dentro de un request de la API.

Como las demas sondas (decisiones 105, 123 y 124), con su propio callback:

- **no gasta `max_urls`** ni cuenta para la salud: no pasa por `parse`;
- **no es una URL del censo**: lo leido va a `adapter_entities` y el resumen a
  `jobs.adapters["wordpress"]`, nunca a `urls`;
- **dos peticiones a la vez como mucho** (`max_concurrencia`), por una cola
  propia y en el slot del HOST, no en uno aparte: asi el Crawl-delay, el
  Retry-After y la concurrencia por dominio del sitio valen tambien para ellas
  (un slot propio sumaria dos peticiones mas al sitio y se saltaria su
  Crawl-delay);
- **se salta sin error** si la REST API esta cerrada (401, 403, 404, un HTML),
  la prohibe robots.txt o responde 429/503: se anota y el rastreo sigue.

Deteccion: con la respuesta de cada semilla (la URL final, tras sus
redirecciones), la raiz que anuncie la cabecera `Link` o el
`<link rel="https://api.w.org/">` (instalacion en una subcarpeta o
`?rest_route=/`) y `/wp-json/` en su origen: una peticion, que es la deteccion
misma (solo sigue si responde un indice con `wp/v2`). Se prueba `/wp-json/` aunque la home no la
anuncie porque hay temas que quitan el `<link>`: medido en blogs.uoc.edu el
24-oct-2026, sin `<link>` en la home y con la REST API abierta.

Sondas (hallazgos 2 y 5): una muestra de paginas de adjunto y la pagina N+1
de unos pocos archivos (la home de entradas y los terminos con mas entradas),
sin seguir redirecciones; van como entidades `sonda_adjunto` y
`sonda_paginacion`.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Generator
from urllib.parse import urlsplit, urlunsplit

import scrapy
from scrapy.http import Response

from shared import wordpress as W

logger = logging.getLogger(__name__)

# Hosts de las semillas a los que se pide `/wp-json/` en modo auto.
MAX_HOSTS = 3
PRIORIDAD = 5


def _hash(url: str | None) -> str | None:
    from seo_crawler.extractors import compute_url_hash

    if not url:
        return None
    try:
        return compute_url_hash(url)
    except Exception:  # noqa: BLE001 -- una URL rara no tumba la lectura
        return None


def _otro_esquema(url: str) -> str | None:
    p = urlsplit(url)
    otro = {"http": "https", "https": "http"}.get(p.scheme)
    return urlunsplit((otro, p.netloc, p.path, p.query, "")) if otro else None


def _cabecera(response: Response, nombre: str) -> str | None:
    valor = response.headers.get(nombre)
    if valor is None:
        return None
    return valor.decode("latin-1", "replace") if isinstance(valor, bytes) else str(valor)


class AdaptadorWordPressMixin:
    """Metodos del spider para leer la REST API de WordPress. Estado en
    `self._wp_*`. Los valores de clase dejan el adaptador apagado en los tests
    que montan el spider sin `spider_opened`."""

    _wp_modo: str = "off"
    _wp_cola: deque | None = None
    _wp_en_vuelo: int = 0

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    def _iniciar_wordpress(self) -> None:
        cfg = ((self.job_config.get("adapters") or {}).get("wordpress") or {})
        modo = cfg.get("modo", "auto")
        if modo not in ("auto", "on", "off"):
            modo = "auto"
        # Una lista (`max_depth` 0, decision 128) es QA de unas URLs: no lee
        # la REST API del sitio entero.
        if getattr(self, "max_depth", 1) < 1:
            modo = "off"
        self._wp_modo = modo
        self._wp_max_vuelo = max(1, min(2, int(cfg.get("max_concurrencia", 2) or 2)))
        self._wp_max_peticiones = int(cfg.get("max_peticiones", 1000) or 1000)
        self._wp_max_por_tipo = int(cfg.get("max_entidades_por_tipo", 20_000) or 20_000)
        self._wp_max_medios = int(cfg.get("max_medios", 1000) or 0)
        self._wp_muestra_adjuntos = int(cfg.get("muestra_adjuntos", 20) or 0)
        self._wp_sondas_restantes = int(cfg.get("sondas_paginacion", 5) or 0)
        self._wp_cola = deque()
        self._wp_en_vuelo = 0
        self._wp_peticiones = 0
        self._wp_raices_pedidas: set[str] = set()
        self._wp_raiz: str | None = None
        self._wp_indice: dict = {}
        self._wp_tipos_contenido: set[str] = set()
        self._wp_sondas_pedidas: set[str] = set()
        self._wp_resumen: dict[str, Any] = {
            "modo": modo, "detectado": False, "rest": None, "evidencia": [],
            "intentos": [], "tipos": {}, "peticiones": 0, "cortada": None,
            "sondas": {"paginacion": 0, "adjuntos": 0},
        }
        if modo == "off":
            return
        # Cada proceso vuelve a leer la REST API entera: lo de una ejecucion
        # anterior del job (reanudacion) se borra antes de empezar.
        try:
            from shared.database import SessionLocal
            from shared.models import AdapterEntity

            import uuid

            try:
                jid = uuid.UUID(str(self.job_id))
            except ValueError:
                jid = self.job_id
            sesion = SessionLocal()
            try:
                sesion.query(AdapterEntity).filter(
                    AdapterEntity.job_id == jid,
                    AdapterEntity.adaptador == W.ADAPTADOR,
                ).delete(synchronize_session=False)
                sesion.commit()
            finally:
                sesion.close()
        except Exception as exc:  # noqa: BLE001 -- sin la tabla el rastreo sigue
            logger.warning("WordPress: no se pudieron borrar las entidades anteriores: %s", exc)

    def _wp_activo(self) -> bool:
        return getattr(self, "_wp_modo", "off") != "off" and self._wp_cola is not None

    # ------------------------------------------------------------------
    # Cola: dos a la vez como mucho
    # ------------------------------------------------------------------
    def _wp_encolar(self, url: str | None, rol: str, **meta) -> None:
        if not url or not self._wp_activo() or self._wp_resumen.get("cortada"):
            return
        if self._wp_peticiones >= self._wp_max_peticiones:
            self._wp_resumen["cortada"] = "tope_de_peticiones"
            logger.warning("WordPress: tope de %d peticiones; la lectura queda parcial",
                           self._wp_max_peticiones)
            return
        self._wp_peticiones += 1
        es_sonda = rol.startswith("sonda_")
        m: dict[str, Any] = {"depth": 0, "wp_rol": rol, "handle_httpstatus_all": True, **meta}
        cabeceras = None
        if es_sonda:
            m["dont_redirect"] = True
        else:
            cabeceras = {"Accept": "application/json"}
        self._wp_cola.append(scrapy.Request(
            url, callback=self.parse_wp, errback=self.error_wp, meta=m,
            headers=cabeceras, dont_filter=True, priority=PRIORIDAD))

    def _wp_soltar(self) -> Generator[scrapy.Request, None, None]:
        while self._wp_cola and self._wp_en_vuelo < self._wp_max_vuelo:
            self._wp_en_vuelo += 1
            yield self._wp_cola.popleft()

    def _wp_reanimar(self) -> bool:
        """Desde `spider_idle`: si quedan peticiones en la cola y ninguna en
        vuelo (una que se perdio sin callback ni errback), se sueltan. True si
        se solto algo y el spider no debe cerrarse aun."""
        if not self._wp_activo() or not self._wp_cola:
            return False
        self._wp_en_vuelo = 0
        soltadas = 0
        for req in self._wp_soltar():
            self.crawler.engine.crawl(req)
            soltadas += 1
        return soltadas > 0

    # ------------------------------------------------------------------
    # Arranque y deteccion
    # ------------------------------------------------------------------
    def _wp_pedir_indice(self, raiz: str, como: str) -> None:
        if raiz in self._wp_raices_pedidas or self._wp_raiz:
            return
        self._wp_raices_pedidas.add(raiz)
        self._wp_encolar(W.url_rest(raiz, "", {"_fields": W.CAMPOS_INDICE}), "indice",
                         wp_raiz=raiz, wp_como=como)

    def _wp_de_pagina(self, response: Response, depth: int, selector=None) -> Generator:
        """Desde la respuesta de una semilla (o, al reanudar, de la primera
        pagina de cada host): la evidencia de WordPress, la raiz que anuncie y
        `/wp-json/` en su origen.

        Se pide desde aqui y no al arrancar por robots.txt: el
        `RobotsTxtMiddleware` de Scrapy 2.19 devuelve el parser solo a las
        dos primeras peticiones que esperan el robots.txt de un host, y a las
        demas `None` (= todo permitido). Medido en el arnes: con la peticion a
        `/wp-json/` en `start_requests`, robots.txt la prohibia y se pedia
        igual. Cuando llega la respuesta de una pagina, el robots.txt de su
        host ya esta leido.
        """
        if not self._wp_activo() or self._wp_raiz:
            return
        if depth > 0 and not getattr(self, "_resume_mode", False):
            return
        try:
            raiz = None
            if depth == 0:
                evidencia = self._wp_resumen["evidencia"]
                raiz = W.raiz_de_cabecera(_cabecera(response, "Link"), response.url)
                if raiz:
                    evidencia.append("cabecera_link")
                elif selector is not None:
                    href = selector.xpath(f'//link[@rel="{W.REL_API}"]/@href').get()
                    if href:
                        raiz = response.urljoin(href.strip())
                        evidencia.append("link_en_head")
                if selector is not None:
                    gen = selector.xpath('//meta[@name="generator"]/@content').get() or ""
                    if gen.lower().startswith("wordpress"):
                        evidencia.append("meta_generator")
                        self._wp_resumen["version"] = gen[len("wordpress"):].strip() or None
                if b"/wp-content/" in response.body or b"/wp-includes/" in response.body:
                    evidencia.append("rutas_wp_content")
                self._wp_resumen["evidencia"] = sorted(set(evidencia))
            if raiz:
                # La anunciada manda: en wordpress.org/news la raiz es
                # /news/wp-json/ y la del origen es OTRO WordPress (el de
                # wordpress.org). El origen solo si no anuncia ninguna.
                self._wp_pedir_indice(raiz, "anunciada")
            elif len(self._wp_raices_pedidas) < MAX_HOSTS:
                origen = W.raiz_por_defecto(response.url)
                if origen:
                    self._wp_pedir_indice(origen, "origen")
        except Exception as exc:  # noqa: BLE001 -- la deteccion no tumba la pagina
            logger.warning("WordPress: no se pudo leer la evidencia de %s: %s", response.url, exc)
        yield from self._wp_soltar()

    # ------------------------------------------------------------------
    # Respuestas
    # ------------------------------------------------------------------
    def parse_wp(self, response: Response) -> Generator:
        self._wp_en_vuelo = max(0, self._wp_en_vuelo - 1)
        rol = response.meta.get("wp_rol")
        try:
            if rol == "indice":
                yield from self._wp_indice_leido(response)
            elif rol in ("tipos", "taxonomias"):
                self._wp_catalogo_leido(response, rol)
            elif rol == "lista":
                yield from self._wp_lista_leida(response)
            elif rol in ("sonda_paginacion", "sonda_adjunto"):
                yield from self._wp_sonda_leida(response)
        except Exception as exc:  # noqa: BLE001 -- una respuesta rara no para la cola
            logger.warning("WordPress: fallo al leer %s (%s): %s", response.url, rol, exc)
        yield from self._wp_soltar()

    def error_wp(self, failure) -> Generator:
        self._wp_en_vuelo = max(0, self._wp_en_vuelo - 1)
        try:
            req = failure.request
            rol = req.meta.get("wp_rol")
            robots = bool(failure.check(scrapy.exceptions.IgnoreRequest))
            motivo = ("bloqueada_por_robots" if robots
                      else f"{type(failure.value).__name__}: {failure.value}"[:200])
            if rol == "indice":
                self._wp_resumen["intentos"].append(
                    {"raiz": req.meta.get("wp_raiz"), "error": motivo})
                if robots and not self._wp_raiz:
                    self._wp_resumen["rest"] = "bloqueada_por_robots"
            elif rol in ("sonda_paginacion", "sonda_adjunto"):
                yield self._wp_item([{
                    "tipo": rol, "url": req.url, "url_hash": _hash(req.url),
                    "status_code": None, "datos": {**req.meta.get("wp_datos", {}),
                                                   "error": motivo}}])
            else:
                tipo = req.meta.get("wp_tipo") or rol
                t = self._wp_resumen["tipos"].setdefault(tipo, {})
                t["error"] = motivo
        except Exception as exc:  # noqa: BLE001
            logger.warning("WordPress: fallo al anotar un error: %s", exc)
        yield from self._wp_soltar()

    def _wp_json(self, response: Response):
        try:
            return json.loads(response.text)
        except Exception:  # noqa: BLE001 -- un HTML, un cuerpo vacio
            return None

    def _wp_corte(self, response: Response) -> bool:
        """Un 429 o un 503 (ya reintentado por Scrapy): el sitio pide parar. Se
        deja de leer la REST API entera; el rastreo sigue a su ritmo."""
        if response.status in (429, 503):
            self._wp_resumen["cortada"] = str(response.status)
            self._wp_cola.clear()
            logger.warning("WordPress: la REST API responde %d; se deja de leer",
                           response.status)
            return True
        return False

    def _wp_indice_leido(self, response: Response) -> Generator:
        raiz = response.meta.get("wp_raiz")
        datos = self._wp_json(response) if response.status == 200 else None
        intento = {"raiz": raiz, "status": response.status, "como": response.meta.get("wp_como")}
        self._wp_resumen["intentos"].append(intento)
        if self._wp_raiz:
            return
        if not W.es_indice(datos):
            if response.status in (401, 403):
                self._wp_resumen["rest"] = "cerrada"
                self._wp_resumen["codigo"] = response.status
            elif response.status in (429, 503):
                self._wp_resumen["rest"] = "cerrada"
                self._wp_resumen["codigo"] = response.status
            # `on`: sin enlaces permanentes la raiz es `?rest_route=/`.
            if self._wp_modo == "on" and "rest_route" not in (raiz or ""):
                p = urlsplit(raiz or "")
                self._wp_pedir_indice(urlunsplit((p.scheme, p.netloc, "/", "rest_route=/", "")),
                                      "rest_route")
            return
        # Si la peticion salto (http -> https), la raiz es la del destino.
        final, pedida = urlsplit(response.url), urlsplit(raiz)
        if (final.scheme, final.netloc) != (pedida.scheme, pedida.netloc):
            raiz = urlunsplit((final.scheme, final.netloc, pedida.path, pedida.query, ""))
        self._wp_raiz = raiz
        self._wp_indice = {k: datos.get(k) for k in ("name", "url", "home", "show_on_front",
                                                     "page_for_posts")}
        namespaces = [n for n in datos.get("namespaces") or [] if isinstance(n, str)]
        self._wp_resumen.update({
            "detectado": True, "rest": "abierta", "codigo": response.status, "raiz": raiz,
            "nombre": datos.get("name"), "home": datos.get("home"),
            "show_on_front": datos.get("show_on_front"),
            "namespaces": namespaces[:60], "plugin_seo": W.plugin_seo(namespaces),
        })
        self._wp_resumen["evidencia"] = sorted(set(self._wp_resumen["evidencia"]) | {"rest_api"})
        logger.info("WordPress detectado en %s (%s)", raiz, self._wp_resumen["plugin_seo"] or
                    "sin plugin SEO conocido")
        self._wp_persistir()
        self._wp_encolar(W.url_rest(raiz, "wp/v2/types"), "tipos")
        return
        yield  # pragma: no cover -- generador

    def _wp_catalogo_leido(self, response: Response, rol: str) -> None:
        if self._wp_corte(response):
            return
        datos = self._wp_json(response) if response.status == 200 else None
        raiz = self._wp_raiz
        if rol == "tipos":
            tipos = W.tipos_de_contenido(datos)
            if response.status != 200 or not tipos:
                # Sin `types` (lo cierran algunos plugins de seguridad): los dos
                # que existen en todo WordPress.
                tipos = [("post", "posts"), ("page", "pages")]
                self._wp_resumen["tipos_por_defecto"] = True
            self._wp_tipos_contenido = {slug for slug, _ in tipos}
            for slug, base in tipos:
                self._wp_pedir_lista(slug, base, "entrada", 1)
            if self._wp_max_medios > 0:
                self._wp_pedir_lista("attachment", "media", "medio", 1)
            self._wp_encolar(W.url_rest(raiz, "wp/v2/taxonomies"), "taxonomias")
            return
        taxs = W.taxonomias(datos, self._wp_tipos_contenido)
        if response.status != 200 or not taxs:
            taxs = [("category", "categories"), ("post_tag", "tags")]
        for slug, base in taxs:
            self._wp_pedir_lista(slug, base, "termino", 1)

    def _wp_pedir_lista(self, tipo: str, base: str, clase: str, pagina: int) -> None:
        campos = {"entrada": W.CAMPOS_ENTRADA, "medio": W.CAMPOS_MEDIO,
                  "termino": W.CAMPOS_TERMINO}[clase]
        params: dict[str, Any] = {"per_page": W.POR_PAGINA, "page": pagina, "_fields": campos}
        if clase == "termino":
            # Los terminos con mas entradas primero: de la primera pagina salen
            # las sondas de paginacion.
            params.update({"orderby": "count", "order": "desc"})
        self._wp_encolar(W.url_rest(self._wp_raiz, f"wp/v2/{base}", params), "lista",
                         wp_tipo=tipo, wp_base=base, wp_clase=clase, wp_pagina=pagina)

    def _wp_lista_leida(self, response: Response) -> Generator:
        tipo = response.meta["wp_tipo"]
        clase = response.meta["wp_clase"]
        pagina = response.meta["wp_pagina"]
        t = self._wp_resumen["tipos"].setdefault(tipo, {"clase": clase, "leidas": 0})
        if self._wp_corte(response):
            return
        if response.status != 200:
            # 400 = pagina mas alla del final (no deberia: se pagina por la
            # cabecera); 401/403/404 = tipo sin acceso publico.
            t.setdefault("status", response.status)
            return
        datos = self._wp_json(response)
        if not isinstance(datos, list):
            t["status"] = "no_json"
            return
        if pagina == 1:
            total = W.entero(_cabecera(response, "X-WP-Total"))
            paginas = W.entero(_cabecera(response, "X-WP-TotalPages")) or 1
            tope = self._wp_max_medios if clase == "medio" else self._wp_max_por_tipo
            hasta = min(paginas, max(1, -(-tope // W.POR_PAGINA)))
            t.update({"total": total, "paginas": paginas, "recortado": hasta < paginas})
            for n in range(2, hasta + 1):
                self._wp_pedir_lista(tipo, response.meta["wp_base"], clase, n)
        entidades = [e for e in (self._wp_entidad(d, tipo, clase) for d in datos) if e]
        t["leidas"] = t.get("leidas", 0) + len(entidades)
        if entidades:
            yield self._wp_item(entidades)
        if pagina == 1:
            self._wp_programar_sondas(tipo, clase, datos, t)

    def _wp_entidad(self, d, tipo: str, clase: str) -> dict | None:
        if not isinstance(d, dict) or d.get("id") is None:
            return None
        from seo_crawler.extractors import normalize_url

        link = d.get("link") if isinstance(d.get("link"), str) else None
        url = None
        en_alcance = None
        if link:
            try:
                url = normalize_url(link)
                en_alcance = bool(self._is_internal(url)) and bool(self._should_follow(url))
            except Exception:  # noqa: BLE001 -- un link que no es una URL
                url, en_alcance = link, False
        datos: dict[str, Any] = {}
        if clase == "medio":
            datos = {"post": d.get("post"), "mime_type": d.get("mime_type"),
                     "source_url": d.get("source_url")}
        elif clase == "termino":
            datos = {"parent": d.get("parent")}
        return {
            "tipo": tipo, "id_externo": str(d.get("id")), "url": url,
            "url_hash": _hash(url), "url_hash_alt": _hash(_otro_esquema(url)) if url else None,
            "en_alcance": en_alcance, "estado": d.get("status"),
            "cuenta": d.get("count") if isinstance(d.get("count"), int) else None,
            "modificado": d.get("modified_gmt"), "datos": datos or None,
        }

    def _wp_item(self, entidades: list[dict]):
        from seo_crawler.items import EntidadesAdaptadorItem

        return EntidadesAdaptadorItem(job_id=self.job_id, adaptador=W.ADAPTADOR,
                                      entidades=entidades)

    # ------------------------------------------------------------------
    # Sondas
    # ------------------------------------------------------------------
    def _wp_programar_sondas(self, tipo: str, clase: str, datos: list, t: dict) -> None:
        if clase == "entrada" and tipo == "post":
            # La home de entradas, si la portada es el blog (con una pagina
            # estatica de portada, `/page/N/` es otra cosa).
            total = t.get("total")
            home = self._wp_indice.get("home")
            if total and home and self._wp_indice.get("show_on_front") == "posts":
                self._wp_sonda_paginacion(home if home.endswith("/") else home + "/",
                                          total + 1, {"archivo": "home", "entradas": total})
        elif clase == "termino":
            # Una categoria muestra tambien las entradas de sus hijas: su
            # `count` no es un tope y su pagina N+1 puede existir. Solo las
            # hojas, y solo si la primera pagina trae todas las del tipo.
            jerarquica = any(isinstance(d, dict) and d.get("parent") for d in datos)
            padres = {d.get("parent") for d in datos if isinstance(d, dict)}
            if jerarquica and (t.get("paginas") or 1) > 1:
                return
            for d in datos:
                if not isinstance(d, dict) or not isinstance(d.get("count"), int):
                    continue
                if d.get("count", 0) < 1 or d.get("id") in padres:
                    continue
                if self._wp_sondas_restantes <= 0:
                    break
                self._wp_sonda_paginacion(d.get("link"), d["count"] + 1,
                                          {"archivo": tipo, "termino": d.get("id"),
                                           "entradas": d["count"]})
        elif clase == "medio":
            for d in datos:
                if self._wp_resumen["sondas"]["adjuntos"] >= self._wp_muestra_adjuntos:
                    break
                if not isinstance(d, dict):
                    continue
                link = d.get("link")
                if not isinstance(link, str) or link == d.get("source_url"):
                    continue
                self._wp_sonda(link, "sonda_adjunto", {"adjunto": d.get("id")}, "adjuntos")

    def _wp_sonda_paginacion(self, link: str | None, n: int, datos: dict) -> None:
        from seo_crawler.extractors import normalize_url

        try:
            link = normalize_url(link) if link else None
        except Exception:  # noqa: BLE001
            return
        url = W.url_de_pagina(link, n)
        if url and self._wp_sondas_restantes > 0:
            datos = {**datos, "n": n, "pagina_1": link, "pagina_1_hash": _hash(link),
                     "siguiente": W.url_de_pagina(link, n + 1)}
            if self._wp_sonda(url, "sonda_paginacion", datos, "paginacion"):
                self._wp_sondas_restantes -= 1

    def _wp_sonda(self, url: str, rol: str, datos: dict, contador: str) -> bool:
        from seo_crawler.extractors import normalize_url

        try:
            url = normalize_url(url)
        except Exception:  # noqa: BLE001
            return False
        if url in self._wp_sondas_pedidas or not self._is_internal(url) \
                or not self._should_follow(url):
            return False
        self._wp_sondas_pedidas.add(url)
        self._wp_resumen["sondas"][contador] += 1
        self._wp_encolar(url, rol, wp_datos=datos)
        return True

    def _wp_sonda_leida(self, response: Response) -> Generator:
        from shared.respuestas import clasificar_respuesta

        rol = response.meta["wp_rol"]
        datos = dict(response.meta.get("wp_datos") or {})
        cabeceras = {
            k.decode("latin-1").lower(): ", ".join(v.decode("latin-1", "replace") for v in vs or [])
            for k, vs in response.headers.items()
        }
        ctype = cabeceras.get("content-type") or ""
        datos["content_type"] = ctype[:120] or None
        if 300 <= response.status < 400:
            loc = cabeceras.get("location")
            datos["redirige_a"] = response.urljoin(loc) if loc else None
        try:
            datos["response_class"], _ = clasificar_respuesta(
                response.status, cabeceras, response.body, content_type=ctype, url=response.url)
        except Exception:  # noqa: BLE001
            datos["response_class"] = None
        try:
            # Las mismas lecturas que una pagina del censo: todas las
            # `X-Robots-Tag` y el canonical de la cabecera `Link`.
            _, datos["x_robots_tag"], datos["canonical_cabecera"] = \
                self._directivas_de_cabecera(response, response.url)
        except Exception:  # noqa: BLE001
            datos["x_robots_tag"] = cabeceras.get("x-robots-tag")
        if response.status == 200 and "html" in ctype.lower():
            try:
                from seo_crawler.extractors import extract_meta

                meta = extract_meta(response.selector, base_url=response.url)
                datos["meta_robots"] = meta.get("meta_robots")
                datos["canonical"] = meta.get("canonical_href")
                datos["canonicals_distintos"] = meta.get("canonical_distintos")
            except Exception as exc:  # noqa: BLE001
                datos["error"] = f"extraccion: {exc}"[:200]
            siguiente = datos.get("siguiente")
            if rol == "sonda_paginacion" and siguiente:
                camino = urlsplit(siguiente).path.encode("utf-8", "replace")
                datos["enlaza_a_la_siguiente"] = bool(camino) and camino in response.body
        logger.info("Sonda WordPress %s -> %s", response.url, response.status)
        yield self._wp_item([{
            "tipo": rol, "url": response.url, "url_hash": _hash(response.url),
            "status_code": response.status, "datos": datos}])

    # ------------------------------------------------------------------
    # Cierre
    # ------------------------------------------------------------------
    def _wp_al_cerrar(self, reason: str | None) -> None:
        if not self._wp_activo():
            return
        try:
            r = self._wp_resumen
            if (self._wp_cola or self._wp_en_vuelo) and not r.get("cortada"):
                # El rastreo cerro con lecturas pendientes (tope de URLs, cancelado).
                r["cortada"] = f"cierre: {reason}"
            if not r["detectado"] and not r.get("rest"):
                r["rest"] = "cerrada" if r["evidencia"] else "no_detectado"
            r["peticiones"] = self._wp_peticiones
            r["terminado_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self._wp_persistir()
        except Exception as exc:  # noqa: BLE001 -- el cierre no puede fallar por esto
            logger.warning("WordPress: no se pudo cerrar el resumen: %s", exc)

    def _wp_persistir(self) -> None:
        try:
            from shared.database import SessionLocal
            from shared.models import Job

            self._wp_resumen["peticiones"] = self._wp_peticiones
            sesion = SessionLocal()
            try:
                sesion.query(Job).filter(Job.id == self.job_id).update(
                    {"adapters": {W.ADAPTADOR: self._wp_resumen}}, synchronize_session=False)
                sesion.commit()
            finally:
                sesion.close()
        except Exception as exc:  # noqa: BLE001 -- un resumen no tumba un rastreo
            logger.warning("WordPress: no se pudo guardar el resumen: %s", exc)
