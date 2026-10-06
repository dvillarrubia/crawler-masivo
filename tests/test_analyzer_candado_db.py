"""Dos analisis del mismo job a la vez duplican incidencias: el candado lo evita.

El DELETE+INSERT de `issues` de cada ejecucion se pisa con el del otro. Medido
en la rama v2-experimental: 1.390 incidencias distintas acabaron como 4.170
filas. Pasa de verdad — un resume que reencola el job mientras el analisis
anterior sigue vivo, o dos lanzamientos a mano.

Necesita Postgres: el candado es `pg_try_advisory_lock` y en SQLite no existe,
asi que alli el analisis no se serializa (y el test se salta). La CI levanta un
Postgres de servicio y le pasa PAGERANK_TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import uuid

import pytest

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL")

from sqlalchemy import create_engine, text  # noqa: E402


def test_el_segundo_analisis_del_mismo_job_no_entra(monkeypatch):
    """Lo que de verdad importa: con uno dentro, el otro se va sin tocar nada."""
    import analysis.analyzer as az
    from sqlalchemy import create_engine

    motor = create_engine(URL_BD)
    monkeypatch.setattr(az, "SessionLocal", lambda: pytest.fail("no deberia abrir sesion"))
    monkeypatch.setattr("shared.database.engine", motor, raising=False)

    job = str(uuid.uuid4())
    with az.candado_de_job(job) as dentro:
        assert dentro is True
        with az.candado_de_job(job) as segundo:
            assert segundo is False, "dos analisis del mismo job a la vez"
        # Otro job no se estorba
        with az.candado_de_job(str(uuid.uuid4())) as otro:
            assert otro is True
    motor.dispose()


def test_el_candado_se_suelta_al_salir(monkeypatch):
    """Aqui estaba el fallo de la version original de v2.

    Soltaba el candado con `pg_advisory_unlock` desde una sesion que devolvia
    la conexion al pool entre medias, dentro de un `except: pass`. Si caia en
    otra conexion fallaba en silencio y el job se quedaba sin poder analizarse
    nunca mas. Una primera version de este arreglo cerraba la conexion
    confiando en que eso bastara: con el pool normal NO basta —`close()` solo
    la devuelve— y este test lo caza.
    """
    import analysis.analyzer as az
    from sqlalchemy import create_engine

    motor = create_engine(URL_BD)
    monkeypatch.setattr("shared.database.engine", motor, raising=False)

    job = str(uuid.uuid4())
    with az.candado_de_job(job) as dentro:
        assert dentro is True
    # Y ahora tiene que poder volver a entrar.
    with az.candado_de_job(job) as otra_vez:
        assert otra_vez is True, "el candado quedo pillado al salir"
    motor.dispose()


def test_si_revienta_dentro_tampoco_queda_pillado(monkeypatch):
    import analysis.analyzer as az
    from sqlalchemy import create_engine

    motor = create_engine(URL_BD)
    monkeypatch.setattr("shared.database.engine", motor, raising=False)

    job = str(uuid.uuid4())
    with pytest.raises(RuntimeError):
        with az.candado_de_job(job) as dentro:
            assert dentro is True
            raise RuntimeError("el analisis se cae a medias")
    with az.candado_de_job(job) as otra_vez:
        assert otra_vez is True, "una excepcion dejo el candado puesto"
    motor.dispose()


def test_la_clave_es_estable_entre_procesos():
    from analysis.analyzer import _clave_candado

    job = "531dbc5e-9039-4550-b185-cbb924d67f13"
    assert _clave_candado(job) == _clave_candado(job)
    assert _clave_candado(uuid.UUID(job)) == _clave_candado(job)
