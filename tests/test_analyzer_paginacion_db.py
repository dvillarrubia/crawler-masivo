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
from shared.models import (Base, EnlaceNoRastreable, Hreflang, HtmlMeta, Issue, Job, Link,
                           Url)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


H = "https://x.com"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Job.__table__, Url.__table__, Issue.__table__,
                                             Link.__table__, HtmlMeta.__table__, Hreflang.__table__,
                                             EnlaceNoRastreable.__table__])
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


def _a(s, j, origen, ruta, veces=1, follow=True):
    s.add(Link(job_id=j.id, from_url_id=origen.id, to_url=f"{H}{ruta}",
               to_url_hash=f"{j.id}{ruta}", is_internal=True, link_position="content",
               follow=follow, repeticiones=veces))
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
    # Deja indexables colgando: el `warning` va en la serie, que es donde se
    # arregla (64), con la cuenta; cada pagina lleva el rastro en `info`.
    assert f.severity == "warning"
    assert (f.details["series"], f.details["paginas_afectadas"]) == (1, 2)
    assert f.details["indexables_que_solo_cuelgan_de_ella"] == 2
    assert f.url_id == p2.id

    solo = {f.url_id: f for f in _issues(s, j, "solo_enlazada_desde_paginacion_excluida")}
    assert set(solo) == {cat1.id, cat2.id}
    assert solo[cat1.id].severity == "info"
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


def test_la_paginacion_va_antes_que_solo_enlazada_con_nofollow():
    """Decision 183: una categoria que cuelga de la paginacion noindex y que
    ademas recibe un nofollow de una pagina con camino. Sin camino desde la
    home, y su unico entrante que cuenta es nofollow; pero la causa que se
    arregla es la paginacion, y ese aviso (que ya cuenta el nofollow en
    `otros_entrantes`) es el unico (36)."""
    s, j = _montar()
    home = _pag(s, j, "/", depth=0)
    blog = _pag(s, j, "/blog/", nxt="/blog/page/2/")
    _a(s, j, home, "/blog/")
    p2 = _pag(s, j, "/blog/page/2/", robots="noindex, follow", prv="/blog/")
    _a(s, j, blog, "/blog/page/2/")
    cat = _pag(s, j, "/category/cat1/")
    _a(s, j, p2, "/category/cat1/")
    otra = _pag(s, j, "/otra/")
    _a(s, j, home, "/otra/")
    _a(s, j, otra, "/category/cat1/", follow=False)
    s.commit()
    _analizar(s, j)
    tipos = _tipos_en(s, j, cat.id)
    assert "solo_enlazada_desde_paginacion_excluida" in tipos
    assert "solo_enlazada_con_nofollow" not in tipos
    assert "not_reachable_from_home" not in tipos
    [f] = [f for f in _issues(s, j, "solo_enlazada_desde_paginacion_excluida")
           if f.url_id == cat.id]
    assert f.details.get("otros_entrantes") == 1


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
    # La entrada solo la enlaza la pagina 2: se alcanza, por la cola del
    # listado (`info`). Nada de exclusiones.
    assert tipos & set(avisos_de_paginacion.TIPOS) == {
        "solo_enlazada_desde_paginacion_profunda"}, tipos
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


# ---------------------------------------------------------------------------
# Revision: lo que la rama de la ola 1 (decision 107, sin mergear) habia
# medido en censos reales, reproducido contra este modulo. Cada test falla sin
# su arreglo.
# ---------------------------------------------------------------------------
def _analizar_todo(s, j):
    """Como `run_all` en lo que toca a la paginacion: tambien los botones de la
    106, que consumen lo que la paginacion ya dice."""
    from analysis import avisos_de_enlaces

    az = SEOAnalyzer(s, j.id)
    az.analyze_indexability()
    avisos_de_paginacion.analizar_paginacion(az)
    az.compute_link_counts()
    az.analyze_links()
    avisos_de_enlaces.avisar_enlaces_no_rastreables(az)
    az.analyze_sitemap()
    az._flush_issues()
    s.commit()
    return az


