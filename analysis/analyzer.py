"""
SEO Analysis Engine
~~~~~~~~~~~~~~~~~~~

Post-crawl analysis that inspects every URL collected by a crawl job
and populates the ``issues`` table with actionable SEO findings.

Usage::

    from analysis.analyzer import run_analysis
    run_analysis(job_id="some-uuid-string")
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Sequence
from urllib.parse import urlparse

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.orm import Session

from shared.config import (
    DESCRIPTION_MAX_LEN,
    DESCRIPTION_MIN_LEN,
    TITLE_MAX_LEN,
    TITLE_MIN_LEN,
)
from shared.database import SessionLocal
from shared.models import (
    Heading,
    HtmlMeta,
    Hreflang,
    Issue,
    Link,
    Resource,
    SecurityHeaders,
    StructuredData,
    Url,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BATCH_SIZE = 1000

LOW_WORD_COUNT_THRESHOLD = 200
LOW_TEXT_RATIO_THRESHOLD = 10.0
VERY_LOW_TEXT_RATIO_THRESHOLD = 5.0
URL_MAX_LENGTH = 115
HIGH_OUTLINK_THRESHOLD = 100

# BCP 47 language tag pattern (simplified but covers common cases).
# Matches things like "en", "en-US", "zh-Hant-TW", "x-default".
_LANG_TAG_RE = re.compile(
    r"^(?:x-default|[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{1,8})*)$"
)

# Regex to detect non-ASCII characters in a URL.
_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")

# Regex to detect multiple consecutive slashes in a path (not the scheme://).
_MULTIPLE_SLASHES_RE = re.compile(r"(?<!:)//+")


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------
class SEOAnalyzer:
    """Runs all SEO analysis checks for a single crawl job.

    Parameters
    ----------
    session:
        An active SQLAlchemy ``Session`` bound to the crawler database.
    job_id:
        UUID (as string or ``uuid.UUID``) of the crawl job to analyse.
    """

    def __init__(self, session: Session, job_id: str) -> None:
        self.session = session
        self.job_id = job_id
        self._pending_issues: list[dict[str, Any]] = []

        # Per-job thresholds (fallback to module-level constants)
        from shared.models import Job

        job = session.query(Job).filter(Job.id == job_id).one_or_none()
        t = (job.config or {}).get("analysis_thresholds", {}) if job else {}
        self.title_min_len = t.get("title_min_length", TITLE_MIN_LEN)
        self.title_max_len = t.get("title_max_length", TITLE_MAX_LEN)
        self.desc_min_len = t.get("description_min_length", DESCRIPTION_MIN_LEN)
        self.desc_max_len = t.get("description_max_length", DESCRIPTION_MAX_LEN)
        self.min_word_count = t.get("min_word_count", LOW_WORD_COUNT_THRESHOLD)
        self.max_redirect_chain = t.get("max_redirect_chain_length", 2)
        self.max_outlinks = t.get("max_outlinks", HIGH_OUTLINK_THRESHOLD)

    # -- public interface ---------------------------------------------------

    def run_all(self) -> None:
        """Run every analysis check and persist results to the issues table."""
        logger.info("Starting SEO analysis for job %s", self.job_id)

        self.clear_existing_issues()

        self.analyze_status_codes()
        self.analyze_titles()
        self.analyze_descriptions()
        self.analyze_headings()
        self.analyze_canonicals()
        self.analyze_hreflang()
        self.analyze_structured_data()
        self.analyze_indexability()
        self.analyze_duplicates()
        self.analyze_redirect_chains()
        self.analyze_images()
        self.analyze_security()
        self.analyze_content()
        self.analyze_url_issues()
        self.compute_link_counts()
        self.compute_pagerank()
        self.analyze_links()

        # Flush any remaining buffered issues.
        self._flush_issues()
        self.session.commit()

        logger.info("SEO analysis completed for job %s", self.job_id)

    # -- helpers ------------------------------------------------------------

    def clear_existing_issues(self) -> None:
        """Remove all issues previously generated for this job."""
        self.session.execute(
            delete(Issue).where(Issue.job_id == self.job_id)
        )
        self.session.flush()

    def _add_issue(
        self,
        url_id: int,
        issue_type: str,
        severity: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Buffer an issue for bulk insertion."""
        self._pending_issues.append(
            {
                "job_id": self.job_id,
                "url_id": url_id,
                "issue_type": issue_type,
                "severity": severity,
                "details": details,
                "detected_at": datetime.now(timezone.utc),
            }
        )
        if len(self._pending_issues) >= BATCH_SIZE:
            self._flush_issues()

    def _flush_issues(self) -> None:
        """Perform a bulk insert of all buffered issues."""
        if not self._pending_issues:
            return
        self.session.bulk_insert_mappings(Issue, self._pending_issues)
        self.session.flush()
        logger.debug("Flushed %d issues", len(self._pending_issues))
        self._pending_issues.clear()

    def _iter_urls(
        self,
        *extra_filters,
        columns: Sequence | None = None,
    ):
        """Yield URL rows in batches of ``BATCH_SIZE``.

        Parameters
        ----------
        *extra_filters:
            Additional SQLAlchemy filter expressions applied on top of
            the job_id filter.
        columns:
            If provided, select only these columns (returns ``Row``
            objects instead of full ORM instances).
        """
        base_filter = Url.job_id == self.job_id
        if columns is not None:
            stmt = select(*columns).where(base_filter, *extra_filters)
        else:
            stmt = select(Url).where(base_filter, *extra_filters)

        result = self.session.execute(stmt.yield_per(BATCH_SIZE))
        yield from result

    # ======================================================================
    # Analysis checks
    # ======================================================================

    # -- Status codes -------------------------------------------------------

    def analyze_status_codes(self) -> None:
        """Flag 4xx, 5xx, and connection-level errors."""
        logger.debug("Analyzing status codes ...")

        # 4xx errors
        rows = self.session.execute(
            select(Url.id, Url.status_code).where(
                Url.job_id == self.job_id,
                Url.status_group == "4xx",
            )
        ).all()
        for url_id, status_code in rows:
            self._add_issue(
                url_id,
                "4xx_error",
                "error",
                {"status_code": status_code},
            )

        # 5xx errors
        rows = self.session.execute(
            select(Url.id, Url.status_code).where(
                Url.job_id == self.job_id,
                Url.status_group == "5xx",
            )
        ).all()
        for url_id, status_code in rows:
            self._add_issue(
                url_id,
                "5xx_error",
                "error",
                {"status_code": status_code},
            )

        # Connection-level errors (timeouts, DNS failures, connection refused, etc.)
        rows = self.session.execute(
            select(Url.id, Url.status_group).where(
                Url.job_id == self.job_id,
                Url.status_group.in_(["timeout", "dns_error", "conn_refused", "error", "unknown"]),
            )
        ).all()
        for url_id, status_group in rows:
            self._add_issue(
                url_id,
                "connection_error",
                "error",
                {"error_type": status_group},
            )

        self._flush_issues()

    # -- Titles -------------------------------------------------------------

    def analyze_titles(self) -> None:
        """Check for missing, short, long, and duplicate page titles."""
        logger.debug("Analyzing titles ...")

        # Join Url with HtmlMeta for all HTML pages in the job.
        stmt = (
            select(Url.id, HtmlMeta.title, HtmlMeta.title_len)
            .join(HtmlMeta, HtmlMeta.url_id == Url.id)
            .where(Url.job_id == self.job_id, Url.is_html.is_(True))
        )
        rows = self.session.execute(stmt).all()

        # Track titles for duplicate detection.
        title_to_url_ids: dict[str, list[int]] = defaultdict(list)

        for url_id, title, title_len in rows:
            if not title or not title.strip():
                self._add_issue(url_id, "title_missing", "warning")
                continue

            clean_title = title.strip()
            effective_len = title_len if title_len is not None else len(clean_title)

            if effective_len < self.title_min_len:
                self._add_issue(
                    url_id,
                    "title_too_short",
                    "warning",
                    {"length": effective_len, "min": self.title_min_len},
                )
            elif effective_len > self.title_max_len:
                self._add_issue(
                    url_id,
                    "title_too_long",
                    "warning",
                    {"length": effective_len, "max": self.title_max_len},
                )

            title_to_url_ids[clean_title.lower()].append(url_id)

        # Duplicate titles: only flag groups with 2+ pages sharing the same title.
        for title_text, url_ids in title_to_url_ids.items():
            if len(url_ids) < 2:
                continue
            for uid in url_ids:
                other_ids = [x for x in url_ids if x != uid]
                self._add_issue(
                    uid,
                    "title_duplicate",
                    "warning",
                    {"duplicate_urls": other_ids},
                )

        self._flush_issues()

    # -- Descriptions -------------------------------------------------------

    def analyze_descriptions(self) -> None:
        """Check for missing, short, long, and duplicate meta descriptions."""
        logger.debug("Analyzing meta descriptions ...")

        stmt = (
            select(Url.id, HtmlMeta.meta_description, HtmlMeta.meta_description_len)
            .join(HtmlMeta, HtmlMeta.url_id == Url.id)
            .where(Url.job_id == self.job_id, Url.is_html.is_(True))
        )
        rows = self.session.execute(stmt).all()

        desc_to_url_ids: dict[str, list[int]] = defaultdict(list)

        for url_id, description, desc_len in rows:
            if not description or not description.strip():
                self._add_issue(url_id, "description_missing", "warning")
                continue

            clean_desc = description.strip()
            effective_len = desc_len if desc_len is not None else len(clean_desc)

            if effective_len < self.desc_min_len:
                self._add_issue(
                    url_id,
                    "description_too_short",
                    "warning",
                    {"length": effective_len, "min": self.desc_min_len},
                )
            elif effective_len > self.desc_max_len:
                self._add_issue(
                    url_id,
                    "description_too_long",
                    "warning",
                    {"length": effective_len, "max": self.desc_max_len},
                )

            desc_to_url_ids[clean_desc.lower()].append(url_id)

        for desc_text, url_ids in desc_to_url_ids.items():
            if len(url_ids) < 2:
                continue
            for uid in url_ids:
                other_ids = [x for x in url_ids if x != uid]
                self._add_issue(
                    uid,
                    "description_duplicate",
                    "warning",
                    {"duplicate_urls": other_ids},
                )

        self._flush_issues()

    # -- Headings -----------------------------------------------------------

    def analyze_headings(self) -> None:
        """Check H1 presence, multiplicity, and duplication."""
        logger.debug("Analyzing headings ...")

        # Get all H1 headings for HTML pages in this job.
        stmt = (
            select(Url.id, Heading.text)
            .join(Heading, Heading.url_id == Url.id)
            .where(
                Url.job_id == self.job_id,
                Url.is_html.is_(True),
                Heading.tag == "h1",
            )
            .order_by(Url.id)
        )
        rows = self.session.execute(stmt).all()

        # Group H1s per URL.
        h1_by_url: dict[int, list[str]] = defaultdict(list)
        for url_id, text in rows:
            h1_by_url[url_id].append(text or "")

        # Get the full set of HTML URL ids so we can detect missing H1s.
        html_url_ids_stmt = select(Url.id).where(
            Url.job_id == self.job_id, Url.is_html.is_(True)
        )
        all_html_url_ids = {
            row[0] for row in self.session.execute(html_url_ids_stmt).all()
        }

        # Missing H1
        for url_id in all_html_url_ids:
            if url_id not in h1_by_url:
                self._add_issue(url_id, "h1_missing", "warning")

        # Multiple H1s
        for url_id, h1_texts in h1_by_url.items():
            if len(h1_texts) > 1:
                self._add_issue(
                    url_id,
                    "h1_multiple",
                    "warning",
                    {"count": len(h1_texts)},
                )

        # Duplicate H1 text across different URLs.
        h1_text_to_url_ids: dict[str, list[int]] = defaultdict(list)
        for url_id, h1_texts in h1_by_url.items():
            for text in h1_texts:
                if text.strip():
                    h1_text_to_url_ids[text.strip().lower()].append(url_id)

        for h1_text, url_ids in h1_text_to_url_ids.items():
            # Deduplicate URL ids (a URL with two identical H1s should not
            # appear twice in the duplicate group).
            unique_ids = list(dict.fromkeys(url_ids))
            if len(unique_ids) < 2:
                continue
            for uid in unique_ids:
                other_ids = [x for x in unique_ids if x != uid]
                self._add_issue(
                    uid,
                    "h1_duplicate",
                    "info",
                    {"duplicate_urls": other_ids},
                )

        self._flush_issues()

    # -- Canonicals ---------------------------------------------------------

    def analyze_canonicals(self) -> None:
        """Validate canonical link declarations."""
        logger.debug("Analyzing canonicals ...")

        # Build a lookup of url_hash -> status_code for the job so we can
        # resolve canonical targets efficiently.
        url_lookup_stmt = select(Url.url, Url.status_code, Url.host).where(
            Url.job_id == self.job_id,
        )
        url_status: dict[str, int | None] = {}
        url_host: dict[str, str | None] = {}
        for row_url, sc, host in self.session.execute(url_lookup_stmt).all():
            url_status[row_url] = sc
            url_host[row_url] = host

        # Iterate HTML pages with their canonical information.
        stmt = (
            select(Url.id, Url.url, Url.host, HtmlMeta.canonical_href)
            .join(HtmlMeta, HtmlMeta.url_id == Url.id)
            .where(Url.job_id == self.job_id, Url.is_html.is_(True))
        )
        rows = self.session.execute(stmt).all()

        for url_id, page_url, page_host, canonical_href in rows:
            if not canonical_href or not canonical_href.strip():
                self._add_issue(url_id, "canonical_missing", "info")
                continue

            canonical = canonical_href.strip()

            # Self-referencing canonical is fine -- skip.
            if canonical == page_url:
                continue

            # Cross-domain canonical.
            try:
                canonical_parsed = urlparse(canonical)
                canonical_host = canonical_parsed.hostname or ""
            except Exception:
                canonical_host = ""

            if canonical_host and page_host and canonical_host != page_host:
                self._add_issue(
                    url_id,
                    "canonical_cross_domain",
                    "info",
                    {"canonical": canonical, "canonical_host": canonical_host},
                )

            # Canonical pointing to a non-200 URL (only if we crawled it).
            target_status = url_status.get(canonical)
            if target_status is not None and target_status != 200:
                self._add_issue(
                    url_id,
                    "canonical_broken",
                    "error",
                    {"canonical": canonical, "target_status": target_status},
                )

        self._flush_issues()

    # -- Hreflang -----------------------------------------------------------

    def analyze_hreflang(self) -> None:
        """Validate hreflang annotations."""
        logger.debug("Analyzing hreflang ...")

        # Preload URL statuses within the job for target validation.
        url_status: dict[str, int | None] = {}
        for row_url, sc in self.session.execute(
            select(Url.url, Url.status_code).where(Url.job_id == self.job_id)
        ).all():
            url_status[row_url] = sc

        stmt = (
            select(
                Hreflang.id,
                Hreflang.url_id,
                Hreflang.lang,
                Hreflang.href,
                Hreflang.return_tag_ok,
                Hreflang.lang_valid,
            )
            .join(Url, Url.id == Hreflang.url_id)
            .where(Url.job_id == self.job_id)
        )
        rows = self.session.execute(stmt).all()

        for _hreflang_id, url_id, lang, href, return_tag_ok, lang_valid in rows:
            # Missing return tag.
            if return_tag_ok is False:
                self._add_issue(
                    url_id,
                    "hreflang_missing_return",
                    "warning",
                    {"lang": lang, "href": href},
                )

            # Invalid language code. Use the stored flag if available,
            # otherwise fall back to regex validation.
            lang_is_valid = lang_valid if lang_valid is not None else bool(
                _LANG_TAG_RE.match(lang)
            )
            if not lang_is_valid:
                self._add_issue(
                    url_id,
                    "hreflang_invalid_lang",
                    "warning",
                    {"lang": lang},
                )

            # Target URL not returning 200.
            target_status = url_status.get(href)
            if target_status is not None and target_status != 200:
                self._add_issue(
                    url_id,
                    "hreflang_broken_target",
                    "error",
                    {"href": href, "target_status": target_status},
                )

        self._flush_issues()

    # -- Structured Data ----------------------------------------------------

    def analyze_structured_data(self) -> None:
        """Surface structured-data validation errors and warnings."""
        logger.debug("Analyzing structured data ...")

        stmt = (
            select(
                StructuredData.url_id,
                StructuredData.schema_type,
                StructuredData.validation_status,
                StructuredData.validation_issues,
            )
            .join(Url, Url.id == StructuredData.url_id)
            .where(Url.job_id == self.job_id)
        )
        rows = self.session.execute(stmt).all()

        for url_id, schema_type, validation_status, validation_issues in rows:
            if validation_status == "error":
                self._add_issue(
                    url_id,
                    "structured_data_error",
                    "error",
                    {
                        "schema_type": schema_type,
                        "validation_issues": validation_issues,
                    },
                )
            elif validation_status == "warning":
                self._add_issue(
                    url_id,
                    "structured_data_warning",
                    "warning",
                    {
                        "schema_type": schema_type,
                        "validation_issues": validation_issues,
                    },
                )

        self._flush_issues()

    # -- Indexability --------------------------------------------------------

    def analyze_indexability(self) -> None:
        """Determine indexability and flag noindex pages.

        A URL is considered indexable when all of the following hold:

        1. HTTP status is 200.
        2. Neither ``meta_robots`` nor ``x_robots_tag`` contain "noindex".
        3. The canonical is either absent or self-referencing.
        """
        logger.debug("Analyzing indexability ...")

        stmt = (
            select(
                Url.id,
                Url.url,
                Url.status_code,
                HtmlMeta.meta_robots,
                HtmlMeta.x_robots_tag,
                HtmlMeta.canonical_href,
            )
            .join(HtmlMeta, HtmlMeta.url_id == Url.id)
            .where(Url.job_id == self.job_id, Url.is_html.is_(True))
        )
        rows = self.session.execute(stmt).all()

        indexable_ids: list[int] = []
        non_indexable_ids: list[int] = []

        for url_id, page_url, status_code, meta_robots, x_robots, canonical_href in rows:
            has_noindex = _contains_noindex(meta_robots) or _contains_noindex(x_robots)
            canonical_ok = (
                not canonical_href
                or not canonical_href.strip()
                or canonical_href.strip() == page_url
            )
            is_indexable = status_code == 200 and not has_noindex and canonical_ok

            if is_indexable:
                indexable_ids.append(url_id)
            else:
                non_indexable_ids.append(url_id)

            if has_noindex:
                self._add_issue(url_id, "noindex_page", "info")

        # Bulk-update the indexable column.
        self._bulk_update_indexable(indexable_ids, True)
        self._bulk_update_indexable(non_indexable_ids, False)

        self._flush_issues()

    def _bulk_update_indexable(self, url_ids: list[int], value: bool) -> None:
        """Set ``Url.indexable`` for a list of URL ids in batches."""
        for start in range(0, len(url_ids), BATCH_SIZE):
            batch = url_ids[start : start + BATCH_SIZE]
            self.session.execute(
                update(Url)
                .where(Url.id.in_(batch))
                .values(indexable=value)
            )
        self.session.flush()

    # -- Duplicate Content --------------------------------------------------

    def analyze_duplicates(self) -> None:
        """Detect pages with identical body content via body_hash."""
        logger.debug("Analyzing duplicate content ...")

        # Find body_hash values shared by two or more URLs.
        dup_stmt = (
            select(Url.body_hash)
            .where(
                Url.job_id == self.job_id,
                Url.body_hash.isnot(None),
                Url.body_hash != "",
            )
            .group_by(Url.body_hash)
            .having(func.count(Url.id) > 1)
        )
        dup_hashes = [
            row[0] for row in self.session.execute(dup_stmt).all()
        ]

        if not dup_hashes:
            return

        # For each duplicate hash, fetch the URL ids sharing it.
        for hash_batch_start in range(0, len(dup_hashes), BATCH_SIZE):
            hash_batch = dup_hashes[hash_batch_start : hash_batch_start + BATCH_SIZE]

            rows = self.session.execute(
                select(Url.id, Url.body_hash).where(
                    Url.job_id == self.job_id,
                    Url.body_hash.in_(hash_batch),
                )
            ).all()

            hash_to_ids: dict[str, list[int]] = defaultdict(list)
            for url_id, body_hash in rows:
                hash_to_ids[body_hash].append(url_id)

            for body_hash, url_ids in hash_to_ids.items():
                if len(url_ids) < 2:
                    continue
                for uid in url_ids:
                    other_ids = [x for x in url_ids if x != uid]
                    self._add_issue(
                        uid,
                        "duplicate_content",
                        "warning",
                        {"body_hash": body_hash, "duplicate_urls": other_ids},
                    )

        self._flush_issues()

    # -- Redirect Chains ----------------------------------------------------

    def analyze_redirect_chains(self) -> None:
        """Detect redirect chains longer than 2 hops and redirect loops."""
        logger.debug("Analyzing redirect chains ...")

        # Build an in-memory redirect graph: url -> redirect_url.
        stmt = select(Url.id, Url.url, Url.redirect_url).where(
            Url.job_id == self.job_id,
            Url.redirect_url.isnot(None),
            Url.redirect_url != "",
        )
        rows = self.session.execute(stmt).all()

        if not rows:
            return

        # url_string -> (url_id, redirect_target_string)
        redirect_map: dict[str, str] = {}
        url_to_id: dict[str, int] = {}
        for url_id, url_str, redirect_url in rows:
            redirect_map[url_str] = redirect_url
            url_to_id[url_str] = url_id

        # Walk each redirect origin and trace the chain.
        for origin_url in list(redirect_map.keys()):
            visited: list[str] = [origin_url]
            current = origin_url
            is_loop = False

            while current in redirect_map:
                target = redirect_map[current]
                if target in visited:
                    is_loop = True
                    break
                visited.append(target)
                current = target

            origin_id = url_to_id[origin_url]
            hops = len(visited) - 1  # number of redirects

            if is_loop:
                self._add_issue(
                    origin_id,
                    "redirect_loop",
                    "error",
                    {"chain": visited + [redirect_map[current]]},
                )
            elif hops > self.max_redirect_chain:
                self._add_issue(
                    origin_id,
                    "redirect_chain",
                    "warning",
                    {"chain": visited, "hops": hops},
                )

        self._flush_issues()

    # -- Images -------------------------------------------------------------

    def analyze_images(self) -> None:
        """Flag images that are missing alt text."""
        logger.debug("Analyzing images ...")

        stmt = (
            select(Resource.url_id, Resource.resource_url)
            .join(Url, Url.id == Resource.url_id)
            .where(
                Url.job_id == self.job_id,
                Resource.resource_type == "image",
                (Resource.alt_text.is_(None)) | (Resource.alt_text == ""),
            )
        )
        rows = self.session.execute(stmt).all()

        for url_id, resource_url in rows:
            self._add_issue(
                url_id,
                "image_missing_alt",
                "warning",
                {"image_url": resource_url},
            )

        self._flush_issues()

    # -- Security -----------------------------------------------------------

    def analyze_security(self) -> None:
        """Security tab equivalent -- flag HTTP URLs, mixed content, and missing security headers."""
        logger.debug("Analyzing security ...")

        stmt = (
            select(
                Url.id,
                SecurityHeaders.is_https,
                SecurityHeaders.has_mixed_content,
                SecurityHeaders.has_hsts,
                SecurityHeaders.has_csp,
                SecurityHeaders.has_x_content_type_options,
                SecurityHeaders.has_x_frame_options,
                SecurityHeaders.has_unsafe_crossorigin,
            )
            .join(SecurityHeaders, SecurityHeaders.url_id == Url.id)
            .where(Url.job_id == self.job_id)
        )
        rows = self.session.execute(stmt).all()

        for (
            url_id,
            is_https,
            has_mixed_content,
            has_hsts,
            has_csp,
            has_x_content_type_options,
            has_x_frame_options,
            has_unsafe_crossorigin,
        ) in rows:
            # HTTP URL (scheme != "https").
            if is_https is False:
                self._add_issue(url_id, "http_url", "warning")

            # Mixed content (HTTPS page loading HTTP resources).
            if has_mixed_content is True:
                self._add_issue(url_id, "mixed_content", "warning")

            # Missing Strict-Transport-Security header.
            if has_hsts is False:
                self._add_issue(url_id, "missing_hsts", "info")

            # Missing Content-Security-Policy header.
            if has_csp is False:
                self._add_issue(url_id, "missing_csp", "info")

            # Missing X-Content-Type-Options header.
            if has_x_content_type_options is False:
                self._add_issue(url_id, "missing_x_content_type_options", "info")

            # Missing X-Frame-Options header.
            if has_x_frame_options is False:
                self._add_issue(url_id, "missing_x_frame_options", "info")

            # Unsafe cross-origin (target=_blank without rel=noopener).
            if has_unsafe_crossorigin is True:
                self._add_issue(url_id, "unsafe_crossorigin", "warning")

        self._flush_issues()

    # -- Content ------------------------------------------------------------

    def analyze_content(self) -> None:
        """Content tab equivalent -- flag pages with low word count or low text-to-HTML ratio."""
        logger.debug("Analyzing content ...")

        stmt = (
            select(Url.id, Url.word_count, Url.text_ratio)
            .where(
                Url.job_id == self.job_id,
                Url.is_html.is_(True),
            )
        )
        rows = self.session.execute(stmt).all()

        for url_id, word_count, text_ratio in rows:
            # Low word count.
            if word_count is not None and word_count < self.min_word_count:
                self._add_issue(
                    url_id,
                    "low_word_count",
                    "warning",
                    {"word_count": word_count},
                )

            # Text-to-HTML ratio checks (very low takes priority over low).
            if text_ratio is not None:
                if text_ratio < VERY_LOW_TEXT_RATIO_THRESHOLD:
                    self._add_issue(
                        url_id,
                        "very_low_text_ratio",
                        "warning",
                        {"text_ratio": text_ratio},
                    )
                elif text_ratio < LOW_TEXT_RATIO_THRESHOLD:
                    self._add_issue(
                        url_id,
                        "low_text_ratio",
                        "info",
                        {"text_ratio": text_ratio},
                    )

        self._flush_issues()

    # -- URL Issues ---------------------------------------------------------

    def analyze_url_issues(self) -> None:
        """URL tab equivalent -- flag structural problems in URLs."""
        logger.debug("Analyzing URL issues ...")

        stmt = (
            select(Url.id, Url.url, Url.path)
            .where(Url.job_id == self.job_id)
        )
        rows = self.session.execute(stmt).all()

        for url_id, url_str, path in rows:
            url_len = len(url_str) if url_str else 0

            # URL over 115 characters.
            if url_len > URL_MAX_LENGTH:
                self._add_issue(
                    url_id,
                    "url_too_long",
                    "warning",
                    {"length": url_len},
                )

            # URL contains non-ASCII characters.
            if url_str and _NON_ASCII_RE.search(url_str):
                self._add_issue(url_id, "url_non_ascii", "warning")

            # Path-specific checks (only when path is available).
            if path:
                # Uppercase letters in path.
                if path != path.lower():
                    self._add_issue(url_id, "url_uppercase", "info")

                # Underscores in path.
                if "_" in path:
                    self._add_issue(url_id, "url_underscores", "info")

                # Multiple consecutive slashes in path.
                if _MULTIPLE_SLASHES_RE.search(path):
                    self._add_issue(url_id, "url_multiple_slashes", "warning")

            # URL contains query parameters.
            if url_str and "?" in url_str:
                self._add_issue(url_id, "url_has_parameters", "info")

        self._flush_issues()

    # -- Link Counts --------------------------------------------------------

    def compute_link_counts(self) -> None:
        """Populate inlinks_count, outlinks_count, external_outlinks_count,
        and unique_inlinks_count on the Url table using efficient SQL
        aggregation queries.
        """
        logger.debug("Computing link counts ...")

        # --- Inlinks: count of Link rows where to_url_hash matches url.url_hash ---
        inlinks_subq = (
            select(
                Url.id.label("url_id"),
                func.count(Link.id).label("inlinks"),
            )
            .join(Link, and_(
                Link.to_url_hash == Url.url_hash,
                Link.job_id == Url.job_id,
            ))
            .where(Url.job_id == self.job_id)
            .group_by(Url.id)
        ).subquery()

        self.session.execute(
            update(Url)
            .where(Url.id == inlinks_subq.c.url_id)
            .values(inlinks_count=inlinks_subq.c.inlinks)
        )

        # --- Unique inlinks: count of DISTINCT from_url_id in Link where to_url_hash matches ---
        unique_inlinks_subq = (
            select(
                Url.id.label("url_id"),
                func.count(func.distinct(Link.from_url_id)).label("unique_inlinks"),
            )
            .join(Link, and_(
                Link.to_url_hash == Url.url_hash,
                Link.job_id == Url.job_id,
            ))
            .where(Url.job_id == self.job_id)
            .group_by(Url.id)
        ).subquery()

        self.session.execute(
            update(Url)
            .where(Url.id == unique_inlinks_subq.c.url_id)
            .values(unique_inlinks_count=unique_inlinks_subq.c.unique_inlinks)
        )

        # --- Internal outlinks: count of Link rows where from_url_id = url.id AND is_internal=True ---
        outlinks_subq = (
            select(
                Link.from_url_id.label("url_id"),
                func.count(Link.id).label("outlinks"),
            )
            .join(Url, Url.id == Link.from_url_id)
            .where(
                Url.job_id == self.job_id,
                Link.is_internal.is_(True),
            )
            .group_by(Link.from_url_id)
        ).subquery()

        self.session.execute(
            update(Url)
            .where(Url.id == outlinks_subq.c.url_id)
            .values(outlinks_count=outlinks_subq.c.outlinks)
        )

        # --- External outlinks: count of Link rows where from_url_id = url.id AND is_internal=False ---
        ext_outlinks_subq = (
            select(
                Link.from_url_id.label("url_id"),
                func.count(Link.id).label("ext_outlinks"),
            )
            .join(Url, Url.id == Link.from_url_id)
            .where(
                Url.job_id == self.job_id,
                Link.is_internal.is_(False),
            )
            .group_by(Link.from_url_id)
        ).subquery()

        self.session.execute(
            update(Url)
            .where(Url.id == ext_outlinks_subq.c.url_id)
            .values(external_outlinks_count=ext_outlinks_subq.c.ext_outlinks)
        )

        self.session.flush()

    # -- PageRank -----------------------------------------------------------

    # Weights for link_position: content links carry more SEO value than
    # boilerplate navigation/footer links that repeat on every page.
    _POSITION_WEIGHT: dict[str | None, float] = {
        "content": 1.0,
        "header": 0.3,
        "footer": 0.2,
        None: 0.5,
    }

    def compute_pagerank(
        self,
        damping: float = 0.85,
        max_iter: int = 100,
        tol: float = 1e-6,
    ) -> None:
        """Compute weighted internal PageRank for all URLs in this job.

        Links from the main content area are weighted higher than
        boilerplate nav/footer links that repeat on every page.
        """
        logger.debug("Computing PageRank ...")

        # 1. Get all internal URL IDs for this job
        url_rows = (
            self.session.execute(
                select(Url.id).where(
                    Url.job_id == self.job_id,
                    Url.is_internal.is_(True),
                )
            ).all()
        )
        if not url_rows:
            return

        url_ids = [r[0] for r in url_rows]
        id_to_idx = {uid: i for i, uid in enumerate(url_ids)}
        n = len(url_ids)

        # 2. Build weighted adjacency from internal dofollow links
        link_rows = (
            self.session.execute(
                select(Link.from_url_id, Url.id, Link.link_position)
                .join(Url, and_(
                    Link.to_url_hash == Url.url_hash,
                    Link.job_id == Url.job_id,
                ))
                .where(
                    Link.job_id == self.job_id,
                    Link.is_internal.is_(True),
                    Link.follow.is_(True),
                )
            ).all()
        )

        # Per edge: accumulate the max weight (deduplicate src->dst,
        # keeping the highest-weight position if multiple links exist).
        edge_weight: dict[tuple[int, int], float] = {}
        for from_id, to_id, position in link_rows:
            src = id_to_idx.get(from_id)
            dst = id_to_idx.get(to_id)
            if src is not None and dst is not None and src != dst:
                w = self._POSITION_WEIGHT.get(position, 0.5)
                key = (src, dst)
                if key not in edge_weight or w > edge_weight[key]:
                    edge_weight[key] = w

        # Build outlinks and total weight per source node
        outlinks: dict[int, dict[int, float]] = defaultdict(dict)  # src -> {dst: weight}
        out_total_weight: dict[int, float] = defaultdict(float)
        for (src, dst), w in edge_weight.items():
            outlinks[src][dst] = w
            out_total_weight[src] += w

        # 3. Weighted iterative power method
        pr = [1.0 / n] * n

        for _ in range(max_iter):
            new_pr = [(1.0 - damping) / n] * n

            for i in range(n):
                total_w = out_total_weight.get(i, 0.0)
                if total_w > 0:
                    for j, w in outlinks[i].items():
                        new_pr[j] += damping * pr[i] * (w / total_w)

            # Handle dangling nodes (no outlinks): redistribute
            dangling_sum = sum(
                pr[i] for i in range(n) if out_total_weight.get(i, 0.0) == 0
            )
            dangling_add = damping * dangling_sum / n
            new_pr = [p + dangling_add for p in new_pr]

            # Check convergence
            diff = max(abs(new_pr[i] - pr[i]) for i in range(n))
            pr = new_pr
            if diff < tol:
                break

        # 4. Normalize to 0-10 scale
        max_pr = max(pr) if pr else 1.0
        if max_pr > 0:
            pr = [p / max_pr * 10.0 for p in pr]

        # 5. Bulk update
        for i, uid in enumerate(url_ids):
            self.session.execute(
                update(Url)
                .where(Url.id == uid)
                .values(pagerank=round(pr[i], 4))
            )
        self.session.flush()
        logger.info("PageRank computed for %d URLs (job %s)", n, self.job_id)

    # -- Link Analysis ------------------------------------------------------

    def analyze_links(self) -> None:
        """Link analysis -- flag orphan pages and pages with excessive outlinks."""
        logger.debug("Analyzing links ...")

        # Orphan pages: HTML pages with 0 inlinks.
        stmt = (
            select(Url.id)
            .where(
                Url.job_id == self.job_id,
                Url.is_html.is_(True),
                (Url.inlinks_count.is_(None)) | (Url.inlinks_count == 0),
            )
        )
        rows = self.session.execute(stmt).all()

        for (url_id,) in rows:
            self._add_issue(url_id, "orphan_page", "warning")

        # Pages with very high outlinks (> threshold).
        stmt = (
            select(Url.id, Url.outlinks_count)
            .where(
                Url.job_id == self.job_id,
                Url.outlinks_count > self.max_outlinks,
            )
        )
        rows = self.session.execute(stmt).all()

        for url_id, outlink_count in rows:
            self._add_issue(
                url_id,
                "high_outlink_count",
                "info",
                {"count": outlink_count},
            )

        self._flush_issues()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _contains_noindex(directive: str | None) -> bool:
    """Return ``True`` if the robots directive string contains 'noindex'."""
    if not directive:
        return False
    return "noindex" in directive.lower()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_analysis(job_id: str) -> None:
    """Entry point called by the crawler worker after a crawl completes.

    Creates its own database session, runs every analysis check, and
    ensures the session is closed on exit.
    """
    session = SessionLocal()
    try:
        analyzer = SEOAnalyzer(session, job_id)
        analyzer.run_all()
    finally:
        session.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m analysis.analyzer <job_id>")
        sys.exit(1)
    run_analysis(sys.argv[1])
