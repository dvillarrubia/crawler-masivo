"""Enlaces que Google no puede seguir (decision 106): el extractor.

La taxonomia sigue a «Make your links crawlable» de Google: solo el `<a>` con un
`href` que resuelve a una URL es un enlace que el buscador sigue. Lo demas se
guarda aparte con su tipo —para poder decirlo— y NO entra en `extract_links`,
que es lo que alimenta el grafo, los entrantes, el PageRank y la frontera.
"""

from __future__ import annotations

import pytest

pytest.importorskip("parsel")

from parsel import Selector  # noqa: E402

from seo_crawler import extractors as ex  # noqa: E402
from shared import enlaces_no_rastreables as enr  # noqa: E402

BASE = "https://x.com/pagina"
HOSTS = {"x.com"}


def _enr(cuerpo: str) -> list[dict]:
    return ex.extract_enlaces_no_rastreables(
        Selector(text=f"<html><body>{cuerpo}</body></html>"), BASE, HOSTS)


def _tipos(cuerpo: str) -> list[tuple[str, str | None]]:
    return [(f["crawlability"], f["destino"]) for f in _enr(cuerpo)]


# --- Los ejemplos de Google, uno por uno -----------------------------------
@pytest.mark.parametrize("cuerpo, esperado", [
    # «<a href="javascript:goTo('products')">»: no dice a donde va.
    ("<a href=\"javascript:goTo('products')\">P</a>", [(enr.JAVASCRIPT_HREF, None)]),
    # «<a href="javascript:window.location.href='/products'">»: si lo dice.
    ("<a href=\"javascript:window.location.href='/products'\">P</a>",
     [(enr.JAVASCRIPT_HREF, "https://x.com/products")]),
    # «<a onclick="goto('https://example.com')">»
    ("<a onclick=\"goto('https://x.com/c')\">C</a>",
     [(enr.ONCLICK_SIN_HREF, "https://x.com/c")]),
    # «<a routerLink="products/category">»
    ('<a routerLink="productos/categoria">R</a>',
     [(enr.A_SIN_HREF, "https://x.com/productos/categoria")]),
    # «<span href="https://example.com">»
    ('<span href="https://x.com/s">S</span>', [(enr.HREF_FUERA_DE_A, "https://x.com/s")]),
    # La tarjeta entera clicable sin un `<a>` dentro.
    ("<div onclick=\"location.href='/ficha'\">Ficha</div>",
     [(enr.ONCLICK_EN_OTRA_ETIQUETA, "https://x.com/ficha")]),
    # La ruta de una SPA en el fragmento (Google pide la History API).
    ('<a href="#/productos">P</a>', [(enr.FRAGMENTO_COMO_RUTA, None)]),
    ('<a href="#!/productos">P</a>', [(enr.FRAGMENTO_COMO_RUTA, None)]),
    # `href="#"` CON un onclick que navega: el enlace esta en el onclick.
    ("<a href=\"#\" onclick=\"window.open('/promo')\">P</a>",
     [(enr.ONCLICK_SIN_HREF, "https://x.com/promo")]),
])
def test_cada_ejemplo_de_google_tiene_su_tipo(cuerpo, esperado):
    assert _tipos(cuerpo) == esperado


