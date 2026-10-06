"""Contrato del spider: cuando se guarda el HTML y cuando no."""
import pytest
pytest.importorskip("scrapy")
from scrapy.http import HtmlResponse, Request
from seo_crawler.items import ContentItem
from seo_crawler.spiders.seo_spider import SeoSpider

HTML_CON = b"<html><head><title>t</title></head><body><main><h1>H</h1><p>" + b"palabra " * 80 + b"</p></main></body></html>"
HTML_VACIO = b"<html><head><title>t</title></head><body><div></div></body></html>"

def _spider(extraction):
    s = SeoSpider.__new__(SeoSpider)
    s.job_id = "00000000-0000-0000-0000-000000000000"
    s.max_urls = None; s.max_depth = 0; s._crawled_count = 0; s._redis = None
    s.allowed_hosts = {"e.com"}; s._allowed_resource_types = {"html", "redirect"}
    s._extraction = extraction; s._crawl_subdomains = False
    s.follow_external = False; s._follow_nofollow = False
    s._seed_keys = set()
    s._should_cancel = lambda: False
    s._update_redis_progress = lambda: None
    s._is_internal = lambda url: "e.com" in url
    s._should_follow = lambda url: False
    return s

def _contenido(extraction, cuerpo):
    req = Request("https://e.com/a", meta={"depth": 0})
    resp = HtmlResponse(url="https://e.com/a", body=cuerpo, encoding="utf-8",
                        request=req, status=200,
                        headers={"Content-Type": "text/html; charset=utf-8"})
    return [i for i in _spider(extraction).parse(resp) if isinstance(i, ContentItem)]

def test_apagado_no_guarda_html():
    items = _contenido({}, HTML_CON)
    assert len(items) == 1
    assert items[0].get("raw_html") is None

def test_encendido_guarda_html():
    items = _contenido({"store_raw_html": True}, HTML_CON)
    assert len(items) == 1
    assert "<h1>H</h1>" in items[0]["raw_html"]

def test_apagado_y_sin_contenido_no_emite_fila():
    assert _contenido({}, HTML_VACIO) == []

def test_encendido_si_emite_fila_aunque_no_haya_contenido():
    """La pagina que sale con 0 palabras es la que hay que poder auditar."""
    items = _contenido({"store_raw_html": True}, HTML_VACIO)
    assert len(items) == 1
    assert items[0]["content_length"] == 0
    assert items[0]["raw_html"]
