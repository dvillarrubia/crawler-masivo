"""Colectores puros Postgres → filas para el grafo (testeables sin Neo4j).

Cada colector devuelve dicts con EXACTAMENTE las propiedades que el
contrato permite en el grafo. Nada de texto completo, chunks, vectores
1024d ni series de métricas: eso vive en Postgres y aquí solo viajan
referencias (`page_id`, `clicks_ref`).

Idempotencia/coste (contrato §4): `collect_pages` marca `changed=False`
para las páginas cuyo body_hash coincide con el del run anterior del
mismo cliente — la comparación se hace ÍNTEGRAMENTE en Postgres, sin
guardar hashes de contenido en el grafo.
"""
from __future__ import annotations

import logging

from shared.graph_identity import page_id, query_id

logger = logging.getLogger(__name__)

# El contrato marca LINKS_TO con is_nav; con el clasificador de aristas
# activo (edge_class) es exacto; sin él, cae a link_position.
_NAV_EDGE_CLASSES = {"menu", "footer", "breadcrumb", "sidebar"}
_NAV_POSITIONS = {"nav", "header", "footer", "sidebar"}


def _prev_completed_job(session, job):
    from shared.models import Job

    if not job.client_id:
        return None
    return (
        session.query(Job)
        .filter(Job.client_id == job.client_id,
                Job.status == "completed",
                Job.created_at < job.created_at,
                Job.id != job.id)
        .order_by(Job.created_at.desc())
        .first()
    )


def collect_site(job) -> dict:
    from urllib.parse import urlsplit

    seed = (job.seeds or [""])[0]
    return {"domain": urlsplit(seed).hostname or seed, "client": job.client_id or ""}


def collect_pages(session, job) -> list[dict]:
    """Nodos Page (props mínimas del contrato + h1/title de navegación)."""
    from shared.models import Heading, HtmlMeta, Url

    rows = (
        session.query(Url, HtmlMeta.title)
        .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
        .filter(Url.job_id == job.id, Url.is_internal.is_(True),
                Url.is_html.is_(True),
                Url.status_code.isnot(None))
        .all()
    )
    ids = [u.id for u, _ in rows]
    h1 = {}
    if ids:
        for uid, text in (session.query(Heading.url_id, Heading.text)
                          .filter(Heading.url_id.in_(ids), Heading.tag == "h1")
                          .order_by(Heading.position)):
            h1.setdefault(uid, text)

    # funnel_stage desde la capa de entidades (si corrió)
    funnel = {}
    try:
        from shared.entity_models import GlinerPageLabel

        for l in session.query(GlinerPageLabel).filter(
                GlinerPageLabel.job_id == job.id,
                GlinerPageLabel.label_type == "funnel"):
            funnel[l.url_id] = l.label
    except Exception:  # tabla aún sin crear en instalaciones viejas
        pass

    # skip por contenido idéntico vs el run anterior (TODO en Postgres)
    prev = _prev_completed_job(session, job)
    prev_hash: dict[str, str] = {}
    if prev is not None:
        from shared.models import Url as U2

        prev_hash = dict(
            session.query(U2.url_hash, U2.body_hash)
            .filter(U2.job_id == prev.id, U2.body_hash.isnot(None))
        )

    out = []
    for u, title in rows:
        unchanged = (
            u.body_hash is not None
            and prev_hash.get(u.url_hash) == u.body_hash
        )
        out.append({
            "page_id": page_id(u.url),
            "url": u.url,
            "title": title,
            "h1": h1.get(u.id),
            "status_code": u.status_code,
            "depth": u.click_depth if u.click_depth is not None else u.crawl_depth,
            "funnel_stage": funnel.get(u.id),
            "pagerank": u.pagerank,
            "last_crawled": (u.last_crawled_at.isoformat()
                             if u.last_crawled_at else None),
            "changed": not unchanged,
            "_url_id": u.id,  # interno: no se escribe en el grafo
        })
    return out