# --- Lo que NO es un enlace no rastreable ----------------------------------
@pytest.mark.parametrize("cuerpo", [
    '<a href="#">Menu</a>',                # conmutador: no se sabe si navega
    '<a href="">Vacio</a>',
    '<a href="#seccion">Ir a la seccion</a>',
    '<a name="ancla"></a>',
    '<a href="mailto:a@x.com">m</a><a href="tel:+34">t</a>',
    '<a href="/ok">Un enlace normal</a>',
    '<a href="/ok" onclick="track()">Con onclick y href: Google lo sigue</a>',
    '<button onclick="abrirModal()">Boton</button>',   # sin destino: un boton
    # Un `onclick` que no navega es un boton aunque vaya en un `<a>`.
    "<a onclick=\"$('.mas').show()\">Leer mas</a>",
    '<a href="#" onclick="window.scrollTo({top: 0})">Subir</a>',
    '<svg><use href="#icono"></use><image href="/i.png"></image></svg>',
    '<link href="/estilo.css" rel="stylesheet">',
    # La tarjeta con onclick que YA tiene el `<a href>` al mismo destino.
    "<div onclick=\"location='/ficha'\"><a href=\"/ficha\">Ficha</a></div>",
    '<template><a href="javascript:x()">t</a></template>',
    '<noscript><span href="/n">n</span></noscript>',
])
def test_lo_que_no_es_un_enlace_perdido_no_se_guarda(cuerpo):
    assert _enr(cuerpo) == []


def test_una_cadena_cualquiera_no_es_un_destino():
    """`goTo('products')` no dice a donde va: adivinar seria afirmar."""
    assert enr.destino_en_script("goTo('products')") is None
    assert enr.destino_en_script("abrir('/ficha/3')") == "/ficha/3"
    assert enr.destino_en_script("location.assign('https://x.com/a')") == "https://x.com/a"
    assert enr.destino_en_script("/* comentario */ hacer()") is None


def test_nada_de_esto_entra_en_extract_links():
    """Disjuntos por construccion: lo que se guarda aqui no es una arista, no
    suma entrantes, no reparte PageRank y no se sigue."""
    cuerpo = (
        "<a href=\"javascript:location='/a'\">A</a><a onclick=\"location='/b'\">B</a>"
        '<a routerLink="/c">C</a><span href="/d">D</span>'
        "<div onclick=\"location='/e'\">E</div><a href=\"#/f\">F</a>"
        '<a href="/normal">N</a>'
    )
    s = Selector(text=f"<html><body>{cuerpo}</body></html>")
    assert [l["url"] for l in ex.extract_links(s, BASE, HOSTS)] == ["https://x.com/normal"]
    destinos = {f["destino"] for f in ex.extract_enlaces_no_rastreables(s, BASE, HOSTS)}
    assert "https://x.com/normal" not in destinos
    assert {"https://x.com/a", "https://x.com/b", "https://x.com/c", "https://x.com/d",
            "https://x.com/e"} <= destinos


def test_interno_externo_posicion_y_ancla():
    filas = _enr("<nav><a href=\"javascript:location='https://otro.com/x'\">Fuera</a></nav>"
                 "<main><span href=\"/dentro\" aria-label=\"Dentro\"></span></main>")
    assert [(f["destino_interno"], f["link_position"], f["anchor_text"]) for f in filas] == [
        (False, "nav", "Fuera"), (True, "content", "Dentro")]


def test_el_megamenu_repetido_es_una_fila():
    """Como `links` (decision 94): lo identico en la pagina va agrupado."""
    menu = "<nav><a href=\"javascript:location='/m'\">M</a></nav>"
    filas = _enr(menu * 3)
    assert len(filas) == 1 and filas[0]["repeticiones"] == 3


def test_hay_tope_por_pagina():
    filas = _enr("".join(f"<span href='/s/{i}'>{i}</span>" for i in range(enr.MAX_POR_PAGINA + 50)))
    assert sum(f["repeticiones"] for f in filas) == enr.MAX_POR_PAGINA


def test_un_destino_no_http_no_es_destino():
    filas = _enr("<span href=\"ftp://x.com/f\">f</span>")
    assert filas[0]["destino"] is None and filas[0]["destino_interno"] is None


def test_utm():
    assert enr.tiene_utm("https://x.com/a?utm_source=home")
    assert enr.tiene_utm("https://x.com/a?x=1&UTM_campaign=b")
    assert not enr.tiene_utm("https://x.com/utm_source/a")
    assert not enr.tiene_utm(None)
