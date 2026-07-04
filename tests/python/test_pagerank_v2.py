"""
Tests de T3 — PageRank v2 (nofollow diluyente, decay 301, solo-indexables,
equity_leak) y conmutación v1/v2 por job.config.

Los casos (a), (b) y (c) son los exigidos por el plan; (d) verifica la
corrección C3 de pesos nav/sidebar SOLO en v2.
"""

from __future__ import annotations

import pytest

V2_CONFIG = {"analysis_thresholds": {"pagerank_version": 2}}


def _url(db_session, job, path, *, status=200, indexable=True,
         redirect_to=None, internal=True):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = path if path.startswith("http") else f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        is_internal=internal, is_html=True, status_code=status,
        status_group=f"{status // 100}xx", indexable=indexable,
        redirect_url=redirect_to,
    )
    db_session.add(u)
    db_session.flush()
    return u


def _link(db_session, job, src, dst_url, *, position="content", follow=True):
    from shared.models import Link
    from shared.url_normalization import compute_url_hash

    db_session.add(Link(
        job_id=job.id, from_url_id=src.id, to_url=dst_url,
        to_url_hash=compute_url_hash(dst_url), is_internal=True,
        follow=follow, link_position=position,
    ))
    db_session.flush()


def _pageranks(db_session, job):
    from shared.models import Url

    rows = db_session.query(Url.url, Url.pagerank).filter(
        Url.job_id == job.id
    ).all()
    return dict(rows)


# ---------------------------------------------------------------------------
# (a) nofollow diluyente
# ---------------------------------------------------------------------------

def test_nofollow_dilutes_instead_of_redistributing(db_session, make_job):
    """1 follow + 1 nofollow: la fracción nofollow se destruye — el destino
    nofollow queda igual que una página aislada sin inlinks."""
    from analysis.analyzer import SEOAnalyzer

    job = make_job(config=V2_CONFIG)
    a = _url(db_session, job, "/a")
    _url(db_session, job, "/b")
    _url(db_session, job, "/c")
    _url(db_session, job, "/isla")  # sin enlaces: referencia de base
    _link(db_session, job, a, "https://toy.local/b", follow=True)
    _link(db_session, job, a, "https://toy.local/c", follow=False)

    SEOAnalyzer(db_session, job.id).compute_pagerank()
    pr = _pageranks(db_session, job)

    # el destino follow recibe; el nofollow NO recibe nada
    assert pr["https://toy.local/b"] > pr["https://toy.local/c"]
    assert pr["https://toy.local/c"] == pytest.approx(pr["https://toy.local/isla"], abs=1e-4)


def test_nofollow_halves_transmission_vs_two_follows(db_session, make_job):
    """Con 1F+1NF el destino follow recibe lo mismo que con 2F (mitad del
    total emitido): B no mejora porque C sea nofollow (sin sculpting)."""
    from analysis.analyzer import SEOAnalyzer

    job1 = make_job(name="nf", config=V2_CONFIG)
    a1 = _url(db_session, job1, "/a")
    _url(db_session, job1, "/b")
    _url(db_session, job1, "/c")
    _link(db_session, job1, a1, "https://toy.local/b", follow=True)
    _link(db_session, job1, a1, "https://toy.local/c", follow=False)

    job2 = make_job(name="ff", config=V2_CONFIG)
    a2 = _url(db_session, job2, "/a")
    _url(db_session, job2, "/b")
    _url(db_session, job2, "/c")
    _link(db_session, job2, a2, "https://toy.local/b", follow=True)
    _link(db_session, job2, a2, "https://toy.local/c", follow=True)

    SEOAnalyzer(db_session, job1.id).compute_pagerank()
    SEOAnalyzer(db_session, job2.id).compute_pagerank()
    pr1 = _pageranks(db_session, job1)
    pr2 = _pageranks(db_session, job2)

    # B recibe la MISMA fracción en ambos: PR_A*0.85*(1/2). Al normalizar
    # ambos grafos por el mismo máximo (A), los ratios son comparables.
    ratio1 = pr1["https://toy.local/b"] / pr1["https://toy.local/a"]
    ratio2 = pr2["https://toy.local/b"] / pr2["https://toy.local/a"]
    assert ratio1 == pytest.approx(ratio2, rel=1e-3)


# ---------------------------------------------------------------------------
# (b) colapso de redirecciones con decay
# ---------------------------------------------------------------------------

