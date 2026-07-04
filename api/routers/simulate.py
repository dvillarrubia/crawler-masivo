"""What-if PageRank simulator (T21) + semantic delta (T18).

The simulator loads the job's graph, applies proposed link mutations in
memory, recomputes PageRank v2 (shared power iteration, sparse above the
threshold) and returns per-page deltas — writing NOTHING to the DB. Pure,
idempotent, no side effects.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.analyzer import SEOAnalyzer, run_power_iteration
from shared.database import get_session
from shared.models import Job, Link, Url, UrlSegment

router = APIRouter(prefix="/api/jobs/{job_id}", tags=["simulate"])

MAX_MUTATIONS = 500


class AddLink(BaseModel):
    from_hash: str = Field(..., min_length=64, max_length=64)
    to_hash: str = Field(..., min_length=64, max_length=64)
    position: str = Field("content")


class SimulateRequest(BaseModel):
    add: list[AddLink] = Field(default_factory=list)
    remove: list[int] = Field(default_factory=list)  # link ids
    top_n: int = Field(default=50, ge=1, le=500)


def _load_graph(db: Session, job_id):
    """Load the v2 graph (indexable nodes, position-weighted edges)."""
    rows = db.execute(
        select(Url.id, Url.url, Url.url_hash, Url.status_code, Url.indexable)
        .where(
            Url.job_id == job_id,
            Url.is_internal.is_(True),
            (Url.status_group.is_(None)) | (Url.status_group != "not_crawled"),
        )
    ).all()
    nodes = [
        r for r in rows
        if r.status_code is not None and 200 <= r.status_code < 300
        and r.indexable is not False
    ]
    idx_by_hash = {r.url_hash: i for i, r in enumerate(nodes)}
    id_to_idx = {r.id: i for i, r in enumerate(nodes)}

    links = db.execute(
        select(Link.id, Link.from_url_id, Link.to_url_hash,
               Link.link_position, Link.follow)
        .where(Link.job_id == job_id, Link.is_internal.is_(True))
    ).all()
    return nodes, idx_by_hash, id_to_idx, links


def _pagerank(nodes, idx_by_hash, id_to_idx, edges) -> list[float]:
    """edges: iterable of (src_idx, dst_hash, position, follow)."""
    weights = SEOAnalyzer._POSITION_WEIGHT_V2
    n = len(nodes)
    if n == 0:
        return []

    edge_weight: dict[tuple[int, int], float] = {}
    out_total: dict[int, float] = defaultdict(float)
    for src, dst_hash, position, follow in edges:
        w = weights.get(position, 0.5)
        out_total[src] += w
        if follow is False:
            continue
        dst = idx_by_hash.get(dst_hash)
        if dst is None or dst == src:
            if dst == src:
                out_total[src] -= w
            continue
        key = (src, dst)
        if key not in edge_weight or w > edge_weight[key]:
            edge_weight[key] = w

    pr = run_power_iteration(n, edge_weight, out_total, 0.85, 100, 1e-6)
    max_pr = max(pr) if pr else 1.0
    return [p / max_pr * 10.0 for p in pr] if max_pr > 0 else pr


@router.post("/pagerank-simulate")
def simulate_pagerank(
    job_id: uuid.UUID,
    payload: SimulateRequest,
    db: Session = Depends(get_session),
):
    """T21: simulate link mutations and return PageRank deltas.

    No DB writes: baseline and mutated graph are computed in memory with
    the same v2 semantics, so both sides are directly comparable.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if len(payload.add) + len(payload.remove) > MAX_MUTATIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Too many mutations (max {MAX_MUTATIONS})",
        )

    nodes, idx_by_hash, id_to_idx, links = _load_graph(db, job_id)
    if not nodes:
        return {"status": "blocked", "reason": "no_indexable_pages"}

    def _edges(with_mutations: bool):
        removed = set(payload.remove) if with_mutations else set()
        for l in links:
            if l.id in removed:
                continue
            src = id_to_idx.get(l.from_url_id)
            if src is None:
                continue
            yield (src, l.to_url_hash, l.link_position, l.follow)
        if with_mutations:
            for a in payload.add:
                src = idx_by_hash.get(a.from_hash)
                if src is None:
                    continue
                yield (src, a.to_hash, a.position, True)

    baseline = _pagerank(nodes, idx_by_hash, id_to_idx, _edges(False))
    mutated = _pagerank(nodes, idx_by_hash, id_to_idx, _edges(True))

    deltas = []
    for i, node in enumerate(nodes):
        d = mutated[i] - baseline[i]
        if abs(d) > 1e-6:
            deltas.append({
                "url": node.url,
                "url_hash": node.url_hash,
                "pagerank_before": round(baseline[i], 4),
                "pagerank_after": round(mutated[i], 4),
                "delta": round(d, 4),
            })
    deltas.sort(key=lambda x: abs(x["delta"]), reverse=True)

    return {
        "status": "ok",
        "mutations": {"added": len(payload.add), "removed": len(payload.remove)},
        "pages_affected": len(deltas),
        "top_deltas": deltas[:payload.top_n],
    }


# ---------------------------------------------------------------------------
# T18: structural vs semantic PageRank delta
# ---------------------------------------------------------------------------
@router.get("/pagerank-delta")
def pagerank_delta(
    job_id: uuid.UUID,
    segment_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
):
    """T18: pages ordered by |pagerank − pagerank_semantic|.

    Positive delta = held up by boilerplate links without contextual
    backing (fragile); negative = strong contextual linking with little
    volume. Blocked when the semantic PageRank was never computed.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    base = db.query(Url).filter(
        Url.job_id == job_id,
        Url.pagerank.isnot(None),
        Url.pagerank_semantic.isnot(None),
    )
    if base.first() is None:
        return {"status": "blocked", "reason": "semantic_pagerank_not_computed"}

    if segment_id is not None:
        seg_urls = select(UrlSegment.url_id).where(
            UrlSegment.job_id == job_id,
            UrlSegment.segment_id == segment_id,
        )
        base = base.filter(Url.id.in_(seg_urls))

    rows = base.all()
    items = [
        {
            "url": u.url,
            "pagerank": u.pagerank,
            "pagerank_semantic": u.pagerank_semantic,
            "delta": round(u.pagerank - u.pagerank_semantic, 4),
        }
        for u in rows
    ]
    items.sort(key=lambda x: abs(x["delta"]), reverse=True)

    total = len(items)
    start = (page - 1) * page_size
    import math

    return {
        "status": "ok",
        "items": items[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
    }
