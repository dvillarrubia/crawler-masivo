"""Agrupar por la forma de la ruta, no por el numero de tramos.

Los scripts de control muestrean por plantilla: con "otras · 2 niveles" caian
en el mismo monton una ficha de producto y una pagina legal, y lo que se medía
era azar. Las reglas genericas que habia estaban escritas para un cliente de
hoteles, asi que en cualquier otro sitio no casaba ninguna.
"""

from __future__ import annotations

from shared.plantillas import firma_de_ruta


def test_la_home_es_la_home():
    assert firma_de_ruta("/") == "/ (home)"
    assert firma_de_ruta("") == "/ (home)"
    # La home de un idioma dice algo: no se mezcla con una pagina suelta.
    assert firma_de_ruta("/es") == "/:idioma (1n)"
    assert firma_de_ruta("/zapatillas-nike-air") == "/:slug (1n)"


def test_el_ultimo_tramo_es_la_pagina_no_la_plantilla():
    """Sin normalizarlo, cada articulo sale como plantilla propia con n=1."""
    assert (firma_de_ruta("/blog/mi-primer-post")
            == firma_de_ruta("/blog/otro-post-distinto"))


def test_un_identificador_de_cms_no_hace_plantilla_nueva():
    """`/26005-ebook-x` y `/31002-ebook-y` son la misma plantilla."""
    assert (firma_de_ruta("/es/libros/26005-ebook-el-corsario")
            == firma_de_ruta("/es/libros/31002-otro-libro"))
    assert firma_de_ruta("/p/12345-zapatilla") == "/p/:slug (2n)"


def test_las_fechas_y_los_numeros_son_comodines():
    assert (firma_de_ruta("/2018-05-17/dia-del-reciclaje")
            == firma_de_ruta("/2024-01-02/otro-dia"))
    assert firma_de_ruta("/page/2/algo") == firma_de_ruta("/page/7/algo")


def test_un_fichero_se_distingue_de_una_pagina():
    assert firma_de_ruta("/sitemap.xml") == "/:fichero.xml (1n)"
    assert firma_de_ruta("/sitemap.xml") != firma_de_ruta("/contacto")


def test_un_tramo_de_dos_letras_no_es_siempre_un_idioma():
    """`/tv/` pasaba por idioma y la seccion de video se comparaba con la
    portada del sitio."""
    from shared.plantillas import es_idioma

    assert es_idioma("es") is True
    assert es_idioma("pt-br") is True
    assert es_idioma("tv") is False
    assert es_idioma("ab") is False
    assert firma_de_ruta("/tv/") == "/:slug (1n)"
    assert firma_de_ruta("/es") == "/:idioma (1n)"
