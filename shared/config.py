import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://crawler:crawler@localhost:5432/crawler_db",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Job defaults
DEFAULT_MAX_DEPTH = 3
DEFAULT_MAX_URLS = 50000
DEFAULT_CONCURRENT_REQUESTS = 32
DEFAULT_CONCURRENT_REQUESTS_PER_DOMAIN = 8
DEFAULT_USER_AGENT = "SEOCrawler/1.0 (+https://github.com/seo-crawler)"

# SEO thresholds
TITLE_MIN_LEN = 10
TITLE_MAX_LEN = 60
DESCRIPTION_MIN_LEN = 50
DESCRIPTION_MAX_LEN = 160
