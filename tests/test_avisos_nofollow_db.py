"""El `nofollow` dentro del propio sitio (decision 183), contra SQLite.

Se EJECUTA el analizador (decision 74): `compute_link_counts` monta la temporal
de aristas, la profundidad de clic emite `solo_enlazada_con_nofollow` en vez de
`not_reachable_from_home`, y el aviso por destino sale de la misma temporal.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, event, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from analysis import avisos_de_nofollow
from shared.models import Base, Hreflang, HtmlMeta, Issue, Job, Link, Url
from shared.rel_de_enlace import (rel_sin_autoridad, rel_tokens, sql_nofollow_por_rel,
                                  sql_rel_sin_autoridad)

URL_PG = os.environ.get("PAGERANK_TEST_DATABASE_URL")


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar(config=None):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, Link.__table__,
        Hreflang.__table__, HtmlMeta.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config=config or {})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, *, status=200, noindex=False, indexable=True, meta_robots=None,
         x_robots=None, googlebot=None):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
            is_html=True, status_code=status, status_group=f"{status // 100}xx",
            host="x.com", crawl_depth=1, noindex=noindex, indexable=indexable)
    s.add(u)
    s.flush()
    s.add(HtmlMeta(url_id=u.id, meta_robots=meta_robots, x_robots_tag=x_robots,
                   meta_robots_googlebot=googlebot))
    s.flush()
    return u


def _enlace(s, j, origen, destino, *, follow=True, rel=None, veces=1):
    s.add(Link(job_id=j.id, from_url_id=origen.id, to_url=f"https://x.com{destino}",
               to_url_hash=destino, is_internal=True, link_position="content",
               follow=follow, rel=rel, repeticiones=veces))
    s.flush()


def _contar(s, j):
    az = SEOAnalyzer(s, j.id)
    az.compute_link_counts()
    az._flush_issues()
    s.flush()


def _issues(s, j, tipo):
    return s.execute(
        select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
        .where(Issue.job_id == j.id, Issue.issue_type == tipo).order_by(Issue.url_id)
    ).all()


# -- solo_enlazada_con_nofollow -------------------------------------------------

def test_solo_con_nofollow_sustituye_a_sin_camino_desde_la_home():
    s, j = _montar()
    home = _url(s, j, "/")
    a = _url(s, j, "/a")
    pagina_nf = _url(s, j, "/b", meta_robots="index, nofollow")
    destino = _url(s, j, "/oculta")
    _enlace(s, j, home, "/a")
    _enlace(s, j, home, "/b")
    _enlace(s, j, a, "/oculta", follow=False, rel="nofollow")
    _enlace(s, j, pagina_nf, "/oculta", follow=False)  # nofollow de la pagina
    _contar(s, j)
    [fila] = _issues(s, j, "solo_enlazada_con_nofollow")
    assert fila.url_id == destino.id and fila.severity == "warning"
    assert fila.details["paginas_que_enlazan"] == 2
    assert (fila.details["con_rel_nofollow"], fila.details["con_nofollow_de_pagina"]) == (1, 1)
    assert fila.details["paginas_ejemplo"] == ["https://x.com/a", "https://x.com/b"]
    # Un aviso por hallazgo: ni el generico ni el de los enlaces nofollow.
    assert _issues(s, j, "not_reachable_from_home") == []
    assert _issues(s, j, "enlace_interno_nofollow") == []


def test_con_un_enlace_seguible_no_es_solo_nofollow():
    # Uno seguible desde una pagina sin camino: sigue sin camino desde la
    # home, pero la causa no es el nofollow.
    s, j = _montar()
    home = _url(s, j, "/")
    a = _url(s, j, "/a")
    suelta = _url(s, j, "/suelta")
    destino = _url(s, j, "/oculta")
    _enlace(s, j, home, "/a")
    _enlace(s, j, a, "/oculta", follow=False, rel="nofollow")
    _enlace(s, j, suelta, "/oculta")
    _contar(s, j)
    assert _issues(s, j, "solo_enlazada_con_nofollow") == []
    assert destino.id in {f.url_id for f in _issues(s, j, "not_reachable_from_home")}
    # El nofollow desde /a si sale, por destino.
    [fila] = _issues(s, j, "enlace_interno_nofollow")
    assert fila.url_id == destino.id


def test_un_nofollow_desde_una_noindex_no_cuenta():
    s, j = _montar()
    home = _url(s, j, "/")
    ni = _url(s, j, "/ni", noindex=True, indexable=False)
    _url(s, j, "/oculta")
    _enlace(s, j, home, "/ni")
    _enlace(s, j, ni, "/oculta", follow=False, rel="nofollow")
    _contar(s, j)
    # Sin entrantes que cuenten: es huerfana (otro aviso), no «solo nofollow».
    assert _issues(s, j, "solo_enlazada_con_nofollow") == []
    assert _issues(s, j, "enlace_interno_nofollow") == []


def test_con_un_tope_que_corto_no_se_afirma():
    s, j = _montar()
    j.topes_carpeta = {"sin_rastrear": 5}
    s.flush()
    home = _url(s, j, "/")
    a = _url(s, j, "/a")
    _url(s, j, "/oculta")
    _enlace(s, j, home, "/a")
    _enlace(s, j, a, "/oculta", follow=False, rel="nofollow")
    az = SEOAnalyzer(s, j.id)
    assert az._topes_cortaron()
    az.compute_link_counts()
    az._flush_issues()
    assert _issues(s, j, "solo_enlazada_con_nofollow") == []
    assert _issues(s, j, "not_reachable_from_home") == []


# -- enlace_interno_nofollow ------------------------------------------------------

def test_nofollow_del_menu_a_una_indexable_es_una_fila_de_plantilla():
    s, j = _montar()
    home = _url(s, j, "/")
    hoteles = _url(s, j, "/hoteles")
    paginas = [_url(s, j, f"/p{i:02d}") for i in range(12)]
    _enlace(s, j, home, "/hoteles")  # seguible: tiene camino
    for p in paginas:
        _enlace(s, j, home, p.url.removeprefix("https://x.com"))
        _enlace(s, j, p, "/hoteles", follow=False, rel="nofollow noopener", veces=2)
    _contar(s, j)
    [fila] = _issues(s, j, "enlace_interno_nofollow")
    assert fila.url_id == hoteles.id and fila.severity == "info"
    d = fila.details
    assert d["alcance"] == "plantilla"
    assert (d["paginas_con_nofollow"], d["paginas_que_enlazan"], d["enlaces"]) == (12, 13, 24)
    assert len(d["paginas_ejemplo"]) == 5 and d["paginas_ejemplo"][0] == "https://x.com/p00"
    assert _issues(s, j, "solo_enlazada_con_nofollow") == []


def test_nofollow_a_una_no_indexable_no_avisa():
    s, j = _montar()
    home = _url(s, j, "/")
    _url(s, j, "/login", noindex=True, indexable=False)
    _url(s, j, "/carrito", status=302)
    _enlace(s, j, home, "/login", follow=False, rel="nofollow")
    _enlace(s, j, home, "/carrito", follow=False, rel="nofollow")
    _contar(s, j)
    assert _issues(s, j, "enlace_interno_nofollow") == []


def test_ugc_y_sponsored_cuentan_y_el_nofollow_de_pagina_no():
    s, j = _montar()
    home = _url(s, j, "/")
    nf = _url(s, j, "/nf", meta_robots="nofollow")
    a, b = _url(s, j, "/a"), _url(s, j, "/b")
    _enlace(s, j, home, "/nf")
    _enlace(s, j, home, "/a")
    _enlace(s, j, home, "/b")
    _enlace(s, j, a, "/b", follow=False, rel="UGC")
    _enlace(s, j, nf, "/a", follow=False)  # por la directiva: es `pagina_nofollow`
    _contar(s, j)
    filas = _issues(s, j, "enlace_interno_nofollow")
    assert [f.url_id for f in filas] == [b.id]
    assert filas[0].details["alcance"] == "pagina"


def test_no_anade_lecturas_de_links():
    # Todo sale de la temporal de aristas (decisiones 37 y 41).
    s, j = _montar()
    home = _url(s, j, "/")
    _url(s, j, "/a")
    _enlace(s, j, home, "/a", follow=False, rel="nofollow")
    engine = s.get_bind()
    sentencias = []

    def _ver(conn, cursor, sql, params, context, executemany):
        sentencias.append(sql)

    event.listen(engine, "before_cursor_execute", _ver)
    try:
        _contar(s, j)
    finally:
        event.remove(engine, "before_cursor_execute", _ver)
    assert len([x for x in sentencias if "from links" in x.lower()
                or "join links" in x.lower()]) == 1, sentencias


# -- pagina_nofollow --------------------------------------------------------------

def _paginas_nofollow(s, j):
    az = SEOAnalyzer(s, j.id)
    avisos_de_nofollow.avisar_paginas_nofollow(az)
    s.flush()
    return _issues(s, j, "pagina_nofollow")


def test_indexables_con_nofollow_de_pagina_agrupadas_por_plantilla():
    s, j = _montar()
    _url(s, j, "/curso/uno", meta_robots="noarchive, nofollow, nosnippet")
    _url(s, j, "/curso/dos", meta_robots="nofollow")
    _url(s, j, "/curso/tres", x_robots="googlebot: none")
    _url(s, j, "/blog/x", googlebot="nofollow")
    _url(s, j, "/curso/cuatro", meta_robots="index, follow")
    _url(s, j, "/curso/cinco", meta_robots="index/nofollow")       # sintaxis mala: otro aviso
    _url(s, j, "/curso/seis", x_robots="bingbot: nofollow")        # no es para Google
    _url(s, j, "/curso/siete", meta_robots="noindex, nofollow", noindex=True,
         indexable=False)                                          # ya es noindex
    filas = _paginas_nofollow(s, j)
    por_plantilla = {f.details["plantilla"]: f for f in filas}
    assert len(filas) == 2 and all(f.severity == "warning" for f in filas)
    curso = next(f for f in filas if f.details["paginas_afectadas"] == 3)
    assert curso.details["paginas_ejemplo"] == [
        "https://x.com/curso/dos", "https://x.com/curso/tres", "https://x.com/curso/uno"]
    assert curso.details["fuente"] == "meta_robots"
    assert curso.details["directiva"] == "nofollow"
    blog = next(f for f in filas if f.details["paginas_afectadas"] == 1)
    assert blog.details["fuente"] == "meta_googlebot"
    assert len(por_plantilla) == 2 and curso.group_key and blog.group_key != curso.group_key


def test_nofollow_por_rel_es_true_o_null():
    s, j = _montar()
    home = _url(s, j, "/")
    _url(s, j, "/a")
    _enlace(s, j, home, "/a", follow=False, rel="sponsored")
    _enlace(s, j, home, "/a", follow=True, rel="noopener")
    _enlace(s, j, home, "/a", follow=False, rel=None)
    # follow=True manda: el `rel` solo se mira si el spider ya lo marco.
    _enlace(s, j, home, "/a", follow=True, rel="nofollow")
    valores = [v for (v,) in s.execute(
        select(sql_nofollow_por_rel(Link.follow, Link.rel))
        .where(Link.job_id == j.id).order_by(Link.id))]
    assert [bool(v) if v is not None else None for v in valores] == [True, None, None, None]


# La regla del spider (Python) y la del analizador (SQL) son la misma: se
# comprueba sobre los mismos `rel`, en SQLite y, si hay, en Postgres.
RELS = [
    None, "", "nofollow", "NoFollow", "nofollow noopener", "noopener nofollow",
    "nofollow,noopener", "noopener ,  ugc", "nofollow\tnoopener", "x\nsponsored\r",
    "nofollow\xa0noopener", "nofollowed", "x-ugc", "no-follow", "sponsoredby",
    "noopener noreferrer", "external", "  ugc  ", "UGC", ",,nofollow,,", "follow",
    "nofollow;noopener",
]


def _paridad(engine):
    from sqlalchemy import Column, Integer, MetaData, Table, Text, insert

    t = Table("rels_paridad", MetaData(), Column("id", Integer, primary_key=True),
              Column("rel", Text))
    t.create(engine)
    try:
        with engine.begin() as c:
            c.execute(insert(t), [{"id": i, "rel": r} for i, r in enumerate(RELS)])
            sql = {i: bool(v) for i, v in c.execute(
                select(t.c.id, sql_rel_sin_autoridad(t.c.rel)))}
        python = {i: rel_sin_autoridad(r) for i, r in enumerate(RELS)}
        assert sql == python, [(RELS[i], python[i], sql[i]) for i in python if sql[i] != python[i]]
        # Y no es trivial: hay de los dos.
        assert any(python.values()) and not all(python.values())
        assert python[RELS.index("nofollowed")] is False
        assert python[RELS.index("x-ugc")] is False
    finally:
        t.drop(engine)


def test_la_regla_sql_del_rel_es_la_del_spider_en_sqlite():
    _paridad(create_engine("sqlite://"))


@pytest.mark.skipif(not URL_PG, reason="sin PAGERANK_TEST_DATABASE_URL")
def test_la_regla_sql_del_rel_es_la_del_spider_en_postgres():
    _paridad(create_engine(URL_PG))


def test_el_extractor_usa_la_regla_de_shared():
    # Una sola regla (decisiones 23 y 64): el extractor no tiene la suya.
    from parsel import Selector

    from crawler.seo_crawler import extractors

    assert extractors.rel_tokens is rel_tokens
    html = ('<a href="/a" rel="nofollow,noopener">a</a><a href="/b" rel="nofollowed">b</a>'
            '<a href="/c" rel="x-ugc ugc">c</a>')
    enlaces = extractors.extract_links(Selector(text=html), "https://x.com/", {"x.com"})
    assert [(e["url"].rsplit("/", 1)[1], e["follow"]) for e in enlaces] == [
        ("a", False), ("b", True), ("c", False)]


# -- casos limite de solo_enlazada_con_nofollow -------------------------------------

def test_destino_de_una_redireccion_no_es_solo_nofollow():
    # /oculta recibe directamente solo nofollow, pero /vieja (un 301 a ella,
    # tambien sin camino) la enlaza con follow desde /suelta: el sitio SI la
    # enlaza con follow, a traves del salto. No se afirma «solo nofollow».
    s, j = _montar()
    home = _url(s, j, "/")
    a = _url(s, j, "/a")
    suelta = _url(s, j, "/suelta")
    vieja = _url(s, j, "/vieja", status=301, indexable=False)
    vieja.redirect_url = "https://x.com/oculta"
    destino = _url(s, j, "/oculta")
    s.flush()
    _enlace(s, j, home, "/a")
    _enlace(s, j, a, "/oculta", follow=False, rel="nofollow")
    _enlace(s, j, suelta, "/vieja")
    _contar(s, j)
    assert _issues(s, j, "solo_enlazada_con_nofollow") == []
    assert destino.id in {f.url_id for f in _issues(s, j, "not_reachable_from_home")}
