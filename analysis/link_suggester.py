"""
Semantic internal-linking suggestions (T10).

Pure core (:func:`suggest_links`) + DB wrapper (:func:`generate_for_job`).
The core is vector-agnostic (any embedding space, injected as plain
lists/arrays) so it is unit-testable without Gemini or pgvector; the
wrapper reads the job's ``SemanticPage`` embeddings (Gemini 1024d — the
only runtime backend) and persists ``link_suggestions`` rows.

Hard rule: nothing auto-accepts. Every suggestion is born ``pending``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_SIMILARITY_THRESHOLD = 0.75
DEFAULT_TOP_K = 5


@dataclass(frozen=True)
class PageVector:
    """One page as the suggester sees it."""

    url_hash: str
    url: str
    vector: tuple  # embedding, any dimension
    pagerank: float | None
    indexable: bool | None
    status_code: int | None
    word_count: int | None


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def suggest_links(
    pages: list[PageVector],
    existing_links: set[tuple[str, str]],
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    top_k: int = DEFAULT_TOP_K,
    min_target_word_count: int = 200,
) -> list[dict]:
    """Compute linking suggestions.

    Targets = pages with pagerank below the median and enough content.
    Candidates per target = similar pages (cosine ≥ threshold) that do
    NOT already link to it, excluding noindex/non-2xx, top-K ordered by
    ``similarity × normalized pagerank``.
    """
    usable = [
        p for p in pages
        if p.vector
        and p.status_code is not None and 200 <= p.status_code < 300
        and p.indexable is not False
    ]
    if len(usable) < 2:
        return []

    ranked = sorted(
        (p.pagerank for p in usable if p.pagerank is not None)
    )
    median_pr = ranked[len(ranked) // 2] if ranked else None
    max_pr = ranked[-1] if ranked else None

    targets = [
        p for p in usable
        if (median_pr is None or (p.pagerank or 0) < median_pr)
        and (p.word_count or 0) >= min_target_word_count
    ]

    suggestions: list[dict] = []
    for target in targets:
        candidates = []
        for source in usable:
            if source.url_hash == target.url_hash:
                continue
            if (source.url_hash, target.url_hash) in existing_links:
                continue
            sim = _cosine(source.vector, target.vector)
            if sim < similarity_threshold:
                continue
            pr_norm = (
                (source.pagerank or 0) / max_pr if max_pr else 0.0
            )
            candidates.append({
                "target_url_hash": target.url_hash,
                "target_url": target.url,
                "source_url_hash": source.url_hash,
                "source_url": source.url,
                "cosine_similarity": round(sim, 4),
                "source_pagerank": source.pagerank,
                "score": round(sim * pr_norm, 4),
            })
        candidates.sort(key=lambda c: c["score"], reverse=True)
        suggestions.extend(candidates[:top_k])

    return suggestions


def generate_for_job(
    session,
    job_id,
    analysis_id,
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    top_k: int = DEFAULT_TOP_K,
) -> int:
    """Read the job's semantic page vectors and persist suggestions.

    Replaces the job's previous PENDING suggestions (decisions are never
    touched). Returns the number of suggestions written.
    """
    from shared.models import Link, LinkSuggestion, Url
    from shared.semantic_models import SemanticPage

    rows = session.query(
        Url.url_hash, Url.url, SemanticPage.embedding, Url.pagerank,
        Url.indexable, Url.status_code, Url.word_count,
    ).join(SemanticPage, SemanticPage.url_id == Url.id).filter(
        SemanticPage.analysis_id == analysis_id,
        SemanticPage.embedding.isnot(None),
    ).all()

    pages = [
        PageVector(
            url_hash=r[0], url=r[1], vector=tuple(r[2]) if r[2] is not None else (),
            pagerank=r[3], indexable=r[4], status_code=r[5], word_count=r[6],
        )
        for r in rows
    ]

    link_rows = session.query(Url.url_hash, Link.to_url_hash).join(
        Link, Link.from_url_id == Url.id,
    ).filter(Link.job_id == job_id, Link.is_internal.is_(True)).all()
    existing = {(src, dst) for src, dst in link_rows}

    suggestions = suggest_links(
        pages, existing,
        similarity_threshold=similarity_threshold, top_k=top_k,
    )

    session.query(LinkSuggestion).filter(
        LinkSuggestion.job_id == job_id,
        LinkSuggestion.status == "pending",
    ).delete()

    for s in suggestions:
        session.add(LinkSuggestion(job_id=job_id, **s))
    session.flush()
    logger.info(
        "Link suggestions for job %s: %d written", job_id, len(suggestions)
    )
    return len(suggestions)


def emit_cannibalization_issues(session, job_id, analysis_id) -> int:
    """T10: dump the analysis' cannibalization pairs into ``issues`` as
    signable ``semantic_cannibalization`` (warning, review_status
    'pending'). Existing pending ones for the job are replaced; signed or
    rejected decisions survive.
    """
    from shared.models import Issue, Url
    from shared.semantic_models import SemanticCannibalization

    pairs = session.query(SemanticCannibalization).filter(
        SemanticCannibalization.analysis_id == analysis_id,
    ).all()
    if not pairs:
        return 0

    session.query(Issue).filter(
        Issue.job_id == job_id,
        Issue.issue_type == "semantic_cannibalization",
        Issue.review_status == "pending",
    ).delete()

    url_ids = {p.url_dominant_id for p in pairs} | {p.url_weak_id for p in pairs}
    urls = dict(
        session.query(Url.id, Url.url).filter(Url.id.in_(url_ids)).all()
    )

    count = 0
    for p in pairs:
        session.add(Issue(
            job_id=job_id,
            url_id=p.url_weak_id,   # the weak page is the actionable one
            issue_type="semantic_cannibalization",
            severity="warning",
            review_status="pending",
            details={
                "dominant_url": urls.get(p.url_dominant_id),
                "weak_url": urls.get(p.url_weak_id),
                "cosine_similarity": p.cosine_similarity,
            },
        ))
        count += 1
    session.flush()
    return count
