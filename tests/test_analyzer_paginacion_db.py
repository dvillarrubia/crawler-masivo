"""Paginacion en el analizador, contra SQLite y Postgres (decision 122).

Se EJECUTAN los pasos del analizador (decision 74) sobre series paginadas por
forma de URL (``/page/N``, ``?page=N``) y por ``rel=prev``:

- pagina 2+ con canonical a la 1, agrupada por plantilla; autocanonica y
  canonical a «ver todo», sin aviso;
- pagina 2+ en noindex, agrupada;
- la categoria que solo cuelga de una paginacion noindex (el caso de las 168 de
  blogs.uoc.edu, decision 38): ``solo_enlazada_desde_paginacion_excluida`` y
  NO ``orphan_page`` ni ``sitemap_orphan``;
- ``rel=next`` sin ``<a>`` (paginacion por JavaScript), rastreada o no;
- ``rel=next`` a un 404 (aviso) y a un 429 (sin aviso);
- y que el paso no anade ninguna lectura de ``links`` (#52): se cuentan las
  sentencias que la nombran, con y sin el paso.
"""

from __future__ import annotations

import os
import re
import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, event, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis import avisos_de_paginacion
from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Hreflang, HtmlMeta, Issue, Job, Link, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


H = "https://x.com"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Job.__table__, Url.__table__, Issue.__table__,
                                             Link.__table__, HtmlMeta.__table__, Hreflang.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=[f"{H}/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _pag(s, j, ruta, status=200, *, robots=None, canonical=None, nxt=None, prv=None,
         depth=1, sitemap=None):
    u = Url(job_id=j.id, url=f"{H}{ruta}", url_hash=f"{j.id}{ruta}", is_internal=True,
            is_html=True, status_code=status, status_group=f"{status // 100}xx",
            crawl_depth=depth, in_sitemap=sitemap)
    s.add(u)
    s.flush()
    s.add(HtmlMeta(url_id=u.id, meta_robots=robots,
                   canonical_href=f"{H}{canonical}" if canonical else None,
                   rel_next=f"{H}{nxt}" if nxt else None,
                   rel_prev=f"{H}{prv}" if prv else None))
    s.flush()
    return u


def _a(s, j, origen, ruta, veces=1):
    s.add(Link(job_id=j.id, from_url_id=origen.id, to_url=f"{H}{ruta}",
               to_url_hash=f"{j.id}{ruta}", is_internal=True, link_position="content",
               follow=True, repeticiones=veces))
    s.flush()


def _analizar(s, j):
    az = SEOAnalyzer(s, j.id)
    az.analyze_indexability()
    avisos_de_paginacion.analizar_paginacion(az)
    az.compute_link_counts()
    az.analyze_links()
    az.analyze_sitemap()
    az._flush_issues()
    s.commit()
    return az


def _issues(s, j, tipo):
    return s.execute(
        select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
        .where(Issue.job_id == j.id, Issue.issue_type == tipo).order_by(Issue.url_id)).all()


def _tipos_en(s, j, url_id):
    return {t for (t,) in s.execute(select(Issue.issue_type).where(
        Issue.job_id == j.id, Issue.url_id == url_id))}


