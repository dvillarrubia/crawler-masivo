"""FastAPI application entry point for the SEO crawler API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from shared.config import REDIS_URL
from shared.database import init_db

from api import dependencies
from api.routers import jobs, results

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


@app.get("/health", tags=["system"])
def healthcheck():
    return {"status": "ok"}


# Static files & SPA fallback
if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        return FileResponse(str(FRONTEND_DIR / "index.html"))
