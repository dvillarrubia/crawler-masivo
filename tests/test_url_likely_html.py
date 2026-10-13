"""Que URLs se mandan a Chromium (issue #25).

`_url_likely_html` solo decide el render, y los dos errores no pesan igual:
renderizar un PDF gasta una pestana; no renderizar una pagina la guarda sin lo
que monta el JavaScript. Por eso los casos de "si es HTML" importan tanto como
los nuevos de "no lo es".
"""

from __future__ import annotations

import pytest

pytest.importorskip("scrapy")
pytest.importorskip("scrapy_playwright")
pytest.importorskip("redis")

from seo_crawler.spiders.seo_spider import _url_likely_html  # noqa: E402


@pytest.mark.parametrize("url", [
    "https://x.com/fotos/IMG_0042.HEIC",
    "https://x.com/fotos/portada.heif",
    "https://x.com/bases/convocatoria-2026.odt",
    "https://x.com/presupuesto.ods",
    "https://x.com/libros/guia.epub",
    # Descargas por query string: la ruta es la de una pagina
    "https://x.com/informe/?download=1",
    "https://x.com/?download=123",
    "https://x.com/descargas/?dl",
    "https://x.com/informe?format=pdf",
    "https://x.com/listado?export=CSV",
    "https://x.com/datos?output=json",
])
def test_no_es_html(url):
    assert _url_likely_html(url) is False


@pytest.mark.parametrize("url", [
    "https://x.com/",
    "https://x.com/pagina",
    "https://x.com/pagina.html",
    # Un punto en el ultimo tramo no es una extension (decision 59)
    "https://x.com/producto-2.5-kg",
    "https://x.com/v1.2-guia/",
    # Parametros que se llaman parecido y son paginas
    "https://x.com/articulo?format=amp",
    "https://x.com/articulo?format=html",
    "https://x.com/informe?download=0",
    "https://x.com/informe?download=false",
    "https://x.com/descargas-gratis?page=2",
    "https://x.com/buscar?q=pdf",
])
def test_si_es_html(url):
    assert _url_likely_html(url) is True
