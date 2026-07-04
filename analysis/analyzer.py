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

# Patterns that indicate non-SEO-friendly URLs exposed to crawlers.
# These waste crawl budget and pollute the index when discoverable.
_NON_SEO_FRIENDLY_RE = re.compile(
    r";jsessionid="                 # Java session IDs leaked into URLs
    r"|%5Cu\d{4}"                   # un-decoded JS unicode escapes (%5Cu002F)
    r"|\\u[0-9a-fA-F]{4}"          # raw JS unicode escapes
    r"|%00"                         # null bytes in URL
    , re.IGNORECASE,
)

# Heuristic: path segments that look like CMS internal/faceted navigation.
# Matches paths containing encoded semicolons, pipe chars, or long
# percent-encoded sequences typical of filter/tag pages.
_CMS_FACETED_RE = re.compile(
    r"[.;|](?:categorias|categories|tags|labels|filters?|facets?|taxonomy)/"
    r"|/ELEM_ENTRY|/BP_Categories/"   # Liferay-specific
    , re.IGNORECASE,
)


# T4 (C1): parse the raw content of <meta http-equiv="refresh"> as stored
# in html_meta.meta_refresh. Formats seen in the wild: "5", "0;URL=/x",
# "3; url='https://x'". Returns (delay_seconds, raw_target_or_None).
_META_REFRESH_URL_RE = re.compile(
    r"url\s*=\s*['\"]?\s*([^'\";\s]+)", re.IGNORECASE
)
_META_REFRESH_DELAY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)")


