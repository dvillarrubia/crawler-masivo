"""
Cron de sincronización diaria (GSC/GA4): CRUD de configuraciones y la
tanda que refresca la ventana móvil. El fetch a Google se mockea — aquí se
prueba el orquestador, no la API externa.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.fixture()
def cron_tables(db_engine):
    pytest.importorskip("pgvector")
    from pgvector.sqlalchemy import Vector
    from sqlalchemy.ext.compiler import compiles

    @compiles(Vector, "sqlite")
    def _v(type_, compiler, **kw):
        return "TEXT"

    from shared.semantic_models import (
        Ga4Account, Ga4Daily, GscAccount, GscDaily, MetricSyncConfig,
    )
    for m in (GscAccount, Ga4Account, GscDaily, Ga4Daily, MetricSyncConfig):
        m.__table__.create(db_engine, checkfirst=True)
    return True


def _gsc_account(db_session):
    from shared.semantic_models import GscAccount

    a = GscAccount(name="cli GSC", credentials_json={"type": "service_account"})
    db_session.add(a)
    db_session.flush()
    return a


def _cfg(db_session, client_id, source, account_id, prop, enabled=True):
    from api.routers.metrics import upsert_sync_config, SyncConfigCreate

    body = SyncConfigCreate(source=source, account_id=str(account_id),
                            property=prop, by_page=True, enabled=enabled)
    return upsert_sync_config(client_id, body, db=db_session)


def test_upsert_is_idempotent_per_property(db_session, cron_tables):
    from api.routers.metrics import list_sync_configs

    acc = _gsc_account(db_session)
    _cfg(db_session, "cli", "gsc", acc.id, "https://x.com/")
    _cfg(db_session, "cli", "gsc", acc.id, "https://x.com/")  # misma → actualiza
    rows = list_sync_configs("cli", db=db_session)
    assert len(rows) == 1  # no duplica


def test_run_one_config_replaces_window(db_session, cron_tables, monkeypatch):
    import pandas as pd

    import POC_centro_semantico.src.gsc as gsc_mod
    from api.routers.metrics import run_one_config
    from shared.semantic_models import GscDaily

    acc = _gsc_account(db_session)
    cfg = _cfg(db_session, "cli", "gsc", acc.id, "https://x.com/")

    # el fetch devuelve 3 días fijos, ignorando el rango pedido
    def fake_fetch(creds, prop, start, end, by_page=False):
        return pd.DataFrame([
            {"date": "2026-06-25", "url": "https://x.com/a", "clicks": 5,
             "impressions": 50, "position": 3.0},
            {"date": "2026-06-26", "url": "https://x.com/a", "clicks": 7,
             "impressions": 70, "position": 4.0},
            {"date": "2026-06-27", "url": "https://x.com/a", "clicks": 9,
             "impressions": 90, "position": 5.0},
        ])
    monkeypatch.setattr(gsc_mod, "fetch_gsc_daily", fake_fetch)

    today = datetime(2026, 6, 30, tzinfo=timezone.utc)
    ok, msg = run_one_config(db_session, cfg, today=today)
    assert ok and "3 filas" in msg
    assert db_session.query(GscDaily).count() == 3
    assert cfg.last_status.startswith("ok")
    assert cfg.last_synced_at is not None

    # segunda pasada: reemplaza, no acumula
    ok2, _ = run_one_config(db_session, cfg, today=today)
    assert ok2 and db_session.query(GscDaily).count() == 3


def test_run_one_config_records_error(db_session, cron_tables, monkeypatch):
    import POC_centro_semantico.src.gsc as gsc_mod
    from api.routers.metrics import run_one_config

    acc = _gsc_account(db_session)
    cfg = _cfg(db_session, "cli", "gsc", acc.id, "https://x.com/")

    def boom(*a, **k):
        raise RuntimeError("403 sin acceso a la propiedad")
    monkeypatch.setattr(gsc_mod, "fetch_gsc_daily", boom)

    ok, msg = run_one_config(db_session, cfg)
    assert ok is False
    assert "403" in msg
    assert cfg.last_status.startswith("error")


def test_run_daily_sync_skips_disabled(db_session, cron_tables, monkeypatch):
    import pandas as pd

    import POC_centro_semantico.src.gsc as gsc_mod
    import api.routers.metrics as metrics_mod

    acc = _gsc_account(db_session)
    _cfg(db_session, "cli", "gsc", acc.id, "https://on.com/", enabled=True)
    _cfg(db_session, "cli", "gsc", acc.id, "https://off.com/", enabled=False)
    db_session.commit()

    calls = []

    def fake_fetch(creds, prop, start, end, by_page=False):
        calls.append(prop)
        return pd.DataFrame([{"date": "2026-06-27", "url": prop + "a",
                              "clicks": 1, "impressions": 10, "position": 2.0}])
    monkeypatch.setattr(gsc_mod, "fetch_gsc_daily", fake_fetch)
    # run_daily_sync abre su propia sesión: la apuntamos a la de test
    monkeypatch.setattr(metrics_mod, "SessionLocal", lambda: db_session,
                        raising=False)
    # SessionLocal se importa dentro de la función desde shared.database
    import shared.database as dbmod
    monkeypatch.setattr(dbmod, "SessionLocal", lambda: _NoCloseSession(db_session))

    summary = metrics_mod.run_daily_sync(today=datetime(2026, 6, 30, tzinfo=timezone.utc))
    assert summary["total"] == 1        # solo la habilitada
    assert summary["ok"] == 1
    assert calls == ["https://on.com/"]  # off.com no se tocó


class _NoCloseSession:
    """Envuelve la sesión de test para que run_daily_sync pueda llamar
    .close() sin cerrar la sesión compartida del fixture."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        pass
