"""La capa de decisiones en la API (#7): presupuesto mensual del cliente y
observabilidad en `GET /api/jobs/{id}`. Ejecutado con TestClient y SQLite,
reutilizando el montaje de `test_clientes.py`."""

from __future__ import annotations

import io
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from shared.models import Client, Job  # noqa: E402
from test_clientes import _app, _backup, _motor  # noqa: E402


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    Sesion = _motor(tmp_path)
    return TestClient(_app(Sesion, monkeypatch)), Sesion


def test_presupuesto_mensual_del_cliente(entorno):
    api, _ = entorno
    r = api.post("/api/clients", json={"nombre": "Acme",
                                       "decisiones": {"presupuesto_mensual_usd": 5}})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["decisiones"] == {"presupuesto_mensual_usd": 5.0}
    r = api.patch(f"/api/clients/{c['id']}", json={"decisiones": {"presupuesto_mensual_usd": 2.5}})
    assert r.json()["decisiones"] == {"presupuesto_mensual_usd": 2.5}
    # Quitarlo deja al cliente sin tope propio (manda el global).
    r = api.patch(f"/api/clients/{c['id']}", json={"decisiones": None})
    assert r.json()["decisiones"] is None


@pytest.mark.parametrize("cuerpo", [
    {"presupuesto_mensual_usd": -1},
    # Una clave desconocida es un 422 que la nombra, no un ajuste que se
    # pierde en silencio (decision 33).
    {"presupuesto_mensual": 5},
    # Y la clave de OpenRouter NO va aqui (decision 84).
    {"api_key": "sk-x"},
])
def test_decisiones_del_cliente_invalidas(entorno, cuerpo):
    api, _ = entorno
    r = api.post("/api/clients", json={"nombre": "Acme", "decisiones": cuerpo})
    assert r.status_code == 422


def test_el_job_expone_el_resumen_de_decisiones(entorno):
    api, Sesion = entorno
    j = api.post("/api/jobs", json={"name": "j", "seeds": ["https://x.com/"]}).json()
    assert api.get(f"/api/jobs/{j['id']}").json()["decisiones_resumen"] is None
    s = Sesion()
    job = s.get(Job, uuid.UUID(j["id"]))
    job.decisiones_resumen = {"llamadas": 3, "coste_usd": 0.0012, "aciertos_cache": 7,
                              "n_429": 1, "estado": "ok"}
    s.commit()
    s.close()
    r = api.get(f"/api/jobs/{j['id']}").json()["decisiones_resumen"]
    assert r["llamadas"] == 3 and r["aciertos_cache"] == 7 and r["n_429"] == 1


def test_el_presupuesto_viaja_en_el_backup(entorno, tmp_path, monkeypatch):
    from api.backup import import_backup_zip

    api, Sesion = entorno
    c = api.post("/api/clients", json={"nombre": "Acme",
                                       "decisiones": {"presupuesto_mensual_usd": 4}}).json()
    j = api.post("/api/jobs", json={"name": "j", "cliente_id": c["id"],
                                    "seeds": ["https://acme.com/"]}).json()
    datos = _backup(Sesion, monkeypatch, j["id"])
    Otra = _motor(tmp_path, "otra.db")
    s = Otra()
    r = import_backup_zip(io.BytesIO(datos), False, s)
    cliente = s.get(Client, s.get(Job, r["new_job_id"]).cliente_id)
    assert cliente.decisiones == {"presupuesto_mensual_usd": 4.0}
    s.close()
