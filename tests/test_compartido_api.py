"""Enlaces de solo lectura para el cliente (decision 143), EJECUTADOS con
TestClient y SQLite: el token se ve una vez, cada enlace solo ve los censos de
su cliente, y uno revocado o caducado es un 404 como uno que no existe."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from api.auth import requiere_autenticacion  # noqa: E402
from shared.database import get_session  # noqa: E402
from shared.models import Base, Client, EnlaceCliente, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture
def entorno(tmp_path):
    from api.routers import compartido, results

    engine = create_engine(f"sqlite:///{tmp_path}/e.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    acme = Client(nombre="Acme", slug="acme", dominios=[])
    otro = Client(nombre="Otro", slug="otro", dominios=[])
    s.add_all([acme, otro])
    s.flush()

    def job(cliente, nombre, dias, status="completed"):
        j = Job(id=uuid.uuid4(), name=nombre, status=status, seeds=["https://x.com/"], config={},
                cliente_id=cliente.id, finish_reason="finished",
                created_at=datetime(2026, 10, dias, tzinfo=timezone.utc),
                completed_at=datetime(2026, 10, dias, 5, tzinfo=timezone.utc))
        s.add(j)
        s.flush()
        s.add(Url(job_id=j.id, url="https://x.com/", url_hash=f"h{j.id}", is_internal=True, is_html=True,
                  status_code=200, status_group="2xx", indexable=True))
        return j

    ids = {"acme": acme.id, "nuevo": job(acme, "acme semanal", 14).id, "viejo": job(acme, "acme semanal", 7).id,
           "canario": job(acme, "CANARIO acme", 15).id, "fallido": job(acme, "acme roto", 16, "failed").id,
           "ajeno": job(otro, "otro semanal", 14).id}
    s.commit()
    s.close()
    app = FastAPI()
    app.include_router(compartido.router)
    app.include_router(results.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), ids, Sesion


def _crear(api, ids, **cuerpo):
    r = api.post(f"/api/clients/{ids['acme']}/enlaces", json=cuerpo or {"nombre": "Dirección de marketing"})
    assert r.status_code == 201
    return r.json()


def test_el_token_se_ve_una_vez_y_no_se_guarda(entorno):
    api, ids, Sesion = entorno
    e = _crear(api, ids)
    assert e["ruta"] == f"/c/{e['token']}" and len(e["token"]) >= 30
    lista = api.get(f"/api/clients/{ids['acme']}/enlaces").json()
    assert [x["prefijo"] for x in lista] == [e["token"][:6]] and "token" not in lista[0]
    with Sesion() as s:
        guardado = s.query(EnlaceCliente).one()
        assert e["token"] not in (guardado.token_hash, guardado.prefijo)


def test_solo_los_censos_de_su_cliente(entorno):
    api, ids, _ = entorno
    token = _crear(api, ids)["token"]
    portada = api.get(f"/api/publico/{token}").json()
    assert portada["cliente"] == {"nombre": "Acme"}
    assert [c["id"] for c in portada["censos"]] == [str(ids["nuevo"]), str(ids["viejo"])]   # sin canario ni fallido
    r = api.get(f"/api/publico/{token}/informe/{ids['nuevo']}")
    assert r.status_code == 200 and r.json()["job"]["id"] == str(ids["nuevo"])
    for otro in ("ajeno", "canario", "fallido"):
        assert api.get(f"/api/publico/{token}/informe/{ids[otro]}").status_code == 404


def test_revocado_caducado_o_inventado_es_un_404(entorno):
    api, ids, Sesion = entorno
    e = _crear(api, ids)
    assert api.delete(f"/api/clients/{ids['acme']}/enlaces/{e['id']}").status_code == 204
    assert api.get(f"/api/publico/{e['token']}").status_code == 404
    f = _crear(api, ids, dias=30)
    with Sesion() as s:
        fila = s.query(EnlaceCliente).filter(EnlaceCliente.prefijo == f["token"][:6]).one()
        fila.caduca_en = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.commit()
    assert api.get(f"/api/publico/{f['token']}").status_code == 404
    assert api.get("/api/publico/" + "x" * 32).status_code == 404
    assert api.get("/api/publico/corto").status_code == 404


def test_se_cuentan_los_usos(entorno):
    api, ids, _ = entorno
    token = _crear(api, ids)["token"]
    api.get(f"/api/publico/{token}")
    api.get(f"/api/publico/{token}")
    (fila,) = api.get(f"/api/clients/{ids['acme']}/enlaces").json()
    assert fila["usos"] == 2 and fila["ultimo_uso"]


@pytest.mark.parametrize("ruta, protegida", [
    ("/api/publico/abc", False),
    ("/api/publico/abc/informe/x", False),
    ("/api/clients/x/enlaces", True),
    ("/api/publicos", True),
])
def test_solo_lo_publico_se_salta_la_sesion(ruta, protegida):
    assert requiere_autenticacion(ruta) is protegida
