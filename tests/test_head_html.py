"""Donde corta el navegador el ``<head>`` y que metadatos deja en el body.

Decision 102. Cada caso lleva lo que hace Chromium (scripting activado),
comprobado el 2026-10-16 con el Chromium de la imagen del crawler cargando el
HTML y preguntando ``e.closest('head')`` por el title, el meta robots, la meta
description y el canonical. ``test_coincide_con_chromium`` lo repite si hay
navegador: en CI corre, porque el trabajo del crawler instala Chromium
(``playwright install --with-deps chromium``); sin navegador se salta.

Los casos que separan a este lector de lxml y de html5lib, que es por lo que
existe:

- ``noscript_img``, ``noscript_iframe`` y ``template``: html5lib corta el head
  (lee el noscript con scripting desactivado y no conoce template en el head);
  Chromium no. Son el pixel de Facebook y el GTM pegado en el head.
- ``svg_en_head`` y ``custom_el``: lxml deja el meta o el title en el head;
  Chromium los manda al body.
- ``tras_cierre_head``: lxml saca la description de los dos (ni head ni body);
  Chromium la devuelve al head.
"""

from __future__ import annotations

import asyncio

import pytest

from seo_crawler.head_html import LIMITE_CARACTERES, leer_head

D = "<!doctype html><html>"

# nombre: (html, cortado_por, fuera_del_head). `fuera_del_head` es lo que
# Chromium deja fuera del head de entre title/robots/description/canonical.
CASOS = {
    "img_en_head": (
        D + "<head><title>T</title><img src=x.png><meta name=robots content=noindex>"
        "</head><body><p>x</p></body></html>",
        "img", ["robots"]),
    "noscript_img": (
        D + "<head><noscript><img src=x.png></noscript><meta name=robots content=noindex>"
        "<title>T</title></head><body><p>x</p></body></html>",
        None, []),
    "texto_suelto": (
        D + "<head><meta charset=utf-8>texto suelto<title>T</title></head>"
        "<body><p>x</p></body></html>",
        "#texto", ["title"]),
    "tras_cierre_head": (
        D + "<head><title>T</title></head><meta name=description content=d>"
        "<body><p>x</p></body></html>",
        None, []),
    "div_en_head": (
        D + "<head><div></div><link rel=canonical href=https://e.com/a><title>T</title>"
        "</head><body><p>x</p></body></html>",
        "div", ["canonical", "title"]),
    "iframe_en_head": (
        D + "<head><iframe src=x></iframe><title>T</title></head><body><p>x</p></body></html>",
        "iframe", ["title"]),
    "segundo_head": (
        D + "<head><title>T</title></head><body><head><meta name=robots content=noindex>"
        "</head><p>x</p></body></html>",
        None, ["robots"]),
    "sin_head": (
        D + "<meta name=robots content=noindex><title>T</title><p>x</p></html>",
        "p", []),
    "template": (
        D + "<head><template><div>x</div></template><meta name=robots content=noindex>"
        "<title>T</title></head><body><p>x</p></body></html>",
        None, []),
    "custom_el": (
        D + "<head><my-el></my-el><title>T</title></head><body><p>x</p></body></html>",
        "my-el", ["title"]),
    "svg_en_head": (
        D + "<head><svg><symbol id=a></symbol></svg><meta name=robots content=noindex>"
        "<title>T</title></head><body><p>x</p></body></html>",
        "svg", ["robots", "title"]),
    "comentario": (
        D + "<head><!-- <div> --><meta name=robots content=noindex><title>T</title></head>"
        "<body><p>x</p></body></html>",
        None, []),
    "itemprop_body": (
        D + "<head><title>T</title></head><body><div itemscope>"
        "<meta itemprop=name content=x></div></body></html>",
        None, []),
    "noscript_iframe": (
        D + "<head><noscript><iframe src=x></iframe></noscript>"
        "<link rel=canonical href=https://e.com/a><title>T</title></head>"
        "<body><p>x</p></body></html>",
        None, []),
    "script_con_div": (
        D + "<head><script>var s='<div>';</script><title>T</title></head>"
        "<body><p>x</p></body></html>",
        None, []),
    "body_doble": (
        D + "<head><title>T</title></head><body><p>x</p><body class=b><p>y</p></body></html>",
        None, []),
    "nbsp_en_head": (
        D + "<head><meta charset=utf-8>&nbsp;<title>T</title></head>"
        "<body><p>x</p></body></html>",
        "#texto", ["title"]),
    # Dentro de <foreignObject> se vuelve a leer HTML, pero al cerrarlo se
    # sigue en el SVG: el <title> de despues es el del icono.
    "foreignobject_y_title_svg": (
        D + "<head><title>T</title></head><body><svg><foreignObject><div>x</div>"
        "</foreignObject><title>icono</title></svg><p>x</p></body></html>",
        None, []),
    "title_html_en_foreignobject": (
        D + "<head><title>T</title></head><body><svg><foreignObject><div>"
        "<title>dentro</title></div></foreignObject></svg></body></html>",
        None, ["title"]),
}


@pytest.mark.parametrize("nombre", sorted(CASOS))
def test_casos_del_head(nombre):
    html, cortado, fuera = CASOS[nombre]
    assert leer_head(html) == {"cortado_por": cortado, "fuera_del_head": fuera}


