"""Hreflang repetido y hreflang en la cabecera HTTP `Link` (#149, secciones 11 y 12).

- La misma anotacion escrita dos veces (dos plugins, `/en/` y `/en/#top`, el
  head y la cabecera) es UNA: una fila y un aviso.
- Google lee el hreflang de la cabecera `Link` igual que el del `<head>`, y en
  un PDF es la unica forma de declararlo.
"""

from __future__ import annotations

import pytest
from parsel import Selector

from seo_crawler import extractors as ex
from shared.models import Hreflang
from test_hreflang_completo_db import _analizar, _anotar, _de, _montar, _url


def _hreflang(head: str, body: str = "") -> list[dict]:
    html = f"<html><head>{head}</head><body>{body}</body></html>"
    return ex.extract_hreflang(Selector(text=html), base_url="https://x.com/pagina")


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------
def test_la_misma_anotacion_cuatro_veces_es_una_fila():
    filas = _hreflang(
        '<link rel="alternate" hreflang="en-GB" href="/en/">'
        '<link rel="alternate" hreflang="en-GB" href="https://x.com/en/">'
        '<link rel="alternate" hreflang="EN-gb" href="/en/">'
        '<link rel="alternate" hreflang="en-GB" href="/en/#top">')
    assert [(f["lang"], f["href"], f["en_body"]) for f in filas] == [
        ("en-GB", "https://x.com/en/", False)]
    assert filas[0]["origen"] == "html"


def test_la_del_head_gana_a_la_del_body():
    filas = _hreflang('<link rel="alternate" hreflang="en-GB" href="/en/">',
                      body='<link rel="alternate" hreflang="en-GB" href="/en/">')
    assert len(filas) == 1 and filas[0]["en_body"] is False
    # Aunque la del body vaya delante (lxml mueve al body lo que va tras un
    # elemento que no cabe en el head; aqui se construye la lista a mano).
    dedup = ex.deduplicar_hreflang([
        {"lang": "en-GB", "href": "https://x.com/en/", "en_body": True},
        {"lang": "en-gb", "href": "https://x.com/en/", "en_body": False},
    ])
    assert dedup == [{"lang": "en-gb", "href": "https://x.com/en/", "en_body": False}]


def test_dos_idiomas_al_mismo_destino_son_dos_filas():
    filas = _hreflang('<link rel="alternate" hreflang="en-GB" href="/en/">'
                      '<link rel="alternate" hreflang="en-US" href="/en/">')
    assert [f["lang"] for f in filas] == ["en-GB", "en-US"]


def test_el_mismo_idioma_a_dos_destinos_son_dos_filas():
    """Eso es `hreflang_idioma_repetido`, no un duplicado."""
    filas = _hreflang('<link rel="alternate" hreflang="en" href="/en/">'
                      '<link rel="alternate" hreflang="en" href="/uk/">')
    assert len(filas) == 2


def test_hreflang_de_cabecera():
    valor = ('<https://x.com/en/>; rel="alternate"; hreflang="en", '
             '<https://x.com/de/>; rel=alternate; hreflang=de, '
             '</style.css>; rel=preload, '
             '</fr/>; rel="alternate nofollow"; hreflang="fr", '
             '</it/>; rel="alternate"; title="Italiano; Italia, IT"; hreflang="it", '
             '<https://x.com/es/>; rel="canonical"; hreflang="es", '
             '<https://x.com/en/#a>; REL="Alternate"; HREFLANG="EN"')
    filas = ex.hreflang_de_cabecera(valor, "https://x.com/doc.pdf")
    assert [(f["lang"], f["href"]) for f in filas] == [
        ("en", "https://x.com/en/"), ("de", "https://x.com/de/"),
        ("fr", "https://x.com/fr/"), ("it", "https://x.com/it/")]
    assert all(f["origen"] == "cabecera" and f["en_body"] is False for f in filas)


def test_hreflang_de_cabecera_sin_nada():
    assert ex.hreflang_de_cabecera(None) == []
    assert ex.hreflang_de_cabecera("</style.css>; rel=preload") == []
    assert ex.hreflang_de_cabecera('<https://x.com/en/>; hreflang="en"') == []


def test_canonical_de_cabecera_no_cambia():
    assert ex.canonical_de_cabecera(
        '</style.css>; rel=preload, <https://x.com/a>; rel="canonical"') == "https://x.com/a"
    assert ex.canonical_de_cabecera("</a>; rel=canonical", "https://x.com/b") == "https://x.com/a"
    assert ex.canonical_de_cabecera('<https://x.com/a,b>; rel="canonical"') == "https://x.com/a,b"
    assert ex.canonical_de_cabecera('<https://x.com/a>; title="x; y"; rel="canonical"') \
        == "https://x.com/a"
    assert ex.canonical_de_cabecera("</a>; rel=preload") is None
    assert ex.canonical_de_cabecera("") is None