def _boton(s, j, origen, ruta):
    s.add(EnlaceNoRastreable(job_id=j.id, from_url_id=origen.id, crawlability="onclick_sin_href",
                             etiqueta="button", href=f"location='{ruta}'",
                             destino=f"{H}{ruta}", destino_interno=True,
                             destino_hash=f"{j.id}{ruta}", anchor_text="Cargar más",
                             link_position="content", repeticiones=1))
    s.flush()


def _todos(s, j):
    return {(t, u) for t, u in s.execute(select(Issue.issue_type, Issue.url_id).where(
        Issue.job_id == j.id))}


def _listado(s, j, *, p1_robots=None):
    """/cat/ -> /cat/page/2/ (noindex) -> /cat/page/3/ (noindex); la ficha
    /producto-viejo solo sale en la 3."""
    home = _pag(s, j, "/", depth=0)
    p1 = _pag(s, j, "/cat/", robots=p1_robots, nxt="/cat/page/2/")
    p2 = _pag(s, j, "/cat/page/2/", robots="noindex", prv="/cat/", nxt="/cat/page/3/")
    p3 = _pag(s, j, "/cat/page/3/", robots="noindex", prv="/cat/page/2/")
    ficha = _pag(s, j, "/producto-viejo")
    _a(s, j, home, "/cat/")
    _a(s, j, p1, "/cat/page/2/")
    _a(s, j, p2, "/cat/page/3/")
    _a(s, j, p3, "/producto-viejo")
    s.commit()
    return home, p1, p2, p3, ficha


def test_r1_un_rel_a_un_muro_no_es_un_rel_roto():
    """Decision 103: el muro no es la pagina; ni roto ni «no coincide»."""
    s, j = _montar()
    p1 = _pag(s, j, "/cat/", nxt="/cat/page/2/")
    muro = _pag(s, j, "/cat/page/2/", status=403)
    muro.response_class = "challenge_waf"
    _a(s, j, p1, "/cat/page/2/")
    s.commit()
    _analizar_todo(s, j)
    tipos = {t for t, _u in _todos(s, j)}
    assert not tipos & set(avisos_de_paginacion.TIPOS), tipos


def test_r2_una_cadena_de_rel_entre_entradas_no_es_un_listado():
    """`adjacent_posts_rel_link` de WordPress: rel=next/prev entre entradas
    que la portada enlaza todas. Ni son paginas 2+, ni su `noindex` es el de
    una paginacion, ni les falta un `<a>` a «la siguiente»."""
    s, j = _montar()
    home = _pag(s, j, "/", depth=0)
    _pag(s, j, "/hola-mundo/", nxt="/segundo-post/")
    b = _pag(s, j, "/segundo-post/", robots="noindex", prv="/hola-mundo/", nxt="/tercer-post/")
    c = _pag(s, j, "/tercer-post/", robots="noindex", prv="/segundo-post/")
    for ruta in ("/hola-mundo/", "/segundo-post/", "/tercer-post/"):
        _a(s, j, home, ruta)
    guia = _pag(s, j, "/guia/")
    _a(s, j, b, "/guia/")
    _a(s, j, c, "/guia/")
    s.commit()
    _analizar_todo(s, j)
    tipos = {t for t, _u in _todos(s, j)}
    assert not tipos & set(avisos_de_paginacion.TIPOS), tipos
    # La guia solo la enlazan dos noindex: es huerfana, sin mas.
    assert "orphan_page" in _tipos_en(s, j, guia.id)


def test_r2_un_listado_que_solo_dice_el_rel_sigue_siendolo():
    """La misma cadena sin enlaces desde fuera a las paginas 2+ es un listado
    (`/alter/news/` -> `/alter/news/2/` en blogs.uoc.edu)."""
    s, j = _montar()
    home = _pag(s, j, "/", depth=0)
    n1 = _pag(s, j, "/news/", nxt="/news/2/")
    n2 = _pag(s, j, "/news/2/", robots="noindex", prv="/news/", nxt="/news/3/")
    _pag(s, j, "/news/3/", robots="noindex", prv="/news/2/")
    _a(s, j, home, "/news/")
    _a(s, j, n1, "/news/2/")
    _a(s, j, n2, "/news/3/")
    s.commit()
    _analizar_todo(s, j)
    assert [f.url_id for f in _issues(s, j, "paginacion_noindex")] == [n2.id]


