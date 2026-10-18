"""El handler de produccion mide lo que antes se perdia (decisiones 109-111).

El SeoSpider real con el `CompositeDownloadHandler` (curl_cffi con la huella
de Chrome) contra servidores locales: TTFB con 300 ms de espera, gzip, un 429
con `Retry-After` en segundos y en fecha HTTP, y un HTTPS con el certificado
caducado. En subproceso, porque el reactor de Twisted no se reinicia.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")
pytest.importorskip("scrapy_impersonate")
pytest.importorskip("curl_cffi")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")

PAGINA = "<html><head><title>t</title></head><body>" + "<p>hola mundo</p>" * 400 + "</body></html>"


def _rastrear(**extra):
    entrada = {"config": {"max_depth": 1, "use_sitemap": False,
                          "crawl_behavior": {"soft404_sondas": 0}},
               "handler_real": True, **extra}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=180)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr[-4000:]
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    paginas = {i["url"]: i for i in items if i["_tipo"] == "PageItem"}
    marcas = {}
    for marca in ("REDIS", "JOB", "PETICIONES"):
        if f"@@{marca}@@" in proc.stdout:
            marcas[marca] = json.loads(proc.stdout.split(f"@@{marca}@@", 1)[1].split("\n", 1)[0])
    return paginas, marcas, proc.stderr


@pytest.fixture(scope="module")
def medidas():
    rutas = {
        "/m/inicio": [200, {}, '<a href="/m/lenta">l</a><a href="/m/gz">g</a>'
                              '<a href="/m/rapida">r</a>'],
        "/m/lenta": [200, {"_retardo_ms": "300"}, PAGINA],
        "/m/gz": [200, {"_gzip": "1"}, PAGINA],
        "/m/rapida": [200, {}, PAGINA],
    }
    return _rastrear(seeds=["http://127.0.0.1:{port}/m/inicio"], rutas=rutas)


def test_ttfb_real_con_300_ms_de_espera(medidas):
    paginas, _, _ = medidas
    lenta = paginas["http://OTRO/m/lenta"]
    assert 250 <= lenta["ttfb_ms"] <= 450, lenta
    assert paginas["http://OTRO/m/rapida"]["ttfb_ms"] < 150


def test_content_encoding_servido(medidas):
    paginas, _, _ = medidas
    assert paginas["http://OTRO/m/gz"]["content_encoding"] == "gzip"
    # Sin cabecera: aqui SI se sabe que llego sin comprimir
    assert paginas["http://OTRO/m/rapida"]["content_encoding"] == "identity"


def test_http_version_sin_render(medidas):
    """Antes vacia siempre sin render: scrapy-impersonate la descartaba."""
    paginas, _, _ = medidas
    # BaseHTTPRequestHandler responde HTTP/1.0
    assert paginas["http://OTRO/m/rapida"]["http_version"] == "HTTP/1.0"


@pytest.fixture(scope="module")
def limitada():
    rutas = {
        "/r/inicio": [200, {}, '<a href="/r/segundos">s</a><a href="/r/fecha">f</a>'],
        "/r/segundos": [429, {"Retry-After": "1", "_primeras": "2"}, PAGINA],
        "/r/fecha": [503, {"Retry-After": "+2", "_primeras": "1"}, PAGINA],
    }
    return _rastrear(seeds=["http://127.0.0.1:{port}/r/inicio"], rutas=rutas,
                     retry_after=True, redis_falso=True)


def test_429_con_retry_after_espera_y_acaba_en_200(limitada):
    paginas, marcas, _ = limitada
    assert paginas["http://OTRO/r/segundos"]["status_code"] == 200
    assert paginas["http://OTRO/r/fecha"]["status_code"] == 200
    resumen = json.loads(marcas["REDIS"]["job:test:retry_after"])
    assert resumen["veces"] == 3 and resumen["truncados"] == 0
    assert 1.0 <= resumen["max_pedido_s"] <= 2.5


def test_el_reintento_respeta_la_pausa_en_el_servidor(limitada):
    """Medido donde importa, en el servidor: el hueco entre el 429 y su
    reintento. Sin el middleware, RetryMiddleware lo repetia al instante."""
    _, marcas, _ = limitada
    def instantes(ruta):
        return [t for t, _h, r in marcas["PETICIONES"] if r == ruta]

    seg = instantes("/r/segundos")
    assert len(seg) == 3
    assert seg[1] - seg[0] >= 0.9 and seg[2] - seg[1] >= 0.9
    fecha = instantes("/r/fecha")
    assert len(fecha) == 2 and fecha[1] - fecha[0] >= 1.0


@pytest.fixture(scope="module")
def caducado():
    return _rastrear(seeds=["https://localhost:{tls_port}/"], tls_caducado=True,
                     ver_job=True)


def test_certificado_caducado_es_tls_error_con_su_motivo(caducado):
    paginas, _, _ = caducado
    (pagina,) = [p for u, p in paginas.items() if u.startswith("https://localhost:")]
    assert pagina["status_group"] == "tls_error"
    assert pagina["status_text"].startswith("TLS: curl 60:")


def test_la_sonda_tls_deja_la_ficha_del_host(caducado):
    _, marcas, _ = caducado
    fichas = [a["tls"]["hosts"]["localhost"] for a in marcas["JOB"] if "tls" in a]
    assert fichas, marcas["JOB"]
    ficha = fichas[-1]
    # Autofirmado y caducado: con el almacen del sistema manda «autofirmado»;
    # la sonda con una CA propia (test_sonda_tls.py) da «caducado».
    assert ficha["verificado"] is False and ficha["motivo"] in ("autofirmado", "caducado")
    assert ficha["dias_restantes"] < 0 and ficha["version"].startswith("TLSv1.")
    assert ficha["fuente"] == "socket"


# -- Las piezas, sin red ------------------------------------------------------

def test_medidas_de_curl():
    from curl_cffi import CurlInfo

    from seo_crawler.handlers import medidas_de_curl

    m = medidas_de_curl({CurlInfo.PRETRANSFER_TIME: 0.05, CurlInfo.STARTTRANSFER_TIME: 0.35},
                        cabecera_encoding="GZIP", download_size=150, http_version=3)
    assert m == {"ttfb_s": pytest.approx(0.30), "content_encoding_servido": "gzip",
                 "bytes_comprimidos": 150, "http_protocol": "HTTP/2"}
    # Sin tiempos ni version: no se inventan; sin cabecera, identity
    m = medidas_de_curl({}, cabecera_encoding=None, download_size=None, http_version=0)
    assert m == {"content_encoding_servido": "identity"}


def _respuesta(meta):
    from scrapy.http import HtmlResponse, Request

    return HtmlResponse("https://x.com/p", body=b"<html></html>",
                        request=Request("https://x.com/p", meta=meta))


def test_medidas_de_respuesta_sin_render_y_con_render():
    from scrapy_playwright.page import PageMethod

    from seo_crawler.spiders import seo_spider as mod

    assert mod.medidas_de_respuesta(_respuesta(
        {"ttfb_s": 0.3123, "content_encoding_servido": "br"})) == (312.3, "br")
    # La repeticion sin render de un job con render no da TTFB (decision 17)
    assert mod.medidas_de_respuesta(_respuesta(
        {"ttfb_s": 0.3, "content_encoding_servido": "gzip", "_sin_render": True})) == (None, "gzip")

    def con(valor):
        metodo = PageMethod(mod._evaluar_tolerante, mod._JS_TIEMPOS_DOCUMENTO)
        metodo.result = valor
        return {"playwright": True, "playwright_page_methods": [metodo]}

    assert mod.medidas_de_respuesta(_respuesta(con(
        {"ttfb": 301.2, "codificado": 157, "decodificado": 34026}))) == (301.2, "comprimido")
    assert mod.medidas_de_respuesta(_respuesta(con(
        {"ttfb": 5, "codificado": 34026, "decodificado": 34026}))) == (5.0, "identity")
    assert mod.medidas_de_respuesta(_respuesta(con(None))) == (None, None)
    # Con render, lo que haya dejado curl en meta no cuenta
    assert mod.medidas_de_respuesta(_respuesta({"playwright": True, "ttfb_s": 1})) == (None, None)


def test_el_metodo_de_tiempos_va_en_el_render():
    import shared.database

    shared.database.SessionLocal = lambda: None
    from seo_crawler.spiders.seo_spider import _JS_TIEMPOS_DOCUMENTO, SeoSpider

    meta = SeoSpider(job_id="t")._playwright_meta()
    assert any(_JS_TIEMPOS_DOCUMENTO in (m.args or ()) for m in meta["playwright_page_methods"])


def _lanzar_chromium(p):
    """El Chromium de Playwright o, si su version no esta descargada, el que
    haya en PLAYWRIGHT_BROWSERS_PATH."""
    import glob

    async def lanzar():
        try:
            return await p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            raiz = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser(
                "~/.cache/ms-playwright")
            for exe in sorted(glob.glob(os.path.join(raiz, "chromium-*", "chrome-linux*", "chrome")),
                              reverse=True):
                try:
                    return await p.chromium.launch(executable_path=exe)
                except Exception:  # noqa: BLE001
                    continue
            pytest.skip(f"sin Chromium: {exc}")
    return lanzar()


def test_navigation_timing_da_ttfb_y_compresion_con_chromium():
    """El JS de verdad contra Chromium, con la interceptacion de peticiones
    que pone scrapy-playwright: 300 ms de espera, gzip y sin comprimir."""
    import asyncio
    import gzip
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    pw = pytest.importorskip("playwright.async_api")
    from seo_crawler.spiders.seo_spider import _JS_TIEMPOS_DOCUMENTO

    cuerpo = PAGINA.encode()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            time.sleep(0.3)
            datos = gzip.compress(cuerpo) if self.path == "/gz" else cuerpo
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            if self.path == "/gz":
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    async def correr():
        async with pw.async_playwright() as p:
            navegador = await _lanzar_chromium(p)
            pagina = await navegador.new_page()
            await pagina.route("**/*", lambda r: r.continue_())
            vistos = {}
            for ruta in ("/gz", "/plano"):
                await pagina.goto(base + ruta, wait_until="domcontentloaded")
                vistos[ruta] = await pagina.evaluate(_JS_TIEMPOS_DOCUMENTO)
            await navegador.close()
            return vistos

    try:
        vistos = asyncio.run(correr())
    finally:
        srv.shutdown()
    assert 250 <= vistos["/gz"]["ttfb"] <= 450 and 250 <= vistos["/plano"]["ttfb"] <= 450
    assert vistos["/gz"]["codificado"] < vistos["/gz"]["decodificado"] == len(cuerpo)
    assert vistos["/plano"]["codificado"] == vistos["/plano"]["decodificado"] == len(cuerpo)
