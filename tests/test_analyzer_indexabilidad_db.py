"""El analyzer recalcula la indexabilidad con los MISMOS datos que el spider (#26).

`analyze_indexability` reescribe `indexable` e `indexability_status` en todas
las URLs del job. No le llegaban ni el `<meta name="googlebot">` —no se
guardaba— ni el canonical de la cabecera `Link` —se guardaba y no se leia—, asi
que una pagina que el rastreo marcaba bien volvia a "Indexable" al analizar.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.indexabilidad import SEPARADOR_CABECERAS
from shared.models import Base, HtmlMeta, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[Job.__table__, Url.__table__, HtmlMeta.__table__, Issue.__table__],
    )
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _pagina(s, j, ruta, estado, **meta):
    u = Url(
        job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
        is_html=True, status_code=200, crawl_depth=1,
        indexable=estado == "Indexable", indexability_status=estado,
    )
    s.add(u)
    s.flush()
    s.add(HtmlMeta(url_id=u.id, **meta))
    s.flush()
    return u


def _analizar(s, j):
    s.commit()
    SEOAnalyzer(s, j.id).analyze_indexability()
    s.commit()


def _estado(s, u):
    return s.execute(
        select(Url.indexable, Url.indexability_status, Url.noindex).where(Url.id == u.id)
    ).one()


def test_el_noindex_de_googlebot_sobrevive_al_analisis():
    s, j = _montar()
    u = _pagina(s, j, "/a", "Noindex", meta_robots="index, follow",
                meta_robots_googlebot="noindex")
    _analizar(s, j)
    assert tuple(_estado(s, u)) == (False, "Noindex", True)


def test_el_canonical_de_la_cabecera_link_sobrevive_al_analisis():
    s, j = _montar()
    u = _pagina(s, j, "/a", "Canonicalised", canonical_header="https://x.com/b")
    _analizar(s, j)
    assert tuple(_estado(s, u))[:2] == (False, "Canonicalised")


def test_las_cabeceras_x_robots_conservan_su_bot():
    s, j = _montar()
    u = _pagina(s, j, "/a", "Noindex",
                x_robots_tag=SEPARADOR_CABECERAS.join(["bingbot: noindex", "googlebot: noindex"]))
    _analizar(s, j)
    assert tuple(_estado(s, u))[:2] == (False, "Noindex")


def test_sin_directivas_sigue_siendo_indexable():
    """El control: nada de lo anterior marca una pagina limpia."""
    s, j = _montar()
    u = _pagina(s, j, "/a", "Indexable", meta_robots="index, follow",
                canonical_href="https://x.com/a")
    _analizar(s, j)
    assert tuple(_estado(s, u)) == (True, "Indexable", False)


def test_un_noindex_detras_de_una_directiva_con_valor_se_corrige_al_reanalizar():
    """Decision 107: `max-snippet:-1, noindex` perdia el noindex (el prefijo se
    tomaba por un bot). El rastreo la guardo "Indexable"; re-analizar lo
    corrige con lo que ya hay en `html_meta`, sin re-rastrear."""
    s, j = _montar()
    u = _pagina(s, j, "/a", "Indexable", x_robots_tag="max-snippet:-1, noindex")
    v = _pagina(s, j, "/b", "Indexable",
                x_robots_tag=SEPARADOR_CABECERAS.join(
                    ["bingbot: noindex", "googlebot: max-snippet:-1, noindex"]))
    w = _pagina(s, j, "/c", "Indexable", x_robots_tag="bingbot: noindex, nofollow")
    _analizar(s, j)
    assert tuple(_estado(s, u)) == (False, "Noindex", True)
    assert tuple(_estado(s, v)) == (False, "Noindex", True)
    assert tuple(_estado(s, w)) == (True, "Indexable", False)
