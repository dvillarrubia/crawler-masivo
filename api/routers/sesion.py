"""Entrar y salir desde el navegador (decision 134).

`POST /api/sesion` cambia el token por una cookie de sesion firmada;
`GET` dice si hace falta entrar y si ya se ha entrado (lo pregunta `/entrar`
antes de pintar el formulario); `DELETE` cierra la sesion. Es la unica parte
de `/api` que no pide autenticacion (`api.auth._PUBLICOS_API`).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from api import auth

router = APIRouter(prefix="/api", tags=["sesion"])


class Entrar(BaseModel):
    token: str


def _https(request: Request) -> bool:
    # Detras de traefik la peticion llega por http al contenedor; el esquema
    # con el que entro el navegador viene en X-Forwarded-Proto.
    reenviado = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    return (reenviado or request.url.scheme) == "https"


@router.get("/sesion")
def estado(request: Request):
    if auth.desactivada():
        return {"requerida": False, "autenticado": True}
    token = auth.token_configurado()
    if token is None:
        return {"requerida": True, "autenticado": False, "configurada": False}
    return {"requerida": True, "configurada": True,
            "autenticado": auth.autenticado(
                {k.lower(): v for k, v in request.headers.items()}, token)}


@router.post("/sesion", status_code=204)
def entrar(cuerpo: Entrar, request: Request, response: Response):
    token = auth.token_configurado()
    if token is None:
        raise HTTPException(status_code=503,
                            detail="La API no tiene API_TOKEN configurado: no se puede entrar.")
    if not auth.token_correcto(cuerpo.token, token):
        raise HTTPException(status_code=401, detail="Token incorrecto.")
    response.set_cookie(
        auth.COOKIE, auth.crear_sesion(token), max_age=auth.DURACION_SESION_S,
        httponly=True, samesite="strict", secure=_https(request), path="/")
    return None


@router.delete("/sesion", status_code=204)
def salir(request: Request, response: Response):
    response.delete_cookie(auth.COOKIE, path="/", httponly=True, samesite="strict",
                           secure=_https(request))
    return None
