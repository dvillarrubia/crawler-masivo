"""Lanza el SeoSpider real contra un sitio servido en local y vuelca los items.

Se ejecuta en un subproceso (el reactor de Twisted no se puede reiniciar):

    python tests/spider_harness.py '<json con seeds y config>'

Imprime por stdout un JSON con las paginas y los enlaces emitidos. La BD y
Redis se simulan: el spider lee el job de una sesion falsa y trabaja sin
Redis, igual que cuando Redis no esta disponible en produccion.
"""

from __future__ import annotations

import json
import os
import sys
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "crawler"), os.path.dirname(os.path.abspath(__file__))):
    if path not in sys.path:
        sys.path.insert(0, path)

# Dependencias que solo existen en la imagen del crawler
try:
    import redis  # noqa: F401
except ImportError:
    _redis = types.ModuleType("redis")

    class _SinRedis:
        @classmethod
        def from_url(cls, *a, **k):
            raise ConnectionError("sin redis en los tests")

    _redis.Redis = _SinRedis
    sys.modules["redis"] = _redis
try:
    import scrapy_playwright.page  # noqa: F401
except ImportError:
    _sp = types.ModuleType("scrapy_playwright")
    _page = types.ModuleType("scrapy_playwright.page")

    class PageMethod:
        def __init__(self, *a, **k):
            pass

    _page.PageMethod = PageMethod
    sys.modules["scrapy_playwright"] = _sp
    sys.modules["scrapy_playwright.page"] = _page


# ---------------------------------------------------------------------------
# Sitio
# ---------------------------------------------------------------------------
def _html(cuerpo: str, head: str = "") -> bytes:
    return (
        f"<html><head><title>t</title>{head}</head><body>{cuerpo}</body></html>"
    ).encode("utf-8")


