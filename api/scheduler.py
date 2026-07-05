"""Cron de sincronización diaria de métricas (GSC/GA4).

Un BackgroundScheduler de APScheduler dentro del proceso de la API: una vez
al día dispara `run_daily_sync`, que recorre las configuraciones habilitadas
(`metric_sync_configs`) y refresca la ventana móvil reciente de cada una.

Robustez:
- Lock Redis (SET NX + TTL) alrededor de cada disparo, para que si hay varias
  réplicas de la API solo UNA ejecute la tanda (mismo patrón que el resto del
  proyecto). Sin Redis, se ejecuta igual (entorno de un solo proceso).
- La hora es configurable con METRICS_SYNC_HOUR (UTC, por defecto 05:00).
  METRICS_SYNC_ENABLED=0 lo desactiva por completo.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_scheduler = None
_LOCK_KEY = "cron:metrics_daily_sync"
_LOCK_TTL = 3600  # 1h: de sobra para una tanda; se auto-libera si algo peta


def _guarded_run() -> None:
    """Ejecuta la tanda diaria si conseguimos el lock (una réplica gana)."""
    import api.dependencies as deps

    redis = getattr(deps, "redis_client", None)
    token = None
    if redis is not None:
        try:
            token = redis.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL)
            if not token:
                log.info("Cron métricas: otra réplica tiene el lock, se omite")
                return
        except Exception as exc:  # pragma: no cover - Redis caído
            log.warning("Cron métricas: Redis no disponible (%s), sigo igual", exc)

    try:
        from api.routers.metrics import run_daily_sync

        summary = run_daily_sync()
        log.info("Cron métricas: %s configs (%s ok, %s error)",
                 summary["total"], summary["ok"], summary["error"])
    except Exception as exc:  # noqa: BLE001
        log.exception("Cron métricas: fallo en la tanda: %s", exc)
    finally:
        if redis is not None and token:
            try:
                redis.delete(_LOCK_KEY)
            except Exception:  # pragma: no cover
                pass


def start_scheduler() -> None:
    """Arranca el cron. Idempotente y tolerante: si APScheduler no está
    instalado o algo falla, la API sigue funcionando sin cron (se avisa)."""
    global _scheduler
    if os.getenv("METRICS_SYNC_ENABLED", "1") == "0":
        log.info("Cron métricas desactivado (METRICS_SYNC_ENABLED=0)")
        return
    if _scheduler is not None:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except Exception as exc:  # pragma: no cover - falta la lib
        log.warning("APScheduler no disponible (%s): sin cron de métricas", exc)
        return

    hour = int(os.getenv("METRICS_SYNC_HOUR", "5"))
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(_guarded_run, CronTrigger(hour=hour, minute=0),
                  id="metrics_daily_sync", replace_existing=True,
                  misfire_grace_time=3600, coalesce=True)
    sched.start()
    _scheduler = sched
    log.info("Cron métricas arrancado: diario a las %02d:00 UTC", hour)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:  # pragma: no cover
            pass
        _scheduler = None
