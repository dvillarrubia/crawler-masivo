"""Client-level segmentation endpoints (T12).

Segments live at CLIENT level (defined once, applied to every crawl of
that client by ``analyzer.assign_segments``). Rules evaluate against the
URL path; first match in priority order (lower number first) wins.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.models import Job, Segment, Url

router = APIRouter(prefix="/api/clients/{client_id}/segments", tags=["segments"])

# Cuantificador anidado: un grupo repetido que a su vez repite → backtracking
# exponencial (ReDoS). Cubre (a+)+, (a*)*, (a+)*, (a{1,3})+, etc.
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*][^)]*\)[+*{]")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SegmentRule(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    rule_type: str = Field("prefix", pattern="^(prefix|regex)$")
    rule: str = Field(..., min_length=1)
    priority: int = Field(100, ge=0, le=10000)
    # T23: business sections get stricter architecture thresholds
    is_business: bool = False

    @field_validator("rule")
    @classmethod
    def validate_rule(cls, v: str, info) -> str:
        return v

    def compiled_matcher(self):
        if self.rule_type == "regex":
            if len(self.rule) > 500:
                raise HTTPException(
                    status_code=422, detail="La regex del segmento es demasiado larga (máx 500).")
            # Corta los ReDoS de libro (cuantificador anidado: (a+)+, (a*)*,
            # (a+)*…) que colgarían el preview sobre una URL adversaria
            # larga. No es exhaustivo — es defensa en profundidad barata.
            if _NESTED_QUANTIFIER.search(self.rule):
                raise HTTPException(
                    status_code=422,
                    detail="Regex potencialmente catastrófica (cuantificador "
                           "anidado tipo (a+)+). Reescríbela sin anidar repeticiones.")
            try:
                return re.compile(self.rule).search
            except re.error as exc:
                raise HTTPException(
                    status_code=422, detail=f"Invalid regex {self.rule!r}: {exc}"
                )
        return lambda path, p=self.rule: path.startswith(p)


class SegmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: str
    name: str
    rule_type: str
    rule: str
    priority: int
    is_business: bool = False
    created_at: datetime | None = None


class SegmentPreviewRequest(BaseModel):
    rules: list[SegmentRule] = Field(..., min_length=1, max_length=100)


class SegmentPreviewEntry(BaseModel):
    name: str
    matched_urls: int
    sample: list[str]


class SegmentPreviewResponse(BaseModel):
    job_id: uuid.UUID | None
    total_urls: int
    unmatched_urls: int
    entries: list[SegmentPreviewEntry]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("", response_model=list[SegmentResponse])
def list_segments(client_id: str, db: Session = Depends(get_session)):
    return (
        db.query(Segment)
        .filter(Segment.client_id == client_id)
        .order_by(Segment.priority, Segment.id)
        .all()
    )


@router.post("", response_model=SegmentResponse, status_code=201)
def create_segment(
    client_id: str, payload: SegmentRule, db: Session = Depends(get_session),
):
    payload.compiled_matcher()  # 422 on bad regex before persisting
    seg = Segment(
        client_id=client_id,
        name=payload.name,
        rule_type=payload.rule_type,
        rule=payload.rule,
        priority=payload.priority,
        is_business=payload.is_business,
    )
    db.add(seg)
    db.commit()
    db.refresh(seg)
    return seg


@router.put("/{segment_id}", response_model=SegmentResponse)
def update_segment(
    client_id: str,
    segment_id: int,
    payload: SegmentRule,
    db: Session = Depends(get_session),
):
    seg = (
        db.query(Segment)
        .filter(Segment.id == segment_id, Segment.client_id == client_id)
        .first()
    )
    if seg is None:
        raise HTTPException(status_code=404, detail="Segment not found")
    payload.compiled_matcher()
    seg.name = payload.name
    seg.rule_type = payload.rule_type
    seg.rule = payload.rule
    seg.priority = payload.priority
    seg.is_business = payload.is_business
    db.commit()
    db.refresh(seg)
    return seg


@router.delete("/{segment_id}", status_code=204)
def delete_segment(
    client_id: str, segment_id: int, db: Session = Depends(get_session),
):
    seg = (
        db.query(Segment)
        .filter(Segment.id == segment_id, Segment.client_id == client_id)
        .first()
    )
    if seg is None:
        raise HTTPException(status_code=404, detail="Segment not found")
    db.delete(seg)
    db.commit()


# ---------------------------------------------------------------------------
# Preview — mandatory sanity check before saving rules
# ---------------------------------------------------------------------------

@router.post("/preview", response_model=SegmentPreviewResponse)
def preview_segments(
    client_id: str,
    payload: SegmentPreviewRequest,
    db: Session = Depends(get_session),
):
    """Evaluate proposed rules against the client's LATEST completed job.

    First-match-wins across the submitted set (ordered by priority), so
    the counts reflect what ``assign_segments`` would actually store —
    the tool that catches rules capturing everything or nothing.
    """
    job = (
        db.query(Job)
        .filter(Job.client_id == client_id, Job.status == "completed")
        .order_by(Job.created_at.desc())
        .first()
    )
    if job is None:
        return SegmentPreviewResponse(
            job_id=None, total_urls=0, unmatched_urls=0,
            entries=[
                SegmentPreviewEntry(name=r.name, matched_urls=0, sample=[])
                for r in payload.rules
            ],
        )

    ordered = sorted(payload.rules, key=lambda r: r.priority)
    matchers = [(r.name, r.compiled_matcher()) for r in ordered]

    counts: dict[str, int] = {r.name: 0 for r in ordered}
    samples: dict[str, list[str]] = {r.name: [] for r in ordered}
    total = 0
    unmatched = 0

    rows = (
        db.query(Url.path, Url.url)
        .filter(
            Url.job_id == job.id,
            Url.is_internal.is_(True),
            Url.is_html.is_(True),
        )
        .yield_per(1000)
    )
    for path, url in rows:
        total += 1
        path = path or "/"
        for name, match in matchers:
            if match(path):
                counts[name] += 1
                if len(samples[name]) < 5:
                    samples[name].append(url)
                break
        else:
            unmatched += 1

    return SegmentPreviewResponse(
        job_id=job.id,
        total_urls=total,
        unmatched_urls=unmatched,
        entries=[
            SegmentPreviewEntry(
                name=r.name, matched_urls=counts[r.name], sample=samples[r.name],
            )
            for r in payload.rules
        ],
    )
