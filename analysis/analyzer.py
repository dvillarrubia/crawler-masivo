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
from urllib.parse import urljoin, urlparse

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
    PageContent,
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
# PageRank power iteration (T17.1)
# ---------------------------------------------------------------------------

# Above this node count the dict-based loop becomes unviable (the 1M-URL
# target of millones-de-URL.md); switch to a scipy.sparse CSR matrix.
SPARSE_PAGERANK_THRESHOLD = 50_000


def run_power_iteration(
    n: int,
    edge_weight: dict[tuple[int, int], float],
    out_total: dict[int, float],
    damping: float,
    max_iter: int,
    tol: float,
    force: str | None = None,
) -> list[float]:
    """Shared PageRank power iteration (T17.1).

    Semantics identical to the historical in-place loop: uniform start,
    dangling nodes (zero out-weight) redistribute evenly, max-abs-diff
    convergence. Dispatches to a vectorized scipy.sparse path when
    ``n > SPARSE_PAGERANK_THRESHOLD`` (or ``force='sparse'``); falls back
    to pure Python when scipy is unavailable. Numerical equivalence
    between both paths is locked by tests (tolerance 1e-6).
    """
    use_sparse = force == "sparse" or (
        force is None and n > SPARSE_PAGERANK_THRESHOLD
    )
    if use_sparse and force != "python":
        try:
            return _power_iteration_sparse(
                n, edge_weight, out_total, damping, max_iter, tol,
            )
        except ImportError:
            logger.warning(
                "scipy no disponible: PageRank con bucle Python para %d nodos",
                n,
            )
    return _power_iteration_python(
        n, edge_weight, out_total, damping, max_iter, tol,
    )


def _power_iteration_python(
    n, edge_weight, out_total, damping, max_iter, tol,
) -> list[float]:
    outlinks: dict[int, dict[int, float]] = defaultdict(dict)
    for (src, dst), w in edge_weight.items():
        outlinks[src][dst] = w

    pr = [1.0 / n] * n
    for _ in range(max_iter):
        new_pr = [(1.0 - damping) / n] * n
        for i in range(n):
            total_w = out_total.get(i, 0.0)
            if total_w > 0:
                for j, w in outlinks[i].items():
                    new_pr[j] += damping * pr[i] * (w / total_w)
        dangling_sum = sum(
            pr[i] for i in range(n) if out_total.get(i, 0.0) == 0
        )
        dangling_add = damping * dangling_sum / n
        new_pr = [p + dangling_add for p in new_pr]

        diff = max(abs(new_pr[i] - pr[i]) for i in range(n))
        pr = new_pr
        if diff < tol:
            break
    return pr