def test_el_title_de_un_svg_no_es_el_de_la_pagina():
    """Los iconos llevan `<title>`: medido en penguin, cuatro por pagina."""
    html = (D + "<head><title>T</title></head><body><svg><title>menu</title>"
            "<path d=''/></svg><p>x</p></body></html>")
    assert leer_head(html)["fuera_del_head"] == []


def test_un_meta_dentro_de_un_svg_sale_a_html():
    """`meta` cierra el SVG (lista de la especificacion): es HTML en el body."""
    html = (D + "<head><title>T</title></head><body><svg><g>"
            "<meta name=robots content=noindex></g></svg></body></html>")
    assert leer_head(html)["fuera_del_head"] == ["robots"]


def test_documento_incrustado_en_el_body():
    """Penguin `/fragmento`: un capitulo EPUB entero, con su head, en el body."""
    html = (D + "<head><title>Libro</title></head><body><div id=c>"
            "<html><head><meta charset=utf-8><title>cap-2</title></head>"
            "<body><p>texto</p></body></html></div></body></html>")
    assert leer_head(html) == {"cortado_por": None, "fuera_del_head": ["title"]}


def test_lo_que_va_en_el_body_dentro_de_template_o_noscript_no_cuenta():
    html = (D + "<head><title>T</title></head><body>"
            "<template><meta name=robots content=noindex></template>"
            "<noscript><meta name=description content=d></noscript></body></html>")
    assert leer_head(html)["fuera_del_head"] == []


def test_metadatos_que_se_reconocen():
    html = (D + "<head><title>T</title></head><body>"
            "<meta name=' GoogleBot ' content=noindex>"
            "<link rel='Alternate' hreflang=en href=/en>"
            "<link rel='stylesheet' href=/a.css>"
            "<meta name=viewport content=x></body></html>")
    assert leer_head(html)["fuera_del_head"] == ["googlebot", "hreflang"]


def test_una_etiqueta_partida_entre_trozos_se_ve():
    """El lector va por trozos de 64 kB y deja de leer si no quedan candidatos:
    un meta partido por el borde tiene que seguir viendose."""
    relleno = "<p>" + "x" * (64 * 1024 - 60) + "</p>"
    html = (D + "<head><title>T</title></head><body>" + relleno
            + "<meta name=robots content=noindex></body></html>")
    for desplazamiento in range(0, 40, 3):
        doc = html.replace("<body>", "<body>" + " " * desplazamiento)
        assert leer_head(doc)["fuera_del_head"] == ["robots"], desplazamiento


def test_lo_que_pasa_del_limite_no_se_mira():
    """Googlebot no descarga mas alla de 2 MB: lo de detras lo dice el otro aviso."""
    html = (D + "<head><title>T</title></head><body>" + "<p>x</p>" * (LIMITE_CARACTERES // 8)
            + "<meta name=robots content=noindex></body></html>")
    assert leer_head(html)["fuera_del_head"] == []


def test_un_nombre_de_etiqueta_que_no_lo_es_no_se_guarda_tal_cual():
    """`html_meta.head_cortado_por` es VARCHAR(64): una errata sin espacios es
    UNA etiqueta para HTMLParser, y guardada entera tumbaria el INSERT en
    Postgres. Va a `#otro`, que tampoco parte el grupo en una fila por errata."""
    errata = ('<metaproperty="og:title"content="Zapatillas-de-running-para-'
              'mujer-con-amortiguacion-maxima">')
    for corte in (errata, "<" + "a" * 80 + ">"):
        html = D + "<head>" + corte + "<title>T</title></head><body></body></html>"
        assert leer_head(html) == {"cortado_por": "#otro", "fuera_del_head": ["title"]}
    html = D + "<head><mi-widget></mi-widget><title>T</title></head><body></body></html>"
    assert leer_head(html)["cortado_por"] == "mi-widget"


def test_vacio_o_ilegible():
    assert leer_head("") == {"cortado_por": None, "fuera_del_head": []}
    assert leer_head(None) == {"cortado_por": None, "fuera_del_head": []}


# -- Contraste con Chromium ------------------------------------------------

_JS = """() => {
  const r = [];
  for (const [k, sel] of [['title','title'], ['robots','meta[name=robots]'],
        ['description','meta[name=description]'], ['canonical','link[rel=canonical]']]) {
    for (const e of document.querySelectorAll(sel)) {
      // Solo elementos HTML: el <title> de un SVG no es el de la pagina, pero
      // uno dentro de un <foreignObject> si es HTML.
      if (e.namespaceURI !== 'http://www.w3.org/1999/xhtml') continue;
      if (!e.closest('head')) r.push(k);
    }
  }
  return [...new Set(r)].sort();
}"""


def _chromium():
    pw = pytest.importorskip("playwright.async_api")

    async def correr():
        async with pw.async_playwright() as p:
            try:
                navegador = await p.chromium.launch()
            except Exception as exc:  # noqa: BLE001 - sin navegador descargado
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await navegador.new_page()
            fuera = {}
            for nombre, (html, _c, _f) in CASOS.items():
                await pagina.set_content(html)
                fuera[nombre] = await pagina.evaluate(_JS)
            await navegador.close()
            return fuera

    return asyncio.run(correr())


def test_coincide_con_chromium():
    fuera = _chromium()
    for nombre, (html, _cortado, esperado) in CASOS.items():
        assert fuera[nombre] == esperado, nombre
        assert leer_head(html)["fuera_del_head"] == fuera[nombre], nombre