def test_r3_el_boton_de_cargar_mas_es_la_paginacion_sin_enlace():
    """El caso tipico: la pagina 2 solo la carga un boton, asi que ni se
    rastreo ni hay `rel=next`. Es `paginacion_sin_enlace` (con el boton en los
    detalles), y `enlace_no_rastreable` no lo repite (36)."""
    s, j = _montar()
    d1 = _pag(s, j, "/d/")
    _boton(s, j, d1, "/d/page/2/")
    otro = _pag(s, j, "/e")
    s.add(EnlaceNoRastreable(job_id=j.id, from_url_id=otro.id, crawlability="javascript_href",
                             etiqueta="a", href="javascript:abrir('/f')", destino=f"{H}/f",
                             destino_interno=True, destino_hash=f"{j.id}/f",
                             link_position="content", repeticiones=1))
    # Con rel=next y boton: un aviso, no dos.
    g1 = _pag(s, j, "/g/", nxt="/g/page/2/")
    _boton(s, j, g1, "/g/page/2/")
    s.commit()
    _analizar_todo(s, j)
    sin = _issues(s, j, "paginacion_sin_enlace")
    assert len(sin) == 1
    d = sin[0].details
    assert d["paginas_afectadas"] == 2 and d["con_boton_js"] == 2
    assert {e["url"] for e in d["ejemplos"]} == {d1.url, g1.url}
    assert [u for t, u in _todos(s, j) if t == "enlace_no_rastreable"] == [otro.id]


def test_r3_el_boton_con_un_a_a_la_misma_pagina_no_le_quita_nada_a_google():
    s, j = _montar()
    d1 = _pag(s, j, "/d/")
    _boton(s, j, d1, "/d/page/2/")
    _a(s, j, d1, "/d/page/2/")
    s.commit()
    _analizar_todo(s, j)
    tipos = {t for t, _u in _todos(s, j)}
    assert "paginacion_sin_enlace" not in tipos and "enlace_no_rastreable" not in tipos


def test_r4_un_enlace_desde_una_noindex_de_fuera_tampoco_es_un_camino():
    s, j = _montar()
    home, p1, p2, p3, ficha = _listado(s, j)
    legal = _pag(s, j, "/aviso-legal", robots="noindex")
    _a(s, j, home, "/aviso-legal")
    _a(s, j, legal, "/producto-viejo")
    s.commit()
    _analizar_todo(s, j)
    assert "solo_enlazada_desde_paginacion_excluida" in _tipos_en(s, j, ficha.id)
    assert "orphan_page" not in _tipos_en(s, j, ficha.id)


def test_r5_el_warning_va_en_la_serie_cuando_deja_indexables_colgando():
    """Decision 64: el arreglo es quitar un `noindex` de plantilla, no enlazar
    ficha a ficha. La serie lleva el `warning` y la cuenta; cada ficha, el
    rastro en `info`."""
    s, j = _montar()
    home, p1, p2, p3, ficha = _listado(s, j)
    s.commit()
    _analizar_todo(s, j)
    serie = _issues(s, j, "paginacion_noindex")
    assert len(serie) == 1 and serie[0].severity == "warning"
    d = serie[0].details
    assert d["indexables_que_solo_cuelgan_de_ella"] == 1
    assert d["ejemplos_indexables"] == [ficha.url]
    solo = _issues(s, j, "solo_enlazada_desde_paginacion_excluida")
    assert [(f.url_id, f.severity) for f in solo] == [(ficha.id, "info")]


def test_r6_la_pagina_3_que_solo_enlaza_la_2_noindex_no_es_huerfana():
    """Es de la propia serie: se cuenta en ella (`solo_enlazadas_por_la_serie`)
    y no como `orphan_page`, que diria que nadie la enlaza."""
    s, j = _montar()
    home, p1, p2, p3, ficha = _listado(s, j)
    _analizar_todo(s, j)
    assert "orphan_page" not in _tipos_en(s, j, p3.id)
    assert _issues(s, j, "paginacion_noindex")[0].details["solo_enlazadas_por_la_serie"] == 1


