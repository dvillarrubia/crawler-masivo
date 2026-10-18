"""
Composite download handler: routes Playwright-tagged requests through
headless Chromium and everything else through curl_cffi with browser
TLS impersonation (JA3/JA4 fingerprint bypass).

Normal HTTP requests get a real Chrome TLS fingerprint + HTTP/2 without
the overhead of launching a browser.
"""

from __future__ import annotations

import logging
import time

from scrapy import Request
from scrapy.crawler import Crawler

from shared.perfiles import IMPERSONATE_POR_DEFECTO, cabeceras_de_huella

logger = logging.getLogger(__name__)


def _casar_client_hints(request: Request) -> None:
    """`sec-ch-ua-platform` del sistema que dice el UA (decision 132).

    curl_cffi manda las Client Hints de su perfil, y las de Chrome de escritorio
    dicen `"macOS"`; el UA de serie es de Windows. Si la peticion ya trae la
    cabecera (la puso alguien a proposito), no se toca.
    """
    if request.headers.get(b"Sec-Ch-Ua-Platform"):
        return
    ua = request.headers.get(b"User-Agent")
    ua = ua.decode("latin-1") if isinstance(ua, bytes) else ua
    plataforma = cabeceras_de_huella(request.meta.get("impersonate"), ua).get("Sec-Ch-Ua-Platform")
    if plataforma:
        request.headers[b"Sec-Ch-Ua-Platform"] = plataforma

# CURLINFO_HTTP_VERSION de libcurl -> lo que se guarda en `urls.http_version`.
_VERSION_HTTP_CURL = {1: "HTTP/1.0", 2: "HTTP/1.1", 3: "HTTP/2", 30: "HTTP/3"}

# Lo que el handler deja en `request.meta` de cada respuesta (decision 109).
# Se BORRA al empezar cada descarga: RetryMiddleware copia la peticion con su
# meta, y el reintento de un 503 heredaria el TTFB del 503.
CLAVES_DE_MEDIDA = ("ttfb_s", "content_encoding_servido", "bytes_comprimidos",
                    "http_protocol")


def medidas_de_curl(infos: dict, *, cabecera_encoding: str | None,
                    download_size: int | None, http_version) -> dict:
    """Las medidas de una respuesta de curl_cffi, como van a `request.meta`.

    - ``ttfb_s``: STARTTRANSFER - PRETRANSFER, el tiempo del SERVIDOR (y de la
      red de ida y vuelta) sin DNS, TCP ni TLS, que con una sesion nueva por
      peticion es lo que mas pesa en `download_latency`. None si curl no lo dio.
    - ``content_encoding_servido``: la cabecera tal cual (en minusculas) o
      ``identity`` si no venia: aqui SI se sabe que llego sin comprimir.
    - ``bytes_comprimidos``: lo que bajo por el cable, sin cabeceras.
    - ``http_protocol``: la version negociada (`HTTP/2`), que
      scrapy-impersonate descartaba y dejaba la columna vacia sin render.
    """
    from curl_cffi import CurlInfo

    medidas: dict = {}
    inicio = infos.get(CurlInfo.PRETRANSFER_TIME)
    primer_byte = infos.get(CurlInfo.STARTTRANSFER_TIME)
    if isinstance(inicio, (int, float)) and isinstance(primer_byte, (int, float)) \
            and primer_byte > 0 and primer_byte >= inicio:
        medidas["ttfb_s"] = primer_byte - inicio
    encoding = (cabecera_encoding or "").strip().lower()
    medidas["content_encoding_servido"] = encoding[:32] or "identity"
    if isinstance(download_size, int) and download_size >= 0:
        medidas["bytes_comprimidos"] = download_size
    version = _VERSION_HTTP_CURL.get(int(http_version)) if isinstance(http_version, int) else None
    if version:
        medidas["http_protocol"] = version
    return medidas


