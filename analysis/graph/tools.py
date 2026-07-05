"""Contrato de tools (§7): la única frontera que ve el agente.

Firmas fijas; cada tool decide internamente a qué motor va y hace el
merge en Python (nunca joins cross-DB en runtime del LLM). Cambiar la
firma o el shape de retorno exige versionar (_v2). Las tools cuya fuente
de datos aún no existe devuelven {"status": "blocked", "reason": ...} en
vez de inventar.
"""
from __future__ import annotations

from shared.graph_identity import page_id as _page_id_of


def graph_canibalizacion(pg_session, driver, page_id: str, *,
                         job_id, threshold: float = 0.85) -> dict:
    """Candidatos en pgvector (espacio 1024d del repo, comparación
    interna al mismo espacio) → contexto en Neo4j (comunidad, queries
    compartidas) → merge en Python. Patrón §5 del contrato."""
    import numpy as np

    from shared.models import Url
    from shared.semantic_models import SemanticAnalysis, SemanticPage

    analysis = (
        pg_session.query(SemanticAnalysis)
        .filter(SemanticAnalysis.job_id == job_id,
                SemanticAnalysis.status == "completed")
        .order_by(SemanticAnalysis.created_at.desc())
        .first()
    )
    if analysis is None:
        return {"status": "blocked", "reason": "semantic_analysis_not_run"}

    rows = (
        pg_session.query(Url.url, SemanticPage.embedding)
        .join(SemanticPage, SemanticPage.url_id == Url.id)
        .filter(SemanticPage.analysis_id == analysis.id,
                SemanticPage.embedding.isnot(None))
        .all()
    )
    by_pid = {_page_id_of(u): np.asarray(list(e), dtype="float32")
              for u, e in rows}
    url_by_pid = {_page_id_of(u): u for u, _ in rows}
    if page_id not in by_pid:
        return {"status": "blocked", "reason": "page_without_vector"}

    ref = by_pid[page_id]
    ref = ref / (np.linalg.norm(ref) or 1.0)
    candidates = []
    for pid, vec in by_pid.items():
        if pid == page_id:
            continue
        v = vec / (np.linalg.norm(vec) or 1.0)
        sim = float(ref @ v)
        if sim > threshold:
            candidates.append({"page_id": pid, "url": url_by_pid[pid],
                               "sim": round(sim, 4)})
    if not candidates:
        return {"status": "ok", "pairs": []}

    with driver.session() as s:
        enriched = s.run("""
            MATCH (a:Page {page_id: $pid})
            UNWIND $candidates AS cid
            MATCH (b:Page {page_id: cid})
            OPTIONAL MATCH (a)-[:COVERS]->(q:Query)<-[:COVERS]-(b)
            RETURN b.page_id AS page_id,
                   a.community_id IS NOT NULL AND
                   a.community_id = b.community_id AS misma_comunidad,
                   count(q) AS queries_compartidas
        """, {"pid": page_id,
              "candidates": [c["page_id"] for c in candidates]}).data()
    ctx = {e["page_id"]: e for e in enriched}
    for c in candidates:
        e = ctx.get(c["page_id"], {})
        c["misma_comunidad"] = bool(e.get("misma_comunidad"))
        c["queries_compartidas"] = int(e.get("queries_compartidas") or 0)
    candidates.sort(key=lambda c: (-c["queries_compartidas"], -c["sim"]))
    return {"status": "ok", "pairs": candidates}


def graph_internal_links(driver, page_id: str, funnel_stage: str,
                         *, limit: int = 10) -> dict:
    """Sugerencias de enlace interno desde el grafo. Variante actual sin
    vector (p.embedding 768d aún no poblado): misma comunidad + funnel
    destino + no enlazada ya, ordenado por pagerank."""
    with driver.session() as s:
        rows = s.run("""
            MATCH (origen:Page {page_id: $pid})
            MATCH (candidata:Page)
            WHERE candidata.page_id <> $pid
              AND candidata.funnel_stage = $funnel
              AND (origen.community_id IS NULL OR
                   candidata.community_id = origen.community_id)
              AND NOT (origen)-[:LINKS_TO]->(candidata)
            RETURN candidata.url AS url, candidata.page_id AS page_id,
                   candidata.pagerank AS pagerank
            ORDER BY candidata.pagerank DESC
            LIMIT $limit
        """, {"pid": page_id, "funnel": funnel_stage, "limit": limit}).data()
    if not rows:
        return {"status": "ok", "suggestions": [],
                "nota": "sin candidatas (¿hay funnel_stage en el grafo?)"}
    return {"status": "ok", "suggestions": rows}


