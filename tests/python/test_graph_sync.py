"""
Tests del lado grafo (contrato Seontology): identidad page_id con la
política FIJA del contrato y colectores puros Postgres→filas. El I/O
real contra Neo4j se verifica en vivo (contenedor del perfil graph);
aquí se garantiza que lo que viaja al grafo es exactamente lo permitido.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# page_id — política del contrato (§2)
# ---------------------------------------------------------------------------

def test_page_id_contract_policy():
    from shared.graph_identity import page_id

    base = page_id("https://Ejemplo.com/Ruta/")
    assert len(base) == 16 and all(c in "0123456789abcdef" for c in base)
    # lowercase + sin trailing slash
    assert page_id("https://ejemplo.com/ruta") == base
    # sin tracking (utm_* y gclid) ni fragmento
    assert page_id("https://ejemplo.com/ruta?utm_source=x&utm_medium=y") == base
    assert page_id("https://ejemplo.com/ruta?gclid=abc#seccion") == base
    # params reales SÍ distinguen, y su orden no importa
    con_param = page_id("https://ejemplo.com/ruta?color=rojo&talla=m")
    assert con_param != base
    assert page_id("https://ejemplo.com/ruta?talla=m&color=rojo") == con_param
    # barras dobles colapsadas
    assert page_id("https://ejemplo.com//ruta") == base


def test_chunk_and_query_ids():
    from shared.graph_identity import chunk_id, page_id, query_id

    pid = page_id("https://e.com/a")
    assert chunk_id("https://e.com/a", 7) == f"{pid}:0007"
    assert query_id("Reforma  Cocina ") == query_id("reforma cocina")
    assert len(query_id("x")) == 16


def test_page_id_independent_of_crawler_hash():
    """La identidad del grafo NO es el url_hash del crawler (sha256/64)."""
    from shared.graph_identity import page_id
    from shared.url_normalization import compute_url_hash

    url = "https://ejemplo.com/ruta"
    assert page_id(url) != compute_url_hash(url)
    assert len(page_id(url)) == 16 and len(compute_url_hash(url)) == 64


# ---------------------------------------------------------------------------
# Colectores (SQLite)
# ---------------------------------------------------------------------------

@pytest.fixture()
def graph_tables(db_engine):
    pytest.importorskip("pgvector")
    from pgvector.sqlalchemy import Vector
    from sqlalchemy.ext.compiler import compiles

    @compiles(Vector, "sqlite")
    def _vector_sqlite(type_, compiler, **kw):
        return "TEXT"

    from shared.entity_models import (
        EntityCatalog, GlinerPageEntity, GlinerPageLabel, GlinerQueryLabel,
    )
    from shared.semantic_models import GscQueryData

    for model in (EntityCatalog, GlinerPageEntity, GlinerPageLabel,
                  GlinerQueryLabel, GscQueryData):
        model.__table__.create(db_engine, checkfirst=True)
    return True


def _url(db_session, job, path, *, body_hash=None, pagerank=None,
         click_depth=None, title=None, status=200):
    from shared.models import HtmlMeta, Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(job_id=job.id, url=full, url_hash=compute_url_hash(full),
            host="toy.local", path=path, scheme="https", is_internal=True,
            is_html=True, status_code=status, status_group=f"{status // 100}xx",
            pagerank=pagerank, click_depth=click_depth, body_hash=body_hash,
            indexable=True)
    db_session.add(u)
    db_session.flush()
    if title:
        db_session.add(HtmlMeta(url_id=u.id, title=title))
        db_session.flush()
    return u


def _link(db_session, job, src, dst_path, *, anchor=None, position="content",
          edge_class=None):
    from shared.models import Link
    from shared.url_normalization import compute_url_hash

    dst = f"https://toy.local{dst_path}"
    db_session.add(Link(job_id=job.id, from_url_id=src.id, to_url=dst,
                        to_url_hash=compute_url_hash(dst), is_internal=True,
                        follow=True, link_position=position,
                        edge_class=edge_class, anchor_text=anchor))
    db_session.flush()


def test_collect_pages_props_and_skip(db_session, make_job, graph_tables):
    from analysis.graph.collect import collect_pages
    from shared.entity_models import GlinerPageLabel

    t0 = datetime.now(timezone.utc)
    prev = make_job(name="run1")
    prev.client_id = "cli"
    prev.created_at = t0 - timedelta(days=1)
    job = make_job(name="run2")
    job.client_id = "cli"
    job.created_at = t0
    db_session.flush()

    # run anterior: /igual con el MISMO body_hash; /cambia con otro
    _url(db_session, prev, "/igual", body_hash="AAA")
    _url(db_session, prev, "/cambia", body_hash="OLD")
    same = _url(db_session, job, "/igual", body_hash="AAA", title="Igual",
                pagerank=2.0, click_depth=1)
    changed = _url(db_session, job, "/cambia", body_hash="NEW")
    db_session.add(GlinerPageLabel(job_id=job.id, url_id=same.id,
                                   label_type="funnel", label="BOFU",
                                   confidence=0.9))
    db_session.flush()

    pages = collect_pages(db_session, job)
    by_url = {p["url"]: p for p in pages}
    assert by_url["https://toy.local/igual"]["changed"] is False   # skip
    assert by_url["https://toy.local/cambia"]["changed"] is True

    p = by_url["https://toy.local/igual"]
    assert p["funnel_stage"] == "BOFU" and p["title"] == "Igual"
    assert p["depth"] == 1 and p["pagerank"] == 2.0
    # reparto estricto: NI texto NI hashes de contenido viajan al grafo
    graph_props = {k for k in p if not k.startswith("_")}
    assert "body_hash" not in graph_props
    assert "content" not in graph_props and "text" not in graph_props


def test_collect_links_dedup_and_is_nav(db_session, make_job, graph_tables):
    from analysis.graph.collect import collect_links, collect_pages

    job = make_job()
    job.client_id = "cli"
    db_session.flush()
    a = _url(db_session, job, "/a")
    _url(db_session, job, "/b")
    _link(db_session, job, a, "/b", anchor="uno", edge_class="contextual")
    _link(db_session, job, a, "/b", anchor="dos", edge_class="contextual")  # dedup
    _link(db_session, job, a, "/a")  # self → fuera

    pages = collect_pages(db_session, job)
    links = collect_links(db_session, job, pages)
    assert len(links) == 1
    assert links[0]["is_nav"] is False and links[0]["anchor"] == "uno"

    # fallback a link_position cuando no hay edge_class
    from shared.graph_identity import page_id

    c = _url(db_session, job, "/c")
    _link(db_session, job, a, "/c", position="footer")
    links = collect_links(db_session, job, collect_pages(db_session, job))
    to_c = next(l for l in links if l["dst"] == page_id(c.url))
    assert to_c["is_nav"] is True


def test_collect_queries_and_entities(db_session, make_job, graph_tables):
    from analysis.graph.collect import collect_entities, collect_queries
    from shared.entity_models import (
        EntityCatalog, GlinerPageEntity, GlinerQueryLabel,
    )
    from shared.graph_identity import page_id, query_id
    from shared.semantic_models import GscQueryData

    job = make_job()
    job.client_id = "cli"
    db_session.flush()
    u = _url(db_session, job, "/p")

    db_session.add_all([
        GscQueryData(job_id=job.id, url_id=u.id, query="reforma cocina",
                     clicks=10, impressions=500, position=7.0),
        GscQueryData(job_id=job.id, url_id=None, url="https://x/",
                     query="reforma cocina", clicks=0, impressions=300,
                     position=9.0),
        GlinerQueryLabel(job_id=job.id, query="reforma cocina",
                         label_type="funnel", label="BOFU", confidence=0.9),
    ])
    db_session.add(EntityCatalog(client_id="cli", entity_id="local:cocina",
                                 name="reforma de cocina", entity_type="servicio"))
    db_session.add_all([
        GlinerPageEntity(job_id=job.id, url_id=u.id, url_hash=u.url_hash,
                         entity_text="reforma de cocina", entity_type="servicio",
                         kind="resoluble", source_field="title", frequency=2,
                         confidence=0.9, entity_id="local:cocina"),
        GlinerPageEntity(job_id=job.id, url_id=u.id, url_hash=u.url_hash,
                         entity_text="reformas cocina", entity_type="servicio",
                         kind="resoluble", source_field="body", frequency=3,
                         confidence=0.7, entity_id="local:cocina"),
    ])
    db_session.flush()

    nodes, covers = collect_queries(db_session, job)
    assert nodes == [{"query_id": query_id("reforma cocina"),
                      "text": "reforma cocina", "intent": "BOFU",
                      "volume": 800}]   # 500 + 300 (la sin match también suma)
    assert len(covers) == 1
    assert covers[0]["src"] == page_id(u.url) and covers[0]["clicks_ref"] == 10

    entities, mentions = collect_entities(db_session, job, "cli")
    assert entities[0]["entity_id"] == "local:cocina"
    assert entities[0]["is_linked"] is False
    # MENTIONS agregada por (página, entidad): frecuencia sumada, conf máx
    assert len(mentions) == 1
    assert mentions[0]["frequency"] == 5 and mentions[0]["confidence"] == 0.9
    assert mentions[0]["source"] == "gliner"
