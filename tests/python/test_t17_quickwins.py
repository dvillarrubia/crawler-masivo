"""
Tests de T17 quick wins: 17.2 re-análisis, 17.3 percentiles + slow_page,
17.4 cadenas de canonicals, 17.6 splitter español, 17.7 export CSV.
(17.5 se testea en test_extractors_golden.py.)
"""

from __future__ import annotations

import pytest


def _url(db_session, job, path, *, status=200, ms=None, canonical=None,
         indexable=True):
    from shared.models import HtmlMeta, Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        is_internal=True, is_html=True, status_code=status,
        status_group=f"{status // 100}xx", response_time_ms=ms,
        indexable=indexable,
    )
    db_session.add(u)
    db_session.flush()
    if canonical is not None:
        db_session.add(HtmlMeta(url_id=u.id, canonical_href=canonical))
        db_session.flush()
    return u


# ---------------------------------------------------------------------------
# 17.3 — percentiles + slow_page
# ---------------------------------------------------------------------------

def test_percentiles_nearest_rank():
    from api.routers.results import _percentiles

    values = [float(i) for i in range(1, 101)]  # 1..100
    p = _percentiles(values)
    assert p == {"p50": 50.0, "p90": 90.0, "p99": 99.0}
    assert _percentiles([42.0]) == {"p50": 42.0, "p90": 42.0, "p99": 42.0}


def test_stats_latency_block(db_session, make_job):
    from api.routers.results import get_stats

    job = make_job()
    _url(db_session, job, "/a", ms=100)
    _url(db_session, job, "/b", ms=300)
    _url(db_session, job, "/c", status=404, ms=1000)
    _url(db_session, job, "/sin-tiempo")

    stats = get_stats(job.id, segment_id=None, db=db_session)
    assert stats.latency is not None
    assert stats.latency.p50 == 300.0
    assert stats.latency.p99 == 1000.0
    assert stats.latency.by_status_group["2xx"].p50 == 100.0
    assert stats.latency.by_status_group["4xx"].p50 == 1000.0


def test_stats_latency_none_without_timings(db_session, make_job):
    from api.routers.results import get_stats

    job = make_job()
    _url(db_session, job, "/a")
    assert get_stats(job.id, segment_id=None, db=db_session).latency is None


def test_slow_page_issue(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job()
    slow = _url(db_session, job, "/lenta", ms=4500)
    _url(db_session, job, "/rapida", ms=200)

    SEOAnalyzer(db_session, job.id).analyze_performance()
    db_session.flush()

    issues = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "slow_page"
    ).all()
    assert [i.url_id for i in issues] == [slow.id]
    assert issues[0].details["threshold_ms"] == 3000


