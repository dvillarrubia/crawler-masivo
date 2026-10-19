"""/insights cacheado en Redis (decision 138, ampliacion): la misma respuesta
sin recalcular, y otra en cuanto el censo se re-analiza."""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import Base, Issue, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


class RedisFalso(dict):
    def get(self, k):
        return super().get(k)

    def set(self, k, v, ex=None):
        self[k] = v


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    from api import dependencies
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/i.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    j = Job(id=uuid.uuid4(), name="censo", status="completed", seeds=["https://x.com/"], config={},
            analisis_version="abc")
    s.add(j)
    s.flush()
    u = Url(job_id=j.id, url="https://x.com/", url_hash="h", is_internal=True, is_html=True,
            status_code=200, status_group="2xx", indexable=True)
    s.add(u)
    s.commit()
    ids = (j.id, u.id)
    s.close()
    redis = RedisFalso()
    monkeypatch.setattr(dependencies, "redis_client", redis)
    llamadas = []
    original = results._calcular_insights
    monkeypatch.setattr(results, "_calcular_insights", lambda jid, db: llamadas.append(1) or original(jid, db))
    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), ids, Sesion, llamadas, redis


def test_la_segunda_vez_sale_de_la_cache_y_un_reanalisis_la_invalida(entorno):
    api, (jid, uid), Sesion, llamadas, redis = entorno
    a = api.get(f"/api/jobs/{jid}/insights").json()
    b = api.get(f"/api/jobs/{jid}/insights").json()
    assert a == b and len(llamadas) == 1 and len(redis) == 1
    with Sesion() as s:   # re-analisis: otras incidencias
        s.add(Issue(job_id=jid, url_id=uid, issue_type="missing_h1", severity="error", details={}))
        s.commit()
    c = api.get(f"/api/jobs/{jid}/insights").json()
    assert len(llamadas) == 2 and c["overall_score"] <= 79       # el tope por errores
    with Sesion() as s:   # o con otra version del codigo
        s.get(Job, jid).analisis_version = "def"
        s.commit()
    api.get(f"/api/jobs/{jid}/insights")
    assert len(llamadas) == 3


def test_sin_redis_se_calcula_igual(entorno, monkeypatch):
    from api import dependencies

    api, (jid, _), _, llamadas, _ = entorno
    monkeypatch.setattr(dependencies, "redis_client", None)
    assert api.get(f"/api/jobs/{jid}/insights").status_code == 200
    assert api.get(f"/api/jobs/{jid}/insights").status_code == 200
    assert len(llamadas) == 2
