"""Autenticacion de la API (decision 134): todo `/api` pide sesion o token,
salvo `/api/sesion`; sin `API_TOKEN` la API esta cerrada, no abierta."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import auth  # noqa: E402

TOKEN = "t" * 12 + "0123456789abcdef"


@pytest.fixture
def app_minima(monkeypatch):
    """La app real necesita Postgres y Redis al arrancar; esta monta el mismo
    middleware y el mismo router de sesion sobre endpoints de juguete."""
    from fastapi.responses import StreamingResponse

    from api.routers import sesion

    monkeypatch.delenv("API_AUTH_DESACTIVADA", raising=False)
    app = FastAPI()
    app.add_middleware(auth.AutenticacionMiddleware)
    app.include_router(sesion.router)

    @app.get("/api/jobs")
    def jobs():
        return {"items": []}

    @app.get("/api/jobs/x/export")
    def export():
        return StreamingResponse(iter([b"a,b\n", b"1,2\n"]), media_type="text/csv")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/static/app.js")
    def estatico():
        return {"js": True}

    return app


def _cliente(app, token=TOKEN, monkeypatch=None):
    if monkeypatch is not None:
        if token is None:
            monkeypatch.delenv("API_TOKEN", raising=False)
        else:
            monkeypatch.setenv("API_TOKEN", token)
    return TestClient(app)


def test_sin_token_configurado_la_api_esta_cerrada(app_minima, monkeypatch):
    c = _cliente(app_minima, None, monkeypatch)
    r = c.get("/api/jobs")
    assert r.status_code == 503 and "API_TOKEN" in r.json()["detail"]
    assert c.get("/health").status_code == 200


def test_un_token_corto_es_como_no_tenerlo(app_minima, monkeypatch):
    c = _cliente(app_minima, "corto", monkeypatch)
    r = c.get("/api/jobs", headers={"Authorization": "Bearer corto"})
    assert r.status_code == 503 and "corto" in r.json()["detail"]


def test_sin_credenciales_401_y_lo_publico_sigue_abierto(app_minima, monkeypatch):
    c = _cliente(app_minima, monkeypatch=monkeypatch)
    assert c.get("/api/jobs").status_code == 401
    assert c.get("/api/jobs/x/export").status_code == 401
    assert c.get("/openapi.json").status_code == 401
    assert c.get("/docs").status_code == 401
    assert c.get("/health").status_code == 200
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/api/sesion").json() == {"requerida": True, "configurada": True,
                                          "autenticado": False}


def test_el_token_vale_como_bearer_y_como_cabecera(app_minima, monkeypatch):
    c = _cliente(app_minima, monkeypatch=monkeypatch)
    assert c.get("/api/jobs", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert c.get("/api/jobs", headers={"X-API-Token": TOKEN}).status_code == 200
    assert c.get("/api/jobs", headers={"Authorization": f"Bearer {TOKEN}x"}).status_code == 401
    assert c.get("/api/jobs", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401


def test_entrar_da_una_cookie_que_no_lleva_el_token(app_minima, monkeypatch):
    c = _cliente(app_minima, monkeypatch=monkeypatch)
    assert c.post("/api/sesion", json={"token": "malo"}).status_code == 401
    r = c.post("/api/sesion", json={"token": TOKEN})
    assert r.status_code == 204
    cabecera = r.headers["set-cookie"]
    assert auth.COOKIE in cabecera and TOKEN not in cabecera
    assert "httponly" in cabecera.lower() and "samesite=strict" in cabecera.lower()
    # La descarga en streaming pasa con la cookie (por aqui va el CSV)
    r = c.get("/api/jobs/x/export")
    assert r.status_code == 200 and r.text == "a,b\n1,2\n"
    assert c.get("/api/sesion").json()["autenticado"] is True
    c.delete("/api/sesion")
    c.cookies.clear()
    assert c.get("/api/jobs").status_code == 401


def test_la_cookie_es_secure_si_se_entra_por_https(app_minima, monkeypatch):
    c = _cliente(app_minima, monkeypatch=monkeypatch)
    r = c.post("/api/sesion", json={"token": TOKEN}, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_cambiar_el_token_invalida_las_sesiones(app_minima, monkeypatch):
    c = _cliente(app_minima, monkeypatch=monkeypatch)
    c.post("/api/sesion", json={"token": TOKEN})
    assert c.get("/api/jobs").status_code == 200
    monkeypatch.setenv("API_TOKEN", "otro-token-de-veinticuatro-caracteres")
    assert c.get("/api/jobs").status_code == 401


def test_una_sesion_caducada_o_manipulada_no_vale():
    valor = auth.crear_sesion(TOKEN, ahora=1000)
    assert auth.sesion_valida(valor, TOKEN, ahora=1001)
    assert not auth.sesion_valida(valor, TOKEN, ahora=1000 + auth.DURACION_SESION_S + 1)
    caduca, firma = valor.split(".")
    # Alargar la caducidad sin la clave rompe la firma
    assert not auth.sesion_valida(f"{int(caduca) + 10**6}.{firma}", TOKEN, ahora=1001)
    assert not auth.sesion_valida("basura", TOKEN)
    assert not auth.sesion_valida(None, TOKEN)


def test_desactivada_a_proposito_deja_pasar(app_minima, monkeypatch):
    c = _cliente(app_minima, None, monkeypatch)
    monkeypatch.setenv("API_AUTH_DESACTIVADA", "1")
    assert c.get("/api/jobs").status_code == 200


@pytest.mark.parametrize("ruta,protegida", [
    ("/api/jobs", True), ("/api/clients/panel", True), ("/api", True),
    ("/openapi.json", True), ("/docs", True), ("/redoc", True),
    ("/api/sesion", False), ("/health", False), ("/static/app.js", False),
    ("/entrar", False), ("/", False), ("/rastreos", False), ("/apiario", False),
])
def test_que_rutas_se_protegen(ruta, protegida):
    assert auth.requiere_autenticacion(ruta) is protegida


def test_la_app_real_monta_el_middleware_y_cierra_el_cors():
    """Sobre `api.main` de verdad: un router nuevo nace protegido porque el
    middleware envuelve la app entera, y el CORS ya no refleja cualquier origen."""
    from api.main import app

    clases = [m.cls.__name__ for m in app.user_middleware]
    assert "AutenticacionMiddleware" in clases
    assert "CORSMiddleware" not in clases
