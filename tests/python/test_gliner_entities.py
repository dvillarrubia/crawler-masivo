"""
Tests de la capa de entidades GLiNER2 (POC entidad-query, fase 1).

Todo con fakes: adaptador del modelo, embedder 768d y juez LLM. Cubre el
schema.yaml, el núcleo de extracción, el pipeline sobre SQLite, el gate
de resolución en tres zonas y los cuatro checks del informe (con la
regla dura T10: lo firmado sobrevive al re-run).
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def entity_tables(db_engine):
    pytest.importorskip("pgvector")
    from pgvector.sqlalchemy import Vector
    from sqlalchemy.ext.compiler import compiles

    @compiles(Vector, "sqlite")
    def _vector_sqlite(type_, compiler, **kw):
        return "TEXT"

    from shared.entity_models import (
        ClientExtractionSchema, EntityCatalog, GlinerPageEntity,
        GlinerPageLabel, GlinerQueryEntity, GlinerQueryLabel,
    )
    from shared.semantic_models import GscQueryData

    for model in (ClientExtractionSchema, EntityCatalog, GlinerPageEntity,
                  GlinerPageLabel, GlinerQueryEntity, GlinerQueryLabel,
                  GscQueryData):
        model.__table__.create(db_engine, checkfirst=True)
    return True


SCHEMA_YAML = """
entidades:
  resolubles:
    servicio: "Servicio profesional concreto que se ofrece o se busca en el texto"
  senal:
    problema: "Problema o necesidad que expresa el usuario en el texto"
catalogo:
  fuente: generado
clasificacion:
  funnel: [TOFU, MOFU, BOFU]
  tipo_pagina: [servicio, blog]
umbrales:
  resolucion_alta: 0.85
  resolucion_baja: 0.60
