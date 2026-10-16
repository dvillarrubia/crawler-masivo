"""La tabla `custom_extraction` a escala, contra Postgres de verdad (decision 104).

60.000 URLs x 10 reglas = 600.000 filas, que es el censo de una tienda mediana
con un juego de reglas normal. Lo que se sujeta es la FORMA del plan: filtrar y
ordenar por `x.<nombre>` tiene que usar los indices `ix_cext_*` y no recorrer la
tabla, porque la tabla guarda las filas de TODOS los censos (como `links`,
decision 37).

Las consultas reproducen las que construyen `_filtros_a_medida` y `_ordenar`
(`api/routers/results.py`): esa funcion vive en la imagen de la API, que en CI no
tiene Postgres, y este trabajo no tiene FastAPI. Se salta sin
`PAGERANK_TEST_DATABASE_URL`; nunca apuntarlo a la BD de trabajo.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL")

from sqlalchemy import and_, create_engine, select, text  # noqa: E402
from sqlalchemy.orm import aliased  # noqa: E402

from shared.models import Base, CustomExtraction, Url  # noqa: E402

N_URLS = 60_000
N_REGLAS = 10


@pytest.fixture(scope="module")
def censo():
    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    jid = uuid.uuid4()
    otro = uuid.uuid4()
    with engine.begin() as c:
        for j in (jid, otro):
            c.execute(text("INSERT INTO jobs (id, name, status, seeds, config) "
                           "VALUES (:j, 'a medida a escala', 'completed', '[]', '{}')"),
                      {"j": str(j)})
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


def _filtrar_y_ordenar(jid):
    CE = CustomExtraction
    sub = select(CE.url_id).where(CE.job_id == jid, CE.nombre == "r0", CE.valor_num >= 500)
    A = aliased(CustomExtraction)
    return (select(Url.id, A.valor_num)
            .where(Url.job_id == jid, Url.id.in_(sub))
            .outerjoin(A, and_(A.url_id == Url.id, A.job_id == jid, A.nombre == "r0"))
            .order_by(A.valor_num.desc().nullslast(), A.valor_clave.desc().nullslast(), Url.id)
            .limit(50))


def test_filtrar_y_ordenar_por_un_valor_extraido_usa_los_indices(censo):
    engine, jid = censo
    consulta = _filtrar_y_ordenar(jid)
    with engine.connect() as c:
        sql = str(consulta.compile(engine, compile_kwargs={"literal_binds": True}))
        plan = "\n".join(r[0] for r in c.execute(text("EXPLAIN " + sql)))
        assert "ix_cext_job_nombre_num" in plan or "uq_cext_url_nombre" in plan, plan
        assert "Seq Scan on custom_extraction" not in plan, plan
        t0 = time.perf_counter()
        filas = c.execute(consulta).all()
        ms = (time.perf_counter() - t0) * 1000
    assert len(filas) == 50 and filas[0][1] >= filas[-1][1]
    # El tamano NO se mide aqui: la tabla de pruebas arrastra las filas muertas
    # de las pasadas anteriores mientras otra transaccion las retenga. Medido
    # aparte en una tabla limpia: ver la decision 104.
    print(f"\nfiltrar+ordenar x.r0 sobre {N_URLS * N_REGLAS:,} filas del censo: {ms:.0f} ms")


def test_no_contiene_mira_solo_lo_evaluado(censo):
    engine, jid = censo
    CE = CustomExtraction
    sub = select(CE.url_id).where(CE.job_id == jid, CE.nombre == "r9",
                                  CE.estado == "sin_coincidencia")
    with engine.connect() as c:
        sql = str(select(Url.id).where(Url.job_id == jid, Url.id.in_(sub))
                  .compile(engine, compile_kwargs={"literal_binds": True}))
        plan = "\n".join(r[0] for r in c.execute(text("EXPLAIN " + sql)))
        n = c.execute(text("SELECT count(*) FROM (" + sql + ") t")).scalar()
    assert "ix_cext_job_nombre_estado" in plan, plan
    assert n == N_URLS // 7 or abs(n - N_URLS / 7) < 2
