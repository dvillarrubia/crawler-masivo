"""Los incidentes de una serie que ya no va a rastrear nadie se cierran
(decision 188): al pausar o borrar su programacion, o al borrar su perfil.
Antes se quedaban abiertos para siempre: solo los cierra un rastreo de su
serie. EJECUTADO con TestClient y SQLite."""

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
from shared.models import Base, Client, CrawlProfile, Incidente, Programacion  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


T = datetime(2026, 10, 12, tzinfo=timezone.utc)


@pytest.fixture
def entorno(tmp_path):
    from api.routers import clientes, programaciones

    engine = create_engine(f"sqlite:///{tmp_path}/i.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    acme = Client(nombre="Acme", slug="acme", dominios=["x.com"])
    s.add(acme)
    s.flush()
    perfil = CrawlProfile(cliente_id=acme.id, nombre="principal", seeds=["https://x.com/"])
    s.add(perfil)
    s.flush()

    def prog(nombre, perfil_id=None):
        p = Programacion(id=uuid.uuid4(), nombre=nombre, cliente_id=acme.id, profile_id=perfil_id,
                         seeds=["https://x.com/"], config={}, cron="0 3 * * 1",
                         zona_horaria="Europe/Madrid", activa=True)
        s.add(p)
        s.flush()
        return p

    sola, con_perfil = prog("sola"), prog("con perfil", perfil.id)

    def inc(serie, clave, estado="abierto"):
        i = Incidente(cliente_id=acme.id, serie=serie, nombre_serie="x", clave=clave, regla=clave,
                      severidad="warning", titulo=clave, estado=estado, detalle={}, abierto_en=T,
                      actualizado_en=T)
        s.add(i)
        s.flush()
        return i.id

    ids = {"acme": acme.id, "perfil": perfil.id, "sola": sola.id, "con_perfil": con_perfil.id,
           "de_sola": inc(f"programacion:{sola.id}", "rotas"),
           "de_sola_visto": inc(f"programacion:{sola.id}", "salen", "visto"),
           "de_perfil": inc(f"perfil:{perfil.id}", "rotas")}
    s.commit()
    s.close()
    app = FastAPI()
    app.include_router(clientes.router)
    app.include_router(programaciones.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), Sesion, ids


def _estado(Sesion, iid):
    with Sesion() as s:
        i = s.get(Incidente, iid)
        return i.estado, i.motivo_cierre, i.cerrado_job_id


def test_pausar_la_programacion_cierra_sus_incidentes(entorno):
    api, Sesion, ids = entorno
    assert api.patch(f"/api/programaciones/{ids['sola']}", json={"activa": False}).status_code == 200
    assert _estado(Sesion, ids["de_sola"]) == ("cerrado", "serie_pausada", None)
    assert _estado(Sesion, ids["de_sola_visto"])[:2] == ("cerrado", "serie_pausada")
    assert _estado(Sesion, ids["de_perfil"])[0] == "abierto"
    # Y la interfaz lo recibe: cerrado, con su motivo, sin rastreo que lo cerrara.
    cerrados = api.get(f"/api/clients/{ids['acme']}/incidentes?estado=cerrados").json()
    assert {(i["clave"], i["motivo_cierre"]) for i in cerrados} == {("rotas", "serie_pausada"),
                                                                   ("salen", "serie_pausada")}


def test_otro_cambio_sin_pausar_no_cierra_nada(entorno):
    api, Sesion, ids = entorno
    assert api.patch(f"/api/programaciones/{ids['sola']}", json={"nombre": "otra"}).status_code == 200
    assert api.patch(f"/api/programaciones/{ids['sola']}", json={"activa": True}).status_code == 200
    assert _estado(Sesion, ids["de_sola"])[0] == "abierto"


def test_una_programacion_con_perfil_no_cierra_la_serie_del_perfil(entorno):
    """El perfil se puede seguir lanzando a mano: su serie sigue viva."""
    api, Sesion, ids = entorno
    assert api.patch(f"/api/programaciones/{ids['con_perfil']}", json={"activa": False}).status_code == 200
    assert api.delete(f"/api/programaciones/{ids['con_perfil']}").status_code == 204
    assert _estado(Sesion, ids["de_perfil"])[0] == "abierto"


def test_borrar_la_programacion_o_el_perfil_cierra_los_suyos(entorno):
    api, Sesion, ids = entorno
    assert api.delete(f"/api/programaciones/{ids['sola']}").status_code == 204
    assert _estado(Sesion, ids["de_sola"]) == ("cerrado", "serie_borrada", None)
    assert api.delete(f"/api/clients/{ids['acme']}/profiles/{ids['perfil']}").status_code == 204
    assert _estado(Sesion, ids["de_perfil"]) == ("cerrado", "serie_borrada", None)
