"""Canonicals y cadenas: lo que encuentra Google al llegar al destino
(decision 101).

`canonical_broken` metia en un saco un 301 y un 404, y un destino 200 en
`noindex` o canonicalizado a su vez a otro no avisaba de nada. Una redireccion
que acaba en 404 solo avisaba en el 404, nunca en el salto que lleva a el. Y una
canonica que el sitio no enlaza salia como `orphan_page`, que manda a buscar
enlaces que no faltan: los hay, pero apuntan a la variante.

Todos los tests EJECUTAN el check contra SQLite (decision 74).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Hreflang, HtmlMeta, Issue, Job, Link, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar(**job):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Issue.__table__,
        Link.__table__, Hreflang.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config={}, **job)
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, *, status=200, canonical=None, canonical_header=None,
         noindex=False, sin_meta=False, interna=True, **kw):
    url = ruta if ruta.startswith("http") else f"https://x.com{ruta}"
    datos = dict(
        job_id=j.id, url=url, url_hash=url, is_internal=interna,
        is_html=not sin_meta, status_code=status, host=url.split("/")[2],
        resource_type="html", noindex=noindex, crawl_depth=1,
    )
    datos.update(kw)
    u = Url(**datos)
    s.add(u)
    s.flush()
    if not sin_meta:
        s.add(HtmlMeta(url_id=u.id, canonical_href=canonical,
                       canonical_header=canonical_header, canonical_count=1 if canonical else 0,
                       meta_robots="noindex" if noindex else None))
        s.flush()
    return u


def _salto(s, j, ruta, destino, status=301, **kw):
    return _url(s, j, ruta, status=status, sin_meta=True,
                redirect_url=destino if destino.startswith("http") else f"https://x.com{destino}",
                **kw)


def _issues(s, j, url_id=None):
    q = select(Issue.url_id, Issue.issue_type, Issue.severity, Issue.details).where(
        Issue.job_id == j.id)
    if url_id is not None:
        q = q.where(Issue.url_id == url_id)
    return s.execute(q).all()


def _tipos(s, j, url_id):
    return sorted(i.issue_type for i in _issues(s, j, url_id))


def _canonicals(s, j):
    SEOAnalyzer(s, j.id).analyze_canonicals()
    s.flush()


# -- El destino del canonical -------------------------------------------------

def test_canonical_a_un_404_es_canonical_a_error():
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/b")
    _url(s, j, "/b", status=404, sin_meta=True)
    _canonicals(s, j)
    [fila] = [i for i in _issues(s, j, a.id) if i.issue_type.startswith("canonical_a")]
    assert (fila.issue_type, fila.severity) == ("canonical_a_error", "error")
    assert fila.details["target_status"] == 404
    assert "canonical_broken" not in _tipos(s, j, a.id)


def test_canonical_a_un_301_que_acaba_bien_es_canonical_a_redireccion():
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/b")
    _salto(s, j, "/b", "/c")
    _url(s, j, "/c", canonical="https://x.com/c")
    _canonicals(s, j)
    [fila] = _issues(s, j, a.id)
    assert (fila.issue_type, fila.severity) == ("canonical_a_redireccion", "warning")
    assert fila.details["destino_final"] == "https://x.com/c"
    assert fila.details["vuelve_a_la_pagina"] is False


def test_canonical_a_un_301_que_acaba_en_404_es_canonical_a_error():
    """Por la consecuencia: el contenido se queda sin URL buena, no «hay un
    salto de mas»."""
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/b")
    _salto(s, j, "/b", "/c")
    _url(s, j, "/c", status=410, sin_meta=True)
    _canonicals(s, j)
    [fila] = _issues(s, j, a.id)
    assert fila.issue_type == "canonical_a_error"
    assert fila.details["via_redireccion"] is True
    assert fila.details["final_status"] == 410


def test_canonical_a_http_que_redirige_a_si_misma_es_un_solo_aviso():
    s, j = _montar()
    a = _url(s, j, "/a", canonical="http://x.com/a")
    _salto(s, j, "http://x.com/a", "https://x.com/a")
    _canonicals(s, j)
    assert _tipos(s, j, a.id) == ["canonical_a_http"]


def test_canonical_a_una_noindex():
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/b")
    _url(s, j, "/b", canonical="https://x.com/b", noindex=True)
    _canonicals(s, j)
    [fila] = _issues(s, j, a.id)
    assert (fila.issue_type, fila.severity) == ("canonical_a_no_indexable", "warning")


def test_canonical_a_noindex_desde_una_noindex_no_avisa():
    """Si la propia pagina tambien es noindex no hay senales que se
    contradigan: lo que haya que revisar es el noindex del destino, que avisa
    en su URL. Medido: era el 100% de los casos en Druni, UOC y penguin."""
    s, j = _montar()
    a = _url(s, j, "/b?p=2&gclid=1", canonical="https://x.com/b?p=2", noindex=True)
    _url(s, j, "/b?p=2", canonical="https://x.com/b?p=2", noindex=True)
    _canonicals(s, j)
    assert _tipos(s, j, a.id) == []


def test_canonical_a_http_que_redirige_a_su_version_https_es_un_solo_aviso():
    """El canonical escrito en http tras migrar a https: el arreglo es el
    mismo (escribir la URL https), asi que es un aviso, no dos."""
    s, j = _montar()
    a = _url(s, j, "/a", canonical="http://x.com/b")
    _salto(s, j, "http://x.com/b", "https://x.com/b")
    _url(s, j, "/b", canonical="https://x.com/b")
    _canonicals(s, j)
    assert _tipos(s, j, a.id) == ["canonical_a_http"]


def test_canonical_del_destino_que_redirige_a_el_no_es_un_bucle():
    """B declara canonical `/b/`, que da 301 a B: B es autocanonica de hecho y
    el canonical de A es correcto, de un salto. Lo de B avisa en B."""
    s, j = _montar()
    a = _url(s, j, "/b?gclid=1", canonical="https://x.com/b")
    b = _url(s, j, "/b", canonical="https://x.com/b/")
    _salto(s, j, "/b/", "/b")
    _canonicals(s, j)
    assert _tipos(s, j, a.id) == []
    assert _tipos(s, j, b.id) == ["canonical_a_redireccion"]


def test_canonical_encadenado_a_b_c():
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/b")
    _url(s, j, "/b", canonical="https://x.com/c")
    _url(s, j, "/c", canonical="https://x.com/c")
    _canonicals(s, j)
    [fila] = _issues(s, j, a.id)
    assert (fila.issue_type, fila.severity) == ("canonical_encadenado", "warning")
    assert fila.details["cadena"] == [
        "https://x.com/a", "https://x.com/b", "https://x.com/c"]
    assert fila.details["bucle"] is False


def test_canonical_encadenado_que_acaba_en_error_es_error():
    """A -> B (200, canonical a C) -> C 404: el contenido no tiene URL buena."""
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/b")
    _url(s, j, "/b", canonical="https://x.com/c")
    _url(s, j, "/c", status=404, sin_meta=True)
    _canonicals(s, j)
    [fila] = [i for i in _issues(s, j, a.id) if i.issue_type == "canonical_encadenado"]
    assert fila.severity == "error"
    assert fila.details["final_status"] == 404


def test_canonical_encadenado_que_acaba_en_noindex_es_warning():
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/b")
    _url(s, j, "/b", canonical="https://x.com/c")
    _url(s, j, "/c", canonical="https://x.com/c", noindex=True)
    _canonicals(s, j)
    [fila] = [i for i in _issues(s, j, a.id) if i.issue_type == "canonical_encadenado"]
    assert fila.severity == "warning"
    assert fila.details["final_noindex"] is True


def test_el_encadenado_usa_el_canonical_de_cabecera_del_destino():
    """Como la indexabilidad: el del HTML y, si no hay, el de `Link`."""
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/b")
    _url(s, j, "/b", canonical_header="https://x.com/c")
    _url(s, j, "/c", canonical="https://x.com/c")
    _canonicals(s, j)
    assert "canonical_encadenado" in _tipos(s, j, a.id)


def test_bucle_de_canonicals():
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/b")
    _url(s, j, "/b", canonical="https://x.com/a")
    _canonicals(s, j)
    [fila] = _issues(s, j, a.id)
    assert fila.issue_type == "canonical_encadenado"
    assert fila.details["bucle"] is True


def test_lo_que_no_se_sabe_no_se_afirma():
    """Destino sin rastrear, bloqueado por robots (sin codigo) o con 429."""
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/no-rastreada")
    b = _url(s, j, "/b", canonical="https://x.com/bloqueada")
    _url(s, j, "/bloqueada", status=None, sin_meta=True, blocked_by_robots=True)
    c = _url(s, j, "/c", canonical="https://x.com/frenada")
    _url(s, j, "/frenada", status=429, sin_meta=True)
    _canonicals(s, j)
    for u in (a, b, c):
        assert _tipos(s, j, u.id) == []


def test_el_canonical_de_una_pagina_que_no_da_200_no_se_sigue():
    s, j = _montar()
    a = _url(s, j, "/a", status=500, canonical="https://x.com/b")
    _url(s, j, "/b", status=404, sin_meta=True)
    _canonicals(s, j)
    assert _tipos(s, j, a.id) == []


def test_canonical_bueno_no_avisa():
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/b")
    _url(s, j, "/b", canonical="https://x.com/b")
    _canonicals(s, j)
    assert _tipos(s, j, a.id) == []


# -- Redirecciones que acaban en error ----------------------------------------

def _cadenas(s, j):
    SEOAnalyzer(s, j.id).analyze_redirect_chains()
    s.flush()


def test_una_redireccion_a_un_404_avisa_en_el_salto():
    s, j = _montar()
    salto = _salto(s, j, "/vieja", "/nueva")
    destino = _url(s, j, "/nueva", status=404, sin_meta=True)
    _cadenas(s, j)
    [fila] = _issues(s, j, salto.id)
    assert (fila.issue_type, fila.severity) == ("redireccion_a_error", "error")
    assert fila.details["final_status"] == 404
    assert _issues(s, j, destino.id) == []


def test_una_cadena_que_acaba_en_error_es_un_aviso_no_dos():
    s, j = _montar()
    a = _salto(s, j, "/a", "/b")
    _salto(s, j, "/b", "/c")
    _url(s, j, "/c", status=500, sin_meta=True)
    _cadenas(s, j)
    assert _tipos(s, j, a.id) == ["redireccion_a_error"]
    [fila] = _issues(s, j, a.id)
    assert fila.details["hops"] == 2


def test_una_cadena_que_acaba_bien_sigue_siendo_redirect_chain():
    s, j = _montar()
    a = _salto(s, j, "/a", "/b")
    _salto(s, j, "/b", "/c")
    _url(s, j, "/c")
    _cadenas(s, j)
    assert _tipos(s, j, a.id) == ["redirect_chain"]


def test_una_redireccion_de_normalizacion_a_un_404_no_avisa():
    """http → https, www o barra final: la redireccion esta bien hecha y el
    error es el destino, que ya tiene su 4xx. En www.uoc.edu eran 383 de las
    473 de un salto, casi todas el 307 de Chromium hacia https."""
    s, j = _montar()
    https = _salto(s, j, "http://x.com/a", "https://x.com/a", status=307)
    _url(s, j, "/a", status=404, sin_meta=True)
    www = _salto(s, j, "https://www.x.com/c", "https://x.com/c")
    barra = _salto(s, j, "/c/", "/c")
    _url(s, j, "/c", status=404, sin_meta=True)
    otra = _salto(s, j, "/vieja", "/c")
    _cadenas(s, j)
    assert _tipos(s, j, https.id) == []
    assert _tipos(s, j, www.id) == []
    assert _tipos(s, j, barra.id) == []
    assert _tipos(s, j, otra.id) == ["redireccion_a_error"]


def test_redireccion_a_429_o_sin_rastrear_no_avisa():
    s, j = _montar()
    a = _salto(s, j, "/a", "/frenada")
    _url(s, j, "/frenada", status=429, sin_meta=True)
    b = _salto(s, j, "/b", "https://otro.com/x")
    _cadenas(s, j)
    assert _tipos(s, j, a.id) == []
    assert _tipos(s, j, b.id) == []


def test_un_salto_externo_que_acaba_en_error_no_es_del_sitio():
    s, j = _montar()
    ext = _salto(s, j, "https://otro.com/x", "https://otro.com/404", interna=False)
    _url(s, j, "https://otro.com/404", status=404, sin_meta=True, interna=False)
    _cadenas(s, j)
    assert _tipos(s, j, ext.id) == []


# -- La canonica que nadie enlaza ---------------------------------------------

def _enlace(s, j, origen, destino):
    s.add(Link(job_id=j.id, from_url_id=origen.id, to_url=destino.url,
               to_url_hash=destino.url_hash, is_internal=True,
               link_position="content", follow=True))
    s.flush()


def _enlazado(s, j):
    """Lo que hace `run_all` alrededor del check, en el mismo orden:
    indexabilidad, conteos de enlaces (con los criterios 38 y 57) y despues
    `analyze_links` y `analyze_sitemap`."""
    a = SEOAnalyzer(s, j.id)
    a.analyze_indexability()
    a.compute_link_counts()
    a.analyze_links()
    a.analyze_sitemap()
    a._flush_issues()
    s.flush()


def test_canonica_sin_enlaces_con_la_variante_enlazada():
    s, j = _montar()
    home = _url(s, j, "/", canonical="https://x.com/", crawl_depth=0)
    variante = _url(s, j, "/p?color=rojo", canonical="https://x.com/p")
    canonica = _url(s, j, "/p", canonical="https://x.com/p", in_sitemap=True)
    _enlace(s, j, home, variante)
    _enlazado(s, j)
    tipos = _tipos(s, j, canonica.id)
    assert tipos == ["canonical_sin_enlazar"]
    [fila] = _issues(s, j, canonica.id)
    assert fila.severity == "warning"
    assert fila.details["variantes"] == 1
    assert fila.details["variantes_enlazadas"] == 1
    assert fila.details["ejemplos_variante"] == ["https://x.com/p?color=rojo"]


def test_si_las_variantes_tampoco_estan_enlazadas_es_huerfana():
    s, j = _montar()
    _url(s, j, "/", canonical="https://x.com/", crawl_depth=0)
    _url(s, j, "/p?color=rojo", canonical="https://x.com/p")
    canonica = _url(s, j, "/p", canonical="https://x.com/p")
    _enlazado(s, j)
    assert _tipos(s, j, canonica.id) == ["orphan_page"]


def test_la_variante_enlazada_solo_desde_su_canonica_no_cuenta():
    """El selector de color de la ficha enlaza la variante desde la propia
    canonica: nadie de fuera enlaza ese contenido, asi que es huerfana y lo
    sigue diciendo `sitemap_orphan`. Medido en www.uoc.edu: una noticia cuyo
    unico enlace a su variante `?idTag=` era ella misma."""
    s, j = _montar()
    _url(s, j, "/", canonical="https://x.com/", crawl_depth=0)
    canonica = _url(s, j, "/p", canonical="https://x.com/p", in_sitemap=True)
    rojo = _url(s, j, "/p?color=rojo", canonical="https://x.com/p")
    azul = _url(s, j, "/p?color=azul", canonical="https://x.com/p")
    _enlace(s, j, canonica, rojo)
    _enlace(s, j, rojo, azul)
    _enlazado(s, j)
    assert _tipos(s, j, canonica.id) == ["sitemap_orphan"]


def test_un_enlace_desde_una_noindex_no_enlaza_la_canonica():
    """Decision 38: el enlace desde una noindex no cuenta, asi que la canonica
    sigue sin enlazar aunque la noindex la enlace."""
    s, j = _montar()
    home = _url(s, j, "/", canonical="https://x.com/", crawl_depth=0)
    privada = _url(s, j, "/privada", noindex=True)
    variante = _url(s, j, "/p?color=rojo", canonical="https://x.com/p")
    canonica = _url(s, j, "/p", canonical="https://x.com/p")
    _enlace(s, j, home, variante)
    _enlace(s, j, privada, canonica)
    _enlazado(s, j)
    assert _tipos(s, j, canonica.id) == ["canonical_sin_enlazar"]


def test_canonica_enlazada_no_avisa():
    s, j = _montar()
    home = _url(s, j, "/", canonical="https://x.com/", crawl_depth=0)
    variante = _url(s, j, "/p?color=rojo", canonical="https://x.com/p")
    canonica = _url(s, j, "/p", canonical="https://x.com/p")
    _enlace(s, j, home, variante)
    _enlace(s, j, home, canonica)
    _enlazado(s, j)
    assert _tipos(s, j, canonica.id) == []


def test_con_enlaces_archivados_no_se_afirma(caplog):
    """Decision 95: sin filas en `links` los conteos no se pueden rehacer, y un
    aviso sobre ellos seria inventado. Se salta con un aviso en el log."""
    s, j = _montar(enlaces_archivados={"ruta": "x.parquet"})
    _url(s, j, "/", canonical="https://x.com/", crawl_depth=0)
    _url(s, j, "/p?color=rojo", canonical="https://x.com/p", inlinks_count=3,
         indexability_status="Canonicalised")
    canonica = _url(s, j, "/p", canonical="https://x.com/p", inlinks_count=0,
                    indexable=True)
    a = SEOAnalyzer(s, j.id)
    with caplog.at_level(logging.WARNING, logger="analysis.analyzer"):
        a.analyze_links()
        a._flush_issues()
    assert "canonical_sin_enlazar" not in _tipos(s, j, canonica.id)
    assert any("archivados" in r.getMessage() for r in caplog.records)