# ---------------------------------------------------------------------------
def test_canonical_a_la_primera_agrupado_por_plantilla():
    s, j = _montar()
    home = _pag(s, j, "/", depth=0)
    for nombre in ("zapatos", "bolsos"):
        _pag(s, j, f"/tienda/{nombre}")
        _a(s, j, home, f"/tienda/{nombre}")
    # Las 2+ de dos series de la misma plantilla, canonicalizadas a su 1 (con
    # y sin barra final: la 1 se escribe de las dos formas).
    z2 = _pag(s, j, "/tienda/zapatos?page=2", canonical="/tienda/zapatos")
    z3 = _pag(s, j, "/tienda/zapatos?page=3", canonical="/tienda/zapatos/")
    b2 = _pag(s, j, "/tienda/bolsos?page=2", canonical="/tienda/bolsos")
    # Autocanonica: lo correcto, sin aviso.
    c2 = _pag(s, j, "/tienda/camisas?page=2", canonical="/tienda/camisas?page=2")
    # Canonical a «ver todo»: otro caso, lo cubren los checks de canonical.
    g2 = _pag(s, j, "/tienda/gorros?page=2", canonical="/tienda/gorros/ver-todo")
    # Otro orden es otra serie: su canonical a la 1 SIN orden no es «la 1».
    o2 = _pag(s, j, "/tienda/zapatos?orden=precio&page=2", canonical="/tienda/zapatos")
    s.commit()
    _analizar(s, j)

    filas = _issues(s, j, "paginacion_canonical_a_la_primera")
    assert len(filas) == 1
    f = filas[0]
    assert f.severity == "warning" and f.group_key
    d = f.details
    assert (d["alcance"], d["series"], d["paginas_afectadas"]) == ("plantilla", 2, 3)
    # Anclada en la pagina de URL menor del grupo (decision 88).
    assert f.url_id == b2.id
    assert [e["url"] for e in d["ejemplos"]] == sorted(u.url for u in (z2, z3, b2))
    for sin in (c2, g2, o2):
        assert "paginacion_canonical_a_la_primera" not in _tipos_en(s, j, sin.id)


def test_canonical_a_la_primera_por_rel_prev_sin_marcador_en_la_url():
    s, j = _montar()
    _pag(s, j, "/lista", nxt="/lista?start=20")
    l2 = _pag(s, j, "/lista?start=20", canonical="/lista", prv="/lista", nxt="/lista?start=40")
    l3 = _pag(s, j, "/lista?start=40", canonical="/lista", prv="/lista?start=20")
    s.commit()
    _analizar(s, j)
    filas = _issues(s, j, "paginacion_canonical_a_la_primera")
    assert len(filas) == 1
    assert filas[0].details["paginas_afectadas"] == 2
    assert filas[0].details["plantilla"].endswith(" rel")
    assert filas[0].url_id == l2.id and l3.id != l2.id


def test_noindex_agrupado_y_lo_que_solo_cuelga_de_ahi():
    """El caso de blogs.uoc.edu (decision 38): categorias indexables a las que
    solo enlaza la paginacion noindex. Hoy salian como `orphan_page` (o
    `sitemap_orphan`) sin decir por que; ahora llevan su aviso, que lo dice."""
    s, j = _montar()
    home = _pag(s, j, "/", depth=0)
    blog = _pag(s, j, "/blog/", nxt="/blog/page/2/")
    _a(s, j, home, "/blog/")
    p2 = _pag(s, j, "/blog/page/2/", robots="noindex, follow", prv="/blog/", nxt="/blog/page/3/")
    p3 = _pag(s, j, "/blog/page/3/", robots="noindex, follow", prv="/blog/page/2/")
    _a(s, j, blog, "/blog/page/2/")
    _a(s, j, p2, "/blog/page/3/")
    cat1 = _pag(s, j, "/category/cat1/")
    cat2 = _pag(s, j, "/category/cat2/", sitemap=True)
    cat3 = _pag(s, j, "/category/cat3/")
    for p in (p2, p3):
        _a(s, j, p, "/category/cat1/", veces=2)
    _a(s, j, p3, "/category/cat2/")
    # cat3 tambien la enlaza la home: esta enlazada de verdad.
    _a(s, j, p2, "/category/cat3/")
    _a(s, j, home, "/category/cat3/")
    s.commit()
    _analizar(s, j)

    filas = _issues(s, j, "paginacion_noindex")
    assert len(filas) == 1
    f = filas[0]
    assert f.severity == "info"
    assert (f.details["series"], f.details["paginas_afectadas"]) == (1, 2)
    assert f.url_id == p2.id

    solo = {f.url_id: f for f in _issues(s, j, "solo_enlazada_desde_paginacion_excluida")}
    assert set(solo) == {cat1.id, cat2.id}
    assert solo[cat1.id].severity == "warning"
    d = solo[cat1.id].details
    assert (d["paginas_que_enlazan"], d["enlaces"], d["motivos"]) == (2, 4, {"noindex": 2})
    assert d["ejemplos_origen"] == [p2.url, p3.url]
    assert solo[cat2.id].details["en_sitemap"] is True
    # Un aviso por hallazgo (decision 36).
    assert "orphan_page" not in _tipos_en(s, j, cat1.id)
    assert "sitemap_orphan" not in _tipos_en(s, j, cat2.id)
    assert "solo_enlazada_desde_paginacion_excluida" not in _tipos_en(s, j, cat3.id)
    # Y los entrantes no cambian: un noindex no reparte (decision 38).
    s.expire_all()
    assert s.get(Url, cat1.id).inlinks_count == 0
    assert s.get(Url, cat3.id).inlinks_count == 1


