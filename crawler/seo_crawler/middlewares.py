"""
Custom Scrapy downloader middlewares for the SEO crawler.

* **UserAgentMiddleware** -- rotates User-Agent strings.
* **ProxyMiddleware**     -- optional proxy rotation.
* **JobConfigMiddleware** -- attaches job config to every request's meta.
"""

from __future__ import annotations

import itertools
import logging
from typing import TYPE_CHECKING

from scrapy import Request, signals
from scrapy.http import Response

if TYPE_CHECKING:
    from scrapy.crawler import Crawler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default UA pool
# ---------------------------------------------------------------------------
DEFAULT_USER_AGENTS = [
    "SEOCrawler/1.0 (+https://github.com/seo-crawler)",
    "Mozilla/5.0 (compatible; SEOCrawler/1.0)",
]


class UserAgentMiddleware:
    """
    Rotate through a list of User-Agent strings on each request.

    Priority order:
    1. Per-request ``request.meta["user_agent"]``
    2. Spider-level ``spider.custom_user_agent`` (set from job config)
    3. Round-robin from the pool
    """

    def __init__(self, user_agents: list[str]):
        self._cycle = itertools.cycle(user_agents)

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        ua_setting = crawler.settings.get("USER_AGENT", "")
        pool = [ua_setting] if ua_setting else DEFAULT_USER_AGENTS
        middleware = cls(pool)
        return middleware

    def process_request(self, request: Request, spider):
        # Per-request override
        if "user_agent" in request.meta:
            request.headers[b"User-Agent"] = request.meta["user_agent"]
            return None

        # Spider-level override (set by job config)
        custom_ua = getattr(spider, "custom_user_agent", None)
        if custom_ua:
            request.headers[b"User-Agent"] = custom_ua
            return None

        # Round-robin
        request.headers[b"User-Agent"] = next(self._cycle)
        return None


class ProxyMiddleware:
    """
    Optionally route requests through a rotating proxy list.

    Set ``PROXY_LIST`` in settings as a comma-separated string of proxy
    URLs (e.g. ``http://proxy1:8080,http://proxy2:8080``).

    If the list is empty or not provided the middleware is a no-op.
    """

    def __init__(self, proxies: list[str]):
        self._proxies = proxies
        self._cycle = itertools.cycle(proxies) if proxies else None

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        raw = crawler.settings.get("PROXY_LIST", "")
        proxies = [p.strip() for p in raw.split(",") if p.strip()]
        if proxies:
            logger.info("ProxyMiddleware loaded with %d proxies", len(proxies))
        return cls(proxies)

    def process_request(self, request: Request, spider):
        if self._cycle is None:
            return None

        # Allow per-request proxy override
        if "proxy" not in request.meta:
            request.meta["proxy"] = next(self._cycle)
        return None


class JobConfigMiddleware:
    """
    Attach the current job's configuration dict to every request so
    that the spider and other middlewares can access it via
    ``request.meta["job_config"]``.

    The spider is expected to expose ``self.job_config`` after loading
    the job from the database.
    """

    def process_request(self, request: Request, spider):
        job_config = getattr(spider, "job_config", None)
        if job_config and "job_config" not in request.meta:
            request.meta["job_config"] = job_config
        return None


class HttpConfigMiddleware:
    """
    Apply per-job HTTP configuration (custom headers, cookies, auth)
    to every outgoing request.

    Reads ``spider._http_config`` which is populated from the job's
    ``http`` configuration block.
    """

    def process_request(self, request: Request, spider):
        http_config = getattr(spider, "_http_config", None)
        if not http_config:
            return None

        # Custom headers
        for key, value in http_config.get("custom_headers", {}).items():
            request.headers[key.encode()] = value.encode()

        # Accept-Language
        accept_lang = http_config.get("accept_language", "")
        if accept_lang:
            request.headers[b"Accept-Language"] = accept_lang.encode()

        # Cookies
        for name, val in http_config.get("cookies", {}).items():
            request.cookies[name] = val

        # Basic Auth
        basic_user = http_config.get("basic_auth_user", "")
        if basic_user:
            import base64

            password = http_config.get("basic_auth_password", "")
            cred = base64.b64encode(f"{basic_user}:{password}".encode()).decode()
            request.headers[b"Authorization"] = f"Basic {cred}".encode()

        return None
