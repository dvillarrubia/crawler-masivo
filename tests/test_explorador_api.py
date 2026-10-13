"""El Explorador contra la API, EJECUTADO con TestClient y SQLite
(docs/INVESTIGACION_EXPLORADOR.md, #131).

Lo que se sujeta: que el CSV de la vista exporte LO QUE SE VE (mismos filtros,
mismo orden, solo las columnas pedidas) y no el censo entero, que el modo
ligero no mande el texto de cada pagina, y los filtros nuevos: varios tipos de
problema, vacio / con valor, URL separada del buscador, en el sitemap y el
ancho en pixeles.
"""

from __future__ import annotations

import csv
import io
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import Base, HtmlMeta, Issue, Job, PageContent, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture()
def entorno(tmp_path):
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/e.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    job = Job(id=uuid.uuid4(), name="censo", status="completed", seeds=["https://x.com/"], config={})
    s.add(job)
    s.flush()

    def url(ruta, codigo=200, titulo=None, px=None, sitemap=True, inlinks=0):
        u = Url(job_id=job.id, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com", is_internal=True,
                is_html=True, status_code=codigo, status_group=f"{codigo // 100}xx",
                in_sitemap=sitemap, inlinks_count=inlinks)
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title=titulo, title_pixel_width=px))
        s.add(PageContent(url_id=u.id, content_text="texto largo " * 200))
        return u

    a = url("/a", titulo="Página A", px=300, inlinks=5)
    b = url("/b", titulo="Un título larguísimo", px=640, sitemap=False, inlinks=2)
    c = url("/c", titulo=None, px=None, inlinks=9)               # sin titulo
    d = url("/null", codigo=404, titulo="No encontrada", inlinks=3)
    s.add_all([
        Issue(job_id=job.id, url_id=b.id, issue_type="title_too_long", severity="warning"),
        Issue(job_id=job.id, url_id=c.id, issue_type="title_missing", severity="warning"),
        Issue(job_id=job.id, url_id=d.id, issue_type="4xx_error", severity="error"),
    ])
    s.commit()
    ids = {k: v.id for k, v in dict(a=a, b=b, c=c, d=d).items()}
    s.close()

    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), job.id, ids


def _urls(api, jid, q=""):
    return [i["url"].replace("https://x.com", "") for i in api.get(f"/api/jobs/{jid}/urls?{q}").json()["items"]]


def test_ligero_no_manda_el_texto_de_cada_pagina(entorno):
    api, jid, _ = entorno
    assert api.get(f"/api/jobs/{jid}/urls").json()["items"][0]["page_content"] is not None
    assert all(i["page_content"] is None for i in api.get(f"/api/jobs/{jid}/urls?ligero=true").json()["items"])


def test_filtros_nuevos(entorno):
    api, jid, _ = entorno
    assert sorted(_urls(api, jid, "issue_type=title_too_long,title_missing")) == ["/b", "/c"]
    assert _urls(api, jid, "severity=error") == ["/null"]
    assert _urls(api, jid, "vacio=title") == ["/c"]
    assert sorted(_urls(api, jid, "con_valor=title")) == ["/a", "/b", "/null"]
    assert _urls(api, jid, "in_sitemap=false") == ["/b"]
    assert _urls(api, jid, "title_pixel_width_gte=580") == ["/b"]
    # La URL tiene su filtro; el buscador (URL o titulo) va aparte y se suman.
    assert _urls(api, jid, "url_contains=null") == ["/null"]
    assert _urls(api, jid, "url_contains=a&search=Página") == ["/a"]


def test_ordenar_por_pixeles(entorno):
    api, jid, _ = entorno
    assert _urls(api, jid, "sort_by=title_pixel_width&sort_dir=desc&con_valor=title")[0] == "/b"


def test_el_csv_de_la_vista_es_lo_que_se_ve(entorno):
    """Antes el boton exportaba el censo entero con 83 columnas."""
    api, jid, _ = entorno
    r = api.get(f"/api/jobs/{jid}/urls/export?status_group=2xx&inlinks_count_gte=3"
                f"&columnas=url,inlinks_count,title_pixel_width,in_sitemap&sort_by=inlinks_count&sort_dir=desc")
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]
    filas = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert filas[0] == ["URL", "Inlinks", "Título (px)", "En el sitemap"]
    assert [f[0] for f in filas[1:]] == ["https://x.com/c", "https://x.com/a"]   # 9 y 5 enlaces, el 404 fuera
    assert filas[2] == ["https://x.com/a", "5", "300", "si"]


def test_el_csv_rechaza_columnas_inventadas(entorno):
    api, jid, _ = entorno
    assert api.get(f"/api/jobs/{jid}/urls/export?columnas=password").status_code == 422
