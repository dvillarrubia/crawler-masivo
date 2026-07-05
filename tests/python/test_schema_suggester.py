"""
Propuesta de esquema de entidades con LLM (schema_suggester).

Las partes puras (contexto desde la BD, prompt, parseo defensivo) se
prueban sin Gemini; la orquestación usa un generate_fn falso.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def ent_tables(db_engine):
    pytest.importorskip("pgvector")
    from pgvector.sqlalchemy import Vector
    from sqlalchemy.ext.compiler import compiles

    @compiles(Vector, "sqlite")
    def _v(type_, compiler, **kw):
        return "TEXT"

    from shared.semantic_models import GscQueryData

    GscQueryData.__table__.create(db_engine, checkfirst=True)
    return True


def _job(db_session, client_id="cli"):
    from shared.models import Job

    j = Job(name="j", seeds=["https://cli.com/"], config={},
            status="completed", client_id=client_id)
    db_session.add(j)
    db_session.flush()
    return j


def _page(db_session, job, path, title, h1, pr=1.0):
    from shared.models import Heading, HtmlMeta, Url
    from shared.url_normalization import compute_url_hash

    full = f"https://cli.com{path}"
    u = Url(job_id=job.id, url=full, url_hash=compute_url_hash(full),
            path=path, is_internal=True, is_html=True, status_code=200,
            status_group="2xx", pagerank=pr)
    db_session.add(u)
    db_session.flush()
    db_session.add(HtmlMeta(url_id=u.id, title=title))
    db_session.add(Heading(url_id=u.id, tag="h1", text=h1, position=0))
    db_session.flush()
    return u


# --- contexto -------------------------------------------------------------
def test_gather_context_from_pages(db_session, ent_tables):
    from analysis.entities.schema_suggester import gather_client_context

    job = _job(db_session)
    _page(db_session, job, "/servicios/seo", "SEO | Cli", "Servicio de SEO", pr=5)
    _page(db_session, job, "/servicios/sem", "SEM | Cli", "Servicio de SEM", pr=4)
    _page(db_session, job, "/blog/post", "Post", "Un post", pr=1)

    ctx = gather_client_context(db_session, "cli")
    assert ctx["host"] == "cli.com"
    assert ctx["n_pages"] == 3
    # ordenado por pagerank: servicios primero
    assert ctx["pages"][0]["h1"] == "Servicio de SEO"
    assert "servicios" in ctx["path_segments"]


def test_gather_context_no_job_is_empty(db_session, ent_tables):
    from analysis.entities.schema_suggester import gather_client_context

    ctx = gather_client_context(db_session, "sin-datos")
    assert ctx["n_pages"] == 0 and ctx["host"] is None


def test_gather_context_top_queries(db_session, ent_tables):
    from analysis.entities.schema_suggester import gather_client_context
    from shared.semantic_models import GscQueryData

    job = _job(db_session)
    _page(db_session, job, "/", "Home", "Home")
    for q, imp in [("agencia seo", 500), ("consultoria sem", 300), ("rara", 5)]:
        db_session.add(GscQueryData(job_id=job.id, url_id=None, query=q,
                                    impressions=imp, clicks=0))
    db_session.flush()

    ctx = gather_client_context(db_session, "cli")
    assert ctx["queries"][0] == "agencia seo"   # por impresiones desc
    assert ctx["n_queries"] == 3


# --- prompt ---------------------------------------------------------------
def test_build_prompt_includes_signal():
    from analysis.entities.schema_suggester import build_prompt

    ctx = {"host": "cli.com", "pages": [{"path": "/servicios/seo",
            "title": "SEO", "h1": "Servicio SEO"}], "path_segments": ["servicios"],
           "queries": ["agencia seo"], "n_pages": 1, "n_queries": 1}
    p = build_prompt(ctx, business_hint="Agencia de marketing")
    assert "cli.com" in p and "Agencia de marketing" in p
    assert "servicios" in p and "agencia seo" in p
    assert "resolubles" in p and "senal" in p


# --- parseo defensivo -----------------------------------------------------
def test_parse_llm_schema_clean_json():
    from analysis.entities.schema_suggester import parse_llm_schema

    out = parse_llm_schema(json.dumps({
        "resolubles": [{"nombre": "Servicio", "descripcion": "Los servicios que ofrece la agencia al cliente"}],
        "senal": [{"nombre": "sector", "descripcion": "El sector del cliente potencial, como retail o banca"}],
        "tipo_pagina": ["Home", "servicio", "servicio"],
        "razonamiento": "Agencia",
    }))
    assert out["resolubles"][0]["nombre"] == "servicio"   # slug
    assert out["tipo_pagina"] == ["home", "servicio"]     # slug + dedup
    assert out["senal"][0]["nombre"] == "sector"


def test_parse_llm_schema_strips_fences_and_drops_bad():
    from analysis.entities.schema_suggester import parse_llm_schema

    text = "```json\n" + json.dumps({
        "resolubles": [
            {"nombre": "producto", "descripcion": "Los productos del catálogo de la tienda online"},
            {"nombre": "malo", "descripcion": "corto"},          # desc <10 → fuera
        ],
        "senal": [{"nombre": "producto", "descripcion": "duplicado, debe caer por estar ya en resolubles"}],
        "tipo_pagina": ["producto"],
    }) + "\n```"
    out = parse_llm_schema(text)
    names = [e["nombre"] for e in out["resolubles"]]
    assert names == ["producto"]           # 'malo' descartado por descripción corta
    assert out["senal"] == []              # 'producto' ya es resoluble → fuera


def test_parse_llm_schema_bad_json_raises():
    from analysis.entities.schema_suggester import parse_llm_schema

    with pytest.raises(ValueError):
        parse_llm_schema("esto no es json")


# --- orquestación con generate_fn falso -----------------------------------
def test_suggest_schema_end_to_end(db_session, ent_tables):
    from analysis.entities.schema_suggester import suggest_schema

    job = _job(db_session)
    _page(db_session, job, "/servicios/seo", "SEO", "Servicio de SEO")

    fake = json.dumps({
        "resolubles": [{"nombre": "servicio", "descripcion": "Servicios de marketing que ofrece la agencia"}],
        "senal": [{"nombre": "sector", "descripcion": "Sector del cliente al que se dirige el servicio"}],
        "tipo_pagina": ["home", "servicio"],
        "razonamiento": "Agencia de marketing digital",
    })
    out = suggest_schema(db_session, "cli", api_key="unused",
                         business_hint="Agencia", generate_fn=lambda _p: fake)
    assert out["resolubles"][0]["nombre"] == "servicio"
    assert out["context"]["n_pages"] == 1
    assert out["context"]["used_business_hint"] is True


def test_suggest_schema_empty_resolubles_raises(db_session, ent_tables):
    from analysis.entities.schema_suggester import suggest_schema

    _job(db_session)
    with pytest.raises(ValueError):
        suggest_schema(db_session, "cli", api_key="x", attempts=2,
                       generate_fn=lambda _p: '{"resolubles": [], "senal": []}')


# --- catálogo (entradas concretas) ---------------------------------------
def _catalog_tables(db_engine):
    from shared.entity_models import ClientExtractionSchema, EntityCatalog
    for m in (ClientExtractionSchema, EntityCatalog):
        m.__table__.create(db_engine, checkfirst=True)


def _schema_row(db_session, client_id="cli"):
    from shared.entity_models import ClientExtractionSchema
    yaml_text = (
        "entidades:\n  resolubles:\n"
        "    servicio: Servicios que ofrece la agencia al cliente final\n"
        "    cliente: Empresas que han contratado a la agencia\n"
        "  senal: {}\n"
        "catalogo:\n  fuente: generado\n"
        "clasificacion:\n  funnel: [TOFU, MOFU, BOFU]\n  tipo_pagina: [home]\n")
    db_session.add(ClientExtractionSchema(client_id=client_id, yaml_text=yaml_text))
    db_session.flush()


def test_parse_llm_catalog_filters_types():
    from analysis.entities.schema_suggester import parse_llm_catalog

    text = json.dumps({"catalogo": [
        {"name": "SEO", "entity_type": "servicio"},
        {"name": "Bellota", "entity_type": "cliente"},
        {"name": "X", "entity_type": "tipo_invalido"},   # tipo fuera → fuera
        {"name": "SEO", "entity_type": "servicio"},        # duplicado → fuera
        {"name": "a", "entity_type": "servicio"},          # nombre <2 → fuera
    ]})
    out = parse_llm_catalog(text, {"servicio", "cliente"})
    names = sorted(e["name"] for e in out)
    assert names == ["Bellota", "SEO"]


def test_suggest_catalog_marks_existing(db_session, db_engine, ent_tables):
    from analysis.entities.schema_suggester import suggest_catalog
    from shared.entity_models import EntityCatalog

    _catalog_tables(db_engine)
    job = _job(db_session)
    _page(db_session, job, "/servicios/seo", "SEO", "Servicio de SEO")
    _schema_row(db_session)
    # una entidad ya en el catálogo
    db_session.add(EntityCatalog(client_id="cli", entity_id="local:seo",
                                 name="SEO", entity_type="servicio",
                                 source="feed", is_linked=False))
    db_session.flush()

    fake = json.dumps({"catalogo": [
        {"name": "SEO", "entity_type": "servicio"},        # ya existe
        {"name": "Branding", "entity_type": "servicio"},   # nueva
        {"name": "Bellota", "entity_type": "cliente"},     # nueva
    ]})
    out = suggest_catalog(db_session, "cli", api_key="x", generate_fn=lambda _p: fake)
    assert out["n_total"] == 3 and out["n_nuevas"] == 2
    seo = next(e for e in out["entries"] if e["name"] == "SEO")
    assert seo["exists"] is True
    branding = next(e for e in out["entries"] if e["name"] == "Branding")
    assert branding["exists"] is False and branding["entity_id"] == "local:branding"


def test_suggest_catalog_needs_schema(db_session, db_engine, ent_tables):
    from analysis.entities.schema_config import SchemaError
    from analysis.entities.schema_suggester import suggest_catalog

    _catalog_tables(db_engine)
    _job(db_session)
    with pytest.raises(SchemaError):     # sin schema del cliente
        suggest_catalog(db_session, "cli", api_key="x", generate_fn=lambda _p: "{}")


def test_suggest_schema_retries_transient_bad_output(db_session, ent_tables):
    """El LLM es no determinista: el 1º intento sale vacío/malo, el 2º bien.
    No debe fallar (evita el 422 intermitente)."""
    from analysis.entities.schema_suggester import suggest_schema

    _job(db_session)
    calls = {"n": 0}
    good = json.dumps({
        "resolubles": [{"nombre": "servicio", "descripcion": "Servicios que ofrece la agencia al cliente final"}],
        "senal": [], "tipo_pagina": ["home"],
    })

    def flaky(_prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return "no soy json"           # 1º intento: basura
        if calls["n"] == 2:
            return '{"resolubles": []}'    # 2º intento: vacío
        return good                        # 3º intento: bien

    out = suggest_schema(db_session, "cli", api_key="x", generate_fn=flaky, attempts=3)
    assert out["resolubles"][0]["nombre"] == "servicio"
    assert calls["n"] == 3
