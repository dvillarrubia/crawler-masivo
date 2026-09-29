"""compute_pagerank contra Postgres de verdad: repeticion, canonical, 301 y 404.

La parte que monta las aristas es SQL de Postgres (tablas temporales,
`substring` con regex, cursor con nombre) y no corre en SQLite. Este test se
salta si no hay una BD de pruebas; para correrlo:

    docker exec crawler-masivo-postgres-1 createdb -U crawler crawler_test
    PAGERANK_TEST_DATABASE_URL=postgresql+psycopg2://crawler:crawler@postgres:5432/crawler_test pytest tests/test_pagerank_db.py

NUNCA apuntarlo a la BD de trabajo: crea y borra un job propio, pero crea
tablas si faltan.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL")

np = pytest.importorskip("numpy")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.models import Base, HtmlMeta, Job, Link, Url  # noqa: E402

BASE = "https://x.com"
N_ARTICULOS = 24


def _h(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


@pytest.fixture()
def sesion():
    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture()
def job(sesion):
    """Blog de 24 articulos con menu en todas las paginas, una variante
    canonicalizada, una 301 y un 404."""
    j = Job(id=uuid.uuid4(), name="test pagerank", status="analyzing",
            seeds=[BASE + "/"], config={})
    sesion.add(j)
    sesion.flush()

    filas: dict[str, Url] = {}

    def url(ruta, status=200, html=True, indexable=True, estado=None,
            redirect=None, depth=1):
        u = Url(job_id=j.id, url=BASE + ruta, url_hash=_h(BASE + ruta),
                is_internal=True, status_code=status, is_html=html,
                indexable=indexable, indexability_status=estado,
                redirect_url=redirect, crawl_depth=depth)
        sesion.add(u)
        filas[ruta] = u
        return u

    url("/", depth=0)
    url("/contacto")
    for i in range(N_ARTICULOS):
        url(f"/blog/a{i}")
    url("/blog/a1?orden=precio", indexable=False, estado="Canonicalised")
    url("/viejo", status=301, html=False, indexable=False,
        estado="3xx Redirect", redirect=BASE + "/blog/a2")
    url("/roto", status=404, indexable=False, estado="4xx Client Error")
    sesion.flush()
    sesion.add(HtmlMeta(url_id=filas["/blog/a1?orden=precio"].id,
                        canonical_href=BASE + "/blog/a1"))

    def enlace(desde, hacia, anchor, pos):
        sesion.add(Link(job_id=j.id, from_url_id=filas[desde].id,
                        to_url=BASE + hacia, to_url_hash=_h(BASE + hacia),
                        anchor_text=anchor, is_internal=True, follow=True,
                        link_position=pos))

    con_menu = ["/", "/contacto", "/blog/a1?orden=precio"] + [
        f"/blog/a{i}" for i in range(N_ARTICULOS)]
    for p in con_menu:
        enlace(p, "/contacto", "Contacto", "nav")
        enlace(p, "/", "Inicio", "nav")
    for i in range(N_ARTICULOS):
        enlace("/", f"/blog/a{i}", f"Articulo {i}", "content")
    enlace("/blog/a3", "/roto", "enlace roto", "content")
    enlace("/blog/a4", "/viejo", "enlace a la 301", "content")
    enlace("/blog/a5", "/blog/a1?orden=precio", "variante", "content")
    # La variante enlaza a a9: al consolidarse en la canonica, ese enlace no
    # debe contar (son los enlaces de la canonica, no se suman dos veces)
    enlace("/blog/a1?orden=precio", "/blog/a9", "solo en la variante", "content")
    sesion.commit()
    yield j, filas
    sesion.delete(sesion.get(Job, j.id))
    sesion.commit()


def test_pagerank_con_repeticion_canonical_y_sumideros(sesion, job):
    from analysis.analyzer import SEOAnalyzer

    j, filas = job
    SEOAnalyzer(sesion, str(j.id)).compute_pagerank()
    sesion.commit()

    pr = dict(sesion.execute(
        select(Url.url, Url.pagerank).where(Url.job_id == j.id)).all())
    articulo = lambda i: pr[f"{BASE}/blog/a{i}"]  # noqa: E731

    resumen = sesion.get(Job, j.id).pagerank_resumen
    assert resumen["peso"] == "repeticion"
    assert resumen["aristas_redireccion"] == 1
    assert resumen["aristas_canonical"] == 1
    # El 404 recibe por su enlace entrante: eso es lo desperdiciado
    assert resumen["desperdiciado"] > 0
    assert sum(resumen["reparto"].values()) == pytest.approx(1.0, abs=1e-3)

    # La canonica recibe lo de su variante; el destino de la 301, lo del salto
    assert articulo(1) > articulo(6)
    assert articulo(2) > articulo(6)
    # El enlace que solo tenia la variante no cuenta tras consolidar
    assert articulo(9) == pytest.approx(articulo(6), rel=0.01)


@pytest.mark.parametrize("posicion", ["content", "nav", "footer", "desconocida", None])
@pytest.mark.parametrize("rep", [0.0, 0.1, 0.2, 0.3, 0.95])
def test_sql_del_peso_coincide_con_python(sesion, posicion, rep):
    from sqlalchemy import bindparam, String, Float, text

    from analysis import pagerank as prk

    sql = prk.sql_peso_arista("CAST(:rep AS float8)", "CAST(:pos AS text)")
    valor = sesion.execute(
        text(f"SELECT {sql}").bindparams(
            bindparam("rep", type_=Float), bindparam("pos", type_=String)),
        {"rep": rep, "pos": posicion},
    ).scalar()
    assert float(valor) == pytest.approx(prk.peso_arista(posicion, rep))
