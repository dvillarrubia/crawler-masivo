"""Informe de rendimiento AGNÓSTICO a los rastreos (serie diaria).

A diferencia de `performance.py` (un punto por rastreo), aquí el eje es la
FECHA, no el crawl: la serie diaria real de Search Console (y GA4),
ingerida a nivel de propiedad/cliente e independiente de cuándo se rastreó.

Tres piezas:
  1. Cuentas GA4 (CRUD) — el lado de negocio (sesiones/conversiones).
  2. Sincronización diaria — trae GSC y GA4 día a día para un rango.
  3. Informe por rangos — agrega por día/semana/mes y compara dos periodos
     (p. ej. este mes vs. el anterior).

Regla de la casa: sin datos → `blocked` con motivo, nunca ceros que
parezcan reales.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.models import WatchlistEntry
from shared.semantic_models import Ga4Account, Ga4Daily, GscAccount, GscDaily

router = APIRouter(prefix="/api/clients/{client_id}/metrics", tags=["metrics"])

# Métricas del informe por fechas, con su fuente y sentido (bajar es mejor
# en posición). El frontend las usa para pintar selector y deltas.
GSC_METRICS = [
    {"key": "clicks", "label": "Clics", "source": "gsc"},
    {"key": "impressions", "label": "Impresiones", "source": "gsc"},
    {"key": "ctr", "label": "CTR (%)", "source": "gsc", "derived": True},
    {"key": "position", "label": "Posición media", "source": "gsc", "lower_better": True},
]
GA4_METRICS = [
    {"key": "sessions", "label": "Sesiones", "source": "ga4"},
    {"key": "active_users", "label": "Usuarios activos", "source": "ga4"},
    {"key": "conversions", "label": "Conversiones", "source": "ga4"},
    {"key": "revenue", "label": "Ingresos", "source": "ga4"},
]


# ---------------------------------------------------------------------------
# Cuentas GA4 (CRUD) — mismo patrón que las cuentas GSC en semantic.py
# ---------------------------------------------------------------------------
class Ga4AccountCreate(BaseModel):
    name: str
    property_id: str
    credentials_json: dict[str, Any]


class Ga4AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    property_id: str


# Estos endpoints no dependen de client_id (las cuentas son globales, como
# las de GSC), pero cuelgan del mismo router por cercanía temática. Se
# ignora client_id en el path para el CRUD.
@router.get("/ga4-accounts", response_model=list[Ga4AccountResponse])
def list_ga4_accounts(client_id: str, db: Session = Depends(get_session)):
    return db.query(Ga4Account).order_by(Ga4Account.created_at.desc()).all()


@router.post("/ga4-accounts", response_model=Ga4AccountResponse)
def create_ga4_account(client_id: str, body: Ga4AccountCreate,
                       db: Session = Depends(get_session)):
    acc = Ga4Account(name=body.name, property_id=body.property_id,
                     credentials_json=body.credentials_json)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


@router.delete("/ga4-accounts/{account_id}")
def delete_ga4_account(client_id: str, account_id: uuid.UUID,
                       db: Session = Depends(get_session)):
    acc = db.query(Ga4Account).filter(Ga4Account.id == account_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Cuenta GA4 no encontrada")
    db.delete(acc)
    db.commit()
    return {"ok": True}


@router.get("/ga4-accounts/{account_id}/properties")
def ga4_account_properties(client_id: str, account_id: uuid.UUID,
                           db: Session = Depends(get_session)):
    acc = db.query(Ga4Account).filter(Ga4Account.id == account_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Cuenta GA4 no encontrada")
    try:
        from POC_centro_semantico.src.ga4 import get_ga4_properties
        return {"properties": get_ga4_properties(acc.credentials_json)}
    except ImportError:
        raise HTTPException(status_code=501, detail=(
            "Falta la librería google-analytics-admin. Instala "
            "google-analytics-data y google-analytics-admin en el contenedor api."))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Sincronización diaria
# ---------------------------------------------------------------------------
class SyncGscRequest(BaseModel):
    gsc_account_id: uuid.UUID
    property_url: str
    start_date: str            # ISO YYYY-MM-DD
    end_date: str
    by_page: bool = False      # también por URL (para watchlist en el tiempo)


class SyncGa4Request(BaseModel):
    ga4_account_id: uuid.UUID
    property_id: str | None = None    # si None usa el de la cuenta
    start_date: str
    end_date: str


def _parse_day(s: str) -> datetime:
    return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


@router.post("/sync-gsc")
def sync_gsc_daily(client_id: str, body: SyncGscRequest,
                   db: Session = Depends(get_session)):
    """Trae la serie DIARIA de GSC para el rango y la reemplaza en
    `gsc_daily` (idempotente: borra el rango y reinserta). `by_page=true`
    guarda además el detalle por URL, con `url_hash` normalizado por
    defecto para poder cruzar con las URLs vigiladas."""
    acc = db.query(GscAccount).filter(GscAccount.id == body.gsc_account_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Cuenta GSC no encontrada")
    try:
        from POC_centro_semantico.src.gsc import fetch_gsc_daily
        from shared.url_normalization import compute_url_hash
    except ImportError as e:  # pragma: no cover
        raise HTTPException(status_code=501, detail=str(e))

    try:
        df = fetch_gsc_daily(acc.credentials_json, body.property_url,
                             body.start_date, body.end_date, by_page=body.by_page)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Error consultando GSC: {e}")

    start, end = _parse_day(body.start_date), _parse_day(body.end_date)
    # Borra el rango exacto (mismo scope: site vs by_page) para no mezclar
    q = db.query(GscDaily).filter(
        GscDaily.client_id == client_id, GscDaily.property == body.property_url,
        GscDaily.date >= start, GscDaily.date <= end)
    q = q.filter(GscDaily.url_hash.isnot(None)) if body.by_page else q.filter(GscDaily.url_hash.is_(None))
    q.delete(synchronize_session=False)

    n = 0
    for _, row in df.iterrows():
        d = _parse_day(str(row["date"]))
        rec = GscDaily(
            client_id=client_id, property=body.property_url, date=d,
            clicks=int(row["clicks"]), impressions=int(row["impressions"]),
            position=float(row["position"]) if row["position"] is not None else None,
        )
        if body.by_page:
            raw = str(row["url"])
            rec.url = raw
            rec.url_hash = compute_url_hash(raw)
        db.add(rec)
        n += 1
    db.commit()
    return {"status": "ok", "rows": n, "by_page": body.by_page,
            "range": [body.start_date, body.end_date]}


@router.post("/sync-ga4")
def sync_ga4_daily(client_id: str, body: SyncGa4Request,
                   db: Session = Depends(get_session)):
    """Trae la serie diaria de GA4 (por canal) y la reemplaza en
    `ga4_daily` para el rango."""
    acc = db.query(Ga4Account).filter(Ga4Account.id == body.ga4_account_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Cuenta GA4 no encontrada")
    prop = body.property_id or acc.property_id
    try:
        from POC_centro_semantico.src.ga4 import fetch_ga4_daily
    except ImportError:
        raise HTTPException(status_code=501, detail=(
            "Falta google-analytics-data. Instálalo en el contenedor api "
            "para sincronizar GA4."))
    try:
        df = fetch_ga4_daily(acc.credentials_json, prop,
                             body.start_date, body.end_date)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Error consultando GA4: {e}")

    start, end = _parse_day(body.start_date), _parse_day(body.end_date)
    db.query(Ga4Daily).filter(
        Ga4Daily.client_id == client_id, Ga4Daily.property_id == prop,
        Ga4Daily.date >= start, Ga4Daily.date <= end).delete(synchronize_session=False)
    n = 0
    for _, row in df.iterrows():
        db.add(Ga4Daily(
            client_id=client_id, property_id=prop, date=_parse_day(str(row["date"])),
            channel=str(row["channel"]) if row.get("channel") is not None else None,
            sessions=int(row["sessions"]), active_users=int(row["active_users"]),
            conversions=float(row["conversions"]), revenue=float(row["revenue"]),
        ))
        n += 1
    db.commit()
    return {"status": "ok", "rows": n, "property_id": prop,
            "range": [body.start_date, body.end_date]}


# ---------------------------------------------------------------------------
# Cobertura: qué rango de datos tenemos (para que la UI no pida a ciegas)
# ---------------------------------------------------------------------------
@router.get("/coverage")
def metrics_coverage(client_id: str, db: Session = Depends(get_session)):
    def _cov(model, extra=None):
        q = db.query(func.min(model.date), func.max(model.date), func.count()).filter(
            model.client_id == client_id)
        if extra is not None:
            q = q.filter(extra)
        lo, hi, cnt = q.one()
        return {"desde": lo.date().isoformat() if lo else None,
                "hasta": hi.date().isoformat() if hi else None, "filas": cnt or 0}

    return {
        "gsc": _cov(GscDaily, GscDaily.url_hash.is_(None)),
        "gsc_por_url": _cov(GscDaily, GscDaily.url_hash.isnot(None)),
        "ga4": _cov(Ga4Daily),
    }


# ---------------------------------------------------------------------------
# Informe por rangos
# ---------------------------------------------------------------------------
def _bucket_key(d: datetime, granularity: str) -> str:
    if granularity == "all":
        return "all"           # un único bucket → totales del rango
    if granularity == "month":
        return d.strftime("%Y-%m")
    if granularity == "week":
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    return d.strftime("%Y-%m-%d")


def _aggregate_gsc(rows: list, granularity: str) -> dict[str, dict]:
    """Agrega filas diarias GSC por bucket. Posición ponderada por
    impresiones; CTR derivado de clics/impresiones del bucket."""
    buckets: dict[str, dict] = {}
    for r in rows:
        k = _bucket_key(r.date, granularity)
        b = buckets.setdefault(k, {"clicks": 0, "impressions": 0, "pos_w": 0.0})
        b["clicks"] += r.clicks or 0
        b["impressions"] += r.impressions or 0
        if r.position is not None and r.impressions:
            b["pos_w"] += r.position * r.impressions
    out = {}
    for k, b in buckets.items():
        imp = b["impressions"]
        out[k] = {
            "clicks": b["clicks"], "impressions": imp,
            "ctr": round(100 * b["clicks"] / imp, 2) if imp else None,
            "position": round(b["pos_w"] / imp, 2) if imp else None,
        }
    return out


def _aggregate_ga4(rows: list, granularity: str) -> dict[str, dict]:
    buckets: dict[str, dict] = {}
    for r in rows:
        k = _bucket_key(r.date, granularity)
        b = buckets.setdefault(k, {"sessions": 0, "active_users": 0,
                                   "conversions": 0.0, "revenue": 0.0})
        b["sessions"] += r.sessions or 0
        b["active_users"] += r.active_users or 0
        b["conversions"] += r.conversions or 0.0
        b["revenue"] += r.revenue or 0.0
    return {k: {"sessions": b["sessions"], "active_users": b["active_users"],
                "conversions": round(b["conversions"], 2),
                "revenue": round(b["revenue"], 2)} for k, b in buckets.items()}


def _totals_gsc(rows: list) -> dict:
    agg = _aggregate_gsc(rows, "all")
    return next(iter(agg.values())) if agg else {
        "clicks": 0, "impressions": 0, "ctr": None, "position": None}


def _totals_ga4(rows: list) -> dict:
    agg = _aggregate_ga4(rows, "all")
    return next(iter(agg.values())) if agg else {
        "sessions": 0, "active_users": 0, "conversions": 0.0, "revenue": 0.0}


@router.get("/report")
def metrics_report(
    client_id: str,
    date_from: str = Query(..., description="ISO YYYY-MM-DD"),
    date_to: str = Query(..., description="ISO YYYY-MM-DD"),
    granularity: str = Query("day", pattern="^(day|week|month)$"),
    compare: str = Query("previous", pattern="^(previous|none)$"),
    source: str = Query("gsc", pattern="^(gsc|ga4)$"),
    watchlist: bool = Query(False),
    db: Session = Depends(get_session),
):
    """Informe por rango de fechas, independiente de los rastreos.

    - `granularity`: agrupa la serie por día/semana/mes.
    - `compare=previous`: compara el rango con el periodo INMEDIATAMENTE
      anterior de la misma longitud (deltas por métrica).
    - `source`: gsc (Search Console) o ga4 (Analytics).
    - `watchlist=true` (solo GSC by_page): restringe a las URLs vigiladas.
    """
    start, end = _parse_day(date_from), _parse_day(date_to)
    if end < start:
        raise HTTPException(status_code=422, detail="date_to anterior a date_from")
    span = end - start
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - span

    if source == "ga4":
        metrics = GA4_METRICS
        base = db.query(Ga4Daily).filter(Ga4Daily.client_id == client_id)
        cur_rows = base.filter(Ga4Daily.date >= start, Ga4Daily.date <= end).all()
        if not cur_rows:
            return {"status": "blocked", "reason": "sin_datos_ga4",
                    "metrics": metrics, "source": source}
        series_map = _aggregate_ga4(cur_rows, granularity)
        cur_tot = _totals_ga4(cur_rows)
        prev_tot = None
        if compare == "previous":
            prev_rows = base.filter(Ga4Daily.date >= prev_start,
                                    Ga4Daily.date <= prev_end).all()
            prev_tot = _totals_ga4(prev_rows) if prev_rows else None
    else:
        metrics = GSC_METRICS
        base = db.query(GscDaily).filter(GscDaily.client_id == client_id)
        if watchlist:
            hashes = {w.url_hash for w in db.query(WatchlistEntry).filter(
                WatchlistEntry.client_id == client_id).all()}
            base = base.filter(GscDaily.url_hash.in_(hashes) if hashes
                               else GscDaily.id.is_(None))
        else:
            base = base.filter(GscDaily.url_hash.is_(None))  # nivel propiedad
        cur_rows = base.filter(GscDaily.date >= start, GscDaily.date <= end).all()
        if not cur_rows:
            return {"status": "blocked",
                    "reason": "sin_datos_gsc_diarios",
                    "hint": "Sincroniza el rango con POST /metrics/sync-gsc",
                    "metrics": metrics, "source": source}
        series_map = _aggregate_gsc(cur_rows, granularity)
        cur_tot = _totals_gsc(cur_rows)
        prev_tot = None
        if compare == "previous":
            prev_rows = base.filter(GscDaily.date >= prev_start,
                                    GscDaily.date <= prev_end).all()
            prev_tot = _totals_gsc(prev_rows) if prev_rows else None

    series = [{"bucket": k, **series_map[k]} for k in sorted(series_map)]

    comparison = {}
    for m in metrics:
        k = m["key"]
        cur = cur_tot.get(k)
        base_v = prev_tot.get(k) if prev_tot else None
        delta = None
        if isinstance(cur, (int, float)) and isinstance(base_v, (int, float)):
            delta = round(cur - base_v, 2)
        comparison[k] = {"label": m["label"], "actual": cur,
                         "anterior": base_v, "delta": delta,
                         "lower_better": m.get("lower_better", False)}

    return {
        "status": "ok", "source": source, "granularity": granularity,
        "metrics": metrics,
        "rango": {"desde": date_from, "hasta": date_to},
        "rango_anterior": ({"desde": prev_start.date().isoformat(),
                            "hasta": prev_end.date().isoformat()}
                           if compare == "previous" else None),
        "series": series,
        "comparacion": comparison,
        "scope": {"kind": "watchlist" if (source == "gsc" and watchlist) else "site"},
    }
