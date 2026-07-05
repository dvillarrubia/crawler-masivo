"""Pasos 01 y 02: extracción GLiNER2 sobre páginas y queries del job.

Escriben las tablas `gliner_*` con reemplazo por job (re-ejecutable).
El adaptador del modelo se inyecta — los tests usan un fake y el CLI el
Gliner2Adapter real.
"""
from __future__ import annotations

import logging

from analysis.entities.extraction import Span, aggregate_spans, chunk_text
from analysis.entities.schema_config import ExtractionSchema

logger = logging.getLogger(__name__)


def _pick_label(votes: list[tuple[str, float]]) -> tuple[str, float] | None:
    """Voto agregado entre chunks: suma de scores por etiqueta."""
    if not votes:
        return None
    acc: dict[str, float] = {}
    for label, score in votes:
        acc[label] = acc.get(label, 0.0) + float(score)
    label = max(acc, key=acc.get)
    total = sum(acc.values()) or 1.0
    return label, round(acc[label] / total, 4)


def extract_pages(session, job_id, schema: ExtractionSchema, adapter, *,
                  max_urls: int | None = None) -> dict:
    """Pasa GLiNER2 por title + H1 + body (chunked) de cada página HTML
    2xx interna con contenido, agrega spans y persiste menciones y labels.
    """
    from shared.entity_models import GlinerPageEntity, GlinerPageLabel
    from shared.models import Heading, HtmlMeta, PageContent, Url

    q = (
        session.query(
            Url.id, Url.url_hash, PageContent.content_text, HtmlMeta.title,
        )
        .join(PageContent, PageContent.url_id == Url.id)
        .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
        .filter(
            Url.job_id == job_id,
            Url.is_internal.is_(True),
            Url.is_html.is_(True),
            Url.status_code >= 200, Url.status_code < 300,
            PageContent.content_text.isnot(None),
        )
        .order_by(Url.id)
    )
    if max_urls:
        q = q.limit(max_urls)
    rows = q.all()

    h1_by_url: dict[int, str] = {}
    if rows:
        ids = [r[0] for r in rows]
        for uid, text in (
            session.query(Heading.url_id, Heading.text)
            .filter(Heading.url_id.in_(ids), Heading.tag == "h1")
            .order_by(Heading.position)
        ):
            h1_by_url.setdefault(uid, text or "")

    # reemplazo por job (re-ejecutable)
    session.query(GlinerPageEntity).filter(GlinerPageEntity.job_id == job_id).delete()
    session.query(GlinerPageLabel).filter(GlinerPageLabel.job_id == job_id).delete()

    n_mentions = 0
    n_labeled = 0
    for url_id, url_hash, body, title in rows:
        spans: list[Span] = []
        funnel_votes: list[tuple[str, float]] = []
        tipo_votes: list[tuple[str, float]] = []

        # title y H1 se procesan enteros; el body, por chunks con solape
        fields = [("title", title or ""), ("h1", h1_by_url.get(url_id, ""))]
        body_chunks = chunk_text(body or "")
        for field_name, text in fields:
            if not text.strip():
                continue
            out = adapter.process(text)
            for e in out["entities"]:
                spans.append(Span(
                    text=e["text"], entity_type=e["type"],
                    start=e.get("start") or 0, end=e.get("end") or 0,
                    confidence=e.get("confidence") or 0.0,
                    source_field=field_name,
                ))
            funnel_votes += out["labels"].get("funnel", [])
            tipo_votes += out["labels"].get("tipo_pagina", [])
        for offset, chunk in body_chunks:
            out = adapter.process(chunk)
            for e in out["entities"]:
                start = (e.get("start") or 0) + offset
                spans.append(Span(
                    text=e["text"], entity_type=e["type"],
                    start=start, end=start + max(0, (e.get("end") or 0) - (e.get("start") or 0)),
                    confidence=e.get("confidence") or 0.0,
                    source_field="body",
                ))
            funnel_votes += out["labels"].get("funnel", [])
            tipo_votes += out["labels"].get("tipo_pagina", [])

        for m in aggregate_spans(spans):
            if m.entity_type not in schema.all_entity_types:
                continue
            session.add(GlinerPageEntity(
                job_id=job_id, url_id=url_id, url_hash=url_hash,
                entity_text=m.entity_text, entity_type=m.entity_type,
                kind=schema.kind_of(m.entity_type),
                source_field=m.source_field, frequency=m.frequency,
                span_start=m.span_start, span_end=m.span_end,
                confidence=round(m.confidence, 4),
            ))
            n_mentions += 1

        for label_type, votes in (("funnel", funnel_votes), ("tipo_pagina", tipo_votes)):
            picked = _pick_label(votes)
            if picked:
                session.add(GlinerPageLabel(
                    job_id=job_id, url_id=url_id, label_type=label_type,
                    label=picked[0], confidence=picked[1],
                ))
                n_labeled += 1

    session.flush()
    logger.info("GLiNER2 páginas job %s: %d URLs, %d menciones, %d labels",
                job_id, len(rows), n_mentions, n_labeled)
    return {"urls": len(rows), "mentions": n_mentions, "labels": n_labeled}


def extract_queries(session, job_id, schema: ExtractionSchema, adapter, *,
                    min_impressions: int = 10,
                    max_queries: int = 2000) -> dict:
    """Misma pasada sobre las queries únicas de GSC del job (agregadas)."""
    from sqlalchemy import func

    from shared.entity_models import GlinerQueryEntity, GlinerQueryLabel
    from shared.semantic_models import GscQueryData

    agg = (
        session.query(
            GscQueryData.query,
            func.sum(GscQueryData.impressions).label("impressions"),
        )
        .filter(GscQueryData.job_id == job_id)
        .group_by(GscQueryData.query)
        .having(func.sum(GscQueryData.impressions) >= min_impressions)
        .order_by(func.sum(GscQueryData.impressions).desc())
        .limit(max_queries)
        .all()
    )
    if not agg:
        return {"status": "blocked", "reason": "no_gsc_query_data"}

    session.query(GlinerQueryEntity).filter(GlinerQueryEntity.job_id == job_id).delete()
    session.query(GlinerQueryLabel).filter(GlinerQueryLabel.job_id == job_id).delete()

    n_entities = 0
    for query, _imprs in agg:
        out = adapter.process(query)
        for m in aggregate_spans([
            Span(text=e["text"], entity_type=e["type"],
                 start=e.get("start") or 0, end=e.get("end") or 0,
                 confidence=e.get("confidence") or 0.0)
            for e in out["entities"]
        ]):
            if m.entity_type not in schema.all_entity_types:
                continue
            session.add(GlinerQueryEntity(
                job_id=job_id, query=query,
                entity_text=m.entity_text, entity_type=m.entity_type,
                kind=schema.kind_of(m.entity_type),
                confidence=round(m.confidence, 4),
            ))
            n_entities += 1
        picked = _pick_label(out["labels"].get("funnel", []))
        if picked:
            session.add(GlinerQueryLabel(
                job_id=job_id, query=query, label_type="funnel",
                label=picked[0], confidence=picked[1],
            ))

    session.flush()
    logger.info("GLiNER2 queries job %s: %d queries, %d entidades",
                job_id, len(agg), n_entities)
    return {"queries": len(agg), "entities": n_entities}
