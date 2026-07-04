"""
Tests del cierre de T18 (relevancia de anchors + anchor propuesto en las
sugerencias T10) y de la caché de grafo del simulador T21.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers compartidos
# ---------------------------------------------------------------------------

def _url(db_session, job, path, *, status=200, pagerank=None, word_count=None):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        host="toy.local", path=path, scheme="https",
        is_internal=True, is_html=True, status_code=status,
        status_group=f"{status // 100}xx", pagerank=pagerank,
        word_count=word_count, indexable=True,
    )
    db_session.add(u)
    db_session.flush()
    return u


def _link(db_session, job, src, dst_path, *, anchor=None, position="content",
          follow=True, edge_class=None):
    from shared.models import Link
    from shared.url_normalization import compute_url_hash

    dst = f"https://toy.local{dst_path}"
    l = Link(
        job_id=job.id, from_url_id=src.id, to_url=dst,
        to_url_hash=compute_url_hash(dst), is_internal=True,
        follow=follow, link_position=position, anchor_text=anchor,
        edge_class=edge_class,
    )
    db_session.add(l)
    db_session.flush()
    return l


def _pad(v):
    return list(v) + [0.0] * (1024 - len(v))


@pytest.fixture()
def semantic_tables(db_engine):
    pytest.importorskip("pgvector")
    from pgvector.sqlalchemy import Vector
    from sqlalchemy.ext.compiler import compiles

    @compiles(Vector, "sqlite")
    def _vector_sqlite(type_, compiler, **kw):
        return "TEXT"

    from shared.semantic_models import SemanticAnalysis, SemanticPage

    SemanticAnalysis.__table__.create(db_engine, checkfirst=True)
    SemanticPage.__table__.create(db_engine, checkfirst=True)
    return SemanticAnalysis, SemanticPage


class FakeBackend:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def embed_queries(self, texts, progress_callback=None):
        import numpy as np

        self.calls.append(list(texts))
        return np.asarray([_pad(self.mapping[t]) for t in texts], dtype="float32")


# ---------------------------------------------------------------------------
# T18 — núcleo puro
# ---------------------------------------------------------------------------

def test_is_generic_anchor():
    from analysis.anchor_relevance import is_generic_anchor

    for generic in ("aquí", "Haz clic aquí", "LEER MÁS", "click here",
                    "más información", "12", "→", ""):
        assert is_generic_anchor(generic), generic
    for good in ("crema hidratante facial", "guía de fiscalidad",
                 "zapatillas trail running"):
        assert not is_generic_anchor(good), good


def test_classify_anchors_core():
    from analysis.anchor_relevance import AnchorGroup, classify_anchors

    groups = [
        AnchorGroup(anchor="leer más", target_hash="t1", n_links=3),
        AnchorGroup(anchor="crema solar", target_hash="t1", n_links=2),
        AnchorGroup(anchor="receta de pizza", target_hash="t1", n_links=1),
        AnchorGroup(anchor="sin vector", target_hash="t2", n_links=1),
    ]
    anchor_vecs = {
        "crema solar": (1, 0),
        "receta de pizza": (0, 1),
        "sin vector": (1, 0),
    }
    target_vecs = {"t1": (1, 0)}  # t2 sin vector

    r = classify_anchors(groups, anchor_vecs, target_vecs, mismatch_threshold=0.35)
    assert [g.anchor for g in r["generic"]] == ["leer más"]
    assert len(r["mismatches"]) == 1
    assert r["mismatches"][0]["group"].anchor == "receta de pizza"
    assert r["mismatches"][0]["similarity"] == pytest.approx(0.0)
    assert r["skipped"] == 1  # t2 sin vector de destino


# ---------------------------------------------------------------------------
# T18 — wrapper DB
# ---------------------------------------------------------------------------

def _setup_anchor_job(db_session, make_job, semantic_tables):
    SemanticAnalysis, SemanticPage = semantic_tables
    job = make_job()
    analysis = SemanticAnalysis(job_id=job.id, status="completed")
    db_session.add(analysis)
    db_session.flush()

    home = _url(db_session, job, "/")
    target = _url(db_session, job, "/crema-solar")
    db_session.add(SemanticPage(
        analysis_id=analysis.id, url_id=target.id, embedding=_pad((1, 0)),
    ))
    db_session.flush()

    # contextual bueno, contextual mismatch, genérico, y uno de menú que
    # debe ignorarse aunque su anchor sea genérico
    _link(db_session, job, home, "/crema-solar", anchor="crema solar factor 50")
    _link(db_session, job, home, "/crema-solar", anchor="receta de pizza casera")
    _link(db_session, job, home, "/crema-solar", anchor="leer más")
    _link(db_session, job, home, "/crema-solar", anchor="aquí", position="nav")

    backend = FakeBackend({
        "crema solar factor 50": (1, 0),
        "receta de pizza casera": (0, 1),
    })
    return job, analysis, target, backend


def test_anchor_wrapper_emits_signable_issues(
    db_session, make_job, semantic_tables,
):
    from analysis.anchor_relevance import run_anchor_relevance
    from shared.models import Issue

    job, analysis, target, backend = _setup_anchor_job(
        db_session, make_job, semantic_tables,
    )
    r = run_anchor_relevance(db_session, job.id, analysis.id, backend)
    assert r["status"] == "ok"
    # solo se embeben los contextuales no genéricos
    assert backend.calls == [["crema solar factor 50", "receta de pizza casera"]]
    assert r["summary"]["mismatches"] == 1
    assert r["summary"]["generic_targets"] == 1

    issues = db_session.query(Issue).filter(Issue.job_id == job.id).all()
    by_type = {i.issue_type: i for i in issues}
    mm = by_type["anchor_target_mismatch"]
    assert mm.url_id == target.id and mm.review_status == "pending"
    assert mm.details["anchor"] == "receta de pizza casera"
    ga = by_type["generic_anchor"]
    assert ga.url_id == target.id and ga.review_status == "pending"
    # el "aquí" del menú NO cuenta: solo contextual
    assert ga.details["anchors"] == ["leer más"]
    assert ga.details["generic_inlinks"] == 1


def test_anchor_wrapper_rerun_preserves_signed(
    db_session, make_job, semantic_tables,
):
    from analysis.anchor_relevance import run_anchor_relevance
    from shared.models import Issue

    job, analysis, _, backend = _setup_anchor_job(
        db_session, make_job, semantic_tables,
    )
    run_anchor_relevance(db_session, job.id, analysis.id, backend)
    mm = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "anchor_target_mismatch",
    ).one()
    mm.review_status = "rejected"
    mm.reviewed_by = "tester"
    db_session.flush()

    run_anchor_relevance(db_session, job.id, analysis.id, backend)
    mms = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "anchor_target_mismatch",
    ).all()
    assert {m.review_status for m in mms} == {"rejected", "pending"}


def test_anchor_wrapper_blocked_without_anchors(
    db_session, make_job, semantic_tables,
):
    from analysis.anchor_relevance import run_anchor_relevance

    SemanticAnalysis, _ = semantic_tables
    job = make_job()
    analysis = SemanticAnalysis(job_id=job.id, status="completed")
    db_session.add(analysis)
    db_session.flush()
    r = run_anchor_relevance(db_session, job.id, analysis.id, FakeBackend({}))
    assert r == {"status": "blocked", "reason": "no_contextual_anchors"}


# ---------------------------------------------------------------------------
# T18 — anchor propuesto en las sugerencias T10
# ---------------------------------------------------------------------------

def test_suggestions_carry_proposed_anchor(
    db_session, make_job, semantic_tables,
):
    from analysis.link_suggester import generate_for_job
    from shared.models import Heading, HtmlMeta, LinkSuggestion

    SemanticAnalysis, SemanticPage = semantic_tables
    job = make_job()
    analysis = SemanticAnalysis(job_id=job.id, status="completed")
    db_session.add(analysis)
    db_session.flush()

    source = _url(db_session, job, "/hub", pagerank=10.0, word_count=500)
    target = _url(db_session, job, "/objetivo", pagerank=1.0, word_count=300)
    other = _url(db_session, job, "/otro", pagerank=5.0, word_count=400)
    for u, vec in ((source, (1, 0)), (target, (0.99, 0.14)), (other, (0, 1))):
        db_session.add(SemanticPage(
            analysis_id=analysis.id, url_id=u.id, embedding=_pad(vec),
        ))
    db_session.add(HtmlMeta(url_id=target.id, title="Title del objetivo"))
    db_session.add(Heading(url_id=target.id, tag="h1", position=1,
                           text="Guía definitiva del objetivo"))
    db_session.flush()

    n = generate_for_job(db_session, job.id, analysis.id)
    assert n >= 1
    s = db_session.query(LinkSuggestion).filter(
        LinkSuggestion.job_id == job.id,
        LinkSuggestion.target_url_hash == target.url_hash,
    ).first()
    # el H1 gana sobre el title
    assert s.proposed_anchor == "Guía definitiva del objetivo"


def test_proposed_anchor_falls_back_to_title(
    db_session, make_job, semantic_tables,
):
    from analysis.link_suggester import generate_for_job
    from shared.models import HtmlMeta, LinkSuggestion

    SemanticAnalysis, SemanticPage = semantic_tables
    job = make_job()
    analysis = SemanticAnalysis(job_id=job.id, status="completed")
    db_session.add(analysis)
    db_session.flush()

    source = _url(db_session, job, "/hub", pagerank=10.0, word_count=500)
    target = _url(db_session, job, "/objetivo", pagerank=1.0, word_count=300)
    for u, vec in ((source, (1, 0)), (target, (0.99, 0.14))):
        db_session.add(SemanticPage(
            analysis_id=analysis.id, url_id=u.id, embedding=_pad(vec),
        ))
    db_session.add(HtmlMeta(url_id=target.id, title="Solo hay title"))
    db_session.flush()

    generate_for_job(db_session, job.id, analysis.id)
    s = db_session.query(LinkSuggestion).filter(
        LinkSuggestion.job_id == job.id,
        LinkSuggestion.target_url_hash == target.url_hash,
    ).first()
    assert s.proposed_anchor == "Solo hay title"


# ---------------------------------------------------------------------------
# T21 — caché de grafo del simulador
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_graph_cache():
    from api.routers import simulate

    simulate._graph_cache.clear()
    yield
    simulate._graph_cache.clear()


def _simulate_job(db_session, make_job):
    job = make_job()
    a = _url(db_session, job, "/a", pagerank=5.0)
    b = _url(db_session, job, "/b", pagerank=1.0)
    _link(db_session, job, a, "/b", anchor="b")
    return job, a, b


def test_simulator_uses_graph_cache(db_session, make_job, monkeypatch):
    from api.routers import simulate
    from api.routers.simulate import SimulateRequest, simulate_pagerank

    job, a, b = _simulate_job(db_session, make_job)

    calls = {"n": 0}
    real_load = simulate._load_graph

    def counting_load(db, job_id):
        calls["n"] += 1
        return real_load(db, job_id)

    monkeypatch.setattr(simulate, "_load_graph", counting_load)

    payload = SimulateRequest(
        add=[{"from_hash": b.url_hash, "to_hash": a.url_hash, "position": "content"}],
    )
    r1 = simulate_pagerank(job.id, payload, db=db_session)
    r2 = simulate_pagerank(job.id, payload, db=db_session)
    assert calls["n"] == 1  # segunda simulación servida de caché
    assert r1["top_deltas"] == r2["top_deltas"]

    # fresh=true fuerza recarga
    payload_fresh = SimulateRequest(
        add=[{"from_hash": b.url_hash, "to_hash": a.url_hash, "position": "content"}],
        fresh=True,
    )
    simulate_pagerank(job.id, payload_fresh, db=db_session)
    assert calls["n"] == 2


def test_simulator_cache_expires_with_ttl(db_session, make_job, monkeypatch):
    from api.routers import simulate
    from api.routers.simulate import SimulateRequest, simulate_pagerank

    job, a, b = _simulate_job(db_session, make_job)

    calls = {"n": 0}
    real_load = simulate._load_graph

    def counting_load(db, job_id):
        calls["n"] += 1
        return real_load(db, job_id)

    monkeypatch.setattr(simulate, "_load_graph", counting_load)

    payload = SimulateRequest(
        add=[{"from_hash": b.url_hash, "to_hash": a.url_hash, "position": "content"}],
    )
    simulate_pagerank(job.id, payload, db=db_session)
    # envejecemos la entrada más allá del TTL
    key = next(iter(simulate._graph_cache))
    ts, data = simulate._graph_cache[key]
    simulate._graph_cache[key] = (ts - simulate.GRAPH_CACHE_TTL - 1, data)

    simulate_pagerank(job.id, payload, db=db_session)
    assert calls["n"] == 2
