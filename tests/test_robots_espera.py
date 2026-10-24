"""robots.txt se respeta en TODAS las peticiones que esperan su descarga
(decision 164), EJECUTADO con un servidor local y un Scrapy de verdad.

El `RobotsTxtMiddleware` de Scrapy 2.19, con `async def start` (el arranque de
`SeoSpider`), dejaba pasar sin comprobar de la tercera peticion en adelante
mientras esperaban el robots.txt del host: el `Deferred` compartido se consumia
al convertirlo en futuro. En un subproceso: el reactor de Twisted no se puede
arrancar dos veces en el mismo proceso.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

SCRIPT = textwrap.dedent('''
    import socket, sys, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    pedidas = []
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/robots.txt":
                pedidas.append(self.path)
            cuerpo = b"User-agent: *\\nDisallow: /prohibido\\n" if self.path == "/robots.txt" else b"<html>x</html>"
            self.send_response(200); self.send_header("Content-Length", str(len(cuerpo))); self.end_headers()
            self.wfile.write(cuerpo)
        def log_message(self, *a): pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    puerto = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    import scrapy
    from scrapy.crawler import CrawlerProcess
    class S(scrapy.Spider):
        name = "s"
        async def start(self):
            for i in range(5):
                yield scrapy.Request(f"http://127.0.0.1:{puerto}/prohibido-{i}", dont_filter=True)
            yield scrapy.Request(f"http://127.0.0.1:{puerto}/permitida", dont_filter=True)
        def parse(self, response):
            pass
    middlewares = {}
    if sys.argv[1] == "nuestro":
        middlewares = {"scrapy.downloadermiddlewares.robotstxt.RobotsTxtMiddleware": None,
                       "seo_crawler.middlewares.RobotsTxtEsperaPropiaMiddleware": 100}
    p = CrawlerProcess({"ROBOTSTXT_OBEY": True, "LOG_LEVEL": "ERROR", "TELNETCONSOLE_ENABLED": False,
                        "CONCURRENT_REQUESTS_PER_DOMAIN": 16, "DOWNLOADER_MIDDLEWARES": middlewares,
                        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor"})
    p.crawl(S); p.start()
    print("PEDIDAS=" + ",".join(sorted(pedidas)))
''')


def _pedidas(modo: str, tmp_path) -> list[str]:
    script = tmp_path / "exp.py"
    script.write_text(SCRIPT)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(RAIZ / "crawler"), str(RAIZ)])}
    salida = subprocess.run([sys.executable, str(script), modo], capture_output=True, text=True,
                            timeout=120, env=env).stdout
    linea = next(l for l in salida.splitlines() if l.startswith("PEDIDAS="))
    return [x for x in linea.split("=", 1)[1].split(",") if x]


def test_ninguna_peticion_prohibida_se_cuela_mientras_llega_el_robots(tmp_path):
    assert _pedidas("nuestro", tmp_path) == ["/permitida"]


def test_el_de_scrapy_si_las_dejaba_pasar(tmp_path):
    """Documenta el fallo que se arregla. Si una version de Scrapy lo corrige,
    este test falla y el middleware propio puede volver al de Scrapy."""
    pedidas = _pedidas("scrapy", tmp_path)
    assert "/permitida" in pedidas and any(p.startswith("/prohibido") for p in pedidas)
