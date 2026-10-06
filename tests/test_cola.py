"""La cola de rastreos es una cola, no una pila.

`rpush` + `brpop` trabajan sobre el MISMO extremo de la lista: con varios jobs
esperando, el ultimo creado adelantaba a todos, y lo recuperado tras un
reinicio se ponia delante de lo que llevaba horas esperando. No se habia
notado porque en el uso normal hay un job a la vez.
"""

from __future__ import annotations

import os

import pytest

from shared.cola import COLA_JOBS, encolar, pendientes, siguiente


class RedisDeMentira:
    """Semantica de Redis para lo que usa este modulo: rpush al final, blpop
    por la cabeza."""

    def __init__(self):
        self.listas: dict[str, list[str]] = {}

    def rpush(self, clave, valor):
        self.listas.setdefault(clave, []).append(valor)

    def blpop(self, clave, timeout=0):
        lista = self.listas.get(clave) or []
        if not lista:
            return None
        return (clave, lista.pop(0))

    def llen(self, clave):
        return len(self.listas.get(clave) or [])


def test_tres_jobs_salen_en_orden_de_llegada():
    r = RedisDeMentira()
    for job in ("primero", "segundo", "tercero"):
        encolar(r, job)
    assert pendientes(r) == 3
    assert [siguiente(r, 1) for _ in range(3)] == ["primero", "segundo", "tercero"]


def test_una_reanudacion_no_adelanta_a_quien_espera():
    """Reanudar es continuar trabajo ya empezado, pero adelantarlo significaria
    que un job que se atasca y se reencola varias veces pisa indefinidamente a
    los demas."""
    r = RedisDeMentira()
    encolar(r, "esperando-desde-hace-horas")
    encolar(r, "job-reanudado")   # el worker reencola igual que la API
    assert siguiente(r, 1) == "esperando-desde-hace-horas"


def test_la_cola_vacia_devuelve_none():
    r = RedisDeMentira()
    assert siguiente(r, 1) is None
    assert pendientes(r) == 0


def test_el_id_se_guarda_como_texto():
    import uuid

    r = RedisDeMentira()
    job = uuid.uuid4()
    encolar(r, job)
    assert siguiente(r, 1) == str(job)


def test_un_solo_nombre_de_cola():
    """Estaba escrito a mano en cinco sitios."""
    import pathlib
    import re

    raiz = pathlib.Path(__file__).resolve().parent.parent
    sueltos = []
    for ruta in list((raiz / "api").rglob("*.py")) + list((raiz / "crawler").rglob("*.py")):
        if re.search(r'["\']jobs:pending["\']', ruta.read_text(encoding="utf-8")):
            sueltos.append(str(ruta.relative_to(raiz)))
    assert sueltos == [], f"el nombre de la cola, a mano en: {sueltos}"
    assert COLA_JOBS == "jobs:pending"


@pytest.mark.skipif(not os.environ.get("REDIS_TEST_URL"), reason="sin REDIS_TEST_URL")
def test_contra_redis_de_verdad():
    """Lo anterior confia en que la clase de mentira imite bien a Redis: esto
    lo comprueba contra el de verdad, que es donde estaba el fallo."""
    import uuid

    redis_lib = pytest.importorskip("redis")
    r = redis_lib.Redis.from_url(os.environ["REDIS_TEST_URL"], decode_responses=True)
    marca = str(uuid.uuid4())[:8]
    ids = [f"{marca}-{n}" for n in ("uno", "dos", "tres")]
    try:
        for job in ids:
            encolar(r, job)
        salida = [siguiente(r, 2) for _ in ids]
        assert salida == ids, "FIFO roto contra Redis de verdad"
    finally:
        for job in ids:
            r.lrem(COLA_JOBS, 0, job)
