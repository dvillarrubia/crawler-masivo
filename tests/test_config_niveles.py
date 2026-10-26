"""Configuracion en tres niveles y «Así rastreará» (decision 196; revision de
UX del 25-oct, 2.4), EJECUTADO contra la API y SQLite.

Lo que se sujeta:

- `config-resuelta` dice de donde sale cada clave (cliente, perfil o el
  borrador) y trae lo de serie, para que la interfaz pinte «del cliente: 6»;
- el borrador (`POST /api/clients/config-resuelta`) se valida como se
  validaria al guardarlo y no guarda nada;
- el aviso del WAF sale SOLO si un rastreo anterior del cliente se paro por
  un bloqueo o tuvo muros; sin ese rastro no se afirma nada;
- `GET /api/ajustes` dice si las claves estan puestas, nunca su valor.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from shared.clientes import origen_de_config  # noqa: E402
from shared.models import Client, Job, Programacion  # noqa: E402

from test_clientes import SECRETO, _app, _cliente, _motor  # noqa: E402


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    Sesion = _motor(tmp_path)
    return TestClient(_app(Sesion, monkeypatch)), Sesion


def _perfil(api, c, **extra):
    cuerpo = {"nombre": "Sitio principal", "seeds": ["https://www.acme.com/"]}
    cuerpo.update(extra)
    r = api.post(f"/api/clients/{c['id']}/profiles", json=cuerpo)
    assert r.status_code == 201, r.text
    return r.json()


# --- de donde sale cada valor ---------------------------------------------

def test_origen_por_capa_y_lo_de_serie(entorno):
    api, _ = entorno
    c = _cliente(api, config_defecto={"concurrent_requests_per_domain": 6,
                                      "crawl_behavior": {"request_delay": 0.5}})
    p = _perfil(api, c, config={"render_js": True, "crawl_behavior": {"request_delay": 1.0}})
    r = api.get(f"/api/clients/{c['id']}/config-resuelta", params={"profile_id": p["id"]}).json()
    assert r["config"]["concurrent_requests_per_domain"] == 6
    assert r["origen"]["concurrent_requests_per_domain"] == "cliente"
    assert r["origen"]["render_js"] == "perfil"
    # El perfil gana en la misma hoja; las hojas se cuentan por separado.
    assert r["origen"]["crawl_behavior.request_delay"] == "perfil"
    assert "crawl_behavior.download_timeout" not in r["origen"]
    assert "concurrent_requests" not in r["origen"]
    assert r["serie"]["concurrent_requests_per_domain"] == 8
    assert r["serie"]["concurrent_requests"] == r["config"]["concurrent_requests"]


def test_un_null_cuenta_como_decision_de_esa_capa():
    o = origen_de_config(("cliente", {"max_urls": 500, "http": {"custom_headers": {"X": "1"}}}),
                         ("perfil", {"max_urls": None}))
    assert o == {"max_urls": "perfil", "http.custom_headers": "cliente"}


# --- borrador ---------------------------------------------------------------

def test_borrador_del_cliente_no_guarda_nada(entorno):
    api, _ = entorno
    c = _cliente(api, config_defecto={"max_depth": 9})
    r = api.post("/api/clients/config-resuelta", json={
        "cliente_id": c["id"], "nivel": "cliente", "config": {"concurrent_requests_per_domain": 2}})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["concurrent_requests_per_domain"] == 2
    assert r.json()["config"]["max_depth"] == 3, "el borrador sustituye la capa entera"
    assert r.json()["origen"] == {"concurrent_requests_per_domain": "cliente"}
    assert api.get(f"/api/clients/{c['id']}").json()["config_defecto"] == {"max_depth": 9}


def test_borrador_de_un_perfil_nuevo_va_sobre_el_cliente(entorno):
    api, _ = entorno
    c = _cliente(api, config_defecto={"max_depth": 9})
    r = api.post("/api/clients/config-resuelta", json={
        "cliente_id": c["id"], "nivel": "perfil", "config": {"render_js": True}}).json()
    assert r["config"]["max_depth"] == 9 and r["config"]["render_js"] is True
    assert r["origen"] == {"max_depth": "cliente", "render_js": "perfil"}


def test_borrador_de_rastreo_sobre_cliente_y_perfil(entorno):
    api, _ = entorno
    c = _cliente(api, config_defecto={"max_depth": 9})
    p = _perfil(api, c, config={"render_js": True})
    r = api.post("/api/clients/config-resuelta", json={
        "cliente_id": c["id"], "profile_id": p["id"], "config": {"max_urls": 250}}).json()
    assert (r["config"]["max_depth"], r["config"]["render_js"], r["config"]["max_urls"]) == (9, True, 250)
    assert r["origen"]["max_urls"] == "rastreo"
    assert r["seeds"] == ["https://www.acme.com/"]


def test_borrador_sin_cliente_es_lo_de_serie_con_lo_suyo(entorno):
    api, _ = entorno
    r = api.post("/api/clients/config-resuelta", json={"config": {"render_js": True}})
    assert r.status_code == 200, r.text
    assert r.json()["cliente_id"] is None
    assert r.json()["config"]["render_js"] is True
    assert r.json()["origen"] == {"render_js": "rastreo"}


@pytest.mark.parametrize("config", [
    {"concurrent_requests_per_domain": 65},          # le=64 en JobConfig
    {"concurent_requests": 4},                       # errata: decision 33
    {"crawl_behavior": {"request_delay": -1}},
])
def test_el_borrador_se_valida_como_al_guardar(entorno, config):
    api, _ = entorno
    c = _cliente(api)
    r = api.post("/api/clients/config-resuelta", json={"cliente_id": c["id"], "nivel": "cliente",
                                                       "config": config})
    assert r.status_code == 422


def test_la_clave_del_basic_solo_en_un_rastreo(entorno):
    api, _ = entorno
    c = _cliente(api)
    cfg = {"http": {"basic_auth_user": "u", "basic_auth_password": "p"}}
    assert api.post("/api/clients/config-resuelta", json={
        "cliente_id": c["id"], "nivel": "cliente", "config": cfg}).status_code == 422
    r = api.post("/api/clients/config-resuelta", json={"cliente_id": c["id"], "config": cfg})
    assert r.status_code == 200, r.text
    assert r.json()["config"]["http"]["basic_auth_password"] == "p"


def test_programacion_que_cambia_algo_por_su_cuenta(entorno):
    api, Sesion = entorno
    c = _cliente(api)
    p = _perfil(api, c)
    import uuid as _u

    with Sesion() as s:
        s.add(Programacion(nombre="semanal", seeds=[], cliente_id=_u.UUID(c["id"]), profile_id=_u.UUID(p["id"]),
                           hereda_perfil=True, config={"max_urls": 900}, cron="0 3 * * 1", activa=True))
        s.commit()
    r = api.get(f"/api/clients/{c['id']}/config-resuelta", params={"profile_id": p["id"]}).json()
    assert [(x["nombre"], x["claves"]) for x in r["programaciones"]] == [("semanal", ["max_urls"])]


# --- avisos ----------------------------------------------------------------

def _job(Sesion, c, **campos):
    import uuid as _u
    from datetime import datetime, timezone

    with Sesion() as s:
        s.add(Job(id=_u.uuid4(), name=campos.pop("name", "censo"), seeds=["https://www.acme.com/"],
                  cliente_id=_u.UUID(c["id"]), created_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
                  **campos))
        s.commit()


def _avisos(api, c, config):
    r = api.post("/api/clients/config-resuelta", json={"cliente_id": c["id"], "config": config})
    assert r.status_code == 200, r.text
    return r.json()["avisos"]


def test_sin_rastro_de_bloqueo_no_se_avisa_del_waf(entorno):
    api, Sesion = entorno
    c = _cliente(api)
    _job(Sesion, c, status="completed", finish_reason="finished",
         health={"respuestas": 900, "no_validas": 0}, config={"concurrent_requests_per_domain": 8})
    avisos = _avisos(api, c, {"concurrent_requests_per_domain": 32, "max_urls": 1000})
    assert not [a for a in avisos if a["campo"] == "concurrent_requests_per_domain"]


def test_un_rastreo_parado_por_el_waf_avisa_con_cifras(entorno):
    api, Sesion = entorno
    c = _cliente(api)
    _job(Sesion, c, name="Censo octubre", status="failed", finish_reason="bloqueado_por_waf",
         health={"respuestas": 60, "no_validas": 55}, config={"concurrent_requests_per_domain": 8})
    a = [x for x in _avisos(api, c, {"concurrent_requests_per_domain": 8})
         if x["campo"] == "concurrent_requests_per_domain"]
    assert len(a) == 1 and a[0]["nivel"] == "peligro"
    assert "«Censo octubre» del 19 oct se paró" in a[0]["texto"]
    assert "8 peticiones a la vez por dominio; este saldría con 8" in a[0]["texto"]
    menos = [x for x in _avisos(api, c, {"concurrent_requests_per_domain": 2})
             if x["campo"] == "concurrent_requests_per_domain"]
    assert menos[0]["nivel"] == "aviso" and "(menos)" in menos[0]["texto"]


def test_muros_sin_parar_tambien_se_dicen(entorno):
    api, Sesion = entorno
    c = _cliente(api)
    _job(Sesion, c, status="completed", finish_reason="finished",
         health={"respuestas": 4000, "no_validas": 1234}, config={"concurrent_requests_per_domain": 8})
    a = [x for x in _avisos(api, c, {}) if x["campo"] == "concurrent_requests_per_domain"]
    assert "1.234 de 4.000 respuestas fueron un muro" in a[0]["texto"]


def test_avisos_de_la_propia_config(entorno):
    api, _ = entorno
    c = _cliente(api)
    campos = {a["campo"] for a in _avisos(api, c, {
        "robots_mode": "ignore", "follow_external": True, "max_urls": 100,
        "user_agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"})}
    assert {"robots_mode", "follow_external", "user_agent"} <= campos
    sin_tope = _avisos(api, c, {})
    assert [a["texto"] for a in sin_tope if a["campo"] == "max_urls"][0].startswith("Sin máximo de URLs")


# --- ajustes ---------------------------------------------------------------

def test_ajustes_dice_si_estan_las_claves_nunca_su_valor(tmp_path, monkeypatch):
    from fastapi import FastAPI

    from api.routers import ajustes

    app = FastAPI()
    app.include_router(ajustes.router)
    api = TestClient(app)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ENRICHMENT_TOKEN", raising=False)
    monkeypatch.delenv("ENRICHMENT_MAX_USD_MES", raising=False)
    r = api.get("/api/ajustes").json()["modelos"]
    assert r == {"clave_modelo": False, "clave_autorizacion": False, "tope_mensual_usd": None,
                 "activada": False}
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRETO)
    monkeypatch.setenv("ENRICHMENT_TOKEN", SECRETO)
    monkeypatch.setenv("ENRICHMENT_MAX_USD_MES", "5")
    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    cuerpo = api.get("/api/ajustes")
    assert SECRETO not in cuerpo.text
    assert cuerpo.json()["modelos"] == {"clave_modelo": True, "clave_autorizacion": True,
                                        "tope_mensual_usd": 5.0, "activada": True}


def test_el_cliente_guardado_no_cambia_por_el_borrador(entorno):
    """Decision 196: el borrador construye un cliente de mentira; que no se
    cuele en la sesion y acabe guardado."""
    api, Sesion = entorno
    c = _cliente(api, config_defecto={"max_depth": 9})
    api.post("/api/clients/config-resuelta", json={"cliente_id": c["id"], "nivel": "cliente",
                                                   "config": {"max_depth": 1}})
    with Sesion() as s:
        assert s.query(Client).count() == 1
        assert s.query(Client).one().config_defecto == {"max_depth": 9}
