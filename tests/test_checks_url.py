"""Los avisos sobre la forma de la URL: que casen lo que dicen casar.

Tres de estas reglas estaban mal y ninguna lo decia: una regex que no casaba el
ejemplo de su propio comentario, otra que marcaba un listado como si fuera una
faceta, y un aviso que no salto NUNCA en ningun censo porque comprobaba la URL
escapada.
"""

from __future__ import annotations

from urllib.parse import unquote

from analysis.analyzer import (
    _CMS_FACETED_RE,
    _MULTIPLE_SLASHES_RE,
    _NON_ASCII_RE,
    _NON_SEO_FRIENDLY_RE,
)


def test_los_escapes_unicode_de_javascript_son_hexadecimales():
    """`%5Cu\\d{4}` pedia digitos, asi que `%5Cu002F` —el ejemplo del propio
    comentario— no casaba: la `F` final lo tumbaba."""
    assert _NON_SEO_FRIENDLY_RE.search("/a%5Cu002F")
    assert _NON_SEO_FRIENDLY_RE.search("/a%5Cu0041")
    assert _NON_SEO_FRIENDLY_RE.search("/a%5cu00ff")   # minusculas tambien


def test_lo_demas_que_delata_una_url_basura():
    assert _NON_SEO_FRIENDLY_RE.search("/p;jsessionid=A1B2")
    assert _NON_SEO_FRIENDLY_RE.search("/p\\u002F")
    assert _NON_SEO_FRIENDLY_RE.search("/p%00")
    assert not _NON_SEO_FRIENDLY_RE.search("/una-url-normal/")


def test_la_faceta_real_de_liferay():
    """`/-/` es el prefijo de las rutas de portlet de Liferay y
    `/-/categories/123` es la faceta: no la cazaba ninguna alternativa."""
    assert _CMS_FACETED_RE.search("/web/guest/-/categories/12345")
    assert _CMS_FACETED_RE.search("/-/tags/novedades")
    assert _CMS_FACETED_RE.search("/es/-/labels/x")


def test_un_listado_no_es_una_faceta():
    """`/elem_entry_list/` casaba por `/ELEM_ENTRY` con IGNORECASE. Un listado
    de contenidos es una pagina legitima; marcarla como faceta manda al cliente
    a bloquear algo que quiere indexar."""
    assert not _CMS_FACETED_RE.search("/elem_entry_list/")
    assert not _CMS_FACETED_RE.search("/elem_entry_listing/novedades")
    # El portlet exacto si se sigue detectando.
    assert _CMS_FACETED_RE.search("/ELEM_ENTRY/123")


def test_las_facetas_clasicas_siguen_detectandose():
    for ruta in ("/productos.categories/zapatos", "/a;tags/x", "/b|filters/y",
                 "/c.taxonomy/z", "/BP_Categories/1"):
        assert _CMS_FACETED_RE.search(ruta), ruta


def test_el_aviso_de_no_ascii_necesita_decodificar():
    """Scrapy guarda las URLs escapadas, que son ASCII puro: comprobando la
    cadena cruda este aviso no salto nunca, ni en censos con URLs en catalan,
    castellano o arabe."""
    escapada = "/cafe%CC%81-y-ni%C3%B1os"
    assert not _NON_ASCII_RE.search(escapada), "la escapada es ASCII"
    assert _NON_ASCII_RE.search(unquote(escapada)), "decodificada, no lo es"
    # Y una URL limpia sigue sin marcarse de ninguna de las dos formas.
    assert not _NON_ASCII_RE.search(unquote("/cafe-y-ninos"))


def test_las_barras_repetidas_no_confunden_el_esquema():
    assert _MULTIPLE_SLASHES_RE.search("/a//b")
    assert not _MULTIPLE_SLASHES_RE.search("https://x.com/a/b")
    assert not _MULTIPLE_SLASHES_RE.search("/a/b/")
