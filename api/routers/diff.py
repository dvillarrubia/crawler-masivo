"""Crawl-to-crawl diff and flapping endpoints (T7).

Turns the crawler from a photo into a film: compares two completed jobs
of the same client by ``url_hash``. Everything is computed on-the-fly
(the plan's v1); materialize into a table only if large sites demand it.

Comparability rules:
* both jobs exist, are ``completed`` and share ``client_id``;
* both share the same ``normalization_fingerprint`` (T8) — otherwise the
  hashes mean different things and the endpoint answers 409;
* synthetic ``status_group='not_crawled'`` rows (T2 orphans) are excluded
  from both sides — they were never fetched.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.orm import Session, aliased

from shared.database import get_session
from shared.models import HtmlMeta, Job, Url, UrlSegment

router = APIRouter(prefix="/api/diff", tags=["diff"])

# change key → human field; used by both the summary and /urls
CHANGE_FIELDS = (
    "status", "indexable", "canonical", "title", "depth", "pagerank",
    "content",
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DiffSummary(BaseModel):
    job_a: uuid.UUID
    job_b: uuid.UUID
    new_urls: int
    gone_urls: int
    changes: dict[str, int]
    pagerank_delta_threshold: float


class DiffUrlEntry(BaseModel):
    url: str
    change: str
    old_value: Any = None
    new_value: Any = None


class FlappingEntry(BaseModel):
    url: str
    field: str  # status_group | indexable
    sequence: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_comparable_jobs(
    job_a: uuid.UUID, job_b: uuid.UUID, db: Session,
) -> tuple[Job, Job]:
    a = db.query(Job).filter(Job.id == job_a).first()
    b = db.query(Job).filter(Job.id == job_b).first()
    if a is None or b is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if a.status != "completed" or b.status != "completed":
        raise HTTPException(
            status_code=422, detail="Both jobs must be completed"
        )
    if not a.client_id or a.client_id != b.client_id:
        raise HTTPException(
            status_code=422, detail="Jobs must share the same client_id"
        )
    fp_a = getattr(a, "normalization_fingerprint", None)
    fp_b = getattr(b, "normalization_fingerprint", None)
    if fp_a != fp_b:
        raise HTTPException(
            status_code=409,
            detail=(
                "Jobs are not comparable: different URL-normalization "
                f"fingerprints ({fp_a!r} vs {fp_b!r}). url_hash values do "
                "not mean the same thing in both crawls (T8)."
            ),
        )
    return a, b


def _crawled(url_cls):
    return (url_cls.status_group.is_(None)) | (url_cls.status_group != "not_crawled")


def _segment_hashes(
    db: Session, job_id, segment_id: int | None,
) -> set[str] | None:
    """url_hash set of a segment (assignments of that job), or None."""
    if segment_id is None:
        return None
    rows = db.execute(
        select(Url.url_hash)
        .join(UrlSegment, UrlSegment.url_id == Url.id)
        .where(
            UrlSegment.job_id == job_id,
            UrlSegment.segment_id == segment_id,
        )
    ).all()
    return {r[0] for r in rows}


def _side(db: Session, job_id) -> dict[str, dict[str, Any]]:
    """Load one job's comparable state keyed by url_hash."""
    rows = db.execute(
        select(
            Url.url_hash, Url.url, Url.status_group, Url.indexable,
            Url.crawl_depth, Url.pagerank, Url.body_hash,
            HtmlMeta.canonical_href, HtmlMeta.title,
        )
        .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
        .where(Url.job_id == job_id, _crawled(Url))
    ).all()
    return {
        r.url_hash: {
            "url": r.url,
            "status": r.status_group,
            "indexable": r.indexable,
            "depth": r.crawl_depth,
            "pagerank": r.pagerank,
            "content": r.body_hash,
            "canonical": r.canonical_href,
            "title": r.title,
        }
        for r in rows
    }


def _field_changed(field: str, old: Any, new: Any, pr_delta: float) -> bool:
    if field == "pagerank":
        if old is None and new is None:
            return False
        if old is None or new is None:
            return True
        return abs(old - new) > pr_delta
    return old != new


# ---------------------------------------------------------------------------
# GET /api/diff — summary
# ---------------------------------------------------------------------------

@router.get("", response_model=DiffSummary)
def diff_summary(
    job_a: uuid.UUID,
    job_b: uuid.UUID,
    pagerank_delta: float = Query(0.5, ge=0.0, le=10.0),
    segment_id: int | None = Query(None),
    db: Session = Depends(get_session),
):
    """Counts of new/gone URLs and per-field changes between two crawls.

    ``segment_id`` (T12) restricts the diff to the URLs assigned to that
    segment in EITHER job.
    """
    _load_comparable_jobs(job_a, job_b, db)

    side_a = _side(db, job_a)
    side_b = _side(db, job_b)

    if segment_id is not None:
        seg = (_segment_hashes(db, job_a, segment_id) or set()) | (
            _segment_hashes(db, job_b, segment_id) or set()
        )
        side_a = {h: v for h, v in side_a.items() if h in seg}
        side_b = {h: v for h, v in side_b.items() if h in seg}

    new_urls = len(side_b.keys() - side_a.keys())
    gone_urls = len(side_a.keys() - side_b.keys())

    changes = {f: 0 for f in CHANGE_FIELDS}
    for h in side_a.keys() & side_b.keys():
        old, new = side_a[h], side_b[h]
        for field in CHANGE_FIELDS:
            if _field_changed(field, old[field], new[field], pagerank_delta):
                changes[field] += 1

    return DiffSummary(
        job_a=job_a,
        job_b=job_b,
        new_urls=new_urls,
        gone_urls=gone_urls,
        changes=changes,
        pagerank_delta_threshold=pagerank_delta,
    )


