"""Segmentos materializados en `urls.segmento` (decision 108), EJECUTANDO el
analisis y el re-segmentado contra una base de verdad (SQLite; Postgres con
PAGERANK_TEST_DATABASE_URL, como test_soft404).

- `run_all` deja cada URL con su segmento y `jobs.segmentacion` con el resumen;
- re-segmentar con reglas nuevas cambia el censo sin re-rastrear (retroactivo);
- funciona tambien con los enlaces archivados en Parquet, que el analisis
  entero rechaza: segmentar no lee `links`.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytest.importorskip("regex")

from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.models import Base, CustomExtraction, HtmlMeta, Job, Url  # noqa: E402

URL_PG = os.environ.get("PAGERANK_TEST_DATABASE_URL")
MOTORES = ["sqlite"] + (["postgres"] if URL_PG else [])


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


REGLAS = [
    {"nombre": "blog", "patron": "^/blog/"},
    {"nombre": "fichas", "si": {"x": "og_type", "igual": "product"}},
    {"nombre": "categorias", "si": {"todas": [{"x": "og_type", "igual": "website"},
                                              {"campo": "status_group", "igual": "2xx"}]}},
]


@pytest.fixture(params=MOTORES)
def censo(request, tmp_path):
    engine = create_engine(URL_PG) if request.param == "postgres" else create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    job_id = uuid.uuid4()
    s.add(Job(id=job_id, name="t", status="analyzing", seeds=["https://x.com/"],
              config={"segmentos": REGLAS}))
    s.flush()
    ids = {}
    for ruta, og in (("/blog/un-post", "article"), ("/crema-hidratante", "product"),
                     ("/serum-noche", "product"), ("/maquillaje", "website"),
                     ("/maquillaje/ojos", "website"), ("/sin-extraccion", None)):
        u = Url(job_id=job_id, url=f"https://x.com{ruta}", url_hash=uuid.uuid4().hex, host="x.com",
                path=ruta, is_internal=True, is_html=True, status_code=200, status_group="2xx",
                resource_type="html", indexable=True)
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title=f"Titulo de {ruta}"))
        if og:
            s.add(CustomExtraction(job_id=job_id, url_id=u.id, nombre="og_type", clase="extractor",
                                   estado="ok", n=1, valor=og, valor_clave=og))
        ids[ruta] = u.id
    s.commit()
    yield s, job_id, ids, request.param, engine
    s.rollback()
    s.query(Job).filter(Job.id == job_id).delete()
    s.commit()
    s.close()


def _segmentos(s, job_id):
    s.expire_all()
    return {u.path: u.segmento for u in s.query(Url).filter(Url.job_id == job_id)}


def test_el_analisis_materializa_el_segmento(censo, monkeypatch):
    from analysis.analyzer import SEOAnalyzer

    s, job_id, _ids, motor, _ = censo
    if motor == "sqlite":
        for paso in ("compute_link_counts", "compute_pagerank", "analyze_links"):
            monkeypatch.setattr(SEOAnalyzer, paso, lambda self: None)
    SEOAnalyzer(s, job_id).run_all()
    seg = _segmentos(s, job_id)
    assert seg["/blog/un-post"] == "blog"
    assert seg["/crema-hidratante"] == seg["/serum-noche"] == "fichas"
    assert seg["/maquillaje"] == seg["/maquillaje/ojos"] == "categorias"
    # Sin fila de extraccion: no se sabe si es ficha; va a la forma de la ruta.
    assert seg["/sin-extraccion"].startswith("/")
    resumen = s.get(Job, job_id).segmentacion
    assert [r["urls"] for r in resumen["por_regla"]] == [1, 2, 2]
    assert resumen["por_regla"][1]["sin_saber"] == 1   # la de sin-extraccion
    assert resumen["por_defecto"] == 1 and resumen["urls"] == 6


def test_resegmentar_con_reglas_nuevas_es_retroactivo(censo):
    from shared.segmentos import segmentar

    s, job_id, _ids, _motor, _ = censo
    segmentar(s, job_id)
    s.commit()
    assert _segmentos(s, job_id)["/maquillaje/ojos"] == "categorias"
    # Reglas nuevas sobre el MISMO censo, sin tocar nada de lo rastreado.
    nuevas = [{"nombre": "maquillaje", "patron": "^/maquillaje"}] + REGLAS
    resumen = segmentar(s, job_id, nuevas)
    s.commit()
    seg = _segmentos(s, job_id)
    assert seg["/maquillaje"] == seg["/maquillaje/ojos"] == "maquillaje"
    assert seg["/serum-noche"] == "fichas"
    assert resumen["por_regla"][0] == {"nombre": "maquillaje", "urls": 2, "sin_saber": 0}


def test_segmenta_un_censo_con_los_enlaces_archivados(censo):
    from analysis.analyzer import EnlacesArchivados, SEOAnalyzer
    from shared.segmentos import segmentar

    s, job_id, _ids, _motor, _ = censo
    s.get(Job, job_id).enlaces_archivados = {"fichero": "x.parquet", "filas": 10}
    s.commit()
    with pytest.raises(EnlacesArchivados):
        SEOAnalyzer(s, job_id).run_all()
    s.rollback()
    segmentar(s, job_id)
    s.commit()
    assert _segmentos(s, job_id)["/blog/un-post"] == "blog"


def test_tramos_pequenos_dan_lo_mismo(censo):
    """El recorrido por tramos de id (keyset) no se salta ni repite URLs."""
    from shared.segmentos import segmentar

    s, job_id, _ids, _motor, _ = censo
    segmentar(s, job_id)
    s.commit()
    una = _segmentos(s, job_id)
    s.query(Url).filter(Url.job_id == job_id).update({"segmento": None})
    s.commit()
    resumen = segmentar(s, job_id, lote=2)
    s.commit()
    assert _segmentos(s, job_id) == una and resumen["urls"] == 6


N_PLAN = 20_000


@pytest.mark.skipif(not URL_PG, reason="el plan solo se puede mirar en Postgres")
def test_los_tramos_van_por_el_indice_del_censo_no_por_la_clave_de_la_tabla():
    """Sin indice `(job_id, id)`, `id > :ultimo ORDER BY id` obligaba a
    Postgres a elegir entre recorrer `urls_pkey` —la clave de TODOS los
    censos— filtrando por job, u ordenar el censo entero en cada tramo: en una
    tabla de 6 M de filas, 6-12 s solo en eso. Por `(job_id, url_hash)` el
    tramo es un recorrido del indice unico `uq_job_url`, sin filtro ni orden.

    Se EJECUTA `segmentar`, se capturan las consultas que manda y se mira su
    plan (con el recorrido secuencial apagado: en una tabla de pruebas
    pequena Postgres leeria la tabla entera de todas formas)."""
    from sqlalchemy import event

    from shared.segmentos import segmentar

    engine = create_engine(URL_PG)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    job_id = uuid.uuid4()
    s.add(Job(id=job_id, name="t", status="completed", seeds=["https://x.com/"], config={}))
    s.flush()
    # Las suficientes para que el planificador no lo resuelva todo ordenando
    # en memoria: con cinco filas cualquier plan sirve y el test no mide nada.
    from sqlalchemy import insert, text

    s.execute(insert(Url), [
        dict(job_id=job_id, url=f"https://x.com/{i}", url_hash=uuid.uuid4().hex, host="x.com",
             path=f"/{i}", is_internal=True, is_html=True, status_code=200, status_group="2xx",
             resource_type="html") for i in range(N_PLAN)])
    s.commit()
    s.execute(text("ANALYZE urls"))
    s.commit()
    capturadas: list[tuple[str, object]] = []

    def capturar(_conn, _cursor, sentencia, parametros, _ctx, _many):
        if sentencia.lstrip().upper().startswith("SELECT") and "FROM urls" in sentencia:
            capturadas.append((sentencia, parametros))

    event.listen(engine, "before_cursor_execute", capturar)
    try:
        segmentar(s, job_id, lote=5000)
        s.rollback()
    finally:
        event.remove(engine, "before_cursor_execute", capturar)
    assert len(capturadas) >= 3  # 20.000 URLs en tramos de 5.000, y el que vuelve vacio
    crudo = engine.raw_connection()
    try:
        cur = crudo.cursor()
        cur.execute("SET enable_seqscan = off")
        for sentencia, parametros in capturadas:
            cur.execute("EXPLAIN " + sentencia, parametros)
            plan = "\n".join(f[0] for f in cur.fetchall())
            assert "uq_job_url" in plan, plan
            assert "Sort" not in plan and "Filter:" not in plan, plan
    finally:
        crudo.rollback()
        crudo.close()
        s.query(Job).filter(Job.id == job_id).delete()
        s.commit()
        s.close()
