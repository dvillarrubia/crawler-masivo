"""
Sitemap ingestion (T1).

Discovers the job's sitemaps (robots.txt ``Sitemap:`` lines, falling back
to ``/sitemap.xml`` per seed host), walks nested sitemap indexes, handles
gzipped documents, normalizes every URL with the SAME normalization the
crawl uses (T8 active config included) and bulk-inserts into
``sitemap_urls``.

Runs once at job startup, only when ``JobConfig.ingest_sitemaps`` is true.
Failures never abort the crawl: callers wrap this in try/except and log.

Fetching is injectable (``fetch`` argument) so tests run without network.
"""

from __future__ import annotations

import zlib
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse

from lxml import etree

from shared.models import SitemapUrl
from shared.url_normalization import compute_url_hash, normalize_url

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 20          # seconds per document
MAX_SITEMAP_DOCS = 200      # safety cap on sitemap files walked
MAX_SITEMAP_URLS = 500_000  # safety cap on URLs ingested
# DoS: un sitemap real cabe de sobra en 50 MB descomprimido. Sin estos
# topes, un documento gigante o una gzip bomb (pocos KB → GB) reventaban
# la memoria del worker (cazado en la auditoría hostil).
MAX_DOC_BYTES = 50 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
BATCH_SIZE = 1000           # insert batch

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

Fetch = Callable[[str], bytes]


def _default_fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "SEOCrawler/1.0 (+sitemap-ingest)"}
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        # Lectura acotada: un byte de más aborta (no descargamos gigas)
        body = resp.read(MAX_DOWNLOAD_BYTES + 1)
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"sitemap demasiado grande (> {MAX_DOWNLOAD_BYTES} bytes): {url}")
    return body


def _safe_gunzip(body: bytes) -> bytes:
    """Descompresión con tope: corta las gzip bombs sin materializar el
    resultado entero en memoria."""
    out = bytearray()
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)  # 16 = cabecera gzip
    for start in range(0, len(body), 65536):
        out += dec.decompress(body[start:start + 65536], MAX_DOC_BYTES - len(out) + 1)
        if len(out) > MAX_DOC_BYTES:
            raise ValueError(f"gzip bomb: descomprime a más de {MAX_DOC_BYTES} bytes")
    return bytes(out)


def _maybe_gunzip(url: str, body: bytes) -> bytes:
    # gzip magic number covers both .xml.gz URLs and mislabelled responses
    if body[:2] == b"\x1f\x8b" or url.lower().endswith(".gz"):
        return _safe_gunzip(body)
    return body


def parse_lastmod(value: str | None) -> datetime | None:
    """Parse a W3C datetime / ISO date; None on garbage (never raises)."""
    if not value:
        return None
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def discover_sitemap_docs(seeds: Iterable[str], fetch: Fetch) -> list[str]:
    """Candidate sitemap document URLs for the job's seed hosts.

    robots.txt ``Sitemap:`` lines win; ``/sitemap.xml`` is the fallback
    per host. Order is preserved, duplicates removed.
    """
    docs: list[str] = []
    seen_hosts: set[str] = set()

    for seed in seeds:
        parsed = urlparse(seed)
        if not parsed.hostname or parsed.hostname in seen_hosts:
            continue
        seen_hosts.add(parsed.hostname)
        base = f"{parsed.scheme}://{parsed.netloc}"

        from_robots: list[str] = []
        try:
            robots = fetch(urljoin(base, "/robots.txt")).decode("utf-8", "replace")
            for line in robots.splitlines():
                key, _, value = line.partition(":")
                if key.strip().lower() == "sitemap" and value.strip():
                    from_robots.append(value.strip())
        except Exception:
            logger.debug("robots.txt not readable at %s", base)

        docs.extend(from_robots or [urljoin(base, "/sitemap.xml")])

    unique: list[str] = []
    for d in docs:
        if d not in unique:
            unique.append(d)
    return unique


def _parse_sitemap_document(body: bytes) -> tuple[list[str], list[tuple[str, str | None]]]:
    """Return (child_sitemap_urls, [(page_url, lastmod_raw), ...])."""
    root = etree.fromstring(body, parser=etree.XMLParser(recover=True, huge_tree=True))
    if root is None:
        return [], []

    tag = etree.QName(root).localname if isinstance(root.tag, str) else ""
    children: list[str] = []
    pages: list[tuple[str, str | None]] = []

    if tag == "sitemapindex":
        for loc in root.iter(f"{_SITEMAP_NS}sitemap"):
            loc_el = loc.find(f"{_SITEMAP_NS}loc")
            if loc_el is not None and loc_el.text:
                children.append(loc_el.text.strip())
    elif tag == "urlset":
        for url_el in root.iter(f"{_SITEMAP_NS}url"):
            loc_el = url_el.find(f"{_SITEMAP_NS}loc")
            if loc_el is None or not loc_el.text:
                continue
            lastmod_el = url_el.find(f"{_SITEMAP_NS}lastmod")
            lastmod = lastmod_el.text if lastmod_el is not None else None
            pages.append((loc_el.text.strip(), lastmod))
    return children, pages


def ingest_sitemaps(
    session,
    job_id,
    seeds: Iterable[str],
    fetch: Fetch | None = None,
    max_docs: int = MAX_SITEMAP_DOCS,
    max_urls: int = MAX_SITEMAP_URLS,
) -> int:
    """Walk the job's sitemaps and insert rows into ``sitemap_urls``.

    Returns the number of URLs ingested. Individual document failures are
    logged and skipped; caps are logged loudly (never truncate silently).
    """
    fetch = fetch or _default_fetch

    queue = discover_sitemap_docs(seeds, fetch)
    visited: set[str] = set()
    seen_hashes: set[str] = set()
    pending: list[SitemapUrl] = []
    total = 0
    docs_walked = 0

    def _flush() -> None:
        nonlocal pending
        if pending:
            session.bulk_save_objects(pending)
            session.flush()
            pending = []

    while queue and docs_walked < max_docs and total < max_urls:
        doc_url = queue.pop(0)
        if doc_url in visited:
            continue
        visited.add(doc_url)
        docs_walked += 1

        try:
            body = _maybe_gunzip(doc_url, fetch(doc_url))
            children, pages = _parse_sitemap_document(body)
        except Exception as exc:
            logger.warning("Sitemap document failed, skipping %s: %s", doc_url, exc)
            continue

        queue.extend(c for c in children if c not in visited)

        for page_url, lastmod_raw in pages:
            if total >= max_urls:
                break
            url_hash = compute_url_hash(page_url)
            if url_hash in seen_hashes:
                continue
            seen_hashes.add(url_hash)
            pending.append(SitemapUrl(
                job_id=job_id,
                url=normalize_url(page_url),
                url_hash=url_hash,
                lastmod=parse_lastmod(lastmod_raw),
                sitemap_source=doc_url,
            ))
            total += 1
            if len(pending) >= BATCH_SIZE:
                _flush()

    _flush()

    if queue and (docs_walked >= max_docs or total >= max_urls):
        logger.warning(
            "Sitemap ingestion CAPPED for job %s: %d docs walked, %d URLs "
            "ingested, %d sitemap docs left unvisited",
            job_id, docs_walked, total, len(queue),
        )
    logger.info(
        "Sitemap ingestion for job %s: %d URLs from %d documents",
        job_id, total, docs_walked,
    )
    return total
