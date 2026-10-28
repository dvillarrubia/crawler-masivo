"""Un enlace ya pedido desde otra pagina no vuelve a crear su Request (#284,
punto 7). El dupefilter la tiraba igual, pero despues de crearla y pasarla por
los middlewares y el motor: con el menu y el pie, ~200 por pagina."""

from __future__ import annotations

import pytest

pytest.importorskip("scrapy")


def _spider():
    import shared.database

    shared.database.SessionLocal = lambda: None
    from scrapy.utils.test import get_crawler

    from seo_crawler.spiders.seo_spider import SeoSpider

    sp = SeoSpider(job_id="t")
    sp.crawler = get_crawler(settings_dict={"DEPTH_PRIORITY": 1})
    sp._allowed_resource_types = {"html", "redirect"}
    sp._crawled_count = 0
    sp.allowed_hosts = {"x.com"}
    sp._crawl_subdomains = False
    sp.max_urls = None
    sp.max_depth = 10
    sp._max_url_length = 0
    sp._max_folder_depth = 0
    sp._follow_nofollow = False
    sp.render_js = False
    sp._extraction = {"extract_page_content": False}
    return sp


def _pedidas(sp, ruta, enlaces):
    from scrapy.http import HtmlResponse, Request

    cuerpo = "<html><body><nav>" + "".join(
        f'<a href="{e}">{e}</a>' for e in enlaces) + "</nav><p>texto</p></body></html>"
    url = f"https://x.com{ruta}"
    r = HtmlResponse(url, status=200, body=cuerpo.encode(),
                     headers={b"Content-Type": b"text/html; charset=utf-8"},
                     request=Request(url, meta={"depth": 1, "download_latency": 0.01}))
    return [x.url for x in sp.parse(r) if isinstance(x, Request)]


def test_lo_ya_pedido_no_se_vuelve_a_pedir():
    sp = _spider()
    menu = ["/a", "/b", "/c?x=1", "/d#ancla"]
    primera = _pedidas(sp, "/uno", menu)
    assert sorted(primera) == ["https://x.com/a", "https://x.com/b",
                               "https://x.com/c?x=1", "https://x.com/d"]
    # La misma URL escrita de otra forma que normaliza igual tampoco.
    segunda = _pedidas(sp, "/dos", menu + ["/nueva", "/a#otra", "https://X.com/b"])
    assert segunda == ["https://x.com/nueva"]


def test_coincide_con_lo_que_deja_pasar_el_dupefilter():
    """Lo que sale del spider es exactamente lo que el dupefilter de Scrapy
    habria dejado pasar de las Request de antes."""
    from scrapy import Request
    from scrapy.dupefilters import RFPDupeFilter

    paginas = [("/p1", ["/a", "/b", "/c"]), ("/p2", ["/b", "/c?y=2&x=1", "/d"]),
               ("/p3", ["/c?x=1&y=2", "/a", "/e/../f"])]
    sp = _spider()
    ahora = [u for ruta, enl in paginas for u in _pedidas(sp, ruta, enl)]

    sp = _spider()
    sp._enlaces_pedidos.anadir = lambda clave: True  # como antes: todas
    df = RFPDupeFilter()
    antes = [u for ruta, enl in paginas for u in _pedidas(sp, ruta, enl)
             if not df.request_seen(Request(u))]
    assert ahora == antes