def test_head_y_cabecera_al_mismo_destino_es_una_fila():
    html = _hreflang('<link rel="alternate" hreflang="en" href="/en/">')
    cab = ex.hreflang_de_cabecera('<https://x.com/en/>; rel="alternate"; hreflang="en", '
                                  '<https://x.com/de/>; rel="alternate"; hreflang="de"')
    filas = ex.deduplicar_hreflang(html + cab)
    assert [(f["lang"], f["origen"]) for f in filas] == [("en", "html"), ("de", "cabecera")]


# ---------------------------------------------------------------------------
# Analisis (SQLite)
# ---------------------------------------------------------------------------
ES, EN = "https://x.com/es/", "https://x.com/en/"


def test_dos_filas_iguales_hacia_un_404_dan_un_aviso():
    """Lo que ya esta guardado: re-analizar lo arregla sin re-rastrear."""
    s, j = _montar()
    es = _url(s, j, ES)
    _url(s, j, EN, status=404, indexable=False, estado="Client Error (404)")
    _anotar(s, es, ("es", ES), ("en-GB", EN), ("en-GB", EN), ("EN-gb", EN + "#top"))
    issues = _analizar(s, j)
    rotos = _de(issues, "hreflang_broken_target")
    assert len(rotos) == 1
    # Las repetidas no quedan a NULL: llevan el veredicto de la que se analizo.
    filas = s.query(Hreflang).filter(Hreflang.url_id == es.id).all()
    assert all(f.lang_valid is True for f in filas)


def test_repetida_en_head_y_body_cuenta_la_del_head():
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    s.add(Hreflang(url_id=es.id, lang="en", href=EN, en_body=True))
    s.add(Hreflang(url_id=es.id, lang="en", href=EN, en_body=False))
    s.add(Hreflang(url_id=es.id, lang="es", href=ES))
    _anotar(s, en, ("en", EN), ("es", ES))
    issues = _analizar(s, j)
    assert _de(issues, "hreflang_in_body") == []
    assert _de(issues, "hreflang_missing_return") == []
    filas = s.query(Hreflang).filter(Hreflang.url_id == es.id, Hreflang.lang == "en").all()
    assert {f.return_tag_ok for f in filas} == {True}


def test_un_retorno_declarado_por_cabecera_cuenta():
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    _anotar(s, es, ("es", ES), ("en", EN))
    s.add(Hreflang(url_id=en.id, lang="en", href=EN, origen="cabecera"))
    s.add(Hreflang(url_id=en.id, lang="es", href=ES, origen="cabecera"))
    s.flush()
    issues = _analizar(s, j)
    assert _de(issues, "hreflang_missing_return") == []
    ida = s.query(Hreflang).filter(Hreflang.url_id == es.id, Hreflang.lang == "en").one()
    assert ida.return_tag_ok is True


def test_un_pdf_con_hreflang_es_retorno_pero_no_fuente_de_avisos():
    """La pestana Hreflang filtra por HTML interno: un aviso en el PDF saldria
    en el chip y no en la lista. Sus anotaciones si son el retorno del HTML."""
    s, j = _montar()
    pdf_es, pdf_en = "https://x.com/es/guia.pdf", "https://x.com/en/guide.pdf"
    es = _url(s, j, pdf_es, tipo="pdf")
    _url(s, j, pdf_en, status=404, indexable=False, estado="Client Error (404)", tipo="pdf")
    s.add(Hreflang(url_id=es.id, lang="en", href=pdf_en, origen="cabecera"))
    s.add(Hreflang(url_id=es.id, lang="es", href=pdf_es, origen="cabecera"))
    html = _url(s, j, "https://x.com/en/a")
    _anotar(s, html, ("en", "https://x.com/en/a"), ("es", pdf_es))
    issues = _analizar(s, j)
    assert all(i.url_id == html.id for i in issues)
    # El HTML anota el PDF y el PDF no le devuelve el enlace: eso si se avisa.
    assert len(_de(issues, "hreflang_missing_return")) == 1


@pytest.mark.parametrize("origen", [None, "html"])
def test_sin_origen_es_del_html(origen):
    s, j = _montar()
    es, en = _url(s, j, ES), _url(s, j, EN)
    s.add(Hreflang(url_id=es.id, lang="en", href=EN, origen=origen))
    s.add(Hreflang(url_id=es.id, lang="es", href=ES, origen=origen))
    _anotar(s, en, ("en", EN), ("es", ES))
    assert _de(_analizar(s, j), "hreflang_missing_return") == []
