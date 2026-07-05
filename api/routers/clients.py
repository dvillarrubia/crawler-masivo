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
# Configurador de cliente: cuentas + propiedad por defecto
# ---------------------------------------------------------------------------

class ClientSettingsPayload(BaseModel):
    gemini_account_id: uuid.UUID | None = None
    gsc_account_id: uuid.UUID | None = None
    gsc_property: str | None = Field(default=None, max_length=512)


@router.get("/settings")
def get_client_settings(client_id: str, db: Session = Depends(get_session)):
    """Qué cuentas y propiedad usa este cliente (para pre-rellenar la
    consola y que los pipelines no pidan la cuenta cada vez)."""
    from shared.entity_models import ClientSettings
    from shared.semantic_models import GeminiAccount, GscAccount

    row = db.get(ClientSettings, client_id)
    if row is None:
        return {"status": "empty"}
    gem = db.get(GeminiAccount, row.gemini_account_id) if row.gemini_account_id else None
    gsc = db.get(GscAccount, row.gsc_account_id) if row.gsc_account_id else None
    return {
        "status": "ok",
        "gemini_account_id": row.gemini_account_id,
        "gemini_account_name": gem.name if gem else None,
        "gsc_account_id": row.gsc_account_id,
        "gsc_account_name": gsc.name if gsc else None,
        "gsc_property": row.gsc_property,
    }


@router.put("/settings")
def put_client_settings(
    client_id: str,
    payload: ClientSettingsPayload,
    db: Session = Depends(get_session),
):
    from shared.entity_models import ClientSettings

    row = db.get(ClientSettings, client_id)
    if row is None:
        row = ClientSettings(client_id=client_id)
        db.add(row)
    row.gemini_account_id = payload.gemini_account_id
    row.gsc_account_id = payload.gsc_account_id
    row.gsc_property = payload.gsc_property
    db.commit()
    return {"status": "ok"}


def _auto_enqueue_entities(db: Session, client_id: str, reason: str) -> None:
    """Encola el pipeline de entidades del último rastreo completado del
    cliente (best-effort): guardar el schema o tocar el catálogo debe
    refrescar el análisis sin pasos manuales."""
    try:
        from api.dependencies import get_redis
        from shared.entities_queue import enqueue_safe

        job = (
            db.query(Job)
            .filter(Job.client_id == client_id, Job.status == "completed")
            .order_by(Job.completed_at.desc())
            .first()
        )
        if job is not None:
            enqueue_safe(get_redis(), db, job.id, reason=reason)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Extracción de entidades: el usuario rellena un FORMULARIO; el YAML es
# interno (se genera y valida en el servidor).
# ---------------------------------------------------------------------------

