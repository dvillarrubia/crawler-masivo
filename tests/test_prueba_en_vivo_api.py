"""`POST /api/extraccion/probar` con una URL en vivo (#34, decision 166),
EJECUTADO con TestClient: la API valida, encola en Redis y espera; no
descarga nada.

El Redis es un doble con la semantica que se usa (`rpush` a la cola, `blpop`
de la respuesta) y un «worker» que contesta lo que diga cada test: lo que
descarga el worker y sus comprobaciones de SSRF estan en
`test_prueba_en_vivo.py`, que necesita curl_cffi (no esta en la imagen de la
API, y a proposito: la API no descarga).
"""

from __future__ import annotations

import json
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("lxml")
pytest.importorskip("regex")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared import prueba_en_vivo as pv  # noqa: E402
from shared.database import get_session  # noqa: E402

REGLAS = {"custom_extraction": {"precio": {"tipo": "css", "expresion": ".precio",
                                           "valor": "numero"}}}


class RedisDoble:
    def __init__(self, contestar=None):
        self.listas: dict[str, list[str]] = {}
        self.contestar = contestar          # peticion -> resultado | None

    def rpush(self, clave, valor):
        self.listas.setdefault(clave, []).append(valor)

    def expire(self, clave, s):
        pass

    def blpop(self, clave, timeout=0):
        cola = self.listas.get(pv.COLA_PRUEBAS) or []
        while cola and self.contestar is not None:
            peticion = json.loads(cola.pop(0))
            resultado = self.contestar(peticion)
            if resultado is not None:
                pv.responder(self, peticion["id"], resultado)
        lista = self.listas.get(clave) or []
        return (clave, lista.pop(0)) if lista else None


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from api import dependencies
    from api.routers import extraccion

    engine = create_engine(f"sqlite:///{tmp_path}/v.db")
    Sesion = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(extraccion.router)

    def _sesion():
        s = Sesion()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    doble = RedisDoble()
    monkeypatch.setattr(dependencies, "redis_client", doble)
    monkeypatch.setattr(pv, "ESPERA_S", 1)
    return TestClient(app), doble


def test_la_api_encola_y_devuelve_lo_que_contesta_el_worker(api):
    cliente, doble = api
    vistas = []

    def worker(p):
        vistas.append(p)
        return {"ok": True, "descarga": {"url": p["url"], "url_final": p["url"], "status": 200,
                                         "saltos": []},
                "reglas": [{"nombre": "precio", "estado": "ok", "valor": "19,95 €",
                            "valor_num": 19.95}],
                "no_evaluadas": [], "avisos": ["aviso del worker"], "ms": 1.0}

    doble.contestar = worker
    r = cliente.post("/api/extraccion/probar", json={
        "url": "https://tienda.example.com/p/1", **REGLAS, "user_agent": "Mozilla/5.0 prueba"})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["reglas"][0]["valor_num"] == 19.95
    assert cuerpo["fuente"].startswith("URL en vivo https://tienda.example.com/p/1")
    assert cuerpo["avisos"] == ["aviso del worker"] and cuerpo["render"] is False
    (p,) = vistas
    # Lo que viaja al worker: la URL y la config resuelta, validada aqui.
    assert p["config"]["custom_extraction"]["precio"]["tipo"] == "css"
    assert p["config"]["user_agent"] == "Mozilla/5.0 prueba"
    assert p["vence"] > 0 and p["id"]


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost:8000/",
                                 "ftp://x.com/", "http://redis:6379/", "http://10.0.0.1/"])
def test_una_url_privada_es_un_422_sin_encolar(api, url):
    cliente, doble = api
    r = cliente.post("/api/extraccion/probar", json={"url": url, **REGLAS})
    assert r.status_code == 422 and r.json()["detail"].startswith("url:")
    assert not doble.listas.get(pv.COLA_PRUEBAS)


def test_reglas_invalidas_son_un_422_sin_encolar(api):
    cliente, doble = api
    r = cliente.post("/api/extraccion/probar", json={"url": "https://x.com/", "custom_extraction": {
        "r": {"tipo": "xpath", "expresion": "//a[re:test(@href,'x')]"}}})
    assert r.status_code == 422 and "custom_extraction.r" in r.text
    assert not doble.listas.get(pv.COLA_PRUEBAS)


def test_sin_worker_es_un_504_que_lo_explica(api):
    cliente, doble = api
    doble.contestar = None
    r = cliente.post("/api/extraccion/probar", json={"url": "https://x.com/", **REGLAS})
    assert r.status_code == 504 and "worker" in r.json()["detail"]


@pytest.mark.parametrize("motivo,codigo", [("url_no_permitida", 422), ("descarga", 502),
                                           ("interno", 500)])
def test_lo_que_no_pudo_el_worker_sale_con_su_codigo(api, motivo, codigo):
    cliente, doble = api
    doble.contestar = lambda p: {"ok": False, "motivo": motivo, "error": "x resuelve a 10.0.0.1"}
    r = cliente.post("/api/extraccion/probar", json={"url": "https://x.com/", **REGLAS})
    assert r.status_code == codigo and "10.0.0.1" in r.json()["detail"]


def test_una_sola_fuente(api):
    cliente, _ = api
    r = cliente.post("/api/extraccion/probar", json={"url": "https://x.com/", "html": "<p>",
                                                     **REGLAS})
    assert r.status_code == 422 and "Una sola fuente" in r.text


def test_la_api_no_carga_curl_cffi(api):
    """La API no descarga: ni importa el cliente HTTP del worker."""
    cliente, doble = api
    doble.contestar = lambda p: {"ok": True, "descarga": {}, "reglas": [], "no_evaluadas": [],
                                 "avisos": [], "ms": 0}
    antes = "curl_cffi" in sys.modules
    assert cliente.post("/api/extraccion/probar", json={"url": "https://x.com/", **REGLAS}
                        ).status_code == 200
    assert ("curl_cffi" in sys.modules) == antes