def graph_required_entities(driver, query_id: str, *, limit: int = 15) -> dict:
    """Entidades que las páginas que cubren la query mencionan — lo que
    una pieza nueva sobre esa demanda debería cubrir."""
    with driver.session() as s:
        rows = s.run("""
            MATCH (q:Query {query_id: $qid})<-[:COVERS]-(p:Page)
                  -[m:MENTIONS]->(e:Entity)
            RETURN e.entity_id AS entity_id, e.name AS name,
                   sum(m.frequency) AS frequency,
                   count(DISTINCT p) AS pages
            ORDER BY frequency DESC LIMIT $limit
        """, {"qid": query_id, "limit": limit}).data()
    if not rows:
        return {"status": "blocked",
                "reason": "sin MENTIONS para esa query (¿corrió el pipeline de entidades?)"}
    return {"status": "ok", "entities": rows}


def graph_architecture(driver, site_domain: str, *, max_depth: int = 3) -> dict:
    """Huérfanas de enlazado y profundidad excesiva, desde el grafo."""
    with driver.session() as s:
        orphans = s.run("""
            MATCH (p:Page)-[:PART_OF_SITE]->(:Site {domain: $d})
            WHERE NOT ()-[:LINKS_TO]->(p) AND p.status_code < 300
            RETURN p.url AS url, p.pagerank AS pagerank
            ORDER BY p.pagerank DESC LIMIT 100
        """, {"d": site_domain}).data()
        deep = s.run("""
            MATCH (p:Page)-[:PART_OF_SITE]->(:Site {domain: $d})
            WHERE p.depth > $max
            RETURN p.url AS url, p.depth AS depth
            ORDER BY p.depth DESC LIMIT 100
        """, {"d": site_domain, "max": max_depth}).data()
    return {"status": "ok", "huerfanas": orphans, "profundas": deep}


def metrics_decay(pg_session, page_url: str, *, client_id: str) -> dict:
    """Serie GSC por crawl (un punto por run del cliente). La granularidad
    diaria del contrato llegará cuando gsc_metrics sea serie temporal;
    hoy el dato honesto es por-run."""
    from shared.models import Job, Url
    from shared.semantic_models import GscJobData
    from shared.url_normalization import compute_url_hash

    jobs = (
        pg_session.query(Job)
        .filter(Job.client_id == client_id, Job.status == "completed")
        .order_by(Job.created_at)
        .all()
    )
    if not jobs:
        return {"status": "blocked", "reason": "sin runs del cliente"}
    serie = []
    for j in jobs:
        row = (
            pg_session.query(GscJobData)
            .join(Url, Url.id == GscJobData.url_id)
            .filter(GscJobData.job_id == j.id, Url.url == page_url)
            .first()
        )
        if row:
            serie.append({"run": j.name,
                          "fecha": j.created_at.isoformat() if j.created_at else None,
                          "clicks": row.clicks, "impressions": row.impressions,
                          "position": row.position})
    if not serie:
        return {"status": "blocked", "reason": "sin datos GSC para esa URL"}
    deltas = None
    if len(serie) >= 2:
        a, b = serie[-2], serie[-1]
        deltas = {"clicks": (b["clicks"] or 0) - (a["clicks"] or 0),
                  "impressions": (b["impressions"] or 0) - (a["impressions"] or 0)}
    return {"status": "ok", "serie": serie, "deltas": deltas}


def graph_content_gaps(driver, cluster_id: str) -> dict:
    """Gaps vs competencia. Bloqueada: no hay nodos Competitor (no existe
    ingesta de SERP/competencia en este repo todavía)."""
    return {"status": "blocked",
            "reason": "sin datos de competencia (nodos Competitor no poblados)"}


def graph_ingest(url: str, content: str) -> dict:
    """Reingesta puntual. Bloqueada a propósito: en este repo la única
    puerta de entrada de contenido es el crawler (Postgres primero,
    grafo derivado vía analysis.graph.sync)."""
    return {"status": "blocked",
            "reason": "usa el crawler + analysis.graph.sync (orden estricto del contrato)"}
