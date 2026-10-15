"""HTML que Google trunca o ignora (decision 102), EJECUTANDO el check.

`analyze_html_ignorado` contra SQLite, y el spider de verdad para ver que
guarda las columnas de las que vive (decision 74: leer el fuente no vale).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.config import GOOGLEBOT_HTML_MAX_BYTES
from shared.models import Base, Hreflang, HtmlMeta, Issue, Job, Url

MB2 = GOOGLEBOT_HTML_MAX_BYTES


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _censo(render_js: bool = False):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, HtmlMeta.__table__,
        Hreflang.__table__,
    ])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing",
            seeds=["https://x.com/"], config={"render_js": render_js})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, *, status=200, indexable=True, interna=True, meta=None, **cols):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com",
            is_internal=interna, is_html=True, status_code=status,
            indexable=indexable, **cols)
    s.add(u)
    s.flush()
    if meta is not None:
        s.add(HtmlMeta(url_id=u.id, **meta))
        s.flush()
    return u


def _issues(s, j, tipo):
    return s.execute(
        select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
        .where(Issue.job_id == j.id, Issue.issue_type == tipo)
    ).all()


# -- html_supera_2mb ----------------------------------------------------------

def test_supera_2mb_solo_indexables_y_con_el_tamano_servido():
    s, j = _censo()
    grande = _url(s, j, "/grande", html_bytes_servidos=MB2 + 1000)
    _url(s, j, "/justo", html_bytes_servidos=MB2)                 # no pasa
    _url(s, j, "/noindex", indexable=False, html_bytes_servidos=MB2 * 3)
    _url(s, j, "/404", status=404, html_bytes_servidos=MB2 * 3)
    _url(s, j, "/externa", interna=False, html_bytes_servidos=MB2 * 3)
    SEOAnalyzer(s, j.id).analyze_html_ignorado()

    filas = _issues(s, j, "html_supera_2mb")
    assert [f.url_id for f in filas] == [grande.id]
    detalle = filas[0].details
    assert detalle["bytes_sin_leer"] == 1000
    assert detalle["fuente"] == "html_bytes_servidos"
    assert filas[0].group_key is None  # uno por URL (decision 88)


def test_censo_viejo_sin_render_usa_el_mayor_de_los_dos_tamanos():
    """Antes del 2026-10-07 el descomprimido estaba en `transfer_size`
    (decision 50); despues, en `content_length`. El mayor vale en las dos."""
    s, j = _censo()
    antes = _url(s, j, "/antes", content_length=40_000, transfer_size=MB2 + 1)
    despues = _url(s, j, "/despues", content_length=MB2 + 1, transfer_size=40_000)
    _url(s, j, "/pequena", content_length=900_000, transfer_size=150_000)
    SEOAnalyzer(s, j.id).analyze_html_ignorado()

    assert sorted(f.url_id for f in _issues(s, j, "html_supera_2mb")) == sorted(
        [antes.id, despues.id])


def test_con_render_y_sin_tamano_servido_no_se_afirma_nada(caplog):
    """Con render `content_length` es el DOM montado. Medido en Druni: 5
    paginas con 2,2-3,9 MB de DOM sirven hoy 1,5-2,2 MB de HTML."""
    s, j = _censo(render_js=True)
    _url(s, j, "/dom-grande", content_length=MB2 * 2, transfer_size=MB2 * 2)
    medida = _url(s, j, "/medida", content_length=MB2 * 2, html_bytes_servidos=MB2 + 5)
    with caplog.at_level("WARNING"):
        SEOAnalyzer(s, j.id).analyze_html_ignorado()

    assert [f.url_id for f in _issues(s, j, "html_supera_2mb")] == [medida.id]
    assert "1 paginas indexables sin el tamano" in caplog.text


# -- head_cortado y meta_fuera_del_head --------------------------------------

def test_head_cortado_agrupa_por_corte_y_perdidos():
    s, j = _censo()
    a = _url(s, j, "/a", meta={"head_cortado_por": "img", "fuera_del_head": "robots,title"})
    _url(s, j, "/b", meta={"head_cortado_por": "img", "fuera_del_head": "robots,title"})
    c = _url(s, j, "/c", meta={"head_cortado_por": "div", "fuera_del_head": "canonical"})
    # Cortado sin perder nada: Google no deja de leer nada que importe.
    _url(s, j, "/d", meta={"head_cortado_por": "div", "fuera_del_head": ""})
    # Censo anterior a la columna: no se sabe.
    _url(s, j, "/e", meta={"head_cortado_por": None, "fuera_del_head": None})
    SEOAnalyzer(s, j.id).analyze_html_ignorado()

    filas = sorted(_issues(s, j, "head_cortado"), key=lambda f: f.url_id)
    assert [f.url_id for f in filas] == [a.id, c.id]
    assert filas[0].details["paginas_afectadas"] == 2
    assert filas[0].details["fuera_del_head"] == ["robots", "title"]
    assert filas[0].details["cortado_por"] == "img"
    assert len(filas[0].details["paginas_ejemplo"]) == 2
    assert filas[0].group_key and filas[0].group_key != filas[1].group_key
    assert _issues(s, j, "meta_fuera_del_head") == []


def test_la_severidad_del_corte_sigue_a_lo_que_se_pierde():
    """Decision 64: robots, googlebot o canonical detras del corte deciden el
    indice (error); titulo, description o hreflang, no (warning)."""
    s, j = _censo()
    robots = _url(s, j, "/r", meta={"head_cortado_por": "img", "fuera_del_head": "robots,title"})
    gbot = _url(s, j, "/g", meta={"head_cortado_por": "div", "fuera_del_head": "googlebot"})
    titulo = _url(s, j, "/t", meta={"head_cortado_por": "img", "fuera_del_head": "description,title"})
    SEOAnalyzer(s, j.id).analyze_html_ignorado()

    filas = {f.url_id: f for f in _issues(s, j, "head_cortado")}
    assert filas[robots.id].severity == "error"
    assert filas[robots.id].details["directiva_en_duda"] is True
    assert filas[gbot.id].severity == "error"
    assert filas[titulo.id].severity == "warning"
    assert filas[titulo.id].details["directiva_en_duda"] is False


def test_con_render_no_se_afirma_nada_del_head():
    """Con render el DOM ya no conserva el corte y lo movido al body saldria
    como `meta_fuera_del_head` con la causa equivocada: no se emite ninguno de
    los dos, aunque un censo viejo trajera las columnas."""
    s, j = _censo(render_js=True)
    _url(s, j, "/a", meta={"head_cortado_por": None, "fuera_del_head": "robots,title"})
    _url(s, j, "/b", meta={"head_cortado_por": "img", "fuera_del_head": "robots"})
    SEOAnalyzer(s, j.id).analyze_html_ignorado()
    assert _issues(s, j, "meta_fuera_del_head") == []
    assert _issues(s, j, "head_cortado") == []


def test_meta_fuera_del_head_sin_corte_y_sin_repetir_otros_avisos():
    s, j = _censo()
    robots = _url(s, j, "/robots", meta={"fuera_del_head": "robots", "title_count": 1})
    # Canonical y hreflang en el body ya tienen su aviso propio.
    _url(s, j, "/canon", meta={"fuera_del_head": "canonical,hreflang"})
    # Un segundo <title> en el body con el bueno en el head es title_multiple.
    _url(s, j, "/epub", meta={"fuera_del_head": "title", "title_count": 2})
    solo = _url(s, j, "/solo", meta={"fuera_del_head": "title", "title_count": 1})
    SEOAnalyzer(s, j.id).analyze_html_ignorado()

    filas = {f.url_id: f for f in _issues(s, j, "meta_fuera_del_head")}
    assert set(filas) == {robots.id, solo.id}
    assert filas[robots.id].details["fuera_del_head"] == ["robots"]
    assert filas[solo.id].details["fuera_del_head"] == ["title"]
    # Informativo: Next.js 15.2+ los sirve ahi a proposito (streaming metadata).
    assert {f.severity for f in filas.values()} == {"info"}


def test_el_corte_se_lleva_canonical_in_body_y_hreflang_in_body():
    """Un aviso por hallazgo (36): si el canonical esta en el body porque un
    <img> corto el head, el hallazgo es el corte."""
    s, j = _censo()
    cortada = _url(s, j, "/cortada", meta={
        "canonical_href": "https://x.com/cortada", "canonical_in_body": True,
        "head_cortado_por": "img", "fuera_del_head": "canonical,hreflang"})
    puesta = _url(s, j, "/puesta", meta={
        "canonical_href": "https://x.com/puesta", "canonical_in_body": True,
        "head_cortado_por": None, "fuera_del_head": "canonical,hreflang"})
    for u in (cortada, puesta):
        s.add(Hreflang(url_id=u.id, lang="en", href="https://x.com/en", en_body=True))
    s.flush()
    an = SEOAnalyzer(s, j.id)
    an.analyze_canonicals()
    an.analyze_hreflang()
    an.analyze_html_ignorado()

    assert [f.url_id for f in _issues(s, j, "canonical_in_body")] == [puesta.id]
    assert [f.url_id for f in _issues(s, j, "hreflang_in_body")] == [puesta.id]
    assert [f.url_id for f in _issues(s, j, "head_cortado")] == [cortada.id]


# -- El spider guarda lo que el check lee ------------------------------------

@pytest.fixture
def spider():
    pytest.importorskip("scrapy")
    import shared.database

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


def _parse(spider, cuerpo: bytes, meta: dict | None = None):
    from scrapy.http import HtmlResponse, Request

    respuesta = HtmlResponse(
        "https://x.com/p", status=200, headers={b"Content-Type": b"text/html"},
        body=cuerpo, request=Request("https://x.com/p", meta={"depth": 0, **(meta or {})}),
    )
    items = list(spider.parse(respuesta))
    pagina = next(i for i in items if type(i).__name__ == "PageItem")
    meta_item = next(i for i in items if type(i).__name__ == "HtmlMetaItem")
    return pagina, meta_item


ROTO = (b"<!doctype html><html><head><title>T</title><img src=/x.png>"
        b"<meta name=robots content=noindex></head><body><p>x</p></body></html>")


def test_spider_guarda_el_corte_y_el_tamano(spider):
    pagina, meta = _parse(spider, ROTO)
    assert pagina["html_bytes_servidos"] == len(ROTO)
    assert meta["head_cortado_por"] == "img"
    assert meta["fuera_del_head"] == "robots"

    sano = b"<html><head><title>T</title></head><body><p>x</p></body></html>"
    _pagina, meta = _parse(spider, sano)
    assert meta["head_cortado_por"] is None
    assert meta["fuera_del_head"] == ""  # medido y vacio, no NULL


def test_spider_con_render_toma_el_tamano_del_navegador(spider):
    from scrapy_playwright.page import PageMethod

    from seo_crawler.spiders import seo_spider as mod

    def con_resultado(valor):
        metodo = PageMethod(mod._evaluar_tolerante, mod._JS_BYTES_DOCUMENTO)
        metodo.result = valor
        return {"playwright": True, "playwright_page_methods": [metodo]}

    pagina, meta = _parse(spider, ROTO, con_resultado(3_400_070))
    assert pagina["html_bytes_servidos"] == 3_400_070
    # Con render el head no se mide: el DOM serializado no conserva el corte.
    assert meta["head_cortado_por"] is None
    assert meta["fuera_del_head"] is None
    # Si el navegador no lo dio, no se sabe: nunca el tamano del DOM.
    pagina, _meta = _parse(spider, ROTO, con_resultado(None))
    assert pagina["html_bytes_servidos"] is None


def test_navigation_timing_da_el_html_servido_descomprimido():
    """El JS de verdad contra Chromium: un HTML gzip de 3,4 MB, con la
    interceptacion de peticiones que pone scrapy-playwright, con y sin
    Content-Length. Lo que tiene que salir es el descomprimido."""
    import asyncio
    import gzip
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    pw = pytest.importorskip("playwright.async_api")
    from seo_crawler.spiders.seo_spider import _JS_BYTES_DOCUMENTO

    cuerpo = (b"<!doctype html><html><head><title>T</title></head><body>"
              + b"<p>hola mundo</p>" * 200_000 + b"</body></html>")
    comprimido = gzip.compress(cuerpo)

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Encoding", "gzip")
            if self.path != "/troceado":
                self.send_header("Content-Length", str(len(comprimido)))
            self.end_headers()
            self.wfile.write(comprimido)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    async def correr():
        async with pw.async_playwright() as p:
            try:
                navegador = await p.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await navegador.new_page()
            await pagina.route("**/*", lambda r: r.continue_())
            vistos = []
            for ruta in ("/", "/troceado"):
                await pagina.goto(base + ruta)
                vistos.append(await pagina.evaluate(_JS_BYTES_DOCUMENTO))
            await navegador.close()
            return vistos

    try:
        assert asyncio.run(correr()) == [len(cuerpo), len(cuerpo)]
    finally:
        srv.shutdown()
