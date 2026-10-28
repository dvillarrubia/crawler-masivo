"""Memoria del rastreo con una frontera enorme (#276).

Un sitio sintetico en el que cada pagina enlaza a N paginas nuevas: con
`--paginas 5000 --enlaces 200`, al llegar a `max_urls` quedan ~1 millon de
URLs descubiertas sin pedir. Mide el RSS del proceso de Scrapy mientras
rastrea, con la cola y el dupefilter de antes (memoria) o los de ahora (JOBDIR
y `DupefilterCompacto`). Sin BD ni Redis: el job sale de una sesion falsa y los
items no se guardan (si no, lo que se mediria seria la lista de items).

    docker run --rm -m 6g -v "$PWD":/repo -w /repo -e PYTHONPATH=/repo:/repo/crawler:/repo/tests \
      crawler-masivo-crawler python scripts/bench/frontera_memoria.py --modo ahora
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def servir(puerto: int, enlaces: int) -> None:
    class Manejador(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802
            if not self.path.startswith("/p/") or not self.path[3:].isdigit():
                cuerpo, estado = b"no", 404
            else:
                n = int(self.path[3:])
                hijos = "".join(f'<li><a href="/p/{n * enlaces + k + 1}">pagina {n * enlaces + k + 1}</a></li>'
                                for k in range(enlaces))
                cuerpo = (f"<html><head><title>Pagina {n}</title></head><body><main><h1>Pagina {n}</h1>"
                          f"<p>{'texto de relleno ' * 40}</p><ul>{hijos}</ul></main></body></html>").encode()
                estado = 200
            self.send_response(estado)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *a):
            pass

    ThreadingHTTPServer(("127.0.0.1", puerto), Manejador).serve_forever()


def _rss_mb() -> float:
    with open("/proc/self/status") as f:
        for linea in f:
            if linea.startswith("VmRSS:"):
                return int(linea.split()[1]) / 1024
    return 0.0


def rastrear(args) -> None:
    import types

    import shared.database
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings
    from spider_harness import _SesionFalsa

    os.environ["SCRAPY_SETTINGS_MODULE"] = "seo_crawler.settings"
    job = types.SimpleNamespace(
        seeds=[f"http://127.0.0.1:{args.puerto}/p/0"],
        config={"max_urls": args.paginas, "max_depth": 50, "use_sitemap": False},
    )
    shared.database.SessionLocal = lambda: _SesionFalsa(job)
    from seo_crawler.spiders.seo_spider import SeoSpider

    ajustes = get_project_settings()
    ajustes.setdict({
        "LOG_LEVEL": "WARNING", "TELNETCONSOLE_ENABLED": False, "ITEM_PIPELINES": {},
        "ROBOTSTXT_OBEY": False, "AUTOTHROTTLE_ENABLED": False, "DOWNLOAD_DELAY": 0,
        "CONCURRENT_REQUESTS": 16, "CONCURRENT_REQUESTS_PER_DOMAIN": 16,
    }, priority="cmdline")
    if args.modo == "antes":
        ajustes.set("DUPEFILTER_CLASS", "scrapy.dupefilters.RFPDupeFilter", priority="cmdline")
    else:
        ajustes.set("JOBDIR", tempfile.mkdtemp(prefix="bench-frontera-"), priority="cmdline")

    serie: list[tuple[float, int, float]] = []
    inicio = time.monotonic()

    def muestrear(spider):
        stats = spider.crawler.stats
        serie.append((round(time.monotonic() - inicio, 1),
                      stats.get_value("response_received_count", 0), round(_rss_mb())))

    original = SeoSpider.parse

    def parse(self, response, *a, **k):
        n = self.crawler.stats.get_value("response_received_count", 0)
        if n % 250 == 0:
            muestrear(self)
        yield from original(self, response, *a, **k)

    SeoSpider.parse = parse
    cierre = {}

    def cerrado(self, spider, reason):
        muestrear(self)
        cierre.update({k: v for k, v in self.crawler.stats.get_stats().items()
                       if k.startswith(("scheduler/", "dupefilter/", "response_received"))})
        cierre["reason"] = reason

    SeoSpider.spider_closed = cerrado
    proceso = CrawlerProcess(ajustes)
    proceso.crawl(SeoSpider, job_id="bench")
    proceso.start()
    import resource

    pico = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    disco = None
    if ajustes.get("JOBDIR"):
        disco = int(subprocess.run(["du", "-sm", ajustes.get("JOBDIR")],
                                   capture_output=True, text=True).stdout.split()[0] or 0)
    print("@@BENCH@@" + json.dumps({"modo": args.modo, "pico_mb": round(pico), "jobdir_mb": disco,
                                    "segundos": round(time.monotonic() - inicio), "serie": serie,
                                    "stats": cierre}, default=str))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--modo", choices=["antes", "ahora"], required=True)
    p.add_argument("--paginas", type=int, default=5000)
    p.add_argument("--enlaces", type=int, default=200)
    p.add_argument("--puerto", type=int, default=8765)
    p.add_argument("--servidor", action="store_true")
    args = p.parse_args()
    if args.servidor:
        servir(args.puerto, args.enlaces)
        return
    srv = subprocess.Popen([sys.executable, __file__, "--servidor", "--modo", args.modo,
                            "--puerto", str(args.puerto), "--enlaces", str(args.enlaces)])
    try:
        time.sleep(1)
        rastrear(args)
    finally:
        srv.kill()


if __name__ == "__main__":
    main()
