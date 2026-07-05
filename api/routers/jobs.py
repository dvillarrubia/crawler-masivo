"""Job management endpoints: create, list, get, cancel, delete."""

from __future__ import annotations

import uuid

from typing import Any

from fastapi import (
    APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Query,
    UploadFile,
)
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.models import Job, Url, Issue, Link
from shared.url_normalization import UrlNormalizationConfig

from api.backup import ConflictError, import_backup_zip
from api.dependencies import get_redis
from api.schemas import (
    ImportResponse,
    JobCreate,
    JobResponse,
    PaginatedResponse,
    ReanalyzeRequest,
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

    crawled_count = 0
    try:
        r = get_redis()
        val = r.get(f"job:{job_id}:crawled_count")
        if val is not None:
            crawled_count = int(val)
    except Exception:
        crawled_count = job.total_urls_crawled

    return {
        "job_id": str(job_id),
        "status": job.status,
        "crawled_count": crawled_count,
        "total_urls_crawled_db": job.total_urls_crawled,
    }


# ---------------------------------------------------------------------------
# POST /api/jobs  --  create a new crawl job
# ---------------------------------------------------------------------------
@router.post("", response_model=JobResponse, status_code=201)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_session),
):
    config_dict = payload.config.model_dump()
    job = Job(
        id=uuid.uuid4(),
        name=payload.name,
        seeds=payload.seeds,
        client_id=payload.client_id,
        status="pending",
        config=config_dict,
        # T8: NULL fingerprint = default normalization (comparable together)
        normalization_fingerprint=(
            UrlNormalizationConfig.from_job_config(config_dict).fingerprint()
        ),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Push the job id onto the Redis pending queue so a worker picks it up.
    r = get_redis()
    r.rpush("jobs:pending", str(job.id))

    return job


# ---------------------------------------------------------------------------
# POST /api/jobs/{job_id}/reanalyze  --  re-run analysis without re-crawling
# ---------------------------------------------------------------------------
def _reanalysis_lock_key(job_id: str) -> str:
    return f"reanalysis:{job_id}:lock"


# Backstop por si el proceso muere con el lock puesto (un re-análisis real
# no debería acercarse ni de lejos).
_REANALYSIS_LOCK_TTL = 3600


def _run_reanalysis(job_id: str) -> None:
    from analysis.analyzer import run_analysis

    try:
        run_analysis(job_id)
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "Reanalysis failed for job %s", job_id
        )
    finally:
        # Libera el lock pase lo que pase (ver reanalyze_job).
        try:
            from api.dependencies import get_redis

            get_redis().delete(_reanalysis_lock_key(job_id))
        except Exception:
            pass


@router.post("/{job_id}/reanalyze", status_code=202)
def reanalyze_job(
    job_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    payload: ReanalyzeRequest | None = Body(default=None),
    db: Session = Depends(get_session),
):
    """T17.2: re-run the SEO analysis over the existing crawl data.

    Crawl data is immutable; only issues and computed metrics change.
    Optional ``analysis_thresholds`` are merged over the job's stored ones
    (persisted, so future reanalyses reuse them).
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail="Cannot reanalyze a job that is still queued or crawling",
        )

    # Lock atómico anti-carrera: dos re-análisis simultáneos del mismo job
    # se pisan (cada uno hace DELETE+INSERT de issues) y DUPLICAN las
    # filas — cazado en la auditoría de concurrencia (1390 → 4170). SET NX
    # es atómico en Redis; solo el primero pasa, el resto recibe 409.
    from api.dependencies import get_redis

    lock_key = _reanalysis_lock_key(str(job_id))
    try:
        got_lock = get_redis().set(lock_key, "1", nx=True, ex=_REANALYSIS_LOCK_TTL)
    except Exception:
        got_lock = True  # Redis caído: no bloqueamos la funcionalidad
    if not got_lock:
        raise HTTPException(
            status_code=409,
            detail="Ya hay un re-análisis en curso para este job. Espera a que termine.",
        )

    if payload is not None and payload.analysis_thresholds is not None:
        cfg = dict(job.config or {})
        cfg["analysis_thresholds"] = {
            **cfg.get("analysis_thresholds", {}),
            **payload.analysis_thresholds.model_dump(exclude_unset=True),
        }
        job.config = cfg
        db.commit()

    background_tasks.add_task(_run_reanalysis, str(job_id))
    return {"job_id": str(job_id), "status": "reanalysis_started"}


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
