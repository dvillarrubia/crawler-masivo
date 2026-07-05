"""Client-level utilities (T16): watchlist CRUD + suggested thresholds."""

from __future__ import annotations

import math
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.models import Job, Url, WatchlistEntry
from shared.url_normalization import compute_url_hash

router = APIRouter(prefix="/api/clients/{client_id}", tags=["clients"])


# ---------------------------------------------------------------------------
# Extracción de entidades: schema.yaml por cliente (GLiNER2)
# ---------------------------------------------------------------------------

class ExtractionSchemaPayload(BaseModel):
    yaml_text: str = Field(..., min_length=1)


@router.get("/extraction-schema")
def get_extraction_schema(client_id: str, db: Session = Depends(get_session)):
    """El schema.yaml del cliente (config única de la capa de entidades)."""
    from shared.entity_models import ClientExtractionSchema

    row = db.get(ClientExtractionSchema, client_id)
    if row is None:
        return {"status": "empty", "yaml_text": ""}
    return {"status": "ok", "yaml_text": row.yaml_text,
            "updated_at": row.updated_at}


@router.put("/extraction-schema")
def put_extraction_schema(
    client_id: str,
    payload: ExtractionSchemaPayload,
    db: Session = Depends(get_session),
):
    """Guarda el schema tras validarlo (422 con el error en castellano)."""
    from analysis.entities.schema_config import SchemaError, parse_schema
    from shared.entity_models import ClientExtractionSchema

    try:
        schema = parse_schema(payload.yaml_text)
    except SchemaError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    row = db.get(ClientExtractionSchema, client_id)
    if row is None:
        row = ClientExtractionSchema(client_id=client_id, yaml_text=payload.yaml_text)
        db.add(row)
    else:
        row.yaml_text = payload.yaml_text
    db.commit()
    return {
        "status": "ok",
        "resolubles": sorted(schema.resolubles),
        "senal": sorted(schema.senal),
        "tipo_pagina": schema.tipo_pagina,
    }


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

class WatchlistCreate(BaseModel):
    url: str = Field(..., min_length=1)
    label: str | None = Field(default=None, max_length=256)


class WatchlistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: str
    url: str
    url_hash: str
    label: str | None = None
    created_at: datetime | None = None


@router.get("/watchlist", response_model=list[WatchlistResponse])
def list_watchlist(client_id: str, db: Session = Depends(get_session)):
    return (
        db.query(WatchlistEntry)
        .filter(WatchlistEntry.client_id == client_id)
        .order_by(WatchlistEntry.id)
        .all()
    )


@router.post("/watchlist", response_model=WatchlistResponse, status_code=201)
def add_watchlist_entry(
    client_id: str, payload: WatchlistCreate, db: Session = Depends(get_session),
):
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=422, detail="Watchlist URL must be absolute (http/https)"
        )
    entry = WatchlistEntry(
        client_id=client_id,
        url=payload.url.strip(),
        url_hash=compute_url_hash(payload.url.strip()),
        label=payload.label,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/watchlist/{entry_id}", status_code=204)
def delete_watchlist_entry(
    client_id: str, entry_id: int, db: Session = Depends(get_session),
):
    entry = (
        db.query(WatchlistEntry)
        .filter(
            WatchlistEntry.id == entry_id,
            WatchlistEntry.client_id == client_id,
        )
        .first()
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    db.delete(entry)
    db.commit()


# ---------------------------------------------------------------------------
# Suggested thresholds — suggestion only, defaults never change (T16)
# ---------------------------------------------------------------------------

def _nearest_rank(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))
    return ordered[idx]


class SuggestedThresholds(BaseModel):
    job_id: uuid.UUID | None
    suggestions: dict[str, float | int | None]


@router.get("/suggested-thresholds", response_model=SuggestedThresholds)
def suggested_thresholds(client_id: str, db: Session = Depends(get_session)):
    """Data-driven suggestions for ``analysis_thresholds`` computed from the
    client's latest completed job. Purely advisory: job defaults never
    change on their own.
    """
    job = (
        db.query(Job)
        .filter(Job.client_id == client_id, Job.status == "completed")
        .order_by(Job.created_at.desc())
        .first()
    )
    if job is None:
        return SuggestedThresholds(job_id=None, suggestions={})

    base = (
        db.query(Url)
        .filter(
            Url.job_id == job.id,
            Url.is_internal.is_(True),
            Url.is_html.is_(True),
            Url.status_code >= 200,
            Url.status_code < 300,
            Url.indexable.isnot(False),
        )
    )

    word_counts = [
        float(w) for (w,) in base.with_entities(Url.word_count)
        if w is not None
    ]
    latencies = [
        float(ms) for (ms,) in base.with_entities(Url.response_time_ms)
        if ms is not None
    ]
    outlinks = [
        float(o) for (o,) in base.with_entities(Url.outlinks_count)
        if o is not None
    ]

    p10_words = _nearest_rank(word_counts, 0.10)
    p90_latency = _nearest_rank(latencies, 0.90)
    p95_outlinks = _nearest_rank(outlinks, 0.95)

    return SuggestedThresholds(
        job_id=job.id,
        suggestions={
            "min_word_count": int(p10_words) if p10_words is not None else None,
            "slow_page_ms": (
                int(round(p90_latency / 100) * 100)
                if p90_latency is not None else None
            ),
            "max_outlinks": (
                int(p95_outlinks) if p95_outlinks is not None else None
            ),
        },
    )
