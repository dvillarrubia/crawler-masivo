"""Job management endpoints: create, list, get, cancel, delete."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
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
