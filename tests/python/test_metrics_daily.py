"""
Informe de rendimiento AGNÓSTICO a los rastreos (serie diaria GSC/GA4).

A diferencia de test_performance (un punto por rastreo), aquí el eje es la
fecha: se ingieren filas diarias a nivel de propiedad/cliente y el informe
las agrega por día/semana/mes y compara con el periodo anterior.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def metrics_tables(db_engine):
    pytest.importorskip("pgvector")
    from pgvector.sqlalchemy import Vector
    from sqlalchemy.ext.compiler import compiles

    @compiles(Vector, "sqlite")
    def _v(type_, compiler, **kw):
        return "TEXT"

    from shared.models import WatchlistEntry
    from shared.semantic_models import Ga4Daily, GscDaily

    for m in (GscDaily, Ga4Daily, WatchlistEntry):
        m.__table__.create(db_engine, checkfirst=True)
    return True


def _day(days_ago: int) -> datetime:
    base = datetime(2026, 6, 30, tzinfo=timezone.utc)
    return base - timedelta(days=days_ago)


def _gsc_daily(db_session, client_id, days_ago, clicks, imprs, pos,
               url_hash=None, url=None, prop="https://toy.local/"):
    from shared.semantic_models import GscDaily

    db_session.add(GscDaily(
        client_id=client_id, property=prop, date=_day(days_ago),
        url=url, url_hash=url_hash, clicks=clicks, impressions=imprs,
        position=pos))
    db_session.flush()


def _ga4_daily(db_session, client_id, days_ago, sessions, users, conv, rev,
               channel="Organic Search", prop="properties/123"):
    from shared.semantic_models import Ga4Daily

    db_session.add(Ga4Daily(
        client_id=client_id, property_id=prop, date=_day(days_ago),
        channel=channel, sessions=sessions, active_users=users,
        conversions=conv, revenue=rev))
    db_session.flush()


def _report(db_session, client_id, **kw):
    from api.routers.metrics import metrics_report

    kw.setdefault("date_from", _day(6).strftime("%Y-%m-%d"))
    kw.setdefault("date_to", _day(0).strftime("%Y-%m-%d"))
    kw.setdefault("granularity", "day")
    kw.setdefault("compare", "previous")
    kw.setdefault("source", "gsc")
    kw.setdefault("watchlist", False)
    return metrics_report(client_id, db=db_session, **kw)


def test_report_blocked_without_data(db_session, metrics_tables):
    r = _report(db_session, "vacio")
    assert r["status"] == "blocked" and r["reason"] == "sin_datos_gsc_diarios"


def test_report_gsc_daily_series_and_totals(db_session, metrics_tables):
    # 7 días con 10 clics/100 imprs cada uno, posición 5
    for d in range(7):
        _gsc_daily(db_session, "cli", d, 10, 100, 5.0)
    r = _report(db_session, "cli", compare="none")
    assert r["status"] == "ok" and r["source"] == "gsc"
    assert len(r["series"]) == 7                       # un bucket por día
    tot = r["comparacion"]
    assert tot["clicks"]["actual"] == 70
    assert tot["impressions"]["actual"] == 700
    assert tot["ctr"]["actual"] == 10.0               # 70/700 = 10%
    assert tot["position"]["actual"] == 5.0
    assert tot["position"]["lower_better"] is True


def test_report_position_weighted_by_impressions(db_session, metrics_tables):
    # dos días: uno con muchas impresiones en pos 2, otro pocas en pos 10
    _gsc_daily(db_session, "cli", 1, 5, 900, 2.0)
    _gsc_daily(db_session, "cli", 0, 1, 100, 10.0)
    r = _report(db_session, "cli", compare="none")
    # ponderada: (2*900 + 10*100) / 1000 = 2.8, no 6.0
    assert r["comparacion"]["position"]["actual"] == 2.8


def test_report_compare_previous_period(db_session, metrics_tables):
    # rango actual (días 0-6): 10 clics/día. Periodo anterior (días 7-13): 5/día
    for d in range(7):
        _gsc_daily(db_session, "cli", d, 10, 100, 5.0)
    for d in range(7, 14):
        _gsc_daily(db_session, "cli", d, 5, 100, 8.0)
    r = _report(db_session, "cli", compare="previous")
    c = r["comparacion"]["clicks"]
    assert c["actual"] == 70 and c["anterior"] == 35
    assert c["delta"] == 35
    assert r["rango_anterior"] is not None


def test_report_month_granularity(db_session, metrics_tables):
    from api.routers.metrics import metrics_report

    # dos meses distintos
    _gsc_daily(db_session, "cli", 40, 10, 100, 5.0)   # ~mayo
    _gsc_daily(db_session, "cli", 5, 20, 100, 5.0)    # junio
    r = metrics_report("cli", date_from=_day(60).strftime("%Y-%m-%d"),
                       date_to=_day(0).strftime("%Y-%m-%d"),
                       granularity="month", compare="none", source="gsc",
                       watchlist=False, db=db_session)
    assert len(r["series"]) == 2
    assert all("-" in s["bucket"] and len(s["bucket"]) == 7 for s in r["series"])


def test_report_watchlist_scope(db_session, metrics_tables):
    from shared.models import WatchlistEntry
    from shared.url_normalization import compute_url_hash

    watched = "https://toy.local/servicios/seo"
    wh = compute_url_hash(watched)
    # fila por-URL vigilada + fila por-URL no vigilada, mismos días
    for d in range(7):
        _gsc_daily(db_session, "cli", d, 10, 100, 5.0, url_hash=wh, url=watched)
        _gsc_daily(db_session, "cli", d, 99, 100, 5.0,
                   url_hash=compute_url_hash("https://toy.local/otra"),
                   url="https://toy.local/otra")
    db_session.add(WatchlistEntry(client_id="cli", url=watched,
                                  url_hash=wh, label="Servicio"))
    db_session.flush()
    r = _report(db_session, "cli", watchlist=True, compare="none")
    assert r["scope"]["kind"] == "watchlist"
    assert r["comparacion"]["clicks"]["actual"] == 70   # solo la vigilada


def test_report_ga4_source(db_session, metrics_tables):
    for d in range(7):
        _ga4_daily(db_session, "cli", d, 100, 80, 3.0, 150.0)
    r = _report(db_session, "cli", source="ga4", compare="none")
    assert r["status"] == "ok" and r["source"] == "ga4"
    assert r["comparacion"]["sessions"]["actual"] == 700
    assert r["comparacion"]["revenue"]["actual"] == 1050.0


def test_report_ga4_blocked_without_data(db_session, metrics_tables):
    r = _report(db_session, "cli", source="ga4")
    assert r["status"] == "blocked" and r["reason"] == "sin_datos_ga4"


def test_coverage_reports_ranges(db_session, metrics_tables):
    from api.routers.metrics import metrics_coverage

    _gsc_daily(db_session, "cli", 6, 10, 100, 5.0)
    _gsc_daily(db_session, "cli", 0, 10, 100, 5.0)
    _ga4_daily(db_session, "cli", 3, 50, 40, 1.0, 10.0)
    cov = metrics_coverage("cli", db=db_session)
    assert cov["gsc"]["filas"] == 2
    assert cov["gsc"]["desde"] == _day(6).date().isoformat()
    assert cov["gsc"]["hasta"] == _day(0).date().isoformat()
    assert cov["ga4"]["filas"] == 1
