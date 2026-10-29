"""El handler de curl reutiliza conexiones sin colar cookies (decisiones 221 y 114).

El `ImpersonateConMedidas` real contra un servidor local con keep-alive que
apunta el puerto de origen de cada peticion y sus cabeceras. En subproceso,
porque el reactor de Twisted no se reinicia.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("scrapy")
pytest.importorskip("scrapy_impersonate")
pytest.importorskip("curl_cffi")

GUION = textwrap.dedent('''
    import asyncio, json, sys, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    VISTAS = []

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            VISTAS.append({"puerto": self.client_address[1], "ruta": self.path,
                           "host": self.headers.get("Host", "").split(":")[0],
                           "cookie": self.headers.get("Cookie")})
            cuerpo = b"<html><body>ok</body></html>"
            self.send_response(200)
            if self.path.startswith("/pone"):
                self.send_header("Set-Cookie", "sesion=secreta; Path=/")
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    puerto = srv.server_address[1]

    from scrapy.utils.reactor import install_reactor
    install_reactor("twisted.internet.asyncioreactor.AsyncioSelectorReactor")
    from scrapy import Request
    from scrapy.utils.defer import deferred_from_coro
    from scrapy.utils.test import get_crawler
    from twisted.internet import reactor
    from seo_crawler import handlers

    async def bajar(h, url, **kw):
        r = Request(url, meta={"impersonate": "chrome124"}, **kw)
        return await h._download_request(r)

    async def todo():
        try:
            h = handlers._clase_con_medidas().from_crawler(
                get_crawler(settings_dict={"CONCURRENT_REQUESTS": 2}))
            base = f"http://127.0.0.1:{puerto}"
            # 20 paginas de dos en dos
            sem = asyncio.Semaphore(2)
            async def una(i):
                async with sem:
                    await bajar(h, f"{base}/p{i}")
            await asyncio.gather(*(una(i) for i in range(20)))
            # una cookie que pone el servidor y otra que manda Scrapy a un host
            await bajar(h, f"{base}/pone")
            await bajar(h, f"{base}/despues")
            await bajar(h, f"{base}/con-cookie", headers={"Cookie": "login=1"})
            await bajar(h, f"http://localhost:{puerto}/otro-host")
            await bajar(h, f"{base}/sin-cookie")
            await h.close()
            print("@@VISTAS@@" + json.dumps(VISTAS))
        finally:
            reactor.stop()

    reactor.callWhenRunning(lambda: deferred_from_coro(todo()))
    reactor.run()
''')


@pytest.fixture(scope="module")
def vistas():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    entorno = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [raiz, os.path.join(raiz, "crawler"), os.environ.get("PYTHONPATH", "")])}
    proc = subprocess.run([sys.executable, "-c", GUION], capture_output=True, text=True,
                          timeout=120, env=entorno)
    assert "@@VISTAS@@" in proc.stdout, proc.stdout + proc.stderr[-4000:]
    return json.loads(proc.stdout.split("@@VISTAS@@", 1)[1].split("\n", 1)[0])


def test_veinte_paginas_van_por_pocas_conexiones(vistas):
    puertos = {v["puerto"] for v in vistas if v["ruta"].startswith("/p") and v["ruta"] != "/pone"}
    assert len([v for v in vistas if v["ruta"].startswith("/p") and v["ruta"] != "/pone"]) == 20
    # Antes, 20 conexiones (una sesion por peticion). Con concurrencia 2, como
    # mucho un par mas por las carreras del arranque.
    assert len(puertos) <= 4, puertos


def test_la_cookie_del_servidor_no_se_guarda_en_la_sesion(vistas):
    por_ruta = {v["ruta"]: v for v in vistas}
    assert por_ruta["/despues"]["cookie"] is None


def test_la_cookie_de_una_peticion_no_pasa_a_la_siguiente(vistas):
    por_ruta = {v["ruta"]: v for v in vistas}
    assert por_ruta["/con-cookie"]["cookie"] == "login=1"
    assert por_ruta["/otro-host"]["host"] == "localhost"
    assert por_ruta["/otro-host"]["cookie"] is None
    assert por_ruta["/sin-cookie"]["cookie"] is None
