"""
Main SEO spider.

Usage::

    scrapy crawl seo -a job_id=<uuid>

The spider loads its seed URLs and configuration from the ``jobs`` table,
then performs a BFS crawl extracting all SEO-relevant data.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Generator
from urllib.parse import urljoin, urlparse

import redis
import scrapy
from sqlalchemy import text
from scrapy import Request, signals
from scrapy.http import HtmlResponse, Response
from scrapy_playwright.page import PageMethod

from seo_crawler.extractors import (
    absolutize_url,
    classify_resource_type,
    compile_url_patterns,
    compute_folder_depth,
    compute_status_group,
    compute_text_ratio,
    contar_palabras,
    hash_de_contenido,
    compute_url_hash,
    detect_mixed_content,
    effective_base_url,
    estimate_description_pixel_width,
    estimate_title_pixel_width,
    extract_headings,
    extract_hreflang,
    extract_links,
    extract_main_content,
    extract_main_content_markdown,
    extract_meta,
    extract_meta_refresh,
    extract_meta_refresh_target,
    extract_resources,
    extract_security_headers,
    extract_structured_data,
    extract_visible_text,
    extract_word_count,
    http_status_text,
    is_internal_url,
    dominio_registrable,
    normalize_host,
    normalize_url,
    robots_tokens,
)
from seo_crawler.sitemaps import (
    MAX_SITEMAP_FILES,
    parse_robots_sitemaps,
    parse_sitemap,
)
from shared.indexabilidad import estado_indexabilidad
from seo_crawler.items import (
    ContentItem,
    HeadingItem,
    HreflangItem,
    HtmlMetaItem,
    LinkItem,
    PageItem,
    ResourceItem,
    SecurityItem,
    StructuredDataItem,
)

logger = logging.getLogger(__name__)
# trafilatura avisa con "empty link" por cada <a> sin texto (iconos, tarjetas
# enteras enlazadas): decenas por pagina en cualquier sitio moderno, y tapa
# los avisos que si importan (Playwright, spider). No es un error de nada.
logging.getLogger("trafilatura").setLevel(logging.ERROR)

# Extensions that never need JS rendering — skip Playwright for these URLs.
_NON_HTML_EXTENSIONS = frozenset({
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff", ".avif",
    # Documents
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # Styles / scripts / fonts
    ".css", ".js", ".mjs", ".woff", ".woff2", ".ttf", ".eot", ".otf",
    # Media
    ".mp3", ".mp4", ".avi", ".mov", ".webm", ".ogg", ".wav",
    # Archives
    ".zip", ".tar", ".gz", ".rar", ".7z",
    # Data / config
    ".json", ".xml", ".rss", ".yaml", ".yml", ".map", ".wasm",
    # Other binary
    ".exe", ".dmg",
    # Plain text
    ".txt", ".csv", ".rtf",
})


def _url_likely_html(url: str) -> bool:
    """Return True if the URL probably points to an HTML page.

    Checks the last segment of the path for a file extension.
    No extension or an extension not in _NON_HTML_EXTENSIONS → probably HTML.
    """
    path = urlparse(url).path
    # Get last segment, ignore trailing slash
    segment = path.rstrip("/").rsplit("/", 1)[-1] if path else ""
    dot_pos = segment.rfind(".")
    if dot_pos == -1:
        return True  # no extension → likely HTML
    ext = segment[dot_pos:].lower()
    return ext not in _NON_HTML_EXTENSIONS


def _hash_key(url_hash: str) -> int:
    """Compacta un sha256 hex a un int de 64 bits para sets en memoria.

    Reduce ~4x la memoria del set de resume. Riesgo de colision para 1M
    de URLs: ~3e-8 (una colision = saltar una URL, aceptable).
    """
    return int(url_hash[:16], 16)


def _safe_normalize(url: str) -> str:
    """normalize_url que no lanza: una URL malformada se compara tal cual."""
    try:
        return normalize_url(url)
    except ValueError:
        return url


# ---------------------------------------------------------------------------
# Boilerplate DOM cleanup — runs inside Chromium via PageMethod("evaluate")
# after page load, BEFORE Scrapy captures the HTML.  Removes cookie banners,
# consent overlays, chat widgets, and ARIA modals so extractors only see
# real page content.
# ---------------------------------------------------------------------------
# Antes de leer el HTML hay que dar tiempo a que el JS de la pagina termine:
# los gestores de consentimiento inyectan su banner (para que el limpiador de
# abajo pueda quitarlo) y muchas plantillas montan en cliente listados y
# enlaces. Esto era una espera FIJA de 2 s por pagina, y era la mayor parte
# del tiempo de render: en el censo del 2026-09-10 la mediana de una pagina
# fue 2672 ms, de los que 2000 eran esta espera — 44 de los 62 minutos de
# render, esperando a paginas que ya habian terminado.
#
# Ahora se espera a que el DOM lleve QUIETO_MS sin mutar, con tope en TOPE_MS.
# Medido sobre 8 plantillas de 5 sitios (Astro, Next.js, Liferay, Symfony):
# mismo texto y mismos enlaces en todas, 30% menos de tiempo.
#
# Va como `evaluate` y no como `wait_for_load_state("networkidle")` a
# proposito: un PageMethod no puede capturar excepciones, asi que un sitio
# cuya red no calla nunca (polling, chat, websockets) tumbaria la pagina por
# timeout. Esta promesa SIEMPRE resuelve, y el tope la acota.
_ESPERA_TOPE_MS = int(os.getenv("PLAYWRIGHT_BANNER_WAIT_MS", "2000"))


async def _evaluar_tolerante(page, js: str, intentos: int = 3):
    """``page.evaluate`` que sobrevive a una navegacion en curso.

    Un PageMethod("evaluate", ...) a secas revienta con "Execution context was
    destroyed, most likely because of a navigation" cuando la pagina se redirige
    por JavaScript justo despues de `domcontentloaded` (portales antiguos, paginas
    "index.html" que saltan a la seccion, selectores de idioma). scrapy-playwright
    cierra entonces la pestaña por error, y tras unos cuantos cierres asi el
    navegador deja de servir paginas: el rastreo sigue "vivo" a 0 paginas/min
    hasta que el vigilante lo mata. Medido: 19 fallos de este tipo y cuelgue total
    en 40 minutos.

    Aqui se espera a que el nuevo documento cargue y se reintenta; si no hay
    manera, se devuelve None y la pagina se entrega tal cual, que siempre es mejor
    que perderla. Va como callable porque PageMethod acepta uno (recibe la page).
    """
    from playwright.async_api import Error as PlaywrightError

    for intento in range(intentos):
        try:
            return await page.evaluate(js)
        except PlaywrightError as exc:
            texto = str(exc)
            if "Execution context was destroyed" not in texto and "navigation" not in texto:
                raise
            if intento == intentos - 1:
                logger.debug("evaluate abandonado tras %d navegaciones: %s", intentos, page.url)
                return None
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except PlaywrightError:
                pass
    return None
_ESPERA_QUIETO_MS = int(os.getenv("PLAYWRIGHT_DOM_QUIET_MS", "400"))

# Piso: por debajo de esto no se da por terminada una pagina aunque el DOM
# lleve quieto desde el primer instante.
#
# Mirar SOLO el DOM tiene un punto ciego que costaba paginas enteras: mientras
# una peticion XHR esta EN VUELO no hay mutaciones, asi que el DOM parece
# "quieto" y la espera resolvia antes de que la respuesta llegase a pintar
# nada. Medido en un rastreo real de 9.895 noticias: 6.555 se guardaron con
# 5-6 bloques de datos estructurados y solo 1.965 con los 7 que tiene la
# pagina montada — faltaba el modulo de relacionadas, y con el ~20 enlaces y
# ~20% del texto. La misma pagina, en frio, se estabiliza a los 500-1.000 ms:
# no era el sitio, era la carrera.
#
# Ahora el temporizador lo reinicia tambien cada recurso que TERMINA
# (PerformanceObserver), que es la senal de que la respuesta de ese XHR acaba
# de llegar y el DOM esta a punto de moverse. Es la semantica de "networkidle"
# sin su riesgo: sigue siendo una promesa que resuelve siempre, acotada por el
# tope, asi que un sitio cuya red no calla nunca no tumba la pagina.
_ESPERA_PISO_MS = int(os.getenv("PLAYWRIGHT_MIN_WAIT_MS", "600"))

_JS_ESPERAR_DOM_QUIETO = """
() => new Promise((resolve) => {
    const TOPE = %d, QUIETO = %d, PISO = %d;
    const t0 = Date.now();
    let t = null, obs = null, po = null, forzado = false;
    const fin = () => {
        clearTimeout(t); clearTimeout(tope);
        try { if (obs) obs.disconnect(); } catch (_) {}
        try { if (po) po.disconnect(); } catch (_) {}
        resolve();
    };
    // Con una peticion en vuelo la pagina NO esta terminada, por mucho que el
    // DOM lleve quieto: la respuesta aun tiene que llegar y pintar. El
    // contador lo instala seo_crawler/render.py antes de navegar; si no
    // estuviera, esto vale 0 y el comportamiento es el de solo-DOM.
    const enVuelo = () => (window.__enVuelo | 0) > 0;
    const termina = () => {
        if (!forzado && enVuelo()) { reinicia(); return; }
        fin();
    };
    const reinicia = () => {
        clearTimeout(t);
        // Nunca antes del piso, aunque no haya pasado nada todavia.
        const espera = Math.max(QUIETO, PISO - (Date.now() - t0));
        t = setTimeout(termina, espera);
    };
    const tope = setTimeout(() => { forzado = true; fin(); }, TOPE);
    try {
        obs = new MutationObserver(reinicia);
        obs.observe(document, { childList: true, subtree: true });
    } catch (_) { obs = null; }
    try {
        po = new PerformanceObserver(reinicia);
        po.observe({ type: "resource", buffered: false });
    } catch (_) { po = null; }
    if (!obs && !po) { fin(); return; }
    reinicia();
})
""" % (_ESPERA_TOPE_MS, _ESPERA_QUIETO_MS, _ESPERA_PISO_MS)

# Version HTTP real de la navegacion. Chromium no la expone en el objeto
# respuesta, pero si en Navigation Timing: "h2", "h3", "http/1.1". Por el
# camino de curl_cffi no hay forma de sacarla sin reimplementar el handler de
# scrapy-impersonate, que la descarta; por eso la columna solo se rellena en
# rastreos con render (antes estaba vacia SIEMPRE, en los dos modos).
_JS_PROTOCOLO = (
    "() => { try { const n = performance.getEntriesByType('navigation')[0];"
    " return n ? n.nextHopProtocol : null; } catch (e) { return null; } }"
)

_PROTOCOLO_LEGIBLE = {
    "h2": "HTTP/2",
    "h3": "HTTP/3",
    "http/1.1": "HTTP/1.1",
    "http/1.0": "HTTP/1.0",
}


def _normaliza_protocolo(valor) -> str | None:
    if not valor or not isinstance(valor, str):
        return None
    return _PROTOCOLO_LEGIBLE.get(valor.lower().strip(), valor.strip())


# Esta limpieza se ejecuta en el NAVEGADOR y antes de capturar el HTML, asi que
# un falso positivo aqui no se lleva solo el texto: se lleva los enlaces de esa
# zona, y el rastreo no vuelve a verlos. Los patrones casan por subcadena, de
# modo que `cookie-policy` o `privacy-notice` casan con el contenedor del
# contenido de la propia pagina de cookies o de privacidad —las que tienen que
# estar indexadas tal cual—. Las dos mismas salvaguardas que en Python
# (`_TAGS_INTOCABLES` y `_MAX_SHARE_PLANTILLA` de extractors.py): no se toca la
# pagina entera, y un bloque que se lleva mas del 40% de las palabras no es un
# aviso de cookies, es la pagina.
_BOILERPLATE_REMOVAL_JS = """
() => {
    const INTOCABLES = new Set(['HTML', 'BODY', 'MAIN', 'ARTICLE']);
    const MAX_SHARE = 0.4;
    // Palabras visibles, sin el texto de script/style/noscript/template.
    const palabras = (el) => {
        if (!el) return 0;
        let n = 0;
        try {
            const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
                acceptNode: (t) => {
                    const p = t.parentElement;
                    if (!p) return NodeFilter.FILTER_REJECT;
                    const tg = p.tagName;
                    return (tg === 'SCRIPT' || tg === 'STYLE' || tg === 'NOSCRIPT'
                            || tg === 'TEMPLATE')
                        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
                }
            });
            while (w.nextNode()) {
                const m = (w.currentNode.nodeValue || '').match(/\\S+/g);
                if (m) n += m.length;
            }
        } catch (_) {}
        return n;
    };
    const total = palabras(document.body);
    const r = (s) => {
        try {
            document.querySelectorAll(s).forEach(e => {
                if (INTOCABLES.has(e.tagName)) return;
                if (total > 0 && palabras(e) / total > MAX_SHARE) return;
                e.remove();
            });
        } catch(_) {}
    };

    // ---- Known consent-management libraries ----
    ['#CybotCookiebotDialog', '#CybotCookiebotDialogBodyUnderlay',
     '#onetrust-banner-sdk', '#onetrust-consent-sdk',
     '.osano-cm-window', '.cc-window', '.cc-banner', '.cc-revoke',
     '#tarteaucitronRoot', '#usercentrics-root', '#sp-consent-message',
     '#ez-cookie-dialog', '#catapult-cookie-bar', '#moove_gdpr_cookie_info_bar'
    ].forEach(r);

    // ---- Pattern-based: id/class contains these substrings ----
    ['cookie-consent', 'cookie-banner', 'cookie-notice', 'cookie-bar',
     'cookie-popup', 'cookie-modal', 'cookie-wall', 'cookie-law',
     'cookie-policy', 'cookie-message', 'cookie-alert', 'cookie-overlay',
     'cookieconsent', 'cookiebanner', 'cookienotice', 'cookiebar',
     'cookies-eu', 'cookies-modal', 'cookies-overlay',
     'gdpr-banner', 'gdpr-notice', 'gdpr-popup', 'gdpr-overlay', 'gdpr-consent',
     'consent-banner', 'consent-modal', 'consent-popup', 'consent-overlay',
     'privacy-banner', 'privacy-notice', 'privacy-popup'
    ].forEach(p => {
        r('[id*="' + p + '" i]');
        r('[class*="' + p + '" i]');
    });

    // ---- Chat widgets ----
    ['#hubspot-messages-iframe-container',
     '#intercom-container', '#intercom-frame',
     '.crisp-client', '#crisp-chatbox',
     '#drift-widget-container', '#drift-frame-chat',
     '#tawk-bubble-container',
     '[class*="chat-widget" i]', '[id*="chat-widget" i]',
     '[class*="livechat" i]', '[id*="livechat" i]'
    ].forEach(r);

    // NOTE: structural elements (form, nav, aside, footer, header) are
    // intentionally NOT removed here. They contain real internal links that
    // must reach `extract_links` so the BFS can follow them. Boilerplate
    // stripping for content extraction happens later in Python via
    // `_strip_boilerplate_html` (extractors.py), which operates on a copy of
    // the HTML and does not affect link discovery.

    // ---- ARIA modals / HTML5 dialogs ----
    r('[aria-modal="true"]');
    r('dialog[open]');
}
"""


class SeoSpider(scrapy.Spider):
    """Broad-crawl SEO spider driven by a job definition in PostgreSQL."""

    name = "seo"

    def __init__(self, job_id: str | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not job_id:
            raise ValueError("spider argument 'job_id' is required")
        self.job_id = job_id
        self.job_config: dict[str, Any] = {}
        self.seed_urls: list[str] = []
        self.allowed_hosts: set[str] = set()
        self.max_depth: int = 3
        # None = sin tope (hasta agotar la frontera)
        self.max_urls: int | None = None
        self.follow_external: bool = False
        # Comprobadores ya compilados (ver compile_url_patterns)
        self._exclude_patterns: list = []
        self._include_patterns: list = []
        # Semillas normalizadas: para reconocer la que redirige a otro host
        self._seed_keys: set[str] = set()
        # Hosts cuyo robots.txt ya se pidio para descubrir sitemaps
        self._robots_hosts: set[str] = set()
        self._crawled_count: int = 0
        self._redis: redis.Redis | None = None
        self._redis_update_interval: int = 50
        # Resume support: claves compactas (64-bit) de URLs ya rastreadas en
        # una ejecucion anterior de este job. La frontera se streamea desde
        # la BD en start_requests (ver _iter_frontier), no se carga entera.
        self._already_crawled_hashes: set[int] = set()
        self._resume_mode: bool = False
        # Sitemap ingestion state. OJO: _sitemap_url_hashes guarda el sha256
        # completo (no la clave compacta) porque se escribe tal cual en la BD
        # al persistir Url.in_sitemap.
        self._use_sitemap: bool = True
        self._sitemap_url_hashes: set[str] = set()
        self._sitemap_files_fetched: int = 0
        # Ficheros de sitemap pedidos. Si al cerrar quedan por debajo de los
        # parseados, la vista es parcial y no se puede afirmar que el resto de
        # URLs este fuera del sitemap.
        self._sitemap_requested: int = 0
        self._sitemaps_pedidos: set[str] = set()
        # True si se alcanzo MAX_SITEMAP_FILES: la vista del sitemap es
        # parcial y no se puede afirmar que nada mas este fuera de el.
        self._sitemap_truncated: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(spider.spider_closed, signal=signals.spider_closed)
        return spider

    def spider_opened(self, spider):
        """Load job config from PostgreSQL and connect to Redis."""
        from shared.database import SessionLocal
        from shared.models import Job

        session = SessionLocal()
        try:
            job = session.query(Job).filter(Job.id == self.job_id).one_or_none()
            if job is None:
                raise RuntimeError(f"Job {self.job_id} not found in database")

            self.seed_urls = job.seeds or []
            self.job_config = job.config or {}

            self.max_depth = self.job_config.get("max_depth", 3)
            self.max_urls = self.job_config.get("max_urls")
            self.follow_external = self.job_config.get("follow_external", False)
            self._exclude_patterns, malos_ex = compile_url_patterns(
                self.job_config.get("exclude_patterns", [])
            )
            self._include_patterns, malos_in = compile_url_patterns(
                self.job_config.get("include_patterns", [])
            )
            for patron in malos_ex + malos_in:
                logger.warning(
                    "Patron de URL que no es una regex valida: %r. Sin prefijo "
                    "se aplica solo como glob; con 're:' se descarta. Declara "
                    "el tipo con 'glob:' o 're:'",
                    patron,
                )
            self.render_js = self.job_config.get("render_js", False)

            # Build allowed-hosts from seed URLs
            self._seed_keys = set()
            for seed in self.seed_urls:
                self._add_allowed_host(urlparse(seed).hostname)
                try:
                    self._seed_keys.add(normalize_url(seed))
                except ValueError:
                    pass

            # User-agent override for middlewares
            if self.job_config.get("user_agent"):
                self.custom_user_agent = self.job_config["user_agent"]

            # -- Advanced config: Resource types --
            rt = self.job_config.get("resource_types", {})
            self._allowed_resource_types = {"html", "redirect"}
            if rt.get("crawl_images", True):
                self._allowed_resource_types.add("image")
            if rt.get("crawl_css", True):
                self._allowed_resource_types.add("css")
            if rt.get("crawl_js", True):
                self._allowed_resource_types.add("js")
            if rt.get("crawl_pdfs", True):
                self._allowed_resource_types.add("pdf")
            if rt.get("crawl_fonts", False):
                self._allowed_resource_types.add("font")
            if rt.get("crawl_svg", True):
                self._allowed_resource_types.add("svg")
            if rt.get("crawl_other", True):
                self._allowed_resource_types.add("other")

            # -- Advanced config: Crawl behavior --
            cb = self.job_config.get("crawl_behavior", {})
            self._follow_nofollow = cb.get("follow_nofollow", False)
            self._crawl_subdomains = cb.get("crawl_subdomains", False)

            # -- Advanced config: URL filters --
            uf = self.job_config.get("url_filters", {})
            self._max_url_length = uf.get("max_url_length", 0)
            self._max_folder_depth = uf.get("max_folder_depth", 0)

            # -- Advanced config: Extraction toggles --
            self._extraction = self.job_config.get("extraction", {})

            # -- Sitemap ingestion (Screaming Frog parity) --
            self._use_sitemap = self.job_config.get("use_sitemap", True)
            self._extra_sitemap_urls = self.job_config.get("sitemap_urls", []) or []

            # -- Advanced config: HTTP config (for middleware) --
            self._http_config = self.job_config.get("http", {})

            # -- Subdomain crawling: expand allowed hosts --
            if self._crawl_subdomains:
                # Por la Public Suffix List, no por las dos ultimas etiquetas:
                # `www.competidor.co.uk` daba raiz `co.uk` y cualquier sitio
                # `.co.uk` entraba en el rastreo y en el informe del cliente.
                self._root_domains: set[str] = {
                    dominio_registrable(host) for host in self.allowed_hosts
                } - {""}

            logger.info(
                "Job %s loaded: %d seeds, max_depth=%d, max_urls=%s, hosts=%s",
                self.job_id,
                len(self.seed_urls),
                self.max_depth,
                self.max_urls,
                self.allowed_hosts,
            )

            # -- Resume detection: load already-crawled URL keys
            # If this job already has rows in `urls`, treat the run as a
            # resume: skip URLs we already fetched. The frontier (discovered-
            # but-not-crawled links) se streamea desde la BD en
            # start_requests via _iter_frontier(), sin cap ni carga completa.
            from shared.models import Url

            # Paginas que se dan por NO rastreadas al reanudar, para que se
            # repitan: perdidas (status NULL: timeout, error de red), 5xx
            # (casi siempre transitorios: 183 de 183 respondian 200 al
            # volver a pedirlas) y las que Chromium dejo en su pagina de error.
            # Antes contaban como hechas y un resume no las tocaba, asi que el
            # unico modo de completarlas era otro rastreo entero.
            self._reintentar: list[tuple[str, int]] = []
            if self.job_config.get("crawl_behavior", {}).get("retry_failed_on_resume", True):
                from sqlalchemy import or_

                self._reintentar = [
                    (row[0], row[1] or 1)
                    for row in session.query(Url.url, Url.crawl_depth)
                    .filter(
                        Url.job_id == self.job_id,
                        or_(
                            Url.status_code.is_(None),
                            Url.status_code >= 500,
                            Url.redirect_url.like("chrome-error://%"),
                        ),
                    )
                    .yield_per(10_000)
                ]
            # Y las que casen con crawl_behavior.recrawl_patterns (regex sobre
            # la URL): para repetir solo una plantilla (p. ej. los listados tras
            # subir la espera de render) sin volver a rastrear el sitio entero.
            patrones = (self.job_config.get("crawl_behavior") or {}).get("recrawl_patterns") or []
            if patrones:
                ya = {u for u, _ in self._reintentar}
                for row in (
                    session.query(Url.url, Url.crawl_depth)
                    .filter(Url.job_id == self.job_id, Url.is_internal.is_(True))
                    .yield_per(10_000)
                ):
                    if row[0] not in ya and any(re.search(pt, row[0]) for pt in patrones):
                        self._reintentar.append((row[0], row[1] or 1))
            # Y, si se pide, las HTML 200 internas que quedaron sin contenido
            # extraido: tras corregir el stripper o los selectores del cliente,
            # es la forma de completar esas paginas sin rastrear el sitio entero.
            if (self.job_config.get("crawl_behavior") or {}).get("recrawl_empty_content"):
                from sqlalchemy import func
                from shared.models import PageContent

                ya = {u for u, _ in self._reintentar}
                filas = (
                    session.query(Url.url, Url.crawl_depth)
                    .outerjoin(PageContent, PageContent.url_id == Url.id)
                    .filter(
                        Url.job_id == self.job_id, Url.is_internal.is_(True),
                        Url.is_html.is_(True), Url.status_code == 200,
                        (PageContent.url_id.is_(None)) | (func.length(PageContent.content_text) < 50),
                    )
                    .yield_per(10_000)
                )
                for row in filas:
                    if row[0] not in ya:
                        self._reintentar.append((row[0], row[1] or 1))
            reintentar_hashes = {
                _hash_key(compute_url_hash(normalize_url(u))) for u, _ in self._reintentar
            }
            self._already_crawled_hashes = {
                _hash_key(row[0])
                for row in session.query(Url.url_hash)
                .filter(Url.job_id == self.job_id)
                .yield_per(10_000)
            } - reintentar_hashes
            if self._already_crawled_hashes:
                self._resume_mode = True
                self._crawled_count = (
                    session.query(Url)
                    .filter(Url.job_id == self.job_id)
                    .count()
                )
                logger.info(
                    "Resume mode for job %s: %d URLs already crawled, "
                    "frontier will be streamed from DB",
                    self.job_id,
                    self._crawled_count,
                )
        finally:
            session.close()

        # Redis connection for progress updates and cancel checks
        redis_url = self.settings.get("REDIS_URL", "redis://localhost:6379/0")
        try:
            self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
            self._redis.ping()
            logger.info("Redis connected for job progress tracking")
            # Initial heartbeat so a freshly-started job (before it reaches the
            # first progress-update interval) is not seen as stale.
            self._write_heartbeat()
        except Exception as exc:
            logger.warning("Redis unavailable; progress tracking disabled: %s", exc)
            self._redis = None

    def spider_closed(self, spider, reason):
        """Persist sitemap membership, push final count, close Redis."""
        self._persist_sitemap_membership()
        if self._redis:
            try:
                self._redis.set(
                    f"job:{self.job_id}:crawled_count", self._crawled_count
                )
                # La cola queda a 0 al cerrar: si el rastreo se corto por el
                # tope o por cancelacion habria quedado el ultimo valor vivo, y
                # un job terminado no tiene nada en cola. Lo que quedase sin
                # rastrear se deduce de finish_reason, no de este contador.
                self._redis.set(f"job:{self.job_id}:pending_count", 0)
            except Exception:
                pass
            try:
                self._redis.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Seed requests
    # ------------------------------------------------------------------
    def _playwright_meta(self) -> dict[str, Any]:
        """Build playwright request meta when JS rendering is enabled.

        Uses the pre-configured "custom" context from PLAYWRIGHT_CONTEXTS
        (settings.py) so the browser context is reused across requests
        instead of creating a new one each time.

        After page load, runs ``_BOILERPLATE_REMOVAL_JS`` to strip cookie
        banners, consent overlays, chat widgets, and ARIA modals from the
        DOM before Scrapy captures the HTML.
        """
        return {
            "playwright": True,
            "playwright_include_page": False,
            "playwright_context": "custom",
            "playwright_page_goto_kwargs": {
                "wait_until": "domcontentloaded",
            },
            "playwright_page_methods": [
                # Esperar a que la pagina termine de montarse (ver arriba) y
                # solo entonces quitar banners: al reves, el limpiador correria
                # antes de que el banner exista.
                PageMethod(_evaluar_tolerante, _JS_ESPERAR_DOM_QUIETO),
                PageMethod(_evaluar_tolerante, _BOILERPLATE_REMOVAL_JS),
                # Ultimo: su resultado se lee luego desde response.meta.
                PageMethod(_evaluar_tolerante, _JS_PROTOCOLO),
            ],
        }

    def _iter_frontier(self) -> Generator[str, None, None]:
        """Streamea la frontera (links descubiertos pero no rastreados).

        Paginacion keyset sobre links.id en lotes, con sesion corta por
        lote: memoria acotada y sin cap duro de frontera. Dedup en memoria
        con claves compactas de 64 bits.
        """
        from shared.database import SessionLocal
        from shared.models import Link, Url

        batch_size = 2_000
        last_hash = ""

        while True:
            session = SessionLocal()
            try:
                # Paginacion keyset sobre to_url_hash, no sobre links.id, y con
                # DISTINCT ON para que la BD devuelva ya un destino por fila.
                #
                # Paginar por links.id obligaba a recorrer TODAS las filas de
                # enlaces y deduplicar en Python: medido en un rastreo real,
                # 740.190 enlaces internos para solo 6.086 destinos distintos —
                # 148 lotes de consulta para sembrar 6.000 URLs, con el rastreo
                # parado mientras tanto. Y empeora segun crece el crawl, porque
                # los enlaces crecen mucho mas rapido que los destinos.
                #
                # Ordenar por to_url_hash aprovecha ademas el indice
                # ix_links_to_hash (job_id, to_url_hash) que ya existe.
                rows = session.execute(
                    text(
                        """
                        SELECT DISTINCT ON (l.to_url_hash)
                               l.to_url_hash, l.to_url
                        FROM links l
                        WHERE l.job_id = :jid
                          AND l.is_internal
                          AND l.to_url_hash > :last_hash
                          AND NOT EXISTS (
                            SELECT 1 FROM urls u
                            WHERE u.job_id = l.job_id
                              AND u.url_hash = l.to_url_hash
                          )
                        ORDER BY l.to_url_hash
                        LIMIT :lim
                        """
                    ),
                    {"jid": self.job_id, "last_hash": last_hash, "lim": batch_size},
                ).all()
            finally:
                session.close()

            if not rows:
                return

            # Sembrar la frontera ES progreso, aunque no se rastree nada aun:
            # en un job grande son minutos. Sin este latido, el vigilante de
            # estancamiento del worker daria por colgado un rastreo sano.
            self._write_heartbeat()

            for to_hash, to_url in rows:
                last_hash = to_hash
                if _hash_key(to_hash) in self._already_crawled_hashes:
                    continue
                yield to_url

    async def start(self):
        """Scrapy 2.13+ entry point.

        ``Spider.start_requests`` was deprecated in Scrapy 2.13 and removed
        from the base class in 2.16, so the engine now drives crawls through
        the async ``start()`` method. We delegate to the existing
        ``start_requests`` generator so the seeding/resume logic is shared and
        the spider keeps working across Scrapy 2.11–2.16 (older versions still
        call ``start_requests`` directly).
        """
        for request in self.start_requests():
            yield request

    def start_requests(self) -> Generator[Request, None, None]:
        # Original seeds — skip any already crawled in a previous run.
        for url in self.seed_urls:
            normalized = normalize_url(url)
            if _hash_key(compute_url_hash(normalized)) in self._already_crawled_hashes:
                continue
            # Sin dont_filter (ver _page_request): la semilla quedaba fuera del
            # set de vistas, cualquier enlace interno a la home la volvia a
            # rastrear a mayor profundidad y se perdia el depth 0.
            yield self._page_request(normalized, 0)

        # Sitemap discovery: robots.txt (Sitemap: directives) per seed host,
        # plus any explicitly configured sitemap URLs. Membership is recorded
        # for every listed URL and uncrawled ones are seeded, so pages only
        # reachable via the sitemap (true orphans) still get audited.
        if self._use_sitemap:
            for seed in self.seed_urls:
                req = self._robots_request(seed)
                if req is not None:
                    yield req
            for sm_url in self._extra_sitemap_urls:
                yield from self._sitemap_request(sm_url)

        # Resume frontier: discovered-but-not-yet-crawled URLs from a previous
        # run, streamed lazily from DB. Emitted with depth=1 since we know
        # they were linked from a crawled page; honoring the original depth
        # would require a join we don't pay for.
        if not self._resume_mode:
            return
        if self._reintentar:
            logger.warning(
                "Resume: se repiten %d paginas perdidas, 5xx o con error de Chromium",
                len(self._reintentar),
            )
        for url, depth in self._reintentar:
            normalized = normalize_url(url)
            if not self._should_follow(normalized):
                continue
            req_meta = {"depth": depth}
            if self.render_js and _url_likely_html(normalized):
                req_meta.update(self._playwright_meta())
            yield scrapy.Request(
                url=normalized,
                callback=self.parse,
                errback=self.handle_error,
                meta=req_meta,
                dont_filter=True,
            )
        for url in self._iter_frontier():
            normalized = normalize_url(url)
            if _hash_key(compute_url_hash(normalized)) in self._already_crawled_hashes:
                continue
            if not self._should_follow(normalized):
                continue
            yield self._page_request(normalized, 1)

    # ------------------------------------------------------------------
    # Sitemap ingestion
    # ------------------------------------------------------------------
    def _sitemap_request(self, url: str) -> Generator[Request, None, None]:
        # Cada fichero se pide una sola vez. El dupefilter descartaria la
        # repeticion (dos robots.txt que declaran el mismo sitemap, como el de
        # una semilla y el del host al que redirige), pero ya contada como
        # pedida: al cerrar parecia un sitemap a medio leer.
        clave = _safe_normalize(url)
        if clave in self._sitemaps_pedidos:
            return
        self._sitemaps_pedidos.add(clave)
        # Se cuenta cada fichero de sitemap pedido para poder comparar al
        # cerrar con los realmente parseados: si no coinciden, la vista del
        # sitemap es parcial (tope alcanzado, crawl cerrado antes de leerlos
        # todos, o algun 404) y no se puede afirmar que el resto este fuera.
        self._sitemap_requested += 1
        yield scrapy.Request(
            url=url,
            callback=self.parse_sitemap_response,
            errback=self.handle_sitemap_error,
            meta={"depth": 0},
        )

    def parse_robots_for_sitemaps(self, response: Response) -> Generator:
        """Extract Sitemap: directives from robots.txt; fall back to the
        conventional /sitemap.xml location when none are declared."""
        sitemap_urls: list[str] = []
        if response.status == 200:
            body_text = response.body.decode("utf-8", errors="ignore")
            sitemap_urls = parse_robots_sitemaps(body_text, response.url)
        if not sitemap_urls:
            yield from self._sitemap_request(urljoin(response.url, "/sitemap.xml"))
            return
        for sm_url in sitemap_urls:
            yield from self._sitemap_request(sm_url)

    def handle_robots_error(self, failure) -> Generator:
        """robots.txt unreachable — still try the conventional location."""
        yield from self._sitemap_request(urljoin(failure.request.url, "/sitemap.xml"))

    def parse_sitemap_response(self, response: Response) -> Generator:
        """Parse a sitemap (urlset or index): record membership, seed
        uncrawled URLs, and recurse into child sitemaps under a hard cap."""
        if response.status != 200:
            return
        if self._sitemap_files_fetched >= MAX_SITEMAP_FILES:
            # Marcar que la ingesta quedo incompleta: sin esto, las URLs no
            # vistas se dan por "fuera del sitemap" cuando en realidad no se
            # llego a mirar (ver _persist_sitemap_membership).
            self._sitemap_truncated = True
            logger.warning("Sitemap file cap (%d) reached, ignoring %s",
                           MAX_SITEMAP_FILES, response.url)
            return
        self._sitemap_files_fetched += 1

        page_urls, child_sitemaps = parse_sitemap(response.body, response.url)
        logger.info("Sitemap %s: %d URLs, %d child sitemaps",
                    response.url, len(page_urls), len(child_sitemaps))

        for child in child_sitemaps:
            yield from self._sitemap_request(child)

        for url in page_urls:
            normalized = normalize_url(url)
            if not self._is_internal(normalized):
                continue  # sitemaps must not seed foreign hosts
            url_hash = compute_url_hash(normalized)
            # El sha256 completo va al set de membresia (se escribe en BD);
            # para el set de resume hay que usar la clave compacta.
            self._sitemap_url_hashes.add(url_hash)
            if _hash_key(url_hash) in self._already_crawled_hashes:
                continue
            if not self._should_follow(normalized):
                continue
            # The scheduler dupefilter drops these if link discovery already
            # queued the same URL. La profundidad 1 se respeta gracias a
            # middlewares.DepthMiddleware (la de Scrapy la pisaba).
            yield self._page_request(normalized, 1)

    def handle_sitemap_error(self, failure) -> None:
        logger.debug("Sitemap fetch failed: %s", failure.request.url)

    def _persist_sitemap_membership(self) -> None:
        """Bulk-write Url.in_sitemap for this job at spider close.

        Done once at the end (not per page) so membership applies no matter
        which path discovered each URL, without any crawl-order race. When no
        sitemap was found, rows keep in_sitemap = NULL ("no sitemap data"),
        which the analyzer uses to skip sitemap checks entirely.
        """
        if not self._use_sitemap or not self._sitemap_url_hashes:
            return
        from shared.database import SessionLocal
        from shared.models import Url

        session = SessionLocal()
        try:
            hashes = list(self._sitemap_url_hashes)
            for start in range(0, len(hashes), 5000):
                batch = hashes[start:start + 5000]
                session.query(Url).filter(
                    Url.job_id == self.job_id, Url.url_hash.in_(batch),
                ).update({Url.in_sitemap: True}, synchronize_session=False)
            parcial = (
                self._sitemap_truncated
                or self._sitemap_files_fetched < self._sitemap_requested
            )
            if parcial:
                # Vista parcial del sitemap: el resto se queda en NULL = "no se
                # sabe", no en False. Marcarlo False afirmaria que esas URLs no
                # estan en el sitemap cuando simplemente no se llego a leerlo
                # entero, y el analyzer generaria incidencias not_in_sitemap
                # falsas. Pasa por el tope MAX_SITEMAP_FILES, porque el crawl
                # cierre antes de leerlos todos, o por hijos que fallan.
                # Medido en un Liferay con 395 sitemaps hijos: se leia el 12,6%.
                logger.warning(
                    "Sitemap incompleto (%d de %d ficheros leidos): %d URLs "
                    "marcadas en sitemap; el resto queda como desconocido en "
                    "vez de fuera",
                    self._sitemap_files_fetched,
                    self._sitemap_requested,
                    len(hashes),
                )
            else:
                session.query(Url).filter(
                    Url.job_id == self.job_id, Url.in_sitemap.is_(None),
                ).update({Url.in_sitemap: False}, synchronize_session=False)
            session.commit()
            logger.info("Sitemap membership persisted: %d URLs in sitemap",
                        len(hashes))
        except Exception:
            session.rollback()
            logger.exception("Failed to persist sitemap membership")
        finally:
            session.close()

    # ------------------------------------------------------------------
    # URL filtering
    # ------------------------------------------------------------------
    def _add_allowed_host(self, host: str | None) -> None:
        host = normalize_host(host)
        if host:
            self.allowed_hosts.add(host)
            self.allowed_hosts.add(host.removeprefix("www."))

    def _is_internal(self, url: str) -> bool:
        """Check if URL is internal, with subdomain support."""
        internal = is_internal_url(url, self.allowed_hosts)
        if not internal and self._crawl_subdomains and hasattr(self, "_root_domains"):
            try:
                host = normalize_host(urlparse(url).hostname)
            except ValueError:
                return False
            # Con punto delante: sin el, `notx.com` salia interno para `x.com`
            internal = any(host == rd or host.endswith("." + rd) for rd in self._root_domains)
        return internal

    def _page_request(self, url: str, depth: int) -> Request:
        """Peticion de pagina con la redireccion desactivada.

        `dont_redirect`: el spider sigue las redirecciones el mismo (ver
        `_handle_redirect`). Si las sigue Scrapy, la peticion al destino pasa
        por el dupefilter y, si el destino ya se habia visto, se descarta: la
        301 de origen no llegaba nunca a `parse` y no quedaba registrada. Eso
        vaciaba el informe de redirecciones (http→https, barra final, www) y
        hacia desaparecer del grafo todos los enlaces que apuntaban a ellas.

        Sin dont_filter: en Scrapy ese flag no solo salta el dupefilter, sino
        que ademas NO registra la peticion como vista, y la URL se rastrearia
        otra vez al encontrarla enlazada.
        """
        meta: dict[str, Any] = {"depth": depth, "dont_redirect": True}
        if self.render_js and _url_likely_html(url):
            meta.update(self._playwright_meta())
        return scrapy.Request(
            url=url,
            callback=self.parse,
            errback=self.handle_error,
            meta=meta,
        )

    def _robots_request(self, url: str) -> Request | None:
        """robots.txt del host de `url`, para descubrir sus sitemaps (una vez)."""
        parsed = urlparse(url)
        host = normalize_host(parsed.hostname)
        if not host or host in self._robots_hosts:
            return None
        self._robots_hosts.add(host)
        return scrapy.Request(
            url=f"{parsed.scheme or 'https'}://{parsed.netloc}/robots.txt",
            callback=self.parse_robots_for_sitemaps,
            errback=self.handle_robots_error,
            meta={"depth": 0},
            dont_filter=True,
        )

    def _should_follow(self, url: str) -> bool:
        """Check exclude/include patterns and URL filters."""
        if any(casa(url) for casa in self._exclude_patterns):
            return False
        if self._include_patterns:
            # include patterns defined: only matching URLs pass
            if not any(casa(url) for casa in self._include_patterns):
                return False
        # URL length filter
        if self._max_url_length > 0 and len(url) > self._max_url_length:
            return False
        # Folder depth filter
        if self._max_folder_depth > 0 and compute_folder_depth(url) > self._max_folder_depth:
            return False
        return True

    # ------------------------------------------------------------------
    # Redirects
    # ------------------------------------------------------------------
    @staticmethod
    def _redirect_location(response: Response) -> str | None:
        """Destino absoluto de un 3xx con Location, o None si no redirige."""
        if not (300 <= response.status < 400) or response.status == 304:
            return None
        raw = response.headers.get(b"Location")
        if not raw:
            return None
        try:
            location = raw.decode("utf-8")
        except UnicodeDecodeError:
            location = raw.decode("latin-1")
        return absolutize_url(response.url, location.strip())

    def _handle_redirect(
        self,
        response: Response,
        location: str,
        depth: int,
        response_time_ms: float,
        content_type: str,
    ) -> Generator:
        """Registra un salto 3xx como su propia fila y sigue el destino."""
        url = response.url
        status = response.status
        parsed = urlparse(url)
        yield PageItem(
            url=url,
            url_hash=compute_url_hash(url),
            host=parsed.hostname or "",
            path=parsed.path or "/",
            scheme=parsed.scheme or "https",
            is_internal=self._is_internal(url),
            crawl_depth=depth,
            content_type=content_type or None,
            content_length=len(response.body),
            status_code=status,
            status_group=compute_status_group(status),
            response_time_ms=round(response_time_ms, 2),
            is_html=False,
            resource_type="redirect",
            redirect_url=location,
            body_hash=None,
            job_id=self.job_id,
            url_length=len(url),
            folder_depth=compute_folder_depth(url),
            word_count=None,
            text_ratio=None,
            redirect_type=status,
            status_text=http_status_text(status),
            last_modified=None,
            http_version=response.meta.get("http_protocol") or getattr(response, "protocol", None),
            transfer_size=len(response.body),
            indexability_status=f"Redirect ({status})",
            blocked_by_robots=response.meta.get("blocked_by_robots"),
        )
        yield from self._follow_redirect(url, location, depth)

    def _adoptar_host_de_semilla(self, origen: str, destino: str) -> Generator:
        """Si una semilla redirige a otro host, ese host pasa a ser interno.

        Semilla que redirige a otro dominio (x.com → x.es): sin esto la pagina
        final salia externa, no se seguia ningun enlace y el job terminaba con
        1 URL sin ningun error. Vale para cadenas: el destino de una semilla
        cuenta como semilla.
        """
        if _safe_normalize(origen) not in self._seed_keys:
            return
        self._seed_keys.add(_safe_normalize(destino))
        if self._is_internal(destino):
            return
        logger.warning(
            "La semilla %s redirige a otro host (%s): se rastrea como interno",
            origen, destino,
        )
        self._add_allowed_host(urlparse(destino).hostname)
        if self._use_sitemap:
            req = self._robots_request(destino)
            if req is not None:
                yield req

    def _follow_redirect(self, origen: str, destino: str, depth: int) -> Generator:
        """Sigue el destino de una redireccion (HTTP o meta refresh).

        Misma profundidad que el origen: una redireccion no es un clic. Si
        sumara un nivel, una semilla `http://x.com` → `https://x.com/` le
        quitaria al rastreo un nivel entero con el max_depth por defecto.
        """
        yield from self._adoptar_host_de_semilla(origen, destino)
        if not (self._is_internal(destino) or self.follow_external):
            return
        if not self._should_follow(destino):
            return
        if self._already_crawled_hashes and \
                _hash_key(compute_url_hash(destino)) in self._already_crawled_hashes:
            return
        yield self._page_request(destino, depth)

    # ------------------------------------------------------------------
    # Main parse
    # ------------------------------------------------------------------
    def parse(self, response: Response) -> Generator:
        # Check cancel signal
        if self._should_cancel():
            logger.info("Cancel signal received for job %s, stopping", self.job_id)
            self.crawler.engine.close_spider(self, "cancelled")
            return

        # Check URL limit. max_urls None = sin tope, hasta agotar la frontera.
        if self.max_urls is not None and self._crawled_count >= self.max_urls:
            # WARNING, no INFO: el rastreo queda INCOMPLETO y el grafo de
            # enlaces —y con el el PageRank— se calcula sobre una parte del
            # sitio. Se deja la marca en Redis para que el worker la persista
            # en jobs.finish_reason y el crawl no pase por completo.
            logger.warning(
                "Tope de %d URLs alcanzado: el rastreo queda INCOMPLETO",
                self.max_urls,
            )
            if self._redis is not None:
                try:
                    self._redis.set(f"job:{self.job_id}:finish_reason",
                                    "max_urls_reached")
                except Exception:
                    pass
            self.crawler.engine.close_spider(self, "max_urls_reached")
            return

        # Chromium ha acabado en su pagina de error (chrome-error://...): la
        # navegacion fallo DESPUES de una redireccion (p. ej. http -> https con
        # ":443" explicito en Location). Sin esto se guardaba un 307 con
        # destino "chrome-error://chromewebdata/" y la URL real quedaba sin
        # estado ni destino. Se repite la peticion sin render, que al menos
        # deja el codigo y la cadena de redirecciones verdaderos.
        #
        # OJO, limitacion aparte: con render, una URL http:// de un sitio https
        # se guarda como 307 (la "redireccion interna" de Chromium al subir a
        # https), no como el 301 que devuelve el servidor. Es un artefacto del
        # navegador, no del sitio: tratar esos 307 http->https como 301.
        # Con render, Chromium sigue las redirecciones por su cuenta: si la
        # pagina acaba en OTRO host, Scrapy nunca ha pasado esa URL por el
        # middleware de robots.txt del destino. Asi se guardaron con contenido
        # 22 paginas de un SSO con "Disallow: /" (destino de 307 desde paginas
        # personales). Se repite sin render: la cadena real queda registrada y
        # el robots del destino se respeta como en el modo sin JS.
        otro_host = (
            response.meta.get("playwright")
            and not response.meta.get("_sin_render")
            and not response.url.startswith("chrome-error://")
            and (urlparse(response.url).hostname or "") != (urlparse(response.request.url).hostname or "")
            and not self._is_internal(response.url)
        )
        if otro_host:
            logger.info(
                "Render acabo en otro host (%s -> %s): se repite sin render para respetar su robots",
                response.request.url, response.url,
            )
        if otro_host or (
            response.url.startswith("chrome-error://") and not response.meta.get("_sin_render")
        ):
            original = response.request.url if otro_host else (response.meta.get("redirect_urls") or [response.url])[0]
            if not otro_host:
                logger.warning(
                    "Chromium acabo en pagina de error para %s: se repite sin render",
                    original,
                )
            meta = {
                k: v for k, v in response.meta.items()
                if not k.startswith("playwright") and k not in ("redirect_urls", "redirect_times", "redirect_reasons")
            }
            meta["_sin_render"] = True
            yield scrapy.Request(
                url=original,
                callback=self.parse,
                errback=self.handle_error,
                meta=meta,
                dont_filter=True,
            )
            return

        self._crawled_count += 1
        self._update_redis_progress()

        url = response.url
        parsed = urlparse(url)
        content_type = response.headers.get(b"Content-Type", b"").decode("utf-8", errors="ignore")
        content_length = int(response.headers.get(b"Content-Length", 0) or 0)
        status_code = response.status
        depth = response.meta.get("depth", 0)
        response_time_ms = response.meta.get("download_latency", 0) * 1000
        is_html = isinstance(response, HtmlResponse)
        resource_type = classify_resource_type(content_type, url)
        internal = self._is_internal(url)

        # Redireccion HTTP: se registra el salto y se sigue el destino a mano
        # (ver _page_request). Va antes del filtro de tipo: un 301 no es un
        # PDF ni una imagen aunque la URL acabe en .pdf.
        location = self._redirect_location(response)
        if location is not None:
            yield from self._handle_redirect(response, location, depth,
                                             response_time_ms, content_type)
            return

        # Resource type filter: skip types not enabled in config
        if resource_type not in self._allowed_resource_types:
            return

        # Cadena de redirecciones seguida fuera del spider (con render JS la
        # sigue el navegador): un PageItem por salto. La pagina de este
        # response es SIEMPRE la final: con ella se compara el canonical.
        # Compararlo con la URL pedida marcaba la pagina final de toda
        # redireccion como "Canonicalised" (R5).
        url_for_record = url
        redirect_urls = response.request.meta.get("redirect_urls")
        redirect_reasons = response.request.meta.get("redirect_reasons", [])
        if (
            not redirect_urls
            and response.meta.get("playwright")
            and _safe_normalize(response.request.url) != _safe_normalize(url)
        ):
            # Con render JS, scrapy-playwright rellena redirect_urls con las
            # redirecciones HTTP que sigue el navegador, pero no con las que
            # hace la propia pagina por JavaScript (location.href). Sin esto
            # la URL pedida desaparecia y con ella los enlaces que apuntaban a
            # ella. No hay codigo HTTP que registrar.
            redirect_urls = [response.request.url]
            redirect_reasons = [None]
        if redirect_urls:
            # Yield separate PageItems for each redirect hop in the chain
            # so the UI shows 301/302/etc. entries (Screaming Frog parity)
            chain = list(redirect_urls) + [response.url]
            for i in range(len(chain) - 1):
                hop_url = chain[i]
                hop_dest = chain[i + 1]
                # Scrapy guarda el codigo (int) en los 3xx pero el texto
                # "meta refresh" en las meta refresh: pasarlo tal cual como
                # status reventaba compute_status_group con TypeError y no se
                # guardaba ni la pagina ni su destino.
                reason = redirect_reasons[i] if i < len(redirect_reasons) else None
                hop_status = reason if isinstance(reason, int) else None
                if reason == "meta refresh":
                    hop_status = 200
                    hop_label = "Redirect (meta refresh)"
                elif hop_status is None:
                    hop_label = "Redirect (JS)"
                else:
                    hop_label = f"Redirect ({hop_status})"
                hop_parsed = urlparse(hop_url)
                hop_hash = compute_url_hash(hop_url)
                yield PageItem(
                    url=hop_url,
                    url_hash=hop_hash,
                    host=hop_parsed.hostname or "",
                    path=hop_parsed.path or "/",
                    scheme=hop_parsed.scheme or "https",
                    is_internal=self._is_internal(hop_url),
                    crawl_depth=depth,
                    # El salto no tiene cuerpo propio: heredar el content-type
                    # de la respuesta FINAL hacia que un 301 se listase como
                    # "text/html", y los ceros se leen como "0 bytes" cuando
                    # lo cierto es que no se midio.
                    content_type=None,
                    content_length=None,
                    status_code=hop_status,
                    status_group=(
                        compute_status_group(hop_status)
                        if hop_status is not None and hop_status >= 300 else "3xx"
                    ),
                    response_time_ms=None,
                    is_html=False,
                    resource_type="redirect",
                    redirect_url=hop_dest,
                    body_hash=None,
                    job_id=self.job_id,
                    url_length=len(hop_url),
                    folder_depth=compute_folder_depth(hop_url),
                    word_count=None,
                    text_ratio=None,
                    redirect_type=hop_status if hop_status and hop_status >= 300 else None,
                    status_text=http_status_text(hop_status) if hop_status else None,
                    last_modified=None,
                    http_version=None,
                    transfer_size=None,
                    indexability_status=hop_label,
                )
            yield from self._adoptar_host_de_semilla(chain[0], url)

        # La URL que se guarda, declarada aqui arriba porque todo lo que se
        # calcula sobre la pagina —el canonical el primero— se compara contra
        # ella. Medido antes de arreglarlo: 1.254 de 34.704 paginas de un
        # rastreo y 1.329 de 28.712 de otro marcadas "Canonicalised" con un
        # canonical identico a su propia URL, el 100% alcanzadas por un 301.
        final_url = url_for_record

        # Body hash for duplicate content detection
        body_hash = None
        if is_html and status_code < 400 and hasattr(response, "body"):
            body_hash = hashlib.sha256(response.body).hexdigest()

        # -- Screaming Frog extended fields --------------------------------
        # Redirect type: the HTTP status code of the first redirect hop

        last_modified_val = (
            response.headers.get(b"Last-Modified", b"").decode("utf-8", errors="ignore") or None
        )
        status_text_val = http_status_text(status_code)

        # HTTP version. Scrapy does not reliably expose this on custom
        # download handlers, so the composite handler stashes it in meta when
        # the sub-handler provides it; fall back to response.protocol.
        http_version_val = _normaliza_protocolo(
            response.meta.get("http_protocol") or getattr(response, "protocol", None)
        )
        if not http_version_val:
            # Con render la da el ultimo PageMethod (Navigation Timing).
            for metodo in response.meta.get("playwright_page_methods") or []:
                if getattr(metodo, "args", None) and _JS_PROTOCOLO in metodo.args:
                    http_version_val = _normaliza_protocolo(metodo.result)
                    break

        # HTML-specific fields computed before PageItem yield so that all
        # Screaming Frog parity fields can be included in the single yield.
        word_count_val = None
        text_ratio_val = None
        content_word_count_val = None
        content_hash_val = None
        main_content = None
        indexability_status_val = None
        meta = None
        x_robots = None
        canonical_header = None

        # Only extract HTML content from successful responses (2xx). Antes
        # era <400: el cuerpo de un 304 o de un 300 sin Location se analizaba
        # como una pagina y se seguian sus enlaces.
        is_success = 200 <= status_code < 300
        refresh_target = None

        if is_html and is_success:
            selector = response.selector

            # Effective base URL for resolving relative URLs (honours <base href>)
            base_url = effective_base_url(selector, response.url)

            # Extract meta first so we can compute indexability. Passing the
            # base URL resolves relative canonicals/og:url to absolute, so
            # self-referencing canonicals are not misread as canonicalised.
            meta = extract_meta(selector, base_url=base_url)

            # X-Robots-Tag: TODAS las cabeceras, no solo la ultima. Un
            # servidor puede mandar dos (`noindex` y `noarchive`) y con
            # `headers.get` la pagina salia indexable.
            cabeceras_robots = [
                valor.decode("utf-8", errors="ignore")
                for valor in response.headers.getlist(b"X-Robots-Tag")
                if valor
            ]
            x_robots = ", ".join(cabeceras_robots) or None

            # Canonical from Link header
            link_header = response.headers.get(b"Link", b"").decode("utf-8", errors="ignore")
            if 'rel="canonical"' in link_header:
                parts = link_header.split(";")
                if parts:
                    canonical_header = parts[0].strip().strip("<>")

            # Word count and text ratio
            word_count_val = extract_word_count(selector)
            visible_text = extract_visible_text(selector)
            text_ratio_val = compute_text_ratio(response.text, visible_text)

            # Palabras del contenido PRINCIPAL, no del body. word_count incluye
            # el menu, el pie y el megamenu: medido en un censo, eso son mas de
            # 200 palabras por pagina, asi que una ficha con dos frases pasaba
            # el umbral de thin content sin que nadie lo viera. Lo que Google
            # valora es el contenido, no la plantilla repetida en todas.
            # Se calcula aqui, antes del PageItem, y se reutiliza en el
            # ContentItem de mas abajo: el contenido se extrae una sola vez.
            if self._extraction.get("extract_page_content", True):
                main_content = extract_main_content(
                    selector,
                    word_count=word_count_val,
                    strip_promo=self._extraction.get("strip_promo_blocks", True),
                    extra_selectors=self._extraction.get("custom_boilerplate_selectors") or None,
                )
                content_word_count_val = contar_palabras(main_content)
                content_hash_val = hash_de_contenido(main_content)

            # Indexabilidad: una sola funcion, compartida con el analyzer
            # (shared/indexabilidad.py). Tenerla por duplicado hacia que la
            # misma pagina fuese "Canonicalised" para uno e "Indexable" para el
            # otro.
            is_indexable, indexability_status_val = estado_indexabilidad(
                status_code,
                meta_robots={
                    "robots": meta.get("meta_robots"),
                    "googlebot": meta.get("meta_robots_googlebot"),
                },
                x_robots=cabeceras_robots,
                canonical_href=meta.get("canonical_href"),
                page_url=final_url,
                canonical_header=canonical_header,
                bloqueada_por_robots=bool(response.meta.get("blocked_by_robots")),
            )

            # Meta refresh con destino: es una redireccion (Google trata la
            # inmediata como permanente). Se guarda como redirect_url para que
            # la cadena y el PageRank lleguen al destino, y se sigue abajo.
            refresh_target = extract_meta_refresh_target(selector, base_url)
            if refresh_target == _safe_normalize(url_for_record):
                refresh_target = None
            if refresh_target:
                indexability_status_val = "Redirect (meta refresh)"
        else:
            # No es HTML, o no es 2xx: la misma funcion decide, sin duplicar
            # aqui la escala de codigos.
            _, indexability_status_val = estado_indexabilidad(
                status_code,
                page_url=url_for_record,
                bloqueada_por_robots=bool(response.meta.get("blocked_by_robots")),
            )

        # -- PageItem for the final destination (always yielded) -----------
        # For redirected URLs, this records the FINAL destination with its
        # actual status code (usually 200).  The redirect hops were already
        # yielded above.
        final_hash = compute_url_hash(final_url)
        final_parsed = urlparse(final_url)
        yield PageItem(
            url=final_url,
            url_hash=final_hash,
            host=final_parsed.hostname or "",
            path=final_parsed.path or "/",
            scheme=final_parsed.scheme or "https",
            is_internal=self._is_internal(final_url),
            crawl_depth=depth,
            content_type=content_type,
            content_length=content_length or len(response.body),
            status_code=status_code,
            status_group=compute_status_group(status_code),
            response_time_ms=round(response_time_ms, 2),
            is_html=is_html,
            resource_type=resource_type,
            redirect_url=refresh_target,  # destino final, salvo meta refresh
            body_hash=body_hash,
            job_id=self.job_id,
            # Screaming Frog parity fields
            url_length=len(final_url),
            folder_depth=compute_folder_depth(final_url),
            word_count=word_count_val,
            content_word_count=content_word_count_val,
            content_hash=content_hash_val,
            text_ratio=text_ratio_val,
            redirect_type=None,
            status_text=status_text_val,
            last_modified=last_modified_val,
            http_version=http_version_val,
            transfer_size=len(response.body),
            indexability_status=indexability_status_val,
            blocked_by_robots=response.meta.get("blocked_by_robots"),
        )

        if refresh_target:
            yield from self._follow_redirect(url_for_record, refresh_target, depth)

        # -- HTML-specific extraction (only for 2xx HTML) ------------------
        if not is_html or not is_success:
            return

        selector = response.selector

        # Detect <meta> tags outside <head>
        has_meta_outside_head = bool(selector.css("body meta"))

        yield HtmlMetaItem(
            url_hash=final_hash,
            job_id=self.job_id,
            title=meta["title"],
            title_len=meta["title_len"],
            meta_description=meta["meta_description"],
            meta_description_len=meta["meta_description_len"],
            meta_keywords=meta["meta_keywords"],
            meta_robots=meta["meta_robots"],
            x_robots_tag=x_robots,
            canonical_href=meta["canonical_href"],
            canonical_header=canonical_header,
            og_title=meta["og_title"],
            og_description=meta["og_description"],
            og_image=meta["og_image"],
            og_url=meta["og_url"],
            og_type=meta["og_type"],
            twitter_card=meta["twitter_card"],
            twitter_title=meta["twitter_title"],
            twitter_description=meta["twitter_description"],
            rel_next=meta["rel_next"],
            rel_prev=meta["rel_prev"],
            # Screaming Frog parity fields
            title_pixel_width=(
                estimate_title_pixel_width(meta["title"]) if meta["title"] else None
            ),
            meta_description_pixel_width=(
                estimate_description_pixel_width(meta["meta_description"])
                if meta["meta_description"]
                else None
            ),
            meta_refresh=extract_meta_refresh(selector),
            has_meta_outside_head=has_meta_outside_head,
        )

        # Headings
        for heading in extract_headings(selector):
            yield HeadingItem(
                url_hash=final_hash,
                job_id=self.job_id,
                tag=heading["tag"],
                position=heading["position"],
                text=heading["text"],
                oculto=heading.get("oculto"),
            )

        # Page-level nofollow: meta robots / X-Robots-Tag "nofollow" (or
        # "none") makes EVERY link on the page nofollow, as Google applies it.
        combined_tokens = robots_tokens(meta.get("meta_robots")) | robots_tokens(x_robots)
        page_nofollow = "nofollow" in combined_tokens or "none" in combined_tokens

        # Links (extract_links already returns enhanced SF fields)
        links = extract_links(selector, base_url, self.allowed_hosts, page_nofollow=page_nofollow)
        for link in links:
            yield LinkItem(
                from_url_hash=final_hash,
                to_url=link["url"],
                to_url_hash=compute_url_hash(link["url"]),
                anchor_text=link["anchor_text"],
                rel=link["rel"],
                is_internal=link["is_internal"],
                link_position=link["link_position"],
                job_id=self.job_id,
                # Screaming Frog parity fields
                follow=link.get("follow", True),
                target=link.get("target"),
                alt_text=link.get("alt_text"),
                link_type=link.get("link_type", "hyperlink"),
            )

        # Hreflang
        if self._extraction.get("extract_hreflang", True):
            for hreflang in extract_hreflang(selector, base_url=base_url):
                yield HreflangItem(
                    url_hash=final_hash,
                    job_id=self.job_id,
                    lang=hreflang["lang"],
                    href=hreflang["href"],
                )

        # Structured data
        if self._extraction.get("extract_structured_data", True):
            try:
                sd_items = extract_structured_data(response.text, response.url)
            except Exception as exc:
                logger.debug("Structured data extraction failed for %s: %s", response.url, exc)
                sd_items = []
            for sd in sd_items:
                yield StructuredDataItem(
                    url_hash=final_hash,
                    job_id=self.job_id,
                    raw=sd["raw"],
                    format=sd["format"],
                    schema_type=sd["schema_type"],
                )

        # Resources (extract_resources already returns width, height,
        # is_mixed_content)
        for resource in extract_resources(selector, base_url):
            yield ResourceItem(
                url_hash=final_hash,
                job_id=self.job_id,
                resource_url=resource["url"],
                resource_type=resource["resource_type"],
                alt_text=resource["alt_text"],
                # Screaming Frog parity fields
                width=resource.get("width"),
                height=resource.get("height"),
                is_mixed_content=resource.get("is_mixed_content", False),
            )

        # -- SecurityItem -------------------------------------------------
        if self._extraction.get("extract_security_headers", True):
            # Build a plain-string header dict for extract_security_headers.
            # Scrapy headers: keys are bytes, values are lists of bytes.
            header_dict: dict[str, str] = {}
            for key, values in response.headers.items():
                if not values:
                    continue
                header_name = (
                    key.decode("utf-8", errors="ignore") if isinstance(key, bytes) else key
                )
                header_value = (
                    values[-1].decode("utf-8", errors="ignore")
                    if isinstance(values[-1], bytes)
                    else str(values[-1])
                )
                header_dict[header_name] = header_value

            sec = extract_security_headers(header_dict)
            mixed_content_urls = detect_mixed_content(selector, response.url)

            # Detect unsafe crossorigin: target="_blank" without rel="noopener"
            has_unsafe_crossorigin = False
            for link in links:
                target = (link.get("target") or "").lower()
                if target == "_blank":
                    rel_val = link.get("rel") or ""
                    rel_tokens = {t.strip().lower() for t in rel_val.split()}
                    if "noopener" not in rel_tokens and "noreferrer" not in rel_tokens:
                        has_unsafe_crossorigin = True
                        break

            yield SecurityItem(
                url_hash=final_hash,
                job_id=self.job_id,
                is_https=final_parsed.scheme == "https",
                has_mixed_content=len(mixed_content_urls) > 0,
                has_hsts=sec["has_hsts"],
                has_csp=sec["has_csp"],
                has_x_content_type_options=sec["has_x_content_type_options"],
                has_x_frame_options=sec["has_x_frame_options"],
                referrer_policy=sec["referrer_policy"],
                has_unsafe_crossorigin=has_unsafe_crossorigin,
            )

        # -- ContentItem (main page text + markdown) -----------------------
        if self._extraction.get("extract_page_content", True):
            strip_promo = self._extraction.get("strip_promo_blocks", True)
            extra_selectors = self._extraction.get("custom_boilerplate_selectors") or None
            # main_content ya se extrajo arriba, para que content_word_count
            # viaje en el PageItem.
            # El HTML crudo solo si el job lo pide: medido en un censo real,
            # 170 kB de media por pagina — 29.808 paginas son 4,9 GB antes de
            # que Postgres lo comprima.
            guardar_html = self._extraction.get("store_raw_html", False)
            html_crudo = response.text if guardar_html else None

            # Se emite la fila tambien SIN contenido extraido cuando se pide el
            # HTML. Es justo al reves de lo que parece: una pagina que sale con
            # 0 palabras es la que hay que poder auditar, y si no se guarda su
            # HTML no hay forma de saber si fallo el extractor o la pagina esta
            # vacia de verdad. Sin el flag se mantiene el comportamiento de
            # siempre: sin contenido, no hay fila.
            if main_content or guardar_html:
                content_md = extract_main_content_markdown(
                    selector,
                    word_count=word_count_val,
                    strip_promo=strip_promo,
                    extra_selectors=extra_selectors,
                ) if main_content else None
                yield ContentItem(
                    url_hash=final_hash,
                    job_id=self.job_id,
                    content_text=main_content,
                    content_length=len(main_content) if main_content else 0,
                    content_markdown=content_md,
                    raw_html=html_crudo,
                )

        # -- Follow links (BFS) -----------------------------------------
        # extract_links no longer dedupes within a page (so every inlink is
        # recorded), so dedupe the follow set here to avoid enqueueing the
        # same target multiple times from one page.
        if depth < self.max_depth:
            followed_hashes: set[str] = set()
            for link in links:
                link_internal = self._is_internal(link["url"]) if self._crawl_subdomains else link["is_internal"]
                should_follow = link_internal or self.follow_external
                if should_follow and (link.get("follow", True) or self._follow_nofollow):
                    if self._should_follow(link["url"]):
                        link_hash = compute_url_hash(link["url"])
                        if link_hash in followed_hashes:
                            continue
                        followed_hashes.add(link_hash)
                        # Resume: skip URLs already crawled in a previous run.
                        if self._already_crawled_hashes and \
                                _hash_key(link_hash) in self._already_crawled_hashes:
                            continue
                        yield self._page_request(link["url"], depth + 1)

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------
    def handle_error(self, failure):
        """Handle download errors (DNS, timeouts, connection refused, etc.)."""
        request = failure.request
        url = request.url
        url_hash = compute_url_hash(url)
        parsed = urlparse(url)
        depth = request.meta.get("depth", 0)

        status_group = "unknown"
        status_code = None
        if failure.check(scrapy.exceptions.IgnoreRequest):
            # Normalmente es robots.txt. Si la URL bloqueada es el final de una
            # cadena de redirecciones, la URL ORIGINAL (la nuestra) desaparecia
            # del informe: nadie sabia que redirigia a algo prohibido. Se
            # registran los saltos con su codigo; el destino bloqueado no se
            # pide ni se guarda.
            # Si lo que robots.txt tumba es una SEMILLA, el rastreo no va a
            # empezar: se deja la marca para que el worker pueda decir por que
            # el job acabo con cero URLs en vez de darlo por completado (#37).
            if _safe_normalize(url) in getattr(self, "_seed_keys", set()):
                logger.warning(
                    "robots.txt prohibe la semilla %s: el rastreo no puede empezar", url,
                )
                if self._redis is not None:
                    try:
                        self._redis.set(f"job:{self.job_id}:robots_bloquea_semillas", 1)
                    except Exception:
                        pass

            # La URL bloqueada SI se guarda, como hace Screaming Frog. No se
            # pide —robots.txt se respeta— pero existe, el sitio la enlaza y el
            # cliente necesita la lista: o es un bloqueo por error sobre
            # contenido que deberia posicionar, o es intencionado y entonces son
            # enlaces internos gastando presupuesto de rastreo. Antes el
            # IgnoreRequest se descartaba aqui y la URL no aparecia en ningun
            # informe: ni bloqueada, ni enlazada, ni nada.
            # status_code va NULL a proposito: no se llego a pedir, y un 0 en el
            # CSV se lee como "respondio 0".
            if self._is_internal(url) and not request.meta.get("es_robots_txt"):
                self._crawled_count += 1
                yield PageItem(
                    url=url, url_hash=url_hash,
                    host=parsed.hostname or "", path=parsed.path or "/",
                    scheme=parsed.scheme or "https",
                    is_internal=True, crawl_depth=depth,
                    content_type=None, content_length=None,
                    status_code=None, status_group="blocked",
                    response_time_ms=None, is_html=False, resource_type="blocked",
                    redirect_url=None, body_hash=None, job_id=self.job_id,
                    url_length=len(url), folder_depth=compute_folder_depth(url),
                    word_count=None, text_ratio=None, redirect_type=None,
                    status_text="Blocked by robots.txt", last_modified=None,
                    http_version=None, transfer_size=None,
                    indexability_status="Blocked by robots.txt",
                    blocked_by_robots=True,
                )

            redirect_urls = request.meta.get("redirect_urls") or []
            if redirect_urls:
                reasons = request.meta.get("redirect_reasons") or []
                chain = list(redirect_urls) + [url]
                logger.info(
                    "Cadena de redireccion cortada por robots.txt: %s -> %s", chain[0], url
                )
                for i in range(len(chain) - 1):
                    hop_url, hop_dest = chain[i], chain[i + 1]
                    hop_status = reasons[i] if i < len(reasons) else 301
                    hp = urlparse(hop_url)
                    self._crawled_count += 1
                    yield PageItem(
                        url=hop_url, url_hash=compute_url_hash(hop_url),
                        host=hp.hostname or "", path=hp.path or "/", scheme=hp.scheme or "https",
                        is_internal=self._is_internal(hop_url), crawl_depth=depth,
                        # NULL, no 0: de este salto no se llego a medir nada
                        # (la cadena la corto robots.txt). Un 0 en el CSV se
                        # lee como "0 bytes medidos".
                        content_type=None, content_length=None,
                        status_code=hop_status, status_group=compute_status_group(hop_status),
                        response_time_ms=None, is_html=False, resource_type="redirect",
                        redirect_url=hop_dest, body_hash=None, job_id=self.job_id,
                        url_length=len(hop_url), folder_depth=compute_folder_depth(hop_url),
                        word_count=None, text_ratio=None, redirect_type=hop_status,
                        status_text=http_status_text(hop_status), last_modified=None,
                        http_version=None, transfer_size=None,
                        indexability_status=f"Redirect ({hop_status})",
                    )
            return
        from twisted.internet.error import (
            DNSLookupError,
            TCPTimedOutError,
            TimeoutError,
            ConnectionRefusedError,
        )
        if failure.check(DNSLookupError):
            status_group = "dns_error"
        elif failure.check(TimeoutError, TCPTimedOutError):
            status_group = "timeout"
        elif failure.check(ConnectionRefusedError):
            status_group = "conn_refused"
        else:
            status_group = "error"

        # WARNING, no DEBUG: una peticion fallida se persiste con status_code
        # NULL y desaparece de cualquier informe. En DEBUG nadie la veia, y asi
        # es como paso inadvertido que el render JS perdia paginas que sin JS
        # respondian 200. El motivo concreto va incluido.
        logger.warning(
            "Request failed [%s]: %s (%s)",
            status_group,
            url,
            failure.getErrorMessage(),
        )

        self._crawled_count += 1

        yield PageItem(
            url=url,
            url_hash=url_hash,
            host=parsed.hostname or "",
            path=parsed.path or "/",
            scheme=parsed.scheme or "https",
            is_internal=self._is_internal(url),
            crawl_depth=depth,
            content_type=None,
            content_length=0,
            status_code=status_code,
            status_group=status_group,
            response_time_ms=0,
            is_html=False,
            resource_type="other",
            redirect_url=None,
            body_hash=None,
            job_id=self.job_id,
            # Screaming Frog parity fields
            url_length=len(url),
            folder_depth=compute_folder_depth(url),
            word_count=None,
            text_ratio=None,
            redirect_type=None,
            status_text=http_status_text(status_code) if status_code else None,
            last_modified=None,
            http_version=None,
            transfer_size=0,
            indexability_status=None,
        )

    # ------------------------------------------------------------------
    # Redis helpers
    # ------------------------------------------------------------------
    def _update_redis_progress(self):
        """Push crawled count + liveness heartbeat to Redis periodically.

        The heartbeat lets the worker's stale-job recovery tell a genuinely
        stuck crawl apart from a long-but-healthy one, so it never re-queues
        (and thus double-crawls) a job that is still making progress.
        """
        if self._redis is None:
            return
        if self._crawled_count % self._redis_update_interval != 0:
            return
        try:
            self._redis.set(
                f"job:{self.job_id}:crawled_count", self._crawled_count
            )
            self._write_pending_count()
            self._write_heartbeat()
        except Exception as exc:
            logger.debug("Redis progress update failed: %s", exc)

    def _write_pending_count(self):
        """Publica la LONGITUD REAL de la cola del planificador.

        Antes la cola se deducia de la BD (enlaces internos cuyo destino no
        estaba en `urls`), y esa cifra nunca llegaba a cero: cuando el crawler
        sigue un redirect y el destino ya estaba rastreado, Scrapy descarta la
        peticion por duplicada y la URL original no llega a registrarse, pero su
        enlace si — asi que se contaba como pendiente para siempre. Medido en un
        rastreo real: 4.955 "pendientes" con la frontera ya agotada.

        El planificador sabe exactamente cuantas quedan, asi que se le pregunta
        a el. Es ademas lo que enseña Screaming Frog.
        """
        if self._redis is None:
            return
        try:
            slot = getattr(self.crawler.engine, "_slot", None) or getattr(
                self.crawler.engine, "slot", None
            )
            if slot is None or getattr(slot, "scheduler", None) is None:
                return
            self._redis.set(
                f"job:{self.job_id}:pending_count", len(slot.scheduler)
            )
        except Exception:
            # La cola es informativa: si la version de Scrapy no la expone, se
            # calla y /progress cae al calculo aproximado desde la BD.
            pass

    def _write_heartbeat(self):
        """Stamp the current time as this job's liveness heartbeat."""
        if self._redis is None:
            return
        try:
            self._redis.set(f"job:{self.job_id}:heartbeat", time.time())
        except Exception:
            pass

    def _should_cancel(self) -> bool:
        """Check whether a cancel signal has been set in Redis."""
        if self._redis is None:
            return False
        try:
            val = self._redis.get(f"job:{self.job_id}:cancel")
            return val is not None and str(val).lower() in ("1", "true", "yes")
        except Exception:
            return False
