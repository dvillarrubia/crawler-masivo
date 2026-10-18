"""Paridad movil/escritorio por muestra de plantilla (decision 119, Ola 7.4).

Google indexa lo que recibe su UA de smartphone. Si el sitio sirve otra cosa
a un movil (servido dinamico, una plantilla movil que se deja el canonical o
mete un `noindex`, un menu hamburguesa que no esta en el HTML), el censo de
escritorio no lo ve. Con `crawl_behavior.paridad_movil_muestra > 0`, al
terminar `parse` de una pagina interna HTML 2xx que no es un muro, si su
plantilla (`config.templates` del job o `shared.plantillas.firma_de_ruta`)
no ha llenado su cupo, se vuelve a pedir esa URL con el otro perfil
(`shared.perfiles`): movil si el UA del job es de escritorio, escritorio si
ya es de movil. Se mide con las MISMAS funciones que el censo y se compara
con `shared.paridad.diferencias`.

Lo que se copia de FreeCrawl (`mobile-parity-probe.ts`) es la idea y el diff
campo a campo. Lo que no:

- **no sigue redirecciones**: una redireccion a `m.` compararia otra pagina
  contra la URL de escritorio. Se anota como hallazgo propio (`redirige`), se
  pide el destino una vez y se mira la anotacion bidireccional (`rel=alternate
  media` en escritorio, canonical a escritorio en el m-dot);
- **compara en la misma ventana**: la linea base viaja en `meta` y la sonda
  sale segundos despues, no horas (los sitios cambian solos, decision 13). Y
  si algo difiere, se pide OTRA VEZ con el perfil del censo: un campo que
  tambien cambia entre las dos de escritorio es volatil y no se afirma;
- **la muestra es por plantilla**, no por entrantes (que se lleva la home y
  el menu y ninguna plantilla profunda);
- un muro en la sonda es «no evaluada», no una diferencia.

Es una sonda como la de soft 404 (decision 105): callback propio, sin
`PageItem`, `dont_filter`, fuera de `max_urls`, respeta robots.txt y no toca
la salud del rastreo. Y escribe el latido por tiempo: una cola final de
sondas sin paginas no puede dejar el job en `stalled`.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Generator
from urllib.parse import urljoin, urlparse

import scrapy
from scrapy.http import HtmlResponse, Response

from shared.indexabilidad import es_nofollow, es_noindex, misma_url
from shared.paridad import canonical_efectivo, diferencias, resumen
from shared.perfiles import (
    IMPERSONATE_MOVIL, UA_CHROME_ESCRITORIO, UA_MOVIL_POR_NOMBRE, contexto_playwright,
    es_ua_movil,
)
from shared.plantillas import firma_de_ruta

logger = logging.getLogger(__name__)

POR_PLANTILLA_POR_DEFECTO = 3
# Cada cuanto escribe el latido una sonda (el vigilante del worker mata el
# job si `job:{id}:heartbeat` no cambia en `stall_timeout_minutes`).
LATIDO_S = 60.0


def impersonate_movil_disponible() -> bool:
    """Si el curl_cffi instalado tiene la huella TLS de Chrome en Android.

    `scrapy-impersonate` no esta fijado en `crawler/requirements.txt`: la
    imagen puede traer un curl_cffi sin ella. Entonces la sonda sale con la
    huella del job y la cabecera de movil, y se dice en el log.
    """
    try:
        from curl_cffi.requests import BrowserType

        return IMPERSONATE_MOVIL in {b.value for b in BrowserType}
    except Exception:  # noqa: BLE001
        return False


def contextos_del_perfil(contextos: dict | None, ua: str | None) -> dict:
    """`PLAYWRIGHT_CONTEXTS` con el contexto `custom` del perfil del job.

    El diccionario de `settings.py` se construye al importar, con el
    `USER_AGENT` del ENTORNO: el `-s USER_AGENT=` del job (worker.py) no lo
    cambiaba, y con render la cabecera llevaba el UA del job y
    `navigator.userAgent` el de escritorio, en una ventana de 1280 px. Con un
    UA de movil, ademas, el contexto pasa a ser de movil (viewport, toque).
    """
    salida = {k: dict(v) for k, v in (contextos or {}).items()}
    if ua and "custom" in salida:
        salida["custom"] = contexto_playwright(ua, es_ua_movil(ua), base=salida["custom"])
    return salida


def huella_paridad(response: Response, *, url: str, allowed_hosts: set[str],
                   extraction: dict | None = None) -> dict[str, Any]:
    """Lo que se compara de una respuesta HTML, con las funciones del censo.

    `url` es la de la pagina (la que se guarda en el censo), contra la que se
    resuelve el canonical. Los enlaces son los destinos internos seguibles
    SIN aplicar el `nofollow` de pagina: ese se compara aparte, y si no, un
    `nofollow` solo en movil saldria dos veces (decision 36).
    """
    from seo_crawler.extractors import (
        canonical_de_cabecera, contar_palabras, effective_base_url, extract_headings,
        extract_links, extract_main_content, extract_meta, extract_word_count,
    )

    extraction = extraction or {}
    selector = response.selector
    base_url = effective_base_url(selector, response.url)
    meta = extract_meta(selector, base_url=base_url)
    robots_meta = {"robots": meta.get("meta_robots"), "googlebot": meta.get("meta_robots_googlebot")}
    x_robots = [v.decode("utf-8", "ignore") for v in response.headers.getlist(b"X-Robots-Tag") if v]
    link = ", ".join(v.decode("utf-8", "ignore") for v in response.headers.getlist(b"Link") if v)
    h1 = next((h["text"] for h in extract_headings(selector)
               if h.get("tag") == "h1" and not h.get("oculto") and h.get("text")), None)
    palabras = None
    if extraction.get("extract_page_content", True):
        palabras = contar_palabras(extract_main_content(
            selector,
            word_count=extract_word_count(selector),
            strip_promo=extraction.get("strip_promo_blocks", True),
            extra_selectors=extraction.get("custom_boilerplate_selectors") or None,
        ))
    enlaces = sorted({
        l["url"] for l in extract_links(selector, base_url, allowed_hosts)
        if l.get("is_internal") and l.get("follow", True)
    })
    alternate = selector.xpath(
        "//head/link[contains(concat(' ', normalize-space(@rel), ' '), ' alternate ')]"
        "[@media][@href]/@href").get()
    vary = response.headers.get(b"Vary")
    return {
        "url": url,
        "status": response.status,
        "title": meta.get("title") or None,
        "h1": h1,
        "description": meta.get("meta_description") or None,
        "canonical": meta.get("canonical_href") or canonical_de_cabecera(link, base_url),
        "noindex": es_noindex(robots_meta, x_robots),
        "nofollow": es_nofollow(robots_meta, x_robots),
        "palabras": palabras,
        "enlaces": enlaces,
        "alternate_movil": urljoin(base_url, alternate.strip()) if alternate else None,
        "vary": vary.decode("latin-1") if vary else None,
    }


def _destino(response: Response) -> str | None:
    """A donde manda la respuesta, sin seguirla: Location de un 3xx, el
    `Location` que la preparacion de pagina retuvo (decision 114) o, con
    render, la URL a la que acabo Chromium."""
    if 300 <= response.status < 400 and response.status != 304:
        loc = response.headers.get(b"Location") or (
            (response.meta.get("_location_render") or "").encode())
        if loc:
            return response.urljoin(loc.decode("latin-1").strip())
    if response.url != response.request.url:
        return response.url
    return None


class ParidadMovilMixin:
    """Metodos del spider para la paridad. Estado en `self._paridad_*`."""

    def _iniciar_paridad(self, session=None) -> None:
        cb = self.job_config.get("crawl_behavior") or {}
        self._paridad_max: int = int(cb.get("paridad_movil_muestra", 0) or 0)
        self._paridad_por_plantilla: int = max(1, int(
            cb.get("paridad_movil_por_plantilla", POR_PLANTILLA_POR_DEFECTO) or 1))
        self._paridad_cupos: dict[str, int] = {}
        self._paridad_pedidas: int = 0
        self._paridad_latido: float = time.monotonic()
        if self._paridad_max <= 0:
            return
        ua_job = self.job_config.get("user_agent") or self.settings.get("USER_AGENT") or ""
        # El perfil que NO es el del censo
        self._paridad_movil = not es_ua_movil(ua_job)
        self._paridad_perfil = "movil" if self._paridad_movil else "escritorio"
        self._paridad_ua = (
            UA_MOVIL_POR_NOMBRE.get(cb.get("paridad_movil_ua") or "chrome_android",
                                    UA_MOVIL_POR_NOMBRE["chrome_android"])
            if self._paridad_movil else UA_CHROME_ESCRITORIO)
        self._paridad_impersonate = None
        if self._paridad_movil:
            if impersonate_movil_disponible():
                self._paridad_impersonate = IMPERSONATE_MOVIL
            else:
                logger.warning(
                    "Paridad movil: este curl_cffi no tiene %s; sin render, la sonda "
                    "sale con la huella TLS del job y el UA de movil", IMPERSONATE_MOVIL)
        elif "android" in str(self.settings.get("IMPERSONATE") or "") \
                or "ios" in str(self.settings.get("IMPERSONATE") or ""):
            self._paridad_impersonate = "chrome124"
        self._paridad_reglas: list[tuple[str, re.Pattern]] = []
        for regla in self.job_config.get("templates") or []:
            if not isinstance(regla, dict):
                continue
            nombre, patron = regla.get("nombre"), regla.get("patron")
            try:
                if nombre and patron:
                    self._paridad_reglas.append((str(nombre), re.compile(str(patron), re.I)))
            except re.error:
                logger.warning("Plantilla %r con una regex no valida: no se usa", nombre)
        # Al reanudar, lo ya muestreado cuenta: si no, cada reanudacion
        # volveria a gastar la muestra entera.
        if session is not None:
            try:
                from sqlalchemy import func

                from shared.models import CapturaAlternativa as C

                with session.begin_nested():
                    for plantilla, n in (
                        session.query(C.plantilla, func.count())
                        .filter(C.job_id == self.job_id, C.perfil == self._paridad_perfil)
                        .group_by(C.plantilla).all()
                    ):
                        self._paridad_cupos[plantilla or ""] = int(n)
                        self._paridad_pedidas += int(n)
            except Exception:  # noqa: BLE001 -- sin tabla, se empieza de cero
                pass
        logger.info("Paridad %s: hasta %d paginas, %d por plantilla", self._paridad_perfil,
                    self._paridad_max, self._paridad_por_plantilla)

    def _plantilla_paridad(self, url: str) -> str:
        path = urlparse(url).path or "/"
        for nombre, patron in getattr(self, "_paridad_reglas", ()):
            if patron.search(path):
                return nombre
        return firma_de_ruta(path)

    def _paridad_de_pagina(self, response: Response, url: str) -> Generator:
        """La sonda de paridad de esta pagina, si su plantilla tiene cupo."""
        if getattr(self, "_paridad_max", 0) <= 0 or self._paridad_pedidas >= self._paridad_max:
            return
        plantilla = self._plantilla_paridad(url)
        if self._paridad_cupos.get(plantilla, 0) >= self._paridad_por_plantilla:
            return
        try:
            base = huella_paridad(response, url=url, allowed_hosts=self.allowed_hosts,
                                  extraction=self.job_config.get("extraction") or {})
        except Exception as exc:  # noqa: BLE001
            logger.debug("Paridad: no se pudo medir %s: %s", url, exc)
            return
        self._paridad_cupos[plantilla] = self._paridad_cupos.get(plantilla, 0) + 1
        self._paridad_pedidas += 1
        # El mismo modo que la pagina del censo: con render las dos, o
        # ninguna. Si no, se mediria el render (7.1) y no el dispositivo.
        render = bool(response.meta.get("playwright")) and not response.meta.get("_sin_render")
        datos = {
            "etapa": "alternativa", "plantilla": plantilla, "url": url,
            "modo": "render" if render else "sin_render", "base": base,
        }
        yield self._peticion_paridad(url, datos)

    def _peticion_paridad(self, url: str, datos: dict, *, perfil_del_censo: bool = False):
        meta: dict[str, Any] = {
            "depth": 0, "dont_redirect": True, "handle_httpstatus_all": True,
            "paridad": datos,
            # Con el jar encendido (decision 114), uno aparte: una cookie de
            # «version de escritorio» que puso el rastreo contaminaria la sonda.
            "cookiejar": "paridad-control" if perfil_del_censo else f"paridad-{self._paridad_perfil}",
        }
        if datos["modo"] == "render":
            meta.update(self._playwright_meta())
        if not perfil_del_censo:
            meta["user_agent"] = self._paridad_ua
            if datos["modo"] == "render":
                base_ctx = (self.settings.getdict("PLAYWRIGHT_CONTEXTS") or {}).get("custom") or {}
                meta["playwright_context"] = f"perfil_{self._paridad_perfil}"
                meta["playwright_context_kwargs"] = contexto_playwright(
                    self._paridad_ua, self._paridad_movil, base=base_ctx)
            elif self._paridad_impersonate:
                meta["impersonate"] = self._paridad_impersonate
        return scrapy.Request(url, callback=self.parse_paridad, errback=self.paridad_error,
                              meta=meta, dont_filter=True, priority=10)

    def _latido_paridad(self) -> None:
        ahora = time.monotonic()
        if ahora - getattr(self, "_paridad_latido", 0.0) >= LATIDO_S:
            self._paridad_latido = ahora
            try:
                self._write_heartbeat()
            except Exception:  # noqa: BLE001
                pass

    def _item_paridad(self, datos: dict, *, estado: str, status=None, clase=None,
                      redirige_a=None, vary=None, medidas=None, difs=None,
                      volatiles=None, detalle=None):
        from seo_crawler.extractors import compute_url_hash
        from seo_crawler.items import CapturaAlternativaItem

        return CapturaAlternativaItem(
            url_hash=compute_url_hash(datos["url"]), job_id=self.job_id,
            perfil=self._paridad_perfil, modo=datos["modo"], estado=estado,
            plantilla=datos["plantilla"], user_agent=self._paridad_ua,
            status_code=status, response_class=clase, redirige_a=redirige_a,
            vary_base=(datos.get("base") or {}).get("vary"), vary=vary,
            medidas=resumen(medidas) if medidas else None,
            diferencias=difs if difs is not None else None,
            volatiles=sorted(volatiles) if volatiles else [],
            detalle=detalle or None,
        )

    @staticmethod
    def _clase_paridad(response: Response) -> str | None:
        try:
            from shared.respuestas import clasificar_respuesta

            cabeceras = {
                k.decode("latin-1").lower(): ", ".join(
                    v.decode("latin-1", "replace") for v in (vs or []))
                for k, vs in response.headers.items()
            }
            clase, _ = clasificar_respuesta(
                response.status, cabeceras, response.body,
                content_type=cabeceras.get("content-type"), url=response.url,
                renderizada=bool(response.meta.get("playwright")))
            return clase
        except Exception:  # noqa: BLE001
            return None

    def parse_paridad(self, response: Response) -> Generator:
        from shared.respuestas import NO_VALIDAS

        self._latido_paridad()
        datos = response.meta["paridad"]
        etapa = datos["etapa"]
        clase = self._clase_paridad(response)
        destino = _destino(response)
        vary = response.headers.get(b"Vary")
        vary = vary.decode("latin-1") if vary else None
        es_pagina = (clase not in NO_VALIDAS and destino is None
                     and isinstance(response, HtmlResponse) and 200 <= response.status < 300)
        captura = None
        if es_pagina:
            try:
                captura = huella_paridad(response,
                                         url=datos["redirige_a"] if etapa == "destino" else datos["url"],
                                         allowed_hosts=self.allowed_hosts,
                                         extraction=self.job_config.get("extraction") or {})
            except Exception as exc:  # noqa: BLE001
                logger.debug("Paridad: no se pudo medir %s: %s", response.url, exc)

        if etapa == "destino":
            yield from self._cerrar_destino(datos, response, clase, captura)
            return
        if etapa == "control" and datos.get("control_de") == "respuesta":
            if captura is None:
                yield self._item_paridad(
                    datos, estado="no_evaluada", status=datos["status"], clase=datos["clase"],
                    vary=datos["vary"],
                    detalle={"motivo": "control_fallido", "control_status": response.status,
                             "control_clase": clase})
                return
            yield self._item_paridad(datos, estado="respuesta_distinta", status=datos["status"],
                                     clase=datos["clase"], vary=datos["vary"],
                                     detalle={"status_base": datos["base"].get("status")})
            return
        if etapa == "control":
            if captura is None:
                # Sin control no se sabe que es volatil: no se afirma nada.
                yield self._item_paridad(
                    datos, estado="no_evaluada", status=datos["status"], clase=datos["clase"],
                    vary=datos["vary"], medidas=datos["alternativa"],
                    detalle={"motivo": "control_fallido", "control_status": response.status,
                             "control_clase": clase,
                             "diferencias_sin_control": datos["difs"]})
                return
            volatiles = {d["campo"] for d in diferencias(datos["base"], captura)}
            yield self._item_paridad(
                datos, estado="comparada", status=datos["status"], clase=datos["clase"],
                vary=datos["vary"], medidas=datos["alternativa"],
                difs=[d for d in datos["difs"] if d["campo"] not in volatiles],
                volatiles=volatiles)
            return

        # etapa alternativa
        if clase in NO_VALIDAS:
            yield self._item_paridad(datos, estado="no_evaluada", status=response.status,
                                     clase=clase, vary=vary, detalle={"motivo": "respuesta_no_valida"})
            return
        if destino is not None:
            base_host = (urlparse(datos["url"]).hostname or "").lower()
            dest_host = (urlparse(destino).hostname or "").lower()
            detalle = {"tipo": "otro_host" if dest_host != base_host else "otra_url",
                       "alternate_en_base": datos["base"].get("alternate_movil")}
            if destino.startswith(("http://", "https://")):
                # El destino una vez, con el mismo perfil y sin seguir mas:
                # para ver si es un m-dot bien anotado. Sin `_should_follow`:
                # el destino es una sonda, no una URL del censo, y un
                # `include_patterns` acotado al host de escritorio dejaba sin
                # comprobar justo el m-dot (revision de #161). robots.txt del
                # destino se sigue respetando (middleware).
                datos.update({"etapa": "destino", "status": response.status, "clase": clase,
                              "vary": vary, "redirige_a": destino, "detalle": detalle})
                yield self._peticion_paridad(destino, datos)
                return
            yield self._item_paridad(datos, estado="redirige", status=response.status,
                                     clase=clase, redirige_a=destino, vary=vary, detalle=detalle)
            return
        if not (200 <= response.status < 300):
            # Otro codigo: antes de afirmarlo, otra vez con el perfil del
            # censo. Si tambien falla, lo que ha cambiado es el sitio (se cae,
            # limita sin firma de muro), no el dispositivo (decision 13).
            datos.update({"etapa": "control", "control_de": "respuesta",
                          "status": response.status, "clase": clase, "vary": vary})
            yield self._peticion_paridad(datos["url"], datos, perfil_del_censo=True)
            return
        if captura is None:
            yield self._item_paridad(datos, estado="no_evaluada", status=response.status,
                                     clase=clase, vary=vary, detalle={"motivo": "no_html"})
            return
        difs = diferencias(datos["base"], captura)
        if not difs:
            yield self._item_paridad(datos, estado="comparada", status=response.status,
                                     clase=clase, vary=vary, medidas=captura, difs=[])
            return
        # Algo difiere: otra vez con el perfil del censo, para ver que cambia solo
        datos.update({"etapa": "control", "status": response.status, "clase": clase,
                      "vary": vary, "alternativa": captura, "difs": difs})
        yield self._peticion_paridad(datos["url"], datos, perfil_del_censo=True)

    def _cerrar_destino(self, datos: dict, response, clase, captura) -> Generator:
        """La fila de una URL cuya version alternativa redirige a otra.

        Si el destino es una pagina, la anotacion de URLs separadas: el
        escritorio declara la movil con `rel=alternate media` y la movil
        declara la de escritorio como canonica. Sin las dos, Google puede
        indexar las dos como paginas distintas o quedarse con la que no es.
        """
        detalle = dict(datos.get("detalle") or {})
        dest = {"url": datos["redirige_a"], "status": response.status, "clase": clase}
        if captura is not None:
            dest["canonical"] = canonical_efectivo(captura)
            dest["canonical_a_base"] = misma_url(dest["canonical"], datos["url"])
            dest["alternate_al_destino"] = misma_url(
                datos["base"].get("alternate_movil"), datos["redirige_a"])
        detalle["destino"] = dest
        yield self._item_paridad(datos, estado="redirige", status=datos["status"],
                                 clase=datos["clase"], redirige_a=datos["redirige_a"],
                                 vary=datos["vary"], medidas=captura, detalle=detalle)

    def paridad_error(self, failure) -> Generator:
        self._latido_paridad()
        datos = failure.request.meta.get("paridad") or {}
        if not datos:
            return
        motivo = ("bloqueada_por_robots" if failure.check(scrapy.exceptions.IgnoreRequest)
                  else f"{type(failure.value).__name__}: {failure.value}"[:200])
        if datos["etapa"] == "destino":
            detalle = dict(datos.get("detalle") or {})
            detalle["destino"] = {"url": datos["redirige_a"], "error": motivo}
            yield self._item_paridad(datos, estado="redirige", status=datos["status"],
                                     clase=datos["clase"], redirige_a=datos["redirige_a"],
                                     vary=datos["vary"], detalle=detalle)
            return
        detalle = {"motivo": "error", "error": motivo}
        if datos["etapa"] == "control":
            detalle.update(motivo="control_fallido")
            if datos.get("difs"):
                detalle["diferencias_sin_control"] = datos["difs"]
        yield self._item_paridad(datos, estado="no_evaluada", detalle=detalle,
                                 status=datos.get("status"), clase=datos.get("clase"),
                                 vary=datos.get("vary"), medidas=datos.get("alternativa"))
