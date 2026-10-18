"""`GET /api/jobs/{id}/veredicto` EJECUTADO con TestClient (decision 128).

Para un CI de despliegue: 200 siempre que el job exista, y el veredicto en el
cuerpo. Un `falla` o un `no_concluyente` con un 4xx/5xx se confundiria con que
la API se ha caido.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import Base, HtmlMeta, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


SEMILLAS = [f"https://x.com/clave{i}" for i in range(50)]
ANTES, MAL, BIEN, SOLO = (uuid.uuid4() for _ in range(4))


@pytest.fixture()
def api(tmp_path):
    from api.routers import results
    from shared.comparacion import comparar_con_el_anterior

    engine = create_engine(f"sqlite:///{tmp_path}/v.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    with Sesion() as s:
        def censo(jid, dia, noindex=0):
            j = Job(id=jid, name=str(dia), status="completed", finish_reason="finished",
                    seeds=SEMILLAS, config={"max_depth": 0}, crawler_version="abc",
                    started_at=datetime(2026, 1, dia))
            s.add(j)
            s.flush()
            for i, url in enumerate(SEMILLAS):
                u = Url(job_id=jid, url=url, url_hash=f"{jid}-{i}", is_internal=True,
                        is_html=True, status_code=200, word_count=500,
                        indexability_status="Noindex" if i < noindex else "Indexable")
                s.add(u)
                s.flush()
                s.add(HtmlMeta(url_id=u.id, title="T", canonical_href=url))
            s.flush()
            return j

        censo(ANTES, 1)
        for jid, dia, noindex in ((MAL, 2, 10), (BIEN, 3, 0)):
            j = censo(jid, dia, noindex)
            j.comparacion = comparar_con_el_anterior(s, j)
        s.add(Job(id=SOLO, name="solo", status="running", seeds=SEMILLAS,
                  config={"max_depth": 0}))
        s.commit()
    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        with Sesion() as s:
            yield s

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app)


def test_diez_a_noindex_es_falla_con_200(api):
    r = api.get(f"/api/jobs/{MAL}/veredicto")
    assert r.status_code == 200
    d = r.json()
    assert d["veredicto"] == "falla"
    assert {m["tipo"] for m in d["motivos"]} == {"alerta", "deja_de_ser_indexable"}


def test_el_control_sin_cambios_pasa(api):
    """BIEN se compara con MAL (el anterior): las 10 vuelven a ser indexables,
    que es una ENTRADA en el indice y no un fallo."""
    d = api.get(f"/api/jobs/{BIEN}/veredicto").json()
    assert d["veredicto"] == "pasa", d


def test_sin_terminar_es_no_concluyente_con_200(api):
    r = api.get(f"/api/jobs/{SOLO}/veredicto")
    assert r.status_code == 200 and r.json()["veredicto"] == "no_concluyente"


def test_un_job_que_no_existe_es_404(api):
    assert api.get(f"/api/jobs/{uuid.uuid4()}/veredicto").status_code == 404
