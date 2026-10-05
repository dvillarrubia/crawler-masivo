"""La indexabilidad se decide sobre la URL que se GUARDA, no sobre la de partida.

El fallo que fija: el spider guardaba la ultima URL de la cadena de
redirecciones pero comparaba el canonical contra la primera, asi que cualquier
pagina alcanzada por un 301 salia "Canonicalised" —no indexable— con un
canonical que apuntaba a si misma. Medido antes del arreglo: 1.254 paginas de
34.704 en un rastreo y 1.329 de 28.712 en otro, el 100% alcanzadas por
redireccion.
"""

from __future__ import annotations

import pytest

pytest.importorskip("scrapy")

from scrapy.http import HtmlResponse, Request  # noqa: E402

from seo_crawler.items import PageItem  # noqa: E402
from seo_crawler.spiders.seo_spider import SeoSpider  # noqa: E402

HTML = """<html><head><title>Una pagina</title>
<link rel="canonical" href="https://e.com/final"/></head>
<body><h1>Hola</h1><p>Texto suficiente para la pagina.</p></body></html>"""


def _spider():
    """Spider sin __init__: el de verdad abre Redis y base de datos."""
    s = SeoSpider.__new__(SeoSpider)
    s.job_id = "00000000-0000-0000-0000-000000000000"
    s.max_urls = None
    s.max_depth = 0
    s._crawled_count = 0
    s._redis = None
    s.allowed_hosts = {"e.com"}
    s._allowed_resource_types = {"html", "redirect"}
    s._extraction = {}
    s._crawl_subdomains = False
    s.follow_external = False
    s._follow_nofollow = False
    s._should_cancel = lambda: False
    s._update_redis_progress = lambda: None
    s._is_internal = lambda url: "e.com" in url
    s._should_follow = lambda url: False
    return s


def _respuesta(con_redireccion: bool):
    meta = {"depth": 0}
    if con_redireccion:
        meta["redirect_urls"] = ["https://e.com/vieja"]
        meta["redirect_reasons"] = [301]
    req = Request("https://e.com/vieja", meta=meta)
    return HtmlResponse(
        url="https://e.com/final", body=HTML.encode(), encoding="utf-8",
        request=req, status=200, headers={"Content-Type": "text/html; charset=utf-8"},
    )


def _paginas(spider, respuesta):
    return [i for i in spider.parse(respuesta) if isinstance(i, PageItem)]


def test_canonical_propio_tras_redireccion_es_indexable():
    paginas = _paginas(_spider(), _respuesta(con_redireccion=True))
    final = [p for p in paginas if p["url"] == "https://e.com/final"]
    assert len(final) == 1
    assert final[0]["indexability_status"] == "Indexable"


def test_el_salto_se_guarda_aparte_y_sin_datos_inventados():
    paginas = _paginas(_spider(), _respuesta(con_redireccion=True))
    salto = [p for p in paginas if p["url"] == "https://e.com/vieja"]
    assert len(salto) == 1
    assert salto[0]["status_code"] == 301
    assert salto[0]["redirect_url"] == "https://e.com/final"
    # Un 301 no tiene cuerpo: ni content-type heredado del destino ni ceros
    # que se lean como "0 bytes medidos".
    assert salto[0]["content_type"] is None
    assert salto[0]["content_length"] is None
    assert salto[0]["transfer_size"] is None


def test_sin_redireccion_sigue_funcionando_igual():
    paginas = _paginas(_spider(), _respuesta(con_redireccion=False))
    assert len(paginas) == 1
    assert paginas[0]["indexability_status"] == "Indexable"


def test_canonical_a_otra_url_si_es_canonicalised():
    html = HTML.replace("https://e.com/final", "https://e.com/otra")
    req = Request("https://e.com/vieja", meta={
        "depth": 0, "redirect_urls": ["https://e.com/vieja"], "redirect_reasons": [301],
    })
    resp = HtmlResponse(url="https://e.com/final", body=html.encode(),
                        encoding="utf-8", request=req, status=200,
                        headers={"Content-Type": "text/html; charset=utf-8"})
    final = [p for p in _paginas(_spider(), resp) if p["url"] == "https://e.com/final"]
    assert final[0]["indexability_status"] == "Canonicalised"
