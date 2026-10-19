"""El servidor MCP en remoto, servido por la propia API en `/mcp` (decision 151).

claude.ai, Cowork y Claude Desktop solo conectan con un servidor MCP remoto
(un «conector personalizado»): una URL https con OAuth. Claude Code acepta las
dos cosas, asi que con esto basta una URL para todos y nadie instala nada:

    https://<produccion>/mcp

- **Las herramientas son las del stdio** (`servidor.crear_servidor`), y llaman
  a la API por dentro (`127.0.0.1:8000`) con `API_TOKEN`: lo que ve el asistente
  es exactamente lo que ve la interfaz, sin una segunda copia de las consultas.
- **OAuth 2.1 con registro dinamico** (lo que piden los conectores), con el
  servidor de autorizacion del SDK y este proveedor. Autorizar es entrar con la
  misma clave de acceso que la interfaz (decision 134), o con un clic si el
  navegador ya tiene la sesion de `/entrar`: no hay usuarios ni roles nuevos.
- **Solo se registran redirecciones de Claude**: el callback de claude.ai y
  claude.com, y `localhost` (Claude Code). Otra app no puede pedir un token
  aunque alguien pique en un enlace suyo. `MCP_REDIRECCIONES_EXTRA` anade URIs
  exactas.
- **Los tokens viven en Redis guardados por su hash**, y cada uno lleva la
  huella de `API_TOKEN`: cambiar la clave invalida tambien todos los conectores,
  como invalida las sesiones del navegador. Acceso 8 h, refresco 90 dias,
  rotado en cada uso.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import secrets
import time
from typing import Protocol
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams,
                                      RefreshToken, RegistrationError, TokenError,
                                      construct_redirect_uri)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from api.mcp_despacho import RUTA_ENTRAR
from mcp_servidor import herramientas as H

ALCANCE = "crawler"
DURACION_CODIGO_S = 5 * 60
DURACION_PENDIENTE_S = 10 * 60
DURACION_ACCESO_S = 8 * 3600
DURACION_REFRESCO_S = 90 * 24 * 3600
DURACION_CLIENTE_S = 365 * 24 * 3600
CALLBACKS_DE_CLAUDE = ("https://claude.ai/api/mcp/auth_callback",
                       "https://claude.com/api/mcp/auth_callback")


def redireccion_permitida(uri: str) -> bool:
    if uri in CALLBACKS_DE_CLAUDE:
        return True
    extra = [x.strip() for x in (os.getenv("MCP_REDIRECCIONES_EXTRA") or "").split(",") if x.strip()]
    if uri in extra:
        return True
    u = urlparse(uri)
    # Claude Code recibe el codigo en un puerto suyo de la propia maquina.
    return u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1") and not u.username


def _hash(valor: str) -> str:
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


def huella(api_token: str) -> str:
    """Lo que ata un token del conector a la clave vigente."""
    return _hash("cm-mcp:" + api_token)[:24]


class Almacen(Protocol):
    async def leer(self, clave: str) -> str | None: ...
    async def guardar(self, clave: str, valor: str, segundos: int) -> None: ...
    async def borrar(self, clave: str) -> None: ...


class AlmacenRedis:
    def __init__(self, url: str):
        import redis.asyncio as aioredis
        self._r = aioredis.from_url(url, decode_responses=True)

    async def leer(self, clave: str) -> str | None:
        return await self._r.get("mcp:" + clave)

    async def guardar(self, clave: str, valor: str, segundos: int) -> None:
        await self._r.set("mcp:" + clave, valor, ex=segundos)

    async def borrar(self, clave: str) -> None:
        await self._r.delete("mcp:" + clave)

    async def cerrar(self) -> None:
        await self._r.aclose()


class AlmacenEnMemoria:
    """Para los tests: mismo contrato, con caducidad."""

    def __init__(self):
        self.datos: dict[str, tuple[str, float]] = {}

    async def leer(self, clave: str) -> str | None:
        v = self.datos.get(clave)
        if v is None or v[1] < time.time():
            self.datos.pop(clave, None)
            return None
        return v[0]

    async def guardar(self, clave: str, valor: str, segundos: int) -> None:
        self.datos[clave] = (valor, time.time() + segundos)

    async def borrar(self, clave: str) -> None:
        self.datos.pop(clave, None)

    async def cerrar(self) -> None:
        pass


class ProveedorOAuth:
    """`OAuthAuthorizationServerProvider` del SDK sobre un `Almacen`."""

    def __init__(self, almacen: Almacen, url_publica: str, api_token: str):
        self.almacen = almacen
        self.url_publica = url_publica.rstrip("/")
        self.api_token = api_token

    # --- clientes (registro dinamico) ---------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        v = await self.almacen.leer("cliente:" + client_id)
        return OAuthClientInformationFull.model_validate_json(v) if v else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        malas = [str(u) for u in client_info.redirect_uris or [] if not redireccion_permitida(str(u))]
        if malas or not client_info.redirect_uris:
            raise RegistrationError("invalid_redirect_uri",
                                    "Este servidor solo autoriza a Claude (claude.ai, Cowork, Claude Code). "
                                    f"Redireccion no admitida: {', '.join(malas) or 'ninguna'}")
        await self.almacen.guardar("cliente:" + client_info.client_id, client_info.model_dump_json(),
                                   DURACION_CLIENTE_S)

    # --- autorizacion: se completa en la pagina de entrar --------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        pendiente = secrets.token_urlsafe(24)
        await self.almacen.guardar("pendiente:" + _hash(pendiente), json.dumps({
            "client_id": client.client_id, "params": params.model_dump(mode="json")}), DURACION_PENDIENTE_S)
        return f"{self.url_publica}{RUTA_ENTRAR}?pendiente={pendiente}"

    async def leer_pendiente(self, pendiente: str) -> dict | None:
        v = await self.almacen.leer("pendiente:" + _hash(pendiente or ""))
        return json.loads(v) if v else None

    async def conceder(self, pendiente: str) -> str | None:
        """Consume la peticion pendiente y devuelve la redireccion con el codigo."""
        p = await self.leer_pendiente(pendiente)
        if p is None:
            return None
        await self.almacen.borrar("pendiente:" + _hash(pendiente))
        params = AuthorizationParams.model_validate(p["params"])
        codigo = secrets.token_urlsafe(32)
        await self.almacen.guardar("codigo:" + _hash(codigo), AuthorizationCode(
            code=codigo, scopes=params.scopes or [ALCANCE], expires_at=time.time() + DURACION_CODIGO_S,
            client_id=p["client_id"], code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource).model_dump_json(), DURACION_CODIGO_S)
        return construct_redirect_uri(str(params.redirect_uri), code=codigo, state=params.state)

    async def denegar(self, pendiente: str) -> str | None:
        p = await self.leer_pendiente(pendiente)
        if p is None:
            return None
        await self.almacen.borrar("pendiente:" + _hash(pendiente))
        params = AuthorizationParams.model_validate(p["params"])
        return construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state)

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        v = await self.almacen.leer("codigo:" + _hash(authorization_code))
        if not v:
            return None
        c = AuthorizationCode.model_validate_json(v)
        return c if c.client_id == client.client_id else None

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        await self.almacen.borrar("codigo:" + _hash(authorization_code.code))
        return await self._emitir(client.client_id, authorization_code.scopes, authorization_code.resource)

    # --- tokens --------------------------------------------------------
    async def _emitir(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        acceso, refresco = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        ahora = int(time.time())
        comun = {"client_id": client_id, "scopes": scopes, "resource": resource,
                 "huella": huella(self.api_token)}
        await self.almacen.guardar("acceso:" + _hash(acceso), json.dumps(
            {**comun, "expires_at": ahora + DURACION_ACCESO_S}), DURACION_ACCESO_S)
        await self.almacen.guardar("refresco:" + _hash(refresco), json.dumps(
            {**comun, "expires_at": ahora + DURACION_REFRESCO_S}), DURACION_REFRESCO_S)
        return OAuthToken(access_token=acceso, expires_in=DURACION_ACCESO_S, refresh_token=refresco,
                          scope=" ".join(scopes))

    async def _leer_token(self, tipo: str, token: str) -> dict | None:
        v = await self.almacen.leer(f"{tipo}:{_hash(token)}")
        if not v:
            return None
        d = json.loads(v)
        # Cambiar API_TOKEN cierra tambien los conectores (decision 134).
        return d if secrets.compare_digest(d.get("huella", ""), huella(self.api_token)) else None

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str) -> RefreshToken | None:
        d = await self._leer_token("refresco", refresh_token)
        if d is None or d["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=d["client_id"], scopes=d["scopes"],
                            expires_at=d["expires_at"], resource=d.get("resource"))

    async def exchange_refresh_token(self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        if scopes and not set(scopes) <= set(refresh_token.scopes):
            raise TokenError("invalid_scope", "No se puede ampliar el alcance al refrescar")
        await self.almacen.borrar("refresco:" + _hash(refresh_token.token))
        return await self._emitir(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        d = await self._leer_token("acceso", token)
        if d is None:
            return None
        return AccessToken(token=token, client_id=d["client_id"], scopes=d["scopes"],
                           expires_at=d["expires_at"], resource=d.get("resource"))

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self.almacen.borrar("acceso:" + _hash(token.token))
        await self.almacen.borrar("refresco:" + _hash(token.token))

    async def exchange_identity_assertion(self, client, params):  # pragma: no cover - no se anuncia
        raise TokenError("unsupported_grant_type", "No admitido")


# --- la pagina de autorizar ---------------------------------------------

def _pagina(titulo: str, cuerpo: str, codigo: int = 200) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(titulo)}</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;background:#f6f7f9;color:#1b1f24;margin:0}}
main{{max-width:28rem;margin:10vh auto;padding:2rem;background:#fff;border-radius:12px;
box-shadow:0 1px 4px rgba(0,0,0,.08)}}h1{{font-size:1.25rem;margin:0 0 1rem}}
input{{width:100%;box-sizing:border-box;padding:.6rem;font:inherit;margin:.25rem 0 1rem;
border:1px solid #c9ced6;border-radius:8px}}.botones{{display:flex;gap:.5rem}}
button{{padding:.6rem 1rem;font:inherit;border-radius:8px;border:1px solid #c9ced6;background:#fff;cursor:pointer}}
button.si{{background:#1f6feb;color:#fff;border-color:#1f6feb}}.error{{color:#b42318}}
.nota{{color:#57606a;font-size:.9rem}}@media (prefers-color-scheme:dark){{body{{background:#0d1117;color:#e6edf3}}
main{{background:#161b22}}input,button{{background:#0d1117;color:#e6edf3;border-color:#30363d}}
.nota{{color:#8b949e}}}}</style></head><body><main>{cuerpo}</main></body></html>""",
        status_code=codigo, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                     "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https: http://localhost:* http://127.0.0.1:*"})