def test_lo_que_cuelga_de_una_paginacion_canonicalizada_a_la_primera():
    """La canonicalizada SI cuenta como entrante (decision 38 no se toca),
    pero lo que solo cuelga de ahi tiene el mismo problema."""
    s, j = _montar()
    home = _pag(s, j, "/", depth=0)
    _pag(s, j, "/tienda/zapatos")
    _a(s, j, home, "/tienda/zapatos")
    z2 = _pag(s, j, "/tienda/zapatos?page=2", canonical="/tienda/zapatos")
    _a(s, j, home, "/tienda/zapatos?page=2")
    prod = _pag(s, j, "/producto/bota")
    _a(s, j, z2, "/producto/bota")
    s.commit()
    _analizar(s, j)
    solo = _issues(s, j, "solo_enlazada_desde_paginacion_excluida")
    assert [f.url_id for f in solo] == [prod.id]
    assert solo[0].details["motivos"] == {"canonical_a_la_primera": 1}
    s.expire_all()
    assert s.get(Url, prod.id).inlinks_count == 1


def test_autocanonicas_e_indexables_no_avisan():
    s, j = _montar()
    home = _pag(s, j, "/", depth=0)
    blog = _pag(s, j, "/blog/", nxt="/blog/page/2/")
    _a(s, j, home, "/blog/")
    p2 = _pag(s, j, "/blog/page/2/", canonical="/blog/page/2/", prv="/blog/")
    _a(s, j, blog, "/blog/page/2/")
    _a(s, j, p2, "/blog/")
    entrada = _pag(s, j, "/blog/una-entrada/")
    _a(s, j, p2, "/blog/una-entrada/")
    s.commit()
    _analizar(s, j)
    tipos = {t for (t,) in s.execute(select(Issue.issue_type).where(Issue.job_id == j.id))}
    assert not tipos & set(avisos_de_paginacion.TIPOS), tipos
    assert "orphan_page" not in _tipos_en(s, j, entrada.id)


def test_marcador_debil_sin_evidencia_no_es_paginacion():
    s, j = _montar()
    # `?p=123` de WordPress y fichas `/producto/N`: no son paginas 2+.
    _pag(s, j, "/?p=123", robots="noindex")
    _pag(s, j, "/?p=456", robots="noindex")
    _pag(s, j, "/producto/123", robots="noindex")
    _pag(s, j, "/producto/456", robots="noindex")
    # `/archivo/2` y `/archivo/3` en el censo: la serie se ve.
    _pag(s, j, "/archivo/2", robots="noindex")
    _pag(s, j, "/archivo/3", robots="noindex")
    s.commit()
    _analizar(s, j)
    filas = _issues(s, j, "paginacion_noindex")
    assert len(filas) == 1
    assert [e["url"] for e in filas[0].details["ejemplos"]] == [
        f"{H}/archivo/2", f"{H}/archivo/3"]


