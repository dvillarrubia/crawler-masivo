"""Semantic analysis router — GSC integration, embedding analysis, visualization."""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

import redis
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config import REDIS_URL
from shared.database import SessionLocal, get_session
from shared.models import Job, Url
from shared.semantic_models import (
    GeminiAccount,
    GscAccount,
    GscJobData,
    GscQueryData,
    QueryEmbedding,
    SemanticAnalysis,
    SemanticCannibalization,
    SemanticChunk,
    SemanticPage,
)

router = APIRouter(prefix="/api", tags=["semantic"])


def _resolve_gemini_key_from_analysis(
    analysis: SemanticAnalysis,
    db: Session,
) -> str:
    """Look up the Gemini API key tied to an analysis.

    Used by endpoints (target-rings, gap-analysis) that need to embed a
    query *after* the main analysis has completed. The account id is
    stored in `analysis.config.gemini_account_id`; if it has been deleted
    since the analysis ran, we respond 400 so the user can re-attach a
    new account rather than silently failing.
    """
    cfg = analysis.config or {}
    account_id_raw = cfg.get("gemini_account_id")
    if not account_id_raw:
        raise HTTPException(
            status_code=400,
            detail="This analysis has no Gemini account attached. Re-run the analysis.",
        )
    try:
        account_id = uuid.UUID(account_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Stored gemini_account_id is invalid")
    account = db.query(GeminiAccount).filter(GeminiAccount.id == account_id).first()
    if not account:
        raise HTTPException(
            status_code=400,
            detail="The Gemini account used for this analysis no longer exists",
        )
    return account.api_key


# T8/C4: the matching normalizer now lives in shared.url_normalization so
# there is a single maintained tracking-param list in the codebase. This
# alias keeps existing call sites working.
from shared.url_normalization import normalize_for_match as _normalize_url_for_match

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class GscAccountCreate(BaseModel):
    name: str
    credentials_json: dict[str, Any]


class GscAccountResponse(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime | None = None


class FetchGscRequest(BaseModel):
    gsc_account_id: uuid.UUID
    property_url: str
    days: int = 90


class AnalyzeRequest(BaseModel):
    """Request body for /semantic/analyze.

    The embedding model is fixed at the backend level (Gemini
    embedding-001 with 1024-dim output). It is not a user-tunable
    parameter on purpose: changing it would silently invalidate stored
    `pgvector(1024)` embeddings and break cross-analysis comparisons.

    `gemini_account_id` is required: each client uses their own API key
    so they pay for their own usage. No global/shared key fallback.
    """
    gemini_account_id: uuid.UUID
    alpha: float = 0.6
    beta: float = 0.4
    cannibal_threshold: float = 0.92


class GeminiAccountCreate(BaseModel):
    name: str
    api_key: str


class GeminiAccountResponse(BaseModel):
    id: uuid.UUID
    name: str
    api_key_preview: str  # first 4 + "…" + last 4, never the full key
    created_at: datetime | None = None


class GapAnalysisRequest(BaseModel):
    topic: str


class TargetRingsRequest(BaseModel):
    target_theme: str


class SemanticStatusResponse(BaseModel):
    status: str
    error_message: str | None = None
    total_pages: int = 0
    progress: int = 0
    stage: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_job_or_404(job_id: uuid.UUID, db: Session) -> Job:
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _get_latest_analysis(job_id: uuid.UUID, db: Session) -> SemanticAnalysis | None:
    return (
        db.query(SemanticAnalysis)
        .filter(SemanticAnalysis.job_id == job_id)
        .order_by(SemanticAnalysis.created_at.desc())
        .first()
    )


def _load_gsc_map(job_id: uuid.UUID, db: Session) -> dict[int, dict]:
    """Load GSC data as a dict keyed by url_id."""
    rows = db.query(GscJobData).filter(GscJobData.job_id == job_id).all()
    return {
        r.url_id: {
            "clicks": r.clicks,
            "impressions": r.impressions,
            "ctr": r.ctr,
            "position": r.position,
        }
        for r in rows
    }


# --- Progress heartbeat ----------------------------------------------------
# We keep two pieces of state in Redis per running analysis:
#   - progress payload: {stage, progress, updated_at}
#   - TTL: 24h so it survives a long-running embedding stage (large corpora
#     with a local model can take well over an hour).
# The watchdog in get_semantic_status() considers the thread dead only if
# `updated_at` is older than HEARTBEAT_DEAD_AFTER_S — never because the key
# happens to be absent (which used to kill live threads after Redis TTL).
PROGRESS_TTL_S = 24 * 3600
HEARTBEAT_DEAD_AFTER_S = 15 * 60  # 15 min with zero heartbeat → presume dead


def _redis_progress_key(analysis_id: str) -> str:
    return f"semantic:{analysis_id}:progress"


def _set_progress(r: redis.Redis, analysis_id: str, stage: str, pct: int) -> None:
    payload = {
        "stage": stage,
        "progress": pct,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    r.set(_redis_progress_key(analysis_id), json.dumps(payload), ex=PROGRESS_TTL_S)


def _get_progress(analysis_id: str) -> dict[str, Any]:
    from api.dependencies import get_redis
    try:
        r = get_redis()
        raw = r.get(_redis_progress_key(analysis_id))
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {}


def _heartbeat_age_seconds(progress_info: dict[str, Any]) -> float | None:
    """Return seconds since last heartbeat, or None if no valid timestamp."""
    ts_raw = progress_info.get("updated_at")
    if not ts_raw:
        return None
    try:
        ts = datetime.fromisoformat(ts_raw)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# GSC Account endpoints
# ---------------------------------------------------------------------------
@router.get("/semantic/gsc-accounts", response_model=list[GscAccountResponse])
def list_gsc_accounts(db: Session = Depends(get_session)):
    accounts = db.query(GscAccount).order_by(GscAccount.created_at.desc()).all()
    return [
        GscAccountResponse(id=a.id, name=a.name, created_at=a.created_at)
        for a in accounts
    ]


@router.post("/semantic/gsc-accounts", response_model=GscAccountResponse)
def create_gsc_account(body: GscAccountCreate, db: Session = Depends(get_session)):
    account = GscAccount(name=body.name, credentials_json=body.credentials_json)
    db.add(account)
    db.commit()
    db.refresh(account)
    return GscAccountResponse(id=account.id, name=account.name, created_at=account.created_at)


@router.delete("/semantic/gsc-accounts/{account_id}")
def delete_gsc_account(account_id: uuid.UUID, db: Session = Depends(get_session)):
    account = db.query(GscAccount).filter(GscAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="GSC account not found")
    db.delete(account)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Gemini Account endpoints — each client brings their own API key
# ---------------------------------------------------------------------------
def _mask_key(api_key: str) -> str:
    """Render a non-reversible preview so the UI can identify a key."""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "…"
    return f"{api_key[:4]}…{api_key[-4:]}"


def _gemini_account_response(a: GeminiAccount) -> GeminiAccountResponse:
    return GeminiAccountResponse(
        id=a.id,
        name=a.name,
        api_key_preview=_mask_key(a.api_key),
        created_at=a.created_at,
    )


@router.get("/semantic/gemini-accounts", response_model=list[GeminiAccountResponse])
def list_gemini_accounts(db: Session = Depends(get_session)):
    accounts = db.query(GeminiAccount).order_by(GeminiAccount.created_at.desc()).all()
    return [_gemini_account_response(a) for a in accounts]


@router.post("/semantic/gemini-accounts", response_model=GeminiAccountResponse)
def create_gemini_account(body: GeminiAccountCreate, db: Session = Depends(get_session)):
    if not body.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key cannot be empty")
    account = GeminiAccount(name=body.name.strip() or "Gemini account", api_key=body.api_key.strip())
    db.add(account)
    db.commit()
    db.refresh(account)
    return _gemini_account_response(account)


@router.delete("/semantic/gemini-accounts/{account_id}")
def delete_gemini_account(account_id: uuid.UUID, db: Session = Depends(get_session)):
    account = db.query(GeminiAccount).filter(GeminiAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Gemini account not found")
    db.delete(account)
    db.commit()
    return {"ok": True}


@router.get("/semantic/gsc-accounts/{account_id}/properties")
def get_gsc_properties(account_id: uuid.UUID, db: Session = Depends(get_session)):
    account = db.query(GscAccount).filter(GscAccount.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="GSC account not found")
    try:
        from POC_centro_semantico.src.gsc import get_gsc_properties as _get_props
        props = _get_props(account.credentials_json)
        return {"properties": props}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Fetch GSC data for a job
# ---------------------------------------------------------------------------
@router.post("/jobs/{job_id}/semantic/fetch-gsc")
def fetch_gsc_data(job_id: uuid.UUID, body: FetchGscRequest, db: Session = Depends(get_session)):
    _get_job_or_404(job_id, db)

    account = db.query(GscAccount).filter(GscAccount.id == body.gsc_account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="GSC account not found")

    try:
        from POC_centro_semantico.src.gsc import (
            fetch_gsc_data as _fetch,
            fetch_gsc_query_page_data as _fetch_queries,
        )
        from shared.url_normalization import (
            UrlNormalizationConfig,
            compute_url_hash as _hash,
        )

        df = _fetch(account.credentials_json, body.property_url, days=body.days)
        if df.empty:
            return {"matched": 0, "unmatched": 0, "total_gsc_rows": 0, "query_rows": 0}

        # T8/T9: hash GSC URLs under THIS job's normalization config so the
        # hashes join against the crawl's url_hash.
        job = db.query(Job).filter(Job.id == job_id).first()
        norm_config = UrlNormalizationConfig.from_job_config(
            job.config if job else None
        )

        # Match GSC URLs to crawled URLs. We do both an exact match and a
        # normalized match (lowercased, trailing slash stripped, tracking
        # params dropped) so minor formatting differences between GSC and
        # the crawler don't silently drop rows.
        url_rows = db.query(Url.id, Url.url).filter(Url.job_id == job_id).all()
        url_map: dict[str, int] = {row.url: row.id for row in url_rows}
        url_map_norm: dict[str, int] = {
            _normalize_url_for_match(row.url): row.id for row in url_rows
        }

        def _resolve_url_id(raw: str) -> int | None:
            return url_map.get(raw) or url_map_norm.get(_normalize_url_for_match(raw))

        # Delete old GSC data for this job
        db.query(GscJobData).filter(GscJobData.job_id == job_id).delete()
        db.query(GscQueryData).filter(GscQueryData.job_id == job_id).delete()

        # T9/D2: unmatched GSC URLs are KEPT (url_id NULL) — they are the
        # orphan candidates that used to be silently discarded.
        matched = 0
        unmatched = 0
        for _, row in df.iterrows():
            raw_url = str(row["url"])
            url_id = _resolve_url_id(raw_url)
            if url_id:
                matched += 1
            else:
                unmatched += 1
            db.add(GscJobData(
                job_id=job_id,
                url_id=url_id,
                url=raw_url,
                url_hash=_hash(raw_url, norm_config),
                clicks=int(row["clicks"]),
                impressions=int(row["impressions"]),
                ctr=float(row["ctr"]),
                position=float(row["position"]),
            ))

        # Fetch query+page data for cannibalization validation
        query_matched = 0
        try:
            df_q = _fetch_queries(
                account.credentials_json, body.property_url, days=body.days,
            )
            if not df_q.empty:
                for _, row in df_q.iterrows():
                    url_id = _resolve_url_id(row["url"])
                    if url_id:
                        db.add(GscQueryData(
                            job_id=job_id,
                            url_id=url_id,
                            query=str(row["query"])[:500],
                            clicks=int(row["clicks"]),
                            impressions=int(row["impressions"]),
                            ctr=float(row["ctr"]),
                            position=float(row["position"]),
                        ))
                        query_matched += 1
        except Exception:
            pass  # Query data is optional, don't fail the whole fetch

        db.commit()
        return {
            "matched": matched,
            "unmatched": unmatched,
            "total_gsc_rows": len(df),
            "query_rows": query_matched,
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Run semantic analysis
# ---------------------------------------------------------------------------
def _run_analysis_thread(
    analysis_id: str,
    job_id: str,
    config: dict,
    gsc_data: dict | None,
    gemini_api_key: str,
):
    """Background thread that runs the semantic engine."""
    db = SessionLocal()
    r = redis.from_url(REDIS_URL, decode_responses=True)
    analysis_uuid = uuid.UUID(analysis_id)
    job_uuid = uuid.UUID(job_id)
    try:
        analysis = db.query(SemanticAnalysis).filter(SemanticAnalysis.id == analysis_uuid).first()
        if not analysis:
            return

        analysis.status = "running"
        db.commit()

        _set_progress(r, analysis_id, "starting", 0)

        def progress_cb(stage: str, pct: int):
            _set_progress(r, analysis_id, stage, pct)

        from POC_centro_semantico.src.embedding_backends import get_backend
        from POC_centro_semantico.src.engine import SemanticEngine

        backend = get_backend(api_key=gemini_api_key)
        engine = SemanticEngine()
        result = engine.process(
            db=db,
            job_id=job_uuid,
            alpha=config.get("alpha", 0.6),
            beta=config.get("beta", 0.4),
            cannibal_threshold=config.get("cannibal_threshold", 0.92),
            gsc_data=gsc_data,
            progress_callback=progress_cb,
            backend=backend,
            # T11 (default fixed = comportamiento actual bit a bit)
            chunking_strategy=config.get("chunking_strategy", "fixed"),
            chunk_embedding_mode=config.get("chunk_embedding_mode", "aggregate"),
        )

        if result.get("error"):
            analysis.status = "failed"
            analysis.error_message = result["error"]
            analysis.total_pages = result.get("total_pages", 0)
            db.commit()
            return

        # Save results to DB
        analysis.status = "completed"
        analysis.site_metrics = result["site_metrics"]
        analysis.centroid = result["centroid"]
        analysis.config = result["config"]
        analysis.total_pages = result["total_pages"]
        analysis.completed_at = datetime.now(timezone.utc)

        # Delete old pages/cannibal/chunk data for this analysis (safety)
        db.query(SemanticPage).filter(SemanticPage.analysis_id == analysis_uuid).delete()
        db.query(SemanticCannibalization).filter(SemanticCannibalization.analysis_id == analysis_uuid).delete()
        from shared.semantic_models import SemanticChunk

        db.query(SemanticChunk).filter(SemanticChunk.analysis_id == analysis_uuid).delete()

        # Insert pages
        for p in result["pages"]:
            db.add(SemanticPage(
                analysis_id=analysis_uuid,
                url_id=p["url_id"],
                embedding=p["embedding"],
                cluster_id=p["cluster_id"],
                ring=p["ring"],
                semantic_role=p["semantic_role"],
                distance_to_centroid=p["distance_to_centroid"],
                weight=p["weight"],
                pr_norm=p["pr_norm"],
                clicks_norm=p["clicks_norm"],
                x=p["x"],
                y=p["y"],
            ))

        # T11: persist chunk-level embeddings (passage-level GEO basis)
        for c in result.get("chunks", []):
            db.add(SemanticChunk(
                analysis_id=analysis_uuid,
                url_id=c["url_id"],
                position=c["position"],
                heading_path=c.get("heading_path"),
                text=c["text"],
                word_count=c.get("word_count"),
                char_start=c.get("char_start"),
                char_end=c.get("char_end"),
                embedding=c.get("embedding"),
                strategy=c.get("strategy", "fixed"),
            ))

        # Insert cannibalization pairs
        for pair in result["cannibalization"]:
            db.add(SemanticCannibalization(
                analysis_id=analysis_uuid,
                url_dominant_id=pair["url_dominant_id"],
                url_weak_id=pair["url_weak_id"],
                cosine_similarity=pair["cosine_similarity"],
            ))

        db.commit()

        # T10: acción a partir del análisis — sugerencias de enlazado y
        # canibalización como issues firmables. Nunca bloquean el análisis.
        try:
            from analysis.link_suggester import (
                compute_semantic_pagerank,
                emit_cannibalization_issues,
                generate_for_job,
            )

            generate_for_job(db, job_uuid, analysis_uuid)
            emit_cannibalization_issues(db, job_uuid, analysis_uuid)
            compute_semantic_pagerank(db, job_uuid, analysis_uuid)  # T18
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Link suggestions failed (analysis %s)", analysis_id)

    except Exception as e:
        try:
            analysis = db.query(SemanticAnalysis).filter(SemanticAnalysis.id == analysis_uuid).first()
            if analysis:
                analysis.status = "failed"
                analysis.error_message = str(e)
                db.commit()
        except Exception:
            pass
    finally:
        try:
            r.delete(_redis_progress_key(analysis_id))
            r.close()
        except Exception:
            pass
        db.close()


@router.post("/jobs/{job_id}/semantic/analyze")
def run_semantic_analysis(
    job_id: uuid.UUID,
    body: AnalyzeRequest,
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    # Validate weighting coefficients. The semantic engine treats α and β as
    # the relative pull of PageRank vs GSC clicks on the centroid; they must
    # be on a comparable scale or the saved pr_norm/clicks_norm columns lose
    # interpretability across analyses.
    if not (0.0 <= body.alpha <= 1.0 and 0.0 <= body.beta <= 1.0):
        raise HTTPException(status_code=400, detail="alpha and beta must be in [0, 1]")
    if abs((body.alpha + body.beta) - 1.0) > 1e-6:
        raise HTTPException(status_code=400, detail="alpha + beta must equal 1.0")

    # Resolve the Gemini account that will pay for this run.
    gemini_account = (
        db.query(GeminiAccount).filter(GeminiAccount.id == body.gemini_account_id).first()
    )
    if not gemini_account:
        raise HTTPException(status_code=404, detail="Gemini account not found")

    # Check if there's already a running analysis
    existing = (
        db.query(SemanticAnalysis)
        .filter(SemanticAnalysis.job_id == job_id, SemanticAnalysis.status == "running")
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Analysis already running for this job")

    # Load GSC data if available
    gsc_rows = db.query(GscJobData).filter(GscJobData.job_id == job_id).all()
    gsc_data: dict[str, dict] | None = None
    if gsc_rows:
        # Need URL strings
        url_id_map = {
            row.id: row.url
            for row in db.query(Url.id, Url.url).filter(Url.job_id == job_id).all()
        }
        gsc_data = {}
        for g in gsc_rows:
            url_str = url_id_map.get(g.url_id)
            if url_str:
                gsc_data[url_str] = {
                    "clicks": g.clicks,
                    "impressions": g.impressions,
                    "ctr": g.ctr,
                    "position": g.position,
                }

    config = {
        "alpha": body.alpha,
        "beta": body.beta,
        "cannibal_threshold": body.cannibal_threshold,
        # Persist the account id (NOT the key itself) so target-rings and
        # gap-analysis can re-use the same billing source without the user
        # selecting it again. The actual key is fetched from the DB at
        # request time so rotating it is enough to take effect.
        "gemini_account_id": str(gemini_account.id),
    }

    # Create analysis record
    analysis = SemanticAnalysis(
        job_id=job_id,
        status="pending",
        config=config,
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    analysis_id = str(analysis.id)

    # Launch background thread (snapshot the key now — if the account is
    # deleted/rotated mid-run, this analysis still completes cleanly).
    t = threading.Thread(
        target=_run_analysis_thread,
        args=(analysis_id, str(job_id), config, gsc_data, gemini_account.api_key),
        daemon=True,
    )
    t.start()

    return {"analysis_id": analysis_id, "status": "pending"}


# ---------------------------------------------------------------------------
# Status / Results
# ---------------------------------------------------------------------------
@router.get("/jobs/{job_id}/semantic/status")
def get_semantic_status(job_id: uuid.UUID, db: Session = Depends(get_session)):
    analysis = _get_latest_analysis(job_id, db)

    # Source chips in the UI need to know whether GSC data exists even
    # before any analysis has run. Counts included so the user can SEE
    # that the import happened (and how many rows matched the crawl).
    from sqlalchemy import func as _func

    gsc_total, gsc_matched = (
        db.query(
            _func.count(GscJobData.id),
            _func.count(GscJobData.url_id),
        )
        .filter(GscJobData.job_id == job_id)
        .one()
    )
    gsc_info = {
        "total": gsc_total,
        "matched": gsc_matched,
        "unmatched": gsc_total - gsc_matched,
    }
    has_gsc = gsc_total > 0

    if not analysis:
        return {"status": "none", "has_gsc_data": has_gsc, "gsc": gsc_info}

    progress_info = _get_progress(str(analysis.id))

    # Detect a truly dead thread by missing *heartbeat*, not by missing key.
    # The old logic killed live analyses as soon as Redis TTL expired, even
    # while the engine was still working at 700%+ CPU. The new rule:
    #   * If there is a heartbeat → check its age. >HEARTBEAT_DEAD_AFTER_S
    #     since last update means the thread really stopped pumping.
    #   * If there is no heartbeat at all → only call it dead if enough time
    #     has passed since the analysis was created that the worker should
    #     have written at least one update.
    if analysis.status == "running":
        elapsed = (datetime.now(timezone.utc) - analysis.created_at).total_seconds()
        age = _heartbeat_age_seconds(progress_info)
        dead = False
        if age is not None and age > HEARTBEAT_DEAD_AFTER_S:
            dead = True
            reason = f"No heartbeat for {int(age)}s"
        elif age is None and not progress_info and elapsed > HEARTBEAT_DEAD_AFTER_S:
            dead = True
            reason = f"No progress ever recorded after {int(elapsed)}s"

        if dead:
            analysis.status = "failed"
            analysis.error_message = f"Analysis thread died: {reason}"
            db.commit()

    return {
        "analysis_id": str(analysis.id),
        "status": analysis.status,
        "error_message": analysis.error_message,
        "total_pages": analysis.total_pages or 0,
        "progress": progress_info.get("progress", 100 if analysis.status == "completed" else 0),
        "stage": progress_info.get("stage", ""),
        "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
        "completed_at": analysis.completed_at.isoformat() if analysis.completed_at else None,
        "has_gsc_data": has_gsc,
        "gsc": gsc_info,
    }


@router.get("/jobs/{job_id}/semantic/results")
def get_semantic_results(job_id: uuid.UUID, db: Session = Depends(get_session)):
    analysis = _get_latest_analysis(job_id, db)
    if not analysis or analysis.status != "completed":
        raise HTTPException(status_code=404, detail="No completed analysis found")

    pages = (
        db.query(
            SemanticPage.url_id,
            SemanticPage.cluster_id,
            SemanticPage.ring,
            SemanticPage.semantic_role,
            SemanticPage.distance_to_centroid,
            SemanticPage.weight,
            SemanticPage.pr_norm,
            SemanticPage.clicks_norm,
            SemanticPage.x,
            SemanticPage.y,
            Url.url,
        )
        .join(Url, Url.id == SemanticPage.url_id)
        .filter(SemanticPage.analysis_id == analysis.id)
        .all()
    )

    # Load GSC data for this job
    gsc_map = _load_gsc_map(job_id, db)

    gsc_summary = None
    if gsc_map:
        total_clicks = sum(g["clicks"] for g in gsc_map.values())
        total_impressions = sum(g["impressions"] for g in gsc_map.values())
        ctrs = [g["ctr"] for g in gsc_map.values() if g["ctr"] is not None]
        positions = [g["position"] for g in gsc_map.values() if g["position"] is not None]
        gsc_summary = {
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "avg_ctr": round(sum(ctrs) / len(ctrs), 4) if ctrs else 0,
            "avg_position": round(sum(positions) / len(positions), 1) if positions else 0,
            "urls_with_data": len(gsc_map),
        }

    return {
        "analysis_id": str(analysis.id),
        "site_metrics": analysis.site_metrics,
        "config": analysis.config,
        "total_pages": analysis.total_pages,
        "gsc_summary": gsc_summary,
        "pages": [
            {
                "url_id": p.url_id,
                "url": p.url,
                "cluster_id": p.cluster_id,
                "ring": p.ring,
                "semantic_role": p.semantic_role,
                "distance_to_centroid": p.distance_to_centroid,
                "weight": p.weight,
                "pr_norm": p.pr_norm,
                "clicks_norm": p.clicks_norm,
                "x": p.x,
                "y": p.y,
                "clicks": gsc_map.get(p.url_id, {}).get("clicks"),
                "impressions": gsc_map.get(p.url_id, {}).get("impressions"),
                "ctr": gsc_map.get(p.url_id, {}).get("ctr"),
                "position": gsc_map.get(p.url_id, {}).get("position"),
            }
            for p in pages
        ],
    }


@router.get("/jobs/{job_id}/semantic/cannibalization")
def get_cannibalization(
    job_id: uuid.UUID,
    brand: str = Query("", description="Brand keywords to exclude, comma-separated"),
    db: Session = Depends(get_session),
):
    analysis = _get_latest_analysis(job_id, db)
    if not analysis or analysis.status != "completed":
        raise HTTPException(status_code=404, detail="No completed analysis found")

    pairs = (
        db.query(
            SemanticCannibalization.url_dominant_id,
            SemanticCannibalization.url_weak_id,
            SemanticCannibalization.cosine_similarity,
        )
        .filter(SemanticCannibalization.analysis_id == analysis.id)
        .order_by(SemanticCannibalization.cosine_similarity.desc())
        .all()
    )

    # Resolve URLs
    url_ids = set()
    for p in pairs:
        url_ids.add(p.url_dominant_id)
        url_ids.add(p.url_weak_id)

    url_map = {}
    if url_ids:
        rows = db.query(Url.id, Url.url).filter(Url.id.in_(url_ids)).all()
        url_map = {r.id: r.url for r in rows}

    gsc_map = _load_gsc_map(analysis.job_id, db)

    # Load query data for keyword overlap validation
    brand_terms = [b.strip().lower() for b in brand.split(",") if b.strip()]
    query_map: dict[int, set[str]] = {}  # url_id -> set of non-brand queries
    has_query_data = False

    if url_ids:
        query_rows = (
            db.query(GscQueryData.url_id, GscQueryData.query)
            .filter(GscQueryData.job_id == job_id, GscQueryData.url_id.in_(url_ids))
            .all()
        )
        if query_rows:
            has_query_data = True
            for qr in query_rows:
                q_lower = qr.query.lower()
                # Skip brand queries
                if brand_terms and any(bt in q_lower for bt in brand_terms):
                    continue
                query_map.setdefault(qr.url_id, set()).add(q_lower)

    result_pairs = []
    for p in pairs:
        shared_queries: list[str] = []
        if has_query_data:
            q_dom = query_map.get(p.url_dominant_id, set())
            q_weak = query_map.get(p.url_weak_id, set())
            shared_queries = sorted(q_dom & q_weak)[:10]  # top 10 shared

        result_pairs.append({
            "url_dominant": url_map.get(p.url_dominant_id, ""),
            "url_dominant_id": p.url_dominant_id,
            "url_weak": url_map.get(p.url_weak_id, ""),
            "url_weak_id": p.url_weak_id,
            "cosine_similarity": p.cosine_similarity,
            "dominant_clicks": gsc_map.get(p.url_dominant_id, {}).get("clicks"),
            "dominant_position": gsc_map.get(p.url_dominant_id, {}).get("position"),
            "weak_clicks": gsc_map.get(p.url_weak_id, {}).get("clicks"),
            "weak_position": gsc_map.get(p.url_weak_id, {}).get("position"),
            "shared_queries": shared_queries,
            "shared_query_count": len(shared_queries),
        })

    return {
        "has_query_data": has_query_data,
        "brand_terms": brand_terms,
        "pairs": result_pairs,
    }


# ---------------------------------------------------------------------------
# Visualization data
# ---------------------------------------------------------------------------
@router.get("/jobs/{job_id}/semantic/scatter-data")
def get_scatter_data(job_id: uuid.UUID, db: Session = Depends(get_session)):
    analysis = _get_latest_analysis(job_id, db)
    if not analysis or analysis.status != "completed":
        raise HTTPException(status_code=404, detail="No completed analysis found")

    pages = (
        db.query(
            SemanticPage.url_id,
            SemanticPage.cluster_id,
            SemanticPage.ring,
            SemanticPage.semantic_role,
            SemanticPage.distance_to_centroid,
            SemanticPage.weight,
            SemanticPage.x,
            SemanticPage.y,
            Url.url,
        )
        .join(Url, Url.id == SemanticPage.url_id)
        .filter(SemanticPage.analysis_id == analysis.id)
        .all()
    )

    pages_data = [
        {
            "url_id": p.url_id,
            "url": p.url,
            "cluster_id": p.cluster_id,
            "ring": p.ring,
            "semantic_role": p.semantic_role,
            "distance_to_centroid": p.distance_to_centroid,
            "weight": p.weight,
            "x": p.x,
            "y": p.y,
        }
        for p in pages
    ]

    job = _get_job_or_404(job_id, db)
    from POC_centro_semantico.src.visualization import build_scatter_umap
    return build_scatter_umap(pages_data, site_name=job.name)


@router.get("/jobs/{job_id}/semantic/ring-data")
def get_ring_data(job_id: uuid.UUID, db: Session = Depends(get_session)):
    analysis = _get_latest_analysis(job_id, db)
    if not analysis or analysis.status != "completed":
        raise HTTPException(status_code=404, detail="No completed analysis found")

    pages = (
        db.query(
            SemanticPage.url_id,
            SemanticPage.cluster_id,
            SemanticPage.ring,
            SemanticPage.semantic_role,
            SemanticPage.distance_to_centroid,
            SemanticPage.weight,
            SemanticPage.pr_norm,
            SemanticPage.clicks_norm,
            Url.url,
            Url.inlinks_count,
            Url.unique_inlinks_count,
        )
        .join(Url, Url.id == SemanticPage.url_id)
        .filter(SemanticPage.analysis_id == analysis.id)
        .all()
    )

    gsc_map = _load_gsc_map(job_id, db)

    pages_data = [
        {
            "url_id": p.url_id,
            "url": p.url,
            "cluster_id": p.cluster_id,
            "ring": p.ring,
            "semantic_role": p.semantic_role,
            "distance_to_centroid": p.distance_to_centroid,
            "weight": p.weight,
            "pr_norm": p.pr_norm,
            "clicks_norm": p.clicks_norm,
            "clicks": gsc_map.get(p.url_id, {}).get("clicks", 0),
            "impressions": gsc_map.get(p.url_id, {}).get("impressions", 0),
            "position": gsc_map.get(p.url_id, {}).get("position"),
            "inlinks": p.inlinks_count or 0,
            "unique_inlinks": p.unique_inlinks_count or 0,
        }
        for p in pages
    ]

    from POC_centro_semantico.src.visualization import build_ring_map
    return build_ring_map(pages_data, site_metrics=analysis.site_metrics)


@router.post("/jobs/{job_id}/semantic/target-rings")
def get_target_rings(
    job_id: uuid.UUID,
    body: TargetRingsRequest,
    db: Session = Depends(get_session),
):
    """Re-center the ring map around a target theme.

    Returns: alignment score, re-classified rings, and actionable recommendations.
    """
    analysis = _get_latest_analysis(job_id, db)
    if not analysis or analysis.status != "completed":
        raise HTTPException(status_code=404, detail="No completed analysis found")

    if analysis.centroid is None:
        raise HTTPException(status_code=400, detail="Analysis has no centroid")

    pages = (
        db.query(
            SemanticPage.url_id,
            SemanticPage.cluster_id,
            SemanticPage.embedding,
            SemanticPage.distance_to_centroid,
            SemanticPage.ring,
            SemanticPage.weight,
            Url.url,
        )
        .join(Url, Url.id == SemanticPage.url_id)
        .filter(SemanticPage.analysis_id == analysis.id)
        .all()
    )

    if not pages:
        raise HTTPException(status_code=400, detail="No pages in analysis")

    import numpy as np
    from sklearn.metrics.pairwise import cosine_similarity as cos_sim
    from POC_centro_semantico.src.analysis import classify_rings
    from POC_centro_semantico.src.embedding_backends import get_backend
    from POC_centro_semantico.src.visualization import build_ring_map

    # Embed target theme as a *query* through the same backend used at
    # analysis time, paid by the same Gemini account that ran the analysis.
    # Stored page embeddings are L2-normalized passages → comparing against
    # a query-type embedding gives the asymmetric retrieval signal Gemini
    # was trained for.
    api_key = _resolve_gemini_key_from_analysis(analysis, db)
    backend = get_backend(api_key=api_key)
    target_emb = backend.embed_query(body.target_theme)
    centroid = np.array(analysis.centroid)

    # Alignment: cosine similarity between current centroid and target
    alignment = float(cos_sim([centroid], [target_emb])[0][0])

    # Compute distances from target for all pages (both sides unit-norm).
    vectors = np.array([list(p.embedding) for p in pages])
    dists_to_target = np.linalg.norm(vectors - target_emb, axis=1)

    # Reclassify rings based on distance to target (Peripheral = IQR outliers).
    target_rings = classify_rings(dists_to_target)

    # Build pages_data for ring map
    pages_data = []
    for i, p in enumerate(pages):
        pages_data.append({
            "url_id": p.url_id,
            "url": p.url,
            "cluster_id": p.cluster_id,
            "ring": target_rings[i],
            "ring_current": p.ring,
            "semantic_role": "core" if target_rings[i] == "Core" else "peripheral",
            "distance_to_centroid": float(dists_to_target[i]),
            "weight": p.weight,
        })

    # Ring counts
    from collections import Counter
    ring_counts = dict(Counter(target_rings))

    # Recommendations
    gsc_map = _load_gsc_map(job_id, db)
    reinforce = []  # close to target, low weight → increase links
    refocus = []    # far from target, high weight → pulling center away

    weights = np.array([p.weight for p in pages])
    w_median = float(np.median(weights))
    d_median = float(np.median(dists_to_target))

    for i, p in enumerate(pages):
        gsc = gsc_map.get(p.url_id, {})
        entry = {
            "url": p.url,
            "url_id": p.url_id,
            "ring_target": target_rings[i],
            "ring_current": p.ring,
            "distance_to_target": round(float(dists_to_target[i]), 4),
            "distance_to_centroid": p.distance_to_centroid,
            "weight": p.weight,
            "clicks": gsc.get("clicks"),
        }
        if dists_to_target[i] <= d_median and p.weight < w_median:
            reinforce.append(entry)
        elif dists_to_target[i] > d_median and p.weight >= w_median:
            refocus.append(entry)

    reinforce.sort(key=lambda x: x["distance_to_target"])
    refocus.sort(key=lambda x: -x["weight"])

    # Build ring map visualization
    ring_map = build_ring_map(pages_data, site_metrics=analysis.site_metrics)

    return {
        "alignment": round(alignment, 4),
        "target_theme": body.target_theme,
        "ring_counts": ring_counts,
        "ring_map": ring_map,
        "reinforce": reinforce[:10],
        "refocus": refocus[:10],
    }


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------
@router.post("/jobs/{job_id}/semantic/gap-analysis")
def run_gap_analysis(
    job_id: uuid.UUID,
    body: GapAnalysisRequest,
    db: Session = Depends(get_session),
):
    analysis = _get_latest_analysis(job_id, db)
    if not analysis or analysis.status != "completed":
        raise HTTPException(status_code=404, detail="No completed analysis found")

    if analysis.centroid is None:
        raise HTTPException(status_code=400, detail="Analysis has no centroid")

    # Load page embeddings
    pages = (
        db.query(SemanticPage.url_id, SemanticPage.embedding, Url.url)
        .join(Url, Url.id == SemanticPage.url_id)
        .filter(SemanticPage.analysis_id == analysis.id)
        .all()
    )

    if not pages:
        raise HTTPException(status_code=400, detail="No pages in analysis")

    import numpy as np
    from POC_centro_semantico.src.analysis import gap_analysis as _gap
    from POC_centro_semantico.src.embedding_backends import get_backend

    centroid = np.array(analysis.centroid)
    vectors = np.array([list(p.embedding) for p in pages])
    url_ids = [p.url_id for p in pages]
    url_list = [p.url for p in pages]
    url_map = {p.url_id: p.url for p in pages}

    api_key = _resolve_gemini_key_from_analysis(analysis, db)
    backend = get_backend(api_key=api_key)
    result = _gap(centroid, body.topic, vectors, url_ids, url_list, backend, top_n=20)

    # Resolve URLs + add GSC data
    gsc_map = _load_gsc_map(job_id, db)
    for c in result["candidates"]:
        c["url"] = url_map.get(c["url_id"], "")
        gsc = gsc_map.get(c["url_id"], {})
        c["clicks"] = gsc.get("clicks")
        c["position"] = gsc.get("position")

    return {"topic": body.topic, "candidates": result["candidates"]}


# ---------------------------------------------------------------------------
# T18 (cierre) — relevancia de anchors
# ---------------------------------------------------------------------------
class AnchorRelevanceRequest(BaseModel):
    """Params del análisis de anchors. El umbral por defecto es laxo a
    propósito: anchor↔página es una comparación query→documento y los
    cosenos absolutos son más bajos que entre documentos."""

    mismatch_threshold: float = 0.35
    max_anchors: int = 300


@router.post("/jobs/{job_id}/semantic/anchor-relevance")
def run_anchor_relevance_endpoint(
    job_id: uuid.UUID,
    body: AnchorRelevanceRequest,
    db: Session = Depends(get_session),
):
    """T18: embebe los anchors contextuales como queries y los compara
    con el vector de su página destino. Emite ``generic_anchor`` (lexical,
    agregado por destino) y ``anchor_target_mismatch`` como issues
    firmables. Los resultados persistentes son los issues (Cola de firma).
    """
    _get_job_or_404(job_id, db)
    analysis = _get_latest_analysis(job_id, db)
    if not analysis or analysis.status != "completed":
        raise HTTPException(status_code=404, detail="No completed analysis found")
    if not (0.0 <= body.mismatch_threshold <= 1.0):
        raise HTTPException(status_code=400, detail="mismatch_threshold must be in [0, 1]")
    if not (1 <= body.max_anchors <= 2000):
        raise HTTPException(status_code=400, detail="max_anchors must be in [1, 2000]")

    from analysis.anchor_relevance import run_anchor_relevance
    from POC_centro_semantico.src.embedding_backends import get_backend

    api_key = _resolve_gemini_key_from_analysis(analysis, db)
    backend = get_backend(api_key=api_key)

    result = run_anchor_relevance(
        db, job_id, analysis.id, backend,
        mismatch_threshold=body.mismatch_threshold,
        max_anchors=body.max_anchors,
    )
    if result.get("status") == "blocked":
        db.rollback()
        return result
    db.commit()
    return result


# ---------------------------------------------------------------------------
# T19 — Query→passage coverage
# ---------------------------------------------------------------------------
class QueryCoverageRequest(BaseModel):
    """Params for the T19 coverage run. Defaults follow the maestro doc:
    top demand queries only (embedding cost is per query) and a coverage
    threshold consistent with the gap-analysis verdict bands."""

    max_queries: int = 200
    min_impressions: int = 10
    sim_threshold: float = 0.60
    buried_min_position: int = 5
    orphan_threshold: float = 0.50


@router.post("/jobs/{job_id}/semantic/query-coverage")
def run_query_coverage_endpoint(
    job_id: uuid.UUID,
    body: QueryCoverageRequest,
    db: Session = Depends(get_session),
):
    """T19: embed the job's GSC queries and cross them against the
    persisted chunks (T11). Emits ``passage_gap`` / ``buried_passage`` /
    ``orphan_chunk`` as signable issues and caches the per-query result
    in ``query_embeddings`` so GET re-serves without re-embedding.
    """
    _get_job_or_404(job_id, db)
    analysis = _get_latest_analysis(job_id, db)
    if not analysis or analysis.status != "completed":
        raise HTTPException(status_code=404, detail="No completed analysis found")
    if not (1 <= body.max_queries <= 1000):
        raise HTTPException(status_code=400, detail="max_queries must be in [1, 1000]")
    if not (0.0 <= body.sim_threshold <= 1.0 and 0.0 <= body.orphan_threshold <= 1.0):
        raise HTTPException(status_code=400, detail="thresholds must be in [0, 1]")

    from analysis.query_coverage import run_query_coverage
    from POC_centro_semantico.src.embedding_backends import get_backend

    api_key = _resolve_gemini_key_from_analysis(analysis, db)
    backend = get_backend(api_key=api_key)

    result = run_query_coverage(
        db, job_id, analysis.id, backend,
        max_queries=body.max_queries,
        min_impressions=body.min_impressions,
        sim_threshold=body.sim_threshold,
        buried_min_position=body.buried_min_position,
        orphan_threshold=body.orphan_threshold,
    )
    if result.get("status") == "blocked":
        db.rollback()
        return result

    # Persist the summary on the analysis so GET can rebuild the header
    # without recomputing the matrix.
    analysis.site_metrics = {
        **(analysis.site_metrics or {}),
        "query_coverage": result["summary"],
    }
    db.commit()
    return result


@router.get("/jobs/{job_id}/semantic/query-coverage")
def get_query_coverage(job_id: uuid.UUID, db: Session = Depends(get_session)):
    """T19: cached coverage from ``query_embeddings`` (no embedding cost).
    Blocked-source rule: explicit reasons, never a silent empty list.
    """
    _get_job_or_404(job_id, db)
    analysis = _get_latest_analysis(job_id, db)
    if not analysis or analysis.status != "completed":
        return {"status": "blocked", "reason": "semantic_analysis_not_run"}

    has_queries = (
        db.query(GscQueryData.id).filter(GscQueryData.job_id == job_id).first()
    )
    rows = (
        db.query(QueryEmbedding)
        .filter(QueryEmbedding.job_id == job_id)
        .order_by(QueryEmbedding.impressions.desc())
        .all()
    )
    if not rows:
        return {
            "status": "blocked",
            "reason": "not_run" if has_queries else "no_gsc_query_data",
        }

    chunk_ids = {r.best_chunk_id for r in rows if r.best_chunk_id is not None}
    chunk_info: dict[int, tuple] = {}
    if chunk_ids:
        chunk_info = {
            c[0]: (c[1], c[2], c[3])
            for c in db.query(
                SemanticChunk.id, SemanticChunk.url_id,
                SemanticChunk.position, SemanticChunk.heading_path,
            ).filter(SemanticChunk.id.in_(chunk_ids)).all()
        }
    url_ids = {r.ranking_url_id for r in rows if r.ranking_url_id} | {
        info[0] for info in chunk_info.values()
    }
    url_by_id = dict(
        db.query(Url.id, Url.url).filter(Url.id.in_(url_ids)).all()
    ) if url_ids else {}

    queries = []
    for r in rows:
        info = chunk_info.get(r.best_chunk_id)
        queries.append({
            "query": r.query,
            "clicks": r.clicks,
            "impressions": r.impressions,
            "position": r.position,
            "best_similarity": r.best_similarity,
            "best_chunk_id": r.best_chunk_id,
            "best_chunk_position": info[1] if info else None,
            "best_chunk_heading": info[2] if info else None,
            "covered": r.covered,
            "buried": r.buried,
            "ranking_url": url_by_id.get(r.ranking_url_id),
            "best_chunk_url": url_by_id.get(info[0]) if info else None,
        })

    summary = (analysis.site_metrics or {}).get("query_coverage") or {
        "queries_analyzed": len(queries),
        "covered": sum(1 for q in queries if q["covered"]),
        "coverage_ratio": (
            round(sum(1 for q in queries if q["covered"]) / len(queries), 4)
            if queries else 0.0
        ),
        "gaps": sum(1 for q in queries if q["covered"] is False),
        "buried": sum(1 for q in queries if q["buried"]),
        "chunks_total": None,
        "orphan_chunks": None,
    }
    return {"status": "ok", "summary": summary, "queries": queries}


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------
@router.get("/jobs/{job_id}/semantic/drift")
def get_drift(job_id: uuid.UUID, db: Session = Depends(get_session)):
    analysis = _get_latest_analysis(job_id, db)
    if not analysis or analysis.status != "completed":
        raise HTTPException(status_code=404, detail="No completed analysis found")

    pages = (
        db.query(
            SemanticPage.url_id,
            SemanticPage.distance_to_centroid,
            SemanticPage.weight,
            Url.url,
        )
        .join(Url, Url.id == SemanticPage.url_id)
        .filter(SemanticPage.analysis_id == analysis.id)
        .all()
    )

    import numpy as np
    from POC_centro_semantico.src.analysis import drift_analysis as _drift

    distances = np.array([p.distance_to_centroid for p in pages])
    weights = np.array([p.weight for p in pages])
    url_ids = [p.url_id for p in pages]
    url_map = {p.url_id: p.url for p in pages}

    drift = _drift(distances, weights, url_ids, top_n=10)

    gsc_map = _load_gsc_map(analysis.job_id, db)
    for d in drift:
        d["url"] = url_map.get(d["url_id"], "")
        gsc = gsc_map.get(d["url_id"], {})
        d["clicks"] = gsc.get("clicks")
        d["impressions"] = gsc.get("impressions")
        d["position"] = gsc.get("position")

    return {"drift": drift}


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------
@router.get("/jobs/{job_id}/semantic/export")
def export_semantic_csv(job_id: uuid.UUID, db: Session = Depends(get_session)):
    analysis = _get_latest_analysis(job_id, db)
    if not analysis or analysis.status != "completed":
        raise HTTPException(status_code=404, detail="No completed analysis found")

    pages = (
        db.query(
            SemanticPage.url_id,
            SemanticPage.cluster_id,
            SemanticPage.ring,
            SemanticPage.semantic_role,
            SemanticPage.distance_to_centroid,
            SemanticPage.weight,
            SemanticPage.pr_norm,
            SemanticPage.clicks_norm,
            SemanticPage.x,
            SemanticPage.y,
            Url.url,
        )
        .join(Url, Url.id == SemanticPage.url_id)
        .filter(SemanticPage.analysis_id == analysis.id)
        .order_by(SemanticPage.distance_to_centroid)
        .all()
    )

    gsc_map = _load_gsc_map(job_id, db)

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "URL", "Cluster", "Anillo", "Rol", "Distancia al Centroide",
            "Peso", "PageRank Norm", "Clicks Norm", "UMAP X", "UMAP Y",
            "GSC Clicks", "GSC Impressions", "GSC CTR", "GSC Position",
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        for p in pages:
            gsc = gsc_map.get(p.url_id, {})
            writer.writerow([
                p.url, p.cluster_id, p.ring, p.semantic_role,
                p.distance_to_centroid, p.weight, p.pr_norm, p.clicks_norm,
                p.x, p.y,
                gsc.get("clicks", ""), gsc.get("impressions", ""),
                gsc.get("ctr", ""), gsc.get("position", ""),
            ])
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=semantic_{job_id}.csv"},
    )