def _clase_con_medidas():
    """`ImpersonateDownloadHandler` con las medidas de la decision 109.

    Se construye al pedirla para que importar este modulo no exija
    scrapy-impersonate (los tests unitarios montan el spider sin el).

    Sobrescribe `_download_request`, que es PRIVADO: es el de
    scrapy-impersonate 1.10.0 (fijado en `crawler/requirements.txt`) con tres
    cambios —`curl_infos` en la sesion, las medidas a `request.meta` antes de
    que se quite `Content-Encoding`, y la limpieza de las del intento
    anterior—. Si se sube la version, hay que volver a copiarlo.
    """
    from curl_cffi import CurlInfo
    from curl_cffi.requests import AsyncSession
    from scrapy.http.headers import Headers
    from scrapy.responsetypes import responsetypes
    from scrapy_impersonate import ImpersonateDownloadHandler
    from scrapy_impersonate.parser import CurlOptionsParser, RequestParser

    infos_pedidas = [CurlInfo.PRETRANSFER_TIME, CurlInfo.STARTTRANSFER_TIME]

    class ImpersonateConMedidas(ImpersonateDownloadHandler):
        async def _download_request(self, request: Request):
            for clave in CLAVES_DE_MEDIDA:
                request.meta.pop(clave, None)
            request_copy = request.copy()
            curl_options = CurlOptionsParser(request_copy).as_dict()

            async with AsyncSession(max_clients=1, curl_options=curl_options,
                                    curl_infos=infos_pedidas) as client:
                request_args = RequestParser(request_copy).as_dict()
                start_time = time.time()
                response = await client.request(**request_args)
                download_latency = time.time() - start_time

            try:
                request.meta.update(medidas_de_curl(
                    getattr(response, "infos", None) or {},
                    cabecera_encoding=response.headers.get("content-encoding"),
                    download_size=getattr(response, "download_size", None),
                    http_version=getattr(response, "http_version", None),
                ))
            except Exception:  # noqa: BLE001 -- medir no tumba una descarga
                logger.debug("No se pudieron leer las medidas de curl", exc_info=True)

            headers = Headers(response.headers.multi_items())
            headers.pop("Content-Encoding", None)
            request_bytes = response.request_size
            response_bytes = response.header_size + response.download_size
            self._crawler.stats.inc_value("impersonate/request_bytes", request_bytes)
            self._crawler.stats.inc_value("impersonate/response_bytes", response_bytes)
            request.meta["impersonate_request_bytes"] = request_bytes
            request.meta["impersonate_response_bytes"] = response_bytes

            respcls = responsetypes.from_args(
                headers=headers, url=response.url, body=response.content)
            resp = respcls(
                url=response.url, status=response.status_code, headers=headers,
                body=response.content, flags=["impersonate"], request=request)
            resp.meta["download_latency"] = download_latency
            return resp

    return ImpersonateConMedidas


class CompositeDownloadHandler:
    """Playwright for JS pages, curl_cffi impersonate for everything else.

    Compatible with Scrapy 2.15+ (async download_request, no spider arg)
    while delegating to sub-handlers that may still use the old signature.
    """

    lazy = False  # Scrapy 2.15 wants this attribute

    def __init__(self, crawler: Crawler, playwright_handler, impersonate_handler):
        self._crawler = crawler
        self._playwright = playwright_handler
        self._impersonate = impersonate_handler

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        from scrapy_playwright.handler import ScrapyPlaywrightDownloadHandler

        pw = ScrapyPlaywrightDownloadHandler.from_crawler(crawler)
        imp = _clase_con_medidas().from_crawler(crawler)
        logger.info(
            "CompositeDownloadHandler ready: Playwright + curl_cffi (%s)",
            crawler.settings.get("IMPERSONATE", IMPERSONATE_POR_DEFECTO),
        )
        return cls(crawler, pw, imp)

    async def download_request(self, request: Request, spider=None):
        es_playwright = bool(request.meta.get("playwright"))
        if not es_playwright and not request.meta.get("impersonate"):
            # scrapy-impersonate solo usa curl_cffi si la peticion lleva
            # meta["impersonate"]; sin esto caia al handler de Twisted y el
            # fingerprint de Chrome no se aplicaba nunca (un F5 que responde
            # basura a la TLS de Twisted tumbaba el rastreo entero).
            request.meta["impersonate"] = self._crawler.settings.get(
                "IMPERSONATE", IMPERSONATE_POR_DEFECTO
            )
        if not es_playwright:
            _casar_client_hints(request)
        handler = self._playwright if es_playwright else self._impersonate
        # Sub-handlers may use old signature (request, spider) or new (request).
        # Always pass spider when we have it; fall back to crawler.spider.
        _spider = spider or getattr(self._crawler, "spider", None)
        method = handler.download_request
        start = time.monotonic()
        try:
            result = method(request, _spider)
        except TypeError:
            # Handler doesn't accept spider arg (new-style)
            result = method(request)
        response = await result if hasattr(result, "__await__") else result
        elapsed = time.monotonic() - start

        # Guarantee a real response time even for sub-handlers that don't set
        # download_latency themselves (e.g. Playwright). Without this,
        # JS-rendered pages report response_time_ms=0. response.meta IS
        # request.meta, so the spider reads this back downstream.
        if not request.meta.get("download_latency"):
            request.meta["download_latency"] = elapsed

        # Preserve the HTTP protocol version when a sub-handler exposes it,
        # so it survives into the spider even if the response object varies.
        protocol = getattr(response, "protocol", None)
        if protocol and not request.meta.get("http_protocol"):
            request.meta["http_protocol"] = protocol

        return response

    async def close(self):
        await self._playwright.close()
        if hasattr(self._impersonate, "close"):
            result = self._impersonate.close()
            if hasattr(result, "__await__"):
                await result
