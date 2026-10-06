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
from contextlib import contextmanager
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Sequence
from urllib.parse import unquote, urlparse

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.orm import aliased
from sqlalchemy.orm import Session, aliased

from shared.config import (
    DESCRIPTION_MAX_LEN,
    DESCRIPTION_MIN_LEN,
    TITLE_MAX_LEN,
    TITLE_MIN_LEN,
)
from analysis.sd_validation import validate_structured_data
from analysis import near_duplicates as nd
from analysis import pagerank as prk
from shared.indexabilidad import es_noindex, estado_indexabilidad
from shared.robots import robots_bad_separators
from shared.database import SessionLocal
from shared.models import (
    Heading,
    HtmlMeta,
    Hreflang,
    Issue,
    Link,
    PageContent,
    Resource,
    SecurityHeaders,
    StructuredData,
    Url,
)

logger = logging.getLogger(__name__)


def _norm_url(url: str | None) -> str | None:
    """Canonicalise a URL for equality comparison (dedup semantics).

    Uses the same w3lib canonicalisation the crawler applies when hashing
    URLs, so a self-referencing canonical that differs from the page URL
    only by trailing slash, query-arg order, or fragment is treated as
    equal.  Falls back to a trimmed string if w3lib is unavailable.
    """
    if not url:
        return url
    try:
        from w3lib.url import canonicalize_url

        return canonicalize_url(url, keep_fragments=False)
    except Exception:
        return url.strip().rstrip("/")


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
    # Escapes unicode de JavaScript sin decodificar. Pedia \d{4} —solo
    # digitos— asi que el ejemplo del propio comentario, %5Cu002F, NO casaba:
    # la letra final lo tumbaba. Son hexadecimales.
    r"|%5Cu[0-9a-fA-F]{4}"
    r"|\\u[0-9a-fA-F]{4}"          # raw JS unicode escapes
    r"|%00"                         # null bytes in URL
    , re.IGNORECASE,
)

