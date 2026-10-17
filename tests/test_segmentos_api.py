"""Segmentos aplicados a todo (decision 125), EJECUTADO con TestClient y
SQLite: filtro en `/urls`, en su CSV, en `/issues` y `/issues/resumen`, el %
por segmento con su denominador, la vista agrupada por segmento y el
re-segmentado retroactivo por la API.
"""

from __future__ import annotations

import csv
import io
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("regex")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import Base, CustomExtraction, HtmlMeta, Issue, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


OG = {"tipo": "css", "expresion": "meta[property='og:type']", "devuelve": "atributo",
      "atributo": "content"}
REGLAS = [{"nombre": "blog", "patron": "^/blog/"},
          {"nombre": "fichas", "si": {"x": "og_type", "igual": "product"}}]


@pytest.fixture()
def entorno(tmp_path):
    from api.routers import results, segmentos
    from shared.segmentos import segmentar

    engine = create_engine(f"sqlite:///{tmp_path}/e.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    job = Job(id=uuid.uuid4(), name="censo", status="completed", seeds=["https://x.com/"],
              config={"segmentos": REGLAS, "custom_extraction": {"og_type": OG}})
    s.add(job)
    s.flush()
    ids = {}
    # 4 fichas (una 404), 2 posts y una categoria sin regla.
    for ruta, og, codigo in (("/crema", "product", 200), ("/serum", "product", 200),
                             ("/champu", "product", 200), ("/agotado", "product", 404),
                             ("/blog/a", "article", 200), ("/blog/b", "article", 200),
                             ("/maquillaje", "website", 200)):
        u = Url(job_id=job.id, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com", path=ruta,
                is_internal=True, is_html=True, status_code=codigo, status_group=f"{codigo // 100}xx",
                resource_type="html", indexable=codigo == 200)
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title=ruta))
        s.add(CustomExtraction(job_id=job.id, url_id=u.id, nombre="og_type", clase="extractor",
                               estado="ok", n=1, valor=og, valor_clave=og))
        ids[ruta] = u.id
    s.add_all([
        Issue(job_id=job.id, url_id=ids["/agotado"], issue_type="status_4xx", severity="error"),
        Issue(job_id=job.id, url_id=ids["/crema"], issue_type="title_too_short", severity="warning"),
        Issue(job_id=job.id, url_id=ids["/serum"], issue_type="title_too_short", severity="warning"),
        Issue(job_id=job.id, url_id=ids["/blog/a"], issue_type="title_too_short", severity="warning"),
        # Una por hallazgo, anclada en una ficha: NO es de la ficha (decision 64).
        Issue(job_id=job.id, url_id=ids["/crema"], issue_type="image_missing_alt", severity="warning",
              details={"paginas_afectadas": 7}),
        # Una por hallazgo pero de UNA sola pagina (la foto propia del
        # producto): esa SI es de su pagina y se reparte.
        Issue(job_id=job.id, url_id=ids["/serum"], issue_type="image_missing_alt", severity="warning",
              details={"paginas_afectadas": 1}),
        # Un tipo que SOLO tiene hallazgos de muchas paginas (el bloque de
        # plantilla de la decision 73): no puede desaparecer al filtrar.
        Issue(job_id=job.id, url_id=ids["/crema"], issue_type="structured_data_error", severity="error",
              details={"paginas_afectadas": 4}),
    ])
    s.commit()
    segmentar(s, job.id)
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(results.router)
    app.include_router(segmentos.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), job.id, ids, Sesion


def _rutas(api, jid, q):
    return sorted(i["path"] for i in api.get(f"/api/jobs/{jid}/urls?{q}").json()["items"])


def test_filtro_por_segmento_en_urls_y_columna(entorno):
    api, jid, _ids, _ = entorno
    assert _rutas(api, jid, "segmento=fichas") == ["/agotado", "/champu", "/crema", "/serum"]
    assert _rutas(api, jid, "segmento=fichas&segmento=blog&status_group=2xx") == [
        "/blog/a", "/blog/b", "/champu", "/crema", "/serum"]
    fila = api.get(f"/api/jobs/{jid}/urls?segmento=blog").json()["items"][0]
    assert fila["segmento"] == "blog"
    orden = [i["segmento"] for i in api.get(f"/api/jobs/{jid}/urls?sort_by=segmento").json()["items"]]
    assert orden == sorted(orden)


def test_csv_de_la_vista_filtra_y_exporta_el_segmento(entorno):
    api, jid, _ids, _ = entorno
    r = api.get(f"/api/jobs/{jid}/urls/export?segmento=blog&columnas=url,segmento")
    filas = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    assert filas[0] == ["URL", "Segmento"]
    assert sorted(filas[1:]) == [["https://x.com/blog/a", "blog"], ["https://x.com/blog/b", "blog"]]


