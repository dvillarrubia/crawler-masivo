"""El spider guarda lo que el HTML declara de si mismo (decision 108).

Dos niveles, los dos ejecutando el codigo (decision 74): `SeoSpider.parse` con
respuestas hechas a mano, para los bytes y el render, y el arnes contra un
sitio local con la tuberia real, para ver que las columnas llegan a Postgres.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import uuid

import pytest

pytest.importorskip("scrapy")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")


@pytest.fixture
def spider():
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


def _meta(spider, cuerpo: bytes, cabeceras: dict | None = None, meta: dict | None = None):
    from scrapy.http import HtmlResponse, Request

    respuesta = HtmlResponse(
        "https://x.com/p", status=200,
        headers=cabeceras or {b"Content-Type": b"text/html; charset=utf-8"},
        body=cuerpo, request=Request("https://x.com/p", meta={"depth": 0, **(meta or {})}),
    )
    items = list(spider.parse(respuesta))
    return next(i for i in items if type(i).__name__ == "HtmlMetaItem")


PUENTE = (
    '<html lang="es"><head><meta charset="utf-8"><title>Redirigiendo</title>'
    '<meta name="viewport" content="width=1024">'
    '<link rel="canonical" href="/p">'
    '<meta property="og:image" content="img/og.jpg">'
    '<meta name="twitter:image" content="https://x.com/tw.jpg">'
    '<script>window.location.replace("/nueva");</script>'
    "</head><body><p>Un momento</p></body></html>"
).encode()


def test_guarda_las_columnas_sin_render(spider):
    m = _meta(spider, PUENTE, {b"Content-Type": b"text/html; charset=utf-8",
                               b"Content-Language": b"es-ES"})
    assert m["viewport"] == "width=1024"
    assert m["viewport_count"] == 1
    assert m["html_lang"] == "es"
    assert m["content_language"] == "es-ES"
    assert m["canonical_crudo"] == "/p"
    assert m["canonical_href"] == "https://x.com/p"
    assert m["og_image_forma"] == "relativa"
    assert m["twitter_image"] == "https://x.com/tw.jpg"
    assert m["redireccion_js"] == "https://x.com/nueva"
    assert m["charset_meta"] == "utf-8"
    assert m["charset_bom"] is None
    assert m["cuerpo_solo_ascii"] is True
    assert m["texto_mal_codificado"] is False


def test_bytes_latin1_servidos_como_utf8_son_texto_mal_codificado(spider):
    cuerpo = "<html><head><title>Canción</title></head><body><p>Pequeño</p></body></html>"
    m = _meta(spider, cuerpo.encode("latin-1"))
    assert m["texto_mal_codificado"] is True
    assert m["cuerpo_solo_ascii"] is False
    assert m["charset_meta"] == ""          # medido: no hay meta
    # Bien declarados, el mismo texto sale bien.
    m = _meta(spider, cuerpo.encode("latin-1"),
              {b"Content-Type": b"text/html; charset=iso-8859-1"})
    assert m["texto_mal_codificado"] is False


def test_con_render_lo_del_html_servido_no_se_mide(spider):
    m = _meta(spider, PUENTE, meta={"playwright": True})
    # Del DOM si: es lo que ve Google tras renderizar.
    assert m["viewport_count"] == 1 and m["html_lang"] == "es"
    # De los bytes servidos no: el DOM ya ejecuto el JavaScript y
    # scrapy-playwright vuelve a codificar el cuerpo.
    assert m["redireccion_js"] is None
    assert m["charset_meta"] is None
    assert m["charset_bom"] is None
    assert m["cuerpo_solo_ascii"] is None
    assert m["texto_mal_codificado"] is None


def test_meta_refresh_a_javascript_no_se_sigue(spider):
    """B3 de #150: `url=javascript:void(0)` se tomaba como redireccion."""
    from scrapy.http import HtmlResponse, Request

    cuerpo = (b'<html><head><title>t</title><meta http-equiv="refresh" '
              b'content="0; url=javascript:void(0)"></head><body></body></html>')
    respuesta = HtmlResponse("https://x.com/p", status=200,
                             headers={b"Content-Type": b"text/html"}, body=cuerpo,
                             request=Request("https://x.com/p", meta={"depth": 0}))
    items = list(spider.parse(respuesta))
    pagina = next(i for i in items if type(i).__name__ == "PageItem")
    assert pagina["redirect_url"] is None
    assert pagina["indexability_status"] != "Redirect (meta refresh)"
    assert not any(type(i).__name__ == "Request" and "javascript" in i.url for i in items)


# -- Con la tuberia real: llega a Postgres -------------------------------------

SITIO = {
    "/home": [200, {"Content-Language": "es"}, (
        '<html lang="es-ES"><head><title>Home</title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="canonical" href="/home#inicio">'
        "</head><body><a href=\"/puente\">p</a></body></html>")],
    "/puente": [200, {}, (
        "<html><head><title>Puente</title>"
        '<script>location.href = "/destino"</script>'
        "</head><body>...</body></html>")],
}


@pytest.fixture()
def bd():
    if not URL_BD:
        pytest.skip("sin PAGERANK_TEST_DATABASE_URL")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from shared.models import Base, Job

    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="test declaraciones", status="running", seeds=[], config={}))
        s.commit()
    yield Sesion, jid
    with engine.begin() as c:
        c.execute(text("DELETE FROM html_meta WHERE url_id IN "
                       "(SELECT id FROM urls WHERE job_id = :j)"), {"j": str(jid)})
        for tabla in ("links", "issues", "urls", "jobs"):
            col = "id" if tabla == "jobs" else "job_id"
            c.execute(text(f"DELETE FROM {tabla} WHERE {col} = :j"), {"j": str(jid)})
    engine.dispose()


def test_las_columnas_llegan_a_la_bd(bd):
    from shared.models import HtmlMeta, Url

    Sesion, jid = bd
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        puerto = so.getsockname()[1]
    entrada = {"seeds": ["http://127.0.0.1:{port}/home"],
               "config": {"max_depth": 2, "use_sitemap": False}, "rutas": SITIO,
               "bd": URL_BD, "job_id": str(jid), "puerto": puerto}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    with Sesion() as s:
        filas = {u.path: m for u, m in s.query(Url, HtmlMeta)
                 .join(HtmlMeta, HtmlMeta.url_id == Url.id).filter(Url.job_id == jid)}
    home, puente = filas["/home"], filas["/puente"]
    assert home.viewport == "width=device-width, initial-scale=1"
    assert home.viewport_count == 1
    assert home.html_lang == "es-ES"
    assert home.content_language == "es"
    assert home.canonical_crudo == "/home#inicio"
    assert home.charset_meta == ""
    assert home.texto_mal_codificado is False
    assert home.redireccion_js is None
    assert puente.viewport_count == 0
    assert puente.html_lang is None
    assert puente.redireccion_js == f"http://127.0.0.1:{puerto}/destino"
    # No se sigue: el destino no es un enlace ni gasta max_urls.
    assert "/destino" not in filas
