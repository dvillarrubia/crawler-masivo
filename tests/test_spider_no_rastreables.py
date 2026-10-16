"""Enlaces no rastreables con el SeoSpider REAL contra un sitio local (decision 106).

Lo que importa no es que se detecten —eso lo cubre el extractor— sino lo que NO
pasa: que el destino de un `<span href>` o de un `javascript:` no se rastrea (el
buscador tampoco llega por ahi) y que no entra en `links`. Y, con la tuberia
real contra Postgres, que se guarda en su tabla sin duplicarse al re-rastrear.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pytest

pytest.importorskip("scrapy")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")


def _pagina(cuerpo: str) -> list:
    return [200, {}, f"<html><head><title>T</title></head><body>{cuerpo}</body></html>"]


SITIO = {
    "/home": _pagina('<a href="/visible">Visible</a>'
                     '<span href="/oculta-span">Span</span>'
                     "<a href=\"javascript:location.href='/oculta-js'\">JS</a>"
                     "<div onclick=\"location='/oculta-card'\">Tarjeta</div>"),
    "/visible": _pagina("<p>ok</p>"),
    "/oculta-span": _pagina("<p>no deberia rastrearse</p>"),
    "/oculta-js": _pagina("<p>no deberia rastrearse</p>"),
    "/oculta-card": _pagina("<p>no deberia rastrearse</p>"),
}
CONFIG = {"max_depth": 3, "use_sitemap": False}


def _rastrear(**extra):
    entrada = {"seeds": ["http://127.0.0.1:{port}/home"], "config": CONFIG, "rutas": SITIO,
               **extra}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    return json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])


def test_lo_no_rastreable_se_emite_y_no_se_sigue_ni_es_arista():
    items = _rastrear()
    rastreadas = {"/" + i["url"].split("/", 3)[-1] for i in items if i["_tipo"] == "PageItem"}
    assert rastreadas == {"/home", "/visible"}
    destinos_links = {i["to_url"].split("/", 3)[-1] for i in items if i["_tipo"] == "LinkItem"}
    assert destinos_links == {"visible"}
    enr = [i for i in items if i["_tipo"] == "EnlacesNoRastreablesItem"]
    filas = [f for i in enr for f in i["filas"]]
    assert sorted((f["crawlability"], f["destino"].rsplit("/", 1)[-1]) for f in filas) == [
        ("href_fuera_de_a", "oculta-span"), ("javascript_href", "oculta-js"),
        ("onclick_en_otra_etiqueta", "oculta-card")]
    assert all(f["destino_interno"] for f in filas)


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
        s.add(Job(id=jid, name="test no rastreables", status="running", seeds=[], config={}))
        s.commit()
    yield Sesion, jid
    with engine.begin() as c:
        for tabla in ("enlaces_no_rastreables", "links", "issues", "urls", "jobs"):
            col = "id" if tabla == "jobs" else "job_id"
            c.execute(text(f"DELETE FROM {tabla} WHERE {col} = :j"), {"j": str(jid)})
    engine.dispose()


def test_se_guarda_en_su_tabla_y_re_rastrear_no_duplica(bd):
    from shared.models import EnlaceNoRastreable, Link

    import socket

    Sesion, jid = bd
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        puerto = so.getsockname()[1]
    # El mismo puerto las dos veces: si no, son otras URLs y no un re-rastreo.
    for _ in range(2):
        _rastrear(bd=URL_BD, job_id=str(jid), puerto=puerto)
    with Sesion() as s:
        filas = s.query(EnlaceNoRastreable).filter(EnlaceNoRastreable.job_id == jid).all()
        assert sorted(f.crawlability for f in filas) == [
            "href_fuera_de_a", "javascript_href", "onclick_en_otra_etiqueta"]
        assert not s.query(Link).filter(Link.job_id == jid,
                                        Link.to_url.like("%oculta%")).count()
