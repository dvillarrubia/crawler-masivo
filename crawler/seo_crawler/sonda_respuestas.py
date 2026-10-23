"""Sondas de respuesta: enlaces externos y recursos embebidos (decisiones 123 y 124).

Lo que el censo no pide pero el sitio ofrece a quien lo visita: los enlaces a
OTROS sitios (`crawl_behavior.comprobar_enlaces_externos`) y las imagenes, CSS
y JS que pinta la pagina (`crawl_behavior.comprobar_recursos` para los del
sitio, `resource_types.check_external_resources` para los de otro host). Un
mismo mecanismo para los dos usos, calcado de la sonda de soft 404
(decision 105):

- **no gasta `max_urls`**: callback y errback propios, nunca `parse` ni
  `handle_error`, que son quienes cuentan; y su propio presupuesto
  (`enlaces_externos_max`, `recursos_max`);
- **no es una URL del censo**: no emite `PageItem`. El resultado va a
  `respuestas_sondeadas` (`RespuestaSondeadaItem`), una fila por URL distinta;
- **no cuenta para la salud del rastreo** (decision 103): un muro de Cloudflare
  delante de LinkedIn no puede parar la auditoria del cliente;
- **late por tiempo**: la cola final de sondas no tiene paginas, y el latido del
  spider va por paginas; sin el suyo, el vigilante del worker daria el rastreo
  por estancado;
- **no lleva nada del job a un tercero**: cabeceras, cookies y Basic solo van a
  los hosts del sitio (decision 114, `HttpConfigMiddleware`), y la sonda no
  anade nada propio.

Una peticion es `HEAD`, y se repite UNA vez con `GET` (y `Range`, para no bajar
el cuerpo entero) ante los codigos en los que un `HEAD` no es de fiar
(`shared.sondas.HEAD_POCO_FIABLE`), incluidos el 404 y el 410: un «roto» se
confirma con `GET`. Las redirecciones las sigue la sonda (hasta 10 saltos), y
cada salto a otro host pasa por su robots.txt. Un salto a la red interna no se
sigue (`no_verificable`): el filtro SSRF vale para cada salto, no solo para la
URL enlazada, y por eso el robots.txt del tercero tampoco lo redirige Scrapy.

**La og:image se mide** (decision 154, `uso='og_image'`): se pide con `GET` y
un `Range` de 64 KB, que es donde cualquier formato de la web lleva el ancho y
el alto (`shared/dimensiones_imagen.py`), y van a `ancho`/`alto`. Encendida de
serie y acotada: `crawl_behavior.og_image_muestra` imagenes distintas como
mucho (200) y `og_image_por_plantilla` por forma de ruta de la pagina (5), para
que la muestra no se la coman la portada y el menu. Sin el tope por host de los
terceros: la og:image vive casi siempre en un CDN y ya tiene su presupuesto.

**robots.txt del tercero, siempre.** El `robots_mode` del job es la decision
del cliente sobre SU sitio; la de otro sitio no es suya. Cada origen externo se
pide su robots.txt una vez (la peticion del middleware de Scrapy se salta con
`dont_obey_robotstxt`, y asi tampoco se piden dos) y una URL prohibida queda
como `prohibido_por_robots`, sin pedirla. Los recursos internos pasan por el
robots del sitio como cualquier peticion del rastreo.

**Un host que no responde se deja de pedir.** El robots.txt es tambien la prueba
de vida del origen: si falla por DNS se pregunta al resolver del sistema (en un
hilo) y un «no existe» da el origen por caido sin pedir nada mas (un resolver
que no contesta es `no_verificable`, nunca un dominio muerto); si falla por
la red, se prueba UNA URL antes de soltar las demas, y un segundo fallo lo da
por caido. Las URLs de un origen caido se anotan con su veredicto y
`host_caido`, sin pedirlas: un dominio muerto cuesta una peticion, no cincuenta
esperas de 30 s.
"""

from __future__ import annotations

import logging
import socket
import time
from typing import Any, AsyncGenerator, Generator
from urllib.parse import urlsplit

import scrapy

from shared import sondas as S
from shared.dimensiones_imagen import BYTES_NECESARIOS, dimensiones
from shared.red import es_host_privado

logger = logging.getLogger(__name__)

# Detras de las paginas: el censo primero. La de robots, justo delante de las
# sondas que esperan por ella.
PRIORIDAD = -100
PRIORIDAD_ROBOTS = -99
# Cada cuanto late (y mira si hay que cancelar) una cola de sondas sin paginas.
LATIDO_S = 60.0
# Saltos que se siguen del robots.txt de un tercero (los de Google).
MAX_SALTOS_ROBOTS = 5

