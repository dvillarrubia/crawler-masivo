"""
Tests de T16 — robots.txt versionado, watchlist y umbrales sugeridos.
"""

from __future__ import annotations

import pytest


def _url(db_session, job, path, *, status=200, indexable=True, words=None,
         ms=None, outlinks=0, canonical=None):
    from shared.models import HtmlMeta, Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        host="toy.local", path=path, scheme="https",
        is_internal=True, is_html=True, status_code=status,
        status_group=f"{status // 100}xx", indexable=indexable,
        word_count=words, response_time_ms=ms, outlinks_count=outlinks,
    )
    db_session.add(u)
    db_session.flush()
    if canonical is not None:
        db_session.add(HtmlMeta(url_id=u.id, canonical_href=canonical))
        db_session.flush()
    return u


# ---------------------------------------------------------------------------
# Snapshot de robots.txt + diff
# ---------------------------------------------------------------------------

ROBOTS_V1 = b"User-agent: *\nDisallow: /admin\n"
ROBOTS_V2 = b"User-agent: *\nDisallow: /admin\nDisallow: /privado\n"


def test_persist_robots_snapshots(db_session, make_job):
    from seo_crawler.robots_snapshot import persist_robots_snapshots
    from shared.models import RobotsSnapshot

    job = make_job()
    count = persist_robots_snapshots(
        db_session, job.id,
        ["https://toy.local/", "https://toy.local/otra", "https://dos.local/"],
        fetch=lambda url: ROBOTS_V1,
    )
    assert count == 2  # hosts únicos

    rows = db_session.query(RobotsSnapshot).filter(
        RobotsSnapshot.job_id == job.id
    ).all()
    assert {r.host for r in rows} == {"toy.local", "dos.local"}
    assert all(r.content_hash is not None for r in rows)


def test_robots_snapshot_survives_fetch_failure(db_session, make_job):
    from seo_crawler.robots_snapshot import persist_robots_snapshots
    from shared.models import RobotsSnapshot

    def boom(url: str) -> bytes:
        raise OSError("500")

    job = make_job()
    assert persist_robots_snapshots(
        db_session, job.id, ["https://toy.local/"], fetch=boom,
    ) == 1
    row = db_session.query(RobotsSnapshot).one()
    assert row.content is None
    assert row.content_hash is None


def test_diff_flags_robots_change_with_readable_diff(db_session, make_job):
    from api.routers.diff import diff_summary
    from seo_crawler.robots_snapshot import persist_robots_snapshots

    a = make_job(name="run-1")
    b = make_job(name="run-2")
    a.client_id = b.client_id = "cliente-x"
    db_session.flush()

    persist_robots_snapshots(db_session, a.id, ["https://toy.local/"],
                             fetch=lambda u: ROBOTS_V1)
    persist_robots_snapshots(db_session, b.id, ["https://toy.local/"],
                             fetch=lambda u: ROBOTS_V2)

    summary = diff_summary(a.id, b.id, pagerank_delta=0.5, segment_id=None,
                           db=db_session)
    assert len(summary.robots_changes) == 1
    change = summary.robots_changes[0]
    assert change.host == "toy.local"
    assert change.changed is True
    assert "+Disallow: /privado" in change.diff


def test_diff_robots_unchanged(db_session, make_job):
    from api.routers.diff import diff_summary
    from seo_crawler.robots_snapshot import persist_robots_snapshots

    a = make_job(name="run-1")
    b = make_job(name="run-2")
    a.client_id = b.client_id = "cliente-x"
    db_session.flush()
    for job in (a, b):
        persist_robots_snapshots(db_session, job.id, ["https://toy.local/"],
                                 fetch=lambda u: ROBOTS_V1)

    summary = diff_summary(a.id, b.id, pagerank_delta=0.5, segment_id=None,
                           db=db_session)
    assert summary.robots_changes[0].changed is False
    assert summary.robots_changes[0].diff is None


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def _watch(db_session, url, label=None, client="cliente-x"):
    from shared.models import WatchlistEntry
    from shared.url_normalization import compute_url_hash

    e = WatchlistEntry(client_id=client, url=url,
                       url_hash=compute_url_hash(url), label=label)
    db_session.add(e)
    db_session.flush()
    return e