def _power_iteration_sparse(
    n, edge_weight, out_total, damping, max_iter, tol,
) -> list[float]:
    import numpy as np
    from scipy.sparse import csr_matrix

    rows, cols, data = [], [], []
    for (src, dst), w in edge_weight.items():
        total = out_total.get(src, 0.0)
        if total > 0:
            rows.append(dst)
            cols.append(src)
            data.append(w / total)
    matrix = csr_matrix((data, (rows, cols)), shape=(n, n))

    dangling = np.array(
        [1.0 if out_total.get(i, 0.0) == 0 else 0.0 for i in range(n)]
    )

    pr = np.full(n, 1.0 / n)
    base = (1.0 - damping) / n
    for _ in range(max_iter):
        new_pr = base + damping * (matrix @ pr) + damping * (dangling @ pr) / n
        diff = float(np.max(np.abs(new_pr - pr)))
        pr = new_pr
        if diff < tol:
            break
    return pr.tolist()


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
        # T14: near duplicates
        self.near_duplicate_detection = t.get("near_duplicate_detection", "off")
        self.near_duplicate_hamming = t.get("near_duplicate_hamming", 3)
        # T6: soft 404
        self.soft404_similarity = t.get("soft404_similarity", 0.85)
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
        self.analyze_near_duplicates()
        self.analyze_soft_404()
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
        self.analyze_crawl_traps()
        self.analyze_freshness()
        self.analyze_gsc_signals()
        self.analyze_geo()
        self.analyze_architecture()
        self.analyze_unique_content()

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
        """Validate hreflang annotations, incluida la RECIPROCIDAD.

        Rellena `return_tag_ok` y `lang_valid` (antes siempre NULL, por eso
        Insights → i18n salía "sin validar"):

        - `lang_valid`: el código de idioma cumple BCP-47 (regex).
        - `return_tag_ok` (reciprocidad, tres estados honestos):
            * True  → el destino, rastreado, declara un hreflang de vuelta a
                      esta URL (o es una autorreferencia).
            * False → el destino se rastreó pero NO enlaza de vuelta.
            * None  → el destino no se rastreó: no se puede confirmar (no se
                      inventa un veredicto).

        Los hrefs se resuelven a absoluto contra la URL de origen (pueden ser
        relativos) y se normalizan para casar con las URLs rastreadas.
        """
        logger.debug("Analyzing hreflang ...")

        def _norm(u: str) -> str:
            """Normalización ligera para casar targets con URLs rastreadas:
            esquema+host en minúsculas, sin fragmento, sin barra final."""
            try:
                p = urlparse(u)
            except Exception:
                return u.strip()
            netloc = p.netloc.lower()
            path = p.path.rstrip("/") or "/"
            base = f"{p.scheme.lower()}://{netloc}{path}"
            return base + (f"?{p.query}" if p.query else "")

        # URLs rastreadas del job: norm → (url_id, status_code) y url_id → norm
        crawled_id: dict[str, int] = {}
        crawled_status: dict[int, int | None] = {}
        id_to_norm: dict[int, str] = {}
        for uid, u, sc in self.session.execute(
            select(Url.id, Url.url, Url.status_code).where(Url.job_id == self.job_id)
        ).all():
            n = _norm(u)
            crawled_id.setdefault(n, uid)
            crawled_status[uid] = sc
            id_to_norm[uid] = n

        # Todas las filas hreflang del job, con la URL de origen para resolver.
        rows = self.session.execute(
            select(
                Hreflang.id, Hreflang.url_id, Hreflang.lang, Hreflang.href, Url.url,
            )
            .join(Url, Url.id == Hreflang.url_id)
            .where(Url.job_id == self.job_id)
        ).all()

        # Paso 1: resolver cada href a absoluto+normalizado y construir el
        # mapa de "qué declara cada URL de origen" (para la reciprocidad).
        resolved: list[tuple] = []  # (hid, src_id, lang, href, target_norm)
        declares: dict[int, set[str]] = defaultdict(set)
        for hid, src_id, lang, href, src_url in rows:
            target_norm = _norm(urljoin(src_url, href))
            resolved.append((hid, src_id, lang, href, target_norm))
            declares[src_id].add(target_norm)

        # Paso 2: veredicto por fila + issues.
        updates: list[dict] = []
        for hid, src_id, lang, href, target_norm in resolved:
            lang_valid = bool(_LANG_TAG_RE.match(lang))

            src_norm = id_to_norm.get(src_id)
            target_id = crawled_id.get(target_norm)
            if target_norm == src_norm:
                return_tag_ok = True                     # autorreferencia
            elif target_id is None:
                return_tag_ok = None                     # destino no rastreado
            else:
                return_tag_ok = src_norm in declares.get(target_id, set())

            updates.append({"id": hid, "return_tag_ok": return_tag_ok,
                            "lang_valid": lang_valid})

            if return_tag_ok is False:
                self._add_issue(src_id, "hreflang_missing_return", "warning",
                                {"lang": lang, "href": href, "target": target_norm})
            if not lang_valid:
                self._add_issue(src_id, "hreflang_invalid_lang", "warning",
                                {"lang": lang})

            # Destino rastreado pero sin 200 (roto). Solo si lo conocemos.
            if target_id is not None:
                st = crawled_status.get(target_id)
                if st is not None and st != 200:
                    self._add_issue(src_id, "hreflang_broken_target", "error",
                                    {"href": href, "target": target_norm,
                                     "target_status": st})

        if updates:
            self.session.bulk_update_mappings(Hreflang, updates)
            self.session.flush()
        self._flush_issues()

    # -- Structured Data ----------------------------------------------------

    def analyze_structured_data(self) -> None:
        """Valida los datos estructurados contra los requisitos de rich
        result de Google y emite errores/warnings.

        Antes leía `validation_status`/`validation_issues` pero NADIE las
        rellenaba (siempre NULL), así que estos issues no saltaban nunca.
        Ahora se calcula la validación aquí (por tipo de schema) sobre el
        `raw` extraído, se persiste y se emiten los issues con el detalle de
        qué campos faltan."""
        logger.debug("Analyzing structured data ...")

        from analysis.rich_results import validate_rich_result

        stmt = (
            select(
                StructuredData.id,
                StructuredData.url_id,
                StructuredData.schema_type,
                StructuredData.raw,
                StructuredData.validation_status,
                StructuredData.validation_issues,
            )
            .join(Url, Url.id == StructuredData.url_id)
            .where(Url.job_id == self.job_id)
        )
        rows = self.session.execute(stmt).all()

        updates: list[dict] = []
        for sd_id, url_id, schema_type, raw, validation_status, validation_issues in rows:
            status, issues = validate_rich_result(schema_type, raw)
            # persistimos lo calculado (idempotente en re-análisis)
            updates.append({"id": sd_id, "validation_status": status,
                            "validation_issues": issues})

            if status == "error":
                self._add_issue(
                    url_id, "structured_data_error", "error",
                    {"schema_type": schema_type, "validation_issues": issues},
                )
            elif status == "warning":
                self._add_issue(
                    url_id, "structured_data_warning", "warning",
                    {"schema_type": schema_type, "validation_issues": issues},
                )

        if updates:
            self.session.bulk_update_mappings(StructuredData, updates)
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

        # Total weight per source node
        out_total_weight: dict[int, float] = defaultdict(float)
        for (src, _dst), w in edge_weight.items():
            out_total_weight[src] += w

        # 3. Weighted power method (T17.1: sparse path above the threshold)
        pr = run_power_iteration(
            n, edge_weight, out_total_weight, damping, max_iter, tol,
        )

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

        # Power iteration (T17.1 shared). The denominator is the TOTAL
        # emitted weight, so nofollow/leaked fractions vanish instead of
        # being redistributed; true dangling nodes still redistribute.
        pr = run_power_iteration(
            n, edge_weight, out_total, damping, max_iter, tol,
        )

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

        # Páginas con demasiados enlaces salientes DESDE EL CONTENIDO.
        # Antes se contaban TODAS las filas Link internas (incluido el
        # menú/cabecera/pie/sidebar), que son sitewide e iguales en todas
        # las páginas: eso penalizaba cada página por tener el mega-menú del
        # sitio (falso positivo real reportado). Ahora se cuentan solo los
        # enlaces del CONTENIDO (link_position='content'); el total se
        # conserva en el detalle para contexto.
        content_counts = self.session.execute(
            select(Link.from_url_id, func.count(Link.id))
            .join(Url, Url.id == Link.from_url_id)
            .where(
                Url.job_id == self.job_id,
                Link.is_internal.is_(True),
                Link.link_position == "content",
            )
            .group_by(Link.from_url_id)
        ).all()

        totals = dict(
            self.session.execute(
                select(Url.id, Url.outlinks_count).where(Url.job_id == self.job_id)
            ).all()
        )

        for url_id, n_content in content_counts:
            if n_content > self.max_outlinks:
                self._add_issue(
                    url_id,
                    "high_outlink_count",
                    "info",
                    {"count": n_content, "content_links": n_content,
                     "total_outlinks": totals.get(url_id)},
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

        # Defensa en profundidad: además del guard del endpoint, se salta
        # aquí cualquier regex legada con cuantificador anidado (ReDoS)
        # antes de que cuelgue el análisis entero sobre una URL adversaria.
        _nested = re.compile(r"\([^)]*[+*][^)]*\)[+*{]")
        matchers: list[tuple[int, Any]] = []
        for seg in segments:
            if seg.rule_type == "regex":
                if len(seg.rule) > 500 or _nested.search(seg.rule):
                    logger.warning(
                        "Segment %s tiene una regex peligrosa/larga, se salta: %r",
                        seg.name, seg.rule,
                    )
                    continue
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

        # 1b. T9: GSC URLs the crawl never saw are orphan candidates too.
        # semantic_models needs pgvector; the worker image may not ship it,
        # so this source degrades gracefully.
        gsc_hashes: set[str] = set()
        try:
            from shared.semantic_models import GscJobData

            gsc_hashes = {
                h for (h,) in self.session.execute(
                    select(GscJobData.url_hash).where(
                        GscJobData.job_id == self.job_id,
                        GscJobData.url_hash.isnot(None),
                    )
                ).all()
            }
            gsc_missing = self.session.execute(
                select(GscJobData.url, GscJobData.url_hash)
                .where(
                    GscJobData.job_id == self.job_id,
                    GscJobData.url_id.is_(None),
                    GscJobData.url.isnot(None),
                    ~select(Url.id)
                    .where(
                        Url.job_id == self.job_id,
                        Url.url_hash == GscJobData.url_hash,
                    )
                    .exists(),
                )
                .distinct()
            ).all()
            for url, url_hash in gsc_missing:
                parsed = _urlparse(url)
                self.session.add(Url(
                    job_id=self.job_id,
                    url=url,
                    url_hash=url_hash,
                    host=parsed.hostname,
                    path=parsed.path or None,
                    scheme=parsed.scheme or None,
                    is_internal=True,
                    is_html=False,
                    status_group="not_crawled",
                ))
            if gsc_missing:
                self.session.flush()
                missing = True  # force the counter-NULL safeguard below
        except ImportError:
            pass

        if missing:
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
            select(Url.id, Url.url_hash, Url.sitemap_lastmod,
                   SitemapUrl.id.isnot(None))
            .outerjoin(SitemapUrl, and_(
                SitemapUrl.job_id == Url.job_id,
                SitemapUrl.url_hash == Url.url_hash,
            ))
            .where(
                Url.job_id == self.job_id,
                Url.status_group == "not_crawled",
            )
        ).all()

        for url_id, url_hash, lastmod, in_sitemap in rows:
            seen_in = (["sitemap"] if in_sitemap else []) + (
                ["gsc"] if url_hash in gsc_hashes else []
            )
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


    # -- Unique content (T20) -----------------------------------------------------

    _SHINGLE_SIZE = 5

    def analyze_unique_content(self) -> None:
        """T20: thin content measured RELATIVE to the template.

        Per segment (T12): word 5-gram shingles present in more than
        ``boilerplate_shingle_share`` of the segment's pages form the
        segment's boilerplate set. Per page: ``unique_word_count`` (words
        in shingles NOT in that set) and ``boilerplate_ratio``. Emits
        ``low_unique_content`` below ``min_unique_word_count``. The
        existing ``low_word_count`` is untouched. Gated by
        ``analysis_thresholds.unique_content_analysis``.
        """
        t = ((self._job.config or {}).get("analysis_thresholds", {})
             if self._job else {})
        if not t.get("unique_content_analysis"):
            return

        from shared.models import UrlSegment

        share = t.get("boilerplate_shingle_share", 0.3)
        min_unique = t.get("min_unique_word_count", 100)
        k = self._SHINGLE_SIZE

        logger.debug("Analyzing unique content ...")

        seg_by_url = dict(self.session.execute(
            select(UrlSegment.url_id, UrlSegment.segment_id)
            .where(UrlSegment.job_id == self.job_id)
        ).all())

        rows = self.session.execute(
            select(Url.id, PageContent.content_text)
            .join(PageContent, PageContent.url_id == Url.id)
            .where(
                Url.job_id == self.job_id,
                Url.is_internal.is_(True),
                Url.is_html.is_(True),
                Url.status_code == 200,
                PageContent.content_text.isnot(None),
            )
        ).all()
        if not rows:
            return

        def _shingles(text: str) -> list[tuple]:
            words = re.findall(r"\w+", text.lower())
            if len(words) < k:
                return [tuple(words)] if words else []
            return [tuple(words[i:i + k]) for i in range(len(words) - k + 1)]

        # shingle presence per segment (segment 0 = unsegmented)
        page_shingles: dict[int, list[tuple]] = {}
        seg_pages: dict[int, list[int]] = defaultdict(list)
        seg_shingle_pages: dict[int, dict[tuple, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        for url_id, text in rows:
            sh = _shingles(text or "")
            page_shingles[url_id] = sh
            seg = seg_by_url.get(url_id, 0) or 0
            seg_pages[seg].append(url_id)
            for s in set(sh):
                seg_shingle_pages[seg][s] += 1

        boilerplate: dict[int, set[tuple]] = {}
        for seg, counts in seg_shingle_pages.items():
            n = len(seg_pages[seg])
            if n < 2:
                boilerplate[seg] = set()
                continue
            boilerplate[seg] = {
                s for s, c in counts.items() if c / n > share
            }

        for url_id, sh in page_shingles.items():
            seg = seg_by_url.get(url_id, 0) or 0
            bp = boilerplate.get(seg, set())
            total = len(sh)
            if total == 0:
                continue
            bp_hits = sum(1 for s in sh if s in bp)
            ratio = bp_hits / total
            # unique words ≈ words covered only by non-boilerplate shingles
            total_words = total + k - 1 if sh and len(sh[0]) == k else total
            unique_words = max(0, round(total_words * (1 - ratio)))
            self.session.execute(
                update(Url).where(Url.id == url_id).values(
                    unique_word_count=unique_words,
                    boilerplate_ratio=round(ratio, 4),
                )
            )
            if unique_words < min_unique:
                self._add_issue(url_id, "low_unique_content", "warning", {
                    "unique_word_count": unique_words,
                    "boilerplate_ratio": round(ratio, 4),
                    "threshold": min_unique,
                })
        self.session.flush()
        self._flush_issues()

    # -- Architecture (T22/T23) --------------------------------------------------

    def analyze_architecture(self) -> None:
        """T22 + T23: edge classifier, real click depth, section flows and
        the deterministic ARQ checks. Gated by ``edge_classification``
        (off → edge_class stays NULL, zero changes).

        Orphan concepts, all three documented here so nobody confuses them:
        * ``orphan_page``: crawled HTML with 0 inlinks.
        * ``orphan_not_in_crawl``: known to sitemap/GSC, unreachable by the
          crawl (synthetic ``not_crawled`` row).
        * ``link_orphan`` (T23): crawled and indexable but with NO click
          path from the home (reached only via sitemap discovery).
        """
        config = (self._job.config or {}) if self._job else {}
        if not config.get("edge_classification"):
            return

        from analysis.architecture import (
            classify_edges,
            compute_click_depth,
            compute_contextual_counters,
            compute_section_flows,
        )
        from shared.models import Segment, UrlSegment
        from shared.url_normalization import compute_url_hash as _hash

        t = config.get("analysis_thresholds", {}) or {}
        max_depth_business = t.get("excessive_click_depth_business", 4)
        max_depth_default = t.get("excessive_click_depth", 5)
        deep_pagination_max = t.get("deep_pagination_max", 3)
        business_min_share = t.get("business_pagerank_min_share", 0.5)

        logger.debug("Analyzing architecture ...")

        client_id = self._job.client_id if self._job else None
        summary = classify_edges(self.session, self.job_id, client_id)
        logger.info("Edge classification: %s", summary)

        seeds = {(
            _hash(s, self._norm_config)
        ) for s in (self._job.seeds or [])}
        depth = compute_click_depth(
            self.session, self.job_id, seeds, self._norm_config,
        )
        compute_contextual_counters(self.session, self.job_id)
        compute_section_flows(self.session, self.job_id)

        # Segment membership + business flags
        seg_by_url = dict(self.session.execute(
            select(UrlSegment.url_id, UrlSegment.segment_id)
            .where(UrlSegment.job_id == self.job_id)
        ).all())
        business_segments = {
            s.id for s in self.session.execute(
                select(Segment).where(
                    Segment.client_id == client_id,
                    Segment.is_business.is_(True),
                )
            ).scalars()
        } if client_id else set()

        rows = self.session.execute(
            select(Url.id, Url.url, Url.click_depth, Url.in_contextual,
                   Url.out_contextual, Url.pagerank)
            .where(
                Url.job_id == self.job_id,
                Url.is_internal.is_(True),
                Url.is_html.is_(True),
                Url.status_code >= 200,
                Url.status_code < 300,
                Url.indexable.isnot(False),
            )
        ).all()

        pr_values = sorted(r.pagerank for r in rows if r.pagerank is not None)
        p50_pr = pr_values[len(pr_values) // 2] if pr_values else None

        for r in rows:
            seg = seg_by_url.get(r.id)
            is_business = seg in business_segments

            # link_orphan: indexable without any click path from home
            if r.click_depth is None:
                self._add_issue(r.id, "link_orphan", "warning", {
                    "reason": "no_click_path_from_home",
                })
            else:
                limit = max_depth_business if is_business else max_depth_default
                if r.click_depth >= limit:
                    self._add_issue(r.id, "excessive_click_depth", "warning", {
                        "click_depth": r.click_depth,
                        "limit": limit,
                        "is_business": is_business,
                    })

            if is_business and (r.in_contextual or 0) == 0:
                self._add_issue(r.id, "no_contextual_inlinks", "warning", {
                    "segment_id": seg,
                })

            if (
                p50_pr is not None and r.pagerank is not None
                and r.pagerank > p50_pr and (r.out_contextual or 0) == 0
            ):
                self._add_issue(r.id, "authority_sink", "info", {
                    "pagerank": r.pagerank,
                    "pagerank_p50": p50_pr,
                    "template_fix": (
                        "Un bloque de relacionados en la plantilla de esta "
                        "sección repartiría autoridad desde todas las "
                        "páginas equivalentes con un solo cambio."
                    ),
                })

        # deep_pagination: chains of 'paginacion' edges longer than K
        self._check_deep_pagination(deep_pagination_max)

        # hierarchy_imbalance (job level)
        if business_segments and pr_values:
            share_by_segment: dict[int, float] = {}
            total_pr = sum(pr_values) or 1.0
            for r in rows:
                seg = seg_by_url.get(r.id) or 0
                share_by_segment[seg] = share_by_segment.get(seg, 0.0) + (r.pagerank or 0.0)
            business_share = sum(
                v for s, v in share_by_segment.items() if s in business_segments
            ) / total_pr
            if business_share < business_min_share:
                home_id = rows[0].id if rows else None
                for r in rows:
                    if r.click_depth == 0:
                        home_id = r.id
                        break
                if home_id is not None:
                    self._add_issue(home_id, "hierarchy_imbalance", "warning", {
                        "business_share": round(business_share, 4),
                        "threshold": business_min_share,
                        "distribution": {
                            str(s): round(v / total_pr, 4)
                            for s, v in sorted(share_by_segment.items())
                        },
                    })

        self._flush_issues()

    def _check_deep_pagination(self, max_len: int) -> None:
        """T23: flag pagination chains longer than *max_len* hops."""
        rows = self.session.execute(
            select(Link.from_url_id, Url.id)
            .join(Url, and_(
                Url.job_id == Link.job_id,
                Url.url_hash == Link.to_url_hash,
            ))
            .where(
                Link.job_id == self.job_id,
                Link.edge_class == "paginacion",
            )
        ).all()
        if not rows:
            return

        nxt: dict[int, set[int]] = {}
        has_incoming: set[int] = set()
        for src, dst in rows:
            nxt.setdefault(src, set()).add(dst)
            has_incoming.add(dst)

        memo: dict[int, int] = {}

        def chain_len(node: int, seen: frozenset) -> int:
            if node in memo:
                return memo[node]
            if node in seen:
                return 0  # cycle guard
            best = 0
            for d in nxt.get(node, ()):
                best = max(best, 1 + chain_len(d, seen | {node}))
            memo[node] = best
            return best

        for start in nxt:
            if start in has_incoming:
                continue  # only chain heads
            length = chain_len(start, frozenset())
            if length > max_len:
                self._add_issue(start, "deep_pagination", "info", {
                    "chain_length": length,
                    "max": max_len,
                })

    def analyze_geo(self) -> None:
        """T15: what only exists after executing JS is invisible to AI
        crawlers (and to Google's first pass).

        For pages with both sides captured, derives ``js_content_ratio``
        (share of rendered text absent from the raw HTML), stamps
        ``structured_data.visible_without_js`` and emits:
        * ``content_only_after_js`` (error) above the ratio threshold;
        * ``schema_only_after_js`` (warning) for JSON-LD blocks missing
          from the raw HTML.
        Jobs without the flag: columns stay NULL, zero changes.
        """
        config = (self._job.config or {}) if self._job else {}
        if not config.get("geo_analysis"):
            return

        threshold = (
            config.get("analysis_thresholds", {}) or {}
        ).get("geo_js_content_threshold", 0.5)

        logger.debug("Analyzing GEO readiness ...")

        rows = self.session.execute(
            select(Url.id, Url.word_count, Url.raw_word_count,
                   Url.raw_schema_types)
            .where(
                Url.job_id == self.job_id,
                Url.is_internal.is_(True),
                Url.is_html.is_(True),
                Url.status_code == 200,
                Url.raw_word_count.isnot(None),
            )
        ).all()

        for url_id, rendered_words, raw_words, raw_types in rows:
            rendered = rendered_words or 0
            ratio = 0.0
            if rendered > 0:
                ratio = max(0.0, 1.0 - (raw_words or 0) / rendered)
            self.session.execute(
                update(Url).where(Url.id == url_id)
                .values(js_content_ratio=round(ratio, 4))
            )
            if ratio >= threshold and rendered > 0:
                self._add_issue(
                    url_id, "content_only_after_js", "error",
                    {
                        "js_content_ratio": round(ratio, 4),
                        "rendered_word_count": rendered,
                        "raw_word_count": raw_words or 0,
                        "threshold": threshold,
                    },
                )

            # JSON-LD visibility: rendered blocks vs raw types
            raw_set = set(raw_types or [])
            sd_rows = self.session.execute(
                select(StructuredData.id, StructuredData.schema_type)
                .where(
                    StructuredData.url_id == url_id,
                    StructuredData.format == "jsonld",
                )
            ).all()
            missing_types = []
            for sd_id, schema_type in sd_rows:
                visible = schema_type in raw_set if schema_type else None
                self.session.execute(
                    update(StructuredData)
                    .where(StructuredData.id == sd_id)
                    .values(visible_without_js=visible)
                )
                if visible is False:
                    missing_types.append(schema_type)
            if missing_types:
                self._add_issue(
                    url_id, "schema_only_after_js", "warning",
                    {"schema_types": sorted(set(missing_types))},
                )

        self.session.flush()
        self._flush_issues()

    # -- GSC signals (T9) -------------------------------------------------------

    def analyze_gsc_signals(self) -> None:
        """T9: cross GSC metrics with the link graph.

        * ``no_inlinks_with_traffic`` (warning): 0 inlinks × clicks > 0.
        * ``underlinked_high_performer`` (info): pagerank < P25 of the job
          × clicks > P75 of the job's clicked pages.

        No-op when the job has no GSC data (never a silent empty result:
        the API exposes the blocked state separately).
        """
        try:
            from shared.semantic_models import GscJobData
        except ImportError:
            return

        rows = self.session.execute(
            select(GscJobData.url_id, GscJobData.clicks)
            .where(
                GscJobData.job_id == self.job_id,
                GscJobData.url_id.isnot(None),
            )
        ).all()
        clicks_by_url = {url_id: (clicks or 0) for url_id, clicks in rows}
        if not clicks_by_url:
            return

        logger.debug("Analyzing GSC signals (%d URLs) ...", len(clicks_by_url))

        import math as _math

        def _nearest_rank(values: list[float], p: float) -> float | None:
            if not values:
                return None
            ordered = sorted(values)
            idx = min(len(ordered) - 1, max(0, _math.ceil(p * len(ordered)) - 1))
            return ordered[idx]

        pr_values = [
            float(pr) for (pr,) in self.session.execute(
                select(Url.pagerank).where(
                    Url.job_id == self.job_id,
                    Url.is_internal.is_(True),
                    Url.is_html.is_(True),
                    Url.pagerank.isnot(None),
                )
            ).all()
        ]
        p25_pr = _nearest_rank(pr_values, 0.25)
        p75_clicks = _nearest_rank(
            [float(c) for c in clicks_by_url.values() if c > 0], 0.75,
        )

        url_rows = self.session.execute(
            select(Url.id, Url.inlinks_count, Url.pagerank)
            .where(Url.job_id == self.job_id, Url.id.in_(clicks_by_url))
        ).all()

        for url_id, inlinks, pagerank in url_rows:
            clicks = clicks_by_url.get(url_id, 0)
            if clicks > 0 and inlinks == 0:
                self._add_issue(
                    url_id, "no_inlinks_with_traffic", "warning",
                    {"clicks": clicks, "inlinks": 0},
                )
            if (
                p25_pr is not None and p75_clicks is not None
                and pagerank is not None
                and pagerank < p25_pr and clicks > p75_clicks
            ):
                self._add_issue(
                    url_id, "underlinked_high_performer", "info",
                    {
                        "clicks": clicks,
                        "pagerank": pagerank,
                        "pagerank_p25": p25_pr,
                        "clicks_p75": p75_clicks,
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


    # -- Soft 404 (T6) ----------------------------------------------------------

    _SOFT404_TITLE_PATTERNS = (
        "404", "no encontrado", "no encontrada", "not found",
        "página no existe", "page not found", "no existe",
    )

    def analyze_soft_404(self) -> None:
        """T6: 200 pages that are actually "not found".

        Signals (any one marks the page, flag ``detect_soft_404`` on):
        (a) body_hash equals the host's 404-probe template;
        (b) token similarity with the probe's text ≥ threshold;
        (c) word_count below ``min_word_count`` AND error-pattern title.
        If the probe answered 200, only (c) applies and the fact is
        recorded in the issue details.
        """
        config = (self._job.config or {}) if self._job else {}
        if not config.get("detect_soft_404"):
            return

        logger.debug("Analyzing soft 404 ...")

        signatures: dict[str, dict] = config.get("_soft404_signature") or {}
        probe_200 = {
            h for h, s in signatures.items() if s.get("status") == 200
        }
        valid_sigs = {
            h: s for h, s in signatures.items() if s.get("status") == 404
        }

        def _tokens(text: str) -> set[str]:
            return set(re.findall(r"\w+", (text or "").lower()))

        sig_tokens = {h: _tokens(s.get("sample_text")) for h, s in valid_sigs.items()}

        rows = self.session.execute(
            select(Url.id, Url.host, Url.body_hash, Url.word_count,
                   HtmlMeta.title, PageContent.content_text)
            .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
            .outerjoin(PageContent, PageContent.url_id == Url.id)
            .where(
                Url.job_id == self.job_id,
                Url.is_internal.is_(True),
                Url.is_html.is_(True),
                Url.status_code == 200,
            )
        ).all()

        for url_id, host, body_hash, word_count, title, content in rows:
            sig = valid_sigs.get(host)
            reason = None
            score = None

            if sig and body_hash and body_hash == sig.get("body_hash"):
                reason = "probe_template_hash"
            elif sig and content:
                probe_toks = sig_tokens.get(host) or set()
                page_toks = _tokens(content)
                smaller = min(len(probe_toks), len(page_toks))
                if smaller >= 5:
                    score = len(probe_toks & page_toks) / smaller
                    if score >= self.soft404_similarity:
                        reason = "probe_template_similarity"
            if reason is None:
                title_l = (title or "").lower()
                if (
                    word_count is not None
                    and word_count < self.min_word_count
                    and any(p in title_l for p in self._SOFT404_TITLE_PATTERNS)
                ):
                    reason = "error_title_low_content"

            if reason:
                details = {"reason": reason}
                if score is not None:
                    details["similarity"] = round(score, 4)
                if host in probe_200:
                    details["probe_returned_200"] = True
                self._add_issue(url_id, "soft_404", "error", details)

        self._flush_issues()

    # -- Near duplicates (T14) --------------------------------------------------

    def analyze_near_duplicates(self) -> None:
        """T14: cluster near-identical pages. ``simhash`` groups by Hamming
        distance ≤ threshold with 4×16-bit band bucketing (no O(n²));
        ``embeddings`` requires the semantic analysis and degrades to a
        logged skip when unavailable. ``duplicate_content`` (exact) is
        untouched.
        """
        mode = self.near_duplicate_detection
        if mode == "off":
            return
        if mode == "embeddings":
            logger.warning(
                "near_duplicate_detection=embeddings requiere análisis "
                "semántico con vectores persistidos (T11); omitido para "
                "job %s", self.job_id,
            )
            return

        from shared.simhash import from_signed, hamming

        logger.debug("Analyzing near duplicates (simhash) ...")

        rows = self.session.execute(
            select(Url.id, Url.url, Url.simhash)
            .where(
                Url.job_id == self.job_id,
                Url.is_internal.is_(True),
                Url.is_html.is_(True),
                Url.simhash.isnot(None),
            )
        ).all()
        if len(rows) < 2:
            return

        values = [(r.id, r.url, from_signed(r.simhash)) for r in rows]

        # Band bucketing: two hashes within Hamming ≤ 16/4-ish share at
        # least one identical 16-bit band (pigeonhole for d ≤ 3 ≤ 4-1).
        buckets: dict[tuple[int, int], list[int]] = {}
        for idx, (_, _, h) in enumerate(values):
            for band in range(4):
                key = (band, (h >> (band * 16)) & 0xFFFF)
                buckets.setdefault(key, []).append(idx)

        parent = list(range(len(values)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for members in buckets.values():
            if len(members) < 2:
                continue
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    a, b = members[i], members[j]
                    if find(a) == find(b):
                        continue
                    d = hamming(values[a][2], values[b][2])
                    if d <= self.near_duplicate_hamming:
                        union(a, b)

        clusters: dict[int, list[int]] = {}
        for idx in range(len(values)):
            clusters.setdefault(find(idx), []).append(idx)

        cluster_id = 0
        for members in clusters.values():
            if len(members) < 2:
                continue
            cluster_id += 1
            urls_sample = [values[i][1] for i in members[:10]]
            for i in members:
                self._add_issue(
                    values[i][0],
                    "near_duplicate_content",
                    "warning",
                    {
                        "cluster_id": cluster_id,
                        "method": "simhash",
                        "cluster_size": len(members),
                        "urls": urls_sample,
                    },
                )
        self._flush_issues()

    # -- Crawl traps (T13) ------------------------------------------------------

    def analyze_crawl_traps(self) -> None:
        """T13: surface capped crawl patterns as ``crawl_trap_detected``
        (warning) so the analyst decides: add an exclude or raise the cap
        and relaunch. Attached to the pattern's first sampled URL.
        """
        from shared.models import CrawlTrapEvent
        from shared.url_normalization import compute_url_hash as _hash

        events = self.session.execute(
            select(CrawlTrapEvent).where(CrawlTrapEvent.job_id == self.job_id)
        ).scalars().all()
        if not events:
            return

        for ev in events:
            url_id = None
            if ev.first_url_sample:
                url_id = self.session.execute(
                    select(Url.id).where(
                        Url.job_id == self.job_id,
                        Url.url_hash == _hash(
                            ev.first_url_sample, self._norm_config
                        ),
                    )
                ).scalar()
            if url_id is None:
                # Sample never crawled (capped immediately): attach to any
                # crawled URL of the job to keep the FK; the pattern lives
                # in details either way.
                url_id = self.session.execute(
                    select(Url.id)
                    .where(Url.job_id == self.job_id)
                    .order_by(Url.id)
                    .limit(1)
                ).scalar()
            if url_id is None:
                continue
            self._add_issue(
                url_id,
                "crawl_trap_detected",
                "warning",
                {
                    "pattern": ev.pattern,
                    "urls_seen": ev.urls_seen,
                    "urls_skipped": ev.urls_skipped,
                    "sample": ev.first_url_sample,
                },
            )
        self._flush_issues()

    # -- Freshness (T5) ---------------------------------------------------------

    def analyze_freshness(self) -> None:
        """T5: ``stale_lastmod`` — sitemaps that lie. Only runs when the job
        was launched with ``compare_to_job_id`` in its config.
        """
        compare_to = (self._job.config or {}).get("compare_to_job_id") if self._job else None
        if not compare_to:
            return

        import uuid as _uuid

        try:
            compare_to = _uuid.UUID(str(compare_to))
        except (TypeError, ValueError):
            logger.warning("compare_to_job_id inválido: %r", compare_to)
            return

        logger.debug("Analyzing freshness vs job %s ...", compare_to)

        prev_rows = self.session.execute(
            select(Url.url_hash, Url.body_hash, Url.sitemap_lastmod)
            .where(Url.job_id == compare_to, Url.body_hash.isnot(None))
        ).all()
        prev = {r.url_hash: r for r in prev_rows}
        if not prev:
            return

        rows = self.session.execute(
            select(Url.id, Url.url_hash, Url.body_hash, Url.sitemap_lastmod)
            .where(
                Url.job_id == self.job_id,
                Url.body_hash.isnot(None),
                Url.sitemap_lastmod.isnot(None),
            )
        ).all()

        for url_id, url_hash, body_hash, lastmod in rows:
            old = prev.get(url_hash)
            if old is None:
                continue
            lastmod_changed = (
                old.sitemap_lastmod is not None and lastmod != old.sitemap_lastmod
            )
            body_changed = body_hash != old.body_hash
            if lastmod_changed and not body_changed:
                self._add_issue(url_id, "stale_lastmod", "info", {
                    "reason": "lastmod_changed_content_identical",
                    "lastmod": lastmod.isoformat() if lastmod else None,
                })
            elif body_changed and not lastmod_changed and old.sitemap_lastmod is not None:
                self._add_issue(url_id, "stale_lastmod", "info", {
                    "reason": "content_changed_lastmod_stale",
                    "lastmod": lastmod.isoformat() if lastmod else None,
                })
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

    Serializado por job con un ADVISORY LOCK de Postgres: dos análisis del
    mismo job (endpoint reanalyze + trigger del worker, o varios reanalyze)
    se pisaban en el DELETE+INSERT de issues y DUPLICABAN filas (cazado en
    la auditoría de concurrencia: 1390 → 4170). El advisory lock cubre
    AMBOS procesos sin depender de Redis; si otro análisis del mismo job
    ya lo tiene, este se salta con log en vez de duplicar.
    """
    from sqlalchemy import text as _text

    session = SessionLocal()
    lock_key = _job_advisory_key(job_id)
    is_pg = session.get_bind().dialect.name.startswith("postgres")
    try:
        if is_pg:
            got = session.execute(
                _text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}
            ).scalar()
            if not got:
                logger.warning(
                    "Análisis de %s ya en curso (advisory lock ocupado): se "
                    "omite esta ejecución para no duplicar", job_id)
                return
        analyzer = SEOAnalyzer(session, job_id)
        analyzer.run_all()
    finally:
        if is_pg:
            try:
                session.execute(
                    _text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
                session.commit()
            except Exception:
                pass
        session.close()


def _job_advisory_key(job_id: str) -> int:
    """Clave estable de 63 bits para pg_advisory_lock desde un UUID de job."""
    import hashlib

    digest = hashlib.sha1(str(job_id).encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m analysis.analyzer <job_id>")
        sys.exit(1)
    run_analysis(sys.argv[1])
