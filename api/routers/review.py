"""Signing endpoints for judgement checks (T10).

Hard rule: nothing auto-accepts. Suggestions and semantic issues are born
``pending`` and only a human moves them — with author and timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.models import Issue, LinkSuggestion

router = APIRouter(prefix="/api", tags=["review"])


class SuggestionDecision(BaseModel):
    status: str = Field(..., pattern="^(accepted|rejected|pending)$")
    decided_by: str = Field(..., min_length=1, max_length=256)


class IssueReview(BaseModel):
    review_status: str = Field(..., pattern="^(signed|rejected|pending)$")
    reviewed_by: str = Field(..., min_length=1, max_length=256)


@router.post("/link-suggestions/{suggestion_id}/decision")
def decide_suggestion(
    suggestion_id: int,
    payload: SuggestionDecision,
    db: Session = Depends(get_session),
):
    s = db.query(LinkSuggestion).filter(LinkSuggestion.id == suggestion_id).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    s.status = payload.status
    s.decided_by = payload.decided_by
    s.decided_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "id": s.id, "status": s.status,
        "decided_by": s.decided_by, "decided_at": s.decided_at,
    }


@router.post("/issues/{issue_id}/review")
def review_issue(
    issue_id: int,
    payload: IssueReview,
    db: Session = Depends(get_session),
):
    """Sign a judgement issue (e.g. ``semantic_cannibalization``).

    Deterministic issues (review_status NULL) are not signable — they are
    facts, not judgements.
    """
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    if issue.review_status is None:
        raise HTTPException(
            status_code=422,
            detail="This issue is deterministic and cannot be signed",
        )
    issue.review_status = payload.review_status
    issue.reviewed_by = payload.reviewed_by
    issue.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "id": issue.id, "review_status": issue.review_status,
        "reviewed_by": issue.reviewed_by, "reviewed_at": issue.reviewed_at,
    }
