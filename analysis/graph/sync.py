"""Sync Postgres → Neo4j (pasos 7-9 del pipeline del contrato).

Reglas que este módulo cumple a rajatabla:
- Orden estricto: lee SOLO de Postgres; el grafo es derivado.
- Todo upsert usa MERGE sobre la PK (idempotente); segundo run sin
  cambios ⇒ las páginas sin cambio de contenido se saltan (comparación
  de body_hash hecha en Postgres contra el run anterior del cliente).
- Nada de texto/chunks/embeddings 1024d/series en el grafo.
- Borrado (§4): --prune hace DETACH DELETE de las Page del sitio que ya
  no existen en el crawl actual; las Entity huérfanas se conservan.
- Paso 9 (GDS: Leiden → Cluster + BELONGS_TO versionado) es opcional
  (--gds) y degrada con aviso si el plugin no está. El pagerank del nodo
  Page viene del cálculo propio en Postgres (dato derivado permitido).

Uso:
    python -m analysis.graph.sync --job-id <uuid> [--prune] [--gds] [--force]
Env: NEO4J_URI (bolt://neo4j:7687), NEO4J_USER, NEO4J_PASSWORD.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid as uuid_mod
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("graph.sync")

BATCH = 1000


def get_driver():
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    # Sin contraseña por defecto en código: un fallback hardcodeado acaba
    # en producción por descuido. Debe venir del entorno (.env / compose).
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        raise RuntimeError(
            "NEO4J_PASSWORD no está definida. Configúrala en .env "
            "(mismo valor que NEO4J_AUTH del servicio neo4j).")
    return GraphDatabase.driver(uri, auth=(user, password))


def _run_batched(session, cypher: str, rows: list[dict], key: str = "rows") -> int:
    total = 0
    for start in range(0, len(rows), BATCH):
        batch = rows[start:start + BATCH]
        result = session.run(cypher, {key: batch})
        result.consume()
        total += len(batch)
    return total


def sync_job(pg_session, driver, job, *, force: bool = False,
             prune: bool = False) -> dict:
    from analysis.graph.collect import (
        collect_entities, collect_links, collect_pages, collect_queries,
        collect_site,
    )

    client_id = job.client_id or ""
    site = collect_site(job)
    pages = collect_pages(pg_session, job)
    links = collect_links(pg_session, job, pages)
    entities, mentions = collect_entities(pg_session, job, client_id) if client_id else ([], [])
    queries, covers = collect_queries(pg_session, job)

    changed_pages = [p for p in pages if p["changed"] or force]
    page_rows = [{k: v for k, v in p.items() if k not in ("changed", "_url_id")}
                 for p in changed_pages]
    all_page_ids = [p["page_id"] for p in pages]

    stats = {"pages_total": len(pages), "pages_upserted": len(page_rows),
             "pages_skipped_unchanged": len(pages) - len(changed_pages)}

    with driver.session() as s:
        # Site + PART_OF_SITE (siempre: barato e idempotente)
        s.run(
            "MERGE (st:Site {domain: $domain}) SET st.client = $client",
            site,
        ).consume()

        stats["pages_written"] = _run_batched(s, """
            UNWIND $rows AS r
            MERGE (p:Page {page_id: r.page_id})
            SET p.url = r.url, p.title = r.title, p.h1 = r.h1,
                p.status_code = r.status_code, p.depth = r.depth,
                p.funnel_stage = r.funnel_stage, p.pagerank = r.pagerank,
                p.last_crawled = r.last_crawled
        """, page_rows)

        # PART_OF_SITE para todas las páginas del job (aunque no cambien)
        _run_batched(s, """
            UNWIND $rows AS pid
            MATCH (p:Page {page_id: pid})
            MERGE (st:Site {domain: $domain})
            MERGE (p)-[:PART_OF_SITE]->(st)
        """.replace("$domain", f"'{site['domain']}'"), all_page_ids)

        stats["links_written"] = _run_batched(s, """
            UNWIND $rows AS r
            MATCH (a:Page {page_id: r.src})
            MATCH (b:Page {page_id: r.dst})
            MERGE (a)-[l:LINKS_TO]->(b)
            SET l.anchor = r.anchor, l.is_nav = r.is_nav, l.position = r.position
        """, links)

        stats["entities_written"] = _run_batched(s, """
            UNWIND $rows AS r
            MERGE (e:Entity {entity_id: r.entity_id})
            SET e.name = r.name, e.entity_type = r.entity_type,
                e.wikidata_qid = r.wikidata_qid, e.is_linked = r.is_linked
        """, entities)

        stats["mentions_written"] = _run_batched(s, """
            UNWIND $rows AS r
            MATCH (p:Page {page_id: r.src})
            MATCH (e:Entity {entity_id: r.dst})
            MERGE (p)-[m:MENTIONS]->(e)
            SET m.frequency = r.frequency, m.confidence = r.confidence,
                m.source = r.source
        """, mentions)

        stats["queries_written"] = _run_batched(s, """
            UNWIND $rows AS r
            MERGE (q:Query {query_id: r.query_id})
            SET q.text = r.text, q.intent = r.intent, q.volume = r.volume
        """, queries)

        stats["covers_written"] = _run_batched(s, """
            UNWIND $rows AS r
            MATCH (p:Page {page_id: r.src})
            MATCH (q:Query {query_id: r.dst})
            MERGE (p)-[c:COVERS]->(q)
            SET c.position = r.position, c.clicks_ref = r.clicks_ref
        """, covers)

        if prune:
            # Borrado §4: páginas del sitio ausentes del crawl actual.
            # Entity huérfanas se CONSERVAN (regla explícita del contrato).
            result = s.run("""
                MATCH (p:Page)-[:PART_OF_SITE]->(st:Site {domain: $domain})
                WHERE NOT p.page_id IN $keep
                DETACH DELETE p
            """, {"domain": site["domain"], "keep": all_page_ids})
            stats["pages_pruned"] = result.consume().counters.nodes_deleted

    logger.info("Sync grafo job %s: %s", job.id, stats)
    return stats


def run_gds(driver, site_domain: str) -> dict:
    """Paso 9 opcional: comunidades Leiden → Cluster versionado + BELONGS_TO.

    Las relaciones BELONGS_TO antiguas se archivan (archived=true), no se
    borran: la evolución de comunidades entre crawls es señal (§8.6).
    """
    computed_at = datetime.now(timezone.utc).isoformat()
    with driver.session() as s:
        try:
            s.run("CALL gds.version() YIELD gdsVersion RETURN gdsVersion").consume()
        except Exception:
            return {"status": "blocked",
                    "reason": "GDS no instalado — descomenta NEO4J_PLUGINS en docker-compose.yml"}

        graph_name = "seo_links"
        s.run("CALL gds.graph.drop($g, false)", {"g": graph_name}).consume()
        s.run("""
            MATCH (source:Page)-[r:LINKS_TO]->(target:Page)
            WITH gds.graph.project($g, source, target) AS g
            RETURN g
        """, {"g": graph_name}).consume()
        rows = s.run("""
            CALL gds.leiden.stream($g)
            YIELD nodeId, communityId
            RETURN gds.util.asNode(nodeId).page_id AS page_id, communityId
        """, {"g": graph_name}).data()
        s.run("CALL gds.graph.drop($g, false)", {"g": graph_name}).consume()

        s.run("""
            MATCH (:Page)-[b:BELONGS_TO]->(:Cluster)
            WHERE b.archived IS NULL OR b.archived = false
            SET b.archived = true
        """).consume()
        s.run("""
            UNWIND $rows AS r
            MATCH (p:Page {page_id: r.page_id})
            SET p.community_id = r.communityId
            MERGE (c:Cluster {cluster_id: $prefix + toString(r.communityId)})
            SET c.method = 'leiden', c.computed_at = $at
            MERGE (p)-[b:BELONGS_TO {computed_at: $at}]->(c)
            SET b.score = 1.0, b.archived = false
        """, {"rows": rows, "at": computed_at,
              "prefix": f"leiden:{site_domain}:{computed_at}:"}).consume()
    return {"status": "ok", "pages_clustered": len(rows), "computed_at": computed_at}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sync Postgres → Neo4j (Seontology)")
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--gds", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="upsertea también las páginas sin cambios")
    args = ap.parse_args(argv)

    from analysis.graph.contract_schema import ensure_schema
    from shared.database import SessionLocal, init_db
    from shared.models import Job

    init_db()
    pg = SessionLocal()
    job = pg.get(Job, uuid_mod.UUID(args.job_id))
    if job is None:
        sys.exit(f"Job {args.job_id} no existe")

    driver = get_driver()
    try:
        n = ensure_schema(driver)
        logger.info("Esquema del contrato asegurado (%d sentencias)", n)
        stats = sync_job(pg, driver, job, force=args.force, prune=args.prune)
        print({"sync": stats})
        if args.gds:
            from analysis.graph.collect import collect_site

            print({"gds": run_gds(driver, collect_site(job)["domain"])})
    finally:
        driver.close()
        pg.close()


if __name__ == "__main__":
    main()
