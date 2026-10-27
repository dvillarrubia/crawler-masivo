"""`GET /api/jobs/cola`: el orden real de salida de la cola de rastreos.

La vista Produccion (revision de UX del 25-oct, 2.12) dice «el 2.º en la cola».
Ese orden tiene que ser el del worker (FIFO en Redis, `shared/cola.py`) y no el
de `created_at`: una reanudacion vuelve al final aunque el job se creara antes
que los que esperan. Y sin Redis la respuesta dice que no se sabe, no que la
cola esta vacia.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from shared.cola import encolar, en_orden, siguiente  # noqa: E402


class RedisDeMentira:
    """rpush al final, blpop por la cabeza y lrange como Redis (bytes)."""

    def __init__(self):
        self.listas: dict[str, list[bytes]] = {}

    def rpush(self, clave, valor):
        self.listas.setdefault(clave, []).append(str(valor).encode())

    def blpop(self, claves, timeout=0):
        for clave in ([claves] if isinstance(claves, str) else claves):
            if self.listas.get(clave):
                return (clave, self.listas[clave].pop(0).decode())
        return None

    def lrange(self, clave, inicio, fin):
        lista = self.listas.get(clave) or []
        return list(lista[inicio:] if fin == -1 else lista[inicio:fin + 1])


def test_en_orden_es_el_orden_en_que_salen():
    r = RedisDeMentira()
    for job in ("creado-ayer", "creado-hoy", "reanudado-de-la-semana-pasada"):
        encolar(r, job)
    orden = en_orden(r)
    assert orden == ["creado-ayer", "creado-hoy", "reanudado-de-la-semana-pasada"]
    assert [siguiente(r, 1) for _ in range(3)] == orden


def test_un_job_reencolado_dos_veces_cuenta_por_su_primera_aparicion():
    r = RedisDeMentira()
    for job in ("a", "b", "a", "c"):
        encolar(r, job)
    assert en_orden(r) == ["a", "b", "c"]


@pytest.fixture()
def api(monkeypatch):
    from api.routers import jobs

    app = FastAPI()
    app.include_router(jobs.router)
    r = RedisDeMentira()
    monkeypatch.setattr(jobs, "get_redis", lambda: r)
    return TestClient(app), r, monkeypatch, jobs


def test_la_ruta_devuelve_el_orden_de_redis(api):
    cliente, r, _, _ = api
    for job in ("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"):
        encolar(r, job)
    resp = cliente.get("/api/jobs/cola")
    # 200 y no 422: la ruta va antes de `/{job_id}`, que esperaria un UUID.
    assert resp.status_code == 200
    assert resp.json() == {"disponible": True, "orden": [
        "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]}


def test_sin_redis_dice_que_no_se_sabe(api):
    cliente, _, monkeypatch, jobs = api

    def _caido():
        raise ConnectionError("redis caido")

    monkeypatch.setattr(jobs, "get_redis", _caido)
    assert cliente.get("/api/jobs/cola").json() == {"disponible": False, "orden": []}