def test_issues_por_segmento_dejan_fuera_las_de_un_hallazgo_de_varias_paginas(entorno):
    api, jid, _ids, _ = entorno
    filas = api.get(f"/api/jobs/{jid}/issues?segmento=fichas").json()["items"]
    tipos = sorted(i["issue_type"] for i in filas)
    assert tipos == ["image_missing_alt", "status_4xx", "title_too_short", "title_too_short"]
    # La imagen que se reparte es la de /serum (1 pagina), no el ancla de 7.
    assert [i["details"] for i in filas if i["issue_type"] == "image_missing_alt"] == [
        {"paginas_afectadas": 1}]


def test_el_explorador_cuenta_lo_mismo_que_problemas_con_un_segmento(entorno):
    """«Ver en el Explorador» desde Problemas con un segmento: las mismas
    paginas que conto la tarjeta. Sin segmento no cambia nada."""
    api, jid, _ids, _ = entorno
    assert _rutas(api, jid, "segmento=fichas&issue_type=image_missing_alt") == ["/serum"]
    assert _rutas(api, jid, "issue_type=image_missing_alt") == ["/crema", "/serum"]


def test_resumen_con_porcentaje_por_segmento_y_su_denominador(entorno):
    api, jid, _ids, _ = entorno
    r = {(x["issue_type"]): x for x in api.get(
        f"/api/jobs/{jid}/issues/resumen?por_segmento=true").json()}
    corto = {p["segmento"]: p for p in r["title_too_short"]["por_segmento"]}
    assert corto["fichas"] == {"segmento": "fichas", "paginas": 2, "urls": 4, "pct": 50.0}
    assert corto["blog"]["pct"] == 50.0
    assert r["title_too_short"]["agregadas"] == 0
    # La de 7 paginas no se reparte y se dice; la de 1 pagina si.
    assert r["image_missing_alt"]["por_segmento"] == [
        {"segmento": "fichas", "paginas": 1, "urls": 4, "pct": 25.0}]
    assert r["image_missing_alt"]["agregadas"] == 1
    assert r["structured_data_error"]["por_segmento"] == []
    assert r["structured_data_error"]["agregadas"] == 1

    filas = api.get(f"/api/jobs/{jid}/issues/resumen?segmento=fichas").json()
    filtrado = {x["issue_type"]: x for x in filas}
    # Ningun tipo desaparece por no poder repartirse: sale con 0 y lo dice.
    assert set(filtrado) == {"status_4xx", "structured_data_error", "image_missing_alt", "title_too_short"}
    sd = filtrado["structured_data_error"]
    assert (sd["paginas"], sd["incidencias"], sd["agregadas"], sd["pct_paginas"]) == (0, 0, 1, 0.0)
    assert (filtrado["image_missing_alt"]["paginas"], filtrado["image_missing_alt"]["agregadas"]) == (1, 1)
    assert filtrado["status_4xx"]["pct_paginas"] == 25.0
    assert filtrado["title_too_short"]["urls_denominador"] == 4
    assert [x["issue_type"] for x in filas][:2] == ["status_4xx", "structured_data_error"]


def test_vista_agrupada_por_segmento(entorno):
    api, jid, _ids, _ = entorno
    g = {p["plantilla"]: p["urls"] for p in api.get(
        f"/api/jobs/{jid}/urls/plantillas?agrupar=segmento").json()["plantillas"]}
    assert g["fichas"] == 4 and g["blog"] == 2 and sum(g.values()) == 7


def test_listar_y_resegmentar_retroactivo(entorno):
    api, jid, _ids, Sesion = entorno
    d = api.get(f"/api/jobs/{jid}/segmentos").json()
    assert d["al_dia"] is True
    assert [x["segmento"] for x in d["segmentos"]][:2] == ["blog", "fichas"]
    fichas = next(x for x in d["segmentos"] if x["segmento"] == "fichas")
    assert (fichas["urls"], fichas["indexables"], fichas["paginas_con_error"]) == (4, 3, 1)

    nuevas = [{"nombre": "fichas rotas", "si": {"todas": [
        {"x": "og_type", "igual": "product"}, {"campo": "status_group", "igual": "4xx"}]}}] + REGLAS
    r = api.post(f"/api/jobs/{jid}/segmentos", json={"segmentos": nuevas})
    assert r.status_code == 200, r.text
    assert r.json()["por_regla"][0]["urls"] == 1
    assert _rutas(api, jid, "segmento=fichas rotas") == ["/agotado"]
    with Sesion() as s:
        assert s.get(Job, jid).config["segmentos"][0]["nombre"] == "fichas rotas"
    # Sin cuerpo: re-materializa con las del censo; sigue al dia.
    assert api.post(f"/api/jobs/{jid}/segmentos").status_code == 200
    assert api.get(f"/api/jobs/{jid}/segmentos").json()["al_dia"] is True


def test_resegmentar_rechaza_lo_que_no_casaria_nunca_y_un_censo_en_marcha(entorno):
    api, jid, _ids, Sesion = entorno
    r = api.post(f"/api/jobs/{jid}/segmentos",
                 json={"segmentos": [{"nombre": "caras", "si": {"x": "precio", "gte": 100}}]})
    assert r.status_code == 422 and "precio" in r.text
    with Sesion() as s:
        s.get(Job, jid).status = "analyzing"
        s.commit()
    assert api.post(f"/api/jobs/{jid}/segmentos").status_code == 409


