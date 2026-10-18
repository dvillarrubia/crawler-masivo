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
import time
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


_DESAFIO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "respuestas", "cloudflare_desafio_progym.html")


def construir_sitio_con_muro(n_muros: int = 30, codigo: int = 403) -> dict:
    """Un sitio detras de Cloudflare (decision 103): la home se sirve, el resto
    responde con el desafio REAL descargado de progym.es el 2026-10-16."""
    with open(_DESAFIO, "rb") as f:
        desafio = f.read()
    cabeceras = {"cf-mitigated": "challenge", "Server": "cloudflare"}
    if codigo == 429:
        cabeceras, desafio = {"Retry-After": "30"}, _html("<p>Too many requests</p>")
    rutas = {
        "/muro-home": (200, {}, _html(
            "".join(f'<a href="/muro/{i}">m{i}</a>' for i in range(n_muros))
            + '<a href="/muro-200">200</a><a href="/binaria">bin</a>'
              '<a href="/articulo-captcha">art</a>')),
        # El desafio servido con 200 y sin la cabecera: lo decide el cuerpo.
        "/muro-200": (200, {}, desafio if codigo != 429 else _html("<p>x</p>")),
        # El caso de la decision 11: HTML declarado y bytes binarios.
        "/binaria": (200, {}, bytes(range(256)) * 16),
        # Un articulo que HABLA de captchas no es un captcha.
        "/articulo-captcha": (200, {}, _html(
            "<h1>Cómo funciona un captcha</h1>"
            + "<p>Un captcha es una prueba para distinguir personas de bots. "
              "Google reCAPTCHA y hCaptcha son los mas usados. </p>" * 20
            + '<div class="g-recaptcha" data-sitekey="x"></div>')),
    }
    for i in range(n_muros):
        rutas[f"/muro/{i}"] = (codigo, cabeceras, desafio)
    return rutas


# (instante, host, ruta) de cada peticion que llega al sitio: para medir el
# ritmo al que el spider pide a cada host (Crawl-delay).
PETICIONES: list[tuple[float, str, str]] = []
# Cuantas veces se ha pedido cada ruta (para `_primeras`).
_VECES: dict[str, int] = {}


def _cabeceras_especiales(ruta: str, status: int, headers: dict, body: bytes):
    """Pseudo-cabeceras de las rutas de un test (decisiones 109 y 111), que el
    servidor interpreta y no envia:

    - ``_retardo_ms``: espera antes de responder (el TTFB que se mide);
    - ``_gzip``: sirve el cuerpo comprimido con ``Content-Encoding: gzip``;
    - ``_primeras``: solo las N primeras peticiones llevan este codigo y estas
      cabeceras; las siguientes, 200 con el mismo cuerpo;
    - ``Retry-After: +N`` se envia como fecha HTTP a N segundos.
    """
    import gzip as _gzip
    from email.utils import formatdate

    headers = dict(headers)
    retardo = float(headers.pop("_retardo_ms", 0) or 0)
    comprimir = headers.pop("_gzip", None)
    primeras = headers.pop("_primeras", None)
    _VECES[ruta] = _VECES.get(ruta, 0) + 1
    if primeras is not None and _VECES[ruta] > int(primeras):
        status, headers = 200, {}
    if str(headers.get("Retry-After", "")).startswith("+"):
        headers["Retry-After"] = formatdate(time.time() + float(headers["Retry-After"][1:]),
                                            usegmt=True)
    if retardo:
        time.sleep(retardo / 1000)
    if comprimir:
        body = _gzip.compress(body)
        headers["Content-Encoding"] = "gzip"
    return status, headers, body


