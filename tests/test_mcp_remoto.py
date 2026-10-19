"""Servidor MCP remoto (decision 151): el baile de OAuth entero como lo hace
claude.ai (registro dinamico, autorizar con la clave, codigo con PKCE, token,
refresco) y las herramientas por HTTP, con un almacen en memoria y la API de
mentira de `test_mcp_herramientas`. Sin red, sin Redis y sin Postgres."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("mcp")
httpx = pytest.importorskip("httpx")

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import PlainTextResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from api import auth  # noqa: E402
from api.mcp_despacho import DespachoMcp, EstadoMcp, es_ruta_mcp  # noqa: E402
from mcp_servidor.remoto import AlmacenEnMemoria, Remoto, redireccion_permitida  # noqa: E402

BASE = "http://localhost"
TOKEN = "t" * 32
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
CID = "6ea9ae5b-426f-4e9b-8700-65a12edd8a53"


def _api_falsa(pedidas):
    def responder(req: httpx.Request) -> httpx.Response:
        pedidas.append((req.method, req.url.path, req.headers.get("authorization")))
        assert req.method == "GET", "las herramientas no escriben"
        if req.url.path == "/api/clients/panel":
            return httpx.Response(200, json={"clientes": [{"nombre": "Lopesan", "slug": "lopesan",
                                                           "n_rastreos": 8, "ultimo": None}]})
        return httpx.Response(404)
    return httpx.MockTransport(responder)


@pytest.fixture
def entorno():
    pedidas: list = []
    remoto = Remoto(BASE, TOKEN, AlmacenEnMemoria(), api_interna="http://api-interna",
                    transporte=_api_falsa(pedidas))
    estado = EstadoMcp()

    @asynccontextmanager
    async def vida(app):
        async with remoto.correr():
            estado.remoto = remoto
            yield
            estado.remoto = None

    interior = Starlette(routes=[Route("/{x:path}", lambda r: PlainTextResponse("spa"))], lifespan=vida)
    with TestClient(DespachoMcp(interior, estado), base_url=BASE, follow_redirects=False) as c:
        yield c, remoto, pedidas


def _pkce():
    verificador = secrets.token_urlsafe(48)
    reto = base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).rstrip(b"=").decode()
    return verificador, reto


def _registrar(c, redirecciones=(CALLBACK,)):
    return c.post("/register", json={"redirect_uris": list(redirecciones), "client_name": "Claude",
                                     "token_endpoint_auth_method": "none",
                                     "grant_types": ["authorization_code", "refresh_token"],
                                     "response_types": ["code"]})


def _pedir_autorizacion(c, client_id, reto, estado="xyz"):
    r = c.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
                                    "code_challenge": reto, "code_challenge_method": "S256", "state": estado,
                                    "scope": "crawler", "resource": BASE + "/mcp"})
    assert r.status_code == 302, r.text
    destino = urlparse(r.headers["location"])
    assert destino.path == "/oauth/entrar"
    return parse_qs(destino.query)["pendiente"][0]


def _conectar(c):
    """Lo que hace claude.ai de punta a punta; devuelve el token."""
    client_id = _registrar(c).json()["client_id"]
    verificador, reto = _pkce()
    pendiente = _pedir_autorizacion(c, client_id, reto)
    r = c.post("/oauth/entrar", data={"pendiente": pendiente, "token": TOKEN, "accion": "autorizar"})
    assert r.status_code == 302
    vuelta = urlparse(r.headers["location"])
    assert f"{vuelta.scheme}://{vuelta.netloc}{vuelta.path}" == CALLBACK
    q = parse_qs(vuelta.query)
    assert q["state"] == ["xyz"]
    r = c.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "client_id": client_id,
                               "redirect_uri": CALLBACK, "code_verifier": verificador})
    assert r.status_code == 200, r.text
    return client_id, r.json()


def _rpc(c, token, metodo, params=None, id_=1):
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params or {}},
                  headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
                           "Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"})


def _inicializar(c, token):
    return _rpc(c, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "test", "version": "1"}})


def test_anuncia_los_metadatos_de_oauth(entorno):
    c, _, _ = entorno
    m = c.get("/.well-known/oauth-authorization-server").json()
    assert m["authorization_endpoint"] == BASE + "/authorize"
    assert m["registration_endpoint"] == BASE + "/register"
    assert "S256" in m["code_challenge_methods_supported"]
    pr = c.get("/.well-known/oauth-protected-resource/mcp").json()
    assert pr["resource"] == BASE + "/mcp"


def test_sin_token_pide_autorizacion_y_dice_donde(entorno):
    c, _, pedidas = entorno
    r = _inicializar(c, "inventado")
    assert r.status_code == 401
    assert "oauth-protected-resource" in r.headers["www-authenticate"]
    assert pedidas == []


def test_solo_se_registran_redirecciones_de_claude(entorno):
    c, _, _ = entorno
    r = _registrar(c, ["https://malo.example/cb"])
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"
    assert _registrar(c, ["http://localhost:53682/callback"]).status_code == 201
    assert redireccion_permitida("https://claude.com/api/mcp/auth_callback")
    assert not redireccion_permitida("https://claude.ai.malo.example/api/mcp/auth_callback")
    assert not redireccion_permitida("http://localhost@malo.example/cb")


def test_conectar_y_usar_las_herramientas(entorno):
    c, _, pedidas = entorno
    _, tokens = _conectar(c)
    assert tokens["refresh_token"] and tokens["expires_in"] == 8 * 3600
    assert _inicializar(c, tokens["access_token"]).status_code == 200
    r = _rpc(c, tokens["access_token"], "tools/list", id_=2)
    nombres = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"clientes", "informe", "consultar", "lanzar_rastreo", "programar_rastreo"} <= nombres
    solo_lectura = {t["name"] for t in r.json()["result"]["tools"] if t["annotations"]["readOnlyHint"]}
    assert nombres - solo_lectura == {"lanzar_rastreo", "cancelar_rastreo", "programar_rastreo",
                                      "editar_programacion"}
    r = _rpc(c, tokens["access_token"], "tools/call", {"name": "clientes", "arguments": {}}, id_=3)
    assert "Lopesan" in r.text
    # Por dentro llama a la API con la clave del servidor, no con el token del conector.
    assert pedidas == [("GET", "/api/clients/panel", f"Bearer {TOKEN}")]


def test_la_clave_mala_no_autoriza(entorno):
    c, _, _ = entorno
    client_id = _registrar(c).json()["client_id"]
    pendiente = _pedir_autorizacion(c, client_id, _pkce()[1])
    r = c.post("/oauth/entrar", data={"pendiente": pendiente, "token": "x" * 32, "accion": "autorizar"})
    assert r.status_code == 401 and "no es la clave" in r.text
    # La peticion sigue viva para reintentar con la buena.
    r = c.post("/oauth/entrar", data={"pendiente": pendiente, "token": TOKEN, "accion": "autorizar"})
    assert r.status_code == 302 and "code=" in r.headers["location"]
    # Mandarlo otra vez (doble clic) no da otro codigo, y dice que ya esta
    # conectado, no «caducada» (asi lo vio el usuario en produccion).
    r = c.post("/oauth/entrar", data={"pendiente": pendiente, "token": TOKEN, "accion": "autorizar"})
    assert r.status_code == 200 and "ya está conectado" in r.text and "location" not in r.headers
    assert "ya está conectado" in c.get("/oauth/entrar", params={"pendiente": pendiente}).text
    # Una que no existe si esta caducada.
    r = c.get("/oauth/entrar", params={"pendiente": "inventada"})
    assert r.status_code == 400 and "caducado" in r.text


def test_con_la_sesion_del_navegador_basta_un_clic(entorno):
    c, _, _ = entorno
    client_id = _registrar(c).json()["client_id"]
    pendiente = _pedir_autorizacion(c, client_id, _pkce()[1])
    c.cookies.set(auth.COOKIE, auth.crear_sesion(TOKEN))
    r = c.get("/oauth/entrar", params={"pendiente": pendiente})
    assert r.status_code == 200 and 'type="password"' not in r.text and "No puede borrar nada" in r.text
    r = c.post("/oauth/entrar", data={"pendiente": pendiente, "accion": "autorizar"})
    assert r.status_code == 302 and "code=" in r.headers["location"]


def test_cancelar_devuelve_acceso_denegado(entorno):
    c, _, _ = entorno
    client_id = _registrar(c).json()["client_id"]
    pendiente = _pedir_autorizacion(c, client_id, _pkce()[1])
    r = c.post("/oauth/entrar", data={"pendiente": pendiente, "accion": "cancelar"})
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["error"] == ["access_denied"] and q["state"] == ["xyz"] and "code" not in q


def test_el_refresco_rota_y_el_viejo_ya_no_vale(entorno):
    c, _, _ = entorno
    client_id, tokens = _conectar(c)
    pedir = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id}
    nuevos = c.post("/token", data=pedir)
    assert nuevos.status_code == 200, nuevos.text
    assert _inicializar(c, nuevos.json()["access_token"]).status_code == 200
    assert c.post("/token", data=pedir).status_code == 400


def test_cambiar_la_clave_de_la_api_cierra_los_conectores(entorno):
    c, remoto, _ = entorno
    _, tokens = _conectar(c)
    remoto.proveedor.api_token = "otra-clave-" + "z" * 24
    assert _inicializar(c, tokens["access_token"]).status_code == 401


def test_el_resto_de_rutas_sigue_a_la_api():
    assert es_ruta_mcp("/mcp") and es_ruta_mcp("/token") and es_ruta_mcp("/.well-known/oauth-authorization-server")
    assert not es_ruta_mcp("/api/jobs") and not es_ruta_mcp("/entrar") and not es_ruta_mcp("/mcpx")
    estado = EstadoMcp()
    interior = Starlette(routes=[Route("/{x:path}", lambda r: PlainTextResponse("spa"))])
    with TestClient(DespachoMcp(interior, estado), base_url=BASE) as c:
        assert c.get("/rastreos").text == "spa"
        # Sin remoto arrancado, las rutas del MCP no caen en la SPA.
        r = c.post("/mcp", json={})
        assert r.status_code == 503 and re.search("MCP", r.text)


class _RedisFalso:
    def ping(self):
        return True

    def close(self):
        pass


@pytest.fixture
def api_real(monkeypatch):
    """`api.main` de verdad, sin Postgres ni Redis: lo que se comprueba es el
    montaje (orden de middlewares, lifespan), no los routers."""
    import api.main as main
    import mcp_servidor.remoto as remoto

    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main.redis, "from_url", lambda *a, **k: _RedisFalso())
    monkeypatch.setattr(remoto, "AlmacenRedis", lambda url: AlmacenEnMemoria())
    monkeypatch.setenv("API_TOKEN", TOKEN)
    monkeypatch.setenv("MCP_URL_PUBLICA", BASE)
    monkeypatch.delenv("API_AUTH_DESACTIVADA", raising=False)
    monkeypatch.delenv("MCP_REMOTO", raising=False)
    return main


def test_la_api_real_sirve_el_mcp_con_su_propia_autenticacion(api_real):
    assert api_real.app.user_middleware[0].cls.__name__ == "DespachoMcp"  # por fuera de todo
    with TestClient(api_real.app, base_url=BASE) as c:
        m = c.get("/.well-known/oauth-authorization-server")
        assert m.status_code == 200 and m.json()["issuer"].rstrip("/") == BASE
        r = c.post("/mcp", json={})
        assert r.status_code == 401 and "oauth-protected-resource" in r.headers["www-authenticate"]
        # Y `/api` sigue con la suya: un token del conector no abre la API.
        assert c.get("/api/jobs").status_code == 401
    assert api_real.estado_mcp.remoto is None


def test_mcp_remoto_0_lo_apaga(api_real, monkeypatch):
    monkeypatch.setenv("MCP_REMOTO", "0")
    with TestClient(api_real.app, base_url=BASE) as c:
        assert c.post("/mcp", json={}).status_code == 503