def parse_meta_refresh(content: str | None) -> tuple[int | None, str | None]:
    """Parse a meta-refresh content attribute into (delay, target URL)."""
    if not content:
        return None, None
    delay: int | None = None
    m = _META_REFRESH_DELAY_RE.match(content)
    if m:
        try:
            delay = int(float(m.group(1)))
        except ValueError:
            delay = None
    m = _META_REFRESH_URL_RE.search(content)
    target = m.group(1).strip() if m else None
    return delay, target or None


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
        self._job = job
        t = (job.config or {}).get("analysis_thresholds", {}) if job else {}
        self.title_min_len = t.get("title_min_length", TITLE_MIN_LEN)
        self.title_max_len = t.get("title_max_length", TITLE_MAX_LEN)
        self.desc_min_len = t.get("description_min_length", DESCRIPTION_MIN_LEN)
        self.desc_max_len = t.get("description_max_length", DESCRIPTION_MAX_LEN)
        self.min_word_count = t.get("min_word_count", LOW_WORD_COUNT_THRESHOLD)
        self.max_redirect_chain = t.get("max_redirect_chain_length", 2)
        self.max_outlinks = t.get("max_outlinks", HIGH_OUTLINK_THRESHOLD)
        # T3: PageRank version switch (1 = historical, bit-for-bit)
        self.pagerank_version = t.get("pagerank_version", 1)
        self.equity_leak_threshold = t.get("equity_leak_threshold", 0.3)
        # T17.3: slow page threshold (ms)
        self.slow_page_ms = t.get("slow_page_ms", 3000)
        # T8: normalization config of THIS job (the analyzer process does
        # not have the crawl subprocess' active config)
        from shared.url_normalization import UrlNormalizationConfig

        self._norm_config = UrlNormalizationConfig.from_job_config(
            job.config if job else None
        )

    # -- public interface ---------------------------------------------------

    def run_all(self) -> None:
        """Run every analysis check and persist results to the issues table."""
        logger.info("Starting SEO analysis for job %s", self.job_id)

        self.clear_existing_issues()

        self.assign_segments()
        self.analyze_status_codes()
        self.analyze_titles()
        self.analyze_descriptions()
        self.analyze_headings()
        self.analyze_canonicals()
        self.analyze_canonical_chains()
        self.analyze_hreflang()
        self.analyze_structured_data()
        self.analyze_indexability()
        self.analyze_duplicates()
        self.analyze_redirect_chains()
        self.analyze_meta_refresh()
        self.analyze_js_redirects()
        self.analyze_images()
        self.analyze_security()
        self.analyze_content()
        self.analyze_performance()
        self.analyze_url_issues()
        self.compute_link_counts()
        self.compute_pagerank()
        self.analyze_links()
        self.analyze_sitemaps()
        self.analyze_real_orphans()
        self.analyze_watchlist()

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

            # Non-SEO-friendly URL (malformed patterns discoverable by bots).
            if url_str and _NON_SEO_FRIENDLY_RE.search(url_str):
                self._add_issue(url_id, "url_non_seo_friendly", "error")

            # CMS faceted/filter URL (wastes crawl budget, index bloat).
            if url_str and _CMS_FACETED_RE.search(url_str):
                self._add_issue(url_id, "url_cms_faceted", "warning",
                                {"hint": "Crawl budget waste — consider blocking with robots.txt or noindex"})

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
    #
    # BUG LATENTE CONOCIDO (C3, documento maestro v2): link_position tiene
    # 5 valores posibles (content, nav, footer, header, sidebar) pero este
    # mapa no cubre "nav" ni "sidebar", que caen al default 0.5 — es decir,
    # hoy un enlace de menú pesa MÁS que uno de header (0.3) o footer (0.2).
    # NO corregir aquí: cambiaría el PageRank de todos los jobs históricos
    # y rompería su comparabilidad (hay snapshot congelado en
    # tests/python/test_pagerank_v1_snapshot.py). La corrección va solo en
    # la rama v2 del cálculo (T3), conmutada por job.config.
    _POSITION_WEIGHT: dict[str | None, float] = {
        "content": 1.0,
        "header": 0.3,
        "footer": 0.2,
        None: 0.5,
    }

    # T3/C3: position weights for the v2 branch ONLY. Fixes the latent bug
    # above: nav/sidebar get boilerplate-level weights instead of the 0.5
    # default that made a menu link outweigh header/footer.
    _POSITION_WEIGHT_V2: dict[str | None, float] = {
        "content": 1.0,
        "header": 0.3,
        "sidebar": 0.25,
        "nav": 0.2,
        "footer": 0.2,
        None: 0.5,
    }

    # T3: per-hop decay applied when collapsing 3xx chains in v2.
    _REDIRECT_DECAY = 0.9
    _REDIRECT_MAX_HOPS = 10

    def compute_pagerank(
        self,
        damping: float = 0.85,
        max_iter: int = 100,
        tol: float = 1e-6,
    ) -> None:
        """Compute weighted internal PageRank for all URLs in this job.

        Dispatches on ``analysis_thresholds.pagerank_version`` (T3):
        version 1 is the historical algorithm bit-for-bit (snapshot-locked);
        version 2 adds nofollow dilution, redirect collapse with decay,
        an indexable-only graph and ``equity_leak`` reporting. The version
        used is recorded in ``jobs.config["_pagerank_version_used"]``.
        """
        version = 2 if self.pagerank_version == 2 else 1
        if self._job is not None:
            self._job.config = {
                **(self._job.config or {}),
                "_pagerank_version_used": version,
            }
        if version == 2:
            self._compute_pagerank_v2(damping, max_iter, tol)
        else:
            self._compute_pagerank_v1(damping, max_iter, tol)

    def _compute_pagerank_v1(
        self,
        damping: float = 0.85,
        max_iter: int = 100,
        tol: float = 1e-6,
    ) -> None:
        """Historical weighted PageRank (v1). DO NOT change: snapshot-locked
        by tests/python/test_pagerank_v1_snapshot.py for job comparability.
        """
        logger.debug("Computing PageRank (v1) ...")

        # 1. Get all internal URL IDs for this job.
        # T2: rows with status_group='not_crawled' (sitemap/GSC orphans that
        # were never fetched) are not part of the crawl graph — excluding
        # them keeps re-analysis output identical to the first analysis.
        # Jobs without such rows are bit-for-bit unaffected (v1 snapshot).
        url_rows = (
            self.session.execute(
                select(Url.id).where(
                    Url.job_id == self.job_id,
                    Url.is_internal.is_(True),
                    (Url.status_group.is_(None))
                    | (Url.status_group != "not_crawled"),
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

    def _compute_pagerank_v2(
        self,
        damping: float = 0.85,
        max_iter: int = 100,
        tol: float = 1e-6,
    ) -> None:
        """PageRank v2 (T3): nofollow dilution, redirect collapse, indexable-
        only graph, equity leak.

        * ALL internal links enter the denominator of their source page;
          only usable ones distribute. The rest of the fraction is
          destroyed (no pre-2009 sculpting, no dangling redistribution).
        * Destinations are resolved through 3xx chains (``urls.redirect_url``,
          max 10 hops, 0.9 decay per hop; loops cut). 3xx URLs are
          pass-through and accumulate no rank of their own.
        * Graph nodes are indexable 2xx internal pages only. Edges toward
          non-indexable / error / unresolved destinations are destroyed and
          reported per page as ``equity_leak`` above the configured ratio.
        """
        from shared.url_normalization import compute_url_hash as _hash

        logger.debug("Computing PageRank (v2) ...")

        rows = self.session.execute(
            select(Url.id, Url.url_hash, Url.status_code, Url.indexable,
                   Url.redirect_url)
            .where(
                Url.job_id == self.job_id,
                Url.is_internal.is_(True),
                (Url.status_group.is_(None))
                | (Url.status_group != "not_crawled"),
            )
        ).all()
        if not rows:
            return

        by_hash: dict[str, tuple] = {r.url_hash: r for r in rows}
        by_id: dict[int, tuple] = {r.id: r for r in rows}

        def _is_node(r) -> bool:
            return (
                r.status_code is not None
                and 200 <= r.status_code < 300
                and r.indexable is not False
            )

        node_ids = [r.id for r in rows if _is_node(r)]
        if not node_ids:
            return
        id_to_idx = {uid: i for i, uid in enumerate(node_ids)}
        n = len(node_ids)

        def _resolve(target_hash: str) -> tuple[int | None, float]:
            """Follow 3xx chains → (final node id | None, decay factor)."""
            decay = 1.0
            seen: set[str] = set()
            current = target_hash
            for _ in range(self._REDIRECT_MAX_HOPS):
                if current in seen:
                    return None, decay  # loop
                seen.add(current)
                row = by_hash.get(current)
                if row is None:
                    return None, decay  # uncrawled or external redirect
                if row.status_code is not None and 300 <= row.status_code < 400:
                    if not row.redirect_url:
                        return None, decay
                    decay *= self._REDIRECT_DECAY
                    current = _hash(row.redirect_url, self._norm_config)
                    continue
                return (row.id, decay) if _is_node(row) else (None, decay)
            return None, decay  # too many hops

        link_rows = self.session.execute(
            select(Link.from_url_id, Link.to_url_hash, Link.link_position,
                   Link.follow)
            .where(
                Link.job_id == self.job_id,
                Link.is_internal.is_(True),
            )
        ).all()

        # Dedup raw links first, keeping the max position weight per
        # (source, target, follow) — same dedup philosophy as v1.
        raw_edges: dict[tuple[int, str, bool], float] = {}
        for from_id, to_hash, position, follow in link_rows:
            src_row = by_id.get(from_id)
            if src_row is None or not _is_node(src_row):
                continue  # non-indexable sources have no rank to give
            w = self._POSITION_WEIGHT_V2.get(position, 0.5)
            key = (src_row.id, to_hash, follow is not False)
            if key not in raw_edges or w > raw_edges[key]:
                raw_edges[key] = w

        # Per source: total emitted weight (denominator), effective edges
        # (what actually distributes) and destroyed weight by cause.
        out_total: dict[int, float] = defaultdict(float)
        edge_weight: dict[tuple[int, int], float] = {}
        leaked: dict[int, float] = defaultdict(float)     # bad destinations
        nofollow_w: dict[int, float] = defaultdict(float)  # deliberate dilution
        leaked_edges: dict[int, int] = defaultdict(int)

        for (src_id, to_hash, follow), w in raw_edges.items():
            src = id_to_idx[src_id]
            out_total[src] += w

            if not follow:
                nofollow_w[src] += w
                continue  # counts in denominator, never distributes

            target_id, decay = _resolve(to_hash)
            if target_id is None:
                leaked[src] += w
                leaked_edges[src] += 1
                continue
            dst = id_to_idx[target_id]
            if dst == src:
                out_total[src] -= w  # self-links stay excluded, as in v1
                continue
            key = (src, dst)
            effective = w * decay
            if key not in edge_weight or effective > edge_weight[key]:
                edge_weight[key] = effective

        outlinks: dict[int, dict[int, float]] = defaultdict(dict)
        for (src, dst), w in edge_weight.items():
            outlinks[src][dst] = w

        # Power iteration. The denominator is the TOTAL emitted weight, so
        # nofollow/leaked fractions vanish instead of being redistributed.
        pr = [1.0 / n] * n
        for _ in range(max_iter):
            new_pr = [(1.0 - damping) / n] * n
            for i in range(n):
                total_w = out_total.get(i, 0.0)
                if total_w > 0:
                    for j, w in outlinks[i].items():
                        new_pr[j] += damping * pr[i] * (w / total_w)
            # True dangling nodes (zero outlinks) still redistribute, as in
            # v1; pages whose weight was fully destroyed do NOT.
            dangling_sum = sum(
                pr[i] for i in range(n) if out_total.get(i, 0.0) == 0
            )
            dangling_add = damping * dangling_sum / n
            new_pr = [p + dangling_add for p in new_pr]

            diff = max(abs(new_pr[i] - pr[i]) for i in range(n))
            pr = new_pr
            if diff < tol:
                break

        max_pr = max(pr) if pr else 1.0
        if max_pr > 0:
            pr = [p / max_pr * 10.0 for p in pr]

        for i, uid in enumerate(node_ids):
            self.session.execute(
                update(Url).where(Url.id == uid).values(pagerank=round(pr[i], 4))
            )
        # Non-node internal URLs (3xx pass-through, errors, non-indexables)
        # accumulate no rank of their own in v2.
        non_nodes = [r.id for r in rows if not _is_node(r)]
        if non_nodes:
            self.session.execute(
                update(Url).where(Url.id.in_(non_nodes)).values(pagerank=None)
            )
        self.session.flush()

        # Equity leak per page (weight destroyed toward worthless targets).
        for src, lost in leaked.items():
            total = out_total.get(src, 0.0)
            if total <= 0:
                continue
            ratio = lost / total
            if ratio >= self.equity_leak_threshold:
                self._add_issue(
                    node_ids[src],
                    "equity_leak",
                    "warning",
                    {
                        "leaked_weight": round(lost, 4),
                        "total_weight": round(total, 4),
                        "leak_ratio": round(ratio, 4),
                        "nofollow_weight": round(nofollow_w.get(src, 0.0), 4),
                        "leaked_edges": leaked_edges.get(src, 0),
                    },
                )
        self._flush_issues()
        logger.info(
            "PageRank v2 computed for %d nodes (job %s)", n, self.job_id
        )

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

    # -- Segments (T12) ---------------------------------------------------------

    def assign_segments(self) -> None:
        """T12: assign every HTML URL of the job to a client-level segment.

        Rules (``segments`` table, client-scoped) are evaluated against the
        URL path in priority order (lower number wins); first match wins;
        no match → no row (implicit "(sin segmento)"). Re-analysis wipes
        the job's assignments first, so re-crawls never duplicate rows.
        Jobs without client_id or without rules are untouched.
        """
        from shared.models import Segment, UrlSegment

        client_id = self._job.client_id if self._job else None
        if not client_id:
            return

        segments = self.session.execute(
            select(Segment)
            .where(Segment.client_id == client_id)
            .order_by(Segment.priority, Segment.id)
        ).scalars().all()
        if not segments:
            return

        logger.debug("Assigning segments (%d rules) ...", len(segments))

        matchers: list[tuple[int, Any]] = []
        for seg in segments:
            if seg.rule_type == "regex":
                try:
                    matchers.append((seg.id, re.compile(seg.rule).search))
                except re.error:
                    logger.warning(
                        "Segment %s has an invalid regex, skipping: %r",
                        seg.name, seg.rule,
                    )
            else:  # prefix
                matchers.append(
                    (seg.id, lambda path, p=seg.rule: path.startswith(p))
                )

        # Idempotent: wipe and reassign in one pass.
        self.session.execute(
            delete(UrlSegment).where(UrlSegment.job_id == self.job_id)
        )

        rows = self.session.execute(
            select(Url.id, Url.path)
            .where(
                Url.job_id == self.job_id,
                Url.is_internal.is_(True),
                Url.is_html.is_(True),
            )
        ).all()

        pending: list[UrlSegment] = []
        for url_id, path in rows:
            path = path or "/"
            for seg_id, match in matchers:
                if match(path):
                    pending.append(UrlSegment(
                        job_id=self.job_id, url_id=url_id, segment_id=seg_id,
                    ))
                    break
            if len(pending) >= BATCH_SIZE:
                self.session.bulk_save_objects(pending)
                pending = []
        if pending:
            self.session.bulk_save_objects(pending)
        self.session.flush()

    # -- Canonical chains (T17.4) ----------------------------------------------

    _CANONICAL_MAX_HOPS = 5

    def analyze_canonical_chains(self) -> None:
        """T17.4: transitive canonical resolution, mirroring
        ``analyze_redirect_chains``. A canonical→B canonical→C emits
        ``canonical_chain`` (warning) on A; cycles emit ``canonical_loop``
        (error) on every member. ``analyze_canonicals`` (single-hop
        validation) is untouched.
        """
        from shared.url_normalization import compute_url_hash as _hash

        logger.debug("Analyzing canonical chains ...")

        rows = self.session.execute(
            select(Url.id, Url.url, Url.url_hash, HtmlMeta.canonical_href)
            .join(HtmlMeta, HtmlMeta.url_id == Url.id)
            .where(
                Url.job_id == self.job_id,
                HtmlMeta.canonical_href.isnot(None),
            )
        ).all()

        # url_hash → (url_id, url, canonical_target_hash or None if self)
        canon: dict[str, tuple[int, str, str | None]] = {}
        for url_id, url, url_hash, href in rows:
            target_hash = _hash(href, self._norm_config)
            canon[url_hash] = (
                url_id, url, target_hash if target_hash != url_hash else None,
            )

        for start_hash, (url_id, url, target) in canon.items():
            if target is None:
                continue
            chain = [start_hash]
            current = target
            is_loop = False
            while current is not None and len(chain) <= self._CANONICAL_MAX_HOPS:
                if current in chain:
                    is_loop = True
                    chain.append(current)
                    break
                chain.append(current)
                entry = canon.get(current)
                current = entry[2] if entry else None

            def _url_of(h: str) -> str:
                e = canon.get(h)
                return e[1] if e else h

            if is_loop:
                self._add_issue(
                    url_id,
                    "canonical_loop",
                    "error",
                    {"chain": [_url_of(h) for h in chain]},
                )
            elif len(chain) > 2:  # start → intermediate → final = chain
                self._add_issue(
                    url_id,
                    "canonical_chain",
                    "warning",
                    {
                        "chain": [_url_of(h) for h in chain],
                        "hops": len(chain) - 1,
                    },
                )
        self._flush_issues()

    # -- Performance (T17.3) ---------------------------------------------------

    def analyze_performance(self) -> None:
        """T17.3: flag pages slower than ``slow_page_ms`` (default 3000)."""
        logger.debug("Analyzing performance ...")

        rows = self.session.execute(
            select(Url.id, Url.response_time_ms)
            .where(
                Url.job_id == self.job_id,
                Url.is_internal.is_(True),
                Url.response_time_ms > self.slow_page_ms,
            )
        ).all()

        for url_id, ms in rows:
            self._add_issue(
                url_id,
                "slow_page",
                "warning",
                {"response_time_ms": ms, "threshold_ms": self.slow_page_ms},
            )
        self._flush_issues()

    # -- Client-side redirects (T4) -------------------------------------------

    def analyze_meta_refresh(self) -> None:
        """T4/C1: derive delay + target from the EXISTING
        ``html_meta.meta_refresh`` column (no re-extraction) and emit
        ``meta_refresh_redirect`` — warning when delay ≤ 5s (search
        engines treat it as a redirect), info otherwise.
        """
        from urllib.parse import urljoin as _urljoin

        logger.debug("Analyzing meta refresh ...")

        rows = self.session.execute(
            select(HtmlMeta.url_id, HtmlMeta.meta_refresh, Url.url)
            .join(Url, Url.id == HtmlMeta.url_id)
            .where(
                Url.job_id == self.job_id,
                HtmlMeta.meta_refresh.isnot(None),
            )
        ).all()

        for url_id, refresh, page_url in rows:
            delay, target = parse_meta_refresh(refresh)
            resolved = _urljoin(page_url, target) if target else None
            self.session.execute(
                update(HtmlMeta)
                .where(HtmlMeta.url_id == url_id)
                .values(meta_refresh_url=resolved, meta_refresh_delay=delay)
            )
            # A refresh pointing at the page itself is a reload, not a
            # redirect; only true redirects become issues.
            if resolved and resolved != page_url:
                severity = "warning" if (delay is not None and delay <= 5) else "info"
                self._add_issue(
                    url_id,
                    "meta_refresh_redirect",
                    severity,
                    {"delay": delay, "target": resolved},
                )

        self.session.flush()
        self._flush_issues()

    def analyze_js_redirects(self) -> None:
        """T4: pages where the Playwright flow recorded a JS redirect
        (``urls.js_redirect_url``). Jobs without render_js have no such
        values and are untouched.
        """
        logger.debug("Analyzing JS redirects ...")

        rows = self.session.execute(
            select(Url.id, Url.js_redirect_url)
            .where(
                Url.job_id == self.job_id,
                Url.js_redirect_url.isnot(None),
            )
        ).all()

        for url_id, target in rows:
            self._add_issue(
                url_id, "js_redirect", "warning", {"target": target}
            )
        self._flush_issues()

    # -- Sitemaps (T1) --------------------------------------------------------

    def analyze_sitemaps(self) -> None:
        """Cross ``sitemap_urls`` (T1) with the crawl by ``url_hash``.

        No-op when the job ingested no sitemaps (flag off → zero rows →
        zero changes). Fills ``urls.in_sitemap`` / ``urls.sitemap_lastmod``
        and emits:

        * ``in_sitemap_not_crawled`` (warning): declared in a sitemap and
          the crawl reached it WITHOUT a 2xx (3xx/4xx/5xx/timeout). URLs
          the crawl never created a row for are T2 territory
          (``orphan_not_in_crawl``), not this issue.
        * ``crawled_not_in_sitemap`` (info): indexable internal HTML with
          2xx that no sitemap declares.
        """
        from shared.models import SitemapUrl

        logger.debug("Analyzing sitemaps ...")

        has_rows = self.session.execute(
            select(SitemapUrl.id)
            .where(SitemapUrl.job_id == self.job_id)
            .limit(1)
        ).first()
        if not has_rows:
            return

        # 1. Mark declared URLs (UPDATE ... FROM works on PG and SQLite 3.33+)
        self.session.execute(
            update(Url)
            .where(
                Url.job_id == self.job_id,
                SitemapUrl.job_id == self.job_id,
                Url.url_hash == SitemapUrl.url_hash,
            )
            .values(in_sitemap=True, sitemap_lastmod=SitemapUrl.lastmod)
        )

        # 2. Crawled internal HTML not declared anywhere → explicit False
        self.session.execute(
            update(Url)
            .where(
                Url.job_id == self.job_id,
                Url.is_internal.is_(True),
                Url.is_html.is_(True),
                Url.in_sitemap.is_(None),
            )
            .values(in_sitemap=False)
        )
        self.session.flush()

        # 3a. in_sitemap_not_crawled: declared but the crawl got a non-2xx.
        rows = self.session.execute(
            select(Url.id, Url.status_code, SitemapUrl.lastmod)
            .join(SitemapUrl, and_(
                SitemapUrl.job_id == Url.job_id,
                SitemapUrl.url_hash == Url.url_hash,
            ))
            .where(
                Url.job_id == self.job_id,
                (Url.status_group.is_(None)) | (Url.status_group != "not_crawled"),
                (Url.status_code.is_(None))
                | (Url.status_code < 200)
                | (Url.status_code >= 300),
            )
        ).all()
        for url_id, status_code, lastmod in rows:
            self._add_issue(
                url_id,
                "in_sitemap_not_crawled",
                "warning",
                {
                    "status_code": status_code,
                    "lastmod": lastmod.isoformat() if lastmod else None,
                },
            )

        # 3b. crawled_not_in_sitemap: indexable HTML the sitemaps omit.
        rows = self.session.execute(
            select(Url.id)
            .where(
                Url.job_id == self.job_id,
                Url.is_internal.is_(True),
                Url.is_html.is_(True),
                Url.indexable.is_(True),
                Url.status_code >= 200,
                Url.status_code < 300,
                Url.in_sitemap.is_(False),
            )
        ).all()
        for (url_id,) in rows:
            self._add_issue(url_id, "crawled_not_in_sitemap", "info")

        self._flush_issues()

    # -- Real orphans (T2) ----------------------------------------------------

    def analyze_real_orphans(self) -> None:
        """T2: URLs known to external sources but unreachable by the crawl.

        Semantics (documented in ``list_issues``):

        * ``orphan_page`` (unchanged, ``analyze_links``): crawled HTML with
          zero inlinks — "page without incoming links".
        * ``orphan_not_in_crawl`` (this step): URL declared by a sitemap
          (and by GSC once T9 lands) whose ``url_hash`` has NO row in
          ``urls`` — "the crawl cannot even reach it".

        Missing URLs get a minimal ``urls`` row so the issues FK holds and
        they show up in the explorer: ``status_group='not_crawled'``,
        ``is_html=False`` and ``inlinks_count=NULL`` — the two safeguards
        that keep the old ``orphan_page`` from double-firing on them.
        Idempotent: re-analysis emits the issue from the persisted rows
        without duplicating them.
        """
        from urllib.parse import urlparse as _urlparse

        from shared.models import SitemapUrl

        logger.debug("Analyzing real orphans ...")

        # 1. Insert minimal rows for sitemap URLs the crawl never saw.
        missing = self.session.execute(
            select(SitemapUrl.url, SitemapUrl.url_hash, SitemapUrl.lastmod)
            .where(
                SitemapUrl.job_id == self.job_id,
                ~select(Url.id)
                .where(
                    Url.job_id == self.job_id,
                    Url.url_hash == SitemapUrl.url_hash,
                )
                .exists(),
            )
        ).all()

        for url, url_hash, lastmod in missing:
            parsed = _urlparse(url)
            self.session.add(Url(
                job_id=self.job_id,
                url=url,
                url_hash=url_hash,
                host=parsed.hostname,
                path=parsed.path or None,
                scheme=parsed.scheme or None,
                is_internal=True,
                is_html=False,          # safeguard: never orphan_page
                status_code=None,
                status_group="not_crawled",
                in_sitemap=True,
                sitemap_lastmod=lastmod,
            ))
        if missing:
            self.session.flush()
            # Safeguard: link counters must be NULL (unknown), not 0. An
            # explicit None in the constructor would not override the
            # column defaults, so force it here.
            self.session.execute(
                update(Url)
                .where(
                    Url.job_id == self.job_id,
                    Url.status_group == "not_crawled",
                )
                .values(
                    inlinks_count=None,
                    outlinks_count=None,
                    unique_inlinks_count=None,
                    external_outlinks_count=None,
                )
            )
            self.session.flush()

        # 2. Emit the issue for EVERY not_crawled row (idempotent re-runs).
        rows = self.session.execute(
            select(Url.id, Url.sitemap_lastmod, SitemapUrl.id.isnot(None))
            .outerjoin(SitemapUrl, and_(
                SitemapUrl.job_id == Url.job_id,
                SitemapUrl.url_hash == Url.url_hash,
            ))
            .where(
                Url.job_id == self.job_id,
                Url.status_group == "not_crawled",
            )
        ).all()

        for url_id, lastmod, in_sitemap in rows:
            seen_in = ["sitemap"] if in_sitemap else []
            self._add_issue(
                url_id,
                "orphan_not_in_crawl",
                "warning",
                {
                    "seen_in": seen_in,
                    "lastmod": lastmod.isoformat() if lastmod else None,
                },
            )

        self._flush_issues()


    # -- Watchlist (T16) --------------------------------------------------------

    def analyze_watchlist(self) -> None:
        """T16: sanity-check the client's business-critical URLs.

        Each watchlist entry must be: crawled, status 200, indexable, and
        canonical-to-self. The FIRST failed condition emits
        ``watchlist_check_failed`` (error) with every failed reason in
        ``details``. Uncrawled entries get a minimal ``not_crawled`` row
        (same pattern as T2) so the issue FK holds.
        """
        from urllib.parse import urlparse as _urlparse

        from shared.models import WatchlistEntry
        from shared.url_normalization import compute_url_hash as _hash

        client_id = self._job.client_id if self._job else None
        if not client_id:
            return

        entries = self.session.execute(
            select(WatchlistEntry).where(WatchlistEntry.client_id == client_id)
        ).scalars().all()
        if not entries:
            return

        logger.debug("Analyzing watchlist (%d entries) ...", len(entries))

        for entry in entries:
            url_hash = _hash(entry.url, self._norm_config)
            row = self.session.execute(
                select(Url, HtmlMeta.canonical_href)
                .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
                .where(Url.job_id == self.job_id, Url.url_hash == url_hash)
            ).first()

            reasons: list[str] = []
            if row is None:
                parsed = _urlparse(entry.url)
                new_row = Url(
                    job_id=self.job_id,
                    url=entry.url,
                    url_hash=url_hash,
                    host=parsed.hostname,
                    path=parsed.path or None,
                    scheme=parsed.scheme or None,
                    is_internal=True,
                    is_html=False,
                    status_group="not_crawled",
                )
                self.session.add(new_row)
                self.session.flush()
                url_id = new_row.id
                reasons.append("not_crawled")
            else:
                url_obj, canonical = row
                url_id = url_obj.id
                if url_obj.status_code != 200:
                    reasons.append(f"status_{url_obj.status_code}")
                if url_obj.indexable is False:
                    reasons.append("not_indexable")
                if canonical and _hash(canonical, self._norm_config) != url_hash:
                    reasons.append("canonical_not_self")

            if reasons:
                self._add_issue(
                    url_id,
                    "watchlist_check_failed",
                    "error",
                    {
                        "watch_url": entry.url,
                        "label": entry.label,
                        "reasons": reasons,
                    },
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
