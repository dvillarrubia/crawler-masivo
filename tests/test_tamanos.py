"""`content_length` y `transfer_size` estaban cambiados.

Scrapy descomprime el cuerpo pero no toca la cabecera: para un cuerpo de 28.055
bytes servido con gzip, `len(response.body)` son 28.055 y el `Content-Length`
de la cabecera son 143. Se guardaba el 143 como `content_length` —el peso de la
pagina dividido por 4 o 5 en cualquier sitio con gzip— y el 28.055 como
`transfer_size`, que es justo lo que viaja por el cable y lo que cuenta para el
presupuesto de rastreo y para las Core Web Vitals.
"""

from __future__ import annotations

import gzip

import pytest

pytest.importorskip("scrapy")

import shared.database  # noqa: E402

from scrapy.http import HtmlResponse, Request  # noqa: E402

CUERPO = b"<html><head><title>t</title></head><body>" + b"<p>relleno</p>" * 200 + b"</body></html>"


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


def test_el_tamano_del_recurso_y_el_del_cable_no_se_confunden(spider):
    comprimido = len(gzip.compress(CUERPO))
    assert comprimido < len(CUERPO) / 4  # el caso que destapa el fallo
    respuesta = HtmlResponse(
        "https://x.com/p", status=200,
        headers={b"Content-Type": b"text/html", b"Content-Length": str(comprimido).encode()},
        body=CUERPO, request=Request("https://x.com/p", meta={"depth": 0}),
    )
    paginas = [i for i in spider.parse(respuesta) if type(i).__name__ == "PageItem"]
    assert len(paginas) == 1
    fila = paginas[0]
    assert fila["content_length"] == len(CUERPO)
    assert fila["transfer_size"] == comprimido


def test_sin_cabecera_de_longitud_se_usa_el_cuerpo(spider):
    """Respuesta troceada: no hay forma de saber los bytes del cable."""
    respuesta = HtmlResponse(
        "https://x.com/p", status=200, headers={b"Content-Type": b"text/html"},
        body=CUERPO, request=Request("https://x.com/p", meta={"depth": 0}),
    )
    fila = [i for i in spider.parse(respuesta) if type(i).__name__ == "PageItem"][0]
    assert fila["content_length"] == len(CUERPO)
    assert fila["transfer_size"] == len(CUERPO)
