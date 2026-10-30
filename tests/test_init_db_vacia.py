"""`init_db()` sobre una base de datos VACIA, como la de una instalacion nueva.

La API llama a `init_db()` al arrancar. Sin la extension `vector`, el
create_all de las tablas semanticas revienta y la API se reiniciaba en bucle
hasta que alguien pasaba `scripts/init_db.py` a mano (docs/instalacion/).
Se ejecuta contra Postgres de verdad: crea una base de datos desechable en el
servidor de PAGERANK_TEST_DATABASE_URL y la borra al acabar.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

URL = os.getenv("PAGERANK_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not URL, reason="sin PAGERANK_TEST_DATABASE_URL")
# Los modelos semanticos lo importan; esta en la imagen de la API, no en la del crawler.
pytest.importorskip("pgvector")


@pytest.fixture()
def base_vacia(monkeypatch):
    nombre = f"init_vacia_{uuid.uuid4().hex[:8]}"
    admin = create_engine(URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{nombre}"'))
    url = admin.url.set(database=nombre)
    engine = create_engine(url)
    import shared.database
    monkeypatch.setattr(shared.database, "engine", engine)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)'))
        admin.dispose()


def test_crea_la_extension_y_las_tablas_en_una_base_vacia(base_vacia):
    from shared.database import init_db

    init_db()

    with base_vacia.connect() as conn:
        assert conn.execute(text(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar() == 1
    tablas = set(inspect(base_vacia).get_table_names())
    assert {"jobs", "urls", "clients", "semantic_analyses"} <= tablas


def test_dos_veces_no_falla(base_vacia):
    from shared.database import init_db

    init_db()
    init_db()
