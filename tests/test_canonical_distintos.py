"""Varios canonicals: lo que cuenta son los DESTINOS distintos (decision 115).

El mismo canonical escrito dos veces (relativo y absoluto, dos plugins) salia
`canonical_multiple`, y uno en el head mas el mismo en el body daba dos avisos
para un arreglo. Y con dos destinos distintos, que Google ignora, la pagina
salia «Canonicalised» al primero. Los tests del analisis EJECUTAN el check
contra SQLite (decision 74).
"""

from __future__ import annotations

from parsel import Selector

from analysis.analyzer import SEOAnalyzer
from seo_crawler import extractors as ex
from shared.indexabilidad import estado_indexabilidad
from shared.models import HtmlMeta, Url
from test_canonicals_y_cadenas_db import _canonicals, _issues, _montar, _tipos, _url

URL = "https://x.com/c"


def _meta(head: str, body: str = "") -> dict:
    html = f"<html><head>{head}</head><body>{body}</body></html>"
    return ex.extract_meta(Selector(text=html), base_url=URL)


# -- Extractor ----------------------------------------------------------------

def test_relativo_y_absoluto_al_mismo_sitio_son_un_destino():
    m = _meta('<link rel="canonical" href="/a"><link rel="canonical" href="https://x.com/a">')
    assert (m["canonical_count"], m["canonical_distintos"]) == (2, 1)


def test_dos_destinos_son_dos():
    m = _meta('<link rel="canonical" href="/a"><link rel="canonical" href="/b">')
    assert m["canonical_distintos"] == 2
    assert m["canonical_href"] == "https://x.com/a"  # el valor guardado no cambia


def test_el_del_body_no_cuenta_como_destino():
    m = _meta('<link rel="canonical" href="/a">', body='<link rel="canonical" href="/a">')
    assert (m["canonical_distintos"], m["canonical_in_body"]) == (1, True)
    m = _meta('<link rel="canonical" href="/a">', body='<link rel="canonical" href="/b">')
    assert m["canonical_distintos"] == 1


def test_un_href_vacio_no_es_un_destino():
    m = _meta('<link rel="canonical" href=""><link rel="canonical" href="/a">')
    assert m["canonical_distintos"] == 1


def test_puerto_caja_y_fragmento_no_hacen_otro_destino():
    m = _meta('<link rel="canonical" href="https://X.com:443/a#x">'
              '<link rel="canonical" href="/a">')
    assert m["canonical_distintos"] == 1


def test_sin_canonical():
    assert _meta("<title>t</title>")["canonical_distintos"] == 0


# -- Indexabilidad ------------------------------------------------------------

def test_con_dos_distintos_la_pagina_es_indexable():
    assert estado_indexabilidad(200, canonical_href="https://x.com/otra",
                                canonicals_distintos=2, page_url=URL) == (True, "Indexable")
    # Tambien se ignora el de la cabecera: es otra senal del mismo conflicto.
    assert estado_indexabilidad(200, canonical_href="https://x.com/otra",
                                canonical_header="https://x.com/tercera",
                                canonicals_distintos=2, page_url=URL) == (True, "Indexable")


def test_con_uno_o_sin_saberlo_manda_el_canonical():
    for distintos in (1, None):
        assert estado_indexabilidad(200, canonical_href="https://x.com/otra",
                                    canonicals_distintos=distintos,
                                    page_url=URL) == (False, "Canonicalised")


def test_el_noindex_sigue_mandando():
    assert estado_indexabilidad(200, meta_robots="noindex", canonical_href="https://x.com/o",
                                canonicals_distintos=2, page_url=URL) == (False, "Noindex")


# -- Analisis -----------------------------------------------------------------

def _con_canonicals(s, j, ruta, canonical, *, count, distintos, **kw):
    u = _url(s, j, ruta, canonical=canonical, **kw)
    meta = s.get(HtmlMeta, u.id)
    meta.canonical_count = count
    meta.canonical_distintos = distintos
    s.flush()
    return u


def test_el_mismo_destino_dos_veces_no_es_multiple():
    s, j = _montar()
    _url(s, j, "/a", canonical="https://x.com/a")
    u = _con_canonicals(s, j, "/c", "https://x.com/a", count=2, distintos=1)
    _canonicals(s, j)
    assert "canonical_multiple" not in _tipos(s, j, u.id)


def test_dos_destinos_es_un_aviso_y_nada_sobre_el_destino():
    s, j = _montar()
    _url(s, j, "/a", status=404)
    u = _con_canonicals(s, j, "/c", "http://otro.com/a", count=2, distintos=2)
    _canonicals(s, j)
    issues = _issues(s, j, u.id)
    assert [i.issue_type for i in issues] == ["canonical_multiple"]
    assert issues[0].details == {"cuantos": 2, "distintos": 2}


def test_dos_destinos_a_un_404_no_es_canonical_a_error():
    s, j = _montar()
    _url(s, j, "/a", status=404)
    u = _con_canonicals(s, j, "/c", "https://x.com/a", count=2, distintos=2)
    _canonicals(s, j)
    assert _tipos(s, j, u.id) == ["canonical_multiple"]


def test_un_censo_viejo_sigue_contando_etiquetas():
    s, j = _montar()
    _url(s, j, "/a", canonical="https://x.com/a")
    u = _con_canonicals(s, j, "/c", "https://x.com/a", count=2, distintos=None)
    _canonicals(s, j)
    multiple = [i for i in _issues(s, j, u.id) if i.issue_type == "canonical_multiple"]
    assert len(multiple) == 1
    assert multiple[0].details == {"cuantos": 2, "distintos": None}


def test_la_indexabilidad_recalculada_deja_la_pagina_indexable():
    s, j = _montar()
    _url(s, j, "/a", canonical="https://x.com/a")
    u = _con_canonicals(s, j, "/c", "https://x.com/a", count=2, distintos=2,
                        indexability_status="Canonicalised")
    v = _con_canonicals(s, j, "/v", "https://x.com/a", count=1, distintos=1)
    SEOAnalyzer(s, j.id).analyze_indexability()
    s.flush()
    s.expire_all()
    assert (s.get(Url, u.id).indexable, s.get(Url, u.id).indexability_status) == (
        True, "Indexable")
    assert s.get(Url, v.id).indexability_status == "Canonicalised"


def test_un_destino_con_varios_canonicals_no_encadena():
    """A → B, y B declara dos canonicals distintos: Google no usa ninguno de
    los de B, asi que el de A es de un salto."""
    s, j = _montar()
    _url(s, j, "/c1", canonical="https://x.com/c1")
    _con_canonicals(s, j, "/b", "https://x.com/c1", count=2, distintos=2)
    a = _url(s, j, "/a", canonical="https://x.com/b")
    _canonicals(s, j)
    assert "canonical_encadenado" not in _tipos(s, j, a.id)
