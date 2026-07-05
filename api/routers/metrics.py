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


# -- Lógica de sincronización reutilizable (endpoint manual + cron) ----------
def do_sync_gsc(db: Session, client_id: str, account: GscAccount,
                property_url: str, start_date: str, end_date: str,
                by_page: bool) -> int:
    """Trae GSC día a día y reemplaza el rango en `gsc_daily` (idempotente).
    Devuelve el nº de filas. Sin capa HTTP para poder llamarla desde el cron."""
    from POC_centro_semantico.src.gsc import fetch_gsc_daily
    from shared.url_normalization import compute_url_hash

    df = fetch_gsc_daily(account.credentials_json, property_url,
                         start_date, end_date, by_page=by_page)
    start, end = _parse_day(start_date), _parse_day(end_date)
    q = db.query(GscDaily).filter(
        GscDaily.client_id == client_id, GscDaily.property == property_url,
        GscDaily.date >= start, GscDaily.date <= end)
    q = q.filter(GscDaily.url_hash.isnot(None)) if by_page else q.filter(GscDaily.url_hash.is_(None))
    q.delete(synchronize_session=False)

    n = 0
    for _, row in df.iterrows():
        rec = GscDaily(
            client_id=client_id, property=property_url, date=_parse_day(str(row["date"])),
            clicks=int(row["clicks"]), impressions=int(row["impressions"]),
            position=float(row["position"]) if row["position"] is not None else None,
        )
        if by_page:
            raw = str(row["url"])
            rec.url = raw
            rec.url_hash = compute_url_hash(raw)
        db.add(rec)
        n += 1
    db.commit()
    return n


def do_sync_ga4(db: Session, client_id: str, account: Ga4Account,
                property_id: str, start_date: str, end_date: str) -> int:
    """Trae GA4 día a día (por canal) y reemplaza el rango. Devuelve nº filas."""
    from POC_centro_semantico.src.ga4 import fetch_ga4_daily

    df = fetch_ga4_daily(account.credentials_json, property_id,
                         start_date, end_date)
    start, end = _parse_day(start_date), _parse_day(end_date)
    db.query(Ga4Daily).filter(
        Ga4Daily.client_id == client_id, Ga4Daily.property_id == property_id,
        Ga4Daily.date >= start, Ga4Daily.date <= end).delete(synchronize_session=False)
    n = 0
    for _, row in df.iterrows():
        db.add(Ga4Daily(
            client_id=client_id, property_id=property_id, date=_parse_day(str(row["date"])),
            channel=str(row["channel"]) if row.get("channel") is not None else None,
            sessions=int(row["sessions"]), active_users=int(row["active_users"]),
            conversions=float(row["conversions"]), revenue=float(row["revenue"]),
        ))
        n += 1
    db.commit()
    return n


