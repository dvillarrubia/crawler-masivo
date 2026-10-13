"""Profundidad de clic contra SQLite: se cuenta desde la home, no desde las semillas.

`crawl_depth` son los saltos del rastreador desde donde empezo: cada semilla
es 0. En el censo semanal de Lopesan (3.040 semillas) 2.867 de 3.237
indexables salian a profundidad 0, y desde la home estaban entre 1 y 12
clics. `click_depth` recorre el grafo desde la raiz del host y las portadas
de idioma, como Google.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Hreflang, Issue, Job, Link, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar(semillas=("https://x.com/",)):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, Link.__table__, Hreflang.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=list(semillas), config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, status=200, crawl_depth=0, redirect=None, noindex=False,
         indexable=True):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta,
            is_internal=True, is_html=True, status_code=status,
            crawl_depth=crawl_depth, noindex=noindex, indexable=indexable,
            redirect_url=f"https://x.com{redirect}" if redirect else None)
    s.add(u)
    s.flush()
    return u


def _enlace(s, j, origen, destino, follow=True):
    s.add(Link(job_id=j.id, from_url_id=origen.id, to_url=f"https://x.com{destino}",
               to_url_hash=destino, is_internal=True, link_position="content",
               follow=follow))


def _analizar(s, j):
    SEOAnalyzer(s, j.id).compute_link_counts()
    s.commit()
    return {u.url.removeprefix("https://x.com"): u.click_depth
            for u in s.execute(select(Url).where(Url.job_id == j.id)).scalars()}


def test_se_cuenta_desde_la_home_y_no_desde_las_semillas():
    s, j = _montar(semillas=["https://x.com/", "https://x.com/a/b/c"])
    home, a, b = _url(s, j, "/"), _url(s, j, "/a"), _url(s, j, "/a/b")
    c = _url(s, j, "/a/b/c")  # semilla: crawl_depth 0, pero esta a 3 clics
    _enlace(s, j, home, "/a")
    _enlace(s, j, a, "/a/b")
    _enlace(s, j, b, "/a/b/c")
    _enlace(s, j, c, "/")
    s.commit()
    assert _analizar(s, j) == {"/": 0, "/a": 1, "/a/b": 2, "/a/b/c": 3}


def test_el_camino_mas_corto_gana():
    s, j = _montar()
    home, a = _url(s, j, "/"), _url(s, j, "/a")
    _url(s, j, "/lejos")
    _enlace(s, j, home, "/a")
    _enlace(s, j, a, "/lejos")
    _enlace(s, j, home, "/lejos")
    s.commit()
    assert _analizar(s, j)["/lejos"] == 1


def test_una_redireccion_no_es_un_clic():
    # La raiz de Lopesan es un 302 a /es/: /es/ es la home, profundidad 0.
    s, j = _montar()
    _url(s, j, "/", status=302, redirect="/es/")
    es = _url(s, j, "/es/")
    _url(s, j, "/viejo", status=301, redirect="/nuevo")
    _url(s, j, "/nuevo")
    _enlace(s, j, es, "/viejo")
    s.commit()
    d = _analizar(s, j)
    assert (d["/"], d["/es/"], d["/viejo"], d["/nuevo"]) == (0, 0, 1, 1)


def test_las_portadas_de_idioma_de_la_home_cuentan_como_home():
    s, j = _montar()
    es = _url(s, j, "/es/")
    _url(s, j, "/", status=302, redirect="/es/")
    en = _url(s, j, "/en/")
    _url(s, j, "/en/hotels")
    _enlace(s, j, en, "/en/hotels")
    s.add(Hreflang(url_id=es.id, lang="en", href="https://x.com/en/"))
    # Un hreflang de cualquier otra pagina no convierte su destino en portada
    otra = _url(s, j, "/es/hoteles")
    _url(s, j, "/de/hotels")
    s.add(Hreflang(url_id=otra.id, lang="de", href="https://x.com/de/hotels"))
    _enlace(s, j, es, "/es/hoteles")
    s.commit()
    d = _analizar(s, j)
    assert (d["/en/"], d["/en/hotels"], d["/de/hotels"]) == (0, 1, None)


def test_de_noindex_404_y_nofollow_no_sale_ningun_camino():
    s, j = _montar()
    home = _url(s, j, "/")
    noidx = _url(s, j, "/paginacion/2", noindex=True, indexable=False)
    roto = _url(s, j, "/roto", status=404, indexable=False)
    for ruta in ("/tras-noindex", "/tras-404", "/tras-nofollow"):
        _url(s, j, ruta)
    _enlace(s, j, home, "/paginacion/2")
    _enlace(s, j, home, "/roto")
    _enlace(s, j, home, "/tras-nofollow", follow=False)
    _enlace(s, j, noidx, "/tras-noindex")
    _enlace(s, j, roto, "/tras-404")
    s.commit()
    d = _analizar(s, j)
    # Las que no reparten si tienen profundidad: se llega a ellas
    assert (d["/paginacion/2"], d["/roto"]) == (1, 1)
    assert d["/tras-noindex"] is None
    assert d["/tras-404"] is None
    assert d["/tras-nofollow"] is None


def test_enlazada_sin_camino_desde_la_home_avisa_y_la_huerfana_no():
    s, j = _montar(semillas=["https://x.com/", "https://x.com/isla/a"])
    _url(s, j, "/")
    isla_a, isla_b = _url(s, j, "/isla/a"), _url(s, j, "/isla/b")
    _url(s, j, "/huerfana")
    _enlace(s, j, isla_a, "/isla/b")
    _enlace(s, j, isla_b, "/isla/a")
    s.commit()
    analizador = SEOAnalyzer(s, j.id)
    analizador.compute_link_counts()
    analizador._flush_issues()
    s.commit()
    avisadas = set(s.execute(
        select(Url.url).join(Issue, Issue.url_id == Url.id)
        .where(Issue.issue_type == "not_reachable_from_home")
    ).scalars())
    # /huerfana no tiene entrantes: eso ya es orphan_page (un aviso por hallazgo)
    assert avisadas == {"https://x.com/isla/a", "https://x.com/isla/b"}


def test_sin_la_home_rastreada_no_se_inventa_nada():
    s, j = _montar(semillas=["https://x.com/a"])
    a = _url(s, j, "/a")
    _url(s, j, "/b")
    _enlace(s, j, a, "/b")
    s.commit()
    analizador = SEOAnalyzer(s, j.id)
    analizador.compute_link_counts()
    analizador._flush_issues()
    s.commit()
    assert set(_analizar(s, j).values()) == {None}
    assert not s.execute(select(Issue).where(
        Issue.issue_type == "not_reachable_from_home")).first()
