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
from urllib.parse import urlparse

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

    // ---- Structural non-content elements ----
    ['form', 'nav', 'aside', 'footer', 'header'].forEach(tag => {
        r(tag);
    });

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
            ],
        }

    def start_requests(self) -> Generator[Request, None, None]:
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

        # -- ContentItem (main page text + markdown) -----------------------
        if self._extraction.get("extract_page_content", True):
            main_content = extract_main_content(selector, word_count=word_count_val)
            if main_content:
                content_md = extract_main_content_markdown(selector, word_count=word_count_val)
                yield ContentItem(
                    url_hash=final_hash,
                    job_id=self.job_id,
                    content_text=main_content,
                    content_length=len(main_content),
                    content_markdown=content_md,
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