class EntityTypeDef(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    descripcion: str = Field(..., min_length=10, max_length=500)


class ExtractionSchemaForm(BaseModel):
    resolubles: list[EntityTypeDef] = Field(..., min_length=1)
    senal: list[EntityTypeDef] = Field(default_factory=list)
    catalogo_fuente: str = Field("generado", pattern="^(feed|crawl|generado)$")
    tipo_pagina: list[str] = Field(default_factory=list)
    resolucion_alta: float = Field(0.85, gt=0, le=1)
    resolucion_baja: float = Field(0.60, gt=0, le=1)


class ExtractionSchemaPayload(BaseModel):
    """O el YAML directo (uso interno/CLI) o el formulario estructurado."""
    yaml_text: str | None = None
    form: ExtractionSchemaForm | None = None


def _form_to_yaml(form: ExtractionSchemaForm) -> str:
    import yaml

    data = {
        "entidades": {
            "resolubles": {e.nombre: e.descripcion for e in form.resolubles},
            "senal": {e.nombre: e.descripcion for e in form.senal},
        },
        "catalogo": {"fuente": form.catalogo_fuente},
        "clasificacion": {
            "funnel": ["TOFU", "MOFU", "BOFU"],
            "tipo_pagina": form.tipo_pagina,
        },
        "umbrales": {
            "resolucion_alta": form.resolucion_alta,
            "resolucion_baja": form.resolucion_baja,
        },
    }
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


@router.get("/extraction-schema")
def get_extraction_schema(client_id: str, db: Session = Depends(get_session)):
    """El schema del cliente, en las dos formas: parsed (para el
    formulario de la consola) y yaml_text (interno)."""
    from analysis.entities.schema_config import SchemaError, parse_schema
    from shared.entity_models import ClientExtractionSchema

    row = db.get(ClientExtractionSchema, client_id)
    if row is None:
        return {"status": "empty", "yaml_text": "", "parsed": None}
    parsed = None
    try:
        s = parse_schema(row.yaml_text)
        parsed = {
            "resolubles": [{"nombre": k, "descripcion": v} for k, v in s.resolubles.items()],
            "senal": [{"nombre": k, "descripcion": v} for k, v in s.senal.items()],
            "catalogo_fuente": s.catalogo_fuente,
            "tipo_pagina": s.tipo_pagina,
            "resolucion_alta": s.high_threshold,
            "resolucion_baja": s.low_threshold,
        }
    except SchemaError:
        pass  # yaml legado inválido: la consola mostrará el formulario vacío
    return {"status": "ok", "yaml_text": row.yaml_text, "parsed": parsed,
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

    if payload.form is not None:
        yaml_text = _form_to_yaml(payload.form)
    elif payload.yaml_text:
        yaml_text = payload.yaml_text
    else:
        raise HTTPException(status_code=422, detail="Falta el formulario del schema.")

    try:
        schema = parse_schema(yaml_text)
    except SchemaError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    row = db.get(ClientExtractionSchema, client_id)
    if row is None:
        row = ClientExtractionSchema(client_id=client_id, yaml_text=yaml_text)
        db.add(row)
    else:
        row.yaml_text = yaml_text
    db.commit()
    _auto_enqueue_entities(db, client_id, "schema")
    return {
        "status": "ok",
        "resolubles": sorted(schema.resolubles),
        "senal": sorted(schema.senal),
        "tipo_pagina": schema.tipo_pagina,
    }


# ---------------------------------------------------------------------------
# Catálogo de entidades: la "validación humana" del catálogo generado
# ---------------------------------------------------------------------------

class CatalogEntryCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=300)
    entity_type: str = Field(..., min_length=1, max_length=64)


@router.get("/entity-catalog")
def list_entity_catalog(
    client_id: str,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_session),
):
    from shared.entity_models import EntityCatalog

    q = db.query(EntityCatalog).filter(EntityCatalog.client_id == client_id)
    if search:
        q = q.filter(EntityCatalog.name.ilike(f"%{search}%"))
    total = q.count()
    rows = (q.order_by(EntityCatalog.entity_type, EntityCatalog.name)
            .offset((max(1, page) - 1) * page_size).limit(page_size).all())
    return {
        "total": total,
        "items": [{
            "entity_id": r.entity_id, "name": r.name,
            "entity_type": r.entity_type, "source": r.source,
            "is_linked": r.is_linked,
            "embedded": r.embedding is not None,
        } for r in rows],
    }


@router.post("/entity-catalog", status_code=201)
def add_entity_catalog(
    client_id: str,
    payload: CatalogEntryCreate,
    db: Session = Depends(get_session),
):
    """Alta manual de una entidad del catálogo (source='feed'). Nace sin
    embedding: el paso `catalog` del pipeline la embebe."""
    from analysis.entities.extraction import slugify
    from shared.entity_models import EntityCatalog

    entity_id = f"local:{slugify(payload.name)}"
    if db.get(EntityCatalog, (client_id, entity_id)) is not None:
        raise HTTPException(status_code=409, detail=f"Ya existe {entity_id}")
    db.add(EntityCatalog(client_id=client_id, entity_id=entity_id,
                         name=payload.name, entity_type=payload.entity_type,
                         source="feed", is_linked=False))
    db.commit()
    _auto_enqueue_entities(db, client_id, "catalog")
    return {"entity_id": entity_id}


@router.delete("/entity-catalog/{entity_id:path}", status_code=204)
def delete_entity_catalog(
    client_id: str,
    entity_id: str,
    db: Session = Depends(get_session),
):
    from shared.entity_models import EntityCatalog

    row = db.get(EntityCatalog, (client_id, entity_id))
    if row is None:
        raise HTTPException(status_code=404, detail="Entrada no encontrada")
    db.delete(row)
    db.commit()
    _auto_enqueue_entities(db, client_id, "catalog")


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
