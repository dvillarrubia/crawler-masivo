"""El spider en un job incremental (decision 220), con `spider_opened` y
`parse` de verdad sobre SQLite: que no pide lo que no toca, que pide la
frontera condicional y con su profundidad, y que un 304 es una fila sin hijos.
"""

from __future__ import annotations

import types
import uuid

import pytest

pytest.importorskip("scrapy")

import shared.database  # noqa: E402

from scrapy.http import HtmlResponse, Request, Response  # noqa: E402
from scrapy.settings import Settings  # noqa: E402

BASE = "https://x.com"


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


def _h(ruta):
    from seo_crawler.extractors import compute_url_hash, normalize_url

    return compute_url_hash(normalize_url(BASE + ruta))


def _preparar(bd, *, render=False):
    """Censo anterior con /, /a, /b, /c; tocan / y /a (con etag); /b y /c no."""
    from shared.models import Job, JobFrontera, Url

    s = bd()
    previo, actual = uuid.uuid4(), uuid.uuid4()
    s.add(Job(id=previo, name="p", status="completed", seeds=[BASE + "/"], config={}))
    s.add(Job(id=actual, name="a", status="running", seeds=[BASE + "/"],
              config={"render_js": render, "max_depth": 5},
              incremental={"modo": "incremental", "prev_job_id": str(previo), "ciclo": 2}))
    for ruta, prof in (("/", 0), ("/a", 1), ("/b", 1), ("/c", 2)):
        s.add(Url(job_id=previo, url=BASE + ruta, url_hash=_h(ruta), is_internal=True,
                  is_html=True, crawl_depth=prof, status_code=200))
    s.add(JobFrontera(job_id=actual, url_hash=_h("/"), url=BASE + "/", profundidad=0,
                      motivo="fijo"))
    s.add(JobFrontera(job_id=actual, url_hash=_h("/a"), url=BASE + "/a", profundidad=3,
                      etag='"a1"', last_modified="Mon, 26 Oct 2026 10:00:00 GMT",
                      motivo="toca"))
    s.commit()
    s.close()
    return previo, actual


def _spider(actual):
    from seo_crawler.spiders.seo_spider import SeoSpider

    sp = SeoSpider(job_id=actual)
    sp.settings = Settings({"REDIS_URL": "redis://127.0.0.1:1/0"})
    sp.crawler = types.SimpleNamespace(settings=Settings({"DEPTH_PRIORITY": 1}))
    sp.spider_opened(sp)
    return sp


def test_lo_que_no_toca_no_se_pide(bd):
    from seo_crawler.spiders.seo_spider import _hash_key

    _previo, actual = _preparar(bd)
    sp = _spider(actual)
    assert sp._incremental is not None
    # No es una reanudacion: este job no tiene filas.
    assert not sp._resume_mode and sp._crawled_count == 0
    for ruta in ("/b", "/c"):
        assert _hash_key(_h(ruta)) in sp._already_crawled_hashes
    for ruta in ("/", "/a"):
        assert _hash_key(_h(ruta)) not in sp._already_crawled_hashes


def test_la_frontera_va_condicional_y_con_su_profundidad(bd):
    _previo, actual = _preparar(bd)
    sp = _spider(actual)
    peticiones = {r.url: r for r in sp._peticiones_de_frontera()}
    assert set(peticiones) == {BASE + "/", BASE + "/a"}
    a = peticiones[BASE + "/a"]
    assert a.meta["depth"] == 3 and a.meta["condicional"] is True
    assert a.headers[b"If-None-Match"] == b'"a1"'
    assert a.headers[b"If-Modified-Since"] == b"Mon, 26 Oct 2026 10:00:00 GMT"
    # Sin etag ni fecha no hay nada que condicionar.
    assert "condicional" not in peticiones[BASE + "/"].meta
    assert b"If-None-Match" not in peticiones[BASE + "/"].headers


def test_con_render_no_hay_peticiones_condicionales(bd):
    _previo, actual = _preparar(bd, render=True)
    sp = _spider(actual)
    a = {r.url: r for r in sp._peticiones_de_frontera()}[BASE + "/a"]
    assert a.meta.get("playwright")
    assert b"If-None-Match" not in a.headers and "condicional" not in a.meta


def test_un_304_es_una_fila_sin_hijos(bd):
    _previo, actual = _preparar(bd)
    sp = _spider(actual)
    sp._allowed_resource_types = {"html", "redirect"}
    req = Request(BASE + "/a", meta={"depth": 3, "condicional": True})
    resp = Response(BASE + "/a", status=304, headers={b"ETag": b'"a1"'}, request=req)
    items = list(sp.parse(resp))
    assert [type(i).__name__ for i in items] == ["PageItem"]
    fila = items[0]
    assert (fila["fetch_mode"], fila["status_code"], fila["etag"], fila["crawl_depth"]) == \
        ("not_modified", 304, '"a1"', 3)
    assert sp._crawled_count == 1


def test_una_pagina_pedida_lleva_su_etag(bd):
    _previo, actual = _preparar(bd)
    sp = _spider(actual)
    sp._allowed_resource_types = {"html", "redirect"}
    sp.allowed_hosts = {"x.com"}
    sp._extraction = {"extract_page_content": False}
    req = Request(BASE + "/a", meta={"depth": 3, "condicional": True})
    resp = HtmlResponse(BASE + "/a", status=200, request=req,
                        headers={b"ETag": b'"a2"', b"Content-Type": b"text/html"},
                        body=b"<html><head><title>A</title></head><body><h1>A</h1></body></html>")
    fila = [i for i in sp.parse(resp) if type(i).__name__ == "PageItem"][-1]
    assert fila["status_code"] == 200 and fila["etag"] == '"a2"'


def test_un_304_sin_peticion_condicional_no_se_toma_por_no_modificada(bd):
    """Un 304 que nadie pidio (servidor roto) no se completa con nada: es una
    respuesta como otra, y el arrastre no la toca."""
    _previo, actual = _preparar(bd)
    sp = _spider(actual)
    sp._allowed_resource_types = {"html", "redirect"}
    sp.allowed_hosts = {"x.com"}
    sp._extraction = {"extract_page_content": False}
    req = Request(BASE + "/b", meta={"depth": 1})
    resp = HtmlResponse(BASE + "/b", status=304, request=req, body=b"")
    filas = [i for i in sp.parse(resp) if type(i).__name__ == "PageItem"]
    assert all(f.get("fetch_mode") is None for f in filas)


def test_reanudar_un_incremental_ya_arrastrado_no_siembra_sus_enlaces(bd):
    from shared.models import Url

    _previo, actual = _preparar(bd)
    s = bd()
    s.add(Url(job_id=actual, url=BASE + "/", url_hash=_h("/"), is_internal=True,
              is_html=True, crawl_depth=0, status_code=200, fetch_mode="fetched"))
    s.add(Url(job_id=actual, url=BASE + "/b", url_hash=_h("/b"), is_internal=True,
              is_html=True, crawl_depth=1, status_code=200, fetch_mode="carried"))
    s.commit()
    s.close()
    sp = _spider(actual)
    assert sp._resume_mode and sp._con_arrastre
    # Lo arrastrado no cuenta como rastreado en este job.
    assert sp._crawled_count == 1