TOPE_ENLACES = 5000
TOPE_RECURSOS = 20000
POR_HOST = 50
TOPE_OG_IMAGE = 200
OG_IMAGE_POR_PLANTILLA = 5

RANGO = "bytes=0-1023"
# La og:image se pide para medirla: el ancho y el alto van en la cabecera del
# fichero, dentro de los primeros 64 KB aun con EXIF (decision 154).
RANGO_IMAGEN = f"bytes=0-{BYTES_NECESARIOS - 1}"
# Muchos servidores ignoran el `Range` y mandan el cuerpo entero, y curl_cffi lo
# baja a memoria. Por encima de esto curl corta (codigo 63) en cuanto ve el
# `Content-Length`: un recurso que anuncia 10 MB existe, no es una pagina de error.
MAX_BYTES_GET = 10 * 1024 * 1024

# Codigos de libcurl (curl_cffi) por tipo de fallo.
_CURL_DNS = {6}
_CURL_CONEXION = {7}
_CURL_TIEMPO = {28}
_CURL_TLS = {35, 51, 53, 54, 58, 59, 60, 64, 66, 77, 80, 82, 83, 90, 91, 98}
_CURL_GRANDE = {63}


def _hash_key(url_hash: str) -> int:
    return int(url_hash[:16], 16)


def clasificar_fallo(failure) -> str:
    """`dns`, `conexion`, `tiempo`, `tls`, `grande` u `otro`, de un fallo de descarga.

    Con curl_cffi (produccion) el error lleva el codigo de libcurl; con el
    handler de Twisted, la excepcion dice el tipo.
    """
    exc = failure.value
    codigo = getattr(exc, "code", None)
    if isinstance(codigo, int):
        if codigo in _CURL_DNS:
            return "dns"
        if codigo in _CURL_CONEXION:
            return "conexion"
        if codigo in _CURL_TIEMPO:
            return "tiempo"
        if codigo in _CURL_TLS:
            return "tls"
        if codigo in _CURL_GRANDE:
            return "grande"
    # Los de Twisted y, desde Scrapy 2.14, los suyos propios.
    nombres = {c.__name__ for c in type(exc).__mro__}
    if nombres & {"DNSLookupError", "DNSError", "CannotResolveHostError"}:
        return "dns"
    if nombres & {"ConnectionRefusedError", "ConnectError", "NoRouteError",
                  "DownloadConnectionRefusedError"}:
        return "conexion"
    if nombres & {"TimeoutError", "TCPTimedOutError", "Timeout", "CancelledError",
                  "DownloadTimeoutError"}:
        return "tiempo"
    if nombres & {"SSLError", "CertificateVerifyError", "Error"} and "SSL" in repr(exc):
        return "tls"
    return "otro"


def _cabeceras(response) -> dict[str, str]:
    return {
        k.decode("latin-1").lower(): ", ".join(v.decode("latin-1", "replace") for v in (vs or []))
        for k, vs in response.headers.items()
    }


def _metodo(ctx: dict) -> str:
    """`GET` para la og:image, que se pide para medirla; `HEAD` para lo demas."""
    return "GET" if ctx.get("uso") == "og_image" else "HEAD"