def test_rel_next_sin_a_rastreada_o_no():
    """Paginacion solo por JavaScript: el `rel=next` esta en el head y el
    listado no tiene ningun `<a>` a la siguiente."""
    s, j = _montar()
    home = _pag(s, j, "/", depth=0)
    # Con <a>: bien (aunque el <a> lleve la barra final y el rel no).
    blog = _pag(s, j, "/blog/", nxt="/blog/page/2")
    _pag(s, j, "/blog/page/2/", prv="/blog/")
    _a(s, j, blog, "/blog/page/2/")
    # Sin <a> y la siguiente sin rastrear (el spider no sigue rel=next).
    noticias = _pag(s, j, "/noticias/", nxt="/noticias/page/2/")
    _a(s, j, noticias, "/noticias/una/")
    eventos = _pag(s, j, "/eventos/", nxt="/eventos/page/2/")
    # Sin <a> a la siguiente, que SI se rastreo por otro camino.
    agenda = _pag(s, j, "/agenda/", nxt="/agenda/?page=2")
    _pag(s, j, "/agenda/?page=2", prv="/agenda/")
    _a(s, j, home, "/agenda/?page=2")
    for p in (noticias, eventos, agenda):
        _a(s, j, home, p.url[len(H):])
    s.commit()
    _analizar(s, j)

    filas = {f.details["plantilla"]: f for f in _issues(s, j, "paginacion_sin_enlace")}
    assert len(filas) == 2, filas
    por_page = next(f for k, f in filas.items() if k.endswith("/page/N"))
    assert por_page.severity == "warning"
    assert por_page.details["paginas_afectadas"] == 2
    assert por_page.url_id == eventos.id
    assert por_page.details["ejemplos"][0] == {"url": eventos.url,
                                               "siguiente": f"{H}/eventos/page/2/"}
    por_query = next(f for k, f in filas.items() if k.endswith("?page"))
    assert por_query.url_id == agenda.id
    assert "paginacion_sin_enlace" not in _tipos_en(s, j, blog.id)


def test_rel_next_a_un_404_avisa_y_a_un_429_no():
    s, j = _montar()
    _pag(s, j, "/blog/", nxt="/blog/page/2/")
    p2 = _pag(s, j, "/blog/page/2/", prv="/blog/", nxt="/blog/page/3/")
    _pag(s, j, "/blog/page/3/", status=404)
    _pag(s, j, "/tienda/", nxt="/tienda/?page=2")
    _pag(s, j, "/tienda/?page=2", status=429)
    s.commit()
    _analizar(s, j)
    filas = _issues(s, j, "paginacion_siguiente_rota")
    assert len(filas) == 1
    f = filas[0]
    assert f.url_id == p2.id and f.severity == "warning"
    assert f.details["ejemplos"] == [{"url": p2.url, "rel": "next",
                                      "destino": f"{H}/blog/page/3/", "status_code": 404}]


# ---------------------------------------------------------------------------
# Ninguna lectura nueva de `links` (#52)
# ---------------------------------------------------------------------------
URL_PG = os.environ.get("PAGERANK_TEST_DATABASE_URL")
_RE_LINKS = re.compile(r"\blinks\b", re.IGNORECASE)


def _censo_paginado(s, j):
    home = _pag(s, j, "/", depth=0)
    blog = _pag(s, j, "/blog/", nxt="/blog/page/2/")
    _a(s, j, home, "/blog/")
    p2 = _pag(s, j, "/blog/page/2/", robots="noindex", prv="/blog/", nxt="/blog/page/3/")
    _a(s, j, blog, "/blog/page/2/")
    _pag(s, j, "/tienda/zapatos", nxt="/tienda/zapatos?page=2")
    _a(s, j, home, "/tienda/zapatos")
    z2 = _pag(s, j, "/tienda/zapatos?page=2", canonical="/tienda/zapatos")
    _a(s, j, home, "/tienda/zapatos?page=2")
    _pag(s, j, "/category/cat1/")
    _a(s, j, p2, "/category/cat1/")
    _pag(s, j, "/producto/bota")
    _a(s, j, z2, "/producto/bota")
    s.commit()