def servir_tls_caducado() -> tuple[ThreadingHTTPServer, int]:
    """Un HTTPS en localhost con un certificado autofirmado CADUCADO
    (decision 110). Lo que tiene que hacer el spider: `tls_error` con el
    motivo, y la sonda TLS del host."""
    import datetime as _dt
    import ssl
    import tempfile

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    clave = ec.generate_private_key(ec.SECP256R1())
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    ahora = _dt.datetime.now(_dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
            .public_key(clave.public_key()).serial_number(1)
            .not_valid_before(ahora - _dt.timedelta(days=30))
            .not_valid_after(ahora - _dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), False)
            .sign(clave, hashes.SHA256()))
    d = tempfile.mkdtemp()
    with open(f"{d}/c.pem", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(f"{d}/k.pem", "wb") as f:
        f.write(clave.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            cuerpo = _html("<p>no deberia verse</p>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(f"{d}/c.pem", f"{d}/k.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


# (host, ruta, cabeceras) de cada peticion: para ver a que host llegan las
# cookies, el Basic y las cabeceras propias del job (decision 114).
CABECERAS: list[tuple[str, str, dict]] = []


def servir(robots_prohibe_todo: bool = False,
           robots_bloquea_privado: bool = False,
           con_recursos: bool = False,
           robots_crawl_delay: float | None = None,
           rutas_extra: dict | None = None,
           puerto: int = 0,
           muro: dict | None = None,
           lentas: dict | None = None) -> tuple[ThreadingHTTPServer, int]:
    rutas: dict = {}
    # {ruta: segundos} que el servidor tarda en contestar: para un timeout.
    lentas = lentas or {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            PETICIONES.append((time.monotonic(),
                               (self.headers.get("Host") or "").split(":")[0],
                               self.path))
            CABECERAS.append(((self.headers.get("Host") or "").split(":")[0],
                              self.path, {k.lower(): v for k, v in self.headers.items()}))
            if self.path in lentas:
                time.sleep(lentas[self.path])
            status, headers, body = rutas.get(self.path.split("?")[0], (404, {}, b"no"))
            status, headers, body = _cabeceras_especiales(
                self.path.split("?")[0], status, headers, body)
            self.send_response(status)
            headers = {"Content-Type": "text/html; charset=utf-8", **headers}
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    # Puerto fijo para reanudar el MISMO job en dos ejecuciones: con uno
    # aleatorio las URLs cambian y la reanudacion las veria como nuevas.
    srv = ThreadingHTTPServer(("127.0.0.1", puerto), Handler)
    port = srv.server_address[1]
    rutas.update(construir_sitio(port, con_privado=robots_bloquea_privado,
                                 con_recursos=con_recursos))
    # Rutas propias de un test: {ruta: [codigo, cabeceras, cuerpo]}. El cuerpo
    # y los valores de las cabeceras admiten {port}.
    for ruta, (codigo, cabeceras, cuerpo) in (rutas_extra or {}).items():
        cabeceras = {k: v.replace("{port}", str(port)) for k, v in cabeceras.items()}
        rutas[ruta] = (codigo, cabeceras, cuerpo.replace("{port}", str(port)).encode("utf-8"))
    if muro is not None:
        rutas.update(construir_sitio_con_muro(**muro))
    if robots_bloquea_privado:
        # Un robots.txt que prohibe una carpeta concreta: el caso del informe
        # "Blocked by robots.txt" (R6 de #25). La URL la enlaza /home.
        rutas["/robots.txt"] = (
            200, {"Content-Type": "text/plain"},
            b"User-agent: *\nDisallow: /privado/\n",
        )
    if robots_crawl_delay is not None:
        # Crawl-delay en el robots.txt de los dos hosts del sitio (decision 81).
        rutas["/robots.txt"] = (
            200, {"Content-Type": "text/plain"},
            f"User-agent: *\nCrawl-delay: {robots_crawl_delay}\n".encode(),
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
# Lo que el spider escribe en su fila de `jobs` (sondas): `update({...})`.
ACTUALIZACIONES_JOB: list[dict] = []


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
        if a and isinstance(a[0], dict):
            ACTUALIZACIONES_JOB.append(a[0])
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


def _armar_trampa_llm() -> dict:
    """Lo peor que puede encontrarse el rastreo: la capa activada y con
    clave. Si algo construye un cliente de OpenRouter o intenta salir hacia
    el, queda anotado (#7: nada se dispara solo; el disparo es #40)."""
    os.environ["DECISIONS_ENABLED"] = "1"
    os.environ["OPENROUTER_API_KEY"] = "sk-trampa"
    trampa = {"clientes": 0, "red": []}
    import shared.llm

    original = shared.llm.ClienteOpenRouter.__init__

    def _init(self, *a, **k):
        trampa["clientes"] += 1
        original(self, *a, **k)

    shared.llm.ClienteOpenRouter.__init__ = _init
    try:
        from fake_llm import vigilar_httpx

        trampa["red"] = vigilar_httpx(setattr)
    except ImportError:  # sin httpx no hay por donde salir
        pass
    return trampa


def main() -> None:
    entrada = json.loads(sys.argv[1])
    srv, port = servir(entrada.get("robots_prohibe_todo", False),
                       entrada.get("robots_bloquea_privado", False),
                       entrada.get("con_recursos", False),
                       entrada.get("robots_crawl_delay"),
                       entrada.get("rutas"),
                       entrada.get("puerto", 0),
                       entrada.get("muro"),
                       entrada.get("lentas"))

    tls_port = None
    if entrada.get("tls_caducado"):
        _srv_tls, tls_port = servir_tls_caducado()

    trampa = None
    if entrada.get("trampa_llm"):
        trampa = _armar_trampa_llm()

    import shared.database
    from scrapy.crawler import CrawlerProcess

    job = types.SimpleNamespace(
        seeds=[s.replace("{port}", str(port)).replace("{tls_port}", str(tls_port))
               for s in entrada["seeds"]],
        config=entrada.get("config", {}),
    )
    pipelines = {"spider_harness.Recolector": 1}
    job_id = entrada.get("job_id", "test")
    if entrada.get("bd"):
        # BD de verdad (Postgres de pruebas): el job ya existe y se usa la
        # tuberia real, para comprobar lo GUARDADO y no solo lo emitido.
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from shared.models import Base, Job

        engine = create_engine(entrada["bd"])
        Base.metadata.create_all(engine)
        Sesion = sessionmaker(bind=engine)
        with Sesion() as s:
            fila = s.get(Job, __import__("uuid").UUID(job_id))
            fila.seeds = job.seeds
            fila.config = job.config
            s.commit()
        shared.database.SessionLocal = Sesion
        pipelines["seo_crawler.pipelines.PostgresPipeline"] = 300
    else:
        shared.database.SessionLocal = lambda: _SesionFalsa(job)

    from seo_crawler.spiders.seo_spider import SeoSpider

    redis_falso = None
    if entrada.get("redis_falso"):
        # Redis en memoria que anota lo que el spider ESCRIBE (`set`), para
        # comprobar las marcas que luego lee el worker. Lo demas no hace nada.
        import seo_crawler.spiders.seo_spider as modulo_spider

        redis_falso = dict(entrada.get("redis_inicial") or {})

        class _RedisFalso:
            def set(self, clave, valor, *a, **k):
                redis_falso[clave] = valor
                return True

            def get(self, clave):
                return redis_falso.get(clave)

            def delete(self, *claves):
                for c in claves:
                    redis_falso.pop(c, None)

            def __getattr__(self, nombre):
                return lambda *a, **k: None

        modulo_spider.redis.Redis.from_url = classmethod(lambda cls, *a, **k: _RedisFalso())

    if entrada.get("simular_navegador"):
        # Como con render JS: la redireccion no la sigue el spider sino quien
        # descarga (aqui el RedirectMiddleware de Scrapy, que rellena
        # redirect_urls igual que scrapy-playwright), y la peticion lleva
        # meta["playwright"].
        original = SeoSpider._page_request

        def _page_request(self, url, depth, *a, **k):
            req = original(self, url, depth, *a, **k)
            req.meta.pop("dont_redirect", None)
            req.meta["playwright"] = True
            return req

        SeoSpider._page_request = _page_request

    middlewares = {"seo_crawler.middlewares.RobotsAuditMiddleware": 100}
    import seo_crawler.middlewares as mw
    if hasattr(mw, "RobotsCrawlDelayMiddleware"):
        # Condicional para que el test del Crawl-delay falle contra el codigo
        # anterior por lo que mide (el ritmo), no por un ImportError.
        middlewares["seo_crawler.middlewares.RobotsCrawlDelayMiddleware"] = 110
    if hasattr(mw, "RobotsTxtRegistroMiddleware"):
        # Como en settings.py (decision 112). Condicional por lo mismo.
        middlewares["seo_crawler.middlewares.RobotsTxtRegistroMiddleware"] = 90
    con_crawl_delay = entrada.get("robots_crawl_delay") is not None
    if entrada.get("retry_after") and hasattr(mw, "RetryAfterMiddleware"):
        middlewares["seo_crawler.middlewares.RetryAfterMiddleware"] = 560
    extra_settings = {}
    if entrada.get("handler_real"):
        # El handler de produccion (curl_cffi con huella de Chrome y las
        # medidas de la decision 109), no el de Twisted por defecto.
        extra_settings = {
            "DOWNLOAD_HANDLERS": {
                "http": "seo_crawler.handlers.CompositeDownloadHandler",
                "https": "seo_crawler.handlers.CompositeDownloadHandler",
            },
            "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
            "IMPERSONATE": "chrome124",
        }
    extra_settings.update(entrada.get("settings") or {})

    ajustes_reales: dict = {}
    if entrada.get("http_real"):
        # Los middlewares del bloque `http` del job, como en settings.py, y el
        # jar como lo deja el worker (decision 114).
        middlewares["seo_crawler.middlewares.HttpConfigMiddleware"] = 420
        middlewares["seo_crawler.middlewares.CookiesTrasElJarMiddleware"] = 705
        ajustes_reales["COOKIES_ENABLED"] = bool(
            ((entrada.get("config") or {}).get("http") or {}).get("conservar_cookies"))
    if entrada.get("descarga_real"):
        # La descarga de produccion: curl_cffi y, con render_js, Playwright con
        # los contextos y la preparacion de pagina de settings.py.
        import seo_crawler.settings as prod

        lanzar = dict(prod.PLAYWRIGHT_LAUNCH_OPTIONS)
        if entrada.get("chromium"):
            lanzar["executable_path"] = entrada["chromium"]
        ajustes_reales.update({
            "DOWNLOAD_HANDLERS": prod.DOWNLOAD_HANDLERS,
            "IMPERSONATE": prod.IMPERSONATE,
            "TWISTED_REACTOR": prod.TWISTED_REACTOR,
            "PLAYWRIGHT_BROWSER_TYPE": prod.PLAYWRIGHT_BROWSER_TYPE,
            "PLAYWRIGHT_LAUNCH_OPTIONS": lanzar,
            "PLAYWRIGHT_CONTEXTS": prod.PLAYWRIGHT_CONTEXTS,
            "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": prod.PLAYWRIGHT_MAX_PAGES_PER_CONTEXT,
            "PLAYWRIGHT_MAX_CONTEXTS": prod.PLAYWRIGHT_MAX_CONTEXTS,
            "PLAYWRIGHT_ABORT_REQUEST": prod.PLAYWRIGHT_ABORT_REQUEST,
            "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": prod.PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT,
        })

    proceso = CrawlerProcess({
        "LOG_LEVEL": entrada.get("log_level", "WARNING"),
        "TELNETCONSOLE_ENABLED": False,
        # Por defecto apagado para que el sitio de pruebas se rastree entero;
        # el escenario de `Disallow: /` lo enciende (ver robots_prohibe_todo).
        "ROBOTSTXT_OBEY": bool(
            entrada.get("robots_prohibe_todo") or entrada.get("robots_bloquea_privado")
            or con_crawl_delay or entrada.get("robots_obey")
        ) and entrada.get("robots_mode") not in ("audit", "ignore"),
        "ROBOTS_MODE": entrada.get("robots_mode", "respect"),
        "DOWNLOADER_MIDDLEWARES": middlewares,
        # Como en produccion (settings.py): AUTOTHROTTLE encendido, que es lo
        # que bajaba el retraso del slot tras cada respuesta.
        "AUTOTHROTTLE_ENABLED": con_crawl_delay,
        "AUTOTHROTTLE_START_DELAY": 0,
        "AUTOTHROTTLE_MAX_DELAY": 10,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 8.0,
        "DOWNLOAD_DELAY": 0,
        "HTTPERROR_ALLOW_ALL": True,
        "DEPTH_PRIORITY": 1,
        "REQUEST_FINGERPRINTER_IMPLEMENTATION": "2.7",
        "SPIDER_MIDDLEWARES": {
            "scrapy.spidermiddlewares.depth.DepthMiddleware": None,
            "seo_crawler.middlewares.DepthMiddleware": 900,
        },
        "ITEM_PIPELINES": pipelines,
        **ajustes_reales,
        **extra_settings,
    })
    cierre: dict = {}
    original_closed = SeoSpider.spider_closed

    def _spider_closed(self, spider, reason):
        cierre["reason"] = reason
        # Lo que anoto el registro de robots.txt (decision 112).
        try:
            for m in self.crawler.engine.downloader.middleware.middlewares:
                if type(m).__name__ == "RobotsTxtRegistroMiddleware":
                    cierre["robots_txt"] = list(m.filas.values())
        except Exception:  # noqa: BLE001
            pass
        return original_closed(self, spider, reason)

    SeoSpider.spider_closed = _spider_closed
    proceso.crawl(SeoSpider, job_id=job_id)
    proceso.start()
    srv.shutdown()

    base = f"127.0.0.1:{port}"
    salida = []
    import spider_harness
    for it in spider_harness.ITEMS:
        fila = {k: v for k, v in it.items() if k in (
            "_tipo", "url", "status_code", "redirect_url", "crawl_depth",
            "indexability_status", "is_internal", "to_url", "follow", "resource_type",
            "blocked_by_robots", "status_group", "response_class", "url_hash",
            "from_url_hash", "filas", "lang", "href", "origen", "en_body",
            "ttfb_ms", "content_encoding", "http_version", "status_text",
        )}
        for k in ("url", "redirect_url", "to_url"):
            if fila.get(k):
                fila[k] = fila[k].replace(base, "OTRO").replace(f"localhost:{port}", "SEMILLA")
        salida.append(fila)
    if entrada.get("ver_cabeceras"):
        print("@@CABECERAS@@" + json.dumps(CABECERAS))
    print("@@CIERRE@@" + json.dumps(cierre, default=str))
    if trampa is not None:
        print("@@LLM@@" + json.dumps(trampa))
    if redis_falso is not None:
        print("@@REDIS@@" + json.dumps(redis_falso, default=str))
    if entrada.get("ver_job"):
        # Solo si se pide: los tests que parten la salida entre marcas no
        # esperan esta linea.
        print("@@JOB@@" + json.dumps(ACTUALIZACIONES_JOB, default=str))
    print("@@PETICIONES@@" + json.dumps(PETICIONES))
    print("@@ITEMS@@" + json.dumps(salida))


if __name__ == "__main__":
    main()