def test_redirect_collapse_with_decay(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer

    # Grafo con redirección: A → B(301→C), C final
    job = make_job(name="redir", config=V2_CONFIG)
    a = _url(db_session, job, "/a")
    _url(db_session, job, "/b", status=301, redirect_to="https://toy.local/c")
    _url(db_session, job, "/c")
    _link(db_session, job, a, "https://toy.local/b")

    # Control: A → C directo
    ctrl = make_job(name="direct", config=V2_CONFIG)
    a2 = _url(db_session, ctrl, "/a")
    _url(db_session, ctrl, "/c")
    _link(db_session, ctrl, a2, "https://toy.local/c")

    SEOAnalyzer(db_session, job.id).compute_pagerank()
    SEOAnalyzer(db_session, ctrl.id).compute_pagerank()
    pr = _pageranks(db_session, job)
    pr_ctrl = _pageranks(db_session, ctrl)

    # la 301 es pass-through: sin PageRank propio en v2
    assert pr["https://toy.local/b"] is None
    # C recibe, pero con decay 0.9 respecto al enlace directo
    ratio_redir = pr["https://toy.local/c"] / pr["https://toy.local/a"]
    ratio_direct = pr_ctrl["https://toy.local/c"] / pr_ctrl["https://toy.local/a"]
    assert ratio_redir < ratio_direct


def test_redirect_loop_is_cut(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer

    job = make_job(config=V2_CONFIG)
    a = _url(db_session, job, "/a")
    _url(db_session, job, "/l1", status=301, redirect_to="https://toy.local/l2")
    _url(db_session, job, "/l2", status=301, redirect_to="https://toy.local/l1")
    _url(db_session, job, "/b")
    _link(db_session, job, a, "https://toy.local/l1")
    _link(db_session, job, a, "https://toy.local/b")

    SEOAnalyzer(db_session, job.id).compute_pagerank()  # no cuelga
    pr = _pageranks(db_session, job)
    assert pr["https://toy.local/l1"] is None
    assert pr["https://toy.local/b"] is not None


# ---------------------------------------------------------------------------
# (c) v1 por defecto y bit a bit
# ---------------------------------------------------------------------------

def test_default_version_is_v1_and_matches_snapshot(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Url
    from test_pagerank_v1_snapshot import EXPECTED_PAGERANK
    from toygraph import build_toy_graph

    job, _ = build_toy_graph(db_session, make_job)  # config vacía
    analyzer = SEOAnalyzer(db_session, job.id)
    assert analyzer.pagerank_version == 1
    analyzer.compute_pagerank()

    rows = db_session.query(Url.url, Url.pagerank).filter(
        Url.job_id == job.id, Url.is_internal.is_(True)
    ).all()
    for url, pr in rows:
        assert pr == pytest.approx(EXPECTED_PAGERANK[url], abs=1e-4)

    db_session.expire_all()
    assert job.config.get("_pagerank_version_used") == 1


def test_v2_records_version_in_job_config(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer

    job = make_job(config=V2_CONFIG)
    a = _url(db_session, job, "/a")
    _url(db_session, job, "/b")
    _link(db_session, job, a, "https://toy.local/b")

    SEOAnalyzer(db_session, job.id).compute_pagerank()
    db_session.flush()
    db_session.expire_all()
    assert job.config.get("_pagerank_version_used") == 2
    assert job.config["analysis_thresholds"]["pagerank_version"] == 2  # intacta


# ---------------------------------------------------------------------------
# (d) C3: pesos nav/sidebar corregidos SOLO en v2
# ---------------------------------------------------------------------------

def _nav_vs_header_graph(db_session, make_job, config, name):
    job = make_job(name=name, config=config)
    a = _url(db_session, job, "/a")
    _url(db_session, job, "/via-nav")
    _url(db_session, job, "/via-header")
    _link(db_session, job, a, "https://toy.local/via-nav", position="nav")
    _link(db_session, job, a, "https://toy.local/via-header", position="header")
    return job


def test_c3_nav_weight_fixed_only_in_v2(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer

    v1 = _nav_vs_header_graph(db_session, make_job, {}, "v1")
    v2 = _nav_vs_header_graph(db_session, make_job, V2_CONFIG, "v2")
    SEOAnalyzer(db_session, v1.id).compute_pagerank()
    SEOAnalyzer(db_session, v2.id).compute_pagerank()
    pr1 = _pageranks(db_session, v1)
    pr2 = _pageranks(db_session, v2)

    # v1: bug latente congelado — nav (default 0.5) pesa MÁS que header (0.3)
    assert pr1["https://toy.local/via-nav"] > pr1["https://toy.local/via-header"]
    # v2: corregido — nav (0.2) pesa MENOS que header (0.3)
    assert pr2["https://toy.local/via-nav"] < pr2["https://toy.local/via-header"]


# ---------------------------------------------------------------------------
# solo-indexables + equity_leak
# ---------------------------------------------------------------------------

def test_equity_leak_issue_and_indexable_only_graph(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job(config=V2_CONFIG)
    a = _url(db_session, job, "/a")
    _url(db_session, job, "/ok")
    _url(db_session, job, "/rota-1", status=404)
    _url(db_session, job, "/rota-2", status=404)
    _url(db_session, job, "/noindex", indexable=False)
    _link(db_session, job, a, "https://toy.local/ok")
    _link(db_session, job, a, "https://toy.local/rota-1")
    _link(db_session, job, a, "https://toy.local/rota-2")
    _link(db_session, job, a, "https://toy.local/noindex")

    analyzer = SEOAnalyzer(db_session, job.id)
    analyzer.compute_pagerank()
    db_session.flush()
    pr = _pageranks(db_session, job)

    # los no-nodos no acumulan rank propio
    assert pr["https://toy.local/rota-1"] is None
    assert pr["https://toy.local/noindex"] is None
    assert pr["https://toy.local/ok"] is not None

    # 3 de 4 aristas destruidas (75% ≥ umbral 30%) → equity_leak en A
    issues = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "equity_leak"
    ).all()
    assert len(issues) == 1
    assert issues[0].url_id == a.id
    assert issues[0].details["leak_ratio"] == pytest.approx(0.75, abs=1e-4)
    assert issues[0].details["leaked_edges"] == 3


def test_equity_leak_respects_threshold(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    config = {"analysis_thresholds": {"pagerank_version": 2,
                                      "equity_leak_threshold": 0.8}}
    job = make_job(config=config)
    a = _url(db_session, job, "/a")
    _url(db_session, job, "/ok")
    _url(db_session, job, "/rota", status=404)
    _link(db_session, job, a, "https://toy.local/ok")
    _link(db_session, job, a, "https://toy.local/rota")

    SEOAnalyzer(db_session, job.id).compute_pagerank()
    db_session.flush()

    # 50% < 80% → sin issue
    count = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "equity_leak"
    ).count()
    assert count == 0
