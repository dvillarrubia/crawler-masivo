"""
Tests de la Fase 5 — T9 señales GSC, T10 sugerencias+firma, T11 chunking
semántico (con embed_fn falso, sin Gemini), T15 GEO.

Las partes que exigen pgvector (tablas semantic_*) se testean a nivel de
lógica pura; el resto contra SQLite como el resto de la suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _url(db_session, job, path, *, status=200, words=None, inlinks=0,
         pagerank=None, indexable=True, raw_words=None, raw_types=None):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        host="toy.local", path=path, scheme="https",
        is_internal=True, is_html=True, status_code=status,
        status_group=f"{status // 100}xx", word_count=words,
        inlinks_count=inlinks, pagerank=pagerank, indexable=indexable,
        raw_word_count=raw_words, raw_schema_types=raw_types,
    )
    db_session.add(u)
    db_session.flush()
    return u


# ---------------------------------------------------------------------------
# T10 — suggester puro + endpoints de firma
# ---------------------------------------------------------------------------

def _page(url_hash, url, vector, pagerank, **kw):
    from analysis.link_suggester import PageVector

    return PageVector(
        url_hash=url_hash, url=url, vector=tuple(vector), pagerank=pagerank,
        indexable=kw.get("indexable", True),
        status_code=kw.get("status_code", 200),
        word_count=kw.get("word_count", 500),
    )


def test_suggest_links_core():
    from analysis.link_suggester import suggest_links

    # B es objetivo (pagerank bajo); A es candidata próxima que NO enlaza
    a = _page("ha", "https://x/a", [1, 0, 0.1], 8.0)
    b = _page("hb", "https://x/b", [0.98, 0.05, 0.12], 1.0)
    c = _page("hc", "https://x/c", [0, 1, 0], 5.0)  # tema distinto

    out = suggest_links([a, b, c], existing_links=set())
    assert len(out) == 1
    s = out[0]
    assert s["target_url_hash"] == "hb"
    assert s["source_url_hash"] == "ha"
    assert s["cosine_similarity"] >= 0.75
    assert s["score"] > 0


def test_suggest_links_skips_existing_and_noindex():
    from analysis.link_suggester import suggest_links

    a = _page("ha", "https://x/a", [1, 0, 0], 8.0)
    b = _page("hb", "https://x/b", [0.99, 0.01, 0], 1.0)
    # ya existe el enlace A→B → no se sugiere
    assert suggest_links([a, b], existing_links={("ha", "hb")}) == []
    # candidata noindex → excluida
    a2 = _page("ha", "https://x/a", [1, 0, 0], 8.0, indexable=False)
    assert suggest_links([a2, b], existing_links=set()) == []


def test_decision_endpoints_persist_author_and_date(db_session, make_job):
    from api.routers.review import (
        IssueReview, SuggestionDecision, decide_suggestion, review_issue,
    )
    from shared.models import Issue, LinkSuggestion

    job = make_job()
    u = _url(db_session, job, "/debil")
    s = LinkSuggestion(
        job_id=job.id, target_url_hash="ht", source_url_hash="hs",
        cosine_similarity=0.9, score=0.8,
    )
    issue = Issue(
        job_id=job.id, url_id=u.id, issue_type="semantic_cannibalization",
        severity="warning", review_status="pending",
    )
    det = Issue(
        job_id=job.id, url_id=u.id, issue_type="missing_title",
        severity="warning",
    )
    db_session.add_all([s, issue, det])
    db_session.flush()

    # regla dura: nace pending
    assert s.status == "pending"

    r = decide_suggestion(
        s.id, SuggestionDecision(status="accepted", decided_by="dvillarrubia"),
        db=db_session,
    )
    assert r["status"] == "accepted"
    db_session.expire_all()
    assert s.decided_by == "dvillarrubia"
    assert s.decided_at is not None

    r2 = review_issue(
        issue.id, IssueReview(review_status="signed", reviewed_by="dv"),
        db=db_session,
    )
    assert r2["review_status"] == "signed"

    # los issues deterministas no se firman
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        review_issue(det.id, IssueReview(review_status="signed", reviewed_by="x"),
                     db=db_session)
    assert exc.value.status_code == 422
    # y siguen con review_status NULL
    db_session.expire_all()
    assert det.review_status is None


# ---------------------------------------------------------------------------
# T9 — señales GSC (requiere pgvector para el modelo; skip si falta)
# ---------------------------------------------------------------------------

@pytest.fixture()
def gsc_tables(db_engine):
    pgvector = pytest.importorskip("pgvector")
    # Vector no compila en SQLite: crea solo la tabla GSC que necesitamos
    from shared.semantic_models import GscJobData

    GscJobData.__table__.create(db_engine, checkfirst=True)
    return GscJobData


def test_gsc_signals_issues(db_session, make_job, gsc_tables):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    GscJobData = gsc_tables
    job = make_job()
    # distribución de pagerank del job
    urls = [
        _url(db_session, job, f"/p{i}", pagerank=float(i), inlinks=3)
        for i in range(1, 9)
    ]
    sin_links = _url(db_session, job, "/sin-links", pagerank=6.0, inlinks=0)
    infra = _url(db_session, job, "/infra", pagerank=0.5, inlinks=2)  # < P25

    clicks = {sin_links.id: 50, infra.id: 500}
    for u in urls:
        clicks.setdefault(u.id, 10)
    for uid, c in clicks.items():
        db_session.add(GscJobData(job_id=job.id, url_id=uid, clicks=c,
                                  impressions=c * 10))
    db_session.flush()

    SEOAnalyzer(db_session, job.id).analyze_gsc_signals()
    db_session.flush()

    by_type = {}
    for i in db_session.query(Issue).filter(Issue.job_id == job.id):
        by_type.setdefault(i.issue_type, []).append(i.url_id)

    assert by_type.get("no_inlinks_with_traffic") == [sin_links.id]
    assert infra.id in by_type.get("underlinked_high_performer", [])


def test_striking_distance_blocked_without_gsc(db_session, make_job, gsc_tables):
    from api.routers.results import striking_distance

    job = make_job()
    result = striking_distance(job.id, page=1, page_size=50, db=db_session)
    assert result == {"status": "blocked", "reason": "gsc_not_configured"}


def test_striking_distance_queue(db_session, make_job, gsc_tables):
    from api.routers.results import striking_distance

    GscJobData = gsc_tables
    job = make_job()
    close = _url(db_session, job, "/casi", pagerank=1.0)
    top = _url(db_session, job, "/ya-arriba", pagerank=5.0)
    db_session.add_all([
        GscJobData(job_id=job.id, url_id=close.id, clicks=10,
                   impressions=5000, position=8.2),
        GscJobData(job_id=job.id, url_id=top.id, clicks=900,
                   impressions=9000, position=1.5),  # fuera del rango 5-15
    ])
    db_session.flush()

    result = striking_distance(job.id, page=1, page_size=50, db=db_session)
    assert result["status"] == "ok"
    assert result["total"] == 1
    assert result["items"][0]["url"] == "https://toy.local/casi"


# ---------------------------------------------------------------------------
# T11 — chunking semántico con embed_fn falso
# ---------------------------------------------------------------------------

def _fake_embed_two_topics(windows):
    """Embeddings sintéticos: cocina → eje X, fiscalidad → eje Y."""
    out = []
    for w in windows:
        cocina = sum(w.lower().count(t) for t in ("receta", "horno", "sal"))
        fiscal = sum(w.lower().count(t) for t in ("iva", "impuesto", "deducc"))
        out.append([1.0 + cocina, float(fiscal), 0.0]
                   if cocina >= fiscal else [float(cocina), 1.0 + fiscal, 0.0])
    return out


COCINA = ("La receta lleva sal y horno fuerte. Precalienta el horno diez "
          "minutos con sal gruesa. La receta admite variantes con horno de "
          "leña y más sal. Otra receta clásica usa horno suave y poca sal. "
          "El horno debe estar limpio antes de cada receta con sal.")
FISCAL = ("El IVA trimestral admite deducciones. Cada impuesto tiene su "
          "modelo y sus deducciones propias. Las deducciones del impuesto "
          "de sociedades cambian cada año con el IVA. Revisa el impuesto "
          "antes de aplicar deducciones de IVA. El IVA soportado genera "
          "deducciones directas en el impuesto.")


def test_semantic_chunk_headings_are_hard_boundaries():
    pytest.importorskip("numpy")
    from POC_centro_semantico.src.text_utils import semantic_chunk_text

    text = ("Guía general de la tienda. "
            "Envíos internacionales. Los envíos tardan tres días. "
            "Devoluciones fáciles. Las devoluciones tienen treinta días. "
            "Garantía extendida. La garantía cubre dos años.")
    headings = [
        {"tag": "h1", "text": "Guía general de la tienda."},
        {"tag": "h2", "text": "Envíos internacionales."},
        {"tag": "h2", "text": "Devoluciones fáciles."},
        {"tag": "h2", "text": "Garantía extendida."},
    ]
    chunks = semantic_chunk_text(text, headings=headings, embed_fn=None,
                                 min_words=1)
    assert len(chunks) >= 3
    paths = [c["heading_path"] for c in chunks]
    assert any(p and "Envíos internacionales." in p for p in paths)
    assert any(p and "Garantía extendida." in p for p in paths)
    # offsets correctos sobre el cuerpo normalizado
    from POC_centro_semantico.src.text_utils import _normalize_ws

    body = _normalize_ws(text)
    for c in chunks:
        assert c["text"] == body[c["char_start"]:c["char_end"]]


def test_semantic_chunk_cuts_at_topic_boundary():
    pytest.importorskip("numpy")
    from POC_centro_semantico.src.text_utils import semantic_chunk_text

    text = COCINA + " " + FISCAL
    chunks = semantic_chunk_text(
        text, headings=None, embed_fn=_fake_embed_two_topics,
        min_words=10, max_words=500, cut_percentile=80,
    )
    assert len(chunks) >= 2
    # el primer chunk es de cocina y el último de fiscalidad
    assert "receta" in chunks[0]["text"].lower()
    assert "iva" in chunks[-1]["text"].lower()
    # aggregate: embeddings presentes sin segunda pasada
    assert chunks[0]["embedding"] is not None


def test_semantic_chunk_aggregate_does_not_reembed():
    pytest.importorskip("numpy")
    from POC_centro_semantico.src.text_utils import semantic_chunk_text

    calls = []

    def counting_embed(windows):
        calls.append(len(windows))
        return _fake_embed_two_topics(windows)

    text = COCINA + " " + FISCAL
    semantic_chunk_text(text, embed_fn=counting_embed, min_words=10,
                        chunk_embedding_mode="aggregate")
    assert len(calls) == 1  # solo las ventanas; cero llamadas extra

    calls.clear()
    semantic_chunk_text(text, embed_fn=counting_embed, min_words=10,
                        chunk_embedding_mode="reembed")
    assert len(calls) == 2  # ventanas + chunks finales


# ---------------------------------------------------------------------------
# T15 — GEO
# ---------------------------------------------------------------------------

def test_geo_analysis_requires_render_js():
    from api.schemas import JobConfig

    with pytest.raises(Exception):
        JobConfig(geo_analysis=True, render_js=False)
    cfg = JobConfig(geo_analysis=True, render_js=True)
    assert cfg.geo_analysis is True


def test_analyze_geo_ratio_and_issues(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue, StructuredData, Url

    job = make_job(config={"geo_analysis": True, "render_js": True})
    spa = _url(db_session, job, "/spa", words=1000, raw_words=50,
               raw_types=[])
    ssr = _url(db_session, job, "/ssr", words=1000, raw_words=980,
               raw_types=["Article"])
    db_session.add_all([
        StructuredData(url_id=spa.id, format="jsonld", schema_type="Product"),
        StructuredData(url_id=ssr.id, format="jsonld", schema_type="Article"),
    ])
    db_session.flush()

    SEOAnalyzer(db_session, job.id).analyze_geo()
    db_session.flush()
    db_session.expire_all()

    assert spa.js_content_ratio == pytest.approx(0.95, abs=0.01)
    assert ssr.js_content_ratio == pytest.approx(0.02, abs=0.01)

    by_type = {}
    for i in db_session.query(Issue).filter(Issue.job_id == job.id):
        by_type.setdefault(i.issue_type, []).append(i.url_id)

    assert by_type.get("content_only_after_js") == [spa.id]
    assert by_type.get("schema_only_after_js") == [spa.id]
    # server-side rendered → ningún issue
    assert ssr.id not in by_type.get("content_only_after_js", [])

    sd = db_session.query(StructuredData).filter(
        StructuredData.url_id == spa.id
    ).one()
    assert sd.visible_without_js is False


def test_analyze_geo_flag_off_zero_changes(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job()
    u = _url(db_session, job, "/spa", words=1000, raw_words=10)
    SEOAnalyzer(db_session, job.id).analyze_geo()
    db_session.flush()
    db_session.expire_all()
    assert u.js_content_ratio is None
    assert db_session.query(Issue).count() == 0


def test_stats_geo_block_only_with_flag(db_session, make_job):
    from api.routers.results import get_stats

    plain = make_job()
    _url(db_session, plain, "/a")
    assert get_stats(plain.id, segment_id=None, db=db_session).geo is None

    geo_job = make_job(config={"geo_analysis": True, "render_js": True})
    u = _url(db_session, geo_job, "/spa", words=100, raw_words=50)
    u.js_content_ratio = 0.5
    db_session.flush()
    stats = get_stats(geo_job.id, segment_id=None, db=db_session)
    assert stats.geo is not None
    assert stats.geo["pages_evaluated"] == 1
    assert stats.geo["avg_js_content_ratio"] == 0.5