def test_r6_ninguna_pagina_se_queda_sin_aviso():
    """Lo que deja de ser `orphan_page`/`sitemap_orphan`/`not_reachable_from_home`
    sale en otro aviso: por pagina, o contada en una serie que lo lleva."""
    s, j = _montar()
    home, p1, p2, p3, ficha = _listado(s, j)
    # /tag/x/ solo la enlaza la 2 noindex de OTRA serie, y no es indexable.
    _pag(s, j, "/tag/x/", robots="noindex", nxt="/tag/x/page/2/")
    _a(s, j, p2, "/tag/x/")
    s.commit()
    _analizar_todo(s, j)
    tag = s.execute(select(Url).where(Url.url == f"{H}/tag/x/")).scalar_one()
    assert _tipos_en(s, j, tag.id) & {"orphan_page", "solo_enlazada_desde_paginacion_excluida"}


def test_r7_una_no_indexable_del_sitemap_no_lleva_el_aviso():
    s, j = _montar()
    home, p1, p2, p3, ficha = _listado(s, j)
    s.get(HtmlMeta, ficha.id).meta_robots = "noindex"
    ficha.in_sitemap = True
    s.commit()
    _analizar_todo(s, j)
    assert "solo_enlazada_desde_paginacion_excluida" not in _tipos_en(s, j, ficha.id)


def test_r8_un_nofollow_desde_fuera_no_es_un_camino_y_se_dice():
    """La home enlaza la ficha con `nofollow`: cuenta como entrante (38) pero no
    es un camino (90). La ficha solo cuelga de la paginacion, y se dice."""
    s, j = _montar()
    home, p1, p2, p3, ficha = _listado(s, j)
    _a(s, j, home, "/producto-viejo", follow=False)
    s.commit()
    _analizar_todo(s, j)
    tipos = _tipos_en(s, j, ficha.id)
    assert "solo_enlazada_desde_paginacion_excluida" in tipos, tipos
    assert "not_reachable_from_home" not in tipos
    fila = _issues(s, j, "solo_enlazada_desde_paginacion_excluida")[0]
    assert fila.details["otros_entrantes"] == 1


def test_r9_la_serie_casa_entre_censos_aunque_cambie_el_ancla():
    """Decision 88/89: el ancla es la pagina de URL menor del grupo, y se mueve
    si en el censo siguiente se rastrea otra. Es el mismo hallazgo."""
    from shared.comparacion import comparar_incidencias

    def censo(rutas):
        s, j = _montar()
        for r in rutas:
            _pag(s, j, r, robots="noindex")
        s.commit()
        _analizar_todo(s, j)
        return [{"issue_type": t, "severity": sev, "group_key": g, "url": u}
                for t, sev, g, u in s.execute(
                    select(Issue.issue_type, Issue.severity, Issue.group_key, Url.url)
                    .join(Url, Url.id == Issue.url_id)
                    .where(Issue.issue_type == "paginacion_noindex")).all()]

    a = censo(["/cat/page/3/", "/cat/page/4/"])
    b = censo(["/cat/page/2/", "/cat/page/3/", "/cat/page/4/"])
    assert a[0]["url"] != b[0]["url"] and a[0]["group_key"] == b[0]["group_key"]
    r = comparar_incidencias(a, b, analisis_a="v", analisis_b="v",
                             urls_del_nuevo={f"{H}/cat/page/{n}/" for n in (2, 3, 4)})
    assert r["siguen"]["total"] == 1
    assert r["nuevas"]["total"] == 0 and r["resueltas"]["total"] == 0


def test_e1_lo_que_solo_enlazan_las_paginas_profundas():
    s, j = _montar()
    home = _pag(s, j, "/", depth=0)
    p1 = _pag(s, j, "/blog/", nxt="/blog/page/2/")
    p2 = _pag(s, j, "/blog/page/2/", prv="/blog/")
    _a(s, j, home, "/blog/")
    _a(s, j, p1, "/blog/page/2/")
    nuevo = _pag(s, j, "/post-nuevo")
    viejo = _pag(s, j, "/post-viejo")
    _a(s, j, p1, "/post-nuevo")
    _a(s, j, p2, "/post-viejo")
    s.commit()
    _analizar_todo(s, j)
    filas = _issues(s, j, "solo_enlazada_desde_paginacion_profunda")
    assert len(filas) == 1 and filas[0].severity == "info"
    assert filas[0].details["paginas_afectadas"] == 1
    assert filas[0].details["ejemplos"] == [{"url": viejo.url, "ejemplo_origen": p2.url}]
    assert nuevo.id


