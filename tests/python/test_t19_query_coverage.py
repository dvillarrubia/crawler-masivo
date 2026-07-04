"""
Tests de T19 — cobertura consulta→pasaje (T9 + T11).

Núcleo puro con vectores inyectados (sin Gemini ni pgvector) + wrapper
DB sobre SQLite con un backend falso. Regla dura T10 verificada: los
issues nacen ``pending`` y las decisiones firmadas sobreviven al re-run.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _url(db_session, job, path, *, status=200):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        host="toy.local", path=path, scheme="https",
        is_internal=True, is_html=True, status_code=status,
        status_group=f"{status // 100}xx",
    )
    db_session.add(u)
    db_session.flush()
    return u


def _pad(v):
    """El tipo Vector valida 1024 dims incluso en SQLite."""
    return list(v) + [0.0] * (1024 - len(v))


@pytest.fixture()
def t19_tables(db_engine):
    pytest.importorskip("pgvector")
    from pgvector.sqlalchemy import Vector
    from sqlalchemy.ext.compiler import compiles

    @compiles(Vector, "sqlite")
    def _vector_sqlite(type_, compiler, **kw):
        return "TEXT"

    from shared.semantic_models import (
        GscQueryData,
        QueryEmbedding,
        SemanticAnalysis,
        SemanticChunk,
    )

    for model in (SemanticAnalysis, SemanticChunk, GscQueryData, QueryEmbedding):
        model.__table__.create(db_engine, checkfirst=True)
    return SemanticAnalysis, SemanticChunk, GscQueryData, QueryEmbedding


class FakeBackend:
    """Devuelve el vector (padded) asignado a cada query, como haría
    embed_queries con RETRIEVAL_QUERY."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def embed_queries(self, texts, progress_callback=None):
        import numpy as np

        self.calls.append(list(texts))
        return np.asarray([_pad(self.mapping[t]) for t in texts], dtype="float32")


# ---------------------------------------------------------------------------
# Núcleo puro
# ---------------------------------------------------------------------------

def test_core_flags_gap_buried_and_orphan():
    from analysis.query_coverage import ChunkVector, QueryVector, compute_coverage

    chunks = [
        ChunkVector(chunk_id=1, url_id=10, position=0, vector=(1, 0, 0, 0)),
        ChunkVector(chunk_id=2, url_id=10, position=7, vector=(0, 1, 0, 0)),
        ChunkVector(chunk_id=3, url_id=20, position=0, vector=(0, 0, 1, 0)),
    ]
    queries = [
        QueryVector("cubierta", (1, 0, 0, 0), 5, 100, 3.0, 10),
        QueryVector("enterrada", (0, 1, 0, 0), 2, 80, 8.0, 10),
        QueryVector("sin pasaje", (0, 0, 0, 1), 1, 60, 15.0, 20),
    ]

    r = compute_coverage(
        queries, chunks,
        sim_threshold=0.6, buried_min_position=5, orphan_threshold=0.5,
    )
    by_q = {row["query"]: row for row in r["per_query"]}

    assert by_q["cubierta"]["covered"] and not by_q["cubierta"]["buried"]
    assert by_q["cubierta"]["best_chunk_id"] == 1

    assert by_q["enterrada"]["covered"] and by_q["enterrada"]["buried"]
    assert by_q["enterrada"]["best_chunk_position"] == 7

    assert not by_q["sin pasaje"]["covered"]
    assert by_q["sin pasaje"]["best_similarity"] < 0.6

    # el chunk 3 no responde a ninguna query → huérfano; 1 y 2 no
    assert r["orphan_chunk_ids"] == [3]


def test_core_normalizes_unnormalized_vectors():
    from analysis.query_coverage import ChunkVector, QueryVector, compute_coverage

    # mismos rumbos con módulos distintos: el coseno debe dar 1.0
    chunks = [ChunkVector(chunk_id=1, url_id=1, position=0, vector=(10, 0))]
    queries = [QueryVector("q", (0.3, 0), 0, 10, None, 1)]
    r = compute_coverage(queries, chunks, sim_threshold=0.9)
    assert r["per_query"][0]["best_similarity"] == pytest.approx(1.0)
    assert r["per_query"][0]["covered"]


def test_core_empty_inputs():
    from analysis.query_coverage import compute_coverage

    r = compute_coverage([], [])
    assert r == {"per_query": [], "orphan_chunk_ids": [], "chunk_max_sim": {}}


# ---------------------------------------------------------------------------
# Wrapper DB — issues firmables + caché en query_embeddings
# ---------------------------------------------------------------------------

def _setup_job(db_session, make_job, t19_tables):
    SemanticAnalysis, SemanticChunk, GscQueryData, _ = t19_tables
    job = make_job()
    analysis = SemanticAnalysis(job_id=job.id, status="completed")
    db_session.add(analysis)
    db_session.flush()

    u1 = _url(db_session, job, "/guia")
    u2 = _url(db_session, job, "/otra")

    # chunks: /guia cubre "tema uno" arriba; /otra cubre "tema dos" pero
    # enterrado (posición 6); nadie cubre "tema perdido"; el chunk 3 de
    # /otra no responde a ninguna query (huérfano).
    for url, pos, vec in (
        (u1, 0, (1, 0, 0)), (u2, 6, (0, 1, 0)), (u2, 0, (0, 0, 1)),
    ):
        db_session.add(SemanticChunk(
            analysis_id=analysis.id, url_id=url.id, position=pos,
            text=f"chunk {pos}", embedding=_pad(vec),
        ))

    rows = [
        ("tema uno", u1, 10, 100, 3.0),
        ("tema dos", u2, 4, 90, 6.0),
        ("tema perdido", u1, 1, 50, 14.0),
        ("irrelevante", u1, 0, 3, 40.0),   # < min_impressions, fuera
    ]
    for q, url, clicks, imprs, pos in rows:
        db_session.add(GscQueryData(
            job_id=job.id, url_id=url.id, query=q,
            clicks=clicks, impressions=imprs, position=pos,
        ))
    db_session.flush()

    backend = FakeBackend({
        "tema uno": (1, 0, 0),
        "tema dos": (0, 1, 0),
        # rumbo propio (4ª dim): sim ≈ 0.30 con chunks 1-2 (gap) y 0.0
        # con el chunk 3, que queda huérfano
        "tema perdido": (0.3, 0.3, 0, 0.9),
    })
    return job, analysis, u1, u2, backend


