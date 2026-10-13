"""FastAPI application entry point for the SEO crawler API."""

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
from api.routers import (
    cleaning, clientes, enriquecido, jobs, programaciones, results, semantic,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup ----------------------------------------------------------
    init_db()
    dependencies.redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    dependencies.redis_client.ping()

    yield

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

# CORS -- allow everything during development; tighten for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(jobs.router)
app.include_router(results.router)
app.include_router(semantic.router)
app.include_router(cleaning.router)
app.include_router(programaciones.router)
app.include_router(clientes.router)
app.include_router(enriquecido.router)


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
