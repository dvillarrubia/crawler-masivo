"""Las 24 plantillas realistas de la auditoria contra `_detect_link_position`.

La auditoria (#24) midio que la clasificacion falla en **13 de 24** plantillas
realistas. Importa por dos cosas: `link_position` decide el peso del enlace en
el PageRank cuando el rastreo es pequeno (y desempata cuando no), y
`high_outlink_count` cuenta SOLO los enlaces de contenido — si un menu sale como
contenido, ese aviso salta en todas las paginas del sitio.

Al escribir estos casos salian 20/24: los arreglos de octubre (tokens por
palabra entera, camelCase, landmarks) ya habian resuelto la mayoria. Los cuatro
que quedaban se arreglan en este mismo cambio.
"""

from __future__ import annotations

import pytest

from parsel import Selector

from seo_crawler.extractors import _detect_link_position


def _pos(html: str) -> str:
    s = Selector(text=f"<html><body>{html}</body></html>")
    return _detect_link_position(s.css("a")[0])


CASOS = [
    # --- navegacion ---
    ("bootstrap navbar", '<nav class="navbar navbar-expand-lg"><ul class="navbar-nav">'
     '<li><a href="/a">x</a></li></ul></nav>', "nav"),
    ("menu en div con clase", '<div class="main-menu"><ul><li><a href="/a">x</a></li></ul></div>', "nav"),
    ("menu por role", '<div role="navigation"><a href="/a">x</a></div>', "nav"),
    ("nav con aria-label", '<div aria-label="Main navigation" role="navigation"><a href="/a">x</a></div>', "nav"),
    ("megamenu con main dentro de nav", '<nav><main><a href="/a">x</a></main></nav>', "nav"),
    ("breadcrumb", '<div class="breadcrumbs"><a href="/a">x</a></div>', "nav"),
    ("menu movil oculto", '<div class="mobile-menu hidden"><a href="/a">x</a></div>', "nav"),
    ("indice dentro del articulo", '<article><nav class="toc"><a href="#s1">x</a></nav></article>', "nav"),
    # --- cabecera y pie de SITIO ---
    ("tailwind header sin nav", '<header class="sticky top-0"><div class="flex"><a href="/a">x</a></div></header>', "header"),
    ("header con clase hero", '<header class="hero"><h1>t</h1><a href="/a">x</a></header>', "header"),
    ("footer de wordpress", '<footer id="colophon" class="site-footer"><a href="/a">x</a></footer>', "footer"),
    ("footer por role", '<div role="contentinfo"><a href="/a">x</a></div>', "footer"),
    # --- barra lateral ---
    ("widget area", '<aside class="widget-area"><a href="/a">x</a></aside>', "sidebar"),
    ("sidebar por id de wordpress", '<div id="secondary"><a href="/a">x</a></div>', "sidebar"),
    # --- contenido ---
    ("parrafo de un article", '<main><article><p><a href="/a">x</a></p></article></main>', "content"),
    ("cabecera del propio article", '<article><header class="entry-header"><a href="/a">x</a></header></article>', "content"),
    ("pie del propio article", '<article><footer class="entry-footer"><a href="/a">x</a></footer></article>', "content"),
    ("aside dentro del article", '<article><aside class="callout"><a href="/a">x</a></aside></article>', "content"),
    ("seccion de elementor", '<div class="elementor-section elementor-top-section">'
     '<div class="elementor-widget-container"><a href="/a">x</a></div></div>', "content"),
    ("enlace sin envoltorio", '<a href="/a">x</a>', "content"),
    ("site-header envolviendo la pagina", '<div class="site-header"><div class="wrapper">'
     '<main><article><a href="/a">x</a></article></main></div></div>', "content"),
    ("portlet de liferay", '<div class="portlet-body"><div class="journal-content-article">'
     '<a href="/a">x</a></div></div>', "content"),
    ("clases utilitarias de tailwind", '<div class="mt-auto border-t py-8"><a href="/a">x</a></div>', "content"),
    ("ficha en un listado", '<section class="product-grid"><div class="card"><a href="/a">x</a></div></section>', "content"),
]


@pytest.mark.parametrize("nombre,html,esperado", CASOS, ids=[c[0] for c in CASOS])
def test_plantillas_realistas(nombre, html, esperado):
    assert _pos(html) == esperado


def test_son_veinticuatro():
    """La auditoria hablaba de 24 plantillas: si se anaden mas, que se note."""
    assert len(CASOS) == 24


def test_el_menu_de_una_carta_de_restaurante_es_el_riesgo_conocido():
    """`class="menu"` se clasifica como navegacion, y en una carta de
    restaurante seria contenido. Se asume a proposito: equivocarse al otro lado
    —tratar un menu de cientos de enlaces como editorial— hace saltar
    `high_outlink_count` en TODAS las paginas del sitio, y este caso afecta a
    una. Queda escrito para que nadie lo descubra como sorpresa."""
    assert _pos('<div class="menu"><a href="/plato">Lubina</a></div>') == "nav"
