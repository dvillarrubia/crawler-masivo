"""Reglas de alerta del cliente y resumen semanal por la API (decision 135),
EJECUTADOS con TestClient y SQLite."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import Base, Client, CrawlProfile, Issue, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture
def entorno(tmp_path):
    from api.routers import clientes

    engine = create_engine(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    acme = Client(nombre="Acme", slug="acme", dominios=["x.com"])
    s.add(acme)
    s.flush()
    perfil = CrawlProfile(cliente_id=acme.id, nombre="Semanal", seeds=["https://x.com/"])
    s.add(perfil)
    s.flush()
    j = Job(id=uuid.uuid4(), name="acme semanal", status="completed", seeds=["https://x.com/"],
            config={}, cliente_id=acme.id, profile_id=perfil.id, finish_reason="finished",
            created_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
            completed_at=datetime(2026, 10, 14, 5, tzinfo=timezone.utc))
    s.add(j)
    s.flush()
    for i in range(30):
        roto = i < 25
        u = Url(job_id=j.id, url=f"https://x.com/fichas/{i}", url_hash=f"h{i}", is_internal=True,
                is_html=True, segmento="fichas", status_code=404 if roto else 200,
                status_group="4xx" if roto else "2xx", indexable=not roto)
        s.add(u)
        s.flush()
        if roto:
            s.add(Issue(job_id=j.id, url_id=u.id, issue_type="4xx_error", severity="error"))
    s.commit()
    ids = {"cliente": acme.id, "job": j.id}
    s.close()

    app = FastAPI()
    app.include_router(clientes.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), Sesion, ids


FICHAS = {"id": "fichas-rotas", "nombre": "Fichas rotas", "medida": "urls_4xx",
          "segmento": "fichas", "modo": "valor", "operador": ">", "umbral": 20,
          "severidad": "critical", "avisar": True}


def test_un_cliente_nuevo_ve_las_de_serie_y_el_catalogo(entorno):
    c, _S, ids = entorno
    r = c.get(f"/api/clients/{ids['cliente']}/reglas").json()
    assert r["guardadas"] is None
    assert r["efectivas"] == r["de_serie"]
    assert r["catalogo"]["segmentos"] == ["fichas"]
    assert "urls_4xx" in r["catalogo"]["medidas"]
    assert {x["regla"] for x in r["catalogo"]["comparacion"]} >= {"rotas", "salen_del_indice"}


def test_guardar_es_parcial_y_null_vuelve_a_las_de_serie(entorno):
    c, S, ids = entorno
    url = f"/api/clients/{ids['cliente']}/reglas"
    r = c.put(url, json={"propias": [FICHAS], "comparacion": {"rotas": {"min_paginas": 5}}})
    assert r.status_code == 200, r.text
    assert r.json()["efectivas"]["comparacion"]["rotas"]["min_paginas"] == 5
    with S() as s:
        assert s.get(Client, ids["cliente"]).reglas_alerta == {
            "comparacion": {"rotas": {"min_paginas": 5}},
            "propias": [{**FICHAS, "activa": True}]}
    assert c.put(url, json=None).json()["guardadas"] is None


def test_una_regla_mal_escrita_es_422_con_el_motivo(entorno):
    c, _S, ids = entorno
    r = c.put(f"/api/clients/{ids['cliente']}/reglas",
              json={"propias": [{**FICHAS, "operador": "=="}]})
    assert r.status_code == 422 and "operador" in r.json()["detail"]


def test_probar_evalua_lo_de_pantalla_sin_guardar(entorno):
    c, S, ids = entorno
    r = c.post(f"/api/clients/{ids['cliente']}/reglas/probar", json={"propias": [FICHAS]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["job_id"] == str(ids["job"])
    assert [a["id"] for a in d["disparadas"]] == ["fichas-rotas"]
    with S() as s:
        assert s.get(Client, ids["cliente"]).reglas_alerta is None


def test_vista_previa_y_lista_de_resumenes(entorno):
    c, _S, ids = entorno
    assert c.get(f"/api/clients/{ids['cliente']}/resumenes").json() == []
    r = c.get(f"/api/clients/{ids['cliente']}/resumenes/vista-previa")
    assert r.status_code == 200, r.text
    assert r.json()["texto"].startswith("*Acme*")
