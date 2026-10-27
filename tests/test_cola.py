"""La cola de rastreos es una cola, no una pila.

`rpush` + `brpop` trabajan sobre el MISMO extremo de la lista: con varios jobs
esperando, el ultimo creado adelantaba a todos, y lo recuperado tras un
reinicio se ponia delante de lo que llevaba horas esperando. No se habia
notado porque en el uso normal hay un job a la vez.
"""

from __future__ import annotations

import os

import pytest

from shared.cola import COLA_JOBS, COLA_PROGRAMADOS, en_orden, encolar, pendientes, siguiente


class RedisDeMentira:
    """Semantica de Redis para lo que usa este modulo: rpush al final, blpop
    por la cabeza."""

    def __init__(self):
        self.listas: dict[str, list[str]] = {}

    def rpush(self, clave, valor):
        self.listas.setdefault(clave, []).append(valor)

    def blpop(self, claves, timeout=0):
        # Como Redis: con varias claves, la primera que tenga algo.
        for clave in ([claves] if isinstance(claves, str) else claves):
            lista = self.listas.get(clave) or []
            if lista:
                return (clave, lista.pop(0))
        return None

    def lrange(self, clave, inicio, fin):
        lista = self.listas.get(clave) or []
        return list(lista[inicio:] if fin == -1 else lista[inicio:fin + 1])

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


def test_lo_lanzado_a_mano_sale_antes_que_lo_programado():
    """Decision 209: quien lanza a mano esta mirando la pantalla; el semanal
    de las 3:00 espera a que la cola normal se vacie."""
    r = RedisDeMentira()
    encolar(r, "semanal-1", programado=True)
    encolar(r, "semanal-2", programado=True)
    encolar(r, "a-mano")
    assert pendientes(r) == 3
    assert en_orden(r) == ["a-mano", "semanal-1", "semanal-2"], "el orden en que saldran"
    assert [siguiente(r, 0) for _ in range(3)] == ["a-mano", "semanal-1", "semanal-2"]
    assert siguiente(r, 0) is None


def test_una_reanudacion_va_a_la_cola_normal():
    """`programado` es solo el primer encolado: lo que se reencola (reanudar,
    recuperar tras un reinicio) usa `encolar` a secas."""
    r = RedisDeMentira()
    encolar(r, "reanudado")
    assert r.listas == {COLA_JOBS: ["reanudado"]}


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
        # Y la prioridad entre las dos listas, que es de Redis y no nuestra
        encolar(r, ids[0], programado=True)
        encolar(r, ids[1])
        assert [siguiente(r, 2), siguiente(r, 2)] == [ids[1], ids[0]]
    finally:
        for job in ids:
            r.lrem(COLA_JOBS, 0, job)
            r.lrem(COLA_PROGRAMADOS, 0, job)
