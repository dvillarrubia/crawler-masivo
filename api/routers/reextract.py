"""Post-crawl content re-extraction from stored raw HTML.

Lets extraction settings (positive content selectors, boilerplate strips)
be calibrated AFTER a crawl: preview the result on a stored page, then
re-run extraction for the whole job in a background thread — no re-crawl.

Storage endpoints expose the size of the stored HTML and allow purging it
once calibration is done (the worker also auto-purges by retention).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import sys
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from shared.database import SessionLocal, get_session
from shared.models import Job, PageContent, RawHtml, Url

from api.dependencies import get_redis

logger = logging.getLogger(__name__)

# The extraction functions live in the crawler package. Inside the API image
# they are copied to /app/seo_crawler; on a bare checkout they live under
# crawler/ — add that to sys.path as a fallback so local dev works too.
try:
    from seo_crawler.extractors import (  # type: ignore
        extract_main_content,
        extract_main_content_markdown,
        extract_word_count,
    )
except ImportError:  # pragma: no cover - path fallback for local checkouts
    _CRAWLER_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "crawler",
    )
    if _CRAWLER_DIR not in sys.path:
        sys.path.insert(0, _CRAWLER_DIR)
    from seo_crawler.extractors import (  # type: ignore
        extract_main_content,
        extract_main_content_markdown,
        extract_word_count,
    )

router = APIRouter(prefix="/api/jobs/{job_id}", tags=["reextract"])

_PROGRESS_TTL_S = 24 * 3600


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ReextractConfig(BaseModel):
    """Extraction settings applied when re-extracting."""

    content_selectors: list[str] = Field(default_factory=list)
    custom_boilerplate_selectors: list[str] = Field(default_factory=list)
    strip_promo_blocks: bool = True


class PreviewRequest(BaseModel):
    url_id: int | None = None
    url: str | None = None  # exact or substring match against stored pages
    config: ReextractConfig = Field(default_factory=ReextractConfig)


class ReextractRequest(BaseModel):
    config: ReextractConfig = Field(default_factory=ReextractConfig)
    # Persist the config into job.config.extraction so future crawls and
    # resumes of this job use the calibrated settings.
    save_config: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_job_or_404(job_id: uuid.UUID, db: Session) -> Job:
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _progress_key(job_id) -> str:
    return f"job:{job_id}:reextract"


def _set_progress(job_id, payload: dict) -> None:
    try:
        r = get_redis()
        payload = dict(payload)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        r.set(_progress_key(job_id), json.dumps(payload), ex=_PROGRESS_TTL_S)
    except Exception:
        pass


def _get_progress(job_id) -> dict:
    try:
        r = get_redis()
        raw = r.get(_progress_key(job_id))
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {}


def _run_extraction(html: str, cfg: ReextractConfig) -> tuple[str | None, str | None]:
    """Run the content pipeline over one page's HTML with *cfg*."""
    from parsel import Selector

    selector = Selector(text=html)
    word_count = extract_word_count(selector)
    kwargs = dict(
        word_count=word_count,
        strip_promo=cfg.strip_promo_blocks,
        extra_selectors=cfg.custom_boilerplate_selectors or None,
        content_selectors=cfg.content_selectors or None,
    )
    text = extract_main_content(selector, **kwargs)
    md = extract_main_content_markdown(selector, **kwargs) if text else None
    return text, md


# ---------------------------------------------------------------------------
# Storage stats / purge
# ---------------------------------------------------------------------------
@router.get("/rawhtml/stats")
def raw_html_stats(job_id: uuid.UUID, db: Session = Depends(get_session)):
    _get_job_or_404(job_id, db)
    pages, total = (
        db.query(func.count(RawHtml.url_id), func.coalesce(func.sum(RawHtml.size_bytes), 0))
        .filter(RawHtml.job_id == job_id)
        .first()
    )
    return {"pages": pages or 0, "total_bytes": int(total or 0)}


