"""El filtro por tipo de recurso no puede gastar el presupuesto del rastreo.

`max_urls` se mide con el contador de paginas, y el contador se incrementaba
ANTES del filtro: los recursos que el job excluye (PDF, imagenes, fuentes)
gastaban presupuesto aunque no se guardara ni una fila de ellos. Pasa con los
que no se pueden reconocer por la URL —un PDF servido desde `/descarga-1`, sin
extension—, porque esos no los para el filtro de seguimiento y hay que
descargarlos para saber lo que son.

Comprobado contra el codigo anterior: el PDF filtrado dejaba el contador en 1.
"""

from __future__ import annotations

import pytest

pytest.importorskip("scrapy")

import shared.database  # noqa: E402

from scrapy.http import HtmlResponse, Request, Response  # noqa: E402


@pytest.fixture
def spider():
    shared.database.SessionLocal = lambda: None
    from seo_crawler.spiders.seo_spider import SeoSpider

    sp = SeoSpider(job_id="t")
    sp._allowed_resource_types = {"html", "redirect"}
    sp._crawled_count = 0
    sp.allowed_hosts = {"x.com"}
    sp._crawl_subdomains = False
    sp.max_urls = None
    sp._extraction = {"extract_page_content": False}
    return sp


def test_un_recurso_filtrado_no_gasta_presupuesto(spider):
    peticion = Request("https://x.com/descarga-1", meta={"depth": 1})
    respuesta = Response(
        "https://x.com/descarga-1", status=200,
        headers={b"Content-Type": b"application/pdf"}, body=b"%PDF-1.4",
        request=peticion,
    )
    assert list(spider.parse(respuesta)) == []
    assert spider._crawled_count == 0


def test_una_pagina_html_si_gasta_presupuesto(spider):
    """Control: que el test anterior no pase porque el contador no suba nunca."""
    respuesta = HtmlResponse(
        "https://x.com/p", status=200, headers={b"Content-Type": b"text/html"},
        body=b"<html><head><title>t</title></head><body><p>hola</p></body></html>",
        request=Request("https://x.com/p", meta={"depth": 1}),
    )
    assert list(spider.parse(respuesta))
    assert spider._crawled_count == 1


def test_el_svg_es_su_propio_tipo():
    """Clasificado como "image", `crawl_svg` no tenia ningun efecto: no habia
    forma de excluirlo ni de incluirlo aparte de las imagenes."""
    from seo_crawler.extractors import classify_resource_type

    assert classify_resource_type("image/svg+xml", "https://x.com/logo.svg") == "svg"
    assert classify_resource_type(None, "https://x.com/logo.svg") == "svg"
    assert classify_resource_type("image/png", "https://x.com/a.png") == "image"


# ---------------------------------------------------------------------------
# Lo que la extension ya dice no hace falta descargarlo (R12 de #25)
# ---------------------------------------------------------------------------
def test_la_extension_ahorra_la_peticion(spider):
    """El filtro por tipo necesita el `Content-Type`, pero cuando la extension
    ya lo dice se puede ahorrar la peticion entera, que es lo que cuesta.
    Medido en el rastreo de un e-commerce: el sitio responde a 2,7 s de media y
    28 de cada 60 respuestas se descargaban para tirarlas, con el ritmo cayendo
    de 50 a 10 paginas por minuto."""
    spider._exclude_patterns = []
    spider._include_patterns = []
    spider._max_url_length = 0
    spider._max_folder_depth = 0
    for url in ("https://x.com/foto.jpg", "https://x.com/doc.pdf",
                "https://x.com/estilo.css", "https://x.com/app.js",
                "https://x.com/logo.svg"):
        assert spider._should_follow(url) is False, url
    # Con el tipo permitido, se pide.
    spider._allowed_resource_types = {"html", "redirect", "image", "pdf", "css", "js", "svg"}
    for url in ("https://x.com/foto.jpg", "https://x.com/doc.pdf"):
        assert spider._should_follow(url) is True, url


def test_un_punto_en_el_slug_no_es_una_extension(spider):
    """`producto-2.5-kg` o `v1.2-guia` son paginas. Con `"other"` como respuesta
    por defecto de la clasificacion por extension, se habrian dejado de pedir:
    perder paginas de producto en silencio es peor que descargar una imagen."""
    spider._exclude_patterns = []
    spider._include_patterns = []
    spider._max_url_length = 0
    spider._max_folder_depth = 0
    for url in ("https://x.com/producto-2.5-kg", "https://x.com/v1.2-guia",
                "https://x.com/mancuerna-2-5-kgs.html", "https://x.com/pagina",
                "https://x.com/descarga?id=1"):
        assert spider._should_follow(url) is True, url


def test_tipo_por_extension_dice_no_se_cuando_no_se_sabe():
    from seo_crawler.extractors import tipo_por_extension

    assert tipo_por_extension("https://x.com/a.jpg") == "image"
    assert tipo_por_extension("https://x.com/a.svg") == "svg"
    assert tipo_por_extension("https://x.com/a.html") == "html"
    assert tipo_por_extension("https://x.com/producto-2.5-kg") is None
    assert tipo_por_extension("https://x.com/pagina") is None
    assert tipo_por_extension("https://x.com/") is None
