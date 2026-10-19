"""La ruta del prototipo semantico que guardaba la llave de Google en la base
de datos esta cerrada (decision 141): un 410 que dice donde se sube ahora."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


def test_subir_una_llave_al_prototipo_es_un_410():
    try:
        from api.routers import semantic
    except Exception as exc:  # noqa: BLE001 — el prototipo pide dependencias pesadas
        pytest.skip(f"sin las dependencias del prototipo semantico: {exc}")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from shared.database import get_session

    app = FastAPI()
    app.include_router(semantic.router)
    app.dependency_overrides[get_session] = lambda: iter([None])
    r = TestClient(app).post("/api/semantic/gsc-accounts",
                             json={"name": "x", "credentials_json": {"private_key": "secreto"}})
    assert r.status_code == 410 and "ficha del cliente" in r.json()["detail"]