@router.delete("/rawhtml")
def purge_raw_html(job_id: uuid.UUID, db: Session = Depends(get_session)):
    """Free the stored HTML for this job (calibration finished)."""
    _get_job_or_404(job_id, db)
    progress = _get_progress(job_id)
    if progress.get("status") == "running":
        raise HTTPException(status_code=409, detail="Hay una re-extraccion en curso")
    deleted = (
        db.query(RawHtml).filter(RawHtml.job_id == job_id).delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted_pages": deleted}


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
@router.post("/reextract/preview")
def preview_reextract(
    job_id: uuid.UUID,
    body: PreviewRequest,
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    q = (
        db.query(RawHtml, Url.url)
        .join(Url, Url.id == RawHtml.url_id)
        .filter(RawHtml.job_id == job_id)
    )
    if body.url_id is not None:
        q = q.filter(RawHtml.url_id == body.url_id)
    elif body.url:
        q = q.filter(Url.url.ilike(f"%{body.url.strip()}%")).order_by(func.length(Url.url))
    row = q.first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No hay HTML almacenado para esa URL en este job",
        )
    raw, page_url = row

    try:
        html = gzip.decompress(raw.html_gz).decode("utf-8", errors="ignore")
    except Exception:
        raise HTTPException(status_code=500, detail="No se pudo descomprimir el HTML")

    text, md = _run_extraction(html, body.config)
    return {
        "url": page_url,
        "content_text": text,
        "content_markdown": md,
        "content_length": len(text) if text else 0,
        "html_bytes": len(html),
    }


# ---------------------------------------------------------------------------
# Bulk re-extraction (background thread + Redis progress)
# ---------------------------------------------------------------------------
def _reextract_thread(job_id: str, cfg: ReextractConfig) -> None:
    done = 0
    failed = 0
    last_id = 0
    batch = 200
    try:
        session = SessionLocal()
        try:
            total = (
                session.query(func.count(RawHtml.url_id))
                .filter(RawHtml.job_id == job_id)
                .scalar()
            ) or 0
        finally:
            session.close()

        _set_progress(job_id, {"status": "running", "done": 0, "total": total})

        while True:
            session = SessionLocal()
            try:
                rows = (
                    session.query(RawHtml)
                    .filter(RawHtml.job_id == job_id, RawHtml.url_id > last_id)
                    .order_by(RawHtml.url_id)
                    .limit(batch)
                    .all()
                )
                if not rows:
                    break
                for raw in rows:
                    last_id = raw.url_id
                    try:
                        html = gzip.decompress(raw.html_gz).decode("utf-8", errors="ignore")
                        text, md = _run_extraction(html, cfg)
                        pc = (
                            session.query(PageContent)
                            .filter(PageContent.url_id == raw.url_id)
                            .first()
                        )
                        if pc is None:
                            pc = PageContent(url_id=raw.url_id)
                            session.add(pc)
                        pc.content_text = text
                        pc.content_length = len(text) if text else None
                        pc.content_markdown = md
                        # Fresh extraction: any post-crawl cleaning state is stale.
                        pc.content_text_original = None
                        pc.content_markdown_original = None
                        pc.cleaned_at = None
                        done += 1
                    except Exception:
                        failed += 1
                        logger.exception("Re-extraction failed for url_id=%s", raw.url_id)
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
            _set_progress(job_id, {"status": "running", "done": done, "total": total, "failed": failed})

        _set_progress(job_id, {"status": "done", "done": done, "total": total, "failed": failed})
        logger.info("Re-extraction for job %s finished: %d pages (%d failed)", job_id, done, failed)
    except Exception as exc:
        logger.exception("Re-extraction thread died for job %s", job_id)
        _set_progress(job_id, {"status": "failed", "done": done, "error": str(exc)})


@router.post("/reextract")
def start_reextract(
    job_id: uuid.UUID,
    body: ReextractRequest,
    db: Session = Depends(get_session),
):
    job = _get_job_or_404(job_id, db)
    if job.status in ("pending", "running", "analyzing"):
        raise HTTPException(status_code=409, detail="El job aun esta en ejecucion")

    progress = _get_progress(job_id)
    if progress.get("status") == "running":
        raise HTTPException(status_code=409, detail="Ya hay una re-extraccion en curso")

    stored = (
        db.query(func.count(RawHtml.url_id)).filter(RawHtml.job_id == job_id).scalar()
    ) or 0
    if stored == 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "Este job no tiene HTML almacenado (se rastreo con el almacenamiento "
                "desactivado o ya se purgo). Re-rastrea con 'Almacenar HTML' activo."
            ),
        )

    # Persist calibrated settings so future crawls/resumes of this job reuse them.
    if body.save_config:
        cfg = dict(job.config or {})
        extraction = dict(cfg.get("extraction", {}))
        extraction["content_selectors"] = body.config.content_selectors
        extraction["custom_boilerplate_selectors"] = body.config.custom_boilerplate_selectors
        extraction["strip_promo_blocks"] = body.config.strip_promo_blocks
        cfg["extraction"] = extraction
        job.config = cfg
        db.commit()

    _set_progress(job_id, {"status": "running", "done": 0, "total": stored})
    thread = threading.Thread(
        target=_reextract_thread, args=(str(job_id), body.config), daemon=True,
    )
    thread.start()
    return {"status": "running", "total": stored}


@router.get("/reextract/status")
def reextract_status(job_id: uuid.UUID, db: Session = Depends(get_session)):
    _get_job_or_404(job_id, db)
    progress = _get_progress(job_id)
    if not progress:
        return {"status": "idle"}
    return progress
