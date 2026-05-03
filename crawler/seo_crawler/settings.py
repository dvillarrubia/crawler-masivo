"""
Scrapy settings for the SEO crawler.

Optimised for broad crawls with BFS ordering, autothrottle, and
PostgreSQL persistence via a custom pipeline.
"""

import os

BOT_NAME = "seo_crawler"
SPIDER_MODULES = ["seo_crawler.spiders"]
NEWSPIDER_MODULE = "seo_crawler.spiders"

# ---------------------------------------------------------------------------
# Broad-crawl tuning
# ---------------------------------------------------------------------------
CONCURRENT_REQUESTS = int(os.getenv("CONCURRENT_REQUESTS", "32"))
CONCURRENT_REQUESTS_PER_DOMAIN = int(
    os.getenv("CONCURRENT_REQUESTS_PER_DOMAIN", "8")
)
DOWNLOAD_TIMEOUT = 30
RETRY_TIMES = 2
RETRY_HTTP_CODES = [502, 503, 504, 408, 429]
DNS_TIMEOUT = 10

# ---------------------------------------------------------------------------
# Allow ALL HTTP status codes to reach the spider (Screaming Frog parity)
# ---------------------------------------------------------------------------
HTTPERROR_ALLOW_ALL = True

# ---------------------------------------------------------------------------
# Politeness
# ---------------------------------------------------------------------------
ROBOTSTXT_OBEY = True
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1
AUTOTHROTTLE_MAX_DELAY = 10
AUTOTHROTTLE_TARGET_CONCURRENCY = 8.0
DOWNLOAD_DELAY = 0  # autothrottle takes over

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

# ---------------------------------------------------------------------------
# BFS scheduling
# ---------------------------------------------------------------------------
DEPTH_PRIORITY = 1
SCHEDULER_DISK_QUEUE = "scrapy.squeues.PickleFifoDiskQueue"
SCHEDULER_MEMORY_QUEUE = "scrapy.squeues.FifoMemoryQueue"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------
ITEM_PIPELINES = {
    "seo_crawler.pipelines.PostgresPipeline": 300,
}

# ---------------------------------------------------------------------------
# Downloader middlewares
# ---------------------------------------------------------------------------
DOWNLOADER_MIDDLEWARES = {
    # Disable the built-in UA middleware so ours takes precedence
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
    "seo_crawler.middlewares.UserAgentMiddleware": 400,
    "seo_crawler.middlewares.ProxyMiddleware": 410,
    "seo_crawler.middlewares.JobConfigMiddleware": 100,
    "seo_crawler.middlewares.HttpConfigMiddleware": 420,
}

# ---------------------------------------------------------------------------
# Database / Redis (imported from environment, fallback to shared.config)
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://crawler:crawler@localhost:5432/crawler_db",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ---------------------------------------------------------------------------
# Proxy (optional)
# ---------------------------------------------------------------------------
PROXY_LIST = os.getenv("PROXY_LIST", "")  # comma-separated proxy URLs

# ---------------------------------------------------------------------------
# Pipeline batching
# ---------------------------------------------------------------------------
PIPELINE_BATCH_SIZE = int(os.getenv("PIPELINE_BATCH_SIZE", "200"))

# ---------------------------------------------------------------------------
# Playwright (JS rendering — activated per-request via meta["playwright"])
# ---------------------------------------------------------------------------
# Composite handler: curl_cffi (browser TLS fingerprint) for normal requests,
# Playwright for JS-rendered pages.  WAF bypass via JA3/JA4 impersonation.
DOWNLOAD_HANDLERS = {
    "http": "seo_crawler.handlers.CompositeDownloadHandler",
    "https": "seo_crawler.handlers.CompositeDownloadHandler",
}
# Browser TLS profile for non-Playwright requests (curl_cffi impersonation)
IMPERSONATE = os.getenv("IMPERSONATE", "chrome124")
PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": True,
    "args": [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--mute-audio",
        "--no-first-run",
    ],
}
# Limit concurrent browser pages to avoid memory exhaustion
PLAYWRIGHT_MAX_PAGES_PER_CONTEXT = int(
    os.getenv("PLAYWRIGHT_MAX_PAGES", "8")
)
# Safety cap on total browser contexts (default + custom + margin)
PLAYWRIGHT_MAX_CONTEXTS = int(os.getenv("PLAYWRIGHT_MAX_CONTEXTS", "3"))
# Named browser contexts — reused across requests to avoid creating a new
# context per page.  The "custom" context carries the configured user-agent
# and is referenced by the spider's _playwright_meta().
PLAYWRIGHT_CONTEXTS = {
    "default": {
        "viewport": {"width": 1280, "height": 720},
        "locale": "es-ES",
        "java_script_enabled": True,
    },
    "custom": {
        "user_agent": USER_AGENT,
        "viewport": {"width": 1280, "height": 720},
        "locale": "es-ES",
        "java_script_enabled": True,
    },
}
# Abort requests for resources we don't need for SEO analysis.
# This dramatically speeds up JS rendering by skipping images, fonts,
# media, and tracking pixels — only HTML/CSS/JS reach the browser.
PLAYWRIGHT_ABORT_REQUEST = lambda req: req.resource_type in (
    "image", "media", "font", "texttrack", "eventsource",
    "websocket", "manifest", "other",
)
PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT = int(
    os.getenv("PLAYWRIGHT_NAV_TIMEOUT", "15000")  # 15s instead of 30s
)

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"