@router.post("/sync-gsc")
def sync_gsc_daily(client_id: str, body: SyncGscRequest,
                   db: Session = Depends(get_session)):
    """Trae la serie DIARIA de GSC para el rango y la reemplaza en
    `gsc_daily` (idempotente). `by_page=true` guarda además el detalle por
    URL, con `url_hash` normalizado para cruzar con las URLs vigiladas."""
    acc = db.query(GscAccount).filter(GscAccount.id == body.gsc_account_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Cuenta GSC no encontrada")
    try:
        n = do_sync_gsc(db, client_id, acc, body.property_url,
                        body.start_date, body.end_date, body.by_page)
    except ImportError as e:  # pragma: no cover
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Error consultando GSC: {e}")
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
        n = do_sync_ga4(db, client_id, acc, prop, body.start_date, body.end_date)
    except ImportError:
        raise HTTPException(status_code=501, detail=(
            "Falta google-analytics-data. Instálalo en el contenedor api "
            "para sincronizar GA4."))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Error consultando GA4: {e}")
    return {"status": "ok", "rows": n, "property_id": prop,
            "range": [body.start_date, body.end_date]}


# ---------------------------------------------------------------------------
# Configuraciones de sincronización diaria (lo que el cron refresca solo)
# ---------------------------------------------------------------------------
class SyncConfigCreate(BaseModel):
    source: str                    # 'gsc' | 'ga4'
    account_id: str
    property: str
    by_page: bool = True
    enabled: bool = True


class SyncConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    source: str
    account_id: str
    property: str
    by_page: bool
    enabled: bool
    last_synced_at: datetime | None
    last_status: str | None


@router.get("/sync-configs", response_model=list[SyncConfigResponse])
def list_sync_configs(client_id: str, db: Session = Depends(get_session)):
    from shared.semantic_models import MetricSyncConfig
    return (db.query(MetricSyncConfig)
            .filter(MetricSyncConfig.client_id == client_id)
            .order_by(MetricSyncConfig.created_at.desc()).all())


@router.post("/sync-configs", response_model=SyncConfigResponse)
def upsert_sync_config(client_id: str, body: SyncConfigCreate,
                       db: Session = Depends(get_session)):
    """Programa (o actualiza) una fuente para el cron diario. Único por
    (cliente, fuente, propiedad): re-programar la misma no duplica."""
    from shared.semantic_models import MetricSyncConfig
    if body.source not in ("gsc", "ga4"):
        raise HTTPException(status_code=422, detail="source debe ser gsc o ga4")
    cfg = (db.query(MetricSyncConfig).filter(
        MetricSyncConfig.client_id == client_id,
        MetricSyncConfig.source == body.source,
        MetricSyncConfig.property == body.property).first())
    if not cfg:
        cfg = MetricSyncConfig(client_id=client_id, source=body.source,
                               property=body.property)
        db.add(cfg)
    cfg.account_id = body.account_id
    cfg.by_page = body.by_page
    cfg.enabled = body.enabled
    db.commit()
    db.refresh(cfg)
    return cfg


@router.delete("/sync-configs/{config_id}")
def delete_sync_config(client_id: str, config_id: uuid.UUID,
                       db: Session = Depends(get_session)):
    from shared.semantic_models import MetricSyncConfig
    cfg = db.query(MetricSyncConfig).filter(MetricSyncConfig.id == config_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Config no encontrada")
    db.delete(cfg)
    db.commit()
    return {"ok": True}


@router.post("/sync-configs/{config_id}/run")
def run_sync_config_now(client_id: str, config_id: uuid.UUID,
                        db: Session = Depends(get_session)):
    """Ejecuta ahora una config concreta (la ventana móvil por defecto).
    Mismo camino que usa el cron."""
    from shared.semantic_models import MetricSyncConfig
    cfg = db.query(MetricSyncConfig).filter(MetricSyncConfig.id == config_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Config no encontrada")
    ok, msg = run_one_config(db, cfg)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "ok", "detail": msg}


# Ventana móvil que refresca el cron: GSC arrastra ~2-3 días de lag y los
# datos siguen consolidándose después, así que reprocesamos los últimos días
# (idempotente) en vez de solo "ayer".
SYNC_WINDOW_DAYS = 5
GSC_LAG_DAYS = 2


def _window(today: datetime) -> tuple[str, str]:
    end = (today - timedelta(days=GSC_LAG_DAYS)).date()
    start = end - timedelta(days=SYNC_WINDOW_DAYS)
    return start.isoformat(), end.isoformat()


def run_one_config(db: Session, cfg, today: datetime | None = None) -> tuple[bool, str]:
    """Refresca UNA config sobre la ventana móvil. Actualiza last_status.
    Devuelve (ok, mensaje). No lanza: el cron debe seguir con las demás."""
    today = today or datetime.now(timezone.utc)
    start, end = _window(today)
    try:
        try:
            acc_uuid = uuid.UUID(str(cfg.account_id))
        except ValueError:
            raise RuntimeError(f"account_id inválido: {cfg.account_id}")
        if cfg.source == "gsc":
            acc = db.query(GscAccount).filter(GscAccount.id == acc_uuid).first()
            if not acc:
                raise RuntimeError("cuenta GSC no encontrada")
            n = do_sync_gsc(db, cfg.client_id, acc, cfg.property, start, end, cfg.by_page)
        else:
            acc = db.query(Ga4Account).filter(Ga4Account.id == acc_uuid).first()
            if not acc:
                raise RuntimeError("cuenta GA4 no encontrada")
            n = do_sync_ga4(db, cfg.client_id, acc, cfg.property, start, end)
        cfg.last_synced_at = today
        cfg.last_status = f"ok: {n} filas ({start}→{end})"
        db.commit()
        return True, cfg.last_status
    except Exception as e:  # noqa: BLE001
        db.rollback()
        cfg.last_status = f"error: {e}"
        cfg.last_synced_at = today
        try:
            db.commit()
        except Exception:  # pragma: no cover
            db.rollback()
        return False, str(e)


def run_daily_sync(today: datetime | None = None) -> dict:
    """Punto de entrada del cron: recorre TODAS las configs habilitadas y
    refresca cada una. Serializado entre réplicas por lock Redis (lo pone
    el planificador). Devuelve un resumen para el log."""
    from shared.database import SessionLocal
    from shared.semantic_models import MetricSyncConfig

    db = SessionLocal()
    done = {"ok": 0, "error": 0, "total": 0}
    try:
        cfgs = db.query(MetricSyncConfig).filter(
            MetricSyncConfig.enabled.is_(True)).all()
        done["total"] = len(cfgs)
        for cfg in cfgs:
            ok, _ = run_one_config(db, cfg, today=today)
            done["ok" if ok else "error"] += 1
    finally:
        db.close()
    return done


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
