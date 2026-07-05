"""
Rendimiento en el tiempo (B3): evolución del proyecto por sus rastreos,
con scope de grupo (sitio / segmento / watchlist) y evolución por URL
vigilada.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def perf_tables(db_engine):
    pytest.importorskip("pgvector")
    from pgvector.sqlalchemy import Vector
    from sqlalchemy.ext.compiler import compiles

    @compiles(Vector, "sqlite")
    def _v(type_, compiler, **kw):
        return "TEXT"

    from shared.models import Segment, UrlSegment, WatchlistEntry
    from shared.semantic_models import GscJobData

    for m in (Segment, UrlSegment, WatchlistEntry, GscJobData):
        m.__table__.create(db_engine, checkfirst=True)
    return True


def _job(db_session, client_id, name, days_ago, fp="fp1"):
    from shared.models import Job

    j = Job(name=name, seeds=["https://toy.local/"], config={},
            status="completed", client_id=client_id,
            normalization_fingerprint=fp)
    db_session.add(j)
    db_session.flush()
    j.created_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    j.completed_at = j.created_at
    db_session.flush()
    return j


def _url(db_session, job, path, *, pagerank=None, indexable=True):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(job_id=job.id, url=full, url_hash=compute_url_hash(full),
            host="toy.local", path=path, scheme="https", is_internal=True,
            is_html=True, status_code=200, status_group="2xx",
            pagerank=pagerank, indexable=indexable)
    db_session.add(u)
    db_session.flush()
    return u


def _gsc(db_session, job, url, clicks, imprs, pos):
    from shared.semantic_models import GscJobData

    db_session.add(GscJobData(job_id=job.id, url_id=url.id, url=url.url,
                              url_hash=url.url_hash, clicks=clicks,
                              impressions=imprs, position=pos))
    db_session.flush()


def _tl(db_session, client_id, **kw):
    from api.routers.performance import performance_timeline

    kw.setdefault("segment_id", None)
    kw.setdefault("watchlist", False)
    return performance_timeline(client_id, db=db_session, **kw)


def _summary(db_session, client_id, **kw):
    from api.routers.performance import performance_summary

    kw.setdefault("segment_id", None)
    kw.setdefault("watchlist", False)
    return performance_summary(client_id, db=db_session, **kw)


def _scenario(db_session, perf_tables):
    # dos runs del cliente 'cli': hace un año y ahora
    old = _job(db_session, "cli", "run-viejo", 365)
    new = _job(db_session, "cli", "run-nuevo", 1)
    urls = {}
    for job, mult in ((old, 1), (new, 2)):
        serv = _url(db_session, job, "/servicios/seo", pagerank=2.0)
        blog = _url(db_session, job, "/blog/post", pagerank=1.0)
        _gsc(db_session, job, serv, 100 * mult, 1000 * mult, 8.0 - mult)
        _gsc(db_session, job, blog, 10 * mult, 500 * mult, 20.0)
        urls[(job.name, "serv")] = serv
    return old, new, urls


def test_timeline_site(db_session, perf_tables):
    old, new, _ = _scenario(db_session, perf_tables)
    r = _tl(db_session, "cli")
    assert r["status"] == "ok" and len(r["points"]) == 2
    assert r["scope"]["kind"] == "site"
    # asc por fecha: viejo primero
    assert r["points"][0]["name"] == "run-viejo"
    # sitio entero: clics = servicios + blog
    assert r["points"][1]["metrics"]["gsc_clicks"] == 200 + 20
    # posición media ponderada por impresiones
    assert r["points"][0]["metrics"]["gsc_position"] is not None


def test_timeline_segment_scope(db_session, perf_tables):
    from shared.models import Segment, UrlSegment

    old, new, urls = _scenario(db_session, perf_tables)
    seg = Segment(client_id="cli", name="Servicios", rule_type="prefix",
                  rule="/servicios/", priority=1)
    db_session.add(seg)
    db_session.flush()
    # asigna la URL de servicios de cada job al segmento
    for job in (old, new):
        u = db_session.query(type(urls[("run-viejo", "serv")])).filter_by(
            job_id=job.id, path="/servicios/seo").one()
        db_session.add(UrlSegment(job_id=job.id, url_id=u.id, segment_id=seg.id))
    db_session.flush()

    r = _tl(db_session, "cli", segment_id=seg.id)
    assert r["scope"]["kind"] == "segment"
    # solo servicios: clics del run nuevo = 200 (no incluye el blog)
    assert r["points"][1]["metrics"]["gsc_clicks"] == 200
    assert r["points"][1]["metrics"]["urls_total"] == 1


def test_timeline_watchlist_scope(db_session, perf_tables):
    from shared.models import WatchlistEntry
    from shared.url_normalization import compute_url_hash

    old, new, _ = _scenario(db_session, perf_tables)
    db_session.add(WatchlistEntry(
        client_id="cli", url="https://toy.local/servicios/seo",
        url_hash=compute_url_hash("https://toy.local/servicios/seo"),
        label="Servicio clave"))
    db_session.flush()

    r = _tl(db_session, "cli", watchlist=True)
    assert r["scope"]["kind"] == "watchlist"
    assert r["points"][1]["metrics"]["gsc_clicks"] == 200  # solo la vigilada
    assert r["points"][1]["metrics"]["urls_total"] == 1


def test_summary_delta(db_session, perf_tables):
    _scenario(db_session, perf_tables)
    r = _summary(db_session, "cli")
    assert r["status"] == "ok"
    # clics del sitio: 220 (nuevo) vs 110 (viejo) → +110
    assert r["metricas"]["gsc_clicks"]["actual"] == 220
    assert r["metricas"]["gsc_clicks"]["referencia"] == 110
    assert r["metricas"]["gsc_clicks"]["delta"] == 110


def test_summary_blocked_with_one_run(db_session, perf_tables):
    _job(db_session, "cli", "solo-uno", 1)
    r = _summary(db_session, "cli")
    assert r["status"] == "blocked" and r["reason"] == "hacen_falta_2_runs"


def test_watchlist_timeline_per_url(db_session, perf_tables):
    from api.routers.performance import watchlist_timeline
    from shared.models import WatchlistEntry
    from shared.url_normalization import compute_url_hash

    _scenario(db_session, perf_tables)
    db_session.add(WatchlistEntry(
        client_id="cli", url="https://toy.local/servicios/seo",
        url_hash=compute_url_hash("https://toy.local/servicios/seo"),
        label="Servicio"))
    db_session.add(WatchlistEntry(
        client_id="cli", url="https://toy.local/inexistente",
        url_hash=compute_url_hash("https://toy.local/inexistente"),
        label="Sin datos"))
    db_session.flush()

    r = watchlist_timeline("cli", db=db_session)
    assert r["status"] == "ok" and len(r["urls"]) == 2
    serv = next(u for u in r["urls"] if u["label"] == "Servicio")
    assert serv["tiene_datos"] is True
    assert len(serv["serie"]) == 2
    assert serv["serie"][0]["clicks"] == 100 and serv["serie"][1]["clicks"] == 200
    sin = next(u for u in r["urls"] if u["label"] == "Sin datos")
    assert sin["tiene_datos"] is False


def test_timeline_blocked_no_runs(db_session, perf_tables):
    r = _tl(db_session, "otro-cliente")
    assert r["status"] == "blocked" and r["reason"] == "sin_runs_completados"
