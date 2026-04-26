"""
PostgreSQL persistence pipeline.

Items are accumulated in memory and flushed to the database in batches
for performance.  On ``close_spider`` any remaining items are flushed.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from scrapy import Spider

from seo_crawler.extractors import compute_url_hash
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


class PostgresPipeline:
    """
    Batched persistence pipeline for all SEO items.

    * ``PageItem`` and ``HtmlMetaItem`` are upserted individually (they
      carry the primary dedup key ``job_id + url_hash``).
    * ``SecurityItem`` is upserted individually (linked to Url by url_hash).
    * ``LinkItem``, ``HeadingItem``, ``HreflangItem``, ``StructuredDataItem``,
      and ``ResourceItem`` are accumulated and bulk-inserted every
      ``PIPELINE_BATCH_SIZE`` items.
    """

    def __init__(self, batch_size: int = 200):
        self.batch_size = batch_size
        self.session = None
        self._buffer: list[tuple[type, dict]] = []
        self._url_id_cache: dict[str, int] = {}  # url_hash -> Url.id
        self._pages_committed: int = 0
        self._last_job_update: float = 0.0

    @classmethod
    def from_crawler(cls, crawler):
        batch_size = crawler.settings.getint("PIPELINE_BATCH_SIZE", 200)
        return cls(batch_size=batch_size)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def open_spider(self, spider: Spider):
        from shared.database import SessionLocal

        self.session = SessionLocal()
        logger.info("PostgresPipeline: database session opened")

    def close_spider(self, spider: Spider):
        self._flush(spider)
        self._update_job_counter(spider, force=True)
        if self.session:
            self.session.close()
            logger.info(
                "PostgresPipeline: session closed, %d pages committed",
                self._pages_committed,
            )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def process_item(self, item, spider: Spider):
        if isinstance(item, PageItem):
            self._handle_page(item, spider)
        elif isinstance(item, HtmlMetaItem):
            self._handle_html_meta(item, spider)
        elif isinstance(item, ContentItem):
            self._handle_content(item, spider)
        elif isinstance(item, SecurityItem):
            self._handle_security(item, spider)
        elif isinstance(item, (LinkItem, HeadingItem, HreflangItem,
                               StructuredDataItem, ResourceItem)):
            self._buffer.append((type(item), dict(item)))
            if len(self._buffer) >= self.batch_size:
                self._flush(spider)
        return item

    # ------------------------------------------------------------------
    # PageItem handling (upsert)
    # ------------------------------------------------------------------
    def _handle_page(self, item: PageItem, spider: Spider):
        from shared.models import Url

        data = dict(item)
        url_hash = data["url_hash"]
        job_id = data["job_id"]

        try:
            existing = (
                self.session.query(Url)
                .filter(Url.job_id == job_id, Url.url_hash == url_hash)
                .first()
            )
            if existing:
                # Update mutable fields
                for field in (
                    "status_code", "status_group", "content_type",
                    "content_length", "response_time_ms", "is_html",
                    "resource_type", "redirect_url", "body_hash",
                    "crawl_depth",
                    # New Screaming Frog fields
                    "url_length", "folder_depth", "word_count",
                    "text_ratio", "redirect_type", "status_text",
                    "last_modified", "http_version", "transfer_size",
                    "indexability_status",
                ):
                    setattr(existing, field, data.get(field))
                existing.last_crawled_at = datetime.now(timezone.utc)
                self.session.flush()
                url_id = existing.id
            else:
                url_obj = Url(
                    job_id=job_id,
                    url=data["url"],
                    url_hash=url_hash,
                    host=data.get("host"),
                    path=data.get("path"),
                    scheme=data.get("scheme"),
                    is_internal=data.get("is_internal", True),
                    crawl_depth=data.get("crawl_depth"),
                    content_type=data.get("content_type"),
                    content_length=data.get("content_length"),
                    status_code=data.get("status_code"),
                    status_group=data.get("status_group"),
                    response_time_ms=data.get("response_time_ms"),
                    is_html=data.get("is_html", False),
                    resource_type=data.get("resource_type"),
                    redirect_url=data.get("redirect_url"),
                    body_hash=data.get("body_hash"),
                    # New Screaming Frog fields
                    url_length=data.get("url_length"),
                    folder_depth=data.get("folder_depth"),
                    word_count=data.get("word_count"),
                    text_ratio=data.get("text_ratio"),
                    redirect_type=data.get("redirect_type"),
                    status_text=data.get("status_text"),
                    last_modified=data.get("last_modified"),
                    http_version=data.get("http_version"),
                    transfer_size=data.get("transfer_size"),
                    indexability_status=data.get("indexability_status"),
                )
                self.session.add(url_obj)
                self.session.flush()
                url_id = url_obj.id

            self._url_id_cache[url_hash] = url_id
            self._pages_committed += 1
            self.session.commit()
            self._update_job_counter(spider)
        except Exception:
            self.session.rollback()
            logger.exception("Failed to persist PageItem for %s", data.get("url"))

    # ------------------------------------------------------------------
    # HtmlMetaItem handling (upsert linked to Url)
    # ------------------------------------------------------------------
    def _handle_html_meta(self, item: HtmlMetaItem, spider: Spider):
        from shared.models import HtmlMeta

        data = dict(item)
        url_hash = data.pop("url_hash")
        job_id = data.pop("job_id")
        url_id = self._resolve_url_id(url_hash, job_id)
        if url_id is None:
            logger.debug("No url_id for HtmlMetaItem url_hash=%s, skipping", url_hash)
            return

        try:
            existing = self.session.query(HtmlMeta).filter(HtmlMeta.url_id == url_id).first()
            if existing:
                for key, val in data.items():
                    setattr(existing, key, val)
            else:
                meta_obj = HtmlMeta(url_id=url_id, **data)
                self.session.add(meta_obj)
            self.session.commit()
        except Exception:
            self.session.rollback()
            logger.exception("Failed to persist HtmlMetaItem for url_id=%s", url_id)

    # ------------------------------------------------------------------
    # ContentItem handling (upsert linked to Url)
    # ------------------------------------------------------------------
    def _handle_content(self, item: ContentItem, spider: Spider):
        from shared.models import PageContent

        data = dict(item)
        url_hash = data.pop("url_hash")
        job_id = data.pop("job_id")
        url_id = self._resolve_url_id(url_hash, job_id)
        if url_id is None:
            logger.debug("No url_id for ContentItem url_hash=%s, skipping", url_hash)
            return

        try:
            existing = self.session.query(PageContent).filter(PageContent.url_id == url_id).first()
            if existing:
                for key, val in data.items():
                    setattr(existing, key, val)
            else:
                content_obj = PageContent(url_id=url_id, **data)
                self.session.add(content_obj)
            self.session.commit()
        except Exception:
            self.session.rollback()
            logger.exception("Failed to persist ContentItem for url_id=%s", url_id)

    # ------------------------------------------------------------------
    # SecurityItem handling (upsert linked to Url)
    # ------------------------------------------------------------------
    def _handle_security(self, item: SecurityItem, spider: Spider):
        from shared.models import SecurityHeaders

        data = dict(item)
        url_hash = data.pop("url_hash")
        job_id = data.pop("job_id")
        url_id = self._resolve_url_id(url_hash, job_id)
        if url_id is None:
            logger.debug("No url_id for SecurityItem url_hash=%s, skipping", url_hash)
            return

        try:
            existing = (
                self.session.query(SecurityHeaders)
                .filter(SecurityHeaders.url_id == url_id)
                .first()
            )
            if existing:
                for key, val in data.items():
                    setattr(existing, key, val)
            else:
                sec_obj = SecurityHeaders(url_id=url_id, **data)
                self.session.add(sec_obj)
            self.session.commit()
        except Exception:
            self.session.rollback()
            logger.exception("Failed to persist SecurityItem for url_id=%s", url_id)

    # ------------------------------------------------------------------
    # Batch flush for child items
    # ------------------------------------------------------------------
    def _flush(self, spider: Spider):
        if not self._buffer:
            return

        from shared.models import Heading, Hreflang, Link, Resource, StructuredData

        item_type_map = {
            LinkItem: self._make_link,
            HeadingItem: self._make_heading,
            HreflangItem: self._make_hreflang,
            StructuredDataItem: self._make_structured_data,
            ResourceItem: self._make_resource,
        }

        objects: list = []
        for item_cls, data in self._buffer:
            factory = item_type_map.get(item_cls)
            if factory:
                obj = factory(data)
                if obj is not None:
                    objects.append(obj)

        self._buffer.clear()

        if not objects:
            return

        try:
            self.session.bulk_save_objects(objects)
            self.session.commit()
            logger.debug("Flushed %d child items to database", len(objects))
        except Exception:
            self.session.rollback()
            logger.exception("Batch flush failed for %d items", len(objects))

    # ------------------------------------------------------------------
    # Factory helpers (dict -> ORM object)
    # ------------------------------------------------------------------
    def _make_link(self, data: dict):
        from shared.models import Link

        from_url_hash = data.get("from_url_hash")
        job_id = data.get("job_id")
        from_url_id = self._resolve_url_id(from_url_hash, job_id)
        if from_url_id is None:
            return None
        return Link(
            job_id=job_id,
            from_url_id=from_url_id,
            to_url=data.get("to_url"),
            to_url_hash=data.get("to_url_hash"),
            anchor_text=data.get("anchor_text"),
            rel=data.get("rel"),
            is_internal=data.get("is_internal", True),
            link_position=data.get("link_position"),
            # New Screaming Frog fields
            follow=data.get("follow", True),
            target=data.get("target"),
            alt_text=data.get("alt_text"),
            link_type=data.get("link_type", "hyperlink"),
        )

    def _make_heading(self, data: dict):
        from shared.models import Heading

        url_id = self._resolve_url_id(data.get("url_hash"), data.get("job_id"))
        if url_id is None:
            return None
        return Heading(
            url_id=url_id,
            tag=data.get("tag"),
            position=data.get("position", 0),
            text=data.get("text"),
        )

    def _make_hreflang(self, data: dict):
        from shared.models import Hreflang

        url_id = self._resolve_url_id(data.get("url_hash"), data.get("job_id"))
        if url_id is None:
            return None
        return Hreflang(
            url_id=url_id,
            lang=data.get("lang"),
            href=data.get("href"),
        )

    def _make_structured_data(self, data: dict):
        from shared.models import StructuredData

        url_id = self._resolve_url_id(data.get("url_hash"), data.get("job_id"))
        if url_id is None:
            return None
        return StructuredData(
            url_id=url_id,
            raw=data.get("raw"),
            format=data.get("format"),
            schema_type=data.get("schema_type"),
        )

    def _make_resource(self, data: dict):
        from shared.models import Resource

        url_id = self._resolve_url_id(data.get("url_hash"), data.get("job_id"))
        if url_id is None:
            return None
        return Resource(
            url_id=url_id,
            resource_url=data.get("resource_url"),
            resource_type=data.get("resource_type"),
            alt_text=data.get("alt_text"),
            # New Screaming Frog fields
            width=data.get("width"),
            height=data.get("height"),
            is_mixed_content=data.get("is_mixed_content", False),
        )

    # ------------------------------------------------------------------
    # URL-ID resolution
    # ------------------------------------------------------------------
    def _resolve_url_id(self, url_hash: str | None, job_id: str | None) -> int | None:
        """Look up the Url.id for a given url_hash, using an in-memory cache."""
        if not url_hash or not job_id:
            return None

        cached = self._url_id_cache.get(url_hash)
        if cached is not None:
            return cached

        from shared.models import Url

        row = (
            self.session.query(Url.id)
            .filter(Url.job_id == job_id, Url.url_hash == url_hash)
            .first()
        )
        if row:
            self._url_id_cache[url_hash] = row[0]
            return row[0]
        return None

    # ------------------------------------------------------------------
    # Job counter
    # ------------------------------------------------------------------
    def _update_job_counter(self, spider: Spider, force: bool = False):
        """Update job.total_urls_crawled every 100 pages (or on close)."""
        now = time.monotonic()
        if not force and (now - self._last_job_update) < 5.0:
            return
        self._last_job_update = now

        from shared.models import Job

        try:
            job = self.session.query(Job).filter(Job.id == spider.job_id).first()
            if job:
                job.total_urls_crawled = self._pages_committed
                self.session.commit()
        except Exception:
            self.session.rollback()
            logger.debug("Failed to update job counter")