# Heuristic: path segments that look like CMS internal/faceted navigation.
# Matches paths containing encoded semicolons, pipe chars, or long
# percent-encoded sequences typical of filter/tag pages.
# `/-/` es el prefijo que Liferay pone a sus rutas de "friendly URL" de
# portlet, y `/-/categories/123` es la faceta de verdad: no la cazaba ninguna de
# las alternativas de abajo. En cambio `/elem_entry_list/` si la cazaba por
# `/ELEM_ENTRY` con IGNORECASE, y un listado no es una faceta: se exige el
# limite de palabra para que solo case el nombre exacto del portlet.
_CMS_FACETED_RE = re.compile(
    r"[.;|](?:categorias|categories|tags|labels|filters?|facets?|taxonomy)/"
    r"|/-/(?:categories|categorias|tags|labels)(?:/|$)"   # faceta de Liferay
    r"|/ELEM_ENTRY\b|/BP_Categories/"
    , re.IGNORECASE,
)


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
        # Cadena a partir de DOS saltos (A->B->C). Estaba en 2 con la
        # comparacion `hops > 2`, asi que hacian falta TRES saltos para que
        # saltara y A->B->C —la cadena mas comun, y la que Google pide evitar—
        # no se reportaba nunca. Un salto solo es una redireccion normal.
        self.max_redirect_chain = t.get("max_redirect_chain_length", 1)
        self.max_outlinks = t.get("max_outlinks", HIGH_OUTLINK_THRESHOLD)
        self.near_duplicate_similarity = t.get(
            "near_duplicate_similarity", nd.UMBRAL_SIMILITUD
        )

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
        self.analyze_robots_syntax()
        self.analyze_duplicates()
        self.analyze_near_duplicates()
        self.analyze_redirect_chains()
        self.analyze_images()
        self.analyze_security()
        self.analyze_content()
        self.analyze_url_issues()
        self.compute_link_counts()
        self.compute_pagerank()
        self.analyze_links()
        self.analyze_sitemap()

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

        result = self.session.execute(
            stmt.execution_options(yield_per=BATCH_SIZE)
        )
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
            select(Url.id, HtmlMeta.title, HtmlMeta.title_len, Url.status_code, Url.is_internal)
            .join(HtmlMeta, HtmlMeta.url_id == Url.id)
            .where(Url.job_id == self.job_id, Url.is_html.is_(True))
        )
        rows = self.session.execute(stmt).all()

        # Track titles for duplicate detection.
        title_to_url_ids: dict[str, list[int]] = defaultdict(list)

        for url_id, title, title_len, status_code, is_internal in rows:
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

            # Only real, served internal pages count toward duplicate groups;
            # otherwise 404/redirect/external pages sharing a boilerplate
            # title bury the genuine duplicates.
            if status_code == 200 and is_internal:
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
            select(Url.id, HtmlMeta.meta_description, HtmlMeta.meta_description_len, Url.status_code, Url.is_internal)
            .join(HtmlMeta, HtmlMeta.url_id == Url.id)
            .where(Url.job_id == self.job_id, Url.is_html.is_(True))
        )
        rows = self.session.execute(stmt).all()

        desc_to_url_ids: dict[str, list[int]] = defaultdict(list)

        for url_id, description, desc_len, status_code, is_internal in rows:
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

            # Restrict duplicate grouping to real, served internal pages.
            if status_code == 200 and is_internal:
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

        # Get the full set of HTML URL ids so we can detect missing H1s. Solo
        # 2xx: los headings solo se extraen de respuestas correctas, y sin este
        # filtro cada 404 HTML salia "sin H1" (55 de 656 en Lopesan).
        html_url_ids_stmt = select(Url.id).where(
            Url.job_id == self.job_id, Url.is_html.is_(True),
            Url.status_code >= 200, Url.status_code < 300,
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
            # Also index by normalised URL so a canonical that differs only
            # by trailing slash / arg order still resolves to its target.
            norm = _norm_url(row_url)
            if norm and norm not in url_status:
                url_status[norm] = sc

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

            # Self-referencing canonical is fine -- skip. Compare after
            # normalisation so trailing-slash / www / scheme differences on
            # an otherwise self-referencing canonical are not flagged.
            if _norm_url(canonical) == _norm_url(page_url):
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
            if target_status is None:
                target_status = url_status.get(_norm_url(canonical))
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
        """Validate hreflang annotations, including reciprocal return tags.

        Computes and persists ``lang_valid`` and ``return_tag_ok`` on each
        Hreflang row (nothing else populates them), then flags invalid
        language codes, missing reciprocal return tags, and hreflang targets
        that do not return 200. Reciprocity is confirmed only when the target
        page was crawled and carries its own hreflang cluster; otherwise the
        return tag is left unknown to avoid false positives.
        """
        logger.debug("Analyzing hreflang ...")

        # Preload URL id/status/normalised-url for target resolution.
        url_status: dict[str, int | None] = {}
        url_id_by_norm: dict[str, int] = {}
        norm_by_id: dict[int, str] = {}
        for uid, row_url, sc in self.session.execute(
            select(Url.id, Url.url, Url.status_code).where(Url.job_id == self.job_id)
        ).all():
            url_status[row_url] = sc
            norm = _norm_url(row_url)
            if norm:
                url_status.setdefault(norm, sc)
                url_id_by_norm[norm] = uid
                norm_by_id[uid] = norm

        stmt = (
            select(Hreflang.id, Hreflang.url_id, Hreflang.lang, Hreflang.href)
            .join(Url, Url.id == Hreflang.url_id)
            .where(Url.job_id == self.job_id)
        )
        rows = self.session.execute(stmt).all()

        # Map each crawled page to the set of hreflang targets it declares,
        # so we can check whether a target links back (reciprocal return tag).
        targets_by_page: dict[int, set[str]] = defaultdict(set)
        for _hid, page_id, _lang, href in rows:
            nh = _norm_url(href)
            if nh:
                targets_by_page[page_id].add(nh)

        for hreflang_id, url_id, lang, href in rows:
            norm_href = _norm_url(href)

            # Language code validity (x-default is always valid).
            lang_is_valid = bool(lang) and bool(_LANG_TAG_RE.match(lang))

            # Reciprocal return tag. Only decidable when the target page was
            # crawled; x-default entries need no reciprocal.
            page_norm = norm_by_id.get(url_id)
            target_uid = url_id_by_norm.get(norm_href)
            if lang and lang.lower() == "x-default":
                return_ok = None
            elif target_uid is None or target_uid == url_id:
                return_ok = None  # target not crawled or self-reference
            else:
                return_ok = page_norm in targets_by_page.get(target_uid, set())

            # Persist the computed flags (consumed by the i18n insights).
            self.session.execute(
                update(Hreflang)
                .where(Hreflang.id == hreflang_id)
                .values(lang_valid=lang_is_valid, return_tag_ok=return_ok)
            )

            if return_ok is False:
                self._add_issue(
                    url_id,
                    "hreflang_missing_return",
                    "warning",
                    {"lang": lang, "href": href},
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
            if target_status is None:
                target_status = url_status.get(norm_href)
            if target_status is not None and target_status != 200:
                self._add_issue(
                    url_id,
                    "hreflang_broken_target",
                    "error",
                    {"href": href, "target_status": target_status},
                )

        self.session.flush()
        self._flush_issues()

    # -- Structured Data ----------------------------------------------------

    def analyze_structured_data(self) -> None:
        """Validate structured data and surface errors/warnings.

        Nothing else populates ``validation_status``/``validation_issues``,
        so this computes them from each block's raw JSON (missing ``@type``
        or missing required properties for common rich-result types),
        persists the result, and raises the corresponding issues. Validation
        is deliberately conservative to avoid false positives.
        """
        logger.debug("Analyzing structured data ...")

        stmt = (
            select(
                StructuredData.id,
                StructuredData.url_id,
                StructuredData.schema_type,
                StructuredData.raw,
            )
            .join(Url, Url.id == StructuredData.url_id)
            .where(Url.job_id == self.job_id)
        )
        rows = self.session.execute(stmt).all()

        for sd_id, url_id, schema_type, raw in rows:
            status, issues = validate_structured_data(raw)

            self.session.execute(
                update(StructuredData)
                .where(StructuredData.id == sd_id)
                .values(validation_status=status, validation_issues=issues or None)
            )

            if status == "error":
                self._add_issue(
                    url_id,
                    "structured_data_error",
                    "error",
                    {"schema_type": schema_type, "validation_issues": issues},
                )
            elif status == "warning":
                self._add_issue(
                    url_id,
                    "structured_data_warning",
                    "warning",
                    {"schema_type": schema_type, "validation_issues": issues},
                )

        self.session.flush()
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

        # LEFT JOIN y sin filtrar por is_html a proposito: con INNER JOIN solo
        # entraban las paginas HTML que llegaron a tener metadatos, y todo lo
        # demas —404, redirecciones, PDFs, imagenes— se quedaba en NULL en vez
        # de en False. Medido: 4.899 de 34.704 filas de un rastreo sin valor,
        # con `indexability_status` diciendo "Client Error (404)" al lado. El
        # filtro ?indexable=false de la API las perdia y el CSV las dejaba en
        # blanco, que se lee como "no se sabe".
        stmt = (
            select(
                Url.id,
                Url.url,
                Url.status_code,
                Url.indexability_status,
                Url.blocked_by_robots,
                HtmlMeta.meta_robots,
                HtmlMeta.x_robots_tag,
                HtmlMeta.canonical_href,
            )
            .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
            .where(Url.job_id == self.job_id)
        )
        rows = self.session.execute(stmt).all()

        indexable_ids: list[int] = []
        non_indexable_ids: list[int] = []
        # Se materializa el `noindex` aparte: de el depende si los enlaces de
        # esa pagina cuentan como entrantes (C3 de #24), y no se puede deducir
        # en SQL sin tokenizar la directiva (decision 23: `noindex/nofollow`
        # separado por barra NO es noindex, y buscar la subcadena lo seria).
        noindex_ids: list[int] = []
        con_indice_ids: list[int] = []

        # Motivos que decide esta funcion. Los demas ("Redirect (301)",
        # "Client Error (404)"...) los escribe el rastreo con el codigo exacto
        # y se respetan tal cual: aqui solo se corrigen los de una pagina 200.
        _MOTIVOS_PROPIOS = {"Indexable", "Canonicalised", "Noindex"}
        estados_por_motivo: dict[str, list[int]] = {}

        for (
            url_id,
            page_url,
            status_code,
            estado_actual,
            bloqueada_robots,
            meta_robots,
            x_robots,
            canonical_href,
        ) in rows:
            # Una sola funcion, la misma que usa el spider
            # (shared/indexabilidad.py). Antes aqui se repetian las reglas con
            # otro normalizador de URLs —w3lib, que no quita el puerto por
            # defecto— y la misma pagina salia "Canonicalised" para el analyzer
            # e "Indexable" para el spider.
            es_indexable_calc, motivo_calc = estado_indexabilidad(
                status_code,
                meta_robots=meta_robots,
                x_robots=x_robots,
                canonical_href=canonical_href,
                page_url=page_url,
                bloqueada_por_robots=bool(bloqueada_robots)
                or (estado_actual or "").startswith("Blocked"),
            )
            has_noindex = es_noindex(meta_robots=meta_robots, x_robots=x_robots)
            canonical_ok = motivo_calc != "Canonicalised"
            # Bloqueada por robots.txt no es indexable por mucho que el
            # servidor devolviera 200: el rastreo la alcanzo por el destino de
            # una redireccion, Google no. Sin esto quedaban 22 paginas de un
            # SSO marcadas indexables con el motivo "Blocked by robots.txt" al
            # lado, las dos columnas diciendo lo contrario.
            is_indexable = es_indexable_calc

            if is_indexable:
                indexable_ids.append(url_id)
            else:
                non_indexable_ids.append(url_id)

            (noindex_ids if has_noindex else con_indice_ids).append(url_id)
            if has_noindex:
                self._add_issue(url_id, "noindex_page", "info")

            # El motivo tambien se recalcula aqui, y no solo en el rastreo.
            # El spider lo computaba contra la URL de PARTIDA de una cadena de
            # redirecciones mientras guardaba la de destino: toda pagina
            # alcanzada por un 301 salia como "Canonicalised" con un canonical
            # identico a su propia URL (1.254 de 34.704 en un rastreo medido, y
            # 1.329 en otro; el 100% llegadas por redireccion). Arreglado en el
            # spider, pero los rastreos ya guardados solo se reparan desde
            # aqui: re-analizar un job corrige el dato. Ademas evita que las
            # dos columnas del mismo CSV se contradigan.
            if estado_actual is None or estado_actual in _MOTIVOS_PROPIOS:
                if motivo_calc and motivo_calc != estado_actual:
                    estados_por_motivo.setdefault(motivo_calc, []).append(url_id)

        # Bulk-update the indexable column.
        self._bulk_update_indexable(indexable_ids, True)
        self._bulk_update_indexable(non_indexable_ids, False)
        self._bulk_update_campo(noindex_ids, Url.noindex, True)
        self._bulk_update_campo(con_indice_ids, Url.noindex, False)
        for motivo, ids in estados_por_motivo.items():
            self._bulk_update_estado(ids, motivo)
            logger.info("indexability_status corregido a %r en %d URLs", motivo, len(ids))

        self._flush_issues()

    def _bulk_update_campo(self, url_ids: list[int], columna, valor) -> None:
        """Escribe una columna de `urls` por lotes."""
        for start in range(0, len(url_ids), BATCH_SIZE):
            batch = url_ids[start : start + BATCH_SIZE]
            self.session.execute(
                update(Url).where(Url.id.in_(batch)).values({columna: valor})
            )
        self.session.flush()

    def _bulk_update_estado(self, url_ids: list[int], estado: str) -> None:
        """Escribe ``Url.indexability_status`` por lotes."""
        for start in range(0, len(url_ids), BATCH_SIZE):
            batch = url_ids[start : start + BATCH_SIZE]
            self.session.execute(
                update(Url)
                .where(Url.id.in_(batch))
                .values(indexability_status=estado)
            )
        self.session.flush()

    # -- Sintaxis de las directivas robots -----------------------------------

    def analyze_robots_syntax(self) -> None:
        """Avisar de directivas robots pegadas con un separador invalido.

        ``content="noindex/nofollow"`` no es sintaxis valida: la oficial separa
        por comas. Google ignora lo que no reconoce, asi que esa pagina se
        indexa y sus enlaces se siguen. No se toca la indexabilidad calculada
        --interpretar la barra seria fabricar un bloqueo que el buscador no
        aplica-- pero se reporta, porque el hallazgo es justamente ese: el
        cliente cree tener un bloqueo que no funciona (decision 7 de CLAUDE.md,
        lo roto se reporta, no se filtra).

        La severidad depende de que directiva se este perdiendo: si es
        restrictiva (``noindex``, ``nofollow``...) hay una intencion que no se
        cumple; si es ``index/follow`` no se pierde nada, porque son el
        comportamiento por defecto.
        """
        logger.debug("Analyzing robots directive syntax ...")

        stmt = (
            select(
                Url.id,
                HtmlMeta.meta_robots,
                HtmlMeta.x_robots_tag,
            )
            .join(HtmlMeta, HtmlMeta.url_id == Url.id)
            .where(Url.job_id == self.job_id, Url.is_html.is_(True))
        )

        for url_id, meta_robots, x_robots in self.session.execute(stmt):
            for fuente, valor in (
                ("meta_robots", meta_robots),
                ("x_robots_tag", x_robots),
            ):
                problemas = robots_bad_separators(valor)
                if not problemas:
                    continue

                ignoradas = sorted(
                    {d for p in problemas for d in p["ignoradas"]}
                )
                self._add_issue(
                    url_id,
                    "robots_invalid_syntax",
                    "warning" if ignoradas else "info",
                    {
                        "source": fuente,
                        "raw": valor,
                        "tokens": [p["token"] for p in problemas],
                        "ignored_directives": ignoradas,
                    },
                )

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

    # -- Casi duplicados ----------------------------------------------------

    def analyze_near_duplicates(self) -> None:
        """Detectar paginas que comparten casi todo el texto.

        `analyze_duplicates` solo ve el duplicado byte-identico, que en un
        sitio real casi no existe: basta un precio distinto para que dos fichas
        clonadas dejen de parecerlo. Aqui se mide la similitud real del
        contenido (ver `analysis/near_duplicates.py`).

        Se mide sobre `page_content.content_text` --el contenido sin plantilla--
        y no sobre el body: con cabecera y pie dentro, todo el sitio saldria
        duplicado de todo. Solo se miran las 200 HTML: un 404 o un redirect no
        canibaliza a nadie.
        """
        logger.debug("Analyzing near-duplicate content ...")

        if self.near_duplicate_similarity < nd.UMBRAL_MINIMO_FIABLE:
            # Por debajo de ~0,6 el filtro por bandas empieza a dejarse parejas
            # y "casi duplicado" deja de querer decir gran cosa. Se avisa y se
            # mide igual: el umbral lo pone quien audita.
            logger.warning(
                "Umbral de casi duplicados muy bajo (%.2f): el recuento sera "
                "incompleto y poco significativo",
                self.near_duplicate_similarity,
            )

        stmt = (
            select(Url.id, Url.url, PageContent.content_text)
            .join(PageContent, PageContent.url_id == Url.id)
            .where(
                Url.job_id == self.job_id,
                Url.is_html.is_(True),
                Url.status_code == 200,
            )
        )

        firmas: dict[int, Any] = {}
        urls: dict[int, str] = {}
        # `yield_per` importa: el texto de un censo grande no cabe en memoria de
        # golpe. De cada fila solo se queda la firma (256 bytes) y la URL.
        for url_id, url, texto in self.session.execute(
            stmt.execution_options(yield_per=BATCH_SIZE)
        ):
            f = nd.firma(texto)
            if f is None:
                continue
            firmas[url_id] = f
            urls[url_id] = url

        if len(firmas) < 2:
            return

        resultado = nd.analizar(firmas, self.near_duplicate_similarity)

        # Medida y sin ninguna es 0, no NULL: NULL queda para "no se pudo medir"
        # (sin contenido guardado, o texto por debajo del minimo de palabras).
        sin_duplicados = [uid for uid in firmas if uid not in resultado]
        self._bulk_update_near_duplicates(
            {uid: (0, None) for uid in sin_duplicados}
        )
        self._bulk_update_near_duplicates(
            {uid: (dato["count"], dato["closest"]) for uid, dato in resultado.items()}
        )

        for url_id, dato in resultado.items():
            self._add_issue(
                url_id,
                "near_duplicate_content",
                "warning",
                {
                    "count": dato["count"],
                    "closest_similarity": round(dato["closest"], 3),
                    "threshold": self.near_duplicate_similarity,
                    "matches": [
                        {"url": urls[otro], "similarity": round(s, 3)}
                        for otro, s in dato["ejemplos"]
                    ],
                },
            )

        self._flush_issues()

    def _bulk_update_near_duplicates(
        self, valores: dict[int, tuple[int, float | None]]
    ) -> None:
        """Guardar recuento y mejor similitud por URL, en lotes."""
        if not valores:
            return
        filas = [
            {
                "id": uid,
                "near_duplicate_count": cuenta,
                "closest_similarity": cercana,
            }
            for uid, (cuenta, cercana) in valores.items()
        ]
        for inicio in range(0, len(filas), BATCH_SIZE):
            lote = filas[inicio : inicio + BATCH_SIZE]
            # "ORM bulk UPDATE by primary key": sin WHERE y con `id` en cada
            # fila. Con un WHERE sobre un bindparam, SQLAlchemy 2.x lanza
            # InvalidRequestError y el analisis entero se caia aqui.
            self.session.execute(update(Url), lote)
        self.session.flush()

    # -- Redirect Chains ----------------------------------------------------

    def analyze_redirect_chains(self) -> None:
        """Cadenas de redireccion de dos saltos o mas, y bucles.

        Criterio SEO: una redireccion es normal; dos encadenadas ya son algo que
        arreglar —Google las sigue pero pierde parte de la senal y gasta
        presupuesto de rastreo—, y un bucle es un error. Lo que se reporta es la
        cadena entera, para que se vea donde cortarla.
        """
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
                # Solo cuando falta el atributo. Un alt="" declara la imagen
                # como decorativa y es lo correcto segun WCAG para iconos y
                # adornos: marcarlo como error generaba falsos positivos en
                # masa (medido en un sitio real: 791.455 avisos, el 94% del
                # total de incidencias del rastreo).
                Resource.alt_text.is_(None),
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

        # Only real, served HTML pages qualify: a 404/redirect body having
        # few words is not a "thin content" problem, and flagging it just
        # buries the genuine low-content pages in noise.
        stmt = (
            select(Url.id, Url.word_count, Url.text_ratio)
            .where(
                Url.job_id == self.job_id,
                Url.is_html.is_(True),
                Url.is_internal.is_(True),
                Url.status_code == 200,
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

        # Solo documentos internos, y nunca los saltos de una redireccion.
        #
        # Criterio SEO: estos avisos se arreglan cambiando la URL de una pagina.
        # En un salto de redireccion no hay nada que arreglar —la URL se esta
        # yendo, y su destino ya se revisa por separado—, y en una imagen, un
        # CSS o un JS la forma de la URL no es una decision editorial. Los PDF
        # SI entran: son documentos que Google indexa.
        stmt = (
            select(Url.id, Url.url, Url.path)
            .where(
                Url.job_id == self.job_id,
                Url.is_internal.is_(True),
                Url.resource_type != "redirect",
                or_(Url.is_html.is_(True), Url.resource_type == "pdf"),
            )
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

            # Caracteres no ASCII. Hay que DECODIFICAR antes: Scrapy guarda
            # las URLs escapadas (`caf%C3%A9`), que es puro ASCII, asi que este
            # aviso no saltaba nunca — ni en un censo con URLs en catalan,
            # castellano o arabe.
            if url_str and _NON_ASCII_RE.search(unquote(url_str)):
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

        # A cero antes de agregar. Los cuatro UPDATE de abajo solo tocan filas
        # con coincidencia en su subconsulta, asi que una URL que perdio todos
        # sus inlinks entre dos rastreos conservaba el conteo viejo: salia con
        # 12 inlinks cuando ya no tenia ninguno, y por tanto no salia como
        # huerfana. Un dato rancio que se lee como dato bueno.
        self.session.execute(
            update(Url)
            .where(Url.job_id == self.job_id)
            .values(
                inlinks_count=0,
                unique_inlinks_count=0,
                outlinks_count=0,
                external_outlinks_count=0,
            )
        )

        # --- Inlinks: enlaces internos que apuntan a esta URL ---
        #
        # Criterio SEO: la pregunta es "esta enlazada desde el sitio". Un
        # enlace de una pagina a si misma no responde a eso —ningun buscador
        # lo lee como respaldo— y era ruido medible: 62.326 de los 2.342.192
        # inlinks de blogs.uoc.edu (un 2,7%), concentrados ademas en las
        # paginas con menu o migas que se enlazan a si mismas.
        #
        # Los `nofollow` SI cuentan aqui, a proposito: la pagina esta enlazada
        # (es alcanzable, no es huerfana), solo que sin respaldo. Quien quiera
        # autoridad mira el PageRank, que construye su propio grafo y excluye
        # los nofollow. Son dos preguntas distintas y no caben en un entero.
        #
        # Lo que NO cuenta es un enlace desde una pagina `noindex` (C3 de #24,
        # criterio SEO decidido el 7-oct-2026). Google acaba tratando los
        # enlaces de una noindex como nofollow, asi que una pagina cuyos unicos
        # enlaces vienen de ahi no esta enlazada a efectos de buscador: esta
        # colgando de paginas que el buscador va a dejar de rastrear. Decirlo
        # es el hallazgo; contarla como enlazada lo tapaba.
        # `isnot(True)` y no `is_(False)`: si no se sabe (NULL, porque el
        # analisis de indexabilidad no llego a esa fila) el enlace cuenta. No
        # se descarta dato por desconocimiento.
        origen = aliased(Url)
        inlinks_subq = (
            select(
                Url.id.label("url_id"),
                func.count(Link.id).label("inlinks"),
            )
            .join(Link, and_(
                Link.to_url_hash == Url.url_hash,
                Link.job_id == Url.job_id,
                Link.from_url_id != Url.id,
            ))
            .join(origen, origen.id == Link.from_url_id)
            .where(Url.job_id == self.job_id, origen.noindex.isnot(True))
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
                Link.from_url_id != Url.id,
            ))
            .join(origen, origen.id == Link.from_url_id)
            .where(Url.job_id == self.job_id, origen.noindex.isnot(True))
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

    # La tabla de pesos por posicion vive en analysis/pagerank.py; se deja el
    # alias por compatibilidad con quien la leia desde aqui.
    _POSITION_WEIGHT = prk.PESO_POSICION

    def compute_pagerank(
        self,
        damping: float = 0.85,
        max_iter: int = 100,
        tol: float = 1e-6,
    ) -> None:
        """PageRank interno. El modelo esta en `analysis/pagerank.py`.

        Aristas: enlaces follow internos pesados por su repeticion medida en
        el sitio (la posicion solo desempata), mas salto -> destino en las
        redirecciones, mas variante -> canonical en las paginas
        canonicalizadas. Teletransporte y masa colgante solo entre paginas
        200 indexables. Deja en `jobs.pagerank_resumen` cuanto PageRank acaba
        en cada tipo de URL, y el desperdiciado.

        Las aristas se agregan EN SQL y se iteran vectorizadas con numpy. Con
        139 millones de enlaces internos (un e-commerce real, ~2.300 por pagina
        por los megamenus) cargarlos como objetos de Python eran ~14 GB;
        agregados en el servidor y leidos por bloques quedan en ~240 MB.
        """
        logger.debug("Computing PageRank ...")

        import numpy as np

        # 1. Nodos internos del job, con su categoria (ver prk.categoria)
        url_rows = self.session.execute(
            select(
                Url.id, Url.status_code, Url.is_html, Url.indexable,
                Url.redirect_url.isnot(None),
            ).where(Url.job_id == self.job_id, Url.is_internal.is_(True))
        ).all()
        if not url_rows:
            return
        # Ordenados porque el mapeo id->indice se hace con searchsorted, que
        # exige orden. El indice i corresponde siempre a ids_ordenados[i].
        url_rows.sort(key=lambda r: r[0])
        ids_ordenados = np.array([r[0] for r in url_rows], dtype=np.int64)
        categorias = [
            prk.categoria(r[1], r[2], r[3], "x" if r[4] else None)
            for r in url_rows
        ]
        del url_rows
        n = len(ids_ordenados)

        # 2. Aviso de posiciones sin peso declarado: el CASE las absorberia en
        # el valor por defecto sin ruido, y asi fue como `nav` acabo pesando
        # mas que header y footer.
        conocidas = [p for p in prk.PESO_POSICION if p is not None]
        desconocidas = self.session.execute(
            text(
                """
                SELECT l.link_position, COUNT(*) AS n
                FROM links l
                WHERE l.job_id = :jid AND l.is_internal AND l.follow
                  AND l.link_position IS NOT NULL
                  AND NOT (l.link_position = ANY(:conocidas))
                GROUP BY 1 ORDER BY 2 DESC
                """
            ),
            {"jid": self.job_id, "conocidas": conocidas},
        ).all()
        if desconocidas:
            logger.warning(
                "PageRank: posiciones de enlace sin peso declarado, entran con "
                "el valor por defecto (%s) y pueden sesgar el reparto: %s",
                prk.PESO_POSICION[None],
                ", ".join(f"{p}={c}" for p, c in desconocidas),
            )

        # 3. Aristas de enlaces, agregadas EN EL SERVIDOR a una tabla temporal
        modo_peso = self._aristas_de_enlaces()

        # 4. Redirecciones (3xx y meta refresh): arista salto -> destino con
        # peso completo. Sin ella, el PageRank que llegaba a una 301 quedaba
        # colgante y se repartia entre todas las URLs en vez de pasar a su
        # destino. Se une por la URL (no por hash) porque redirect_url se
        # guarda ya normalizada y asi valen tambien los censos anteriores.
        aristas_redireccion = self.session.execute(text(
            """
            INSERT INTO pr_edges_tmp (src, dst, w)
            SELECT r.id, d.id, 1.0::real
            FROM urls r
            JOIN urls d ON d.job_id = r.job_id AND d.url = r.redirect_url
            WHERE r.job_id = :jid AND r.is_internal AND d.is_internal
              AND r.redirect_url IS NOT NULL AND r.id <> d.id
              AND NOT EXISTS (
                SELECT 1 FROM pr_edges_tmp e WHERE e.src = r.id AND e.dst = d.id
              )
            """
        ), {"jid": self.job_id}).rowcount

        # 5. Canonicals: Google consolida las senales de la variante en la
        # canonica. Antes `?orden=precio`, `/page/2` o `?utm=` retenian su
        # PageRank y lo repartian por su cuenta. Se modela como una
        # redireccion: la variante pierde sus enlaces salientes (son los de la
        # canonica, casi siempre la misma plantilla) y pasa todo a la canonica.
        # Solo si la canonica se rastreo y responde 200: si no, consolidar
        # seria mandar el PageRank a un sitio que no existe.
        self.session.execute(text("DROP TABLE IF EXISTS pr_canon_tmp"))
        self.session.execute(text(
            """
            CREATE TEMP TABLE pr_canon_tmp AS
            SELECT u.id AS src, d.id AS dst
            FROM urls u
            JOIN html_meta m ON m.url_id = u.id
            JOIN urls d ON d.job_id = u.job_id AND d.url = m.canonical_href
            WHERE u.job_id = :jid AND u.is_internal AND d.is_internal
              AND u.indexability_status = 'Canonicalised'
              AND d.status_code = 200 AND d.id <> u.id
            """
        ), {"jid": self.job_id})
        self.session.execute(text(
            "DELETE FROM pr_edges_tmp e USING pr_canon_tmp c WHERE e.src = c.src"
        ))
        aristas_canonical = self.session.execute(text(
            "INSERT INTO pr_edges_tmp (src, dst, w) "
            "SELECT src, dst, 1.0::real FROM pr_canon_tmp"
        )).rowcount
        self.session.execute(text("DROP TABLE IF EXISTS pr_canon_tmp"))

        total = self.session.execute(
            text("SELECT COUNT(*) FROM pr_edges_tmp")
        ).scalar() or 0
        if not total:
            logger.info("PageRank: el job %s no tiene aristas", self.job_id)
            self.session.execute(text("DROP TABLE IF EXISTS pr_edges_tmp"))
            return
        logger.info(
            "PageRank: %d nodos, %d aristas (peso por %s; %d de redireccion, "
            "%d de canonical)",
            n, total, modo_peso, aristas_redireccion, aristas_canonical,
        )

        src = np.empty(total, dtype=np.int32)
        dst = np.empty(total, dtype=np.int32)
        w = np.empty(total, dtype=np.float32)

        cruda = self.session.connection().connection
        cur = cruda.cursor(name="pr_edges_cursor")
        cur.itersize = 1_000_000
        cur.execute("SELECT src, dst, w FROM pr_edges_tmp")
        pos = 0
        while True:
            filas = cur.fetchmany(1_000_000)
            if not filas:
                break
            k = len(filas)
            # fromiter evita construir listas intermedias de 1 M de elementos
            s_bloque = np.fromiter((f[0] for f in filas), dtype=np.int64, count=k)
            d_bloque = np.fromiter((f[1] for f in filas), dtype=np.int64, count=k)
            src[pos:pos + k] = np.searchsorted(ids_ordenados, s_bloque)
            dst[pos:pos + k] = np.searchsorted(ids_ordenados, d_bloque)
            w[pos:pos + k] = np.fromiter(
                (f[2] for f in filas), dtype=np.float32, count=k
            )
            pos += k
            del filas, s_bloque, d_bloque
        cur.close()
        self.session.execute(text("DROP TABLE IF EXISTS pr_edges_tmp"))

        # 6. Iteracion, con el teletransporte solo entre paginas indexables
        indexables = np.array([c == prk.INDEXABLE for c in categorias])
        pr = prk.pagerank(
            n, src, dst, w, teletransporte=indexables,
            damping=damping, max_iter=max_iter, tol=tol,
        )
        reparto = prk.reparto(pr, categorias)
        resumen = {
            "peso": modo_peso,
            "nodos": n,
            "aristas": int(total),
            "aristas_redireccion": int(aristas_redireccion or 0),
            "aristas_canonical": int(aristas_canonical or 0),
            "reparto": reparto,
            "desperdiciado": prk.desperdiciado(reparto),
        }
        # A7 de #24: si la comprobacion automatica vio plantillas que montan
        # sus enlaces con JavaScript, el grafo esta incompleto y el PageRank se
        # calcula igual. Antes solo quedaba un WARNING en el log del worker, y
        # el numero se entregaba como si nada. Ahora el aviso viaja CON el
        # dato: en jobs.pagerank_resumen, en el endpoint del job y en una
        # columna del CSV, para que quien ordene por PageRank en una hoja de
        # calculo lo vea.
        resumen.update(self._fiabilidad_del_grafo())
        logger.info(
            "PageRank: %.1f%% en paginas indexables, %.1f%% desperdiciado en "
            "errores (job %s)",
            reparto[prk.INDEXABLE] * 100, resumen["desperdiciado"] * 100,
            self.job_id,
        )

        # 7. Tres escalas, porque son tres preguntas (C4 de #24)
        #
        # La de siempre, 0-10 lineal, no distingue la pagina 250 de la 25.000:
        # la home vale 10 porque es el maximo y el resto del sitio se apelotona
        # en 0,00xx. Medido en blogs.uoc.edu: 33.956 de 34.704 paginas por
        # debajo de 0,1, o sea el 97,8% indistinguible entre si.
        #
        # `pagerank_raw` es la probabilidad tal cual (suma 1): por el numero de
        # nodos da "veces la pagina media", que si se puede comparar entre
        # sitios de tamaño distinto. `pagerank_score` es 0-100 en logaritmico
        # —el equivalente al Link Score de Screaming Frog— y es la que hay que
        # leer: reparte la cola larga en vez de aplastarla.
        crudo = pr.copy()
        puntuacion = prk.puntuacion_log(crudo)

        maximo = pr.max()
        if maximo > 0:
            pr = pr / maximo * 10.0

        # 8. Guardado en bloque. Antes se emitia una UPDATE por URL: en un job
        # de 60.000 paginas eran 60.000 consultas sueltas.
        self.session.execute(text(
            "CREATE TEMP TABLE pr_tmp (id BIGINT PRIMARY KEY, pr DOUBLE PRECISION, "
            "crudo DOUBLE PRECISION, score INTEGER) ON COMMIT DROP"
        ))
        filas = [{"id": int(i), "pr": round(float(v), 4),
                  "crudo": float(c), "score": int(sc)}
                 for i, v, c, sc in zip(ids_ordenados, pr, crudo, puntuacion)]
        for i in range(0, len(filas), 10_000):
            self.session.execute(
                text("INSERT INTO pr_tmp (id, pr, crudo, score) "
                     "VALUES (:id, :pr, :crudo, :score)"),
                filas[i:i + 10_000],
            )
        self.session.execute(text(
            "UPDATE urls SET pagerank = pr_tmp.pr, pagerank_raw = pr_tmp.crudo, "
            "pagerank_score = pr_tmp.score FROM pr_tmp WHERE urls.id = pr_tmp.id"
        ))
        from shared.models import Job
        self.session.execute(
            update(Job).where(Job.id == self.job_id).values(pagerank_resumen=resumen)
        )
        self.session.flush()
        logger.info("PageRank computed for %d URLs (job %s)", n, self.job_id)

    def _fiabilidad_del_grafo(self) -> dict:
        """Dice si el grafo de enlaces es fiable, segun la comprobacion de render.

        `jobs.js_check` lo rellena `scripts/check_js_templates.py` al cerrar un
        rastreo SIN `render_js`: si alguna plantilla esconde enlaces en
        JavaScript, el grafo esta incompleto y el PageRank sale de un grafo
        parcial. NULL = no se llego a comprobar (p. ej. el rastreo ya iba con
        render), y entonces no se afirma nada.
        """
        from shared.models import Job

        js_check = self.session.execute(
            select(Job.js_check).where(Job.id == self.job_id)
        ).scalar()
        if not isinstance(js_check, dict):
            return {"grafo_fiable": None}
        fiable = js_check.get("grafo_fiable")
        if fiable is False:
            plantillas = [
                p.get("plantilla")
                for p in (js_check.get("plantillas") or [])
                if p.get("enlaces_solo_js")
            ]
            return {
                "grafo_fiable": False,
                "aviso": (
                    "PageRank calculado sin render: hay plantillas con enlaces "
                    "solo en JavaScript, asi que el grafo esta incompleto. "
                    "Re-rastrear con render_js=true antes de fiarse de estas "
                    "cifras."
                ),
                "plantillas_con_enlaces_js": [p for p in plantillas if p][:10],
            }
        return {"grafo_fiable": bool(fiable)}

    def _aristas_de_enlaces(self) -> str:
        """Crea `pr_edges_tmp` con las aristas de `links`. Devuelve el modo.

        Peso por repeticion medida (ver `analysis/pagerank.py`): la fraccion
        de paginas de origen que llevan el mismo enlace, destino + anchor, en
        todo el sitio y en su seccion (host + primer segmento); cuenta la
        mayor. Si hay menos de MIN_FUENTES_REPETICION paginas con enlaces, la
        fraccion es ruido y se usa el peso por posicion.
        """
        jid = {"jid": self.job_id}
        temporales = ("pr_fuentes_tmp", "pr_lk_tmp", "pr_rep_tmp", "pr_sec_tmp")
        for tabla in ("pr_edges_tmp",) + temporales:
            self.session.execute(text(f"DROP TABLE IF EXISTS {tabla}"))

        self.session.execute(text(
            r"""
            CREATE TEMP TABLE pr_fuentes_tmp AS
            SELECT u.id,
                   COALESCE(substring(u.url FROM '^https?://[^/?#]+(?:/[^/?#]*)?'), '')
                       AS sec
            FROM urls u
            WHERE u.job_id = :jid AND EXISTS (
                SELECT 1 FROM links l
                WHERE l.from_url_id = u.id AND l.job_id = :jid
                  AND l.is_internal AND l.follow
            )
            """
        ), jid)
        self.session.execute(text("ANALYZE pr_fuentes_tmp"))
        n_fuentes = self.session.execute(
            text("SELECT COUNT(*) FROM pr_fuentes_tmp")
        ).scalar() or 0

        anchor = "COALESCE(lower(btrim(l.anchor_text)), '')"
        if n_fuentes < prk.MIN_FUENTES_REPETICION:
            self.session.execute(text(
                f"""
                CREATE TEMP TABLE pr_edges_tmp AS
                SELECT l.from_url_id AS src, u.id AS dst,
                       MAX({prk.sql_peso_posicion('l.link_position')})::real AS w
                FROM links l
                JOIN urls u ON u.url_hash = l.to_url_hash AND u.job_id = l.job_id
                WHERE l.job_id = :jid AND l.is_internal AND l.follow
                  AND u.is_internal AND l.from_url_id <> u.id
                GROUP BY 1, 2
                """
            ), jid)
            self.session.execute(text("DROP TABLE IF EXISTS pr_fuentes_tmp"))
            return "posicion"

        # Los enlaces, UNA vez, con el anchor y la seccion ya calculados. Unir
        # por expresiones (`lower(btrim(anchor))`) dejaba al planificador sin
        # estimacion: suponia 1 fila, elegia bucles anidados y recorria la
        # tabla de fuentes 462.452 veces -- 116 s en Lopesan (150 k enlaces).
        self.session.execute(text(
            f"""
            CREATE TEMP TABLE pr_lk_tmp AS
            SELECT l.from_url_id AS src, l.to_url_hash AS h, {anchor} AS anc,
                   l.link_position AS pos, f.sec
            FROM links l JOIN pr_fuentes_tmp f ON f.id = l.from_url_id
            WHERE l.job_id = :jid AND l.is_internal AND l.follow
            """
        ), jid)
        self.session.execute(text("ANALYZE pr_lk_tmp"))
        # Cuantas paginas de cada seccion llevan el enlace (c) y cuantas del
        # sitio (cg). Cada pagina esta en una sola seccion, asi que el total
        # del sitio es la suma de sus secciones.
        self.session.execute(text(
            """
            CREATE TEMP TABLE pr_rep_tmp AS
            SELECT h, anc, sec, c, SUM(c) OVER (PARTITION BY h, anc) AS cg
            FROM (
                SELECT h, anc, sec, COUNT(DISTINCT src) AS c
                FROM pr_lk_tmp GROUP BY 1, 2, 3
            ) x
            """
        ))
        self.session.execute(text(
            "CREATE TEMP TABLE pr_sec_tmp AS "
            "SELECT sec, COUNT(*) AS n FROM pr_fuentes_tmp GROUP BY 1"
        ))
        for tabla in ("pr_rep_tmp", "pr_sec_tmp"):
            self.session.execute(text(f"ANALYZE {tabla}"))

        repeticion = (
            "GREATEST(r.cg::float8 / :n_fuentes, "
            "CASE WHEN sn.n >= :min_sec THEN r.c::float8 / sn.n ELSE 0.0 END)"
        )
        self.session.execute(text(
            f"""
            CREATE TEMP TABLE pr_edges_tmp AS
            SELECT lk.src, u.id AS dst,
                   MAX({prk.sql_peso_arista(repeticion, 'lk.pos')})::real AS w
            FROM pr_lk_tmp lk
            JOIN pr_rep_tmp r ON r.h = lk.h AND r.anc = lk.anc AND r.sec = lk.sec
            JOIN pr_sec_tmp sn ON sn.sec = lk.sec
            JOIN urls u ON u.job_id = :jid AND u.url_hash = lk.h
            WHERE u.is_internal AND lk.src <> u.id
            GROUP BY 1, 2
            """
        ), {**jid, "n_fuentes": n_fuentes, "min_sec": prk.MIN_FUENTES_SECCION})
        for tabla in temporales:
            self.session.execute(text(f"DROP TABLE IF EXISTS {tabla}"))
        return "repeticion"

    # -- Link Analysis ------------------------------------------------------

    def analyze_links(self) -> None:
        """Link analysis -- flag orphan pages and pages with excessive outlinks."""
        logger.debug("Analyzing links ...")

        # Orphan pages: internal HTML pages with 0 inlinks. Seed pages
        # (crawl_depth 0, e.g. the homepage) legitimately have no discovered
        # inlinks and must not be flagged as orphans. Only 200-OK internal
        # pages are considered — a 404/redirect having no inlinks is not an
        # "orphan" in the SEO sense.
        #
        # Tampoco el destino de una redireccion interna: los enlaces apuntan a
        # la URL vieja, asi que el destino tiene 0 inlinks y salia huerfano.
        # Toda migracion o paso de http a https llenaba el informe de
        # huerfanas falsas.
        stmt = (
            select(Url.id)
            .where(
                Url.job_id == self.job_id,
                Url.is_html.is_(True),
                Url.is_internal.is_(True),
                Url.status_code == 200,
                (Url.crawl_depth.is_(None)) | (Url.crawl_depth > 0),
                (Url.inlinks_count.is_(None)) | (Url.inlinks_count == 0),
                ~_es_destino_de_redireccion(),
                # Las que estan en el sitemap las cuenta `analyze_sitemap` como
                # `sitemap_orphan`, que dice lo mismo pero mas fuerte: esta
                # declarada para indexar y aun asi nadie la enlaza. Emitir los
                # dos avisos sobre la misma URL es ruido, y hacia que el
                # informe pareciera tener el doble de problemas.
                (Url.in_sitemap.isnot(True)),
            )
        )
        rows = self.session.execute(stmt).all()

        for (url_id,) in rows:
            self._add_issue(url_id, "orphan_page", "warning")

        # Demasiados enlaces salientes, contando solo los EDITORIALES.
        #
        # Criterio SEO: lo que preocupa de una pagina con cientos de enlaces es
        # que diluya el presupuesto de rastreo y el reparto de autoridad entre
        # sus enlaces propios. Un megamenu de 500 entradas repetido en todo el
        # sitio no es eso: es un hecho de la plantilla, igual en todas las
        # paginas, y no dice nada de ninguna. Por eso se cuentan destinos
        # DISTINTOS en `content` y no instancias en toda la pagina.
        #
        # Medido en blogs.uoc.edu (34.704 URLs): con `outlinks_count` avisaban
        # 7.011 paginas, el 24% de las HTML; deduplicando, 6.578 —apenas
        # ayuda, porque lo que infla es la plantilla—; contando solo contenido,
        # 51. El 99,3% del aviso era ruido que el cliente leia como problemas.
        # Se filtra por `links.job_id` y NO uniendo con `urls`: la tabla de
        # enlaces guarda los de TODOS los rastreos —46 GB y 181 millones de
        # filas en esta instalacion— y poner el job en el lado de `urls` deja
        # el filtro fuera del alcance del indice. Medido con EXPLAIN: recorrido
        # secuencial de la tabla entera, coste 6.189.456, frente a 122.247
        # filtrando aqui. Cincuenta veces.
        enlaces_de_contenido = (
            select(
                Link.from_url_id.label("url_id"),
                func.count(func.distinct(Link.to_url_hash)).label("destinos"),
            )
            .where(
                Link.job_id == self.job_id,
                Link.is_internal.is_(True),
                Link.link_position == "content",
                # Un nofollow no reparte autoridad: no diluye nada.
                Link.follow.is_(True),
            )
            .group_by(Link.from_url_id)
            .having(func.count(func.distinct(Link.to_url_hash)) > self.max_outlinks)
        )

        for url_id, destinos in self.session.execute(enlaces_de_contenido).all():
            self._add_issue(
                url_id,
                "high_outlink_count",
                "info",
                {"count": destinos, "criterio": "destinos distintos en contenido"},
            )

        self._flush_issues()

    # -- Sitemap --------------------------------------------------------------

    def analyze_sitemap(self) -> None:
        """Sitemap coverage checks (Screaming Frog "Sitemaps" tab parity).

        Runs only when the crawl ingested a sitemap (some ``in_sitemap`` is
        True); otherwise all rows are NULL and there is nothing to compare.

        - ``sitemap_orphan``: URL listed in the sitemap but with zero internal
          inlinks — only reachable through the sitemap. Stronger signal than
          plain ``orphan_page``.
        - ``not_in_sitemap``: indexable internal 200 HTML page missing from
          the sitemap — should probably be listed.
        """
        logger.debug("Analyzing sitemap coverage ...")

        has_sitemap = self.session.execute(
            select(func.count(Url.id)).where(
                Url.job_id == self.job_id, Url.in_sitemap.is_(True),
            )
        ).scalar() or 0
        if not has_sitemap:
            return

        # In sitemap but not linked internally (run after compute_link_counts).
        rows = self.session.execute(
            select(Url.id).where(
                Url.job_id == self.job_id,
                Url.in_sitemap.is_(True),
                Url.is_html.is_(True),
                Url.status_code == 200,
                (Url.crawl_depth.is_(None)) | (Url.crawl_depth > 0),
                (Url.inlinks_count.is_(None)) | (Url.inlinks_count == 0),
                ~_es_destino_de_redireccion(),
            )
        ).all()
        for (url_id,) in rows:
            self._add_issue(url_id, "sitemap_orphan", "warning")

        # Indexable page the sitemap forgot.
        rows = self.session.execute(
            select(Url.id).where(
                Url.job_id == self.job_id,
                Url.in_sitemap.is_(False),
                Url.is_internal.is_(True),
                Url.is_html.is_(True),
                Url.status_code == 200,
                Url.indexable.is_(True),
            )
        ).all()
        for (url_id,) in rows:
            self._add_issue(url_id, "not_in_sitemap", "info")

        self._flush_issues()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _es_destino_de_redireccion():
    """EXISTS: la fila de `Url` es el destino de una redireccion interna.

    Los enlaces apuntan a la URL vieja, asi que el destino tiene 0 inlinks
    propios aunque este perfectamente enlazado a traves del salto.
    """
    origen = aliased(Url)
    return (
        select(origen.id)
        .where(
            origen.job_id == Url.job_id,
            origen.redirect_url == Url.url,
            origen.is_internal.is_(True),
        )
        .exists()
    )

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _clave_candado(job_id: str) -> int:
    """Clave estable de 63 bits para el advisory lock, a partir del UUID."""
    import hashlib

    return int.from_bytes(hashlib.sha1(str(job_id).encode()).digest()[:8], "big") & 0x7FFFFFFFFFFFFFFF


@contextmanager
def candado_de_job(job_id: str):
    """Serializa el analisis de un job. Cede True si se ha tomado el candado.

    Dos analisis del mismo job a la vez se pisan en el DELETE+INSERT de
    `issues` y DUPLICAN filas: medido en v2-experimental, 1.390 incidencias
    distintas acabaron como 4.170 filas. Pasa de verdad — un resume que
    reencola el job mientras el analisis anterior sigue vivo, o dos
    lanzamientos a mano.

    Tres detalles que parecen de fontaneria y son justo donde falla:

    1. La conexion es DEDICADA, no la sesion del analisis. Un advisory lock de
       sesion se suelta en la conexion que lo pidio, y la sesion del analyzer
       hace docenas de commits que la devuelven al pool: el unlock podia caer
       en otra conexion y fallar en silencio.
    2. El motor es `NullPool`, para que cerrar cierre DE VERDAD. Con el pool
       normal, `close()` solo devuelve la conexion y la sesion de Postgres
       sigue viva con el candado puesto.
    3. UN SOLO `yield`, y el `except` del montaje no lo envuelve. La primera
       version tenia el yield dentro de un try/except amplio: cuando el
       analisis de dentro reventaba, la excepcion entraba por el yield, la
       cazaba ese except y se cedia por segunda vez, asi que Python lanzaba
       `generator didn't stop after throw()` y el error ORIGINAL desaparecia.
       Medido: el analisis de un censo de 60.399 URLs fallo y el log solo
       decia eso. Un candado no puede tragarse los errores de lo que protege.

    En SQLite no hay advisory locks: cede True sin serializar (los tests del
    analyzer corren ahi).
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    from shared.database import engine as motor_app

    motor = conexion = None
    clave = _clave_candado(job_id)
    es_postgres = False
    tomado = True
    try:
        motor = create_engine(motor_app.url, poolclass=NullPool)
        conexion = motor.connect()
        es_postgres = conexion.dialect.name.startswith("postgres")
        if es_postgres:
            tomado = bool(
                conexion.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": clave}
                ).scalar()
            )
            if not tomado:
                logger.warning(
                    "El job %s ya se esta analizando: se omite esta ejecucion "
                    "para no duplicar incidencias", job_id,
                )
    except Exception:
        logger.debug("Sin candado disponible; el analisis sigue sin serializar")
        tomado = True
        es_postgres = False

    try:
        yield tomado
    finally:
        if conexion is not None:
            if es_postgres and tomado:
                try:
                    conexion.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": clave})
                except Exception:
                    pass  # al cerrar la conexion se suelta igual
            conexion.close()
        if motor is not None:
            motor.dispose()


def run_analysis(job_id: str) -> None:
    """Entry point called by the crawler worker after a crawl completes.

    Creates its own database session, runs every analysis check, and
    ensures the session is closed on exit. Serializado por job: ver
    ``candado_de_job``.
    """
    with candado_de_job(job_id) as puedo:
        if not puedo:
            return
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
