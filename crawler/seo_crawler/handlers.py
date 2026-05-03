"""
Composite download handler: routes Playwright-tagged requests through
headless Chromium and everything else through curl_cffi with browser
TLS impersonation (JA3/JA4 fingerprint bypass).

Normal HTTP requests get a real Chrome TLS fingerprint + HTTP/2 without
the overhead of launching a browser.
"""

from __future__ import annotations

import logging

from scrapy import Request
from scrapy.crawler import Crawler

logger = logging.getLogger(__name__)


class CompositeDownloadHandler:
    """Playwright for JS pages, curl_cffi impersonate for everything else.

    Compatible with Scrapy 2.15+ (async download_request, no spider arg)
    while delegating to sub-handlers that may still use the old signature.
    """

    lazy = False  # Scrapy 2.15 wants this attribute

    def __init__(self, crawler: Crawler, playwright_handler, impersonate_handler):
        self._crawler = crawler
        self._playwright = playwright_handler
        self._impersonate = impersonate_handler

    @classmethod
    def from_crawler(cls, crawler: Crawler):
        from scrapy_impersonate import ImpersonateDownloadHandler
        from scrapy_playwright.handler import ScrapyPlaywrightDownloadHandler

        pw = ScrapyPlaywrightDownloadHandler.from_crawler(crawler)
        imp = ImpersonateDownloadHandler.from_crawler(crawler)
        logger.info(
            "CompositeDownloadHandler ready: Playwright + curl_cffi (%s)",
            crawler.settings.get("IMPERSONATE", "chrome124"),
        )
        return cls(crawler, pw, imp)

    async def download_request(self, request: Request, spider=None):
        handler = (
            self._playwright
            if request.meta.get("playwright")
            else self._impersonate
        )
        # Sub-handlers may use old signature (request, spider) or new (request).
        # Always pass spider when we have it; fall back to crawler.spider.
        _spider = spider or getattr(self._crawler, "spider", None)
        method = handler.download_request
        try:
            result = method(request, _spider)
        except TypeError:
            # Handler doesn't accept spider arg (new-style)
            result = method(request)
        if hasattr(result, "__await__"):
            return await result
        return result

    async def close(self):
        await self._playwright.close()
        if hasattr(self._impersonate, "close"):
            result = self._impersonate.close()
            if hasattr(result, "__await__"):
                await result
