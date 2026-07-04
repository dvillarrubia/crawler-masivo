"""
Tests de T1 — ingesta de sitemaps.

Sin red: el fetch es inyectable y los documentos son fixtures en memoria
(índice anidado + urlset + gzip + robots.txt), como exige el criterio de
aceptación del plan.
"""

from __future__ import annotations

import gzip
from datetime import datetime, timezone

import pytest

SITEMAP_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://toy.local/sitemap-pages.xml</loc></sitemap>
  <sitemap><loc>https://toy.local/sitemap-blog.xml.gz</loc></sitemap>
</sitemapindex>
"""

SITEMAP_PAGES = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://toy.local/</loc><lastmod>2026-06-01T10:00:00+00:00</lastmod></url>
  <url><loc>https://toy.local/b</loc><lastmod>2026-06-15</lastmod></url>
  <url><loc>https://toy.local/declarada-no-rastreada</loc></url>
</urlset>
"""

SITEMAP_BLOG = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://toy.local/blog/post-1</loc><lastmod>garbage-date</lastmod></url>
  <url><loc>https://toy.local/b#fragmento</loc></url>
</urlset>
"""

ROBOTS_WITH_SITEMAP = b"""User-agent: *
Disallow: /admin
Sitemap: https://toy.local/sitemap-index.xml
"""

DOCS = {
    "https://toy.local/robots.txt": ROBOTS_WITH_SITEMAP,
    "https://toy.local/sitemap-index.xml": SITEMAP_INDEX,
    "https://toy.local/sitemap-pages.xml": SITEMAP_PAGES,
    "https://toy.local/sitemap-blog.xml.gz": gzip.compress(SITEMAP_BLOG),
}


def fake_fetch(url: str) -> bytes:
    if url in DOCS:
        return DOCS[url]
    raise OSError(f"404 for {url}")


# ---------------------------------------------------------------------------
# Descubrimiento
# ---------------------------------------------------------------------------

def test_discover_from_robots():
    from seo_crawler.sitemap_ingest import discover_sitemap_docs

    docs = discover_sitemap_docs(["https://toy.local/"], fake_fetch)
    assert docs == ["https://toy.local/sitemap-index.xml"]


def test_discover_fallback_when_no_robots():
    from seo_crawler.sitemap_ingest import discover_sitemap_docs

    def no_robots(url: str) -> bytes:
        raise OSError("nope")

    docs = discover_sitemap_docs(["https://otro.local/x"], no_robots)
    assert docs == ["https://otro.local/sitemap.xml"]


def test_parse_lastmod():
    from seo_crawler.sitemap_ingest import parse_lastmod

    assert parse_lastmod("2026-06-01T10:00:00+00:00") == datetime(
        2026, 6, 1, 10, tzinfo=timezone.utc
    )
    assert parse_lastmod("2026-06-15") == datetime(
        2026, 6, 15, tzinfo=timezone.utc
    )
    assert parse_lastmod("2026-06-01T10:00:00Z") is not None
    assert parse_lastmod("garbage-date") is None
    assert parse_lastmod(None) is None


# ---------------------------------------------------------------------------
# Ingesta completa (índice + gzip + dedup + normalización)
# ---------------------------------------------------------------------------

def test_ingest_walks_index_gzip_and_dedups(db_session, make_job):
    from seo_crawler.sitemap_ingest import ingest_sitemaps
    from shared.models import SitemapUrl
    from shared.url_normalization import compute_url_hash

    job = make_job()
    count = ingest_sitemaps(
        db_session, job.id, ["https://toy.local/"], fetch=fake_fetch
    )
    # 5 <loc> en total, pero /b y /b#fragmento colapsan al mismo hash
    assert count == 4

    rows = db_session.query(SitemapUrl).filter(SitemapUrl.job_id == job.id).all()
    by_url = {r.url: r for r in rows}
    assert set(by_url) == {
        "https://toy.local/",
        "https://toy.local/b",
        "https://toy.local/declarada-no-rastreada",
        "https://toy.local/blog/post-1",
    }
    # hashes con la misma normalización que el crawl
    assert by_url["https://toy.local/b"].url_hash == compute_url_hash("https://toy.local/b")
    # lastmod válido parseado; basura → None
    assert by_url["https://toy.local/"].lastmod is not None
    assert by_url["https://toy.local/blog/post-1"].lastmod is None
    # procedencia
    assert by_url["https://toy.local/blog/post-1"].sitemap_source.endswith(".xml.gz")


def test_ingest_survives_missing_sitemap(db_session, make_job):
    """Sitemap 404 → 0 URLs y sin excepción (el crawl seguiría)."""
    from seo_crawler.sitemap_ingest import ingest_sitemaps

    def all_404(url: str) -> bytes:
        raise OSError("404")

    job = make_job()
    assert ingest_sitemaps(db_session, job.id, ["https://toy.local/"], fetch=all_404) == 0


def test_ingest_respects_doc_cap(db_session, make_job):
    """Una bomba de índices no cuelga la ingesta (cap de documentos)."""
    from seo_crawler.sitemap_ingest import ingest_sitemaps

    def bomb(url: str) -> bytes:
        if url.endswith("robots.txt"):
            raise OSError("no robots")
        n = int(url.rsplit("-", 1)[-1].split(".")[0]) if "-" in url else 0
        return (
            "<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
            f"<sitemap><loc>https://toy.local/deep-{n + 1}.xml</loc></sitemap>"
            "</sitemapindex>"
        ).encode()

    job = make_job()
    count = ingest_sitemaps(
        db_session, job.id, ["https://toy.local/"], fetch=bomb, max_docs=10
    )
    assert count == 0  # terminó sin colgarse


# ---------------------------------------------------------------------------
# Cruce en el analyzer (in_sitemap / lastmod / issues)
# ---------------------------------------------------------------------------

def _crawled_url(db_session, job, path, *, status=200, is_html=True,
                 indexable=True, internal=True):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = path if path.startswith("http") else f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        is_internal=internal, is_html=is_html, status_code=status,
        status_group=f"{status // 100}xx", indexable=indexable,
    )
    db_session.add(u)
    db_session.flush()
    return u


def test_analyze_sitemaps_noop_without_rows(db_session, make_job):
    """Flag off → cero filas en sitemap_urls → cero cambios (criterio T1)."""
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job()
    home = _crawled_url(db_session, job, "/")
    SEOAnalyzer(db_session, job.id).analyze_sitemaps()
    db_session.flush()

    assert home.in_sitemap is None
    assert db_session.query(Issue).filter(Issue.job_id == job.id).count() == 0


def test_analyze_sitemaps_flags_and_issues(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from seo_crawler.sitemap_ingest import ingest_sitemaps
    from shared.models import Issue

    job = make_job()
    ingest_sitemaps(db_session, job.id, ["https://toy.local/"], fetch=fake_fetch)

    home = _crawled_url(db_session, job, "/")                        # en sitemap, 200
    b = _crawled_url(db_session, job, "/b", status=404)              # en sitemap, 404
    extra = _crawled_url(db_session, job, "/no-declarada")           # 200 fuera de sitemap
    noindex = _crawled_url(db_session, job, "/noindex", indexable=False)
    externa = _crawled_url(
        db_session, job, "https://fuera.example.org/", internal=False
    )

    analyzer = SEOAnalyzer(db_session, job.id)
    analyzer.analyze_sitemaps()
    db_session.flush()
    db_session.expire_all()  # los UPDATE por SQL no refrescan objetos ya cargados

    # flags
    assert home.in_sitemap is True
    assert home.sitemap_lastmod is not None
    assert b.in_sitemap is True
    assert extra.in_sitemap is False
    assert noindex.in_sitemap is False
    assert externa.in_sitemap is None  # las externas no se marcan

    issues = db_session.query(Issue).filter(Issue.job_id == job.id).all()
    by_type: dict[str, list] = {}
    for i in issues:
        by_type.setdefault(i.issue_type, []).append(i)

    # /b declarada y rastreada con 404 → in_sitemap_not_crawled
    assert [i.url_id for i in by_type["in_sitemap_not_crawled"]] == [b.id]
    assert by_type["in_sitemap_not_crawled"][0].details["status_code"] == 404

    # /no-declarada indexable 200 fuera del sitemap → crawled_not_in_sitemap
    assert [i.url_id for i in by_type["crawled_not_in_sitemap"]] == [extra.id]

    # la noindex fuera de sitemap NO es issue (solo indexables)
    assert all(i.url_id != noindex.id for i in issues)
