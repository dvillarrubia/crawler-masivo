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
    "SEOCrawler/1.0 (+https://github.com/seo-crawler)",
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
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {
    "headless": True,
    "args": [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
    ],
}
# Default context options — real-looking viewport and locale
PLAYWRIGHT_CONTEXTS = {
    "default": {
        "viewport": {"width": 1920, "height": 1080},
        "locale": "es-ES",
        "java_script_enabled": True,
    },
}

# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"