def test_e2_rel_que_no_coincide_con_el_enlace_no_es_sin_enlace():
    """Lopesan: el `rel=next` dice `/page/2/` y la paginacion de Elementor
    enlaza `?e-page-1f=2`. HAY `<a href>` a la pagina siguiente: lo que falla
    es el `rel`. Y progym: el `rel` de la ficha dice `ficha.html?p=2` y su
    paginador enlaza `home.html?p=2`."""
    s, j = _montar()
    b1 = _pag(s, j, "/b/", nxt="/b/page/2/")
    _a(s, j, b1, "/b/?e-page-1f=2")
    f1 = _pag(s, j, "/ficha.html", nxt="/ficha.html?p=2")
    _a(s, j, f1, "/home.html?p=2")
    a1 = _pag(s, j, "/a/", nxt="/a/page/2/")
    s.commit()
    _analizar_todo(s, j)
    sin = _issues(s, j, "paginacion_sin_enlace")
    assert [e["url"] for f in sin for e in f.details["ejemplos"]] == [a1.url]
    rel = _issues(s, j, "paginacion_rel_incorrecto")
    motivos = {}
    for f in rel:
        assert f.severity == "info"
        for k, v in f.details["motivos"].items():
            motivos[k] = motivos.get(k, 0) + v
    assert motivos == {"no_coincide_con_el_enlace": 2}


def test_e2_rel_a_una_redireccion_y_fuera_de_la_serie():
    s, j = _montar()
    c1 = _pag(s, j, "/c/", nxt="/c/page/2/")
    _pag(s, j, "/c/page/2/", status=301)
    _a(s, j, c1, "/c/page/2/")
    d1 = _pag(s, j, "/d/page/2/", prv="/otra/")
    s.commit()
    _analizar_todo(s, j)
    motivos = {}
    for f in _issues(s, j, "paginacion_rel_incorrecto"):
        for k, v in f.details["motivos"].items():
            motivos[k] = motivos.get(k, 0) + v
    assert motivos == {"destino_redireccion": 1, "fuera_de_la_serie": 1}
    assert d1.id


def test_e3_elementor_e_page_es_paginacion():
    from shared.paginacion import pagina_de

    p = pagina_de("https://x.com/blog/?e-page-1f2a=3")
    assert p is not None and p.n == 3 and not p.debil
    assert pagina_de("https://x.com/blog/?e-page-1f2a=3").serie == \
        pagina_de("https://x.com/blog/?e-page-1f2a=2").serie


def test_e4_una_variante_canonicalizada_a_otra_serie_no_es_del_listado():
    """blogs.uoc.edu: paginaciones enteras con parametros basura (`?x=ooo`)
    canonicalizadas a la limpia. No son el listado: ni «sin enlace» por un
    `rel` a la version limpia, ni otra serie en noindex."""
    s, j = _montar()
    _pag(s, j, "/blog/page/2/?x=ooo", robots="noindex", canonical="/blog/page/2/",
         nxt="/blog/page/3/")
    _pag(s, j, "/blog/page/3/?x=ooo", robots="noindex", canonical="/blog/page/3/",
         prv="/blog/page/2/")
    s.commit()
    _analizar_todo(s, j)
    tipos = {t for t, _u in _todos(s, j)}
    assert not tipos & set(avisos_de_paginacion.TIPOS), tipos


def test_r5_una_serie_entera_en_noindex_deja_el_warning_en_la_pagina():
    """Si la pagina 1 tambien es noindex (un archivo por fecha), el noindex es
    una decision sobre el listado: lo que hay que hacer es enlazar la ficha
    desde otra parte, y el `warning` es de la ficha."""
    s, j = _montar()
    home, p1, p2, p3, ficha = _listado(s, j, p1_robots="noindex")
    _analizar_todo(s, j)
    serie = _issues(s, j, "paginacion_noindex")
    assert [f.severity for f in serie] == ["info"]
    assert "indexables_que_solo_cuelgan_de_ella" not in serie[0].details
    solo = _issues(s, j, "solo_enlazada_desde_paginacion_excluida")
    assert [(f.url_id, f.severity) for f in solo] == [(ficha.id, "warning")]


