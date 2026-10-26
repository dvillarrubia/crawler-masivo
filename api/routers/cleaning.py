"""Post-crawl content cleaning endpoints.

Optional, a posteriori process: group a job's pages by URL shape,
preview cleaning rules against samples, apply them in bulk (with
original-content backup), and revert if needed.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.cleaning_engine import (
    clean_with_safety,
    lineas_repetidas,
    make_excerpt,
    url_group_key,
    validate_rules,
)
from api.schemas import (
    CleaningApplyRequest,
    CleaningApplyResponse,
    CleaningGroupInfo,
    CleaningPreviewRequest,
    CleaningPreviewResponse,
    CleaningPreviewSample,
    CleaningRevertRequest,
    CleaningRulesetResponse,
)
from shared.database import get_session
from shared.models import CleaningRuleset, Job, PageContent, Url

router = APIRouter(prefix="/api/jobs/{job_id}/cleaning", tags=["cleaning"])

APPLY_BATCH_SIZE = 500
# Texto repetido por grupo (decision 198): paginas de la muestra, minimo de
# paginas para medirlo y cuantos grupos (los mas grandes) se miden.
REPETIDO_MUESTRA = 20
REPETIDO_MIN_PAGINAS = 3
REPETIDO_MAX_GRUPOS = 80


def _get_job(db: Session, job_id: uuid.UUID) -> Job:
    job = db.query(Job).filter(Job.id == job_id).one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _group_url_ids(
    db: Session,
    job_id: uuid.UUID,
    group_key: str | None,
    url_regex: str | None,
):
    """Yield (url_id, url) tuples for HTML pages matching group/regex."""
    compiled = None
    if url_regex:
        try:
            compiled = re.compile(url_regex)
        except re.error as exc:
            raise HTTPException(status_code=422, detail=f"Invalid url_regex: {exc}")

    query = (
        db.query(Url.id, Url.url, Url.path)
        .filter(Url.job_id == job_id, Url.is_html.is_(True))
        .order_by(Url.id)
    )
    for url_id, url, path in query.yield_per(1000):
        if group_key and url_group_key(path) != group_key:
            continue
        if compiled and not compiled.search(url):
            continue
        yield url_id, url


# ---------------------------------------------------------------------------
# GET /groups — URL-shape groups with content stats
# ---------------------------------------------------------------------------
@router.get("/groups", response_model=list[CleaningGroupInfo])
def list_groups(job_id: uuid.UUID, db: Session = Depends(get_session)):
    """Los grupos por forma de URL, los de mas texto repetido primero.

    El texto repetido (decision 198) se mide en una muestra de cada grupo: las
    lineas que salen en al menos la mitad de sus paginas. Es lo que la
    limpieza quita, y ordenar por el pone arriba lo que merece la pena limpiar;
    por paginas salian primero las carpetas mas grandes aunque no tuvieran
    nada repetido. Solo en los REPETIDO_MAX_GRUPOS grupos mas grandes y de al
    menos REPETIDO_MIN_PAGINAS: el resto va detras, por paginas, sin cifra.
    """
    _get_job(db, job_id)

    rows = (
        db.query(Url.id, Url.path, Url.url, PageContent.content_length, PageContent.cleaned_at)
        .join(PageContent, PageContent.url_id == Url.id)
        .filter(Url.job_id == job_id, Url.is_html.is_(True))
        .order_by(Url.id)
        .yield_per(2000)
    )

    groups: dict[str, dict] = {}
    for url_id, path, url, content_length, cleaned_at in rows:
        key = url_group_key(path)
        g = groups.setdefault(
            key,
            {"pages": 0, "cleaned": 0, "total_len": 0, "samples": [], "ids": []},
        )
        g["pages"] += 1
        g["total_len"] += content_length or 0
        if cleaned_at is not None:
            g["cleaned"] += 1
        if len(g["samples"]) < 3:
            g["samples"].append(url)
        # La muestra, las primeras por id: la misma en cada llamada.
        if len(g["ids"]) < REPETIDO_MUESTRA:
            g["ids"].append(url_id)

    medidos = sorted(
        (k for k, g in groups.items() if g["pages"] >= REPETIDO_MIN_PAGINAS),
        key=lambda k: -groups[k]["pages"],
    )[:REPETIDO_MAX_GRUPOS]
    for key in medidos:
        groups[key]["repetido"] = _repetido(db, groups[key]["ids"])

    def orden(kv):
        g = kv[1]
        rep = g.get("repetido")
        # Caracteres repetidos en todo el grupo: lo que quitaria limpiarlo.
        total = rep["chars_medio"] * g["pages"] if rep else -1
        return (-total, -g["pages"], kv[0])

    salida = []
    for key, g in sorted(groups.items(), key=orden):
        rep = g.get("repetido")
        salida.append(CleaningGroupInfo(
            group_key=key,
            pages=g["pages"],
            cleaned_pages=g["cleaned"],
            avg_content_length=round(g["total_len"] / g["pages"]) if g["pages"] else 0,
            sample_urls=g["samples"],
            repetido_parte=rep["parte"] if rep else None,
            repetido_chars_medio=rep["chars_medio"] if rep else None,
            repetido_muestra=rep["muestra"] if rep else None,
            lineas_repetidas=rep["lineas"] if rep else [],
            lineas_repetidas_total=rep["lineas_total"] if rep else None,
            repetido_parte_lineas=rep["parte_lineas"] if rep else None,
        ))
    return salida


def _repetido(db: Session, url_ids: list[int]) -> dict | None:
    """El texto repetido de una muestra, sobre `content_text`: es lo que leen
    duplicados, pocas palabras, coherencia y semantico, y donde casan las
    reglas de linea que propone la pestana. Antes se media sobre el markdown,
    y sus lineas (enlaces con su URL, imagenes en data:) no existen en el
    texto: «Quitar esta linea» no quitaba nada de el y la cifra salia
    inflada por las URLs. Best-effort: si falla, el grupo va
    sin cifra."""
    try:
        filas = (
            db.query(PageContent.content_text)
            .filter(PageContent.url_id.in_(url_ids))
            .all()
        )
        return lineas_repetidas([tx for (tx,) in filas])
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# POST /preview — dry-run rules against N sample pages
# ---------------------------------------------------------------------------
@router.post("/preview", response_model=CleaningPreviewResponse)
def preview_cleaning(
    job_id: uuid.UUID,
    payload: CleaningPreviewRequest,
    db: Session = Depends(get_session),
):
    _get_job(db, job_id)
    problems = validate_rules([r.model_dump() for r in payload.rules])
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    rules = [r.model_dump() for r in payload.rules]

    samples: list[CleaningPreviewSample] = []
    total_removed = 0
    skipped = 0

    for url_id, url in _group_url_ids(db, job_id, payload.group_key, payload.url_regex):
        pc = db.query(PageContent).filter(PageContent.url_id == url_id).one_or_none()
        if pc is None:
            continue

        source_text = pc.content_text
        source_md = pc.content_markdown
        removed_text = removed_md = 0

        if payload.targets in ("text", "both"):
            cleaned_text, removed_text, skip = clean_with_safety(source_text, rules)
            skipped += 1 if skip else 0
        else:
            cleaned_text = source_text
        if payload.targets in ("markdown", "both"):
            cleaned_md, removed_md, _ = clean_with_safety(source_md, rules)
        else:
            cleaned_md = source_md

        preview_before = source_md if payload.targets == "markdown" else source_text
        preview_after = cleaned_md if payload.targets == "markdown" else cleaned_text
        # Lo quitado, del mismo texto que se ensena y sobre el que se calcula
        # el porcentaje: con «Ambos» se sumaba lo del markdown y el extracto
        # (el texto) podia no cambiar (revision de UX del 25-oct, 3.6).
        removed = removed_md if payload.targets == "markdown" else removed_text

        samples.append(
            CleaningPreviewSample(
                url=url,
                before_excerpt=make_excerpt(preview_before),
                after_excerpt=make_excerpt(preview_after),
                chars_removed=removed,
                removed_pct=round(
                    removed / len(preview_before) * 100, 1
                ) if preview_before else 0.0,
            )
        )
        total_removed += removed
        if len(samples) >= payload.sample_size:
            break

    return CleaningPreviewResponse(
        samples=samples,
        total_chars_removed=total_removed,
        pages_skipped_safety=skipped,
    )


# ---------------------------------------------------------------------------
# POST /apply — apply rules to the whole group (with backup)
# ---------------------------------------------------------------------------
@router.post("/apply", response_model=CleaningApplyResponse)
def apply_cleaning(
    job_id: uuid.UUID,
    payload: CleaningApplyRequest,
    db: Session = Depends(get_session),
):
    _get_job(db, job_id)
    problems = validate_rules([r.model_dump() for r in payload.rules])
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    rules = [r.model_dump() for r in payload.rules]

    pages_updated = 0
    pages_skipped = 0
    chars_removed = 0
    now = datetime.now(timezone.utc)

    url_ids = [uid for uid, _ in _group_url_ids(db, job_id, payload.group_key, payload.url_regex)]

    for start in range(0, len(url_ids), APPLY_BATCH_SIZE):
        batch = url_ids[start : start + APPLY_BATCH_SIZE]
        contents = (
            db.query(PageContent)
            .filter(PageContent.url_id.in_(batch))
            .all()
        )
        for pc in contents:
            removed_page = 0
            page_skipped = False

            if payload.targets in ("text", "both") and pc.content_text:
                cleaned, removed, skip = clean_with_safety(pc.content_text, rules)
                if skip:
                    page_skipped = True
                elif removed > 0:
                    if pc.content_text_original is None:
                        pc.content_text_original = pc.content_text
                    pc.content_text = cleaned
                    pc.content_length = len(cleaned or "")
                    removed_page += removed

            if payload.targets in ("markdown", "both") and pc.content_markdown:
                cleaned, removed, skip = clean_with_safety(pc.content_markdown, rules)
                if skip:
                    page_skipped = True
                elif removed > 0:
                    if pc.content_markdown_original is None:
                        pc.content_markdown_original = pc.content_markdown
                    pc.content_markdown = cleaned
                    removed_page += removed

            if removed_page > 0:
                pc.cleaned_at = now
                pages_updated += 1
                chars_removed += removed_page
            if page_skipped:
                pages_skipped += 1
        db.commit()

    ruleset = CleaningRuleset(
        job_id=job_id,
        group_key=payload.group_key,
        url_regex=payload.url_regex,
        rules=rules,
        targets=payload.targets,
        pages_updated=pages_updated,
        pages_skipped=pages_skipped,
        chars_removed=chars_removed,
    )
    db.add(ruleset)
    db.commit()

    return CleaningApplyResponse(
        ruleset_id=ruleset.id,
        pages_updated=pages_updated,
        pages_skipped_safety=pages_skipped,
        total_chars_removed=chars_removed,
    )


# ---------------------------------------------------------------------------
# POST /revert — restore original content for a group
# ---------------------------------------------------------------------------
@router.post("/revert", response_model=CleaningApplyResponse)
def revert_cleaning(
    job_id: uuid.UUID,
    payload: CleaningRevertRequest,
    db: Session = Depends(get_session),
):
    _get_job(db, job_id)

    url_ids = [uid for uid, _ in _group_url_ids(db, job_id, payload.group_key, payload.url_regex)]
    reverted = 0

    for start in range(0, len(url_ids), APPLY_BATCH_SIZE):
        batch = url_ids[start : start + APPLY_BATCH_SIZE]
        contents = (
            db.query(PageContent)
            .filter(
                PageContent.url_id.in_(batch),
                PageContent.cleaned_at.isnot(None),
            )
            .all()
        )
        for pc in contents:
            if pc.content_text_original is not None:
                pc.content_text = pc.content_text_original
                pc.content_length = len(pc.content_text or "")
                pc.content_text_original = None
            if pc.content_markdown_original is not None:
                pc.content_markdown = pc.content_markdown_original
                pc.content_markdown_original = None
            pc.cleaned_at = None
            reverted += 1
        db.commit()

    return CleaningApplyResponse(
        ruleset_id=None,
        pages_updated=reverted,
        pages_skipped_safety=0,
        total_chars_removed=0,
    )


# ---------------------------------------------------------------------------
# GET /rulesets — history of applied cleaning rules
# ---------------------------------------------------------------------------
@router.get("/rulesets", response_model=list[CleaningRulesetResponse])
def list_rulesets(job_id: uuid.UUID, db: Session = Depends(get_session)):
    _get_job(db, job_id)
    rows = (
        db.query(CleaningRuleset)
        .filter(CleaningRuleset.job_id == job_id)
        .order_by(CleaningRuleset.created_at.desc())
        .all()
    )
    return rows
