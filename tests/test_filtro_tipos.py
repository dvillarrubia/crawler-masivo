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