def test_r9_los_tipos_de_plantilla_casan_por_grupo_en_la_comparacion():
    """`shared.comparacion` no puede importar `analysis/` (la imagen de la API
    no lo lleva): repite la lista, y esto comprueba que coinciden."""
    from analysis.avisos_de_logs import TIPOS_ANCLADOS_POR_GRUPO as TIPOS_LOGS
    from analysis.avisos_de_trampas import TIPOS_ANCLADOS_POR_GRUPO as TIPOS_TRAMPAS
    from analysis.soft404 import TIPOS_ANCLADOS_POR_GRUPO as TIPOS_SOFT404
    from shared.accesibilidad import TIPOS as TIPOS_AXE
    from shared.comparacion import ANCLADOS_POR_GRUPO

    # Mas los de axe (decision 174), que viven en `shared/`, y el soft 404 de
    # plantilla (decision 190).
    assert ANCLADOS_POR_GRUPO == (set(avisos_de_paginacion.TIPOS_DE_PLANTILLA) | TIPOS_AXE
                                  | set(TIPOS_SOFT404) | set(TIPOS_LOGS) | set(TIPOS_TRAMPAS))


def test_r9_un_map_host_tambien_casa_la_serie():
    from shared.comparacion import comparar_incidencias

    a = [{"issue_type": "paginacion_noindex", "severity": "info", "group_key": "g",
          "url": "https://pre.x.com/cat/page/3/"}]
    b = [{"issue_type": "paginacion_noindex", "severity": "info", "group_key": "g",
          "url": "https://x.com/cat/page/2/"}]
    r = comparar_incidencias(a, b, analisis_a="v", analisis_b="v",
                             mapa_hosts={"pre.x.com": "x.com"})
    assert r["siguen"]["total"] == 1


def test_r11_cada_clave_de_los_detalles_tiene_nombre_en_la_interfaz():
    """Se EJECUTAN los escenarios y se miran las claves que salen (74)."""
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = open(os.path.join(raiz, "frontend", "incidencias.js"), encoding="utf-8").read()
    bloque = js[js.index("const DETALLE_LABEL = {"):]
    bloque = bloque[:bloque.index("\n};")]
    nombres = set(re.findall(r"^\s*([a-z0-9_]+)\s*:", bloque, re.M))
    nombres |= {"alcance"}  # lo pinta `fmt.detalles` aparte («de plantilla»)

    claves = set()
    for p1_robots in (None, "noindex"):
        s, j = _montar()
        home, p1, p2, p3, ficha = _listado(s, j, p1_robots=p1_robots)
        _a(s, j, home, "/producto-viejo", follow=False)
        _pag(s, j, "/x/page/3/", robots="noindex")       # cuelga solo de su serie
        x2 = _pag(s, j, "/x/page/2/", robots="noindex")
        _a(s, j, x2, "/x/page/3/")
        _a(s, j, home, "/x/page/2/")
        d1 = _pag(s, j, "/d/")
        _boton(s, j, d1, "/d/page/2/")
        b1 = _pag(s, j, "/b/", nxt="/b/page/2/")
        _a(s, j, b1, "/b/?e-page-1f=2")
        bl = _pag(s, j, "/blog/")
        b2 = _pag(s, j, "/blog/page/2/", canonical="/blog/", nxt="/blog/page/3/")
        _pag(s, j, "/blog/page/3/", status=404)
        _a(s, j, bl, "/blog/page/2/")
        _a(s, j, b2, "/solo-desde-b2")
        _pag(s, j, "/solo-desde-b2")
        t1 = _pag(s, j, "/t/")
        t2 = _pag(s, j, "/t/page/2/")
        _a(s, j, t1, "/t/page/2/")
        _a(s, j, t2, "/hondo")
        _pag(s, j, "/hondo")
        s.commit()
        _analizar_todo(s, j)
        for (d,) in s.execute(select(Issue.details).where(
                Issue.issue_type.in_(avisos_de_paginacion.TIPOS))).all():
            claves |= set(d or {})
    tipos = {t for (t,) in s.execute(select(Issue.issue_type).where(
        Issue.issue_type.in_(avisos_de_paginacion.TIPOS)))}
    assert tipos == set(avisos_de_paginacion.TIPOS), set(avisos_de_paginacion.TIPOS) - tipos
    assert claves and not claves - nombres, sorted(claves - nombres)


