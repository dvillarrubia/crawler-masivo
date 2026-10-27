"""FastAPI application entry point for the SEO crawler API."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from shared.config import REDIS_URL
from shared.database import init_db

from api import dependencies
from api.auth import AutenticacionMiddleware, desactivada, token_configurado
from api.cabeceras import CabecerasSeguridadMiddleware
from api.mcp_despacho import DespachoMcp, EstadoMcp
from api.routers import (
    ajustes, almacenamiento, backlinks, cleaning, clientes, compartido, credenciales, enriquecido, extraccion, jobs, programaciones,
    results, revision, robots_prueba, segmentos, semantic, sesion, uso,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
estado_mcp = EstadoMcp()
log = logging.getLogger(__name__)


def _remoto_mcp():
    """El servidor MCP remoto (decision 151), o None si no toca: lo apaga
    `MCP_REMOTO=0`, y sin `API_TOKEN` no arranca (no habria con que autorizar
    ni con que llamar a la API), salvo con la autenticacion desactivada."""
    if (os.getenv("MCP_REMOTO") or "1").strip() == "0":
        return None
    token = token_configurado()
    if token is None and not desactivada():
        return None
    try:
        from mcp_servidor.herramientas import URL_POR_DEFECTO
        from mcp_servidor.remoto import AlmacenRedis, Remoto
    except ImportError as e:  # imagen sin el SDK de MCP
        log.warning("Servidor MCP remoto apagado: %s", e)
        return None
    return Remoto(os.getenv("MCP_URL_PUBLICA") or URL_POR_DEFECTO, token or "", AlmacenRedis(REDIS_URL))


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup ----------------------------------------------------------
    init_db()
    dependencies.redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    dependencies.redis_client.ping()

    remoto = _remoto_mcp()
    if remoto is None:
        yield
    else:
        async with remoto.correr():
            estado_mcp.remoto = remoto
            try:
                yield
            finally:
                estado_mcp.remoto = None
                await remoto.almacen.cerrar()

    # Shutdown ---------------------------------------------------------
    if dependencies.redis_client is not None:
        dependencies.redis_client.close()
        dependencies.redis_client = None


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="SEO Crawler API",
    description="Job management and results API for the SEO crawler system",
    version="1.0.0",
    lifespan=lifespan,
)

# Autenticacion (decision 134): todo `/api` salvo `/api/sesion` pide sesion o
# token. Va como middleware y no como dependencia de cada router para que uno
# nuevo nazca protegido.
app.add_middleware(AutenticacionMiddleware)

# CORS: la interfaz se sirve desde el mismo origen y no lo necesita. Antes era
# `allow_origins=["*"]` con `allow_credentials=True`, que Starlette resuelve
# devolviendo el origen de quien pregunta: cualquier web podia leer la API con
# la sesion del navegador. Solo los origenes de `CORS_ORIGINS` (por comas).
_ORIGENES = [o.strip() for o in (os.getenv("CORS_ORIGINS") or "").split(",") if o.strip()]
if _ORIGENES:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ORIGENES,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# El servidor MCP remoto (decision 151) por fuera de todo: `/mcp` y las rutas
# de OAuth llevan su propia autenticacion (token del conector), no la de `/api`.
app.add_middleware(DespachoMcp, estado=estado_mcp)

# Cabeceras de seguridad (decision 208). La ultima en anadirse es la mas
# externa, asi que envuelve a todas: las respuestas de `/api`, las de 401 de la
# autenticacion, el MCP y los estaticos llevan las cabeceras.
app.add_middleware(CabecerasSeguridadMiddleware)

# Routers
app.include_router(jobs.router)
app.include_router(results.router)
app.include_router(semantic.router)
app.include_router(cleaning.router)
app.include_router(programaciones.router)
app.include_router(clientes.router)
app.include_router(enriquecido.router)
app.include_router(uso.router)
app.include_router(almacenamiento.router)
app.include_router(extraccion.router)
app.include_router(segmentos.router)
app.include_router(sesion.router)
app.include_router(credenciales.router)
app.include_router(compartido.router)
app.include_router(robots_prueba.router)  # decision 171
app.include_router(ajustes.router)  # decision 196
app.include_router(revision.router)  # decision 211
app.include_router(backlinks.router)  # decision 212


@app.get("/health", tags=["system"])
def healthcheck():
    return {"status": "ok"}


class _EstaticosQueRevalidan(StaticFiles):
    """Estáticos con `Cache-Control: no-cache`.

    Sin esa cabecera el navegador decide por su cuenta cuánto se fía de su
    copia, y sirve un `app.js` de hace días junto a un `index.html` recién
    desplegado. El síntoma es una consola llena de
    `ReferenceError: <funcion> is not defined` para funciones que SÍ están en
    el fichero que sirve el servidor, y no hay forma de distinguirlo de un
    fallo de código sin ir a mirar lo que se sirve de verdad.

    `no-cache` no es «no guardes»: es «guárdalo, pero pregunta antes de
    usarlo». Como ya se manda `etag`, la pregunta se contesta con un 304 de
    unos cientos de bytes — el fichero entero son 100 kB, así que lo que se
    paga es una petición condicional por carga y lo que se gana es no servir
    nunca una mezcla de dos versiones.
    """

    async def get_response(self, path: str, scope):
        respuesta = await super().get_response(path, scope)
        respuesta.headers.setdefault("Cache-Control", "no-cache")
        return respuesta


# Static files & SPA fallback
if FRONTEND_DIR.is_dir():
    app.mount("/static", _EstaticosQueRevalidan(directory=str(FRONTEND_DIR)),
              name="static")

    # Well-known files that browsers and crawlers fetch from the site root.
    # Serving them from a real path with a proper content-type avoids the
    # 404s seen behind reverse proxies that don't fall through to the SPA.
    @app.get("/robots.txt", include_in_schema=False)
    def robots_txt() -> Response:
        path = FRONTEND_DIR / "robots.txt"
        if path.is_file():
            return FileResponse(str(path), media_type="text/plain")
        return Response(status_code=404)

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon_svg() -> Response:
        path = FRONTEND_DIR / "favicon.svg"
        if path.is_file():
            return FileResponse(str(path), media_type="image/svg+xml")
        return Response(status_code=404)

    @app.get("/entrar", include_in_schema=False)
    def entrar_html() -> Response:
        return FileResponse(str(FRONTEND_DIR / "entrar.html"),
                            headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon_ico() -> Response:
        # Most modern browsers accept SVG via the <link rel="icon"> tag, but
        # they still issue a bare /favicon.ico request. Serve the SVG with
        # the right content-type so it works for both lookups.
        svg = FRONTEND_DIR / "favicon.svg"
        if svg.is_file():
            return FileResponse(str(svg), media_type="image/svg+xml")
        return Response(status_code=404)

    # Non-HTML file extensions that reach this fallback are genuinely
    # missing — returning index.html with HTML content-type would confuse
    # the browser (and any future crawler). Issue a real 404 instead.
    _NON_HTML_EXTS = {
        "txt", "xml", "json", "ico", "png", "jpg", "jpeg", "gif", "svg",
        "webp", "css", "js", "map", "woff", "woff2", "ttf", "eot",
    }

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> Response:
        last = full_path.rsplit("/", 1)[-1]
        if "." in last:
            ext = last.rsplit(".", 1)[-1].lower()
            if ext in _NON_HTML_EXTS:
                return Response(status_code=404)
        # El HTML, nunca de cache: es quien referencia al resto, y si se sirve
        # uno viejo todo lo demas da igual.
        return FileResponse(str(FRONTEND_DIR / "index.html"),
                            headers={"Cache-Control": "no-cache"})