def test_wrapper_emits_signable_issues_and_cache(
    db_session, make_job, t19_tables,
):
    from analysis.query_coverage import run_query_coverage
    from shared.models import Issue

    _, _, GscQueryData, QueryEmbedding = t19_tables
    job, analysis, u1, u2, backend = _setup_job(db_session, make_job, t19_tables)

    result = run_query_coverage(
        db_session, job.id, analysis.id, backend,
        min_impressions=10, sim_threshold=0.6,
        buried_min_position=5, orphan_threshold=0.5,
    )
    assert result["status"] == "ok"
    # la query bajo el umbral de impresiones no se embebe
    assert backend.calls == [["tema uno", "tema dos", "tema perdido"]]

    s = result["summary"]
    assert s["queries_analyzed"] == 3
    assert s["covered"] == 2 and s["gaps"] == 1 and s["buried"] == 1
    assert s["orphan_chunks"] == 1

    # issues firmables: nacen pending (regla dura T10)
    issues = db_session.query(Issue).filter(Issue.job_id == job.id).all()
    by_type = {}
    for i in issues:
        by_type.setdefault(i.issue_type, []).append(i)
    assert len(by_type["passage_gap"]) == 1
    assert by_type["passage_gap"][0].url_id == u1.id
    assert by_type["passage_gap"][0].review_status == "pending"
    assert by_type["passage_gap"][0].details["query"] == "tema perdido"
    assert len(by_type["buried_passage"]) == 1
    assert by_type["buried_passage"][0].url_id == u2.id
    assert len(by_type["orphan_chunk"]) == 1
    assert by_type["orphan_chunk"][0].url_id == u2.id
    assert by_type["orphan_chunk"][0].details["orphan_chunks"] == 1

    # caché persistida para el GET
    cached = db_session.query(QueryEmbedding).filter(
        QueryEmbedding.job_id == job.id,
    ).all()
    assert len(cached) == 3
    perdido = next(c for c in cached if c.query == "tema perdido")
    assert perdido.covered is False and perdido.best_similarity is not None
    assert perdido.ranking_url_id == u1.id


def test_wrapper_rerun_preserves_signed_decisions(
    db_session, make_job, t19_tables,
):
    from analysis.query_coverage import run_query_coverage
    from shared.models import Issue

    job, analysis, _, _, backend = _setup_job(db_session, make_job, t19_tables)

    run_query_coverage(
        db_session, job.id, analysis.id, backend,
        min_impressions=10, sim_threshold=0.6,
        buried_min_position=5, orphan_threshold=0.5,
    )
    gap = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "passage_gap",
    ).one()
    gap.review_status = "signed"
    gap.reviewed_by = "tester"
    db_session.flush()

    run_query_coverage(
        db_session, job.id, analysis.id, backend,
        min_impressions=10, sim_threshold=0.6,
        buried_min_position=5, orphan_threshold=0.5,
    )
    gaps = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "passage_gap",
    ).all()
    # la firmada sobrevive; el re-run añade la nueva pending (misma query)
    assert {g.review_status for g in gaps} == {"signed", "pending"}
    # los pending de tipos T19 sí se reemplazan (no se duplican)
    buried = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "buried_passage",
    ).all()
    assert len(buried) == 1 and buried[0].review_status == "pending"


def test_wrapper_blocked_without_query_data(db_session, make_job, t19_tables):
    from analysis.query_coverage import run_query_coverage

    SemanticAnalysis, _, _, _ = t19_tables
    job = make_job()
    analysis = SemanticAnalysis(job_id=job.id, status="completed")
    db_session.add(analysis)
    db_session.flush()

    r = run_query_coverage(db_session, job.id, analysis.id, FakeBackend({}))
    assert r == {"status": "blocked", "reason": "no_gsc_query_data"}


def test_get_endpoint_blocked_then_serves_cache(
    db_session, make_job, t19_tables,
):
    from analysis.query_coverage import run_query_coverage
    from api.routers.semantic import get_query_coverage

    job, analysis, _, u2, backend = _setup_job(db_session, make_job, t19_tables)

    r = get_query_coverage(job.id, db=db_session)
    assert r["status"] == "blocked" and r["reason"] == "not_run"

    run_query_coverage(
        db_session, job.id, analysis.id, backend,
        min_impressions=10, sim_threshold=0.6,
        buried_min_position=5, orphan_threshold=0.5,
    )
    db_session.commit()

    r = get_query_coverage(job.id, db=db_session)
    assert r["status"] == "ok"
    assert len(r["queries"]) == 3
    enterrada = next(q for q in r["queries"] if q["query"] == "tema dos")
    assert enterrada["buried"] is True
    assert enterrada["best_chunk_url"].endswith("/otra")
    assert enterrada["best_chunk_position"] == 6