def test_e5_un_rel_de_la_pagina_1_es_evidencia_de_un_marcador_debil():
    """progym: la ficha `x.html` declara `rel=next` a `x.html?p=2`, y esa
    pagina 2 (sin `rel` propio) va canonicalizada a la ficha. `?p=N` solo es
    paginacion con evidencia, y el `rel` de la 1 lo es."""
    s, j = _montar()
    _pag(s, j, "/x.html", nxt="/x.html?p=2")
    x2 = _pag(s, j, "/x.html?p=2", canonical="/x.html")
    # Sin nadie que la anuncie, `?p=2` sigue sin ser paginacion.
    _pag(s, j, "/y.html")
    _pag(s, j, "/y.html?p=2", canonical="/y.html")
    s.commit()
    _analizar_todo(s, j)
    filas = _issues(s, j, "paginacion_canonical_a_la_primera")
    assert [(f.url_id, f.details["paginas_afectadas"]) for f in filas] == [(x2.id, 1)]


def test_m1_entradas_de_wordpress_con_p_no_son_la_serie_del_blog():
    """Medido en blogs.uoc.edu: `/unescochair/?p=38` es una ENTRADA (su
    canonical es la portada) y lleva `rel=next/prev` a otras entradas. Que las
    paginas declaren un `rel` cualquiera no es evidencia de que `?p=` pagine:
    hace falta que algo anuncie su pagina 2."""
    s, j = _montar()
    _pag(s, j, "/blog/", nxt="/blog/page/2/")
    _pag(s, j, "/blog/page/2/", prv="/blog/")
    _pag(s, j, "/blog/?p=38", canonical="/blog/", nxt="/blog/?p=39")
    _pag(s, j, "/blog/?p=39", canonical="/blog/", prv="/blog/?p=38")
    s.commit()
    _analizar_todo(s, j)
    assert _issues(s, j, "paginacion_canonical_a_la_primera") == []


def test_m2_lo_que_solo_cuelga_de_la_pagina_1_noindex_de_un_archivo():
    """Medido en blogs.uoc.edu (las «168» de la decision 38): un archivo por
    mes entero en noindex, y su pagina 2 indexable y en el sitemap, a la que
    solo enlaza la 1. Cuelga de la paginacion, no de nadie: ni `orphan_page`
    ni `sitemap_orphan`, y el `warning` es de la pagina (el noindex del
    archivo es deliberado)."""
    s, j = _montar()
    home = _pag(s, j, "/", depth=0)
    m1 = _pag(s, j, "/2025/10/", robots="noindex", nxt="/2025/10/page/2/")
    m2 = _pag(s, j, "/2025/10/page/2/", prv="/2025/10/", sitemap=True)
    _a(s, j, home, "/2025/10/")
    _a(s, j, m1, "/2025/10/page/2/")
    s.commit()
    _analizar_todo(s, j)
    assert _tipos_en(s, j, m2.id) & {"orphan_page", "sitemap_orphan",
                                     "solo_enlazada_desde_paginacion_excluida"} == {
        "solo_enlazada_desde_paginacion_excluida"}
    fila = _issues(s, j, "solo_enlazada_desde_paginacion_excluida")
    assert [(f.url_id, f.severity) for f in fila] == [(m2.id, "warning")]
    # La 1 no es una pagina 2+: no sale en `paginacion_noindex`.
    assert _issues(s, j, "paginacion_noindex") == []


def test_m3_un_enlace_a_la_siguiente_con_un_parametro_de_mas_es_un_enlace():
    """blogs.uoc.edu: el `rel` dice `/page/6/` y el paginador enlaza
    `/page/6/?page=0`. Hay `<a>`: es el `rel` el que no coincide."""
    s, j = _montar()
    p5 = _pag(s, j, "/cat/page/5/", nxt="/cat/page/6/", prv="/cat/page/4/")
    _a(s, j, p5, "/cat/page/6/?page=0")
    s.commit()
    _analizar_todo(s, j)
    assert _issues(s, j, "paginacion_sin_enlace") == []
    rel = _issues(s, j, "paginacion_rel_incorrecto")
    assert [f.details["motivos"] for f in rel] == [{"no_coincide_con_el_enlace": 1}]
