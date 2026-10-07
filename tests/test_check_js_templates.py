"""La clasificacion por plantilla del comprobador de render.

`grafo_fiable` decide si el PageRank del rastreo es de fiar, asi que agrupar mal
no es un detalle de presentacion: se muestrea una plantilla creyendo medir otra.
Los casos son los de la auditoria (#30).
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)


def _cargar():
    """Carga el script sin dejar rastro en `sys.path` ni en `sys.modules`.

    El script llama a `_preparar_rutas()`, que anade rutas candidatas (entre
    ellas `/app`, que en la imagen del crawler tiene una copia del codigo). Sin
    deshacerlo, el resto de la suite se quedaba importando ESA copia: 43 tests
    de otros ficheros fallaban por este, no por su propio codigo.
    """
    ruta = os.path.join(RAIZ, "scripts", "check_js_templates.py")
    camino_previo = list(sys.path)
    modulos_previos = set(sys.modules)
    spec = importlib.util.spec_from_file_location("cjt", ruta)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cjt"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = camino_previo
        for nombre in set(sys.modules) - modulos_previos - {"cjt"}:
            fichero = getattr(sys.modules[nombre], "__file__", None) or ""
            if fichero and not fichero.startswith(RAIZ):
                del sys.modules[nombre]
    return mod


cjt = _cargar()


def test_tv_no_es_la_home():
    """`^/[a-z]{2}/?$` daba "home" para `/tv/`."""
    assert cjt.clasificar("/") == "home"
    assert cjt.clasificar("/es") == "home"
    assert cjt.clasificar("/tv/") != "home"


@pytest.mark.parametrize("ruta", [
    "/es/productos/galletas-cookies/",
    "/es/privacy-shield-producto",
])
def test_una_pagina_que_menciona_cookies_no_es_una_pagina_legal(ruta):
    """El patron casaba la palabra en cualquier parte de la ruta."""
    assert cjt.clasificar(ruta) != "legal"


@pytest.mark.parametrize("ruta", [
    "/es/politica-de-cookies", "/privacy-policy", "/aviso-legal",
    "/de/datenschutz", "/ca/politica-de-galletes", "/cookies",
])
def test_las_paginas_legales_de_verdad_si_se_reconocen(ruta):
    assert cjt.clasificar(ruta) == "legal"


def test_lo_que_no_casa_se_agrupa_por_la_forma_no_por_niveles():
    """"otras · 2 niveles" metia en el mismo monton una ficha de producto y una
    pagina legal; y las reglas de producto estaban escritas para un cliente de
    hoteles, asi que en los demas sitios no casaba ninguna."""
    assert (cjt.clasificar("/es/libros/26005-ebook-uno")
            == cjt.clasificar("/es/libros/31002-otro"))
    assert cjt.clasificar("/es/libros/26005-x") != cjt.clasificar("/es/autores/9-y")
    assert "niveles" not in cjt.clasificar("/es/libros/26005-x")


def test_el_limpiador_de_banners_es_el_mismo_que_usa_el_rastreo():
    """Sin limpiar, el enlace que inyecta un gestor de consentimiento solo
    existe en el render y se contaba como enlace escondido tras JavaScript:
    cualquier sitio con OneTrust o Cookiebot salia con el grafo no fiable."""
    assert "INTOCABLES" in cjt._LIMPIADOR_DE_BANNERS
    assert "cookie-banner" in cjt._LIMPIADOR_DE_BANNERS