def _caducada() -> HTMLResponse:
    return _pagina("Autorización caducada", "<h1>Esta autorización ha caducado</h1><p>Vuelve a conectar "
                   "el crawler desde Claude.</p>", 400)


def _formulario(pendiente: str, p: dict, con_sesion: bool, error: str = "") -> HTMLResponse:
    destino = urlparse(p["params"]["redirect_uri"]).hostname or ""
    clave = ("" if con_sesion else
             '<label for="token">Clave de acceso del crawler</label>'
             '<input id="token" name="token" type="password" autocomplete="current-password" required autofocus>')
    aviso = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return _pagina("Conectar Claude con el crawler", f"""<h1>Conectar Claude con el crawler SEO</h1>
<p><strong>{html.escape(destino)}</strong> pide acceso al crawler: leer todos sus datos (clientes, censos,
informes, URLs, alertas, Search Console) y <strong>lanzar, cancelar y programar rastreos</strong>, siempre
enseñándote antes lo que va a hacer. No puede borrar nada.</p>{aviso}<form method="post" action="{RUTA_ENTRAR}">
<input type="hidden" name="pendiente" value="{html.escape(pendiente)}">{clave}
<div class="botones"><button class="si" name="accion" value="autorizar">Autorizar</button>
<button name="accion" value="cancelar" formnovalidate>Cancelar</button></div></form>
<p class="nota">La autorización dura 90 días sin usarse y se cae si cambia la clave de acceso.</p>""",
                   401 if error else 200)


