"""Rendimiento en el tiempo (B3): evolución de un proyecto a lo largo de
sus rastreos completados.

Convierte datos que YA existen (varios runs del mismo client_id, cada uno
con su GSC, sus issues y su PageRank) en una serie temporal: la foto que
un SEO mira para saber si va mejor o peor que antes.

Honestidad de la casa: solo son comparables los runs con la MISMA
normalización de URL (fingerprint). Los cambios de fingerprint se marcan
en la serie (`comparable=False`) en vez de fingir continuidad.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.models import Issue, Job, Url, UrlSegment, WatchlistEntry

router = APIRouter(prefix="/api/clients/{client_id}", tags=["performance"])

# Métricas disponibles en la serie (para el selector del frontend).
METRICS = [
    {"key": "gsc_clicks", "label": "Clics (GSC)", "source": "gsc"},
    {"key": "gsc_impressions", "label": "Impresiones (GSC)", "source": "gsc"},
    {"key": "gsc_position", "label": "Posición media (GSC)", "source": "gsc", "lower_better": True},
    {"key": "urls_total", "label": "URLs rastreadas", "source": "crawl"},
    {"key": "urls_indexable", "label": "URLs indexables", "source": "crawl"},
    {"key": "issues_total", "label": "Incidencias", "source": "analysis", "lower_better": True},
    {"key": "issues_error", "label": "Incidencias graves", "source": "analysis", "lower_better": True},
    {"key": "pagerank_avg", "label": "PageRank medio", "source": "analysis"},
]


@router.get("/timeline")
def performance_timeline(
    client_id: str,
    segment_id: int | None = Query(None),
    watchlist: bool = Query(False),
    db: Session = Depends(get_session),
):
    """Un punto por rastreo completado del cliente (asc por fecha), con las
    métricas agregadas de cada uno. `comparable` marca los cortes de
    normalización.

    Seguir GRUPOS de URLs (petición del usuario): `segment_id` restringe la
    serie a una sección (los servicios, los cursos, una categoría…), y
    `watchlist=true` a las URLs concretas vigiladas del proyecto. Así se ve
    la evolución de LO IMPORTANTE, no solo la del sitio entero."""
    from shared.semantic_models import GscJobData

    jobs = (
        db.query(Job)
        .filter(Job.client_id == client_id, Job.status == "completed")
        .order_by(Job.created_at.asc())
        .all()
    )
    if len(jobs) < 1:
        return {"status": "blocked", "reason": "sin_runs_completados",
                "metrics": METRICS, "points": [], "scope": _scope(segment_id, watchlist)}

    job_ids = [j.id for j in jobs]

    # -- restricción del scope: qué url_ids cuentan en cada job ----------
    # segment_id → las URLs de esa sección (por job). watchlist → las URLs
    # cuyo hash está en la watchlist del cliente. Ambos limitan por job.
    url_filter = [Url.job_id.in_(job_ids), Url.is_internal.is_(True), Url.is_html.is_(True)]
    gsc_url_join = None
    if segment_id is not None:
        seg_pairs = set(db.query(UrlSegment.job_id, UrlSegment.url_id).filter(
            UrlSegment.job_id.in_(job_ids), UrlSegment.segment_id == segment_id).all())
        allowed_url_ids = {uid for (_j, uid) in seg_pairs}
        url_filter.append(Url.id.in_(allowed_url_ids) if allowed_url_ids else Url.id.is_(None))
        gsc_url_join = allowed_url_ids
    elif watchlist:
        watch_hashes = {w.url_hash for w in db.query(WatchlistEntry).filter(
            WatchlistEntry.client_id == client_id).all()}
        url_filter.append(Url.url_hash.in_(watch_hashes) if watch_hashes else Url.id.is_(None))
        # url_ids vigilados por job (para restringir GSC)
        gsc_url_join = {uid for (uid,) in db.query(Url.id).filter(
            Url.job_id.in_(job_ids), Url.url_hash.in_(watch_hashes)).all()} if watch_hashes else set()

    # -- agregados por job -----------------------------------------------
    gsc_q = db.query(
        GscJobData.job_id,
        func.coalesce(func.sum(GscJobData.clicks), 0).label("clicks"),
        func.coalesce(func.sum(GscJobData.impressions), 0).label("impressions"),
        func.sum(GscJobData.position * GscJobData.impressions).label("pos_w"),
    ).filter(GscJobData.job_id.in_(job_ids))
    if gsc_url_join is not None:  # scope activo: GSC solo de esas URLs
        gsc_q = gsc_q.filter(GscJobData.url_id.in_(gsc_url_join) if gsc_url_join else GscJobData.id.is_(None))
    gsc = {r.job_id: r for r in gsc_q.group_by(GscJobData.job_id).all()}

    urls = {
        r.job_id: r for r in db.query(
            Url.job_id,
            func.count().label("total"),
            func.coalesce(func.sum(
                case((Url.indexable.is_(True), 1), else_=0)), 0).label("indexable"),
            func.avg(Url.pagerank).label("pr_avg"),
        ).filter(*url_filter).group_by(Url.job_id).all()
    }

    # incidencias: si hay scope, solo de las URLs del scope
    iq = db.query(Issue.job_id, Issue.severity, func.count()).filter(
        Issue.job_id.in_(job_ids))
    if gsc_url_join is not None:
        iq = iq.filter(Issue.url_id.in_(gsc_url_join) if gsc_url_join else Issue.id.is_(None))
    issues: dict = {}
    for jid, sev, n in iq.group_by(Issue.job_id, Issue.severity).all():
        d = issues.setdefault(jid, {"total": 0, "error": 0})
        d["total"] += n
        if sev == "error":
            d["error"] += n

    points = []
    prev_fp = object()  # sentinela: el primero nunca es comparable
    for j in jobs:
        g = gsc.get(j.id)
        u = urls.get(j.id)
        iss = issues.get(j.id, {"total": 0, "error": 0})
        clicks = int(g.clicks) if g else 0
        imprs = int(g.impressions) if g else 0
        avg_pos = round(float(g.pos_w) / imprs, 2) if g and imprs and g.pos_w else None
        comparable = j.normalization_fingerprint == prev_fp
        points.append({
            "job_id": str(j.id), "name": j.name,
            "date": (j.completed_at or j.created_at).isoformat() if (j.completed_at or j.created_at) else None,
            "comparable": comparable,  # vs el punto anterior
            "metrics": {
                "gsc_clicks": clicks,
                "gsc_impressions": imprs,
                "gsc_position": avg_pos,
                "urls_total": int(u.total) if u else 0,
                "urls_indexable": int(u.indexable) if u else 0,
                "issues_total": iss["total"],
                "issues_error": iss["error"],
                "pagerank_avg": round(float(u.pr_avg), 3) if u and u.pr_avg is not None else None,
            },
        })
        prev_fp = j.normalization_fingerprint

    return {"status": "ok", "metrics": METRICS, "points": points,
            "scope": _scope(segment_id, watchlist)}


def _scope(segment_id, watchlist) -> dict:
    if segment_id is not None:
        return {"kind": "segment", "segment_id": segment_id}
    if watchlist:
        return {"kind": "watchlist"}
    return {"kind": "site"}


@router.get("/performance-summary")
def performance_summary(
    client_id: str,
    segment_id: int | None = Query(None),
    watchlist: bool = Query(False),
    db: Session = Depends(get_session),
):
    """Comparación destacada: el último rastreo frente al más antiguo del
    histórico. Devuelve por métrica el valor actual, el de referencia y el
    delta — la foto interanual. Respeta el scope (segmento / watchlist)."""
    tl = performance_timeline(client_id, segment_id=segment_id,
                              watchlist=watchlist, db=db)
    if tl["status"] != "ok" or len(tl["points"]) < 2:
        return {"status": "blocked", "reason": "hacen_falta_2_runs",
                "metrics": METRICS}

    points = tl["points"]
    current = points[-1]
    # referencia: el más antiguo COMPARABLE con el actual (mismo
    # fingerprint desde ahí hasta el final); si hubo un corte, avisamos
    ref = points[0]
    cut = any(not p["comparable"] for p in points[1:])

    out = {}
    for m in METRICS:
        k = m["key"]
        cur = current["metrics"].get(k)
        base = ref["metrics"].get(k)
        delta = None
        if isinstance(cur, (int, float)) and isinstance(base, (int, float)):
            delta = round(cur - base, 3)
        out[k] = {"label": m["label"], "actual": cur, "referencia": base,
                  "delta": delta, "lower_better": m.get("lower_better", False)}

    return {
        "status": "ok",
        "scope": _scope(segment_id, watchlist),
        "actual": {"name": current["name"], "date": current["date"]},
        "referencia": {"name": ref["name"], "date": ref["date"]},
        "hay_corte_normalizacion": cut,
        "metricas": out,
    }


@router.get("/watchlist-timeline")
def watchlist_timeline(
    client_id: str,
    db: Session = Depends(get_session),
):
    """Evolución de CADA URL vigilada, una a una (petición del usuario:
    "seguir la evolución de ciertas URLs solo"). Por cada entrada de la
    watchlist, su serie de clics/impresiones/posición a lo largo de los
    rastreos — para vigilar los servicios, los cursos, las categorías clave.
    """
    from shared.semantic_models import GscJobData

    watch = db.query(WatchlistEntry).filter(
        WatchlistEntry.client_id == client_id).order_by(WatchlistEntry.id).all()
    if not watch:
        return {"status": "blocked", "reason": "watchlist_vacia", "urls": []}

    jobs = (
        db.query(Job).filter(Job.client_id == client_id, Job.status == "completed")
        .order_by(Job.created_at.asc()).all()
    )
    if not jobs:
        return {"status": "blocked", "reason": "sin_runs_completados", "urls": []}
    job_ids = [j.id for j in jobs]
    job_meta = {j.id: {"name": j.name,
                       "date": (j.completed_at or j.created_at).isoformat()
                       if (j.completed_at or j.created_at) else None} for j in jobs}

    hashes = [w.url_hash for w in watch]
    # GSC por (job, url_hash) agregada — gsc_job_data conserva url_hash (T9)
    gsc_rows = db.query(
        GscJobData.job_id, GscJobData.url_hash,
        func.coalesce(func.sum(GscJobData.clicks), 0),
        func.coalesce(func.sum(GscJobData.impressions), 0),
        func.sum(GscJobData.position * GscJobData.impressions),
    ).filter(GscJobData.job_id.in_(job_ids),
             GscJobData.url_hash.in_(hashes)).group_by(
        GscJobData.job_id, GscJobData.url_hash).all()
    by_hash: dict[str, dict] = {}
    for jid, h, clicks, imprs, pos_w in gsc_rows:
        by_hash.setdefault(h, {})[jid] = {
            "clicks": int(clicks), "impressions": int(imprs),
            "position": round(float(pos_w) / imprs, 2) if imprs and pos_w else None,
        }

    urls = []
    for w in watch:
        serie = []
        for j in jobs:
            pt = by_hash.get(w.url_hash, {}).get(j.id)
            serie.append({
                "run": job_meta[j.id]["name"], "date": job_meta[j.id]["date"],
                "clicks": pt["clicks"] if pt else None,
                "impressions": pt["impressions"] if pt else None,
                "position": pt["position"] if pt else None,
            })
        con_datos = [s for s in serie if s["clicks"] is not None]
        urls.append({
            "url": w.url, "label": w.label,
            "serie": serie,
            "tiene_datos": bool(con_datos),
        })
    return {"status": "ok", "urls": urls}
