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


# ---------------------------------------------------------------------------
# El crudo por el mismo camino que el rastreo (#30)
# ---------------------------------------------------------------------------
class _Respuesta:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


@pytest.fixture
def finge(monkeypatch):
    """Sustituye la descarga por una respuesta dada y anota con que se pidio."""
    from curl_cffi import requests as cr

    llamadas: list[dict] = []

    def poner(status_code: int, text: str):
        def get(url, **kw):
            llamadas.append({"url": url, **kw})
            return _Respuesta(status_code, text)

        monkeypatch.setattr(cr, "get", get)
        return llamadas

    return poner


def test_el_crudo_se_baja_con_huella_de_navegador(finge):
    """Con `urllib` un WAF que deja pasar al rastreo servia un desafio: el
    render si llegaba a la pagina y TODOS sus enlaces salian solo-JS."""
    llamadas = finge(200, "<html><head><title>Ficha</title></head><body>x</body></html>")
    assert "Ficha" in cjt.descargar_crudo("https://x.test/a")
    assert llamadas and llamadas[0].get("impersonate")


def test_un_403_o_un_muro_no_son_la_pagina(finge):
    finge(403, "<html><head><title>403 Forbidden</title></head></html>")
    with pytest.raises(cjt.NoEsLaPagina):
        cjt.descargar_crudo("https://x.test/a")
    finge(200, "<html><head><title>Just a moment...</title></head></html>")
    with pytest.raises(cjt.NoEsLaPagina):
        cjt.descargar_crudo("https://x.test/a")


def _playwright_falso(monkeypatch, html_render: str):
    """Chromium de mentira: cada pagina renderiza `html_render`."""
    import types as _types

    class Pagina:
        async def goto(self, *a, **k):
            pass

        async def wait_for_timeout(self, *a):
            pass

        async def evaluate(self, *a):
            pass

        async def content(self):
            return html_render

        async def close(self):
            pass

    class Contexto:
        async def new_page(self):
            return Pagina()

    class Navegador:
        async def new_context(self, **k):
            return Contexto()

        async def close(self):
            pass

    class Chromium:
        async def launch(self, **k):
            return Navegador()

    class _Gestor:
        async def __aenter__(self):
            return _types.SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *a):
            return False

    modulo = _types.ModuleType("playwright.async_api")
    modulo.async_playwright = lambda: _Gestor()
    monkeypatch.setitem(sys.modules, "playwright.async_api", modulo)


def test_la_muestra_bloqueada_se_cuenta_y_la_plantilla_no_desaparece(finge, monkeypatch):
    """Antes una plantilla cuyas muestras fallaban todas desaparecia del
    resultado; y con el WAF dejando pasar solo al navegador, se comparaba el
    muro (0 enlaces) contra la pagina renderizada (todos)."""
    import asyncio

    finge(200, "<html><head><title>Just a moment...</title></head><body></body></html>")
    _playwright_falso(monkeypatch, '<html><body><a href="/b">b</a><a href="/c">c</a></body></html>')
    resultados = asyncio.run(cjt.analizar(
        [("fichas  (n=40)", ["https://x.test/a1", "https://x.test/a2"])], 0, {"x.test"},
    ))
    (r,) = resultados
    assert r["muestras"] == 0
    assert r["sin_comprobar"] == 2
    assert r.get("enlaces_solo_js", 0) == 0  # no se afirma nada sobre el grafo
    assert any("WAF" in m for m in r["motivos"])
