"""Semantic analysis router — GSC integration, embedding analysis, visualization."""
from __future__ import annotations

import csv
import io
import json
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
    GscAccount,
    GscJobData,
    GscQueryData,
    SemanticAnalysis,
    SemanticCannibalization,
    SemanticPage,
)

router = APIRouter(prefix="/api", tags=["semantic"])


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
    model_name: str = "intfloat/multilingual-e5-large-instruct"
    alpha: float = 0.6
    beta: float = 0.4
    cannibal_threshold: float = 0.92


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


def _redis_progress_key(analysis_id: str) -> str:
    return f"semantic:{analysis_id}:progress"


def _set_progress(r: redis.Redis, analysis_id: str, stage: str, pct: int) -> None:
    r.set(
        _redis_progress_key(analysis_id),
        json.dumps({"stage": stage, "progress": pct}),
        ex=3600,  # expire after 1h
    )


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

        df = _fetch(account.credentials_json, body.property_url, days=body.days)
        if df.empty:
            return {"matched": 0, "total_gsc_rows": 0, "query_rows": 0}

        # Match GSC URLs to crawled URLs
        url_map = {
            row.url: row.id
            for row in db.query(Url.id, Url.url).filter(Url.job_id == job_id).all()
        }

        # Delete old GSC data for this job
        db.query(GscJobData).filter(GscJobData.job_id == job_id).delete()
        db.query(GscQueryData).filter(GscQueryData.job_id == job_id).delete()

        matched = 0
        for _, row in df.iterrows():
            url_id = url_map.get(row["url"])
            if url_id:
                db.add(GscJobData(
                    job_id=job_id,
                    url_id=url_id,
                    clicks=int(row["clicks"]),
                    impressions=int(row["impressions"]),
                    ctr=float(row["ctr"]),
                    position=float(row["position"]),
                ))
                matched += 1

        # Fetch query+page data for cannibalization validation
        query_matched = 0
        try:
            df_q = _fetch_queries(
                account.credentials_json, body.property_url, days=body.days,
            )
            if not df_q.empty:
                for _, row in df_q.iterrows():
                    url_id = url_map.get(row["url"])
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
        return {"matched": matched, "total_gsc_rows": len(df), "query_rows": query_matched}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Run semantic analysis
# ---------------------------------------------------------------------------
def _run_analysis_thread(analysis_id: str, job_id: str, config: dict, gsc_data: dict | None):
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

        from POC_centro_semantico.src.engine import SemanticEngine

        engine = SemanticEngine()
        result = engine.process(
            db=db,
            job_id=job_uuid,
            model_name=config.get("model_name", "intfloat/multilingual-e5-large-instruct"),
            alpha=config.get("alpha", 0.6),
            beta=config.get("beta", 0.4),
            cannibal_threshold=config.get("cannibal_threshold", 0.92),
            gsc_data=gsc_data,
            progress_callback=progress_cb,
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

        # Delete old pages/cannibal data for this analysis (safety)
        db.query(SemanticPage).filter(SemanticPage.analysis_id == analysis_uuid).delete()
        db.query(SemanticCannibalization).filter(SemanticCannibalization.analysis_id == analysis_uuid).delete()

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

        # Insert cannibalization pairs
        for pair in result["cannibalization"]:
            db.add(SemanticCannibalization(
                analysis_id=analysis_uuid,
                url_dominant_id=pair["url_dominant_id"],
                url_weak_id=pair["url_weak_id"],
                cosine_similarity=pair["cosine_similarity"],
            ))

        db.commit()

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
        "model_name": body.model_name,
        "alpha": body.alpha,
        "beta": body.beta,
        "cannibal_threshold": body.cannibal_threshold,
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

    # Launch background thread
    t = threading.Thread(
        target=_run_analysis_thread,
        args=(analysis_id, str(job_id), config, gsc_data),
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
    if not analysis:
        return {"status": "none"}

    progress_info = _get_progress(str(analysis.id))

    # Detect stale analysis: running >10 min with no Redis progress → mark failed
    if analysis.status == "running" and not progress_info:
        elapsed = (datetime.now(timezone.utc) - analysis.created_at).total_seconds()
        if elapsed > 600:
            analysis.status = "failed"
            analysis.error_message = "Analysis thread died (no progress for 10 min)"
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
    from POC_centro_semantico.src.embeddings import load_model
    from POC_centro_semantico.src.visualization import build_ring_map

    model_name = (analysis.config or {}).get("model_name", "intfloat/multilingual-e5-large-instruct")
    model = load_model(model_name)

    # Embed target theme
    target_emb = model.encode([body.target_theme], show_progress_bar=False)[0]
    centroid = np.array(analysis.centroid)

    # Alignment: cosine similarity between current centroid and target
    alignment = float(cos_sim([centroid], [target_emb])[0][0])

    # Compute distances from target for all pages
    vectors = np.array([list(p.embedding) for p in pages])
    # Euclidean distance to target
    dists_to_target = np.linalg.norm(vectors - target_emb, axis=1)

    # Reclassify rings based on distance to target
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
    from POC_centro_semantico.src.embeddings import load_model

    centroid = np.array(analysis.centroid)
    vectors = np.array([list(p.embedding) for p in pages])
    url_ids = [p.url_id for p in pages]
    url_list = [p.url for p in pages]
    url_map = {p.url_id: p.url for p in pages}

    model_name = (analysis.config or {}).get("model_name", "intfloat/multilingual-e5-large-instruct")
    model = load_model(model_name)

    result = _gap(centroid, body.topic, vectors, url_ids, url_list, model, top_n=20)

    # Resolve URLs + add GSC data
    gsc_map = _load_gsc_map(job_id, db)
    for c in result["candidates"]:
        c["url"] = url_map.get(c["url_id"], "")
        gsc = gsc_map.get(c["url_id"], {})
        c["clicks"] = gsc.get("clicks")
        c["position"] = gsc.get("position")

    return {"topic": body.topic, "candidates": result["candidates"]}


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
