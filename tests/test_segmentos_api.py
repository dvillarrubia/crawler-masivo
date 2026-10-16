"""Segmentos aplicados a todo (decision 108), EJECUTADO con TestClient y
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


def test_issues_por_segmento_dejan_fuera_las_de_un_hallazgo(entorno):
    api, jid, _ids, _ = entorno
    tipos = sorted(i["issue_type"] for i in api.get(f"/api/jobs/{jid}/issues?segmento=fichas").json()["items"])
    assert tipos == ["status_4xx", "title_too_short", "title_too_short"]


def test_resumen_con_porcentaje_por_segmento_y_su_denominador(entorno):
    api, jid, _ids, _ = entorno
    r = {(x["issue_type"]): x for x in api.get(
        f"/api/jobs/{jid}/issues/resumen?por_segmento=true").json()}
    corto = {p["segmento"]: p for p in r["title_too_short"]["por_segmento"]}
    assert corto["fichas"] == {"segmento": "fichas", "paginas": 2, "urls": 4, "pct": 50.0}
    assert corto["blog"]["pct"] == 50.0
    assert r["title_too_short"]["agregadas"] == 0
    # La imagen sin alt no se reparte: se dice cuantas hay sin segmento.
    assert r["image_missing_alt"]["por_segmento"] == [] and r["image_missing_alt"]["agregadas"] == 1

    filtrado = {x["issue_type"]: x for x in api.get(
        f"/api/jobs/{jid}/issues/resumen?segmento=fichas").json()}
    assert set(filtrado) == {"status_4xx", "title_too_short"}
    assert filtrado["status_4xx"]["pct_paginas"] == 25.0
    assert filtrado["title_too_short"]["urls_denominador"] == 4


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
