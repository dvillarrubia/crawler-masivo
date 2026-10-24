"""Topes por carpeta en la API (decision 172), EJECUTADO contra SQLite: el
informe dice que partes se quedaron sin rastrear entero, y la ficha del job
devuelve el recuento."""

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
from shared.models import Base, Job, Url  # noqa: E402

TOPES = {
    "version": 1,
    "reglas": [{"regla": "*", "tope": 300, "grupos": 4, "grupos_cortados": 1,
                "rastreadas": 700, "sin_rastrear": 1234, "lista_completa": True}],
    "cortados": [{"regla": "*", "grupo": "blogs.x.com/economia/", "tope": 300,
                  "rastreadas": 300, "sin_rastrear": 1234}],
    "sin_rastrear": 1234, "sin_rastrear_minimo": True, "recuento_aproximado": False,
}


@pytest.fixture()
def entorno(tmp_path):
    from api.routers import jobs, results

    engine = create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    ids = {}
    for nombre, motivo, topes in (("topado", "tope_por_carpeta", TOPES),
                                  ("sin_topes", "finished", None)):
        j = Job(id=uuid.uuid4(), name=nombre, status="completed", finish_reason=motivo,
                seeds=["https://blogs.x.com/"], config={"topes_por_carpeta": {"*": 300}},
                topes_carpeta=topes)
        s.add(j)
        s.flush()
        s.add(Url(job_id=j.id, url="https://blogs.x.com/", url_hash="h", host="blogs.x.com",
                  is_internal=True, is_html=True, status_code=200, status_group="2xx",
                  indexable=True, click_depth=0))
        ids[nombre] = j.id
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(results.router)
    app.include_router(jobs.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), ids


def test_el_informe_nombra_las_partes_topadas(entorno):
    api, ids = entorno
    d = api.get(f"/api/jobs/{ids['topado']}/informe").json()
    texto = " ".join(d["fiabilidad"])
    assert "al menos 1.234 URLs" in texto
    assert "blogs.x.com/economia/" in texto
    # Una sola frase: la generica de truncado no se repite encima.
    assert len(d["fiabilidad"]) == 1
    assert api.get(f"/api/jobs/{ids['sin_topes']}/informe").json()["fiabilidad"] == []


def test_la_ficha_devuelve_el_recuento_y_el_progreso_dice_truncado(entorno):
    api, ids = entorno
    ficha = api.get(f"/api/jobs/{ids['topado']}").json()
    assert ficha["topes_carpeta"]["sin_rastrear"] == 1234
    assert ficha["config"]["topes_por_carpeta"] == {"*": 300}
