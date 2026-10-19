"""Autenticacion de la API (decision 134).

Hasta ahora la API no tenia ninguna: quien llegara a la direccion publica podia
leer los censos de todos los clientes, lanzar rastreos y borrarlos, y con el
CORS abierto Y `allow_credentials=True` cualquier pagina web podia hacerlo
desde el navegador de quien la visitara. Ahora:

- **Un token, `API_TOKEN`, en el entorno** (nunca en la BD, decision 83). Los
  scripts lo mandan como `Authorization: Bearer <token>`; el navegador lo
  escribe una vez en `/entrar` y recibe una cookie de sesion firmada.
- **Todo cerrado por defecto.** El middleware protege cualquier ruta que no
  este en la lista de publicas, asi que un router nuevo nace protegido. Sin
  `API_TOKEN` (o con uno corto) la API responde 503 diciendo que falta: el
  fallo seguro de un despliegue que se olvida de configurarlo es no poder
  entrar, no quedarse abierta. `API_AUTH_DESACTIVADA=1` la abre a proposito
  (desarrollo local y tests), nunca por omision.
- **La cookie no lleva el token**: lleva una caducidad firmada con una clave
  derivada de el. Cambiar el token invalida todas las sesiones.
  `HttpOnly` (el JavaScript de la pagina no la lee) y `SameSite=Strict` (otra
  web no puede hacer que el navegador la mande); `Secure` cuando se entra por
  https.
- Comparacion en tiempo constante (`hmac.compare_digest`), como el token del
  enriquecido (decision 85), que se mantiene aparte: lanzar algo de pago sigue
  pidiendo su propio token aunque se haya entrado.

Publico: `/health`, los estaticos, la pagina de la aplicacion (solo el
esqueleto: los datos llegan por `/api`) y `/api/sesion`, que es por donde se
entra.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

LONGITUD_MINIMA = 24
COOKIE = "cm_sesion"
DURACION_SESION_S = 30 * 24 * 3600

# Prefijos que exigen sesion o token. Todo lo de `/api` salvo `/api/sesion`, y
# la documentacion de FastAPI, que enumera cada endpoint con sus parametros.
_PROTEGIDOS = ("/api/", "/docs", "/redoc", "/openapi.json")
# `/api/publico`: el enlace de solo lectura de un cliente (decision 143). No
# pide sesion: la autoriza el token de la ruta, y solo da el informe de ese
# cliente.
_PUBLICOS_API = ("/api/sesion", "/api/publico")


def desactivada() -> bool:
    return (os.getenv("API_AUTH_DESACTIVADA") or "").strip() == "1"


def token_configurado() -> str | None:
    t = (os.getenv("API_TOKEN") or "").strip()
    return t if len(t) >= LONGITUD_MINIMA else None


def _clave(token: str) -> bytes:
    return hashlib.sha256(b"cm-sesion:" + token.encode("utf-8")).digest()


def _firma(token: str, caduca: int) -> str:
    return hmac.new(_clave(token), str(caduca).encode(), hashlib.sha256).hexdigest()


def crear_sesion(token: str, ahora: float | None = None) -> str:
    caduca = int((ahora if ahora is not None else time.time()) + DURACION_SESION_S)
    return f"{caduca}.{_firma(token, caduca)}"


def sesion_valida(valor: str | None, token: str, ahora: float | None = None) -> bool:
    if not valor or "." not in valor:
        return False
    caduca, firma = valor.split(".", 1)
    if not caduca.isdigit():
        return False
    if int(caduca) < (ahora if ahora is not None else time.time()):
        return False
    return hmac.compare_digest(firma.encode(), _firma(token, int(caduca)).encode())


def token_correcto(recibido: str | None, token: str) -> bool:
    recibido = (recibido or "").strip()
    return bool(recibido) and hmac.compare_digest(recibido.encode("utf-8"),
                                                  token.encode("utf-8"))


def requiere_autenticacion(ruta: str) -> bool:
    if ruta in _PUBLICOS_API or any(ruta.startswith(p + "/") for p in _PUBLICOS_API):
        return False
    return ruta == "/api" or any(ruta == p.rstrip("/") or ruta.startswith(p)
                                 for p in _PROTEGIDOS)


def _cabeceras(scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers") or []}


def _cookies(cabecera: str | None) -> dict[str, str]:
    salida: dict[str, str] = {}
    for trozo in (cabecera or "").split(";"):
        if "=" in trozo:
            k, v = trozo.split("=", 1)
            salida[k.strip()] = v.strip()
    return salida


def autenticado(cabeceras: dict[str, str], token: str) -> bool:
    """Si la peticion trae el token (scripts) o una sesion valida (navegador)."""
    auth = cabeceras.get("authorization") or ""
    if auth[:7].lower() == "bearer " and token_correcto(auth[7:], token):
        return True
    if token_correcto(cabeceras.get("x-api-token"), token):
        return True
    return sesion_valida(_cookies(cabeceras.get("cookie")).get(COOKIE), token)


class AutenticacionMiddleware:
    """ASGI puro y no `BaseHTTPMiddleware`: este envuelve la respuesta entera,
    y por aqui pasan el CSV en streaming y el backup de un censo de 1 GB."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or desactivada() or scope.get("method") == "OPTIONS":
            return await self.app(scope, receive, send)
        if not requiere_autenticacion(scope.get("path") or ""):
            return await self.app(scope, receive, send)
        token = token_configurado()
        if token is None:
            motivo = ("API_TOKEN es demasiado corto (minimo "
                      f"{LONGITUD_MINIMA} caracteres)"
                      if (os.getenv("API_TOKEN") or "").strip()
                      else "falta API_TOKEN en el entorno de la API")
            return await _responder(send, 503, (
                f"La API esta cerrada: {motivo}. Configuralo en el .env del "
                "servidor y reinicia la API (o API_AUTH_DESACTIVADA=1 en local)."))
        if autenticado(_cabeceras(scope), token):
            return await self.app(scope, receive, send)
        return await _responder(send, 401, (
            "Hace falta autenticarse: entra en /entrar, o manda "
            "'Authorization: Bearer <API_TOKEN>'."))


async def _responder(send, codigo: int, detalle: str) -> None:
    cuerpo = json.dumps({"detail": detalle}, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": codigo, "headers": [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(cuerpo)).encode()),
        (b"cache-control", b"no-store"),
    ]})
    await send({"type": "http.response.body", "body": cuerpo})