def _origen(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


class SondaRespuestasMixin:
    """Metodos del spider para las sondas de respuesta. Estado en `self._sr_*`."""

    # -- configuracion -------------------------------------------------------

    def _iniciar_sondas_respuesta(self) -> None:
        cfg = getattr(self, "job_config", None) or {}
        cb = cfg.get("crawl_behavior") or {}
        rt = cfg.get("resource_types") or {}
        self._sr_activa = {
            "enlace": bool(cb.get("comprobar_enlaces_externos")),
            "recurso_interno": bool(cb.get("comprobar_recursos")),
            "recurso_externo": bool(rt.get("check_external_resources")),
        }
        self._sr_tope = {
            "enlace": int(cb.get("enlaces_externos_max", TOPE_ENLACES) or 0),
            "recurso": int(cb.get("recursos_max", TOPE_RECURSOS) or 0),
            # Decision 154: encendida de serie, porque esta acotada. 0 = apagada.
            "og_image": int(cb.get("og_image_muestra", TOPE_OG_IMAGE) or 0),
        }
        self._sr_activa["og_image"] = self._sr_tope["og_image"] > 0
        self._sr_og_por_plantilla_max = int(
            cb.get("og_image_por_plantilla", OG_IMAGE_POR_PLANTILLA) or 0)
        self._sr_og_plantillas: dict[tuple[str, str], int] = {}
        self._sr_por_host_max = int(cb.get("sondas_por_host", POR_HOST) or 0)
        ajustes = getattr(self, "settings", None)
        # Solo para los tests: el arnes sirve los «terceros» en 127.0.0.x.
        self._sr_red_privada = bool(ajustes.getbool("SONDAS_RED_PRIVADA")) if ajustes else False
        self._sr_vistas: set[int] = set()
        self._sr_usadas = {"enlace": 0, "recurso": 0, "og_image": 0}
        # De esta sesion: URLs distintas encoladas y filas emitidas. La
        # diferencia al cerrar es lo que se quedo en la cola (max_urls, cancelar).
        self._sr_encoladas = 0
        self._sr_filas = 0
        self._sr_por_host: dict[str, int] = {}
        # origen -> {estado: robots|probando|abierto|caido, robots, espera,
        # fallos, veredicto, error}
        self._sr_origenes: dict[str, dict[str, Any]] = {}
        self._sr_resolucion: dict[str, str] = {}
        self._sr_sin_comprobar = {"tope": 0, "por_host": 0, "red_privada": 0,
                                  "og_por_plantilla": 0, "excluida": 0}
        self._sr_tope_avisado: set[str] = set()
        self._sr_ultimo_latido = time.monotonic()
        # Crawl-delay de cada tercero: slot -> segundos.
        self._sr_retrasos: dict[str, float] = {}
        self._sr_senal_conectada = False
        if not any(self._sr_activa.values()):
            return
        logger.info("Sondas de respuesta: %s, topes %s, %d por host",
                    {k: v for k, v in self._sr_activa.items() if v}, self._sr_tope,
                    self._sr_por_host_max)
        # Al reanudar no se repiten: las ya guardadas cuentan como vistas y
        # gastan su presupuesto. En una sesion propia: si la tabla aun no
        # existe, la transaccion abortada no se lleva la del spider.
        try:
            from shared.database import SessionLocal
            from shared.models import RespuestaSondeada

            sesion = SessionLocal()
            try:
                for url_hash, uso in (
                    sesion.query(RespuestaSondeada.url_hash, RespuestaSondeada.uso)
                    .filter(RespuestaSondeada.job_id == self.job_id)
                    .yield_per(10_000)
                ):
                    self._sr_vistas.add(_hash_key(url_hash))
                    if uso in self._sr_usadas:
                        self._sr_usadas[uso] += 1
            finally:
                sesion.close()
        except Exception as exc:  # noqa: BLE001 -- sin la tabla se empieza de cero
            logger.warning("No se pudieron leer las sondas de respuesta previas: %s", exc)

    def _sr_al_cerrar(self, motivo: str) -> None:
        """Lo pedido, lo que no se compruebo y lo que el cierre dejo en la cola.

        Las sondas van detras de las paginas (prioridad -100), asi que un
        rastreo que corta `max_urls` con frontera por delante cierra con ellas
        encoladas: sin esto el log decia «N pedidas» de sondas que nunca
        salieron.
        """
        logger.info("Sondas de respuesta: %s pedidas; sin comprobar %s",
                    self._sr_usadas, self._sr_sin_comprobar)
        if self._sr_sin_comprobar.get("excluida") and not any(self._sr_usadas.values()):
            logger.warning(
                "Sondas de respuesta: %d sin pedir porque `exclude_patterns` las excluye y ninguna "
                "pedida. Si se excluyen las imagenes por extension para no rastrearlas, para eso "
                "estan las casillas de `resource_types`: asi la sonda puede medirlas.",
                self._sr_sin_comprobar["excluida"])
        en_cola = self._sr_encoladas - self._sr_filas
        if en_cola > 0:
            logger.warning(
                "Sondas de respuesta: %d de %d sin respuesta al cerrar (%s): quedan sin fila, "
                "que es «no comprobado», no «bien»", en_cola, self._sr_encoladas, motivo)

    def _sondas_activas(self) -> bool:
        act = getattr(self, "_sr_activa", None)
        return bool(act) and any(act.values())

    # -- encolar ---------------------------------------------------------------

    def _sondas_de_pagina(self, links: list[dict], resources: list[dict]) -> Generator:
        """Las sondas de lo que enlaza y embebe una pagina interna."""
        if not self._sondas_activas():
            return
        act = self._sr_activa
        if act["enlace"]:
            for link in links:
                url = link.get("url") or ""
                if url and not self._is_internal(url):
                    yield from self._sondear(url, "enlace", interno=False)
        if act["recurso_interno"] or act["recurso_externo"]:
            for recurso in resources:
                url = recurso.get("url") or ""
                if not url:
                    continue
                interno = self._is_internal(url)
                if act["recurso_interno"] if interno else act["recurso_externo"]:
                    yield from self._sondear(url, "recurso", interno=interno)

    def _sonda_og_image(self, url: str | None, pagina: str) -> Generator:
        """La sonda que mide la og:image de una pagina interna (decision 154).

        Una por imagen distinta (la og:image por defecto del sitio se pide una
        vez) y como mucho `og_image_por_plantilla` imagenes NUEVAS por forma de
        ruta de la pagina: los avisos van por plantilla, y sin este tope las
        200 de la muestra serian las de las primeras paginas del rastreo.

        Best-effort con el `try` cubriendo la funcion ENTERA (40, 74): va en
        mitad de `parse`, y un fallo aqui se llevaria los items que quedan de
        la pagina (cabeceras de seguridad, contenido).
        """
        try:
            act = getattr(self, "_sr_activa", None)
            if not url or not act or not act.get("og_image"):
                return
            from shared.plantillas import firma_de_ruta

            partes = urlsplit(pagina)
            plantilla = ((partes.hostname or "").lower(), firma_de_ruta(partes.path or "/"))
            maximo = self._sr_og_por_plantilla_max
            if maximo and self._sr_og_plantillas.get(plantilla, 0) >= maximo:
                self._sr_sin_comprobar["og_por_plantilla"] += 1
                return
            antes = self._sr_encoladas
            peticiones = list(self._sondear(url, "og_image", interno=self._is_internal(url)))
            if self._sr_encoladas > antes:
                self._sr_og_plantillas[plantilla] = self._sr_og_plantillas.get(plantilla, 0) + 1
        except Exception:  # noqa: BLE001 -- una sonda no tumba la pagina
            logger.warning("Sonda de og:image: no se pudo encolar %s", url, exc_info=True)
            return
        yield from peticiones

    def _sondear(self, url: str, uso: str, *, interno: bool) -> Generator:
        partes = urlsplit(url)
        # Lista blanca de esquemas: `whatsapp://` o `intent://` salen como
        # enlaces externos y no se pueden pedir.
        if partes.scheme not in ("http", "https") or not partes.hostname:
            return
        if any(casa(url) for casa in (getattr(self, "_exclude_patterns", None) or [])):
            # «Esto no se toca» vale tambien para la sonda (124), pero se cuenta:
            # una config que excluye las imagenes por extension (UOC,
            # Workoholics) dejaba la og:image sin medir y el log decia «0
            # pedidas, 0 sin comprobar», como si no hubiera nada que medir.
            self._sr_sin_comprobar["excluida"] = self._sr_sin_comprobar.get("excluida", 0) + 1
            return
        from seo_crawler.extractors import compute_url_hash

        try:
            url_hash = compute_url_hash(url)
        except ValueError:
            return
        clave = _hash_key(url_hash)
        if clave in self._sr_vistas:
            return
        host = partes.hostname.lower()
        # Lo que no se pide no entra en `_sr_vistas`: pasado el tope, el
        # conjunto creceria con cada URL externa del sitio. Los contadores
        # cuentan apariciones, no URLs distintas.
        if not interno and not self._sr_red_privada and es_host_privado(host):
            self._sr_sin_comprobar["red_privada"] += 1
            return
        if self._sr_usadas[uso] >= self._sr_tope[uso]:
            self._sr_sin_comprobar["tope"] += 1
            if uso not in self._sr_tope_avisado:
                self._sr_tope_avisado.add(uso)
                logger.warning("Sondas de %s: tope de %d alcanzado; el resto queda sin "
                               "comprobar (y sin fila)", uso, self._sr_tope[uso])
            return
        # La og:image no lleva tope por host: vive en un CDN y ya tiene el suyo.
        if not interno and self._sr_por_host_max and uso != "og_image":
            if self._sr_por_host.get(host, 0) >= self._sr_por_host_max:
                self._sr_sin_comprobar["por_host"] += 1
                return
            self._sr_por_host[host] = self._sr_por_host.get(host, 0) + 1
        self._sr_vistas.add(clave)
        self._sr_usadas[uso] += 1
        self._sr_encoladas += 1
        ctx = {"url": url, "url_hash": url_hash, "uso": uso, "interno": interno,
               "host": host, "saltos": 0, "status": None}
        yield from self._pedir(url, ctx)

    def _es_red_interna(self, url: str) -> bool:
        """Si *url* apunta a la red interna y no es del sitio (decision 123)."""
        if self._sr_red_privada or self._is_internal(url):
            return False
        return es_host_privado(urlsplit(url).hostname)

    def _pedir(self, url: str, ctx: dict) -> Generator:
        """La peticion de *url* (la enlazada o un salto), o la espera por el
        robots.txt de su origen, o la fila si el origen ya esta caido."""
        if self._is_internal(url):
            yield self._peticion(url, ctx, _metodo(ctx), origen=None)
            return
        origen = _origen(url)
        est = self._sr_origenes.get(origen)
        if est is None:
            self._sr_origenes[origen] = {"estado": "robots", "robots": None,
                                         "espera": [(url, ctx)], "fallos": 0,
                                         "veredicto": None, "error": None}
            yield self._peticion_robots(origen)
            return
        if est["estado"] in ("robots", "probando"):
            est["espera"].append((url, ctx))
            return
        if est["estado"] == "caido":
            yield self._fila_de_caido(url, ctx, est)
            return
        yield from self._pedir_si_robots_permite(url, ctx, est, origen)

    def _pedir_si_robots_permite(self, url: str, ctx: dict, est: dict, origen: str) -> Generator:
        robots = est.get("robots")
        if robots is not None:
            try:
                permitido = robots.can_fetch(url, self._sr_ua())
            except Exception:  # noqa: BLE001 -- un robots raro no prohibe
                permitido = True
            if not permitido:
                yield self._fila(ctx, url, metodo=None, veredicto=S.PROHIBIDO_POR_ROBOTS,
                                 error=f"robots.txt de {origen}")
                return
        yield self._peticion(url, ctx, _metodo(ctx), origen=origen)

    def _peticion(self, url: str, ctx: dict, metodo: str, *, origen: str | None) -> scrapy.Request:
        meta: dict[str, Any] = {
            "depth": 0, "dont_redirect": True, "handle_httpstatus_all": True,
            # Los reintentos los decide la sonda (HEAD -> GET, host caido): el
            # RetryMiddleware repetiria tres veces el 429 de un tercero.
            "dont_retry": True,
            "sonda_respuesta": dict(ctx), "sonda_origen": origen,
        }
        if origen is not None:
            meta["dont_obey_robotstxt"] = True  # su robots ya lo miro la sonda
        cabeceras = None
        if metodo == "GET":
            cabeceras = {"Range": RANGO_IMAGEN if ctx.get("uso") == "og_image" else RANGO}
            meta["impersonate_curl_options"] = {"MAXFILESIZE_LARGE": MAX_BYTES_GET}
            meta["download_maxsize"] = MAX_BYTES_GET
        return scrapy.Request(url, method=metodo, headers=cabeceras,
                              callback=self._sonda_respuesta,
                              errback=self._sonda_respuesta_error,
                              meta=meta, dont_filter=True, priority=PRIORIDAD)

    def _peticion_robots(self, origen: str, url: str | None = None,
                         saltos: int = 0) -> scrapy.Request:
        # Las redirecciones del robots.txt (http -> https, como haria Google)
        # las sigue la sonda y no el RedirectMiddleware de Scrapy: el de Scrapy
        # iria a donde dijera el tercero, red interna incluida (SSRF).
        return scrapy.Request(
            url or f"{origen}/robots.txt", callback=self._sonda_robots,
            errback=self._sonda_robots_error,
            meta={"depth": 0, "dont_obey_robotstxt": True, "dont_retry": True,
                  "dont_redirect": True, "handle_httpstatus_all": True,
                  "sonda_robots": origen, "sonda_robots_saltos": saltos},
            dont_filter=True, priority=PRIORIDAD_ROBOTS)

    # -- filas -----------------------------------------------------------------

    def _fila(self, ctx: dict, url_actual: str | None = None, **campos):
        from seo_crawler.items import RespuestaSondeadaItem

        saltos = ctx.get("saltos") or 0
        self._sr_filas = getattr(self, "_sr_filas", 0) + 1
        fila = {
            "job_id": self.job_id, "url_hash": ctx["url_hash"], "url": ctx["url"],
            "host": ctx["host"], "uso": ctx["uso"], "interno": bool(ctx["interno"]),
            "status_code": ctx.get("status"), "saltos": saltos,
            "final_url": url_actual if saltos else None,
            "final_status": None, "metodo": None, "response_class": None,
            "host_caido": None, "content_type": None, "content_length": None, "error": None,
            "ancho": None, "alto": None,
        }
        fila.update(campos)
        if isinstance(fila.get("error"), str):
            fila["error"] = fila["error"][:300]
        return RespuestaSondeadaItem(**fila)

    def _fila_de_caido(self, url: str, ctx: dict, est: dict):
        return self._fila(ctx, url, metodo=None, veredicto=est["veredicto"], host_caido=True,
                          error=f"no pedida: el origen ya fallo ({est['error'] or est['veredicto']})")

    def _caer(self, origen: str, veredicto: str, error: str) -> list:
        """Da el origen por caido y devuelve las filas de lo que esperaba."""
        est = self._sr_origenes[origen]
        est.update(estado="caido", veredicto=veredicto, error=error)
        espera, est["espera"] = est["espera"], []
        logger.info("Sondas: origen %s caido (%s): %d URLs sin pedir", origen, veredicto,
                    len(espera))
        return [self._fila_de_caido(url, ctx, est) for url, ctx in espera]

    def _soltar(self, origen: str) -> Generator:
        est = self._sr_origenes[origen]
        est["estado"] = "abierto"
        espera, est["espera"] = est["espera"], []
        for url, ctx in espera:
            yield from self._pedir_si_robots_permite(url, ctx, est, origen)

    # -- latido ------------------------------------------------------------------

    def _sr_latir(self) -> None:
        ahora = time.monotonic()
        if ahora - getattr(self, "_sr_ultimo_latido", 0.0) < LATIDO_S:
            return
        self._sr_ultimo_latido = ahora
        self._write_heartbeat()
        if self._should_cancel():
            logger.info("Cancel signal received for job %s during probes, stopping", self.job_id)
            self.crawler.engine.close_spider(self, "cancelled")

    # -- DNS ---------------------------------------------------------------------

    async def _veredicto_de_fallo(self, tipo: str, url: str) -> str:
        if tipo == "dns":
            partes = urlsplit(url)
            host = (partes.hostname or "").lower()
            resultado = await self._resolver(host, partes.port or (443 if partes.scheme == "https" else 80))
            if resultado == "no_existe":
                return S.DOMINIO_INEXISTENTE
            # El resolver no contesto (EAI_AGAIN) o si resuelve y curl no: no
            # se sabe nada del dominio. `sin_conexion` lo contaria como muerto.
            return S.NO_VERIFICABLE
        return {"conexion": S.SIN_CONEXION, "tiempo": S.TIEMPO_AGOTADO,
                "tls": S.TLS_INVALIDO}.get(tipo, S.NO_VERIFICABLE)

    async def _resolver(self, host: str, puerto: int) -> str:
        """`no_existe`, `temporal` o `resuelve`, segun el resolver del sistema.

        curl dice «Could not resolve host» tanto para un dominio que no existe
        (NXDOMAIN) como para un resolver que no contesta. `getaddrinfo`
        distingue `EAI_NONAME`/`EAI_NODATA` (no existe) de `EAI_AGAIN`
        (temporal). En un hilo: bloquea, y el reactor no puede esperar.
        """
        if host in self._sr_resolucion:
            return self._sr_resolucion[host]

        def _consulta() -> str:
            try:
                socket.getaddrinfo(host, puerto, proto=socket.IPPROTO_TCP)
                return "resuelve"
            except socket.gaierror as exc:
                no_existe = {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}
                return "no_existe" if exc.errno in no_existe else "temporal"
            except Exception:  # noqa: BLE001
                return "temporal"

        try:
            from scrapy.utils.defer import maybe_deferred_to_future
            from twisted.internet.threads import deferToThread

            resultado = await maybe_deferred_to_future(deferToThread(_consulta))
        except Exception:  # noqa: BLE001
            resultado = "temporal"
        self._sr_resolucion[host] = resultado
        return resultado

    # -- robots.txt del tercero ----------------------------------------------------

    def _sonda_robots(self, response) -> Generator:
        self._sr_latir()
        origen = response.meta["sonda_robots"]
        est = self._sr_origenes[origen]
        if 300 <= response.status < 400 and response.headers.get(b"Location"):
            destino = response.urljoin(response.headers[b"Location"].decode("latin-1").strip())
            saltos = int(response.meta.get("sonda_robots_saltos") or 0) + 1
            # Google sigue cinco saltos del robots.txt; despues, sin reglas.
            if (saltos <= MAX_SALTOS_ROBOTS
                    and urlsplit(destino).scheme in ("http", "https")
                    and not self._es_red_interna(destino)):
                yield self._peticion_robots(origen, destino, saltos)
                return
            est["robots"] = None
            yield from self._soltar(origen)
            return
        parser = None
        if 200 <= response.status < 300 and response.body:
            try:
                from protego import Protego

                parser = Protego.parse(response.body.decode("utf-8", "ignore"))
            except Exception:  # noqa: BLE001 -- ilegible = sin reglas, como Scrapy
                parser = None
        # 4xx: no hay reglas. 5xx: Scrapy tambien lo trata como permitido; la
        # sonda es un HEAD por URL y con tope por host.
        est["robots"] = parser
        if parser is not None:
            self._sr_aplicar_crawl_delay(response.request, parser)
        yield from self._soltar(origen)

    def _sr_ua(self) -> str:
        return (getattr(self, "custom_user_agent", None)
                or (self.settings.get("USER_AGENT") if getattr(self, "settings", None) else None)
                or "*")

    def _sr_aplicar_crawl_delay(self, request, parser) -> None:
        """El `Crawl-delay` del tercero, como retraso de su slot (decision 81).

        Su robots.txt se respeta siempre (es la decision del tercero, no del
        cliente), y el `Crawl-delay` es parte de el: sin esto las 50 sondas de un
        host salian con la concurrencia por dominio del rastreo aunque pidiera
        una cada 10 s. Con el mismo tope que el del sitio y reaplicado tras cada
        respuesta, porque AUTOTHROTTLE reescribe `slot.delay`.
        """
        from seo_crawler.middlewares import CRAWL_DELAY_MAX_SEGUNDOS, crawl_delay_aplicable

        ajustes = getattr(self, "settings", None)
        tope = (ajustes.getfloat("ROBOTS_CRAWL_DELAY_MAX", CRAWL_DELAY_MAX_SEGUNDOS)
                if ajustes else CRAWL_DELAY_MAX_SEGUNDOS)
        retraso = crawl_delay_aplicable(parser, self._sr_ua(), tope)
        if retraso is None:
            return
        try:
            downloader = self.crawler.engine.downloader
            clave = downloader.get_slot_key(request)
        except Exception:  # noqa: BLE001 -- sin motor (tests del mixin)
            return
        if ajustes:
            retraso = max(retraso, ajustes.getfloat("DOWNLOAD_DELAY", 0.0))
        self._sr_retrasos[clave] = retraso
        por_slot = downloader.per_slot_settings.setdefault(clave, {})
        por_slot["delay"] = retraso
        por_slot["jitter"] = 0.0
        self._sr_reaplicar_retraso(clave)
        if not self._sr_senal_conectada:
            self._sr_senal_conectada = True
            from scrapy import signals

            self.crawler.signals.connect(self._sr_tras_descarga,
                                         signal=signals.response_downloaded)
        logger.info("Sondas: robots.txt de %s pide Crawl-delay; %.1f s entre sondas", clave,
                    retraso)

    def _sr_reaplicar_retraso(self, clave: str) -> None:
        retraso = self._sr_retrasos.get(clave)
        if retraso is None:
            return
        slot = self.crawler.engine.downloader.slots.get(clave)
        if slot is None:
            return
        if slot.delay < retraso:
            slot.delay = retraso
        slot.jitter = 0.0

    def _sr_tras_descarga(self, response, request, spider=None) -> None:
        clave = request.meta.get("download_slot")
        if clave is not None and clave in self._sr_retrasos:
            self._sr_reaplicar_retraso(clave)

    async def _sonda_robots_error(self, failure) -> AsyncGenerator:
        self._sr_latir()
        origen = failure.request.meta["sonda_robots"]
        est = self._sr_origenes[origen]
        texto = f"robots.txt: {type(failure.value).__name__}: {failure.value}"[:300]
        veredicto = await self._veredicto_de_fallo(clasificar_fallo(failure), failure.request.url)
        if veredicto == S.DOMINIO_INEXISTENTE:
            for fila in self._caer(origen, veredicto, texto):
                yield fila
            return
        # Sin robots por la red: se prueba una URL antes de soltar el resto.
        est.update(estado="probando", fallos=1, veredicto=veredicto, error=texto)
        if est["espera"]:
            url, ctx = est["espera"].pop(0)
            yield self._peticion(url, ctx, _metodo(ctx), origen=origen)
        else:
            est["estado"] = "abierto"

    # -- respuesta -------------------------------------------------------------------

    def _sonda_respuesta(self, response) -> Generator:
        self._sr_latir()
        ctx = dict(response.meta["sonda_respuesta"])
        origen = response.meta.get("sonda_origen")
        status = response.status
        metodo = response.request.method
        url_actual = response.request.url
        if ctx["status"] is None:
            ctx["status"] = status
        if origen is not None:
            est = self._sr_origenes.get(origen)
            if est is not None:
                est["fallos"] = 0
                if est["estado"] == "probando":
                    yield from self._soltar(origen)

        if 300 <= status < 400 and status != 304:
            loc = response.headers.get(b"Location")
            if loc:
                destino = response.urljoin(loc.decode("latin-1").strip())
                if ctx["saltos"] >= S.MAX_SALTOS:
                    yield self._fila(ctx, url_actual, metodo=metodo, final_status=status,
                                     veredicto=S.REDIRIGE_A_ERROR,
                                     error=f"mas de {S.MAX_SALTOS} redirecciones")
                    return
                ctx["saltos"] += 1
                if urlsplit(destino).scheme not in ("http", "https"):
                    yield self._fila(ctx, destino, metodo=metodo, veredicto=S.NO_VERIFICABLE,
                                     error="redirige a un esquema que no es web")
                    return
                if self._es_red_interna(destino):
                    # El filtro de `_sondear` mira la URL enlazada; un tercero
                    # puede redirigir a donde quiera (SSRF por redireccion).
                    yield self._fila(ctx, destino, metodo=metodo, final_status=status,
                                     veredicto=S.NO_VERIFICABLE,
                                     error="redirige a la red interna: no se sigue")
                    return
                ctx.pop("get_tras_fallo", None)
                yield from self._pedir(destino, ctx)
                return

        if metodo == "HEAD" and S.repetir_con_get(status):
            if not ctx["saltos"]:
                ctx["status"] = None  # el codigo de la URL enlazada es el del GET
            yield self._peticion(url_actual, ctx, "GET", origen=origen)
            return

        cab = _cabeceras(response)
        clase = None
        try:
            from shared.respuestas import clasificar_respuesta

            clase, _ = clasificar_respuesta(status, cab, response.body,
                                            content_type=cab.get("content-type"),
                                            url=url_actual)
        except Exception:  # noqa: BLE001
            clase = None
        veredicto = S.veredicto_final(status, clase, ctx["saltos"])
        tamano = S.tamano_de_cabeceras(cab, status, len(response.body) if metodo == "GET" else None)
        if veredicto != S.OK:
            logger.info("Sonda %s %s -> %s (%s)", ctx["uso"], ctx["url"], status, veredicto)
        medida = None
        if ctx["uso"] == "og_image" and metodo == "GET" and veredicto == S.OK:
            medida = dimensiones(response.body)  # None si no se puede leer; no lanza
        yield self._fila(ctx, url_actual, metodo=metodo,
                         final_status=status if ctx["saltos"] else None,
                         veredicto=veredicto, response_class=clase,
                         content_type=(cab.get("content-type") or None),
                         content_length=tamano,
                         ancho=medida[0] if medida else None,
                         alto=medida[1] if medida else None)

    async def _sonda_respuesta_error(self, failure) -> AsyncGenerator:
        self._sr_latir()
        req = failure.request
        ctx = dict(req.meta["sonda_respuesta"])
        origen = req.meta.get("sonda_origen")
        if failure.check(scrapy.exceptions.IgnoreRequest):
            # El robots del SITIO (recurso interno con robots_mode=respect).
            yield self._fila(ctx, req.url, metodo=None, veredicto=S.PROHIBIDO_POR_ROBOTS,
                             error="robots.txt del sitio")
            return
        tipo = clasificar_fallo(failure)
        texto = f"{type(failure.value).__name__}: {failure.value}"[:300]
        if tipo == "otro" and req.method == "HEAD" and not ctx.get("get_tras_fallo"):
            # Hay servidores que cortan la conexion a un HEAD: se prueba GET.
            ctx["get_tras_fallo"] = True
            yield self._peticion(req.url, ctx, "GET", origen=origen)
            return
        if tipo == "grande":
            yield self._fila(ctx, req.url, metodo=req.method, veredicto=S.OK,
                             content_length=None,
                             error=f"mas de {MAX_BYTES_GET // (1024 * 1024)} MB: no se descargo")
            return
        veredicto = await self._veredicto_de_fallo(tipo, req.url)
        extra: list = []
        caido = False
        if origen is not None and origen in self._sr_origenes:
            est = self._sr_origenes[origen]
            # Por el TIPO de fallo, no por el veredicto: un DNS que no contesta
            # (`no_verificable`) tambien es un origen al que no se llega, y se
            # deja de pedir, aunque no se afirme que este muerto.
            if tipo in ("dns", "conexion", "tiempo", "tls"):
                est["fallos"] = est.get("fallos", 0) + 1
                if est["estado"] != "caido" and (
                        veredicto == S.DOMINIO_INEXISTENTE or est["fallos"] >= 2):
                    extra = self._caer(origen, veredicto, texto)
                    caido = True
            elif est["estado"] == "probando":
                # Contesto algo (aunque roto): el origen esta vivo.
                for r in self._soltar(origen):
                    extra.append(r)
            # Lo que estaba en vuelo cuando el origen se dio por caido tambien
            # es del origen caido.
            caido = caido or (est["estado"] == "caido"
                              and tipo in ("dns", "conexion", "tiempo", "tls"))
        yield self._fila(ctx, req.url, metodo=req.method, veredicto=veredicto, error=texto,
                         host_caido=True if caido else None)
        for fila in extra:
            yield fila
