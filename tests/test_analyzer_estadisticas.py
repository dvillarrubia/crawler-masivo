"""Decirle a Postgres cuántas filas acaba de insertar el rastreo.

El rastreo mete millones de enlaces y el planificador no se entera hasta que
el autovacuum pasa por ahí. Como las filas nuevas son de un `job_id` que no
existía cuando se tomaron las últimas estadísticas, la estimación para ese job
es **1 fila**, y con eso Postgres elige planes de bucle anidado para
agregaciones de millones.

Medido en producción el 2026-10-13 con el censo semanal de Lopesan (5.276 URLs,
1.431.667 enlaces): la materialización de aristas llevaba **1 h 3 min sin
terminar** y hubo que cancelarla. El `EXPLAIN` de `count(*) FROM links WHERE
job_id=…` estimaba **1 fila** (coste 8,58); tras un `ANALYZE`, 1,43 M de filas
(coste 342.082).
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Job


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _analizador():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    jid = uuid.uuid4()
    s.add(Job(id=jid, name="t", status="analyzing", seeds=["https://x.com/"],
              config={}))
    s.commit()
    return SEOAnalyzer(s, jid)


def test_en_sqlite_no_hace_nada_y_no_revienta():
    """Los tests del analyzer corren contra SQLite, donde no hay planificador
    al que informar ni sentencia `ANALYZE <tabla>` equivalente."""
    a = _analizador()
    a.actualizar_estadisticas()      # no debe lanzar
    a.session.close()


def test_en_postgres_analiza_links_y_urls(monkeypatch):
    """Las dos tablas que mandan en los planes de las agregaciones pesadas."""
    a = _analizador()
    sentencias = []

    class _DialectoFalso:
        name = "postgresql"

    class _BindFalso:
        dialect = _DialectoFalso()

    monkeypatch.setattr(type(a.session), "bind",
                        property(lambda self: _BindFalso()), raising=False)
    monkeypatch.setattr(a.session, "execute",
                        lambda sent, *a_, **k: sentencias.append(str(sent)))
    monkeypatch.setattr(a.session, "commit", lambda: None)

    a.actualizar_estadisticas()

    assert sentencias == ["ANALYZE links", "ANALYZE urls"]


def test_si_no_se_pueden_actualizar_el_analisis_SIGUE(monkeypatch, caplog):
    """Lo único que se pierde es velocidad. Que un permiso o un bloqueo dejara
    un censo sin analizar sería cambiar un problema por otro peor.
    """
    import logging

    a = _analizador()

    class _DialectoFalso:
        name = "postgresql"

    class _BindFalso:
        dialect = _DialectoFalso()

    monkeypatch.setattr(type(a.session), "bind",
                        property(lambda self: _BindFalso()), raising=False)

    def explota(*args, **kw):
        raise RuntimeError("permission denied for table links")

    monkeypatch.setattr(a.session, "execute", explota)
    monkeypatch.setattr(a.session, "rollback", lambda: None)

    with caplog.at_level(logging.WARNING):
        a.actualizar_estadisticas()      # no debe lanzar

    assert any("estadisticas" in r.message for r in caplog.records)
    assert any("mas lento" in r.message for r in caplog.records)


def test_run_all_actualiza_las_estadisticas_ANTES_de_nada():
    """Se EJECUTA `run_all` y se comprueba que la llamada ocurrio primero.

    Primero porque `clear_existing_issues` y todo lo que sigue ya consultan, y
    de nada sirve informar al planificador a mitad de la pasada.
    """
    a = _analizador()
    orden = []
    original = a.actualizar_estadisticas
    a.actualizar_estadisticas = lambda: (orden.append("estadisticas"), original())[1]
    limpiar = a.clear_existing_issues
    a.clear_existing_issues = lambda: (orden.append("limpiar"), limpiar())[1]

    a.run_all()

    assert orden[:2] == ["estadisticas", "limpiar"]
    a.session.close()