@pytest.fixture()
def client_job(db_session, make_job):
    job = make_job()
    job.client_id = "cliente-x"
    db_session.flush()
    return job


def test_watchlist_url_gone_404_is_error(db_session, client_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    ok = _url(db_session, client_job, "/importante")
    rota = _url(db_session, client_job, "/clave", status=404)
    _watch(db_session, "https://toy.local/importante", "Home ventas")
    _watch(db_session, "https://toy.local/clave", "Landing clave")

    SEOAnalyzer(db_session, client_job.id).analyze_watchlist()
    db_session.flush()

    issues = db_session.query(Issue).filter(
        Issue.job_id == client_job.id,
        Issue.issue_type == "watchlist_check_failed",
    ).all()
    assert [i.url_id for i in issues] == [rota.id]
    assert issues[0].severity == "error"
    assert issues[0].details["reasons"] == ["status_404"]
    assert issues[0].details["label"] == "Landing clave"


def test_watchlist_detects_noindex_and_canonical(db_session, client_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    _url(db_session, client_job, "/noindex", indexable=False)
    _url(db_session, client_job, "/canonicalizada",
         canonical="https://toy.local/otra")
    _watch(db_session, "https://toy.local/noindex")
    _watch(db_session, "https://toy.local/canonicalizada")

    SEOAnalyzer(db_session, client_job.id).analyze_watchlist()
    db_session.flush()

    issues = db_session.query(Issue).filter(Issue.job_id == client_job.id).all()
    reasons = {tuple(i.details["reasons"]) for i in issues}
    assert ("not_indexable",) in reasons
    assert ("canonical_not_self",) in reasons


def test_watchlist_uncrawled_url_gets_not_crawled_row(db_session, client_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue, Url

    _watch(db_session, "https://toy.local/desaparecida")
    SEOAnalyzer(db_session, client_job.id).analyze_watchlist()
    db_session.flush()

    issue = db_session.query(Issue).filter(
        Issue.job_id == client_job.id
    ).one()
    assert issue.details["reasons"] == ["not_crawled"]
    row = db_session.query(Url).filter(Url.id == issue.url_id).one()
    assert row.status_group == "not_crawled"
    assert row.is_html is False


def test_watchlist_crud(db_session):
    from api.routers.clients import (
        WatchlistCreate, add_watchlist_entry, delete_watchlist_entry,
        list_watchlist,
    )

    entry = add_watchlist_entry(
        "cliente-x", WatchlistCreate(url="https://toy.local/vip", label="VIP"),
        db=db_session,
    )
    assert entry.url_hash
    assert [e.label for e in list_watchlist("cliente-x", db=db_session)] == ["VIP"]
    delete_watchlist_entry("cliente-x", entry.id, db=db_session)
    assert list_watchlist("cliente-x", db=db_session) == []


def test_watchlist_rejects_relative_url(db_session):
    from fastapi import HTTPException

    from api.routers.clients import WatchlistCreate, add_watchlist_entry

    with pytest.raises(HTTPException) as exc:
        add_watchlist_entry(
            "cliente-x", WatchlistCreate(url="/relativa"), db=db_session,
        )
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Umbrales sugeridos
# ---------------------------------------------------------------------------

def test_suggested_thresholds_from_percentiles(db_session, client_job):
    from api.routers.clients import suggested_thresholds

    # 10 páginas: word_count 100..1000, latencia 100..1000, outlinks 1..10
    for i in range(1, 11):
        _url(db_session, client_job, f"/p{i}", words=i * 100, ms=i * 100.0,
             outlinks=i)
    db_session.commit()

    result = suggested_thresholds("cliente-x", db=db_session)
    assert result.job_id == client_job.id
    s = result.suggestions
    assert s["min_word_count"] == 100     # P10 de 100..1000
    assert s["slow_page_ms"] == 900       # P90 redondeado a centenas
    assert s["max_outlinks"] == 10        # P95


def test_suggested_thresholds_without_jobs(db_session):
    from api.routers.clients import suggested_thresholds

    result = suggested_thresholds("cliente-sin-jobs", db=db_session)
    assert result.job_id is None
    assert result.suggestions == {}
