"""Result endpoints: urls, issues, links, stats, CSV export for a job."""

from __future__ import annotations

import csv
import io
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, joinedload, subqueryload

from api.backup import stream_backup_zip

from shared.database import get_session
from shared.models import (
    Job, Url, Issue, Link, HtmlMeta, PageContent,
    Heading, Hreflang, StructuredData, Resource, SecurityHeaders,
)

from api.schemas import (
    CategoryInsight,
    HeadingResponse,
    HreflangResponse,
    HostCount,
    InsightsResponse,
    IssueResponse,
    IssueTypeCount,
    JobStats,
    LinkResponse,
    PaginatedResponse,
    Recommendation,
    ResourceResponse,
    ResourceTypeCount,
    SecurityHeadersResponse,
    StatusGroupCount,
    StructuredDataResponse,
    UrlDetailResponse,
    UrlResponse,
)

router = APIRouter(prefix="/api/jobs/{job_id}", tags=["results"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_job_or_404(job_id: uuid.UUID, db: Session) -> Job:
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _paginate(
    items: list[Any],
    total: int,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
    }


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/urls
# ---------------------------------------------------------------------------
SORT_COLUMNS = {
    "url": Url.url,
    "status_code": Url.status_code,
    "word_count": Url.word_count,
    "response_time_ms": Url.response_time_ms,
    "content_length": Url.content_length,
    "crawl_depth": Url.crawl_depth,
    "inlinks_count": Url.inlinks_count,
    "outlinks_count": Url.outlinks_count,
    "pagerank": Url.pagerank,
    "url_length": Url.url_length,
    "text_ratio": Url.text_ratio,
}


@router.get("/urls", response_model=PaginatedResponse[UrlResponse])
def list_urls(
    job_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status_group: str | None = Query(None),
    is_internal: bool | None = Query(None),
    resource_type: str | None = Query(None),
    search: str | None = Query(None),
    sort_by: str | None = Query(None),
    sort_dir: str = Query("asc", pattern="^(asc|desc)$"),
    indexable: bool | None = Query(None),
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    q = db.query(Url).filter(Url.job_id == job_id)

    if status_group is not None:
        q = q.filter(Url.status_group == status_group)
    if is_internal is not None:
        q = q.filter(Url.is_internal == is_internal)
    if resource_type is not None:
        q = q.filter(Url.resource_type == resource_type)
    if indexable is not None:
        q = q.filter(Url.indexable == indexable)

    # Search across url and title
    if search:
        pattern = f"%{search}%"
        q = q.outerjoin(HtmlMeta, Url.id == HtmlMeta.url_id).filter(
            or_(Url.url.ilike(pattern), HtmlMeta.title.ilike(pattern))
        )

    total = q.count()

    # Sorting
    if sort_by and sort_by in SORT_COLUMNS:
        col = SORT_COLUMNS[sort_by]
        if sort_dir == "desc":
            q = q.order_by(col.desc().nullslast())
        else:
            q = q.order_by(col.asc().nullsfirst())
    else:
        q = q.order_by(Url.id)

    rows = (
        q.options(joinedload(Url.html_meta), joinedload(Url.page_content))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return _paginate(
        items=[UrlResponse.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/urls/{url_id}  -- full detail for a single URL
# ---------------------------------------------------------------------------
@router.get("/urls/{url_id}", response_model=UrlDetailResponse)
def get_url_detail(
    job_id: uuid.UUID,
    url_id: int,
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    url_obj = (
        db.query(Url)
        .options(
            joinedload(Url.html_meta),
            joinedload(Url.page_content),
            joinedload(Url.security),
            subqueryload(Url.headings),
            subqueryload(Url.hreflangs),
            subqueryload(Url.structured_data),
            subqueryload(Url.resources),
            subqueryload(Url.issues),
        )
        .filter(Url.id == url_id, Url.job_id == job_id)
        .first()
    )
    if url_obj is None:
        raise HTTPException(status_code=404, detail="URL not found")

    # Build outlinks using a join to get from_url string directly
    from sqlalchemy.orm import aliased
    UrlAlias = aliased(Url)

    outlink_rows = (
        db.query(Link, UrlAlias.url.label("src_url"))
        .outerjoin(UrlAlias, Link.from_url_id == UrlAlias.id)
        .filter(Link.from_url_id == url_id)
        .order_by(Link.id)
        .limit(200)
        .all()
    )

    # Build inlinks using to_url_hash match
    inlink_rows = (
        db.query(Link, UrlAlias.url.label("src_url"))
        .outerjoin(UrlAlias, Link.from_url_id == UrlAlias.id)
        .filter(Link.job_id == job_id, Link.to_url_hash == url_obj.url_hash)
        .order_by(Link.id)
        .limit(200)
        .all()
    )

    # Serialize
    resp = UrlDetailResponse.model_validate(url_obj)
    resp.headings = [HeadingResponse.model_validate(h) for h in url_obj.headings]
    resp.resources = [ResourceResponse.model_validate(r) for r in url_obj.resources]
    resp.hreflangs = [HreflangResponse.model_validate(h) for h in url_obj.hreflangs]
    resp.structured_data = [StructuredDataResponse.model_validate(s) for s in url_obj.structured_data]
    resp.security = SecurityHeadersResponse.model_validate(url_obj.security) if url_obj.security else None
    resp.issues = [IssueResponse.model_validate(i) for i in url_obj.issues]

    out_items = []
    for link_obj, src_url in outlink_rows:
        item = LinkResponse.model_validate(link_obj)
        item.from_url = src_url
        out_items.append(item)
    resp.outlinks = out_items

    in_items = []
    for link_obj, src_url in inlink_rows:
        item = LinkResponse.model_validate(link_obj)
        item.from_url = src_url
        in_items.append(item)
    resp.inlinks = in_items

    return resp


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/issues
# ---------------------------------------------------------------------------
@router.get("/issues", response_model=PaginatedResponse[IssueResponse])
def list_issues(
    job_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    severity: str | None = Query(None),
    issue_type: str | None = Query(None),
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    q = db.query(Issue).filter(Issue.job_id == job_id)

    if severity is not None:
        q = q.filter(Issue.severity == severity)
    if issue_type is not None:
        q = q.filter(Issue.issue_type == issue_type)

    total = q.count()

    rows = (
        q.options(joinedload(Issue.url_rel))
        .order_by(Issue.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for r in rows:
        resp = IssueResponse.model_validate(r)
        if r.url_rel:
            resp.url = r.url_rel.url
        items.append(resp)

    return _paginate(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/issues/urls — URLs affected by specific issue types
# ---------------------------------------------------------------------------
class _AffectedUrl(BaseModel):
    id: int
    url: str
    status_code: int | None = None


@router.get("/issues/urls", response_model=list[_AffectedUrl])
def list_issue_urls(
    job_id: uuid.UUID,
    issue_type: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
):
    """Return distinct URLs affected by a given issue type."""
    _get_job_or_404(job_id, db)

    rows = (
        db.query(Url.id, Url.url, Url.status_code)
        .join(Issue, Issue.url_id == Url.id)
        .filter(Issue.job_id == job_id, Issue.issue_type == issue_type)
        .distinct()
        .order_by(Url.id)
        .limit(limit)
        .all()
    )
    return [_AffectedUrl(id=r.id, url=r.url, status_code=r.status_code) for r in rows]


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/links
# ---------------------------------------------------------------------------
@router.get("/links", response_model=PaginatedResponse[LinkResponse])
def list_links(
    job_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    q = db.query(Link).filter(Link.job_id == job_id)

    total = q.count()

    rows = (
        q.options(joinedload(Link.from_url_rel))
        .order_by(Link.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for r in rows:
        resp = LinkResponse.model_validate(r)
        if r.from_url_rel:
            resp.from_url = r.from_url_rel.url
        items.append(resp)

    return _paginate(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/stats
# ---------------------------------------------------------------------------
@router.get("/stats", response_model=JobStats)
def get_stats(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    job = _get_job_or_404(job_id, db)

    # Total URL count for this job
    total_urls = db.query(func.count(Url.id)).filter(Url.job_id == job_id).scalar() or 0

    # URLs by status group
    status_rows = (
        db.query(Url.status_group, func.count(Url.id))
        .filter(Url.job_id == job_id, Url.status_group.isnot(None))
        .group_by(Url.status_group)
        .all()
    )
    urls_by_status = [
        StatusGroupCount(status_group=sg, count=c) for sg, c in status_rows
    ]

    # Issues by type + severity
    issue_rows = (
        db.query(Issue.issue_type, Issue.severity, func.count(Issue.id))
        .filter(Issue.job_id == job_id)
        .group_by(Issue.issue_type, Issue.severity)
        .all()
    )
    issues_by_type = [
        IssueTypeCount(issue_type=it, severity=sev, count=c)
        for it, sev, c in issue_rows
    ]

    # Top 20 hosts
    host_rows = (
        db.query(Url.host, func.count(Url.id))
        .filter(Url.job_id == job_id, Url.host.isnot(None))
        .group_by(Url.host)
        .order_by(func.count(Url.id).desc())
        .limit(20)
        .all()
    )
    top_hosts = [HostCount(host=h, count=c) for h, c in host_rows]

    # URLs by resource_type
    rt_rows = (
        db.query(Url.resource_type, func.count(Url.id))
        .filter(Url.job_id == job_id, Url.resource_type.isnot(None))
        .group_by(Url.resource_type)
        .all()
    )
    urls_by_rt = [
        ResourceTypeCount(resource_type=rt, count=c) for rt, c in rt_rows
    ]

    # Internal / external counts
    internal_count = db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True,
    ).scalar() or 0
    external_count = total_urls - internal_count

    return JobStats(
        job_id=job.id,
        total_urls=total_urls,
        total_urls_crawled=job.total_urls_crawled,
        total_urls_failed=job.total_urls_failed,
        urls_by_status_group=urls_by_status,
        issues_by_type=issues_by_type,
        top_hosts=top_hosts,
        urls_by_resource_type=urls_by_rt,
        internal_count=internal_count,
        external_count=external_count,
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/export  --  stream CSV of all URLs + metadata
# ---------------------------------------------------------------------------
CSV_COLUMNS = [
    "url",
    "host",
    "path",
    "scheme",
    "is_internal",
    "crawl_depth",
    "status_code",
    "status_group",
    "status_text",
    "content_type",
    "content_length",
    "transfer_size",
    "response_time_ms",
    "is_html",
    "resource_type",
    "redirect_url",
    "redirect_type",
    "indexable",
    "indexability_status",
    "url_length",
    "folder_depth",
    "word_count",
    "text_ratio",
    "last_modified",
    "http_version",
    "inlinks_count",
    "unique_inlinks_count",
    "outlinks_count",
    "external_outlinks_count",
    "pagerank",
    "title",
    "title_len",
    "title_pixel_width",
    "meta_description",
    "meta_description_len",
    "meta_description_pixel_width",
    "meta_robots",
    "canonical_href",
    "meta_refresh",
    "has_meta_outside_head",
    "content_text",
    "content_length",
]


def _val(v) -> str:
    """Format a value for CSV output."""
    if v is None:
        return ""
    return str(v)


def _csv_row(url_obj: Url) -> list[str]:
    """Build a flat CSV row from a Url + optional HtmlMeta + PageContent."""
    meta: HtmlMeta | None = url_obj.html_meta
    pc: PageContent | None = url_obj.page_content

    # Truncate content_text to 500 chars for CSV
    content_text_val = ""
    if pc and pc.content_text:
        content_text_val = pc.content_text[:500]

    return [
        _val(url_obj.url),
        _val(url_obj.host),
        _val(url_obj.path),
        _val(url_obj.scheme),
        _val(url_obj.is_internal),
        _val(url_obj.crawl_depth),
        _val(url_obj.status_code),
        _val(url_obj.status_group),
        _val(url_obj.status_text),
        _val(url_obj.content_type),
        _val(url_obj.content_length),
        _val(url_obj.transfer_size),
        _val(url_obj.response_time_ms),
        _val(url_obj.is_html),
        _val(url_obj.resource_type),
        _val(url_obj.redirect_url),
        _val(url_obj.redirect_type),
        _val(url_obj.indexable),
        _val(url_obj.indexability_status),
        _val(url_obj.url_length),
        _val(url_obj.folder_depth),
        _val(url_obj.word_count),
        _val(url_obj.text_ratio),
        _val(url_obj.last_modified),
        _val(url_obj.http_version),
        _val(url_obj.inlinks_count),
        _val(url_obj.unique_inlinks_count),
        _val(url_obj.outlinks_count),
        _val(url_obj.external_outlinks_count),
        _val(url_obj.pagerank),
        # HtmlMeta fields
        _val(meta.title) if meta else "",
        _val(meta.title_len) if meta else "",
        _val(meta.title_pixel_width) if meta else "",
        _val(meta.meta_description) if meta else "",
        _val(meta.meta_description_len) if meta else "",
        _val(meta.meta_description_pixel_width) if meta else "",
        _val(meta.meta_robots) if meta else "",
        _val(meta.canonical_href) if meta else "",
        _val(meta.meta_refresh) if meta else "",
        _val(meta.has_meta_outside_head) if meta else "",
        # PageContent fields
        content_text_val,
        _val(pc.content_length) if pc else "",
    ]


def _stream_csv(job_id: uuid.UUID):
    """Generator that yields CSV content in chunks using windowed queries."""
    # Each chunk opens its own session so we do not hold one long transaction.
    from shared.database import SessionLocal

    batch_size = 1000
    last_id = 0

    # Header row
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    yield buf.getvalue()

    while True:
        session = SessionLocal()
        try:
            rows = (
                session.query(Url)
                .options(joinedload(Url.html_meta), joinedload(Url.page_content))
                .filter(Url.job_id == job_id, Url.id > last_id)
                .order_by(Url.id)
                .limit(batch_size)
                .all()
            )

            if not rows:
                break

            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in rows:
                writer.writerow(_csv_row(row))
                last_id = row.id

            yield buf.getvalue()
        finally:
            session.close()


@router.get("/export")
def export_csv(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    return StreamingResponse(
        _stream_csv(job_id),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=job_{job_id}_urls.csv",
        },
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/backup  --  full backup as ZIP with NDJSON
# ---------------------------------------------------------------------------
@router.get("/backup")
def export_backup(
    job_id: uuid.UUID,
    include_content: bool = Query(True),
    db: Session = Depends(get_session),
):
    job = _get_job_or_404(job_id, db)

    if job.status in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail="No se puede exportar un job que aun esta en ejecucion",
        )

    return StreamingResponse(
        stream_backup_zip(job_id, include_content),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="backup_{job_id}.zip"',
        },
    )


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/insights  --  SEO insights report
# ---------------------------------------------------------------------------
def _safe_pct(numerator: int, denominator: int) -> float:
    """Return percentage (0-100) or 0 if denominator is 0."""
    return round(numerator / denominator * 100, 1) if denominator else 0.0


def _clamp_score(val: float) -> int:
    return max(0, min(100, round(val)))


def _calc_crawlability(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Crawlability: status codes, indexability, redirects."""
    total = db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True,
    ).scalar() or 0

    status_counts: dict[str, int] = {}
    rows = (
        db.query(Url.status_group, func.count(Url.id))
        .filter(Url.job_id == job_id, Url.is_internal == True)
        .group_by(Url.status_group)
        .all()
    )
    for sg, c in rows:
        if sg:
            status_counts[sg] = c

    ok_2xx = status_counts.get("2xx", 0)
    redirects_3xx = status_counts.get("3xx", 0)
    errors_4xx = status_counts.get("4xx", 0)
    errors_5xx = status_counts.get("5xx", 0)

    indexable_count = db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True, Url.indexable == True,
    ).scalar() or 0

    pct_2xx = _safe_pct(ok_2xx, total)
    pct_indexable = _safe_pct(indexable_count, total)
    pct_redirects = _safe_pct(redirects_3xx, total)
    pct_4xx = _safe_pct(errors_4xx, total)
    pct_5xx = _safe_pct(errors_5xx, total)

    # Score: base from 2xx%, penalized by errors
    score = pct_2xx * 0.5 + pct_indexable * 0.3 + max(0, 100 - pct_4xx * 5 - pct_5xx * 10) * 0.2

    recs: list[Recommendation] = []
    if errors_4xx > 0:
        recs.append(Recommendation(
            priority="alta", title="Corregir errores 4xx",
            description=f"Hay {errors_4xx} URLs internas que devuelven errores 4xx (no encontrado). Revisa los enlaces rotos y corrige o elimina las referencias.",
            affected_count=errors_4xx,
            url_filter={"status_group": "4xx", "is_internal": "true"},
        ))
    if errors_5xx > 0:
        recs.append(Recommendation(
            priority="alta", title="Corregir errores 5xx",
            description=f"Hay {errors_5xx} URLs internas con errores de servidor (5xx). Investiga los problemas del servidor para estas paginas.",
            affected_count=errors_5xx,
            url_filter={"status_group": "5xx", "is_internal": "true"},
        ))
    if pct_redirects > 10:
        recs.append(Recommendation(
            priority="media", title="Reducir redirecciones",
            description=f"El {pct_redirects}% de las URLs internas son redirecciones. Actualiza los enlaces para apuntar directamente a las URLs finales.",
            affected_count=redirects_3xx,
            url_filter={"status_group": "3xx", "is_internal": "true"},
        ))
    non_indexable = total - indexable_count
    if total > 0 and pct_indexable < 80:
        recs.append(Recommendation(
            priority="media", title="Mejorar indexabilidad",
            description=f"Solo el {pct_indexable}% de las URLs internas son indexables. Revisa las directivas noindex y canonicals para asegurar que las paginas importantes sean rastreables.",
            affected_count=non_indexable,
            url_filter={"indexable": "false", "is_internal": "true"},
        ))

    return CategoryInsight(
        key="crawlability", name="Rastreabilidad", icon="🔍",
        score=_clamp_score(score),
        metrics={
            "total_internal": total, "pct_2xx": pct_2xx, "pct_indexable": pct_indexable,
            "pct_redirects": pct_redirects, "pct_4xx": pct_4xx, "pct_5xx": pct_5xx,
            "errors_4xx": errors_4xx, "errors_5xx": errors_5xx, "redirects_3xx": redirects_3xx,
        },
        recommendations=recs,
    )


def _calc_content(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Content quality: titles, descriptions, word count, thin content."""
    total_html = db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True, Url.is_html == True,
    ).scalar() or 0

    # Issue counts for content-related issues
    content_issue_types = [
        "title_missing", "title_too_short", "title_too_long", "title_duplicate",
        "description_missing", "description_too_short", "description_too_long", "description_duplicate",
        "h1_missing", "h1_multiple", "h1_duplicate", "low_word_count",
        "very_low_text_ratio", "low_text_ratio", "duplicate_content",
    ]
    issue_rows = (
        db.query(Issue.issue_type, func.count(Issue.id))
        .filter(Issue.job_id == job_id, Issue.issue_type.in_(content_issue_types))
        .group_by(Issue.issue_type)
        .all()
    )
    issue_map: dict[str, int] = {it: c for it, c in issue_rows}

    title_missing = issue_map.get("title_missing", 0)
    title_problems = sum(issue_map.get(k, 0) for k in ["title_missing", "title_too_short", "title_too_long", "title_duplicate"])
    desc_missing = issue_map.get("description_missing", 0)
    desc_problems = sum(issue_map.get(k, 0) for k in ["description_missing", "description_too_short", "description_too_long", "description_duplicate"])
    h1_problems = sum(issue_map.get(k, 0) for k in ["h1_missing", "h1_multiple", "h1_duplicate"])
    thin_content = sum(issue_map.get(k, 0) for k in ["low_word_count", "very_low_text_ratio", "low_text_ratio"])

    # Average word count
    avg_wc = float(db.query(func.avg(Url.word_count)).filter(
        Url.job_id == job_id, Url.is_internal == True, Url.is_html == True, Url.word_count.isnot(None),
    ).scalar() or 0)

    pct_title_ok = _safe_pct(max(0, total_html - title_problems), total_html)
    pct_desc_ok = _safe_pct(max(0, total_html - desc_problems), total_html)
    pct_h1_ok = _safe_pct(max(0, total_html - h1_problems), total_html)
    pct_thin = _safe_pct(thin_content, total_html)

    score = pct_title_ok * 0.3 + pct_desc_ok * 0.25 + pct_h1_ok * 0.2 + max(0, 100 - pct_thin * 2) * 0.15 + min(100, avg_wc / 3) * 0.1

    recs: list[Recommendation] = []
    if title_missing > 0:
        recs.append(Recommendation(
            priority="alta", title="Agregar titulos faltantes",
            description=f"Hay {title_missing} paginas sin etiqueta title. El titulo es crucial para el posicionamiento y el CTR en los resultados de busqueda.",
            affected_count=title_missing,
            issue_types=["title_missing"],
        ))
    if title_problems - title_missing > 0:
        recs.append(Recommendation(
            priority="media", title="Optimizar titulos",
            description=f"Hay {title_problems - title_missing} paginas con titulos problematicos (demasiado cortos, largos o duplicados). Optimiza cada titulo para que sea unico y tenga entre 30-60 caracteres.",
            affected_count=title_problems - title_missing,
            issue_types=["title_too_short", "title_too_long", "title_duplicate"],
        ))
    if desc_missing > 0:
        recs.append(Recommendation(
            priority="alta", title="Agregar meta descripciones",
            description=f"Hay {desc_missing} paginas sin meta descripcion. Las descripciones ayudan a mejorar el CTR en los resultados de busqueda.",
            affected_count=desc_missing,
            issue_types=["description_missing"],
        ))
    if h1_problems > 0:
        recs.append(Recommendation(
            priority="media", title="Corregir encabezados H1",
            description=f"Hay {h1_problems} paginas con problemas en el H1 (faltante, multiples o duplicados). Cada pagina debe tener exactamente un H1 unico.",
            affected_count=h1_problems,
            issue_types=["h1_missing", "h1_multiple", "h1_duplicate"],
        ))
    if thin_content > 0:
        recs.append(Recommendation(
            priority="media", title="Mejorar contenido delgado",
            description=f"Hay {thin_content} paginas con contenido escaso (pocas palabras o ratio de texto bajo). Amplia el contenido de estas paginas para aportar mas valor.",
            affected_count=thin_content,
            issue_types=["low_word_count", "very_low_text_ratio", "low_text_ratio"],
        ))

    return CategoryInsight(
        key="content", name="Contenido", icon="📝",
        score=_clamp_score(score),
        metrics={
            "total_html": total_html, "pct_title_ok": pct_title_ok, "pct_desc_ok": pct_desc_ok,
            "pct_h1_ok": pct_h1_ok, "avg_word_count": round(avg_wc),
            "pct_thin": pct_thin, "thin_count": thin_content,
            "title_problems": title_problems, "desc_problems": desc_problems,
        },
        recommendations=recs,
    )


def _calc_links(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Links: orphan pages, inlinks distribution, nofollow, broken links."""
    total_internal = db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True, Url.is_html == True,
    ).scalar() or 0

    # Orphan pages (0 inlinks)
    orphan_count = db.query(func.count(Issue.id)).filter(
        Issue.job_id == job_id, Issue.issue_type == "orphan_page",
    ).scalar() or 0

    # Average inlinks
    avg_inlinks = float(db.query(func.avg(Url.inlinks_count)).filter(
        Url.job_id == job_id, Url.is_internal == True, Url.is_html == True,
    ).scalar() or 0)

    # Nofollow internal links
    total_internal_links = db.query(func.count(Link.id)).filter(
        Link.job_id == job_id, Link.is_internal == True,
    ).scalar() or 0
    nofollow_links = db.query(func.count(Link.id)).filter(
        Link.job_id == job_id, Link.is_internal == True, Link.follow == False,
    ).scalar() or 0

    # Broken links (4xx/5xx issue counts)
    broken_link_issues = db.query(func.count(Issue.id)).filter(
        Issue.job_id == job_id, Issue.issue_type.in_(["4xx_error", "5xx_error"]),
    ).scalar() or 0

    # High outlinks
    high_outlinks = db.query(func.count(Issue.id)).filter(
        Issue.job_id == job_id, Issue.issue_type == "high_outlink_count",
    ).scalar() or 0

    pct_orphan = _safe_pct(orphan_count, total_internal)
    pct_nofollow = _safe_pct(nofollow_links, total_internal_links)

    score = (
        max(0, 100 - pct_orphan * 3) * 0.35
        + min(100, avg_inlinks * 10) * 0.25
        + max(0, 100 - pct_nofollow * 2) * 0.15
        + max(0, 100 - _safe_pct(broken_link_issues, total_internal) * 5) * 0.25
    )

    recs: list[Recommendation] = []
    if orphan_count > 0:
        recs.append(Recommendation(
            priority="alta", title="Enlazar paginas huerfanas",
            description=f"Hay {orphan_count} paginas sin enlaces internos que apunten a ellas. Agrega enlaces desde paginas relevantes para que los buscadores las descubran.",
            affected_count=orphan_count,
            issue_types=["orphan_page"],
        ))
    if broken_link_issues > 0:
        recs.append(Recommendation(
            priority="alta", title="Corregir enlaces rotos",
            description=f"Se detectaron {broken_link_issues} paginas con errores HTTP. Corrige o elimina los enlaces que apuntan a estas paginas.",
            affected_count=broken_link_issues,
            issue_types=["4xx_error", "5xx_error"],
        ))
    if high_outlinks > 0:
        recs.append(Recommendation(
            priority="baja", title="Reducir enlaces salientes excesivos",
            description=f"Hay {high_outlinks} paginas con demasiados enlaces salientes, lo que puede diluir la autoridad de enlace.",
            affected_count=high_outlinks,
            issue_types=["high_outlink_count"],
        ))

    return CategoryInsight(
        key="links", name="Enlaces", icon="🔗",
        score=_clamp_score(score),
        metrics={
            "total_internal_pages": total_internal, "orphan_count": orphan_count,
            "pct_orphan": pct_orphan, "avg_inlinks": round(avg_inlinks, 1),
            "total_internal_links": total_internal_links,
            "nofollow_links": nofollow_links, "pct_nofollow": pct_nofollow,
            "broken_link_issues": broken_link_issues, "high_outlinks": high_outlinks,
        },
        recommendations=recs,
    )


def _calc_security(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Security: HTTPS, HSTS, CSP, mixed content."""
    # Count security header stats across all internal HTML pages
    total_with_sec = db.query(func.count(SecurityHeaders.url_id)).join(
        Url, SecurityHeaders.url_id == Url.id,
    ).filter(Url.job_id == job_id, Url.is_internal == True).scalar() or 0

    if total_with_sec == 0:
        return CategoryInsight(
            key="security", name="Seguridad", icon="🔒", score=0,
            metrics={"total_checked": 0, "pct_https": 0, "pct_hsts": 0, "pct_csp": 0, "pct_mixed": 0},
            recommendations=[Recommendation(
                priority="baja", title="Sin datos de seguridad",
                description="No se encontraron cabeceras de seguridad en las paginas rastreadas.",
                affected_count=0,
            )],
        )

    agg = db.query(
        func.sum(case((SecurityHeaders.is_https == True, 1), else_=0)).label("https_count"),
        func.sum(case((SecurityHeaders.has_hsts == True, 1), else_=0)).label("hsts_count"),
        func.sum(case((SecurityHeaders.has_csp == True, 1), else_=0)).label("csp_count"),
        func.sum(case((SecurityHeaders.has_mixed_content == True, 1), else_=0)).label("mixed_count"),
        func.sum(case((SecurityHeaders.has_x_content_type_options == True, 1), else_=0)).label("xcto_count"),
        func.sum(case((SecurityHeaders.has_x_frame_options == True, 1), else_=0)).label("xfo_count"),
    ).join(Url, SecurityHeaders.url_id == Url.id).filter(
        Url.job_id == job_id, Url.is_internal == True,
    ).first()

    https_count = agg.https_count or 0
    hsts_count = agg.hsts_count or 0
    csp_count = agg.csp_count or 0
    mixed_count = agg.mixed_count or 0
    xcto_count = agg.xcto_count or 0
    xfo_count = agg.xfo_count or 0

    pct_https = _safe_pct(https_count, total_with_sec)
    pct_hsts = _safe_pct(hsts_count, total_with_sec)
    pct_csp = _safe_pct(csp_count, total_with_sec)
    pct_mixed = _safe_pct(mixed_count, total_with_sec)

    score = pct_https * 0.35 + pct_hsts * 0.2 + pct_csp * 0.15 + max(0, 100 - pct_mixed * 5) * 0.15 + _safe_pct(xcto_count, total_with_sec) * 0.075 + _safe_pct(xfo_count, total_with_sec) * 0.075

    recs: list[Recommendation] = []
    non_https = total_with_sec - https_count
    if non_https > 0:
        recs.append(Recommendation(
            priority="alta", title="Migrar a HTTPS",
            description=f"Hay {non_https} paginas servidas sin HTTPS. HTTPS es fundamental para la seguridad y es un factor de posicionamiento.",
            affected_count=non_https,
            issue_types=["http_url"],
        ))
    if mixed_count > 0:
        recs.append(Recommendation(
            priority="alta", title="Eliminar contenido mixto",
            description=f"Hay {mixed_count} paginas con contenido mixto (recursos HTTP en paginas HTTPS). Actualiza todas las referencias a HTTPS.",
            affected_count=mixed_count,
            issue_types=["mixed_content"],
        ))
    non_hsts = total_with_sec - hsts_count
    if non_hsts > 0 and https_count > 0:
        recs.append(Recommendation(
            priority="media", title="Implementar HSTS",
            description=f"Hay {non_hsts} paginas sin la cabecera HSTS. HSTS fuerza conexiones seguras y previene ataques de downgrade.",
            affected_count=non_hsts,
            issue_types=["missing_hsts"],
        ))
    non_csp = total_with_sec - csp_count
    if non_csp > 0:
        recs.append(Recommendation(
            priority="baja", title="Agregar Content-Security-Policy",
            description=f"Hay {non_csp} paginas sin politica CSP. CSP protege contra ataques XSS e inyeccion de datos.",
            affected_count=non_csp,
            issue_types=["missing_csp"],
        ))

    return CategoryInsight(
        key="security", name="Seguridad", icon="🔒",
        score=_clamp_score(score),
        metrics={
            "total_checked": total_with_sec, "https_count": https_count,
            "pct_https": pct_https, "pct_hsts": pct_hsts, "pct_csp": pct_csp,
            "pct_mixed": pct_mixed, "mixed_count": mixed_count,
        },
        recommendations=recs,
    )


def _calc_structured_data(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Structured data: pages with SD, types, validation errors."""
    total_html = db.query(func.count(Url.id)).filter(
        Url.job_id == job_id, Url.is_internal == True, Url.is_html == True,
    ).scalar() or 0

    # Pages that have at least one structured data item
    pages_with_sd = db.query(func.count(func.distinct(StructuredData.url_id))).join(
        Url, StructuredData.url_id == Url.id,
    ).filter(Url.job_id == job_id, Url.is_internal == True).scalar() or 0

    # Schema types used
    type_rows = (
        db.query(StructuredData.schema_type, func.count(StructuredData.id))
        .join(Url, StructuredData.url_id == Url.id)
        .filter(Url.job_id == job_id, StructuredData.schema_type.isnot(None))
        .group_by(StructuredData.schema_type)
        .order_by(func.count(StructuredData.id).desc())
        .limit(10)
        .all()
    )
    types_used = {t: c for t, c in type_rows}

    # Validation errors
    sd_errors = db.query(func.count(StructuredData.id)).join(
        Url, StructuredData.url_id == Url.id,
    ).filter(
        Url.job_id == job_id, StructuredData.validation_status == "error",
    ).scalar() or 0

    total_sd = db.query(func.count(StructuredData.id)).join(
        Url, StructuredData.url_id == Url.id,
    ).filter(Url.job_id == job_id).scalar() or 0

    pct_with_sd = _safe_pct(pages_with_sd, total_html)
    pct_errors = _safe_pct(sd_errors, total_sd)

    score = min(100, pct_with_sd * 1.2) * 0.6 + max(0, 100 - pct_errors * 3) * 0.4

    recs: list[Recommendation] = []
    pages_without = total_html - pages_with_sd
    if pages_without > 0 and pct_with_sd < 50:
        recs.append(Recommendation(
            priority="media", title="Agregar datos estructurados",
            description=f"Hay {pages_without} paginas HTML sin datos estructurados. Agrega Schema.org (JSON-LD) para mejorar la visibilidad en resultados de busqueda.",
            affected_count=pages_without,
        ))
    if sd_errors > 0:
        recs.append(Recommendation(
            priority="alta", title="Corregir errores de validacion",
            description=f"Hay {sd_errors} bloques de datos estructurados con errores de validacion. Corrigelos para que los buscadores los interpreten correctamente.",
            affected_count=sd_errors,
            issue_types=["structured_data_error"],
        ))

    return CategoryInsight(
        key="structured_data", name="Datos Estructurados", icon="📊",
        score=_clamp_score(score),
        metrics={
            "total_html": total_html, "pages_with_sd": pages_with_sd,
            "pct_with_sd": pct_with_sd, "types_used": types_used,
            "total_sd_blocks": total_sd, "sd_errors": sd_errors, "pct_errors": pct_errors,
        },
        recommendations=recs,
    )


def _calc_i18n(job_id: uuid.UUID, db: Session) -> CategoryInsight:
    """Internationalization: hreflang tags, return tags, valid langs."""
    total_hreflang = db.query(func.count(Hreflang.id)).join(
        Url, Hreflang.url_id == Url.id,
    ).filter(Url.job_id == job_id).scalar() or 0

    if total_hreflang == 0:
        return CategoryInsight(
            key="i18n", name="Internacionalizacion", icon="🌐", score=100,
            metrics={"total_hreflang": 0, "languages": [], "pct_return_ok": 0, "pct_lang_valid": 0},
            recommendations=[],
        )

    # Languages detected
    lang_rows = (
        db.query(Hreflang.lang, func.count(Hreflang.id))
        .join(Url, Hreflang.url_id == Url.id)
        .filter(Url.job_id == job_id)
        .group_by(Hreflang.lang)
        .all()
    )
    languages = [lang for lang, _ in lang_rows]

    # Return tags OK / lang valid
    return_ok = db.query(func.count(Hreflang.id)).join(
        Url, Hreflang.url_id == Url.id,
    ).filter(Url.job_id == job_id, Hreflang.return_tag_ok == True).scalar() or 0

    lang_valid = db.query(func.count(Hreflang.id)).join(
        Url, Hreflang.url_id == Url.id,
    ).filter(Url.job_id == job_id, Hreflang.lang_valid == True).scalar() or 0

    pct_return_ok = _safe_pct(return_ok, total_hreflang)
    pct_lang_valid = _safe_pct(lang_valid, total_hreflang)

    score = pct_return_ok * 0.5 + pct_lang_valid * 0.5

    recs: list[Recommendation] = []
    missing_return = total_hreflang - return_ok
    if missing_return > 0:
        recs.append(Recommendation(
            priority="alta", title="Corregir etiquetas hreflang sin retorno",
            description=f"Hay {missing_return} etiquetas hreflang sin una etiqueta de retorno confirmada. Cada hreflang debe tener una referencia reciproca.",
            affected_count=missing_return,
            issue_types=["hreflang_missing_return"],
        ))
    invalid_lang = total_hreflang - lang_valid
    if invalid_lang > 0:
        recs.append(Recommendation(
            priority="media", title="Corregir codigos de idioma invalidos",
            description=f"Hay {invalid_lang} etiquetas hreflang con codigos de idioma no validos. Usa codigos ISO 639-1 (ej: es, en, fr).",
            affected_count=invalid_lang,
            issue_types=["hreflang_invalid_lang"],
        ))

    return CategoryInsight(
        key="i18n", name="Internacionalizacion", icon="🌐",
        score=_clamp_score(score),
        metrics={
            "total_hreflang": total_hreflang, "languages": languages,
            "pct_return_ok": pct_return_ok, "pct_lang_valid": pct_lang_valid,
        },
        recommendations=recs,
    )


@router.get("/insights", response_model=InsightsResponse)
def get_insights(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    _get_job_or_404(job_id, db)

    categories = [
        _calc_crawlability(job_id, db),
        _calc_content(job_id, db),
        _calc_links(job_id, db),
        _calc_security(job_id, db),
        _calc_structured_data(job_id, db),
        _calc_i18n(job_id, db),
    ]

    # Weighted average: crawlability 25%, content 25%, links 20%, security 15%, SD 10%, i18n 5%
    weights = [0.25, 0.25, 0.20, 0.15, 0.10, 0.05]
    overall = sum(c.score * w for c, w in zip(categories, weights))

    return InsightsResponse(
        job_id=job_id,
        overall_score=_clamp_score(overall),
        categories=categories,
        generated_at=datetime.now(timezone.utc),
    )