def construir_sitio(port: int, con_privado: bool = False,
                    con_recursos: bool = False) -> dict:
    otro = f"http://127.0.0.1:{port}"
    return {
        # Semilla en localhost que redirige a otro host (127.0.0.1)
        "/": (301, {"Location": f"{otro}/home"}, b""),
        "/home": (200, {}, _html(
            '<a href="/a">a</a>'
            '<a href="/a-old">a vieja</a>'
            '<a href="/mr">meta refresh</a>'
            '<a href="/bad">malformado</a>'
            '<a href="/tag/x">excluida</a>'
            '<a href="/rel">rel con comas</a>'
            + ('<a href="/recursos">recursos</a>' if con_recursos else '')
            + ('<a href="/privado/x">privada por robots</a>'
               '<a href="/publica-tras-privada">publica</a>' if con_privado else ''),
            f'<link rel="canonical" href="{otro}/home">',
        )),
        "/a": (200, {}, _html("<p>a</p>")),
        "/a-old": (301, {"Location": "/a"}, b""),
        "/mr": (200, {}, _html("<p>refresh</p>", '<meta http-equiv="refresh" content="0; url=/mr-dest">')),
        "/mr-dest": (200, {}, _html("<p>destino</p>")),
        "/bad": (200, {}, _html(
            '<a href="https://[LINK]/x">roto</a><a href="/after-bad">bien</a>'
            '<img srcset=" ">'
        )),
        "/after-bad": (200, {}, _html("<p>ok</p>")),
        "/tag/x": (200, {}, _html("<p>no deberia rastrearse</p>")),
        "/rel": (200, {}, _html('<a href="/nf" rel="nofollow,noopener">nf</a>')),
        "/nf": (200, {}, _html("<p>no deberia rastrearse</p>")),
        "/robots.txt": (200, {"Content-Type": "text/plain"},
                        f"User-agent: *\nSitemap: http://localhost:{port}/sitemap_index.xml\n".encode()),
        "/sitemap_index.xml": (200, {"Content-Type": "application/xml"}, (
            '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<sitemap><loc>http://localhost:{port}/sitemap-1.xml</loc></sitemap></sitemapindex>"
        ).encode()),
        "/sitemap-1.xml": (200, {"Content-Type": "application/xml"}, (
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>http://localhost:{port}/solo-sitemap</loc></url></urlset>"
        ).encode()),
        "/solo-sitemap": (200, {}, _html('<a href="/nivel2">n2</a>')),
        "/nivel2": (200, {}, _html("<p>n2</p>")),
        "/privado/x": (200, {}, _html("<p>no deberia pedirse en modo respect</p>")),
        "/publica-tras-privada": (200, {}, _html("<p>si</p>")),
        # Recursos para el filtro por tipo: enlazados desde /recursos
        "/recursos": (200, {}, _html(
            '<a href="/doc1.pdf">1</a><a href="/doc2.pdf">2</a>'
            '<a href="/descarga-1">d1</a><a href="/descarga-2">d2</a>'
            '<a href="/logo.svg">logo</a><a href="/pagina-real">real</a>')),
        "/doc1.pdf": (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4 x"),
        # Sin extension: el filtro de seguimiento no puede saber que es un
        # PDF hasta descargarlo, que es cuando el tipo gastaba presupuesto.
        "/descarga-1": (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4 d1"),
        "/descarga-2": (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4 d2"),
        "/doc2.pdf": (200, {"Content-Type": "application/pdf"}, b"%PDF-1.4 y"),
        "/logo.svg": (200, {"Content-Type": "image/svg+xml"}, b"<svg/>"),
        "/pagina-real": (200, {}, _html("<p>contenido</p>")),
    }


def servir(robots_prohibe_todo: bool = False,
           robots_bloquea_privado: bool = False,
           con_recursos: bool = False) -> tuple[ThreadingHTTPServer, int]:
    rutas: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            status, headers, body = rutas.get(self.path.split("?")[0], (404, {}, b"no"))
            self.send_response(status)
            headers = {"Content-Type": "text/html; charset=utf-8", **headers}
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    rutas.update(construir_sitio(port, con_privado=robots_bloquea_privado,
                                 con_recursos=con_recursos))
    if robots_bloquea_privado:
        # Un robots.txt que prohibe una carpeta concreta: el caso del informe
        # "Blocked by robots.txt" (R6 de #25). La URL la enlaza /home.
        rutas["/robots.txt"] = (
            200, {"Content-Type": "text/plain"},
            b"User-agent: *\nDisallow: /privado/\n",
        )
    if robots_prohibe_todo:
        # Un robots.txt que lo prohibe todo: el caso que acababa como un job
        # "completado" con cero URLs, indistinguible de un sitio limpio (#37).
        rutas["/robots.txt"] = (
            200, {"Content-Type": "text/plain"}, b"User-agent: *\nDisallow: /\n",
        )
    Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


# ---------------------------------------------------------------------------
# BD simulada y recolector de items
# ---------------------------------------------------------------------------
class _SesionFalsa:
    def __init__(self, job):
        self._job = job

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def one_or_none(self):
        return self._job

    def yield_per(self, *a):
        return iter(())

    def count(self):
        return 0

    def update(self, *a, **k):
        return 0

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


ITEMS: list[dict] = []


class Recolector:
    def process_item(self, item, spider):
        ITEMS.append({"_tipo": type(item).__name__, **dict(item)})
        return item


def main() -> None:
    entrada = json.loads(sys.argv[1])
    srv, port = servir(entrada.get("robots_prohibe_todo", False),
                       entrada.get("robots_bloquea_privado", False),
                       entrada.get("con_recursos", False))

    import shared.database
    from scrapy.crawler import CrawlerProcess

    job = types.SimpleNamespace(
        seeds=[s.replace("{port}", str(port)) for s in entrada["seeds"]],
        config=entrada.get("config", {}),
    )
    shared.database.SessionLocal = lambda: _SesionFalsa(job)

    from seo_crawler.spiders.seo_spider import SeoSpider

    if entrada.get("simular_navegador"):
        # Como con render JS: la redireccion no la sigue el spider sino quien
        # descarga (aqui el RedirectMiddleware de Scrapy, que rellena
        # redirect_urls igual que scrapy-playwright), y la peticion lleva
        # meta["playwright"].
        original = SeoSpider._page_request

        def _page_request(self, url, depth):
            req = original(self, url, depth)
            req.meta.pop("dont_redirect", None)
            req.meta["playwright"] = True
            return req

        SeoSpider._page_request = _page_request

    proceso = CrawlerProcess({
        "LOG_LEVEL": "WARNING",
        "TELNETCONSOLE_ENABLED": False,
        # Por defecto apagado para que el sitio de pruebas se rastree entero;
        # el escenario de `Disallow: /` lo enciende (ver robots_prohibe_todo).
        "ROBOTSTXT_OBEY": bool(
            entrada.get("robots_prohibe_todo") or entrada.get("robots_bloquea_privado")
        ) and entrada.get("robots_mode") != "audit",
        "ROBOTS_MODE": entrada.get("robots_mode", "respect"),
        "DOWNLOADER_MIDDLEWARES": {
            "seo_crawler.middlewares.RobotsAuditMiddleware": 100,
        },
        "HTTPERROR_ALLOW_ALL": True,
        "DEPTH_PRIORITY": 1,
        "REQUEST_FINGERPRINTER_IMPLEMENTATION": "2.7",
        "SPIDER_MIDDLEWARES": {
            "scrapy.spidermiddlewares.depth.DepthMiddleware": None,
            "seo_crawler.middlewares.DepthMiddleware": 900,
        },
        "ITEM_PIPELINES": {"spider_harness.Recolector": 1},
    })
    proceso.crawl(SeoSpider, job_id="test")
    proceso.start()
    srv.shutdown()

    base = f"127.0.0.1:{port}"
    salida = []
    import spider_harness
    for it in spider_harness.ITEMS:
        fila = {k: v for k, v in it.items() if k in (
            "_tipo", "url", "status_code", "redirect_url", "crawl_depth",
            "indexability_status", "is_internal", "to_url", "follow", "resource_type",
            "blocked_by_robots", "status_group",
        )}
        for k in ("url", "redirect_url", "to_url"):
            if fila.get(k):
                fila[k] = fila[k].replace(base, "OTRO").replace(f"localhost:{port}", "SEMILLA")
        salida.append(fila)
    print("@@ITEMS@@" + json.dumps(salida))


if __name__ == "__main__":
    main()
