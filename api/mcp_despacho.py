"""Rutas del servidor MCP remoto dentro de la API (decision 151).

Aparte de `mcp_servidor/remoto.py` para que la API arranque aunque la imagen no
traiga el SDK de MCP: entonces estas rutas dan 503 en vez de caer en la SPA.
"""

from __future__ import annotations

import json
from typing import Any

RUTA_ENTRAR = "/oauth/entrar"
# Lo que va al servidor MCP; el resto sigue a FastAPI.
RUTAS_EXACTAS = frozenset({"/mcp", "/mcp/", "/authorize", "/token", "/register", "/revoke", RUTA_ENTRAR})
PREFIJO_METADATOS = "/.well-known/oauth-"


def es_ruta_mcp(ruta: str) -> bool:
    return ruta in RUTAS_EXACTAS or ruta.startswith(PREFIJO_METADATOS)


class EstadoMcp:
    """El remoto arrancado por el lifespan de la API, o None."""

    remoto: Any = None


class DespachoMcp:
    """ASGI por fuera de todo: las rutas del MCP y de OAuth van al servidor
    remoto, que lleva su propia autenticacion; el resto, a la API."""

    def __init__(self, app, estado: EstadoMcp):
        self.app = app
        self.estado = estado

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and es_ruta_mcp(scope.get("path") or ""):
            remoto = self.estado.remoto
            if remoto is None:
                cuerpo = json.dumps({"detail": "El servidor MCP remoto no esta activo"}).encode()
                await send({"type": "http.response.start", "status": 503,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(cuerpo)).encode())]})
                await send({"type": "http.response.body", "body": cuerpo})
                return
            return await remoto.app(scope, receive, send)
        return await self.app(scope, receive, send)
