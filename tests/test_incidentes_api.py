"""Incidentes por la API (decision 139), EJECUTADOS con TestClient y SQLite:
listar, marcar visto, silenciar, no cerrar a mano, y el recuento del panel."""

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
from shared.models import Base, Client, Incidente  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture
def entorno(tmp_path):
    from api.routers import clientes

    engine = create_engine(f"sqlite:///{tmp_path}/i.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    acme = Client(nombre="Acme", slug="acme", dominios=["x.com"])
    otro = Client(nombre="Otro", slug="otro", dominios=["y.com"])
    s.add_all([acme, otro])
    s.flush()
    t = datetime(2026, 10, 12, tzinfo=timezone.utc)

    def inc(cliente, clave, estado="abierto", severidad="critical"):
        i = Incidente(cliente_id=cliente.id, serie=f"perfil:{cliente.slug}", nombre_serie="semanal", clave=clave,
                      regla=clave, severidad=severidad, titulo=clave, estado=estado,
                      detalle={"regla": clave}, abierto_en=t, actualizado_en=t)
        s.add(i)
        s.flush()
        return i

    ids = {"acme": acme.id, "otro": otro.id,
           "rotas": inc(acme, "rotas").id,
           "desc": inc(acme, "cambia_la_description", severidad="warning").id,
           "viejo": inc(acme, "salen_del_indice", estado="cerrado").id,
           "ajeno": inc(otro, "rotas").id}
    s.commit()
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
    return TestClient(app), ids


def test_lista_los_vivos_y_los_cerrados_aparte(entorno):
    api, ids = entorno
    vivos = api.get(f"/api/clients/{ids['acme']}/incidentes").json()
    assert [i["clave"] for i in vivos] == ["rotas", "cambia_la_description"]   # criticos primero
    cerrados = api.get(f"/api/clients/{ids['acme']}/incidentes?estado=cerrados").json()
    assert [i["clave"] for i in cerrados] == ["salen_del_indice"]


def test_marcar_visto_y_silenciar(entorno):
    api, ids = entorno
    r = api.patch(f"/api/clients/{ids['acme']}/incidentes/{ids['rotas']}",
                  json={"estado": "visto", "silenciado_hasta": "2026-10-31"})
    assert r.status_code == 200
    d = r.json()
    assert (d["estado"], d["silenciado_hasta"]) == ("visto", "2026-10-31") and d["visto_en"]
    d = api.patch(f"/api/clients/{ids['acme']}/incidentes/{ids['rotas']}",
                  json={"estado": "abierto", "silenciado_hasta": None}).json()
    assert (d["estado"], d["silenciado_hasta"], d["visto_en"]) == ("abierto", None, None)


def test_cerrar_lo_decide_un_rastreo_no_una_persona(entorno):
    api, ids = entorno
    assert api.patch(f"/api/clients/{ids['acme']}/incidentes/{ids['rotas']}",
                     json={"estado": "cerrado"}).status_code == 422
    assert api.patch(f"/api/clients/{ids['acme']}/incidentes/{ids['viejo']}",
                     json={"estado": "visto"}).status_code == 409


def test_no_se_toca_el_incidente_de_otro_cliente(entorno):
    api, ids = entorno
    assert api.patch(f"/api/clients/{ids['acme']}/incidentes/{ids['ajeno']}",
                     json={"estado": "visto"}).status_code == 404
    assert api.patch(f"/api/clients/{ids['acme']}/incidentes/{uuid.uuid4()}",
                     json={"estado": "visto"}).status_code == 404


def test_el_panel_cuenta_los_vivos(entorno):
    api, ids = entorno
    panel = {c["slug"]: c for c in api.get("/api/clients/panel").json()["clientes"]}
    assert (panel["acme"]["incidentes_vivos"], panel["otro"]["incidentes_vivos"]) == (2, 1)