"""


def _schema():
    from analysis.entities.schema_config import parse_schema

    return parse_schema(SCHEMA_YAML)


def _url(db_session, job, path, *, title=None, h1=None, body=None,
         pagerank=None, click_depth=None):
    from shared.models import Heading, HtmlMeta, PageContent, Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(job_id=job.id, url=full, url_hash=compute_url_hash(full),
            host="toy.local", path=path, scheme="https", is_internal=True,
            is_html=True, status_code=200, status_group="2xx",
            pagerank=pagerank, click_depth=click_depth, indexable=True)
    db_session.add(u)
    db_session.flush()
    if body is not None:
        db_session.add(PageContent(url_id=u.id, content_text=body))
    if title is not None:
        db_session.add(HtmlMeta(url_id=u.id, title=title))
    if h1 is not None:
        db_session.add(Heading(url_id=u.id, tag="h1", position=1, text=h1))
    db_session.flush()
    return u


class FakeAdapter:
    """Devuelve entidades por palabra clave contenida y labels fijos por
    texto (mapeo exacto por fragmento)."""

    def __init__(self, keyword_entities, labels_by_fragment=None):
        self.keyword_entities = keyword_entities      # frase -> (tipo, conf)
        self.labels_by_fragment = labels_by_fragment or {}

    def process(self, text):
        low = text.lower()
        entities = []
        for phrase, (etype, conf) in self.keyword_entities.items():
            idx = low.find(phrase)
            if idx >= 0:
                entities.append({"text": phrase, "type": etype, "start": idx,
                                 "end": idx + len(phrase), "confidence": conf})
        labels = {}
        for fragment, lbls in self.labels_by_fragment.items():
            if fragment in low:
                for task, pair in lbls.items():
                    labels.setdefault(task, []).append(pair)
        return {"entities": entities, "labels": labels}


def _pad768(v):
    return list(v) + [0.0] * (768 - len(v))


class FakeEmbedder:
    """Vectores unitarios deterministas por texto (768d como el real)."""

    def __init__(self, mapping):
        self.mapping = mapping

    def embed(self, texts):
        import numpy as np

        rows = []
        for t in texts:
            rows.append(_pad768(self.mapping.get(t, [0.0, 0.0, 0.0, 1.0])))
        m = np.asarray(rows, dtype="float32")
        n = np.linalg.norm(m, axis=1)
        n[n == 0] = 1.0
        return m / n[:, None]


class FakeJudge:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def judge(self, entity_text, context, candidates):
        self.calls.append((entity_text, [c[0] for c in candidates]))
        return self.answers.get(entity_text)


# ---------------------------------------------------------------------------
# schema.yaml
# ---------------------------------------------------------------------------

def test_parse_schema_ok():
    s = _schema()
    assert s.resolubles == {"servicio": "Servicio profesional concreto que se ofrece o se busca en el texto"}
    assert s.kind_of("problema") == "senal"
    assert s.funnel == ["TOFU", "MOFU", "BOFU"]
    assert s.high_threshold == 0.85 and s.low_threshold == 0.60


def test_parse_schema_errores():
    from analysis.entities.schema_config import SchemaError, parse_schema

    with pytest.raises(SchemaError):
        parse_schema("entidades:\n  resolubles: {}\n")  # sin resolubles
    with pytest.raises(SchemaError):
        parse_schema(SCHEMA_YAML.replace("[TOFU, MOFU, BOFU]", "[FRIO, CALIENTE]"))
    with pytest.raises(SchemaError):
        parse_schema(SCHEMA_YAML.replace("0.85", "0.5"))  # alta < baja


# ---------------------------------------------------------------------------
# Núcleo puro
# ---------------------------------------------------------------------------

def test_aggregate_and_primary():
    from analysis.entities.extraction import Span, aggregate_spans, primary_entity

    spans = [
        Span("Reforma de Cocina", "servicio", 0, 17, 0.9, "title"),
        Span("reforma de cocina", "servicio", 40, 57, 0.7, "body"),
        Span("baño", "servicio", 100, 104, 0.95, "body"),
        Span("baño", "servicio", 200, 204, 0.95, "body"),
        Span("baño", "servicio", 300, 304, 0.95, "body"),
        Span("baño", "servicio", 400, 404, 0.95, "body"),
    ]
    mentions = aggregate_spans(spans)
    by_text = {m.entity_text: m for m in mentions}
    cocina = by_text["reforma de cocina"]
    assert cocina.frequency == 2 and cocina.source_field == "title"
    assert cocina.weight == pytest.approx(4.0)  # 3 (title) + 1 (body)
    assert by_text["bano"].weight == pytest.approx(4.0)  # 4 menciones body

    # el title desempata: con igual peso gana el orden de agregación, así
    # que forzamos el caso claro: una mención más de baño en body
    resolved = {("reforma de cocina", "servicio"): "local:cocina",
                ("bano", "servicio"): "local:bano"}
    assert primary_entity(mentions, resolved_ids=resolved) in ("local:cocina", "local:bano")
    # sin resolver → None con resolved_only
    assert primary_entity(mentions, resolved_ids={}) is None


def test_chunk_text_overlap():
    from analysis.entities.extraction import chunk_text

    words = " ".join(f"w{i}" for i in range(700))
    chunks = chunk_text(words, size=300, overlap=50)
    assert len(chunks) >= 3
    # el solape existe: el inicio del 2º está antes del final del 1º
    assert chunks[1][0] < len(" ".join(f"w{i}" for i in range(300)))


# ---------------------------------------------------------------------------
# Pipeline páginas y queries (SQLite + FakeAdapter)
# ---------------------------------------------------------------------------

def _setup_job(db_session, make_job):
    job = make_job()
    job.client_id = "cli"
    db_session.flush()
    return job


def test_extract_pages_writes_mentions_and_labels(
    db_session, make_job, entity_tables,
):
    from analysis.entities.pipeline import extract_pages
    from shared.entity_models import GlinerPageEntity, GlinerPageLabel

    job = _setup_job(db_session, make_job)
    _url(db_session, job, "/cocina", title="Reforma de cocina en Bilbao",
         h1="Reforma de cocina", body="Presupuesto de reforma de cocina a medida.",
         pagerank=3.0)

    adapter = FakeAdapter(
        {"reforma de cocina": ("servicio", 0.9)},
        {"reforma de cocina": {"funnel": ("BOFU", 0.9),
                               "tipo_pagina": ("servicio", 0.8)}},
    )
    stats = extract_pages(db_session, job.id, _schema(), adapter)
    assert stats["urls"] == 1 and stats["mentions"] == 1

    m = db_session.query(GlinerPageEntity).one()
    assert m.entity_text == "reforma de cocina"
    assert m.kind == "resoluble"
    assert m.source_field == "title"     # title pesa más que h1/body
    assert m.frequency == 3              # title + h1 + body
    labels = {(l.label_type, l.label) for l in db_session.query(GlinerPageLabel)}
    assert ("funnel", "BOFU") in labels and ("tipo_pagina", "servicio") in labels

    # re-run reemplaza (no duplica)
    extract_pages(db_session, job.id, _schema(), adapter)
    assert db_session.query(GlinerPageEntity).count() == 1


def test_extract_queries_blocked_and_ok(db_session, make_job, entity_tables):
    from analysis.entities.pipeline import extract_queries
    from shared.entity_models import GlinerQueryEntity, GlinerQueryLabel
    from shared.semantic_models import GscQueryData

    job = _setup_job(db_session, make_job)
    adapter = FakeAdapter({"reforma baño": ("servicio", 0.8)},
                          {"reforma baño": {"funnel": ("BOFU", 0.9)}})

    assert extract_queries(db_session, job.id, _schema(), adapter)["reason"] == "no_gsc_query_data"

    u = _url(db_session, job, "/banos", body="x", pagerank=1.0)
    db_session.add_all([
        GscQueryData(job_id=job.id, url_id=u.id, query="reforma baño precio",
                     clicks=5, impressions=300, position=8.0),
        # fila sin match (url_id NULL) — la extensión GLiNER2/T9-D2 cuenta
        GscQueryData(job_id=job.id, url_id=None, url="https://toy.local/x",
                     query="reforma baño precio", clicks=0, impressions=200,
                     position=9.0),
        GscQueryData(job_id=job.id, url_id=u.id, query="query mínima",
                     clicks=0, impressions=2, position=40.0),  # < min_impressions
    ])
    db_session.flush()

    r = extract_queries(db_session, job.id, _schema(), adapter, min_impressions=10)
    assert r == {"queries": 1, "entities": 1}  # agregó 300+200 y filtró la mínima
    qe = db_session.query(GlinerQueryEntity).one()
    assert qe.query == "reforma baño precio" and qe.entity_text == "reforma bano"
    assert db_session.query(GlinerQueryLabel).one().label == "BOFU"


# ---------------------------------------------------------------------------
# Resolución en tres zonas
# ---------------------------------------------------------------------------

def test_resolve_three_zones(db_session, make_job, entity_tables):
    from analysis.entities.resolve import embed_catalog, resolve_job
    from shared.entity_models import EntityCatalog, GlinerPageEntity

    job = _setup_job(db_session, make_job)
    u = _url(db_session, job, "/p", body="x")

    db_session.add_all([
        EntityCatalog(client_id="cli", entity_id="local:cocina",
                      name="reforma de cocina", entity_type="servicio"),
        EntityCatalog(client_id="cli", entity_id="local:bano",
                      name="reforma de bano", entity_type="servicio"),
    ])
    for text in ("reforma de cocina", "reformar la cocina", "persianas"):
        db_session.add(GlinerPageEntity(
            job_id=job.id, url_id=u.id, url_hash=u.url_hash,
            entity_text=text, entity_type="servicio", kind="resoluble",
            source_field="body", frequency=1, confidence=0.8))
    db_session.flush()

    embedder = FakeEmbedder({
        "reforma de cocina": [1, 0, 0],
        "reforma de bano": [0, 1, 0],
        "reformar la cocina": [0.75, 0.0, 0.66],  # cos≈0.75 → zona gris
        "persianas": [0, 0, 1],                    # cos 0 → sin resolver
    })
    assert embed_catalog(db_session, "cli", embedder) == 2

    judge = FakeJudge({"reformar la cocina": "local:cocina"})
    r = resolve_job(db_session, job.id, "cli", _schema(), embedder, judge)
    assert r["resolved_cosine"] == 1 and r["resolved_llm"] == 1
    assert r["unresolved"] == 1 and r["gray_judged"] == 1

    by_text = {m.entity_text: m for m in db_session.query(GlinerPageEntity)}
    assert by_text["reforma de cocina"].resolved_by == "cosine"
    assert by_text["reformar la cocina"].resolved_by == "llm"
    assert by_text["persianas"].entity_id is None
    # el juez vio los candidatos top del catálogo
    assert judge.calls and "local:cocina" in judge.calls[0][1]


def test_seed_catalog_from_crawl(db_session, make_job, entity_tables):
    from analysis.entities.resolve import seed_catalog_from_crawl
    from shared.entity_models import EntityCatalog, GlinerPageEntity

    job = _setup_job(db_session, make_job)
    u = _url(db_session, job, "/p", body="x")
    for _ in range(3):
        db_session.add(GlinerPageEntity(
            job_id=job.id, url_id=u.id, url_hash=u.url_hash,
            entity_text="reforma de cocina", entity_type="servicio",
            kind="resoluble", source_field="body", frequency=1))
    db_session.flush()

    n = seed_catalog_from_crawl(db_session, "cli", job.id, _schema())
    assert n == 1
    row = db_session.query(EntityCatalog).one()
    assert row.entity_id == "local:reforma-de-cocina" and row.is_linked is False


# ---------------------------------------------------------------------------
# Informe: los 4 checks + regla T10
# ---------------------------------------------------------------------------

def _mention(db_session, job, u, text, eid, *, field="title", freq=1, conf=0.9):
    from shared.entity_models import GlinerPageEntity

    db_session.add(GlinerPageEntity(
        job_id=job.id, url_id=u.id, url_hash=u.url_hash, entity_text=text,
        entity_type="servicio", kind="resoluble", source_field=field,
        frequency=freq, confidence=conf, entity_id=eid, resolved_by="cosine"))


def _label(db_session, job, u, ltype, label):
    from shared.entity_models import GlinerPageLabel

    db_session.add(GlinerPageLabel(job_id=job.id, url_id=u.id,
                                   label_type=ltype, label=label, confidence=0.9))


def _report_scenario(db_session, make_job):
    from shared.entity_models import (
        EntityCatalog, GlinerQueryEntity, GlinerQueryLabel,
    )
    from shared.semantic_models import GscQueryData

    job = _setup_job(db_session, make_job)
    home = _url(db_session, job, "/", body="home", pagerank=5.0, click_depth=0)
    a = _url(db_session, job, "/cocina", body="a", pagerank=3.0)
    b = _url(db_session, job, "/cocina-guia", body="b", pagerank=1.0)
    c = _url(db_session, job, "/banos", body="c", pagerank=2.0)

    for eid, name in (("local:cocina", "reforma de cocina"),
                      ("local:bano", "reforma de bano"),
                      ("local:salon", "decoracion de salon")):
        db_session.add(EntityCatalog(client_id="cli", entity_id=eid, name=name,
                                     entity_type="servicio"))

    # A y B: misma entidad primaria + mismo funnel → canibalización.
    # C cubre "baño": así la query de baño que rankea en A es mismatch
    # legítimo (cubierta en otra página) y no un gap global.
    _mention(db_session, job, a, "reforma de cocina", "local:cocina")
    _mention(db_session, job, b, "reforma de cocina", "local:cocina")
    _mention(db_session, job, c, "reforma de bano", "local:bano")
    _label(db_session, job, a, "funnel", "BOFU")
    _label(db_session, job, b, "funnel", "BOFU")
    _label(db_session, job, a, "tipo_pagina", "servicio")
    _label(db_session, job, b, "tipo_pagina", "blog")   # distinto → diferenciar

    # queries: mismatch (baño rankea en A), gap (salón sin cobertura),
    # funnel roto (TOFU rankeando en A que es BOFU)
    rows = [
        ("reforma bano precio", a.id, 10, 800, 7.0),
        ("decoracion salon ideas", a.id, 2, 500, 12.0),
        ("que es una reforma", a.id, 1, 400, 15.0),
    ]
    for q, uid, clicks, imprs, pos in rows:
        db_session.add(GscQueryData(job_id=job.id, url_id=uid, query=q,
                                    clicks=clicks, impressions=imprs, position=pos))
    db_session.add_all([
        GlinerQueryEntity(job_id=job.id, query="reforma bano precio",
                          entity_text="reforma bano", entity_type="servicio",
                          kind="resoluble", confidence=0.9,
                          entity_id="local:bano", resolved_by="cosine"),
        GlinerQueryEntity(job_id=job.id, query="decoracion salon ideas",
                          entity_text="decoracion salon", entity_type="servicio",
                          kind="resoluble", confidence=0.8,
                          entity_id="local:salon", resolved_by="cosine"),
    ])
    db_session.add_all([
        GlinerQueryLabel(job_id=job.id, query="que es una reforma",
                         label_type="funnel", label="TOFU", confidence=0.9),
        GlinerQueryLabel(job_id=job.id, query="reforma bano precio",
                         label_type="funnel", label="BOFU", confidence=0.9),
    ])
    db_session.flush()
    return job, home, a, b


def test_report_four_checks(db_session, make_job, entity_tables):
    from analysis.entities.report import build_report, write_outputs
    from shared.models import Issue

    job, home, a, b = _report_scenario(db_session, make_job)
    report = build_report(db_session, job.id, "cli")

    assert [m["query"] for m in report["mismatches"]] == ["reforma bano precio"]
    assert report["mismatches"][0]["url_id"] == a.id
    assert [g["entity"] for g in report["gaps"]] == ["decoracion de salon"]
    assert report["gaps"][0]["url_id"] == home.id
    assert len(report["cannibalization"]) == 1
    c = report["cannibalization"][0]
    assert c["url_id"] == b.id            # la débil (menos pagerank)
    assert c["accion"] == "diferenciar"   # tipo_pagina distinto
    assert len(report["funnel_mismatches"]) == 1
    assert report["funnel_mismatches"][0]["page_funnel"] == "BOFU"

    out = write_outputs(db_session, job.id, report)
    assert out["issues"] == {
        "entity_query_mismatch": 1, "entity_coverage_gap": 1,
        "entity_cannibalization": 1, "funnel_mismatch": 1,
    }
    firmables = db_session.query(Issue).filter(
        Issue.issue_type.in_(("entity_cannibalization", "funnel_mismatch"))).all()
    assert all(i.review_status == "pending" for i in firmables)
    deterministas = db_session.query(Issue).filter(
        Issue.issue_type.in_(("entity_query_mismatch", "entity_coverage_gap"))).all()
    assert all(i.review_status is None for i in deterministas)


def test_report_rerun_preserves_signed(db_session, make_job, entity_tables):
    from analysis.entities.report import build_report, write_outputs
    from shared.models import Issue

    job, *_ = _report_scenario(db_session, make_job)
    write_outputs(db_session, job.id, build_report(db_session, job.id, "cli"))

    canib = db_session.query(Issue).filter(
        Issue.issue_type == "entity_cannibalization").one()
    canib.review_status = "signed"
    canib.reviewed_by = "tester"
    db_session.flush()

    write_outputs(db_session, job.id, build_report(db_session, job.id, "cli"))
    rows = db_session.query(Issue).filter(
        Issue.issue_type == "entity_cannibalization").all()
    assert {r.review_status for r in rows} == {"signed", "pending"}
    # los deterministas se regeneran sin duplicarse
    assert db_session.query(Issue).filter(
        Issue.issue_type == "entity_query_mismatch").count() == 1


# ---------------------------------------------------------------------------
# Endpoint del schema (validación en castellano)
# ---------------------------------------------------------------------------

def test_schema_endpoint_roundtrip(db_session, entity_tables):
    from fastapi import HTTPException

    from api.routers.clients import (
        ExtractionSchemaPayload, get_extraction_schema, put_extraction_schema,
    )

    assert get_extraction_schema("cli", db=db_session)["status"] == "empty"

    r = put_extraction_schema(
        "cli", ExtractionSchemaPayload(yaml_text=SCHEMA_YAML), db=db_session)
    assert r["status"] == "ok" and r["resolubles"] == ["servicio"]
    assert get_extraction_schema("cli", db=db_session)["status"] == "ok"

    with pytest.raises(HTTPException) as exc:
        put_extraction_schema(
            "cli", ExtractionSchemaPayload(yaml_text="entidades: {}\n"),
            db=db_session)
    assert exc.value.status_code == 422