# ---------------------------------------------------------------------------
# GET /api/diff/urls — per-URL detail for one change type
# ---------------------------------------------------------------------------

@router.get("/urls")
def diff_urls(
    job_a: uuid.UUID,
    job_b: uuid.UUID,
    change: str = Query(..., pattern="^(status|indexable|canonical|title|depth|pagerank|content|new|gone)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    pagerank_delta: float = Query(0.5, ge=0.0, le=10.0),
    segment_id: int | None = Query(None),
    db: Session = Depends(get_session),
):
    _load_comparable_jobs(job_a, job_b, db)

    side_a = _side(db, job_a)
    side_b = _side(db, job_b)

    if segment_id is not None:
        seg = (_segment_hashes(db, job_a, segment_id) or set()) | (
            _segment_hashes(db, job_b, segment_id) or set()
        )
        side_a = {h: v for h, v in side_a.items() if h in seg}
        side_b = {h: v for h, v in side_b.items() if h in seg}

    entries: list[DiffUrlEntry] = []
    if change == "new":
        for h in side_b.keys() - side_a.keys():
            entries.append(DiffUrlEntry(
                url=side_b[h]["url"], change="new",
                new_value=side_b[h]["status"],
            ))
    elif change == "gone":
        for h in side_a.keys() - side_b.keys():
            entries.append(DiffUrlEntry(
                url=side_a[h]["url"], change="gone",
                old_value=side_a[h]["status"],
            ))
    else:
        for h in side_a.keys() & side_b.keys():
            old, new = side_a[h][change], side_b[h][change]
            if _field_changed(change, old, new, pagerank_delta):
                entries.append(DiffUrlEntry(
                    url=side_b[h]["url"], change=change,
                    old_value=old, new_value=new,
                ))

    entries.sort(key=lambda e: e.url)
    total = len(entries)
    start = (page - 1) * page_size
    return {
        "items": entries[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
    }


# ---------------------------------------------------------------------------
# GET /api/diff/flapping — values alternating across the last N jobs
# ---------------------------------------------------------------------------

@router.get("/flapping", response_model=list[FlappingEntry])
def diff_flapping(
    client_id: str,
    last_n: int = Query(4, ge=3, le=12),
    db: Session = Depends(get_session),
):
    """URLs whose ``status_group`` or ``indexable`` alternates (A→B→A)
    across the client's last N completed crawls. Returns the sequence.
    """
    jobs = (
        db.query(Job)
        .filter(Job.client_id == client_id, Job.status == "completed")
        .order_by(Job.created_at.desc())
        .limit(last_n)
        .all()
    )
    if len(jobs) < 3:
        return []
    jobs = list(reversed(jobs))  # chronological

    # Comparability: only jobs sharing the newest job's fingerprint.
    ref_fp = getattr(jobs[-1], "normalization_fingerprint", None)
    jobs = [
        j for j in jobs
        if getattr(j, "normalization_fingerprint", None) == ref_fp
    ]
    if len(jobs) < 3:
        return []

    # url_hash → [(job_id, status, indexable, url)] in chronological order
    history: dict[str, list[tuple]] = {}
    for j in jobs:
        rows = db.execute(
            select(Url.url_hash, Url.url, Url.status_group, Url.indexable)
            .where(Url.job_id == j.id, _crawled(Url))
        ).all()
        for h, url, status, indexable in rows:
            history.setdefault(h, []).append((j.id, url, status, indexable))

    def _flaps(values: list[Any]) -> bool:
        # compress consecutive duplicates; flapping = it changes AND
        # returns to a previously seen value (A→B→A)
        compressed: list[Any] = []
        for v in values:
            if not compressed or compressed[-1] != v:
                compressed.append(v)
        return len(compressed) >= 3 and len(set(compressed)) < len(compressed)

    results: list[FlappingEntry] = []
    for h, seq in history.items():
        if len(seq) < 3:
            continue
        url = seq[-1][1]
        statuses = [s for (_, _, s, _) in seq]
        indexables = [i for (_, _, _, i) in seq]
        if _flaps(statuses):
            results.append(FlappingEntry(
                url=url, field="status_group",
                sequence=[
                    {"job_id": str(jid), "value": s} for (jid, _, s, _) in seq
                ],
            ))
        if _flaps(indexables):
            results.append(FlappingEntry(
                url=url, field="indexable",
                sequence=[
                    {"job_id": str(jid), "value": i} for (jid, _, _, i) in seq
                ],
            ))

    results.sort(key=lambda e: (e.url, e.field))
    return results
