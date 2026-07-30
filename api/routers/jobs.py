"""Job management endpoints: create, list, get, cancel, delete."""

from __future__ import annotations

import uuid

from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.models import Job, Url, Issue, Link

from api.backup import ConflictError, import_backup_zip
from api.dependencies import get_redis
from api.schemas import (
    ImportResponse,
    JobCreate,
    JobResponse,
    PaginatedResponse,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/progress  --  real-time crawl progress from Redis
# ---------------------------------------------------------------------------
@router.get("/{job_id}/progress")
def get_progress(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Manda el contador de BD, no el de Redis. Miden cosas distintas: el de
    # Redis cuenta respuestas que llegan al spider, mientras que el spider
    # ademas guarda una fila por cada salto de una cadena de redirecciones sin
    # sumarlo. En un sitio con muchos 301 el de Redis se queda corto de forma
    # permanente (medido: 6.050 frente a 7.259 filas reales, un 17%) y la UI
    # aparentaba un rastreo estancado cuando iba fino. El de BD se refresca
    # cada 5s y cuadra con lo que luego se ve en los resultados.
    crawled_count = job.total_urls_crawled or 0
    live_count = None
    try:
        r = get_redis()
        val = r.get(f"job:{job_id}:crawled_count")
        if val is not None:
            live_count = int(val)
            # Solo al arrancar, antes del primer volcado del contador de BD.
            if crawled_count == 0:
                crawled_count = live_count
    except Exception:
        live_count = None

    # Cola pendiente: la publica el propio spider leyendo la longitud del
    # planificador de Scrapy. Es exacta y llega a 0 al terminar. Los rastreos
    # anteriores a ese cambio no tienen la clave y devuelven null.
    pending_count = None
    pending_exacto = False
    try:
        r = get_redis()
        val = r.get(f"job:{job_id}:pending_count")
        if val is not None:
            pending_count = int(val)
            pending_exacto = True
    except Exception:
        pending_count = None

    # Sin respaldo desde la BD a proposito. Antes, cuando la clave de Redis no
    # estaba (rastreos anteriores al cambio), se calculaba la cola con un
    # NOT EXISTS sobre `links`. Ese calculo costaba 7 s con 7,8 M de enlaces —42 s
    # en frio— y este endpoint lo llama la interfaz CADA 2 SEGUNDOS mientras hay
    # un rastreo abierto, asi que dejaba la aplicacion inutilizable en los jobs
    # grandes. Y encima devolvia un numero que ya sabiamos que era erroneo:
    # contaba como pendientes las URLs que Scrapy descarta al seguir un redirect
    # hacia algo ya rastreado (347.780 "pendientes" en un job terminado).
    #
    # Un dato desconocido se declara desconocido; no se paga con siete segundos
    # por una cifra que ademas esta mal. Para saber si un rastreo cubrio todo
    # esta finish_reason, que si es fiable.

    return {
        "job_id": str(job_id),
        "status": job.status,
        "crawled_count": crawled_count,
        "total_urls_crawled_db": job.total_urls_crawled,
        # Respuestas contadas por el spider. Excluye los saltos intermedios de
        # redireccion, asi que es normal que quede por debajo de crawled_count.
        "responses_count": live_count,
        "pending_count": pending_count,
        # True = cola real del planificador. False = no se sabe (rastreo
        # anterior al cambio); pending_count viene null en ese caso.
        "pending_exacto": pending_exacto,
        # "finished" = frontera agotada (dato completo)
        # "max_urls_reached" = cortado por el tope (dato PARCIAL)
        "finish_reason": job.finish_reason,
        "truncated": job.finish_reason == "max_urls_reached",
    }


# ---------------------------------------------------------------------------
# POST /api/jobs  --  create a new crawl job
# ---------------------------------------------------------------------------
@router.post("", response_model=JobResponse, status_code=201)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_session),
):
    job = Job(
        id=uuid.uuid4(),
        name=payload.name,
        seeds=payload.seeds,
        client_id=payload.client_id,
        status="pending",
        config=payload.config.model_dump(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Push the job id onto the Redis pending queue so a worker picks it up.
    r = get_redis()
    r.rpush("jobs:pending", str(job.id))

    return job


# ---------------------------------------------------------------------------
# GET /api/jobs  --  list jobs with pagination / filters
# ---------------------------------------------------------------------------
@router.get("", response_model=PaginatedResponse[JobResponse])
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    client_id: str | None = Query(None),
    db: Session = Depends(get_session),
):
    q = db.query(Job)

    if status is not None:
        q = q.filter(Job.status == status)
    if client_id is not None:
        q = q.filter(Job.client_id == client_id)

    total = q.count()
    pages = max(1, -(-total // page_size))  # ceiling division

    items = (
        q.order_by(Job.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PaginatedResponse[JobResponse](
        items=[JobResponse.model_validate(j) for j in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}  --  single job detail
# ---------------------------------------------------------------------------
@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ---------------------------------------------------------------------------
# PATCH /api/jobs/{job_id}/cancel  --  cancel a running / pending job
# ---------------------------------------------------------------------------
@router.patch("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel a job with status '{job.status}'",
        )

    job.status = "cancelled"
    db.commit()
    db.refresh(job)

    # Signal the crawler workers to stop processing this job.
    # The spider checks this key on every response.
    r = get_redis()
    r.set(f"job:{job.id}:cancel", "1")

    return job


# ---------------------------------------------------------------------------
# POST /api/jobs/{job_id}/resume  --  re-queue a failed/cancelled job
# ---------------------------------------------------------------------------
@router.post("/{job_id}/resume", response_model=JobResponse)
def resume_job(
    job_id: uuid.UUID,
    overrides: dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_session),
):
    """Re-queue a failed/cancelled job. The spider auto-detects already-crawled
    URLs and the discovered-but-not-yet-crawled frontier from the database, so
    the crawl resumes rather than restarting.

    Optional body: shallow-merged config overrides. Supported keys:
      - top-level: ``concurrent_requests``, ``concurrent_requests_per_domain``,
        ``render_js``, ``robots_mode``, ``user_agent``, ``impersonate``
      - sub-objects: ``crawl_behavior`` (deep-merged)
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("failed", "cancelled", "completed"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot resume a job with status '{job.status}'",
        )

    # Apply optional config overrides (shallow + crawl_behavior deep merge)
    if overrides:
        cfg = dict(job.config or {})
        cb = dict(cfg.get("crawl_behavior", {}))
        if isinstance(overrides.get("crawl_behavior"), dict):
            cb.update(overrides["crawl_behavior"])
        cfg["crawl_behavior"] = cb
        for key in (
            "concurrent_requests",
            "concurrent_requests_per_domain",
            "render_js",
            "robots_mode",
            "user_agent",
            "impersonate",
            "max_urls",
            "max_depth",
        ):
            if key in overrides:
                cfg[key] = overrides[key]
        job.config = cfg

    job.status = "pending"
    job.completed_at = None
    db.commit()
    db.refresh(job)

    # Clear any stale cancel signal and re-queue
    r = get_redis()
    try:
        r.delete(f"job:{job_id}:cancel")
    except Exception:
        pass
    r.rpush("jobs:pending", str(job.id))

    return job


# ---------------------------------------------------------------------------
# DELETE /api/jobs/{job_id}  --  delete a job and all associated data
# ---------------------------------------------------------------------------
@router.delete("/{job_id}", status_code=204)
def delete_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # If the job is still active, signal the crawler to stop first so it does
    # not keep writing rows (with a now-dangling job_id FK) while we delete.
    if job.status in ("pending", "running"):
        try:
            get_redis().set(f"job:{job_id}:cancel", "1")
        except Exception:
            pass

    # Delete associated records in bulk (faster than cascade for large sets).
    db.query(Link).filter(Link.job_id == job_id).delete(synchronize_session=False)
    db.query(Issue).filter(Issue.job_id == job_id).delete(synchronize_session=False)
    # Urls cascade handles html_meta, headings, etc. via DB-level ON DELETE CASCADE.
    db.query(Url).filter(Url.job_id == job_id).delete(synchronize_session=False)
    db.delete(job)
    db.commit()

    return None


# ---------------------------------------------------------------------------
# POST /api/jobs/import  --  import a backup ZIP
# ---------------------------------------------------------------------------
@router.post("/import", response_model=ImportResponse, status_code=201)
async def import_job(
    file: UploadFile = File(...),
    preserve_job_id: bool = Query(False),
    db: Session = Depends(get_session),
):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Se requiere un archivo .zip")

    try:
        result = import_backup_zip(file.file, preserve_job_id, db)
    except ConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al importar: {exc}")

    return result
