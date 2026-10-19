"""Slack por su API, un canal por cliente (decision 149).

Lo que se manda de verdad (metodo, token, canal, texto), los errores de Slack
traducidos a algo que se pueda arreglar, que un Slack caido no lance, y la API:
el token se valida al guardarlo y nunca vuelve. Ninguna prueba sale a la red:
`urlopen` esta interceptado.
"""

from __future__ import annotations

import io
import json
import types
import urllib.error
import uuid

import pytest

import shared.avisos as avisos
import shared.slack as slack

TOKEN = "xoxb-1234567890-abcdefghijkl"


class _Resp:
    def __init__(self, cuerpo):
        self._b = json.dumps(cuerpo).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def red(monkeypatch):
    """Una Slack de mentira: `respuestas[metodo]` es lo que contesta (un dict
    o una excepcion). Cada llamada queda en `llamadas`."""
    estado = {"llamadas": [], "respuestas": {
        "auth.test": {"ok": True, "team": "Lin3s", "user": "crawler", "bot_id": "B1"},
        "chat.postMessage": {"ok": True, "channel": "C1", "ts": "1.2"},
        "conversations.list": {"ok": True, "channels": [
            {"id": "C2", "name": "seo-zeta", "is_member": False},
            {"id": "C1", "name": "seo-acme", "is_member": True, "is_private": True}]}}}

    def urlopen(peticion, timeout=None):
        metodo = peticion.full_url.split("/api/")[1].split("?")[0]
        cuerpo = json.loads(peticion.data.decode()) if peticion.data else None
        estado["llamadas"].append({"metodo": metodo, "cuerpo": cuerpo, "url": peticion.full_url,
                                   "auth": peticion.get_header("Authorization")})
        r = estado["respuestas"][metodo]
        if isinstance(r, list):
            r = r.pop(0)
        if isinstance(r, Exception):
            raise r
        return _Resp(r)

    monkeypatch.setattr(slack.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(slack.time, "sleep", lambda s: None)
    anotado = {}
    monkeypatch.setattr(slack, "_anotar", lambda cid, envio, canal, ok, motivo:
                        anotado.update({cid: {"envio": envio, "canal": canal, "ok": ok, "motivo": motivo}}))
    estado["anotado"] = anotado
    return estado


def _cliente(**parametros):
    p = {"canal": "C1"} | parametros
    return types.SimpleNamespace(id=uuid.uuid4(), slug="acme", nombre="Acme", integraciones=[
        {"tipo": "slack", "credencial_env": "SLACK_LIN3S", "activa": True, "parametros": p}])


@pytest.fixture
def con_token(monkeypatch):
    monkeypatch.setenv("SLACK_LIN3S", TOKEN)


def test_publicar_manda_al_canal_con_el_bot_y_el_enlace(red):
    slack.publicar(TOKEN, "C1", "*Acme*: algo", "https://x/#/rastreos/1", "Ver")
    ll = red["llamadas"][0]
    assert ll["metodo"] == "chat.postMessage" and ll["auth"] == f"Bearer {TOKEN}"
    assert ll["cuerpo"]["channel"] == "C1"
    assert ll["cuerpo"]["text"] == "*Acme*: algo\n<https://x/#/rastreos/1|Ver>"
    assert ll["cuerpo"]["unfurl_links"] is False


def test_un_texto_enorme_se_recorta(red):
    slack.publicar(TOKEN, "C1", "x" * 50000)
    assert len(red["llamadas"][0]["cuerpo"]["text"]) <= slack.MAX_TEXTO + 2


def test_un_429_corto_se_reintenta_una_vez(red):
    limitado = urllib.error.HTTPError("u", 429, "lim", {"Retry-After": "1"}, io.BytesIO(b""))
    red["respuestas"]["chat.postMessage"] = [limitado, {"ok": True, "channel": "C1", "ts": "1"}]
    slack.publicar(TOKEN, "C1", "hola")
    assert len(red["llamadas"]) == 2


def test_el_error_de_slack_se_traduce():
    assert "/invite @crawler" in slack.traducir("not_in_channel", "crawler")
    assert "channels:read" in slack.traducir("missing_scope", needed="channels:read")
    assert "raro" in slack.traducir("raro")


def test_enviar_a_cliente_llega_y_lo_anota(red, con_token):
    c = _cliente()
    assert slack.enviar_a_cliente(c, "alertas", "hola") == 1
    assert red["anotado"][c.id]["ok"] is True


def test_un_canal_sin_el_bot_no_lanza_y_queda_el_motivo(red, con_token):
    red["respuestas"]["chat.postMessage"] = {"ok": False, "error": "not_in_channel"}
    c = _cliente()
    assert slack.enviar_a_cliente(c, "alertas", "hola") == 0
    assert "/invite" in red["anotado"][c.id]["motivo"]


def test_sin_token_no_lanza(red, monkeypatch, tmp_path):
    monkeypatch.delenv("SLACK_LIN3S", raising=False)
    monkeypatch.setenv("SECRETOS_DIR", str(tmp_path))
    c = _cliente()
    assert slack.enviar_a_cliente(c, "alertas", "hola") == 0
    assert red["llamadas"] == [] and red["anotado"][c.id]["ok"] is False


@pytest.mark.parametrize("valor,sale", [(None, True), (True, True), (False, False),
                                        ("false", False), ("0", False), ("true", True)])
def test_cada_envio_se_puede_apagar(valor, sale):
    # «false» como texto: el formulario generico de integraciones guarda texto.
    assert bool(slack.destinos(_cliente(resumen=valor), "resumen")) is sale


def test_una_integracion_pausada_o_sin_canal_no_manda():
    c = _cliente()
    c.integraciones[0]["activa"] = False
    assert slack.destinos(c, "alertas") == []
    assert slack.destinos(_cliente(canal=" "), "alertas") == []


ALERTA = {"regla": "canonical_a_otro_host", "severidad": "critical", "paginas": 2319,
          "de": 4193, "pct": 55.3, "que_decide_google": "Google consolida en otro host."}


def test_las_alertas_van_al_slack_del_cliente_sin_webhook(red, con_token, monkeypatch):
    monkeypatch.setattr(avisos, "WEBHOOK_URL", "")
    monkeypatch.setattr(avisos, "BASE_PUBLICA", "https://crawler.x")
    assert avisos.avisar_de_alertas("j1", "Censo semanal", [ALERTA], cliente=_cliente()) is True
    texto = red["llamadas"][0]["cuerpo"]["text"]
    assert "*2319* de 4193" in texto and "<https://crawler.x/#/rastreos/j1|Ver el rastreo>" in texto


def test_sin_cliente_las_alertas_siguen_como_antes(red, monkeypatch):
    monkeypatch.setattr(avisos, "WEBHOOK_URL", "")
    assert avisos.avisar_de_alertas("j1", "x", [ALERTA]) is False
    assert red["llamadas"] == []


def test_el_resumen_va_al_slack_del_cliente(red, con_token, monkeypatch):
    monkeypatch.setattr(avisos, "WEBHOOK_URL", "")
    c = _cliente(alertas=False)
    assert avisos.avisar_de_resumen("Acme", "*Acme* · resumen", {"cliente": {"id": str(c.id)}}, cliente=c)
    assert red["llamadas"][0]["cuerpo"]["text"].startswith("*Acme* · resumen")


def test_el_informe_solo_de_rastreos_programados(red, con_token, monkeypatch):
    monkeypatch.setattr(avisos, "BASE_PUBLICA", "https://crawler.x")
    resumen = {"cifras": {"urls": {"valor": 3848, "cambio": 12}, "errores": {"valor": 40}},
               "incidencias": {"nuevas": 5, "resueltas": 9}, "alertas": [], "reglas_cliente": [],
               "comparable": True}
    a_mano = types.SimpleNamespace(id="j1", name="Canario", programacion_id=None)
    assert avisos.avisar_de_informe(_cliente(), a_mano, resumen) is False
    semanal = types.SimpleNamespace(id="j2", name="Censo semanal", programacion_id=uuid.uuid4())
    assert avisos.avisar_de_informe(_cliente(), semanal, resumen) is True
    texto = red["llamadas"][0]["cuerpo"]["text"]
    assert "*Censo semanal* ha terminado." in texto
    assert "3.848 URLs (+12)" in texto and "5 incidencias nuevas y 9 resueltas" in texto
    assert "Sin alertas." in texto
    assert "/#/rastreos/j2/informe-cliente|Ver el informe>" in texto


def test_el_informe_apagado_no_sale(red, con_token):
    semanal = types.SimpleNamespace(id="j2", name="S", programacion_id=uuid.uuid4())
    assert avisos.avisar_de_informe(_cliente(informe=False), semanal, {}) is False
    assert red["llamadas"] == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@pytest.fixture
def api(tmp_path, monkeypatch, red):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    from api.routers import credenciales
    from shared.database import get_session
    from shared.models import Base, Client

    @compiles(BigInteger, "sqlite")
    def _bigint(tipo, compilador, **kw):
        return "INTEGER"

    monkeypatch.setenv("SECRETOS_DIR", str(tmp_path / "secretos"))
    monkeypatch.delenv("SLACK_LIN3S", raising=False)
    monkeypatch.setattr(slack, "ultimo_envio", lambda cid: None)
    engine = create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    acme = Client(nombre="Acme", slug="acme", dominios=[], integraciones=[
        {"tipo": "gsc", "credencial_env": "GSC_X", "activa": True, "parametros": {"propiedad": "sc-domain:a.com"}}])
    s.add(acme)
    s.commit()
    cid = acme.id
    s.close()
    app = FastAPI()
    app.include_router(credenciales.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), cid, Sesion


def _subir(cliente_http, contenido=TOKEN):
    return cliente_http.post("/api/credenciales", json={"tipo": "slack", "nombre": "slack_lin3s",
                                                        "contenido": contenido})


def test_el_token_se_valida_se_guarda_y_nunca_vuelve(api, red, tmp_path):
    http, _, _ = api
    r = _subir(http)
    assert r.status_code == 201, r.text
    assert r.json()["bot"] == "crawler" and r.json()["equipo"] == "Lin3s"
    assert TOKEN not in r.text
    lista = http.get("/api/credenciales?tipo=slack")
    assert [x["nombre"] for x in lista.json()] == ["SLACK_LIN3S"] and TOKEN not in lista.text
    assert (tmp_path / "secretos" / "SLACK_LIN3S.json").read_text() == TOKEN
    # Y no se mezcla con las llaves de Google.
    assert http.get("/api/credenciales?tipo=gsc").json() == []


def test_un_token_mal_pegado_o_rechazado_no_se_guarda(api, red, tmp_path):
    http, _, _ = api
    assert _subir(http, "https://hooks.slack.com/services/T/B/X").status_code == 422
    red["respuestas"]["auth.test"] = {"ok": False, "error": "invalid_auth"}
    r = _subir(http)
    assert r.status_code == 422 and "no es válido" in r.json()["detail"]
    assert not (tmp_path / "secretos" / "SLACK_LIN3S.json").exists()


def test_los_canales_del_bot_y_sin_permiso_un_409(api, red):
    http, _, _ = api
    _subir(http)
    r = http.get("/api/credenciales/SLACK_LIN3S/canales")
    assert [c["nombre"] for c in r.json()] == ["seo-acme", "seo-zeta"]
    red["respuestas"]["conversations.list"] = {"ok": False, "error": "missing_scope", "needed": "channels:read"}
    r = http.get("/api/credenciales/SLACK_LIN3S/canales")
    assert r.status_code == 409 and "a mano" in r.json()["detail"]


def test_conectar_probar_y_quitar_el_canal_del_cliente(api, red):
    http, cid, Sesion = api
    _subir(http)
    r = http.put(f"/api/clients/{cid}/slack", json={"credencial": "SLACK_LIN3S", "canal": "C1",
                                                    "canal_nombre": "#seo-acme", "resumen": False})
    assert r.status_code == 200, r.text
    fila = r.json()["canales"][0]
    assert fila["configurada"] and fila["canal_nombre"] == "#seo-acme"
    assert (fila["alertas"], fila["resumen"], fila["informe"]) == (True, False, True)
    from shared.models import Client
    integ = Sesion().get(Client, cid).integraciones
    # La de GSC sigue ahi; la de Slack, sin el token.
    assert [i["tipo"] for i in integ] == ["gsc", "slack"] and TOKEN not in json.dumps(integ)

    # Volver a guardar sustituye, no duplica.
    http.put(f"/api/clients/{cid}/slack", json={"credencial": "SLACK_LIN3S", "canal": "C2"})
    assert [c["canal"] for c in http.get(f"/api/clients/{cid}/slack").json()["canales"]] == ["C2"]

    red["respuestas"]["chat.postMessage"] = {"ok": False, "error": "not_in_channel"}
    p = http.post(f"/api/clients/{cid}/slack/probar").json()
    assert p[0]["ok"] is False and "/invite @crawler" in p[0]["motivo"]

    assert http.delete(f"/api/clients/{cid}/slack").status_code == 204
    assert http.get(f"/api/clients/{cid}/slack").json()["canales"] == []
    assert http.post(f"/api/clients/{cid}/slack/probar").status_code == 409


def test_el_token_de_otro_tipo_no_vale_para_slack(api):
    http, cid, _ = api
    r = http.put(f"/api/clients/{cid}/slack", json={"credencial": "GSC_X", "canal": "C1"})
    assert r.status_code == 422


def test_el_manifiesto_de_la_guia_pide_lo_que_usa_el_codigo():
    """La guia de la ficha (decision 149) da un manifiesto para crear la app.
    Tiene que pedir chat:write (publicar) y channels:read/groups:read (la
    lista de canales), y SIN rotacion de token: con ella caduca a las 12 h y
    los avisos dejarian de llegar sin que nadie toque nada."""
    import pathlib
    import re

    js = (pathlib.Path(__file__).parent.parent / "frontend" / "app.js").read_text()
    bloque = js[js.index("manifiestoSlack() {"):js.index("copiarManifiestoSlack()")]
    lineas = re.findall(r"'([^']*)'", bloque)
    assert {"      - chat:write", "      - channels:read", "      - groups:read"} <= set(lineas)
    assert "  token_rotation_enabled: false" in lineas
