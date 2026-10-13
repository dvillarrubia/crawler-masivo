"""Lectura del uso del Explorador (#131). Ver `api/uso.py`."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api import uso
from api.dependencies import get_redis

router = APIRouter(prefix="/api/uso", tags=["uso"])


@router.get("/explorador")
def uso_explorador(dias: int = Query(30, ge=1, le=90)):
    """Que pestanas, filtros, ordenes, problemas y columnas (las del CSV) se
    han pedido en el Explorador en los ultimos `dias`. Solo nombres, nunca
    valores."""
    try:
        return uso.leer(get_redis(), dias)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Sin acceso a Redis: {exc}")
