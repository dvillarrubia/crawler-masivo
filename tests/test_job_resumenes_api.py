"""Los resumenes JSON del job llegan a `GET /api/jobs/{id}` (decisiones 105,
110, 123 y 124), EJECUTADO con TestClient contra SQLite.

`jobs.soft404`, `jobs.tls` y `jobs.sondas_resumen` se escribian y no salian en
la respuesta: Pydantic tira lo que `JobResponse` no declara (decision 33), asi
que el aviso «comprobados < distintos» de las sondas solo se podia leer con
psql. Comprobado en produccion el 2026-10-18 con un canario de sondas."""

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
from shared.models import Base, Job  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


SONDAS = {"enlaces_externos": {"distintos": 451, "comprobados": 212, "rotos": 0,
                               "dominios_muertos": 0},
          "recursos": {"comprobados": 16, "rotos": 0}}
TLS = {"hosts": {"x.com": {"verificado": True, "dias_restantes": 40.5}}}
SOFT404 = {"sondas": {"x.com": {"status_code": 404}}}
SITEMAPS = {"ficheros": [{"url": "https://x.com/sitemap.xml", "status": 200,
                          "formato": "urlset", "declaradas": 3, "devueltas": 3}],
            "pedidos": 1, "leidos": 1, "parcial": False, "entradas_recortadas": False}
CON, SIN = uuid.uuid4(), uuid.uuid4()


@pytest.fixture()
def api(tmp_path):
    from api.routers import jobs

    engine = create_engine(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    with Sesion() as s:
        s.add(Job(id=CON, name="con", status="completed", seeds=["https://x.com/"],
                  config={}, sondas_resumen=SONDAS, tls=TLS, soft404=SOFT404,
                  sitemaps=SITEMAPS))
        s.add(Job(id=SIN, name="sin", status="completed", seeds=["https://x.com/"],
                  config={}))
        s.commit()
    app = FastAPI()
    app.include_router(jobs.router)

    def _sesion():
        with Sesion() as s:
            yield s

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app)


def test_la_ficha_del_job_trae_los_resumenes(api):
    d = api.get(f"/api/jobs/{CON}").json()
    assert d["sondas_resumen"] == SONDAS
    assert d["tls"] == TLS
    assert d["soft404"] == SOFT404


def test_sin_resumen_es_null_no_un_dict_vacio(api):
    """NULL = analisis anterior o sin sondas: no se convierte en «0 rotos»."""
    d = api.get(f"/api/jobs/{SIN}").json()
    assert d["sondas_resumen"] is None and d["tls"] is None and d["soft404"] is None


def test_los_sitemaps_van_en_la_ficha_y_no_en_la_lista(api):
    """`jobs.sitemaps` (decision 126) es una fila por fichero: en la ficha si,
    en cada fila de la lista de rastreos no (la lista no lo pinta)."""
    assert api.get(f"/api/jobs/{CON}").json()["sitemaps"] == SITEMAPS
    assert api.get(f"/api/jobs/{SIN}").json()["sitemaps"] is None
    filas = api.get("/api/jobs").json()["items"]
    assert filas and all("sitemaps" not in f for f in filas)
    # Renombrar devuelve la ficha: la interfaz la pone en `this.job` y no puede
    # perder la tarjeta de sitemaps por haber editado el nombre.
    assert api.patch(f"/api/jobs/{CON}", json={"name": "otro"}).json()["sitemaps"] == SITEMAPS