def _lecturas_de_links(engine, correr) -> list[str]:
    vistas: list[str] = []

    def anotar(conn, cursor, statement, params, context, executemany):
        if _RE_LINKS.search(statement):
            vistas.append(statement)

    event.listen(engine, "before_cursor_execute", anotar)
    try:
        correr()
    finally:
        event.remove(engine, "before_cursor_execute", anotar)
    return vistas


@pytest.mark.parametrize("motor", [
    "sqlite",
    pytest.param("postgres", marks=pytest.mark.skipif(
        not URL_PG, reason="sin PAGERANK_TEST_DATABASE_URL")),
])
def test_la_paginacion_no_anade_lecturas_de_links(tmp_path, monkeypatch, motor):
    """Mismo numero de sentencias que nombran `links` con el paso de
    paginacion que sin el, en un censo con paginacion que emite los avisos.
    En Postgres corre `run_all` entero; en SQLite, los pasos que leen
    enlaces (el PageRank es SQL de Postgres)."""
    if motor == "postgres":
        engine = create_engine(URL_PG)
        Base.metadata.create_all(engine)
    else:
        engine = create_engine(f"sqlite:///{tmp_path}/a.db")
        Base.metadata.create_all(engine, tables=[
            Job.__table__, Url.__table__, Issue.__table__, Link.__table__,
            HtmlMeta.__table__, Hreflang.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=[f"{H}/"], config={})
    s.add(j)
    s.flush()
    try:
        _censo_paginado(s, j)

        def correr():
            az = SEOAnalyzer(s, j.id)
            if motor == "postgres":
                az.run_all()
            else:
                az.clear_existing_issues()
                az.analyze_indexability()
                avisos_de_paginacion.analizar_paginacion(az)
                az.compute_link_counts()
                az.analyze_links()
                az.analyze_sitemap()
                az._flush_issues()
                s.commit()

        con = _lecturas_de_links(engine, correr)
        tipos = {t for (t,) in s.execute(select(Issue.issue_type).where(Issue.job_id == j.id))}
        assert {"paginacion_noindex", "paginacion_canonical_a_la_primera",
                "solo_enlazada_desde_paginacion_excluida"} <= tipos, tipos

        with monkeypatch.context() as m:
            m.setattr(avisos_de_paginacion, "analizar_paginacion", lambda az: None)
            sin = _lecturas_de_links(engine, correr)
        tipos = {t for (t,) in s.execute(select(Issue.issue_type).where(Issue.job_id == j.id))}
        assert not tipos & set(avisos_de_paginacion.TIPOS)

        assert len(con) == len(sin), (con, sin)
        # La unica que cambia es la que materializa las aristas, y solo para
        # unir la temporal de paginacion (por clave primaria).
        distintas = [(a, b) for a, b in zip(con, sin) if a != b]
        assert len(distintas) == 1
        assert "paginacion_tmp" in distintas[0][0] and "aristas_tmp" in distintas[0][0]
    finally:
        s.rollback()
        if motor == "postgres":
            from sqlalchemy import delete
            ids = [u for (u,) in s.execute(select(Url.id).where(Url.job_id == j.id))]
            s.execute(delete(Issue).where(Issue.job_id == j.id))
            s.execute(delete(Link).where(Link.job_id == j.id))
            s.execute(delete(HtmlMeta).where(HtmlMeta.url_id.in_(ids or [0])))
            s.execute(delete(Url).where(Url.job_id == j.id))
            s.execute(delete(Job).where(Job.id == j.id))
            s.commit()
        s.close()
        engine.dispose()
