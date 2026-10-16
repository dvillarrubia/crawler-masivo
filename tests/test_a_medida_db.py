"""La tabla `custom_extraction` a escala, contra Postgres de verdad (decision 104).

60.000 URLs x 10 reglas = 600.000 filas, que es el censo de una tienda mediana
con un juego de reglas normal. Lo que se sujeta es la FORMA del plan: filtrar y
ordenar por `x.<nombre>` tiene que usar los indices `ix_cext_*` y no recorrer la
tabla, porque la tabla guarda las filas de TODOS los censos (como `links`,
decision 37).

Se ejecutan las funciones REALES de la API (`_urls_filtradas` y `_ordenar`,
`api/routers/results.py`), no una copia de su consulta (decision 74): una
copia seguia en verde aunque se quitara el `job_id` del join del orden, que es
justo la regresion que anota la decision 104. Por eso corre en el trabajo de la
API, que tiene FastAPI y un Postgres de servicio. Se salta sin
`A_MEDIDA_TEST_DATABASE_URL` (o `PAGERANK_TEST_DATABASE_URL`); nunca apuntarlo
a la BD de trabajo.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

# En CI, `A_MEDIDA_TEST_DATABASE_URL` en el trabajo de la API: con la de
# PAGERANK se destaparian alli otros tests de Postgres que no son de esa imagen.
URL_BD = (os.environ.get("A_MEDIDA_TEST_DATABASE_URL")
          or os.environ.get("PAGERANK_TEST_DATABASE_URL"))
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin A_MEDIDA_TEST_DATABASE_URL")
pytest.importorskip("fastapi")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import shared.models as modelos  # noqa: E402
from shared.models import Base, Url  # noqa: E402

N_URLS = 60_000
N_REGLAS = 10


@pytest.fixture(scope="module")
def censo():
    engine = create_engine(URL_BD)
    # Solo las tablas de `shared.models`: si otro test de la sesion importo la
    # API entera, el MetaData lleva las de pgvector y el Postgres de CI no.
    tablas = [v.__table__ for v in vars(modelos).values()
              if isinstance(v, type) and getattr(v, "__module__", "") == modelos.__name__
              and hasattr(v, "__table__")]
    Base.metadata.create_all(engine, tables=tablas)
    jid = uuid.uuid4()
    otro = uuid.uuid4()
    import json

    config = json.dumps({
        "custom_extraction": {f"r{r}": {"tipo": "css", "expresion": ".p", "valor": "numero"}
                              for r in range(N_REGLAS - 1)},
        "custom_search": {f"r{N_REGLAS - 1}": {"patron": "GTM-"}},
    })
    with engine.begin() as c:
        for j in (jid, otro):
            c.execute(text("INSERT INTO jobs (id, name, status, seeds, config) "
                           "VALUES (:j, 'a medida a escala', 'completed', '[]', :c)"),
                      {"j": str(j), "c": config})
            c.execute(text(f"""
                INSERT INTO urls (job_id, url, url_hash, is_internal, is_html, status_code)
                SELECT :j, 'https://x.com/p/' || g, md5(:j || g), true, true, 200
                  FROM generate_series(1, {N_URLS}) g"""), {"j": str(j)})
            # 10 reglas por URL: precio con valor, y uno de cada 7 sin coincidencia
            c.execute(text(f"""
                INSERT INTO custom_extraction
                       (job_id, url_id, nombre, clase, estado, n, valor, valor_clave, valor_num, truncado)
                SELECT u.job_id, u.id, 'r' || r,
                       CASE WHEN r = 9 THEN 'busqueda' ELSE 'extractor' END,
                       CASE WHEN u.id % 7 = 0 THEN 'sin_coincidencia' ELSE 'ok' END,
                       CASE WHEN u.id % 7 = 0 THEN 0 ELSE 1 END,
                       CASE WHEN u.id % 7 = 0 THEN NULL ELSE (u.id % 997) || ',95 €' END,
                       CASE WHEN u.id % 7 = 0 THEN NULL ELSE (u.id % 997) || ',95 €' END,
                       CASE WHEN u.id % 7 = 0 THEN NULL ELSE (u.id % 997) + 0.95 END,
                       false
                  FROM urls u CROSS JOIN generate_series(0, {N_REGLAS - 1}) r
                 WHERE u.job_id = :j"""), {"j": str(j)})
        c.execute(text("ANALYZE custom_extraction"))
        c.execute(text("ANALYZE urls"))
    yield engine, jid
    with engine.begin() as c:
        for j in (jid, otro):
            c.execute(text("DELETE FROM custom_extraction WHERE job_id = :j"), {"j": str(j)})
            c.execute(text("DELETE FROM urls WHERE job_id = :j"), {"j": str(j)})
            c.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": str(j)})
    engine.dispose()


def _plan(c, consulta) -> str:
    sql = str(consulta.compile(c.engine, compile_kwargs={"literal_binds": True}))
    return "\n".join(r[0] for r in c.execute(text("EXPLAIN " + sql)))


def _escaneos_de_la_tabla(plan: str) -> list[str]:
    """Cada nodo que lee `custom_extraction`, con sus condiciones."""
    lineas = plan.splitlines()
    nodos = []
    for i, linea in enumerate(lineas):
        if " on custom_extraction" in linea:
            sangria = len(linea) - len(linea.lstrip())
            bloque = [linea]
            for sig in lineas[i + 1:]:
                if len(sig) - len(sig.lstrip()) <= sangria or "->" in sig:
                    break
                bloque.append(sig)
            nodos.append("\n".join(bloque))
    return nodos


@pytest.mark.parametrize("filtros", [{"x.r0_gte": "500"}, {}],
                         ids=["filtrado", "censo-entero"])
def test_filtrar_y_ordenar_por_un_valor_extraido_usa_los_indices(censo, filtros):
    """Con las funciones de la API. Cada lectura de `custom_extraction` tiene
    que ir acotada al censo (`job_id`) o a una URL concreta (`url_id`, que ya
    es de este censo): la tabla tiene filas de TODOS los censos. Sin filtro es
    donde muerde: hay que ordenar el censo entero, y sin `job_id` en el join
    se leen las filas de la regla de todos los censos."""
    from api.routers.results import _ordenar, _urls_filtradas

    engine, jid = censo
    Sesion = sessionmaker(bind=engine)
    with Sesion() as db:
        q = _urls_filtradas(db, jid, filtros)
        q = _ordenar(q, "x.r0", "desc", db, jid).limit(50)
        consulta = q.statement
        with engine.connect() as c:
            plan = _plan(c, consulta)
        assert "Seq Scan on custom_extraction" not in plan, plan
        for nodo in _escaneos_de_la_tabla(plan):
            assert "job_id" in nodo or "url_id = " in nodo, plan
        t0 = time.perf_counter()
        filas = q.all()
        ms = (time.perf_counter() - t0) * 1000
        total = _urls_filtradas(db, jid, filtros).count()
    assert len(filas) == 50
    # valor_num de r0 = (id % 997) + 0,95, y NULL en uno de cada 7
    valores = [(u.id % 997) + 0.95 for u in filas]
    assert valores == sorted(valores, reverse=True)
    if filtros:
        assert all(v >= 500 for v in valores)
    assert total > 0
    # El tamano NO se mide aqui: la tabla de pruebas arrastra las filas muertas
    # de las pasadas anteriores mientras otra transaccion las retenga. Medido
    # aparte en una tabla limpia: ver la decision 104.
    print(f"\nfiltrar+ordenar x.r0 sobre {N_URLS * N_REGLAS:,} filas del censo: {ms:.0f} ms")


def test_no_contiene_mira_solo_lo_evaluado(censo):
    from api.routers.results import _urls_filtradas

    engine, jid = censo
    Sesion = sessionmaker(bind=engine)
    with Sesion() as db:
        q = _urls_filtradas(db, jid, {f"x.r{N_REGLAS - 1}": "no"})
        with engine.connect() as c:
            plan = _plan(c, q.statement)
        n = q.count()
    assert "ix_cext_job_nombre_estado" in plan, plan
    assert abs(n - N_URLS / 7) < 2
