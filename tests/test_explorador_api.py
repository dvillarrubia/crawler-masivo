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
from shared.models import Base, HtmlMeta, Issue, Job, Link, PageContent, Url  # noqa: E402


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


# ---------------------------------------------------------------------------
# Medir el uso (api/uso.py)
# ---------------------------------------------------------------------------
class _RedisFalso:
    def __init__(self):
        self.h: dict[str, dict[str, int]] = {}

    def pipeline(self):
        return self

    def hincrby(self, clave, campo, n):
        self.h.setdefault(clave, {})[campo] = self.h.setdefault(clave, {}).get(campo, 0) + n

    def expire(self, *a):
        pass

    def execute(self):
        pass

    def hgetall(self, clave):
        return self.h.get(clave, {})


def test_el_uso_cuenta_nombres_nunca_valores():
    from starlette.datastructures import QueryParams

    from api import uso

    r = _RedisFalso()
    uso.anotar(r, QueryParams("vista=explorador&pestana=4xx&status_group=4xx&inlinks_count_gte=1"
                              "&search=cliente-secreto&issue_type=a,b&vacio=title&sort_by=inlinks_count&page=3"))
    uso.anotar(r, QueryParams("vista=explorador&columnas=url,title_pixel_width"), csv=True)
    uso.anotar(r, QueryParams("status_group=4xx"))            # no viene del Explorador: no cuenta
    campos = set().union(*[set(v) for v in r.h.values()])
    assert {"consultas", "csv", "pestana:4xx", "filtro:inlinks_count", "busqueda", "problema:a",
            "problema:b", "filtro:title:vacio", "orden:inlinks_count", "paginado",
            "columna:title_pixel_width"} <= campos
    assert not any("cliente-secreto" in c for c in campos)    # el texto buscado no se guarda
    assert "filtro:status_group" not in campos                 # lo pone la pestaña
    d = uso.leer(r, dias=1)
    assert d["consultas"] == 1 and d["csv"] == 1


def test_el_uso_no_rompe_si_redis_falla():
    from starlette.datastructures import QueryParams

    from api import uso

    class Roto:
        def pipeline(self):
            raise ConnectionError("redis caido")

    uso.anotar(Roto(), QueryParams("vista=explorador"))       # no lanza


# ---------------------------------------------------------------------------
# Agrupar por plantilla
# ---------------------------------------------------------------------------
@pytest.fixture()
def con_plantillas(tmp_path):
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/p.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    job = Job(id=uuid.uuid4(), name="censo", status="completed", seeds=["https://x.com/"], config={})
    s.add(job)
    s.flush()
    rutas = [("/es/null", 404), ("/en/null", 404), ("/de/null", 404), ("/es/corporativa/null", 404),
             ("/es/hoteles/a", 200), ("/es/hoteles/b", 200), ("/es/hoteles/c", 200)]
    for i, (ruta, codigo) in enumerate(rutas):
        u = Url(job_id=job.id, url=f"https://x.com{ruta}", path=ruta, url_hash=str(i), is_internal=True,
                is_html=True, status_code=codigo, status_group=f"{codigo // 100}xx",
                indexable=codigo == 200, inlinks_count=2, pagerank_score=10 * i)
        s.add(u)
        s.flush()
        if codigo == 404:
            s.add(Issue(job_id=job.id, url_id=u.id, issue_type="4xx_error", severity="error"))
    # /es/null (hash "0") enlazada dos veces desde /es/hoteles/a y una desde /b.
    a, b = (s.query(Url).filter(Url.path == r).one() for r in ("/es/hoteles/a", "/es/hoteles/b"))
    for origen, ancla in ((a, "uno"), (a, "dos"), (b, "tres")):
        s.add(Link(job_id=job.id, from_url_id=origen.id, to_url="https://x.com/es/null", to_url_hash="0",
                   anchor_text=ancla, link_position="content", follow=True, link_type="hyperlink"))
    s.commit()
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
    return TestClient(app), job.id


def test_por_plantilla_agrupa_y_delata_el_tramo_repetido(con_plantillas):
    """`/de/null`, `/en/null`... son un solo fallo de plantilla, no cuatro 404."""
    api, jid = con_plantillas
    d = api.get(f"/api/jobs/{jid}/urls/plantillas").json()
    assert d["urls"] == 7
    por_forma = {p["plantilla"]: p for p in d["plantillas"]}
    idioma = por_forma["/:idioma/:slug (2n)"]
    assert idioma["urls"] == 3 and idioma["con_errores"] == 3 and idioma["indexables"] == 0
    assert idioma["tramos_repetidos"] == [{"tramo": "null", "veces": 3}]
    hoteles = por_forma["/:idioma/hoteles/:slug (3n)"]
    assert hoteles["urls"] == 3 and hoteles["por_estado"] == {"2xx": 3} and hoteles["tramos_repetidos"] == []
    # En toda la vista, `null` cruza de plantilla (tambien /es/corporativa/null).
    assert {"tramo": "null", "veces": 4} in d["tramos_repetidos"]


def test_por_plantilla_respeta_los_filtros_y_se_puede_entrar(con_plantillas):
    api, jid = con_plantillas
    d = api.get(f"/api/jobs/{jid}/urls/plantillas?status_group=2xx").json()
    assert [p["plantilla"] for p in d["plantillas"]] == ["/:idioma/hoteles/:slug (3n)"]
    r = api.get(f"/api/jobs/{jid}/urls", params={"plantilla": "/:idioma/:slug (2n)"}).json()
    assert sorted(i["url"] for i in r["items"]) == [f"https://x.com/{l}/null" for l in ("de", "en", "es")]


def test_enlazada_desde_y_enlaces_entrantes(con_plantillas):
    """Para un 404, de donde se enlaza: el origen mas frecuente y cuantas
    paginas distintas lo enlazan, y el CSV con todos los enlaces."""
    api, jid = con_plantillas
    items = api.get(f"/api/jobs/{jid}/urls?url_contains=/es/null&origenes=true").json()["items"]
    fila = next(i for i in items if i["url"].endswith("/es/null"))
    assert fila["enlazada_desde"] == "https://x.com/es/hoteles/a" and fila["origenes"] == 2
    sin = api.get(f"/api/jobs/{jid}/urls?url_contains=/es/null").json()["items"][0]
    assert sin["enlazada_desde"] is None                     # sin ?origenes no se calcula
    r = api.get(f"/api/jobs/{jid}/urls/enlaces-entrantes?status_group=4xx")
    filas = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert filas[0][:3] == ["Destino", "Estado destino", "Origen"]
    assert sorted(f[4] for f in filas[1:]) == ["dos", "tres", "uno"]
    exp = list(csv.reader(io.StringIO(api.get(
        f"/api/jobs/{jid}/urls/export?url_contains=/es/null&columnas=url,enlazada_desde,origenes").content.decode("utf-8-sig"))))
    assert ["https://x.com/es/null", "https://x.com/es/hoteles/a", "2"] in exp
