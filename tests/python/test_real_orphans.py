"""
Tests de T2 — huérfanas reales (orphan_not_in_crawl).

Criterios del plan: 3 URLs de sitemap no enlazadas → 3 issues nuevos;
`orphan_page` no cambia; una URL con `orphan_not_in_crawl` JAMÁS tiene
además `orphan_page`; el PageRank no se mueve por las filas not_crawled.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _sitemap_row(db_session, job, url, lastmod=None):
    from shared.models import SitemapUrl
    from shared.url_normalization import compute_url_hash, normalize_url

    row = SitemapUrl(
        job_id=job.id,
        url=normalize_url(url),
        url_hash=compute_url_hash(url),
        lastmod=lastmod,
        sitemap_source="https://toy.local/sitemap.xml",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _crawled_html(db_session, job, path, *, inlinks=1, status=200):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        is_internal=True, is_html=True, status_code=status,
        status_group=f"{status // 100}xx", inlinks_count=inlinks,
    )
    db_session.add(u)
    db_session.flush()
    return u


def test_three_unlinked_sitemap_urls_emit_three_orphans(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue, Url

    job = make_job()
    home = _crawled_html(db_session, job, "/")
    lastmod = datetime(2026, 6, 1, tzinfo=timezone.utc)
    _sitemap_row(db_session, job, "https://toy.local/")  # rastreada, no huérfana
    _sitemap_row(db_session, job, "https://toy.local/huerfana-1", lastmod)
    _sitemap_row(db_session, job, "https://toy.local/huerfana-2")
    _sitemap_row(db_session, job, "https://toy.local/huerfana-3")

    analyzer = SEOAnalyzer(db_session, job.id)
    analyzer.analyze_sitemaps()
    analyzer.analyze_real_orphans()
    db_session.flush()

    orphans = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "orphan_not_in_crawl"
    ).all()
    assert len(orphans) == 3
    assert all(i.severity == "warning" for i in orphans)
    assert all(i.details["seen_in"] == ["sitemap"] for i in orphans)

    # filas mínimas insertadas con las dos salvaguardas
    rows = db_session.query(Url).filter(
        Url.job_id == job.id, Url.status_group == "not_crawled"
    ).all()
    assert len(rows) == 3
    for r in rows:
        assert r.is_html is False
        assert r.inlinks_count is None
        assert r.status_code is None
        assert r.in_sitemap is True
        assert r.is_internal is True
        assert r.host == "toy.local"

    by_url = {r.url: r for r in rows}
    assert by_url["https://toy.local/huerfana-1"].sitemap_lastmod is not None

    # la home rastreada no es huérfana real
    assert all(i.url_id != home.id for i in orphans)


def test_orphan_not_in_crawl_never_coexists_with_orphan_page(db_session, make_job):
    """Test explícito exigido por el plan (salvaguarda is_html=False)."""
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job()
    _crawled_html(db_session, job, "/")                       # con inlinks
    sin_inlinks = _crawled_html(db_session, job, "/aislada", inlinks=0)
    _sitemap_row(db_session, job, "https://toy.local/huerfana-real")

    analyzer = SEOAnalyzer(db_session, job.id)
    analyzer.analyze_real_orphans()
    analyzer.analyze_links()  # orphan_page corre DESPUÉS de insertar filas
    db_session.flush()

    issues = db_session.query(Issue).filter(Issue.job_id == job.id).all()
    per_url: dict[int, set[str]] = {}
    for i in issues:
        per_url.setdefault(i.url_id, set()).add(i.issue_type)

    # ninguna URL tiene ambos tipos
    for types in per_url.values():
        assert not ({"orphan_page", "orphan_not_in_crawl"} <= types)

    # orphan_page sigue funcionando exactamente igual para HTML rastreado
    assert "orphan_page" in per_url[sin_inlinks.id]


def test_real_orphans_idempotent_on_reanalysis(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue, Url

    job = make_job()
    _crawled_html(db_session, job, "/")
    _sitemap_row(db_session, job, "https://toy.local/huerfana")

    analyzer = SEOAnalyzer(db_session, job.id)
    for _ in range(2):  # simula re-análisis (T17.2)
        analyzer.clear_existing_issues()
        analyzer.analyze_sitemaps()
        analyzer.analyze_real_orphans()
        db_session.flush()

    rows = db_session.query(Url).filter(
        Url.job_id == job.id, Url.status_group == "not_crawled"
    ).count()
    issues = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "orphan_not_in_crawl"
    ).count()
    assert rows == 1      # sin duplicados
    assert issues == 1    # el issue sobrevive al re-análisis


def test_pagerank_unaffected_by_not_crawled_rows(db_session, make_job):
    """Las filas not_crawled no entran al grafo: snapshot v1 intacto."""
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Url
    from test_pagerank_v1_snapshot import EXPECTED_PAGERANK
    from toygraph import build_toy_graph

    job, _ = build_toy_graph(db_session, make_job)
    _sitemap_row(db_session, job, "https://toy.local/huerfana-x")
    _sitemap_row(db_session, job, "https://toy.local/huerfana-y")

    analyzer = SEOAnalyzer(db_session, job.id)
    analyzer.analyze_real_orphans()   # inserta not_crawled ANTES del PR
    analyzer.compute_pagerank()

    rows = db_session.query(Url.url, Url.pagerank).filter(
        Url.job_id == job.id, Url.is_internal.is_(True),
        Url.status_group.isnot(None) == False,  # noqa: E712 — todas las del toygraph
    ).all()
    actual = {u: pr for u, pr in rows if u in EXPECTED_PAGERANK}
    for url, expected in EXPECTED_PAGERANK.items():
        assert actual[url] == pytest.approx(expected, abs=1e-4), url

    # y las huérfanas no reciben pagerank
    orphan_pr = db_session.query(Url.pagerank).filter(
        Url.job_id == job.id, Url.status_group == "not_crawled"
    ).all()
    assert all(pr is None for (pr,) in orphan_pr)


def test_get_stats_excludes_not_crawled_from_totals(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer

    job = make_job()
    _crawled_html(db_session, job, "/")
    _crawled_html(db_session, job, "/b")
    _sitemap_row(db_session, job, "https://toy.local/huerfana")

    analyzer = SEOAnalyzer(db_session, job.id)
    analyzer.analyze_real_orphans()
    db_session.flush()

    from api.routers.results import get_stats

    stats = get_stats(job.id, db_session)
    assert stats.total_urls == 2          # la huérfana no cuenta
    assert stats.internal_count == 2
    # pero sí aparece en el desglose por status_group
    groups = {g.status_group: g.count for g in stats.urls_by_status_group}
    assert groups.get("not_crawled") == 1