def test_el_uso_cuenta_el_filtro_y_la_agrupacion_sin_el_valor():
    from api.uso import _claves

    from starlette.datastructures import QueryParams

    claves = _claves(QueryParams("vista=explorador&segmento=fichas-nike-outlet&agrupar=segmento"), csv=False)
    assert "filtro:segmento" in claves and "agrupar:segmento" in claves
    assert not any("nike" in c for c in claves)


def test_externas_no_entran_en_el_segmento_ni_en_su_denominador(entorno):
    """Un enlace saliente a `otro.com/blog/` casa con `^/blog/` por la ruta,
    pero no es del blog del cliente: con las cuatro externas dentro, el % de
    titulos cortos del blog bajaba de 50 % (1 de 2) a 16,67 % (1 de 6). Una
    imagen interna bajo `/blog/` tampoco es una pagina del blog."""
    api, jid, _ids, Sesion = entorno
    with Sesion() as s:
        for i in range(4):
            s.add(Url(job_id=jid, url=f"https://otro.com/blog/{i}", url_hash=f"ext{i}", host="otro.com",
                      path=f"/blog/{i}", is_internal=False, is_html=True, status_code=200,
                      status_group="2xx", resource_type="html"))
        s.add(Url(job_id=jid, url="https://x.com/blog/wp-content/a.jpg", url_hash="img", host="x.com",
                  path="/blog/wp-content/a.jpg", is_internal=True, is_html=False, status_code=200,
                  status_group="2xx", resource_type="image"))
        s.commit()
    assert api.post(f"/api/jobs/{jid}/segmentos").status_code == 200
    assert _rutas(api, jid, "segmento=blog") == ["/blog/a", "/blog/b"]
    blog = {x["issue_type"]: x for x in api.get(
        f"/api/jobs/{jid}/issues/resumen?segmento=blog").json()}
    assert blog["title_too_short"]["urls_denominador"] == 2
    assert blog["title_too_short"]["pct_paginas"] == 50.0
    d = api.get(f"/api/jobs/{jid}/segmentos").json()
    assert d["segmentacion"]["externas"] == 4 and d["segmentacion"]["recursos"] == 1
    assert {x["segmento"]: x["urls"] for x in d["segmentos"]}["(externa)"] == 4


def _app_clientes(Sesion, monkeypatch):
    from api.routers import clientes, jobs

    app = FastAPI()
    app.include_router(jobs.router)
    app.include_router(clientes.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    class _Redis:
        def rpush(self, *a):
            pass

        def set(self, *a, **k):
            pass

        def delete(self, *a, **k):
            pass

    app.dependency_overrides[get_session] = _sesion
    monkeypatch.setattr(jobs, "get_redis", lambda: _Redis())
    return TestClient(app)


def test_un_perfil_o_un_job_segmentan_por_una_extraccion_del_cliente(tmp_path, monkeypatch):
    """La herencia de la decision 83 con segmentos: la `x` se comprueba sobre
    la config fusionada, no capa a capa. Lo que ninguna capa define sigue
    siendo un 422 que lo nombra."""
    engine = create_engine(f"sqlite:///{tmp_path}/c.db")
    Base.metadata.create_all(engine)
    api = _app_clientes(sessionmaker(bind=engine, expire_on_commit=False), monkeypatch)
    c = api.post("/api/clients", json={"nombre": "Acme", "config_defecto": {
        "custom_extraction": {"og_type": OG}}})
    assert c.status_code == 201, c.text
    cid = c.json()["id"]
    fichas = [{"nombre": "fichas", "si": {"x": "og_type", "igual": "product"}}]

    p = api.post(f"/api/clients/{cid}/profiles", json={
        "nombre": "Tienda", "seeds": ["https://acme.com/"], "config": {"segmentos": fichas}})
    assert p.status_code == 201, p.text
    j = api.post("/api/jobs", json={"name": "censo", "profile_id": p.json()["id"]})
    assert j.status_code == 201, j.text
    assert j.json()["config"]["segmentos"][0]["nombre"] == "fichas"
    # El override de un job sobre el cliente, sin perfil.
    j = api.post("/api/jobs", json={"name": "censo2", "cliente_id": cid,
                                    "seeds": ["https://acme.com/"], "config": {"segmentos": fichas}})
    assert j.status_code == 201, j.text

    malas = [{"nombre": "caras", "si": {"x": "precio", "gte": 100}}]
    r = api.post(f"/api/clients/{cid}/profiles", json={
        "nombre": "Otro", "seeds": ["https://acme.com/"], "config": {"segmentos": malas}})
    assert r.status_code == 422 and "precio" in r.text
    r = api.post("/api/jobs", json={"name": "sin cliente", "seeds": ["https://acme.com/"],
                                    "config": {"segmentos": malas}})
    assert r.status_code == 422 and "precio" in r.text
    r = api.post("/api/clients", json={"nombre": "Otro", "config_defecto": {"segmentos": malas}})
    assert r.status_code == 422 and "precio" in r.text