def collect_links(session, job, pages: list[dict]) -> list[dict]:
    """Aristas LINKS_TO (anchor, is_nav, position) dedupe por par."""
    from shared.models import Link, Url

    by_url_id = {p["_url_id"]: p["page_id"] for p in pages}
    by_url_hash = {}
    for u in session.query(Url.url_hash, Url.url).filter(
            Url.job_id == job.id, Url.is_internal.is_(True), Url.is_html.is_(True)):
        by_url_hash[u.url_hash] = page_id(u.url)

    seen: dict[tuple[str, str], dict] = {}
    for l in session.query(Link).filter(Link.job_id == job.id,
                                        Link.is_internal.is_(True)):
        src = by_url_id.get(l.from_url_id)
        dst = by_url_hash.get(l.to_url_hash)
        if not src or not dst or src == dst:
            continue
        key = (src, dst)
        if key in seen:
            continue
        is_nav = (l.edge_class in _NAV_EDGE_CLASSES if l.edge_class
                  else l.link_position in _NAV_POSITIONS)
        seen[key] = {
            "src": src, "dst": dst,
            "anchor": (l.anchor_text or "")[:120] or None,
            "is_nav": bool(is_nav),
            "position": l.link_position,
        }
    return list(seen.values())


def collect_entities(session, job, client_id: str) -> tuple[list[dict], list[dict]]:
    """Nodos Entity (catálogo del cliente) + aristas MENTIONS agregadas.

    MENTIONS lleva frequency/confidence/source (nombres del contrato).
    Los spans y el texto se quedan en Postgres.
    """
    from analysis.entities.extraction import FIELD_WEIGHT  # noqa: F401 (doc)
    from shared.entity_models import EntityCatalog, GlinerPageEntity
    from shared.models import Url

    entities = [
        {"entity_id": c.entity_id, "name": c.name,
         "wikidata_qid": c.entity_id if c.is_linked else None,
         "entity_type": c.entity_type, "is_linked": bool(c.is_linked)}
        for c in session.query(EntityCatalog).filter(
            EntityCatalog.client_id == client_id)
    ]

    url_by_id = dict(session.query(Url.id, Url.url).filter(Url.job_id == job.id))
    agg: dict[tuple[str, str], dict] = {}
    for m in session.query(GlinerPageEntity).filter(
            GlinerPageEntity.job_id == job.id,
            GlinerPageEntity.entity_id.isnot(None)):
        url = url_by_id.get(m.url_id)
        if not url:
            continue
        key = (page_id(url), m.entity_id)
        cur = agg.get(key)
        if cur is None:
            agg[key] = {"src": key[0], "dst": key[1],
                        "frequency": m.frequency or 1,
                        "confidence": m.confidence or 0.0,
                        "source": "gliner"}
        else:
            cur["frequency"] += m.frequency or 1
            cur["confidence"] = max(cur["confidence"], m.confidence or 0.0)
    return entities, list(agg.values())


def collect_queries(session, job) -> tuple[list[dict], list[dict]]:
    """Nodos Query + COVERS (position, clicks_ref). La serie completa de
    métricas vive en Postgres; clicks_ref es la referencia temporal que
    el contrato permite en la arista."""
    from sqlalchemy import func

    from shared.models import Url
    from shared.semantic_models import GscQueryData

    intent = {}
    try:
        from shared.entity_models import GlinerQueryLabel

        for l in session.query(GlinerQueryLabel).filter(
                GlinerQueryLabel.job_id == job.id,
                GlinerQueryLabel.label_type == "funnel"):
            intent[l.query] = l.label
    except Exception:
        pass

    nodes: dict[str, dict] = {}
    for q, imprs in (session.query(
            GscQueryData.query, func.sum(GscQueryData.impressions))
            .filter(GscQueryData.job_id == job.id)
            .group_by(GscQueryData.query)):
        qid = query_id(q)
        nodes[qid] = {"query_id": qid, "text": q,
                      "intent": intent.get(q),
                      "volume": int(imprs or 0)}

    url_by_id = dict(session.query(Url.id, Url.url).filter(Url.job_id == job.id))
    covers: dict[tuple[str, str], dict] = {}
    for row in session.query(GscQueryData).filter(
            GscQueryData.job_id == job.id,
            GscQueryData.url_id.isnot(None)):
        url = url_by_id.get(row.url_id)
        if not url:
            continue
        key = (page_id(url), query_id(row.query))
        cur = covers.get(key)
        if cur is None or (row.clicks or 0) > cur["clicks_ref"]:
            covers[key] = {"src": key[0], "dst": key[1],
                           "position": row.position,
                           "clicks_ref": row.clicks or 0}
    return list(nodes.values()), list(covers.values())