def test_slow_page_threshold_configurable(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job(config={"analysis_thresholds": {"slow_page_ms": 10000}})
    _url(db_session, job, "/lenta", ms=4500)

    SEOAnalyzer(db_session, job.id).analyze_performance()
    db_session.flush()
    assert db_session.query(Issue).filter(Issue.job_id == job.id).count() == 0


# ---------------------------------------------------------------------------
# 17.4 — cadenas y bucles de canonicals
# ---------------------------------------------------------------------------

def test_canonical_chain_detected(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job()
    a = _url(db_session, job, "/a", canonical="https://toy.local/b")
    _url(db_session, job, "/b", canonical="https://toy.local/c")
    _url(db_session, job, "/c", canonical="https://toy.local/c")  # self = final

    SEOAnalyzer(db_session, job.id).analyze_canonical_chains()
    db_session.flush()

    chains = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "canonical_chain"
    ).all()
    assert [i.url_id for i in chains] == [a.id]
    assert chains[0].details["hops"] == 2
    assert chains[0].details["chain"] == [
        "https://toy.local/a", "https://toy.local/b", "https://toy.local/c",
    ]


def test_canonical_loop_detected(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job()
    a = _url(db_session, job, "/x", canonical="https://toy.local/y")
    b = _url(db_session, job, "/y", canonical="https://toy.local/x")

    SEOAnalyzer(db_session, job.id).analyze_canonical_chains()
    db_session.flush()

    loops = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "canonical_loop"
    ).all()
    assert {i.url_id for i in loops} == {a.id, b.id}
    assert all(i.severity == "error" for i in loops)


def test_single_hop_canonical_is_not_chain(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job()
    _url(db_session, job, "/a", canonical="https://toy.local/b")
    _url(db_session, job, "/b", canonical="https://toy.local/b")

    SEOAnalyzer(db_session, job.id).analyze_canonical_chains()
    db_session.flush()
    assert db_session.query(Issue).filter(Issue.job_id == job.id).count() == 0


# ---------------------------------------------------------------------------
# 17.6 — splitter de frases para español
# ---------------------------------------------------------------------------

GOLDEN_SENTENCES = [
    (
        "El Sr. García llegó tarde. La reunión empezó sin él.",
        ["El Sr. García llegó tarde.", "La reunión empezó sin él."],
    ),
    (
        "Ver pág. 42 del vol. 3 para más detalles. Fin.",
        ["Ver pág. 42 del vol. 3 para más detalles.", "Fin."],
    ),
    (
        "La sede de EE. UU. abrió en 2020. Luego creció.",
        ["La sede de EE. UU. abrió en 2020.", "Luego creció."],
    ),
    (
        "Según el art. 15, aplica el núm. 7. ¡Confirmado!",
        ["Según el art. 15, aplica el núm. 7.", "¡Confirmado!"],
    ),
    (
        "La Dra. Ruiz y el Dr. Gil operan hoy.",
        ["La Dra. Ruiz y el Dr. Gil operan hoy."],
    ),
]


@pytest.mark.parametrize("text,expected", GOLDEN_SENTENCES)
def test_spanish_sentence_splitter(text, expected):
    numpy = pytest.importorskip("numpy")  # text_utils lo importa
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from POC_centro_semantico.src.text_utils import _split_sentences

    assert _split_sentences(text) == expected


def test_splitter_still_splits_normal_sentences():
    pytest.importorskip("numpy")
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from POC_centro_semantico.src.text_utils import _split_sentences

    assert _split_sentences("Una frase. Otra frase. ¿Y una tercera?") == [
        "Una frase.", "Otra frase.", "¿Y una tercera?",
    ]


# ---------------------------------------------------------------------------
# 17.7 — export CSV de issues y links
# ---------------------------------------------------------------------------

@pytest.fixture()
def _patched_sessionlocal(db_engine, monkeypatch):
    """Los streamers abren su propia sesión: apúntalas a la BD del test."""
    from sqlalchemy.orm import sessionmaker

    import shared.database as shared_db

    monkeypatch.setattr(
        shared_db, "SessionLocal",
        sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False),
    )


def test_export_issues_csv(db_session, make_job, _patched_sessionlocal):
    from analysis.analyzer import SEOAnalyzer
    from api.routers.results import _stream_issues_csv

    job = make_job()
    _url(db_session, job, "/lenta", ms=9000)
    SEOAnalyzer(db_session, job.id).analyze_performance()
    db_session.commit()

    csv_text = "".join(_stream_issues_csv(job.id))
    lines = csv_text.strip().splitlines()
    assert lines[0] == "url,issue_type,severity,details,detected_at"
    assert len(lines) == 2
    assert "slow_page" in lines[1]
    assert "https://toy.local/lenta" in lines[1]


def test_export_links_csv(db_session, make_job, _patched_sessionlocal):
    from api.routers.results import _stream_links_csv
    from shared.models import Link
    from shared.url_normalization import compute_url_hash

    job = make_job()
    src = _url(db_session, job, "/origen")
    db_session.add(Link(
        job_id=job.id, from_url_id=src.id,
        to_url="https://toy.local/destino",
        to_url_hash=compute_url_hash("https://toy.local/destino"),
        is_internal=True, follow=True, link_position="content",
        anchor_text="ancla", dom_ancestor="main", dom_container="p.intro",
    ))
    db_session.commit()

    csv_text = "".join(_stream_links_csv(job.id))
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("from_url,to_url,anchor_text")
    assert len(lines) == 2
    assert "https://toy.local/origen" in lines[1]
    assert "main" in lines[1]


# ---------------------------------------------------------------------------
# 17.2 — re-análisis sin re-crawl
# ---------------------------------------------------------------------------

def test_reanalyze_merges_thresholds_and_queues_task(db_session, make_job):
    from fastapi import BackgroundTasks

    from api.routers.jobs import reanalyze_job
    from api.schemas import AnalysisThresholdsConfig, ReanalyzeRequest

    job = make_job(config={"analysis_thresholds": {"title_min_length": 20}})
    bg = BackgroundTasks()

    result = reanalyze_job(
        job.id, bg,
        payload=ReanalyzeRequest(
            analysis_thresholds=AnalysisThresholdsConfig(slow_page_ms=500),
        ),
        db=db_session,
    )
    assert result["status"] == "reanalysis_started"
    assert len(bg.tasks) == 1

    db_session.expire_all()
    t = job.config["analysis_thresholds"]
    assert t["slow_page_ms"] == 500       # nuevo
    assert t["title_min_length"] == 20    # preservado


def test_reanalyze_rejects_running_jobs(db_session, make_job):
    from fastapi import BackgroundTasks, HTTPException

    from api.routers.jobs import reanalyze_job

    job = make_job()
    job.status = "running"
    db_session.flush()

    with pytest.raises(HTTPException) as exc:
        reanalyze_job(job.id, BackgroundTasks(), payload=None, db=db_session)
    assert exc.value.status_code == 409


def test_reanalysis_does_not_mutate_crawl_data(db_session, make_job):
    """Criterio del plan: los datos de crawl son inmutables al re-analizar."""
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Url
    from toygraph import build_toy_graph

    job, _ = build_toy_graph(db_session, make_job)

    def _crawl_snapshot():
        return sorted(
            (u.url, u.status_code, u.is_html, u.body_hash, u.crawl_depth)
            for u in db_session.query(Url).filter(Url.job_id == job.id)
        )

    before = _crawl_snapshot()
    analyzer = SEOAnalyzer(db_session, job.id)
    analyzer.run_all()
    analyzer.run_all()  # re-análisis
    db_session.expire_all()
    assert _crawl_snapshot() == before