def rutas_de_entrar(proveedor: ProveedorOAuth, sesion_valida, token_correcto) -> list:
    from starlette.routing import Route

    async def entrar(request: Request) -> Response:
        if request.method == "GET":
            pendiente = request.query_params.get("pendiente", "")
            p = await proveedor.leer_pendiente(pendiente)
            if p is None:
                return _caducada()
            return _formulario(pendiente, p, sesion_valida(request))
        form = await request.form()
        pendiente = str(form.get("pendiente") or "")
        p = await proveedor.leer_pendiente(pendiente)
        if p is None:
            return _caducada()
        if form.get("accion") == "cancelar":
            return RedirectResponse(await proveedor.denegar(pendiente), status_code=302)
        if not (sesion_valida(request) or token_correcto(str(form.get("token") or ""))):
            return _formulario(pendiente, p, False, "Esa no es la clave de acceso.")
        return RedirectResponse(await proveedor.conceder(pendiente), status_code=302)

    return [Route(RUTA_ENTRAR, entrar, methods=["GET", "POST"])]


# --- montaje en la API --------------------------------------------------

class Remoto:
    """Lo que la API arranca en su lifespan: el servidor, su app y el almacen."""

    def __init__(self, url_publica: str, api_token: str, almacen: Almacen,
                 api_interna: str = "http://127.0.0.1:8000", transporte=None):
        from mcp_servidor.servidor import crear_servidor
        from api import auth

        self.url_publica = url_publica.rstrip("/")
        self.almacen = almacen
        self.proveedor = ProveedorOAuth(almacen, self.url_publica, api_token)
        _api: dict[str, H.Api] = {}

        def api() -> H.Api:
            if "x" not in _api:
                _api["x"] = H.Api(base=api_interna, token=api_token or "sin-token", transporte=transporte,
                                  publica=self.url_publica)
            return _api["x"]

        self.servidor = crear_servidor(
            api, auth_server_provider=self.proveedor,
            auth=AuthSettings(
                issuer_url=self.url_publica, resource_server_url=self.url_publica + "/mcp",
                validate_token_resource=False, required_scopes=[ALCANCE],
                client_registration_options=ClientRegistrationOptions(
                    enabled=True, valid_scopes=[ALCANCE], default_scopes=[ALCANCE]),
                revocation_options=RevocationOptions(enabled=True)))

        def sesion_valida(request: Request) -> bool:
            return bool(api_token) and auth.sesion_valida(request.cookies.get(auth.COOKIE), api_token)

        def token_correcto(t: str) -> bool:
            return bool(api_token) and auth.token_correcto(t, api_token)

        host = urlparse(self.url_publica).netloc
        self.app = self.servidor.streamable_http_app(
            streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0",
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[host, "127.0.0.1:*", "localhost:*"],
                allowed_origins=[self.url_publica, "https://claude.ai", "https://claude.com",
                                 "http://127.0.0.1:*", "http://localhost:*"]))
        self.app.router.routes.extend(rutas_de_entrar(self.proveedor, sesion_valida, token_correcto))

    def correr(self):
        return self.servidor.session_manager.run()
