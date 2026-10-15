"""El spider ante un muro, llamando a `parse` y a `spider_opened` de verdad
(decision 103): con render manda el cuerpo, la salud es la del sitio que se
audita, y al reanudar se repiten los muros.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("scrapy")

import shared.database  # noqa: E402

from scrapy.http import HtmlResponse, Request  # noqa: E402

PAGINA_REAL = (b"<html><head><title>Maquinas de cardio | Progym</title></head><body>"
               b"<h1>Maquinas de cardio</h1><p>" + b"Texto de la ficha de verdad. " * 200
               + b"</p></body></html>")
DESAFIO = (b"<html><head><title>Just a moment...</title></head><body>"
           b"<script>window._cf_chl_opt={cvId:'3'}</script></body></html>")
CF = {b"Content-Type": b"text/html; charset=utf-8", b"cf-mitigated": b"challenge"}


@pytest.fixture
def spider(monkeypatch):
    monkeypatch.setattr(shared.database, "SessionLocal", lambda: None, raising=False)
    from seo_crawler.spiders.seo_spider import SeoSpider

    sp = SeoSpider(job_id="t")
    sp._allowed_resource_types = {"html", "redirect"}
    sp._crawled_count = 0
    sp.allowed_hosts = {"x.com"}
    sp._crawl_subdomains = False
    sp.max_urls = None
    sp._extraction = {"extract_page_content": False}
    return sp


def _pagina(spider, url, cuerpo, cabeceras=CF, status=403, **meta):
    r = HtmlResponse(url, status=status, headers=cabeceras, body=cuerpo,
                     request=Request(url, meta={"depth": 1, **meta}))
    return [i for i in spider.parse(r) if type(i).__name__ == "PageItem"][-1]


def test_sin_render_la_cabecera_del_desafio_manda(spider):
    assert _pagina(spider, "https://x.com/a", PAGINA_REAL)["response_class"] == "challenge_waf"


def test_con_render_un_desafio_resuelto_es_la_pagina(spider):
    """scrapy-playwright da el 403 y el `cf-mitigated` de la primera respuesta de
    `page.goto` y el cuerpo del DOM final: si Chromium paso el desafio, el
    cuerpo es la pagina."""
    fila = _pagina(spider, "https://x.com/a", PAGINA_REAL, playwright=True)
    assert fila["response_class"] == "contenido"
    assert fila["indexability_status"] != "Bloqueada (WAF)"


def test_con_render_un_desafio_sin_resolver_sigue_siendo_muro(spider):
    fila = _pagina(spider, "https://x.com/a", DESAFIO, playwright=True)
    assert fila["response_class"] == "challenge_waf"


def test_los_muros_de_un_host_externo_no_cuentan_para_la_salud(spider):
    spider.follow_external = True
    for i in range(5):
        _pagina(spider, f"https://twitter.com/p{i}", DESAFIO)
    assert sum(spider._salud.totales.values()) == 0
    _pagina(spider, "https://x.com/a", DESAFIO)
    assert spider._salud.totales == {"challenge_waf": 1}


# --- Reanudar repite los muros ------------------------------------------------

@pytest.fixture
def bd(monkeypatch, tmp_path):
    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    from shared.models import Base

    @compiles(BigInteger, "sqlite")
    def _bigint(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion, raising=False)
    return Sesion


def test_al_reanudar_se_repiten_los_muros_y_no_las_paginas(bd):
    from scrapy.settings import Settings

    from seo_crawler.spiders.seo_spider import SeoSpider
    from shared.models import Job, Url
    from seo_crawler.extractors import compute_url_hash, normalize_url

    s = bd()
    jid = uuid.uuid4()
    s.add(Job(id=jid, name="t", status="running", seeds=["https://x.com/"], config={}))
    filas = {
        "https://x.com/buena": (200, "contenido"),
        "https://x.com/vieja": (200, None),  # censo anterior a la columna
        "https://x.com/muro": (403, "challenge_waf"),
        "https://x.com/limitada": (429, "rate_limited"),
        "https://x.com/binaria": (200, "respuesta_binaria"),
        "https://x.com/login": (401, "login_requerido"),
    }
    for url, (codigo, clase) in filas.items():
        s.add(Url(job_id=jid, url=url, url_hash=compute_url_hash(normalize_url(url)),
                  is_internal=True, is_html=True, crawl_depth=1,
                  status_code=codigo, response_class=clase))
    s.commit()
    s.close()

    # Un UUID y no su texto: el tipo UUID de SQLAlchemy en SQLite lo exige.
    sp = SeoSpider(job_id=jid)
    sp.settings = Settings({"REDIS_URL": "redis://127.0.0.1:1/0"})
    sp.spider_opened(sp)

    repetir = {u for u, _ in sp._reintentar}
    assert repetir == {"https://x.com/muro", "https://x.com/limitada", "https://x.com/binaria"}
    # Lo que no se repite cuenta como ya rastreado.
    assert len(sp._already_crawled_hashes) == 3
