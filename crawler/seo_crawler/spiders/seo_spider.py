"""
Main SEO spider.

Usage::

    scrapy crawl seo -a job_id=<uuid>

The spider loads its seed URLs and configuration from the ``jobs`` table,
then performs a BFS crawl extracting all SEO-relevant data.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import re
from typing import Any, Generator
from urllib.parse import urljoin, urlparse

import redis
import scrapy
from scrapy import Request, signals
from scrapy.http import HtmlResponse, Response
from scrapy_playwright.page import PageMethod

from seo_crawler.extractors import (
    classify_resource_type,
    compute_folder_depth,
    compute_indexability_status,
    compute_status_group,
    compute_text_ratio,
    compute_url_hash,
    detect_mixed_content,
    estimate_description_pixel_width,
    estimate_title_pixel_width,
    extract_headings,
    extract_hreflang,
    extract_links,
    extract_main_content,
    extract_main_content_markdown,
    extract_meta,
    extract_meta_refresh,
    extract_resources,
    extract_security_headers,
    extract_structured_data,
    extract_visible_text,
    extract_word_count,
    http_status_text,
    is_internal_url,
    normalize_url,
)
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


# ---------------------------------------------------------------------------
# Boilerplate DOM cleanup — runs inside Chromium via PageMethod("evaluate")
# after page load, BEFORE Scrapy captures the HTML.  Removes cookie banners,
# consent overlays, chat widgets, and ARIA modals so extractors only see
# real page content.
# ---------------------------------------------------------------------------
# T4: evaluated in the final document to disambiguate HTTP vs JS redirects.
_NAV_ENTRIES_JS = """
() => performance.getEntriesByType('navigation').map(e => ({
    name: e.name, redirectCount: e.redirectCount, type: e.type
}))
"""

_BOILERPLATE_REMOVAL_JS = """
() => {
    const r = (s) => { try { document.querySelectorAll(s).forEach(e => e.remove()); } catch(_) {} };

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
        self.max_urls: int = 50_000
        self.follow_external: bool = False
        self._exclude_patterns: list[str] = []
        self._include_patterns: list[str] = []
        self._crawled_count: int = 0
        self._redis: redis.Redis | None = None
        self._redis_update_interval: int = 50
        # Resume support: hashes of URLs already crawled in a previous run for
        # this same job, plus the discovered-but-not-yet-crawled frontier.
        self._already_crawled_hashes: set[str] = set()
        self._frontier_urls: list[str] = []

        # T13: crawl-trap detector (set in spider_opened when enabled)
        self._trap_detector = None

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

            # T8: activate the per-job URL normalization policy for this
            # crawl subprocess. Every normalize_url/compute_url_hash call
            # (spider, extractors, pipeline, sitemap ingest) picks it up.
            from shared.url_normalization import (
                UrlNormalizationConfig,
                set_active_config,
            )
            set_active_config(
                UrlNormalizationConfig.from_job_config(self.job_config)
            )

            self.max_depth = self.job_config.get("max_depth", 3)
            self.max_urls = self.job_config.get("max_urls", 50_000)
            self.follow_external = self.job_config.get("follow_external", False)
            self._exclude_patterns = self.job_config.get("exclude_patterns", [])
            self._include_patterns = self.job_config.get("include_patterns", [])
            self.render_js = self.job_config.get("render_js", False)

            # Build allowed-hosts from seed URLs
            for seed in self.seed_urls:
                parsed = urlparse(seed)
                if parsed.hostname:
                    host = parsed.hostname.lower()
                    self.allowed_hosts.add(host)
                    self.allowed_hosts.add(host.removeprefix("www."))

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

            # -- Advanced config: HTTP config (for middleware) --
            self._http_config = self.job_config.get("http", {})

            # -- Subdomain crawling: expand allowed hosts --
            if self._crawl_subdomains:
                self._root_domains: set[str] = set()
                for host in list(self.allowed_hosts):
                    parts = host.split(".")
                    if len(parts) >= 2:
                        self._root_domains.add(".".join(parts[-2:]))

            logger.info(
                "Job %s loaded: %d seeds, max_depth=%d, max_urls=%d, hosts=%s",
                self.job_id,
                len(self.seed_urls),
                self.max_depth,
                self.max_urls,
                self.allowed_hosts,
            )

            # -- T13: crawl-trap detection (flag off by default)
            trap_cfg = self.job_config.get("trap_detection") or {}
            self._trap_detector = None
            if trap_cfg.get("enabled"):
                from seo_crawler.trap_detection import TrapDetector

                self._trap_detector = TrapDetector(
                    max_urls_per_pattern=trap_cfg.get("max_urls_per_pattern", 500),
                    max_param_combinations=trap_cfg.get("max_param_combinations", 3),
                )

            # -- T16: robots.txt snapshot (always on, one fetch per host);
            # a failure never aborts the crawl.
            try:
                from seo_crawler.robots_snapshot import persist_robots_snapshots

                persist_robots_snapshots(session, job.id, self.seed_urls)
                session.commit()
            except Exception:
                session.rollback()
                logger.warning(
                    "robots.txt snapshot failed for job %s; crawling anyway",
                    self.job_id, exc_info=True,
                )

            # -- T1: sitemap ingestion (flag off by default). Runs after
            # set_active_config so sitemap hashes match crawl hashes. A
            # sitemap failure never aborts the crawl.
            if self.job_config.get("ingest_sitemaps"):
                try:
                    from seo_crawler.sitemap_ingest import ingest_sitemaps

                    count = ingest_sitemaps(session, job.id, self.seed_urls)
                    session.commit()
                    logger.info(
                        "Sitemap ingestion done for job %s: %d URLs",
                        self.job_id, count,
                    )
                except Exception:
                    session.rollback()
                    logger.warning(
                        "Sitemap ingestion failed for job %s; crawling anyway",
                        self.job_id, exc_info=True,
                    )

            # -- Resume detection: load already-crawled URL hashes + frontier
            # If this job already has rows in `urls`, treat the run as a resume:
            # skip URLs we already fetched and seed the queue with the
            # discovered-but-not-yet-crawled internal links from the `links`
            # table so the BFS picks up where it left off.
            from shared.models import Link, Url

            already_rows = (
                session.query(Url.url_hash)
                .filter(Url.job_id == self.job_id)
                .all()
            )
            if already_rows:
                self._already_crawled_hashes = {row[0] for row in already_rows}
                # Discovered-but-not-crawled internal links (frontier).
                # NOT EXISTS lets Postgres use the indexed (job_id, url_hash)
                # lookup on `urls`, so this scales beyond a few thousand URLs.
                # Cap the result count to keep start_requests time bounded; if
                # a job has more than this in flight, the rest will be
                # rediscovered as the crawl progresses through the frontier.
                FRONTIER_CAP = 200_000
                already_subq = (
                    session.query(Url.url_hash)
                    .filter(
                        Url.job_id == Link.job_id,
                        Url.url_hash == Link.to_url_hash,
                    )
                    .exists()
                )
                frontier_rows = (
                    session.query(Link.to_url)
                    .filter(
                        Link.job_id == self.job_id,
                        Link.is_internal.is_(True),
                        ~already_subq,
                    )
                    .distinct()
                    .limit(FRONTIER_CAP)
                    .all()
                )
                self._frontier_urls = [row[0] for row in frontier_rows]
                self._crawled_count = len(self._already_crawled_hashes)
                logger.info(
                    "Resume mode for job %s: %d URLs already crawled, "
                    "%d frontier URLs to seed",
                    self.job_id,
                    len(self._already_crawled_hashes),
                    len(self._frontier_urls),
                )
        finally:
            session.close()

        # Redis connection for progress updates and cancel checks
        redis_url = self.settings.get("REDIS_URL", "redis://localhost:6379/0")
        try:
            self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
            self._redis.ping()
            logger.info("Redis connected for job progress tracking")
        except Exception as exc:
            logger.warning("Redis unavailable; progress tracking disabled: %s", exc)
            self._redis = None

    def spider_closed(self, spider, reason):
        """Push final count and close Redis connection."""
        # T13: persist capped trap patterns; nothing is lost silently.
        if self._trap_detector is not None:
            events = self._trap_detector.events()
            if events:
                from shared.database import SessionLocal
                from shared.models import CrawlTrapEvent

                session = SessionLocal()
                try:
                    for ev in events:
                        session.add(CrawlTrapEvent(job_id=self.job_id, **ev))
                    session.commit()
                    logger.warning(
                        "Crawl traps detected for job %s: %d patterns capped",
                        self.job_id, len(events),
                    )
                except Exception:
                    session.rollback()
                    logger.exception("Failed to persist crawl trap events")
                finally:
                    session.close()

        if self._redis:
            try:
                self._redis.set(
                    f"job:{self.job_id}:crawled_count", self._crawled_count
                )
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
                # Brief wait for consent-management scripts to inject their
                # banners (they typically fire on DOMContentLoaded / load).
                PageMethod("wait_for_timeout", 2000),
                PageMethod("evaluate", _BOILERPLATE_REMOVAL_JS),
                # T4: navigation probe for JS-redirect detection. Runs in
                # the FINAL document; redirectCount tells HTTP hops apart
                # from JS-driven URL changes (see _detect_js_redirect).
                PageMethod("evaluate", _NAV_ENTRIES_JS),
            ],
        }

    def _detect_js_redirect(self, response) -> str | None:
        """T4: return the browser's final URL when it differs from the
        requested one for reasons other than HTTP redirects.

        With Playwright the browser follows HTTP redirects internally, so
        ``response.url`` (page.url) != ``request.url`` covers both cases.
        The navigation probe evaluated in the final document disambiguates:
        ``redirectCount == 0`` means the final navigation involved no HTTP
        redirect — the URL change came from JS (location.replace/assign or
        an early meta refresh). Conservative: ambiguous cases return None.
        """
        if compute_url_hash(response.url) == compute_url_hash(response.request.url):
            return None

        for pm in response.request.meta.get("playwright_page_methods", []):
            args = getattr(pm, "args", None)
            if args and args[0] == _NAV_ENTRIES_JS:
                entries = getattr(pm, "result", None) or []
                if entries and entries[-1].get("redirectCount", 0) == 0:
                    return response.url
                return None
        return None

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
        # T6: soft-404 probe per host (flag off by default). The probe
        # response never becomes a `urls` row: its callback only persists
        # the error-template signature into job.config.
        if self.job_config.get("detect_soft_404"):
            import uuid as _uuid

            seen_hosts: set[str] = set()
            for seed in self.seed_urls:
                parsed = urlparse(seed)
                if not parsed.hostname or parsed.hostname in seen_hosts:
                    continue
                seen_hosts.add(parsed.hostname)
                probe_url = (
                    f"{parsed.scheme}://{parsed.netloc}"
                    f"/__soft404_probe_{_uuid.uuid4().hex}"
                )
                yield scrapy.Request(
                    url=probe_url,
                    callback=self._handle_soft404_probe,
                    errback=lambda f: None,
                    meta={"depth": 0, "soft404_probe": True},
                    dont_filter=True,
                )

        # Original seeds — skip any already crawled in a previous run.
        for url in self.seed_urls:
            normalized = normalize_url(url)
            if compute_url_hash(normalized) in self._already_crawled_hashes:
                continue
            req_meta: dict[str, Any] = {"depth": 0}
            if self.render_js and _url_likely_html(normalized):
                req_meta.update(self._playwright_meta())
            yield scrapy.Request(
                url=normalized,
                callback=self.parse,
                errback=self.handle_error,
                meta=req_meta,
                dont_filter=True,
            )

        # Resume frontier: discovered-but-not-yet-crawled URLs from a previous
        # run. Emitted with depth=1 since we know they were linked from a
        # crawled page; honoring the original depth would require a join we
        # don't pay for.
        for url in self._frontier_urls:
            normalized = normalize_url(url)
            if compute_url_hash(normalized) in self._already_crawled_hashes:
                continue
            if not self._should_follow(normalized):
                continue
            req_meta = {"depth": 1}
            if self.render_js and _url_likely_html(normalized):
                req_meta.update(self._playwright_meta())
            yield scrapy.Request(
                url=normalized,
                callback=self.parse,
                errback=self.handle_error,
                meta=req_meta,
                dont_filter=True,
            )

    # ------------------------------------------------------------------
    # URL filtering
    # ------------------------------------------------------------------
    def _is_internal(self, url: str) -> bool:
        """Check if URL is internal, with subdomain support."""
        internal = is_internal_url(url, self.allowed_hosts)
        if not internal and self._crawl_subdomains and hasattr(self, "_root_domains"):
            host = urlparse(url).hostname or ""
            internal = any(host.endswith(rd) or host == rd for rd in self._root_domains)
        return internal

    def _should_follow(self, url: str) -> bool:
        """Check exclude/include patterns and URL filters."""
        if self._exclude_patterns:
            for pattern in self._exclude_patterns:
                if fnmatch.fnmatch(url, pattern) or re.search(pattern, url):
                    return False
        if self._include_patterns:
            for pattern in self._include_patterns:
                if fnmatch.fnmatch(url, pattern) or re.search(pattern, url):
                    return True
            return False  # include patterns defined but none matched
        # URL length filter
        if self._max_url_length > 0 and len(url) > self._max_url_length:
            return False
        # Folder depth filter
        if self._max_folder_depth > 0 and compute_folder_depth(url) > self._max_folder_depth:
            return False
        return True

    # ------------------------------------------------------------------
    # Main parse
    # ------------------------------------------------------------------
    def parse(self, response: Response) -> Generator:
        # Check cancel signal
        if self._should_cancel():
            logger.info("Cancel signal received for job %s, stopping", self.job_id)
            self.crawler.engine.close_spider(self, "cancelled")
            return

        # Check URL limit
        if self._crawled_count >= self.max_urls:
            logger.info("Max URL limit (%d) reached, stopping", self.max_urls)
            self.crawler.engine.close_spider(self, "max_urls_reached")
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

        # Resource type filter: skip types not enabled in config
        if resource_type not in self._allowed_resource_types:
            return

        # Detect redirect: store where this page redirects TO
        # Also yield separate PageItems for each intermediate redirect hop.
        redirect_url = None
        redirect_urls = response.request.meta.get("redirect_urls")
        redirect_reasons = response.request.meta.get("redirect_reasons", [])
        if redirect_urls:
            # redirect_urls is the chain of original URLs before the final one.
            # The original requested URL is redirect_urls[0].
            # response.url is the final destination.
            # For the PageItem we record the original URL and where it ended up.
            original_url = redirect_urls[0]
            url_for_record = original_url
            redirect_url = response.url  # final destination

            # Yield separate PageItems for each redirect hop in the chain
            # so the UI shows 301/302/etc. entries (Screaming Frog parity)
            chain = list(redirect_urls) + [response.url]
            for i in range(len(chain) - 1):
                hop_url = chain[i]
                hop_dest = chain[i + 1]
                hop_status = redirect_reasons[i] if i < len(redirect_reasons) else 301
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
                    content_type=content_type,
                    content_length=0,
                    status_code=hop_status,
                    status_group=compute_status_group(hop_status),
                    response_time_ms=0,
                    is_html=False,
                    resource_type="redirect",
                    redirect_url=hop_dest,
                    body_hash=None,
                    job_id=self.job_id,
                    url_length=len(hop_url),
                    folder_depth=compute_folder_depth(hop_url),
                    word_count=None,
                    text_ratio=None,
                    redirect_type=hop_status,
                    status_text=http_status_text(hop_status),
                    last_modified=None,
                    http_version=None,
                    transfer_size=0,
                    indexability_status=f"Redirect ({hop_status})",
                )
        else:
            url_for_record = url

        url_hash = compute_url_hash(url_for_record)

        # -- 3xx directo (middlewares de redirección DESACTIVADOS en
        # settings): registrar el salto y encolar el destino. Con el
        # middleware activo, un bucle (A↔B) agotaba los 20 saltos y se
        # esfumaba SIN fila ni issue, y una redirección hacia una URL ya
        # vista caía en el dupefilter perdiendo el salto (cazado con el
        # sitio hostil). El dupefilter corta los bucles de forma natural
        # porque cada hop es una request propia.
        if 300 <= status_code < 400 and not redirect_urls:
            location = response.headers.get(b"Location", b"").decode(
                "utf-8", errors="ignore").strip()
            redirect_target = urljoin(url_for_record, location) if location else None
            yield PageItem(
                url=url_for_record,
                url_hash=url_hash,
                host=parsed.hostname or "",
                path=parsed.path or "/",
                scheme=parsed.scheme or "https",
                is_internal=internal,
                crawl_depth=depth,
                content_type=content_type,
                content_length=content_length,
                status_code=status_code,
                status_group=compute_status_group(status_code),
                response_time_ms=response_time_ms,
                is_html=False,
                resource_type="redirect",
                redirect_url=redirect_target,
                body_hash=None,
                job_id=self.job_id,
                url_length=len(url_for_record),
                folder_depth=compute_folder_depth(url_for_record),
                word_count=None,
                text_ratio=None,
                redirect_type=status_code,
                status_text=http_status_text(status_code),
                last_modified=None,
                http_version=None,
                transfer_size=0,
                indexability_status=f"Redirect ({status_code})",
            )
            follow_ok = redirect_target and (
                self._is_internal(redirect_target) or self.follow_external
            ) and self._should_follow(redirect_target)
            if follow_ok and not (
                self._trap_detector is not None
                and not self._trap_detector.allow(redirect_target)
            ):
                follow_meta: dict[str, Any] = {"depth": depth}  # un hop no gasta profundidad BFS
                if self.render_js and _url_likely_html(redirect_target):
                    follow_meta.update(self._playwright_meta())
                yield scrapy.Request(
                    url=redirect_target,
                    callback=self.parse,
                    errback=self.handle_error,
                    meta=follow_meta,
                )
            return

        # Body hash for duplicate content detection
        body_hash = None
        if is_html and status_code < 400 and hasattr(response, "body"):
            body_hash = hashlib.sha256(response.body).hexdigest()

        # -- Screaming Frog extended fields --------------------------------
        # Redirect type: the HTTP status code of the first redirect hop
        redirect_type_val = None
        if redirect_urls and redirect_reasons:
            redirect_type_val = redirect_reasons[0]

        last_modified_val = (
            response.headers.get(b"Last-Modified", b"").decode("utf-8", errors="ignore") or None
        )
        status_text_val = http_status_text(status_code)

        # HTTP version (Scrapy does not reliably expose this)
        http_version_val = getattr(response, "protocol", None)

        # HTML-specific fields computed before PageItem yield so that all
        # Screaming Frog parity fields can be included in the single yield.
        word_count_val = None
        text_ratio_val = None
        indexability_status_val = None
        meta = None
        x_robots = None
        canonical_header = None

        # Only extract HTML content from successful responses (2xx)
        is_success = 200 <= status_code < 400

        if is_html and is_success:
            selector = response.selector

            # Extract meta first so we can compute indexability
            meta = extract_meta(selector)

            # X-Robots-Tag header
            x_robots = (
                response.headers.get(b"X-Robots-Tag", b"").decode("utf-8", errors="ignore")
                or None
            )

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

            # Indexability
            is_indexable, reason = compute_indexability_status(
                status_code,
                meta.get("meta_robots"),
                x_robots,
                meta.get("canonical_href"),
                url_for_record,
            )
            indexability_status_val = "Indexable" if is_indexable else reason
        elif not is_success:
            # Non-2xx: mark indexability accordingly
            if 300 <= status_code < 400:
                indexability_status_val = f"Redirect ({status_code})"
            elif 400 <= status_code < 500:
                indexability_status_val = f"Client Error ({status_code})"
            else:
                indexability_status_val = f"Server Error ({status_code})"

        # -- PageItem for the final destination (always yielded) -----------
        # For redirected URLs, this records the FINAL destination with its
        # actual status code (usually 200).  The redirect hops were already
        # yielded above.
        final_url = response.url if redirect_urls else url_for_record
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
            redirect_url=None,  # This is the final destination
            body_hash=body_hash,
            job_id=self.job_id,
            # Screaming Frog parity fields
            url_length=len(final_url),
            folder_depth=compute_folder_depth(final_url),
            word_count=word_count_val,
            text_ratio=text_ratio_val,
            redirect_type=None,
            status_text=status_text_val,
            last_modified=last_modified_val,
            http_version=http_version_val,
            transfer_size=len(response.body),
            indexability_status=indexability_status_val,
            blocked_by_robots=response.meta.get("blocked_by_robots"),
            # T4: only evaluated on Playwright responses; None otherwise
            js_redirect_url=(
                self._detect_js_redirect(response)
                if response.request.meta.get("playwright")
                else None
            ),
        )

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
            )

        # Links (extract_links already returns enhanced SF fields)
        links = extract_links(selector, response.url, self.allowed_hosts)
        # El destino de un meta-refresh es una redirección: se sigue como
        # un enlace más (con los middlewares de meta-refresh desactivados
        # no se seguía y su página destino quedaba sin descubrir).
        mr = extract_meta_refresh(selector)
        if mr:
            mr_target = urljoin(response.url, mr)
            if not any(l["url"] == mr_target for l in links):
                links.append({
                    "url": mr_target, "anchor_text": None, "rel": None,
                    "is_internal": self._is_internal(mr_target),
                    "link_position": "content", "follow": True,
                    "link_type": "meta_refresh", "target": None,
                    "dom_ancestor": None, "dom_container": None,
                })
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
                # T17.5.b: DOM context (edge classifier prerequisite)
                dom_ancestor=link.get("dom_ancestor"),
                dom_container=link.get("dom_container"),
            )

        # Hreflang
        if self._extraction.get("extract_hreflang", True):
            for hreflang in extract_hreflang(selector):
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
        for resource in extract_resources(selector, response.url):
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

        # -- ContentItem (main page text + markdown + raw HTML) ------------
        store_raw = self._extraction.get("store_raw_html", False)
        if self._extraction.get("extract_page_content", True) or store_raw:
            main_content = None
            content_md = None
            if self._extraction.get("extract_page_content", True):
                main_content = extract_main_content(selector, word_count=word_count_val)
                if main_content:
                    content_md = extract_main_content_markdown(selector, word_count=word_count_val)
            # El HTML tal y como lo vio el crawler: con render_js activo es
            # el DOM renderizado (el lado crudo lo cubre GEO/T15). Se guarda
            # aunque el extractor de contenido no saque nada (páginas
            # boilerplate-only), que es justo donde más se necesita.
            raw_html = response.text if store_raw else None
            if main_content or raw_html:
                yield ContentItem(
                    url_hash=final_hash,
                    job_id=self.job_id,
                    content_text=main_content,
                    content_length=len(main_content) if main_content else None,
                    content_markdown=content_md,
                    raw_html=raw_html,
                )

        # -- T15: GEO — fetch the RAW (no-JS) side of rendered pages ------
        # Only for geo_analysis jobs (which require render_js). One plain
        # extra request per HTML page, low priority so the pipeline has
        # committed the rendered row before the raw callback updates it.
        if (
            self.job_config.get("geo_analysis")
            and response.request.meta.get("playwright")
        ):
            yield scrapy.Request(
                url=response.url,
                callback=self._handle_geo_raw,
                errback=lambda f: None,
                meta={"depth": depth, "geo_raw": True},
                dont_filter=True,
                priority=-10,
            )

        # -- Follow links (BFS) -----------------------------------------
        if depth < self.max_depth:
            for link in links:
                link_internal = self._is_internal(link["url"]) if self._crawl_subdomains else link["is_internal"]
                should_follow = link_internal or self.follow_external
                if should_follow and (link.get("follow", True) or self._follow_nofollow):
                    if self._should_follow(link["url"]):
                        # Resume: skip URLs already crawled in a previous run.
                        if self._already_crawled_hashes and \
                                compute_url_hash(link["url"]) in self._already_crawled_hashes:
                            continue
                        # T13: crawl-trap gate (no-op unless enabled)
                        if self._trap_detector is not None and \
                                not self._trap_detector.allow(link["url"]):
                            continue
                        follow_meta: dict[str, Any] = {"depth": depth + 1}
                        if self.render_js and _url_likely_html(link["url"]):
                            follow_meta.update(self._playwright_meta())
                        yield scrapy.Request(
                            url=link["url"],
                            callback=self.parse,
                            errback=self.handle_error,
                            meta=follow_meta,
                        )

    # ------------------------------------------------------------------
    # GEO raw fetch (T15)
    # ------------------------------------------------------------------
    def _handle_geo_raw(self, response):
        """Persist the raw (pre-JS) word count and JSON-LD types of a page
        already crawled with rendering. The analyzer derives the ratio and
        the issues; a page whose raw side never lands is simply not
        evaluated (never guessed).
        """
        from shared.database import SessionLocal
        from shared.models import Url

        try:
            raw_words = extract_word_count(response.selector)
        except Exception:
            raw_words = 0
        raw_types: list[str] = []
        try:
            for item in extract_structured_data(response.text, response.url):
                if item.get("format") == "jsonld" and item.get("schema_type"):
                    raw_types.append(item["schema_type"])
        except Exception:
            pass

        url_hash = compute_url_hash(response.url)
        session = SessionLocal()
        try:
            updated = (
                session.query(Url)
                .filter(Url.job_id == self.job_id, Url.url_hash == url_hash)
                .update({
                    "raw_word_count": raw_words,
                    "raw_schema_types": sorted(set(raw_types)),
                })
            )
            session.commit()
            if not updated:
                logger.debug("GEO raw fetch: no row yet for %s", response.url)
        except Exception:
            session.rollback()
            logger.exception("Failed to persist GEO raw data")
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Soft-404 probe (T6)
    # ------------------------------------------------------------------
    def _handle_soft404_probe(self, response):
        """Persist the host's error-template signature into job.config.

        A well-behaved host answers 404: we keep the template's body hash
        and a text sample so the analyzer can spot 200 pages serving the
        same template. A host answering 200 here serves soft 404s across
        the board — recorded as such, only heuristic (c) applies then.
        """
        import hashlib as _hashlib

        host = urlparse(response.url).hostname or "?"
        try:
            sample = extract_visible_text(response.selector)[:1000]
        except Exception:
            sample = ""
        signature = {
            "status": response.status,
            "body_hash": _hashlib.sha256(response.body).hexdigest(),
            "sample_text": sample,
        }

        from shared.database import SessionLocal
        from shared.models import Job

        session = SessionLocal()
        try:
            job = session.query(Job).filter(Job.id == self.job_id).one_or_none()
            if job is not None:
                config = dict(job.config or {})
                sigs = dict(config.get("_soft404_signature") or {})
                sigs[host] = signature
                config["_soft404_signature"] = sigs
                job.config = config
                session.commit()
                logger.info(
                    "Soft-404 probe for %s: status %s", host, response.status
                )
        except Exception:
            session.rollback()
            logger.exception("Failed to persist soft-404 signature")
        finally:
            session.close()

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

        logger.debug("Request failed [%s]: %s", status_group, url)

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
        """Push crawled count to Redis periodically."""
        if self._redis is None:
            return
        if self._crawled_count % self._redis_update_interval != 0:
            return
        try:
            self._redis.set(
                f"job:{self.job_id}:crawled_count", self._crawled_count
            )
        except Exception as exc:
            logger.debug("Redis progress update failed: %s", exc)

    def _should_cancel(self) -> bool:
        """Check whether a cancel signal has been set in Redis."""
        if self._redis is None:
            return False
        try:
            val = self._redis.get(f"job:{self.job_id}:cancel")
            return val is not None and str(val).lower() in ("1", "true", "yes")
        except Exception:
            return False
