"""La espera de render: que no vuelva a resolver con peticiones en vuelo.

El fallo que esto fija no se ve en los datos: la pagina se guarda con 200,
titulo y texto, solo que le falta el modulo que monta el XHR —y con el, sus
enlaces—. Medido en un rastreo real: 6.555 de 9.895 noticias sin el bloque de
relacionadas. La prueba de extremo a extremo con navegador esta en
``scripts/prueba_espera_render.py``; aqui se fija el contrato.
"""

from __future__ import annotations

import pytest

pytest.importorskip("scrapy")

from seo_crawler import render  # noqa: E402
from seo_crawler.spiders import seo_spider as sp  # noqa: E402


def test_la_espera_consulta_las_peticiones_en_vuelo():
    js = sp._JS_ESPERAR_DOM_QUIETO
    assert "__enVuelo" in js, "sin esto la espera solo mira el DOM"
    assert "PerformanceObserver" in js
    assert "MutationObserver" in js


def test_el_tope_siempre_resuelve():
    # El tope pone `forzado` y llama a fin() directamente: si no, una pagina
    # con red que no calla nunca se quedaria esperando y Playwright cerraria
    # la pestaña por timeout.
    js = sp._JS_ESPERAR_DOM_QUIETO
    assert "forzado = true; fin();" in js
    assert "if (!forzado && enVuelo()) { reinicia(); return; }" in js


def test_hay_piso_y_esta_por_debajo_del_tope():
    assert 0 < sp._ESPERA_PISO_MS < sp._ESPERA_TOPE_MS
    assert str(sp._ESPERA_PISO_MS) in sp._JS_ESPERAR_DOM_QUIETO


def test_el_contador_se_instala_antes_de_navegar():
    js = render._JS_CONTAR_PETICIONES
    assert "window.fetch" in js and "XMLHttpRequest.prototype.send" in js
    # Nunca negativo: un loadend duplicado dejaria el contador en -1 y la
    # espera creeria para siempre que no hay nada en vuelo.
    assert "Math.max(0," in js


def test_protocolo_legible():
    assert sp._normaliza_protocolo("h2") == "HTTP/2"
    assert sp._normaliza_protocolo("h3") == "HTTP/3"
    assert sp._normaliza_protocolo("http/1.1") == "HTTP/1.1"
    assert sp._normaliza_protocolo(None) is None
    assert sp._normaliza_protocolo("") is None
    assert sp._normaliza_protocolo("quic") == "quic"
