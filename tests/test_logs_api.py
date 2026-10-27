"""Subir el log del servidor de un cliente (#35, decision 213), EJECUTADO con
TestClient sobre SQLite: el fichero al volumen, encolado, lo que se rechaza ya
y las tres que se guardan."""

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
from shared.models import Base, Client, LogImport  # noqa: E402
from test_logs_servidor import LOG  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint(tipo, compilador, **kw):
    return "INTEGER"


class Cola:
    def __init__(self):
        self.encolados = []

    def rpush(self, clave, valor):
        self.encolados.append((clave, str(valor)))


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    import api.dependencies as dependencias
    from api.routers import logs

    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    cola = Cola()
    monkeypatch.setattr(dependencias, "get_redis", lambda: cola)
    engine = create_engine(f"sqlite:///{tmp_path}/a.db")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(logs.router)

    def _sesion():
        s = S()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    with S() as s:
        c = Client(id=uuid.uuid4(), nombre="X", slug="x", dominios=["www.x.com"], config_defecto={}, integraciones=[])
        s.add(c)
        s.commit()
        cid = c.id
    return TestClient(app), S, cid, cola, tmp_path


def _subir(api, cid, contenido: bytes, **form):
    return api.post(f"/api/clients/{cid}/logs", files={"fichero": ("access.log", contenido, "text/plain")}, data=form)


def test_subir_guarda_el_fichero_y_lo_encola(entorno):
    api, S, cid, cola, tmp = entorno
    r = _subir(api, cid, LOG.encode())
    assert r.status_code == 201, r.text
    j = r.json()
    assert (j["estado"], j["host"], j["formato"]) == ("pendiente", "www.x.com", "combined")
    assert cola.encolados == [("logs:pending", str(j["id"]))]
    with S() as s:
        imp = s.get(LogImport, j["id"])
        assert open(imp.ruta_disco, "rb").read() == LOG.encode()
        assert imp.ruta_disco.startswith(str(tmp / "archivo" / "logs"))


def test_lo_que_se_rechaza_en_la_subida(entorno):
    api, _, cid, cola, _ = entorno
    assert _subir(api, cid, b"x", formato="propio", regex=r"(?P<ip>\S+)").status_code == 422
    assert _subir(api, cid, b"x", formato="otro").status_code == 422
    assert _subir(api, cid, b"").status_code == 422
    assert _subir(api, uuid.uuid4(), b"x").status_code == 404
    assert cola.encolados == []


def test_el_dominio_si_no_lo_tiene_el_cliente(entorno):
    api, S, _, _, _ = entorno
    with S() as s:
        c = Client(id=uuid.uuid4(), nombre="Y", slug="y", dominios=[], config_defecto={}, integraciones=[])
        s.add(c)
        s.commit()
        cid = c.id
    assert _subir(api, cid, b"x").status_code == 422
    assert _subir(api, cid, b"x", host="https://www.y.com/").json()["host"] == "www.y.com"


def test_se_guardan_las_tres_ultimas_y_se_borran_sus_ficheros(entorno):
    import os

    api, S, cid, _, _ = entorno
    ids = [_subir(api, cid, LOG.encode()).json()["id"] for _ in range(4)]
    with S() as s:
        assert sorted(i.id for i in s.query(LogImport)) == sorted(ids[1:])
    lista = api.get(f"/api/clients/{cid}/logs").json()
    assert [i["id"] for i in lista] == ids[:0:-1]
    ultimo = lista[0]
    with S() as s:
        ruta = s.get(LogImport, ultimo["id"]).ruta_disco
    assert api.delete(f"/api/clients/{cid}/logs/{ultimo['id']}").status_code == 204
    assert not os.path.exists(ruta)
