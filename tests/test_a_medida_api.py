"""Extraccion y busqueda a medida contra la API, EJECUTADO con TestClient y
SQLite (#34, decision 104).

Lo que se sujeta:

- que el listado y el CSV de la vista devuelven las MISMAS filas con el mismo
  filtro (los dos pasan por `_urls_filtradas`);
- que «no evaluada», «sin coincidencia» y «error / bloqueada» son casos
  distintos: «no contiene» (`!a`, `=no`, `vacio`) solo mira lo evaluado;
- que una regla que el censo no tiene es un 422, no el censo entero;
- el orden numerico, una columna por posicion con `todos` y el escape de
  formulas en el CSV;
- `/export`: las columnas `x_*` al final y las 83 fijas en su sitio;
- la prueba de reglas (HTML pegado y `raw_html`, 409 sin el, 413 si pesa);
- que el uso cuenta `x.*` y nunca el nombre de una regla;
- que guardar un perfil valida la fusion con su cliente.
"""

from __future__ import annotations

import csv
import io
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("lxml")
pytest.importorskip("regex")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import (  # noqa: E402
    Base, CustomExtraction, HtmlMeta, Job, PageContent, Url,
)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


CONFIG = {
    "custom_extraction": {
        "precio": {"etiqueta": "Precio", "tipo": "css", "expresion": ".precio", "valor": "numero"},
        "sku": {"tipo": "css", "expresion": ".sku"},
        "tallas": {"etiqueta": "Talla", "tipo": "css", "expresion": "li", "cuantos": "todos",
                   "max_valores": 5},
    },
    "custom_search": {"gtm": {"etiqueta": "GTM", "patron": "GTM-"}},
}

FICHA = ("<html><head><title>Ficha</title><script>GTM-AB12</script></head><body>"
         "<span class='precio'>19,95 €</span><div class='sku'>=HYPERLINK(\"x\")</div>"
         "<ul><li>38</li><li>39</li></ul></body></html>")


def _ce(job_id, url_id, nombre, clase, estado, **kw):
    return CustomExtraction(job_id=job_id, url_id=url_id, nombre=nombre, clase=clase,
                            estado=estado, truncado=False, render=False, **kw)


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    from api.routers import extraccion, results

    engine = create_engine(f"sqlite:///{tmp_path}/m.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    job = Job(id=uuid.uuid4(), name="tienda", status="completed",
              seeds=["https://x.com/"], config=CONFIG)
    s.add(job)
    s.flush()

    def url(ruta, codigo=200, html=True):
        u = Url(job_id=job.id, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com",
                path=ruta, is_internal=True, is_html=html, status_code=codigo,
                status_group=f"{codigo // 100}xx", resource_type="html" if html else "pdf")
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title=ruta))
        return u

    p1, p2, p3, muro, pdf = url("/p/1"), url("/p/2"), url("/p/3"), url("/muro"), url("/doc.pdf", html=False)
    j = job.id
    s.add_all([
        _ce(j, p1.id, "precio", "extractor", "ok", n=1, valor="19,95 €", valor_clave="19,95 €", valor_num=19.95),
        _ce(j, p1.id, "sku", "extractor", "ok", n=1, valor='=HYPERLINK("x")', valor_clave='=HYPERLINK("x")'),
        _ce(j, p1.id, "tallas", "extractor", "ok", n=2, valor="38 | 39", valor_clave="38 | 39",
            valores=["38", "39"]),
        _ce(j, p1.id, "gtm", "busqueda", "ok", n=1, valor="GTM-AB12", valor_num=1.0),
        _ce(j, p2.id, "precio", "extractor", "ok", n=1, valor="1.299,00 €", valor_clave="1.299,00 €",
            valor_num=1299.0),
        _ce(j, p2.id, "tallas", "extractor", "ok", n=3, valor="40 | 41 | 42", valor_clave="40 | 41 | 42",
            valores=["40", "41", "42"]),
        _ce(j, p2.id, "gtm", "busqueda", "sin_coincidencia", n=0),
        _ce(j, p3.id, "precio", "extractor", "sin_coincidencia", n=0),
        _ce(j, p3.id, "gtm", "busqueda", "sin_coincidencia", n=0),
        _ce(j, muro.id, "precio", "extractor", "pagina_bloqueada"),
        _ce(j, muro.id, "gtm", "busqueda", "pagina_bloqueada"),
    ])
    s.add(PageContent(url_id=p1.id, content_text="Ficha 19,95", raw_html=FICHA))
    s.add(PageContent(url_id=p2.id, content_text="Ficha 2"))
    s.commit()
    ids = {k: v.id for k, v in dict(p1=p1, p2=p2, p3=p3, muro=muro, pdf=pdf).items()}
    s.close()

    app = FastAPI()
    app.include_router(results.router)
    app.include_router(extraccion.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    import shared.database

    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    return TestClient(app), j, ids


def _rutas(api, jid, q=""):
    r = api.get(f"/api/jobs/{jid}/urls?{q}")
    assert r.status_code == 200, r.text
    return [i["url"].replace("https://x.com", "") for i in r.json()["items"]]


def _csv(api, jid, q):
    r = api.get(f"/api/jobs/{jid}/urls/export?{q}")
    assert r.status_code == 200, r.text
    return list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("q,esperado", [
    ("x.gtm=no", ["/p/2", "/p/3"]),                       # ni la bloqueada ni el PDF
    ("x.gtm=si", ["/p/1"]),
    ("x.precio_gte=100", ["/p/2"]),
    ("x.precio_lte=100", ["/p/1"]),
    ("x.precio_contains=19", ["/p/1"]),
    ("x.precio_contains=!19", ["/p/2", "/p/3"]),          # «no contiene»: solo lo evaluado
    ("vacio=x.precio", ["/p/3"]),                         # vacio = sin_coincidencia, no sin fila
    ("con_valor=x.precio", ["/p/1", "/p/2"]),
    ("x.precio_estado=pagina_bloqueada", ["/muro"]),
    ("x.tallas_contains=40", ["/p/2"]),
    ("x.gtm_gte=1", ["/p/1"]),
])
def test_filtros_y_el_csv_dan_las_mismas_filas(entorno, q, esperado):
    api, jid, _ = entorno
    assert sorted(_rutas(api, jid, q)) == esperado
    filas = _csv(api, jid, q + "&columnas=url")
    assert sorted(f[0].replace("https://x.com", "") for f in filas[1:]) == esperado


def test_no_evaluada_sin_coincidencia_y_bloqueada_son_cosas_distintas(entorno):
    api, jid, ids = entorno
    r = api.get(f"/api/jobs/{jid}/urls?a_medida=precio,gtm").json()["items"]
    por = {i["url"].replace("https://x.com", ""): i["a_medida"] for i in r}
    assert por["/doc.pdf"] == {}                                    # no evaluada: sin fila
    assert por["/p/3"]["precio"]["estado"] == "sin_coincidencia"
    assert por["/muro"]["precio"]["estado"] == "pagina_bloqueada"
    assert set(por["/p/1"]) == {"precio", "gtm"}                    # solo lo pedido
    todas = api.get(f"/api/jobs/{jid}/urls?a_medida=todas").json()["items"]
    assert set(next(i for i in todas if i["url"].endswith("/p/1"))["a_medida"]) == {
        "precio", "sku", "tallas", "gtm"}


@pytest.mark.parametrize("q", ["x.nada=si", "x.nada_gte=1", "vacio=x.nada",
                               "sort_by=x.nada", "a_medida=nada"])
def test_una_regla_que_el_censo_no_tiene_es_un_422(entorno, q):
    api, jid, _ = entorno
    r = api.get(f"/api/jobs/{jid}/urls?{q}")
    assert r.status_code == 422 and '"nada"' in r.json()["detail"]
    assert api.get(f"/api/jobs/{jid}/urls/export?columnas=url,x.nada").status_code == 422


def test_orden_numerico_con_las_no_evaluadas_al_final(entorno):
    api, jid, _ = entorno
    assert _rutas(api, jid, "sort_by=x.precio&sort_dir=desc")[:2] == ["/p/2", "/p/1"]
    asc = _rutas(api, jid, "sort_by=x.precio&sort_dir=asc")
    assert asc[:2] == ["/p/1", "/p/2"] and set(asc[2:]) == {"/p/3", "/muro", "/doc.pdf"}
    assert _rutas(api, jid, "sort_by=x.gtm&sort_dir=desc")[0] == "/p/1"
    filas = _csv(api, jid, "columnas=url&sort_by=x.precio&sort_dir=desc")
    assert [f[0] for f in filas[1:3]] == ["https://x.com/p/2", "https://x.com/p/1"]


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def test_csv_de_la_vista_columna_por_posicion_y_formulas(entorno):
    api, jid, _ = entorno
    filas = _csv(api, jid, "columnas=url,x.precio,x.tallas,x.gtm,x.sku&sort_by=url")
    # Tallas: el maximo de coincidencias del censo (3) con el tope de la regla (5)
    assert filas[0] == ["URL", "Precio", "Talla 1", "Talla 2", "Talla 3", "GTM", "sku"]
    por = {f[0].replace("https://x.com", ""): f[1:] for f in filas[1:]}
    assert por["/p/1"] == ["19.95", "38", "39", "", "si", "'=HYPERLINK(\"x\")"]
    assert por["/p/2"] == ["1299", "40", "41", "42", "no", ""]
    assert por["/p/3"][0] == "" and por["/p/3"][4] == "no"
    assert por["/muro"][0] == "[pagina_bloqueada]"           # no se lee como «vacio»
    assert por["/doc.pdf"] == ["", "", "", "", "", ""]


def test_export_completo_lleva_las_reglas_al_final(entorno, monkeypatch):
    from api.routers import results
    from api.routers.results import CSV_COLUMNS, _stream_csv

    # Los agregados por lote usan `string_agg DISTINCT`, que SQLite no tiene;
    # aqui se mira solo lo que anade la extraccion a medida.
    monkeypatch.setattr(results, "_extras_por_lote", lambda s, ids: {})
    monkeypatch.setattr(results, "_pagerank_fiable", lambda s, j: None)
    api, jid, _ = entorno
    texto = "".join(_stream_csv(jid))
    filas = list(csv.reader(io.StringIO(texto.lstrip("﻿"))))
    assert filas[0][:len(CSV_COLUMNS)] == CSV_COLUMNS            # las 83 en su sitio
    assert filas[0][len(CSV_COLUMNS):] == ["x_precio", "x_sku", "x_tallas", "x_gtm"]
    p1 = next(f for f in filas[1:] if f[0] == "https://x.com/p/1")
    assert p1[len(CSV_COLUMNS):] == ["19.95", "'=HYPERLINK(\"x\")", "38 | 39", "si"]
    assert all(len(f) == len(filas[0]) for f in filas[1:])


def test_export_completo_sin_reglas_no_cambia(entorno, tmp_path):
    from api.routers.results import CSV_COLUMNS, columnas_a_medida_export

    assert columnas_a_medida_export({}) == []
    assert columnas_a_medida_export(None) == []
    assert len(CSV_COLUMNS) == 83


# ---------------------------------------------------------------------------
# Ficha, resumen y distribucion
# ---------------------------------------------------------------------------
def test_la_ficha_lleva_sus_valores(entorno):
    api, jid, ids = entorno
    d = api.get(f"/api/jobs/{jid}/urls/{ids['p1']}").json()
    assert d["a_medida"]["precio"]["valor_num"] == 19.95
    assert api.get(f"/api/jobs/{jid}/urls/{ids['pdf']}").json()["a_medida"] == {}


def test_resumen_del_job(entorno):
    api, jid, _ = entorno
    d = api.get(f"/api/jobs/{jid}/extraccion").json()
    por = {r["nombre"]: r for r in d["reglas"]}
    assert por["precio"]["evaluadas"] == 4 and por["precio"]["ok"] == 2
    assert por["precio"]["sin_coincidencia"] == 1 and por["precio"]["bloqueadas"] == 1
    assert {t["valor"] for t in por["precio"]["top"]} == {"19,95 €", "1.299,00 €"}
    assert por["gtm"]["clase"] == "busqueda" and len(por["gtm"]["firma"]) == 64
    v = api.get(f"/api/jobs/{jid}/extraccion/precio/valores").json()
    assert v["total"] == 2
    assert api.get(f"/api/jobs/{jid}/extraccion/nada/valores").status_code == 422


# ---------------------------------------------------------------------------
# Probar reglas
# ---------------------------------------------------------------------------
def test_probar_con_html_pegado(entorno):
    api, _, _ = entorno
    r = api.post("/api/extraccion/probar", json={
        "html": FICHA, **CONFIG,
        "custom_search": {"gtm": {"patron": "GTM-"}, "stock": {"patron": "sin stock"}},
    })
    assert r.status_code == 200, r.text
    por = {f["nombre"]: f for f in r.json()["reglas"]}
    assert por["precio"]["valor_num"] == 19.95 and por["precio"]["estado"] == "ok"
    assert por["tallas"]["valores"] == ["38", "39"]
    assert por["gtm"]["estado"] == "ok" and por["stock"]["estado"] == "sin_coincidencia"
    assert all("ms" in f for f in r.json()["reglas"])
    assert r.json()["fuente"].startswith("HTML pegado")


def test_probar_con_el_raw_html_de_un_censo_y_409_sin_el(entorno):
    api, jid, ids = entorno
    r = api.post("/api/extraccion/probar", json={"job_id": str(jid), "url_id": ids["p1"], **CONFIG})
    assert r.status_code == 200, r.text
    assert {f["nombre"]: f for f in r.json()["reglas"]}["precio"]["valor_num"] == 19.95
    r = api.post("/api/extraccion/probar", json={"job_id": str(jid), "url_id": ids["p2"], **CONFIG})
    assert r.status_code == 409


def test_probar_valida_y_limita(entorno):
    api, jid, ids = entorno
    r = api.post("/api/extraccion/probar", json={"html": FICHA, "custom_extraction": {
        "r": {"tipo": "xpath", "expresion": "//a[re:test(@href,'x')]"}}})
    assert r.status_code == 422 and "custom_extraction.r" in r.text
    assert api.post("/api/extraccion/probar", json={"custom_search": {"g": {"patron": "x"}}}
                    ).status_code == 422                       # sin fuente
    assert api.post("/api/extraccion/probar", json={
        "html": FICHA, "job_id": str(jid), "url_id": ids["p1"]}).status_code == 422  # dos
    grande = "<p>" + "x" * (5 * 1024 * 1024) + "</p>"
    assert api.post("/api/extraccion/probar", json={
        "html": grande, "custom_search": {"g": {"patron": "x"}}}).status_code == 413


def test_probar_avisa_de_lo_que_cuesta(entorno):
    api, _, _ = entorno
    html = "<html><body><p>" + "a" * 40 + "b</p></body></html>"
    r = api.post("/api/extraccion/probar", json={"html": html, "custom_extraction": {
        "mala": {"tipo": "regex", "expresion": r"(a|aa)+$"}}})
    assert r.status_code == 200
    assert r.json()["reglas"][0]["estado"] == "tiempo_agotado"
    assert any("mala" in a and "min" in a for a in r.json()["avisos"])


# ---------------------------------------------------------------------------
# Uso y herencia
# ---------------------------------------------------------------------------
def test_el_uso_cuenta_x_y_nunca_el_nombre():
    from starlette.datastructures import QueryParams

    from api import uso

    claves = uso._claves(QueryParams(
        "vista=explorador&x.precio_outlet_nike_gte=1&sort_by=x.precio_outlet_nike"
        "&vacio=x.precio_outlet_nike,title&a_medida=precio_outlet_nike"
        "&columnas=url,x.precio_outlet_nike"), csv=True)
    assert not any("nike" in c for c in claves)
    assert {"filtro:x.*", "orden:x.*", "filtro:x.*:vacio", "filtro:title:vacio",
            "columna:x.*"} <= set(claves)


def test_guardar_un_perfil_valida_la_fusion_con_su_cliente(tmp_path, monkeypatch):
    from api.routers import clientes

    engine = create_engine(f"sqlite:///{tmp_path}/c.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(clientes.router)

    def _sesion():
        s = Sesion()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    api = TestClient(app)
    c = api.post("/api/clients", json={
        "nombre": "Tienda", "config_defecto": {"extraction": {"extract_page_content": False}}})
    assert c.status_code == 201, c.text
    cid = c.json()["id"]
    malo = {"nombre": "fichas", "seeds": ["https://x.com/"], "config": {"custom_search": {
        "s": {"patron": "sin stock", "fuente": "contenido"}}}}
    r = api.post(f"/api/clients/{cid}/profiles", json=malo)
    assert r.status_code == 422 and "custom_search.s" in r.text
    bueno = {"nombre": "fichas", "seeds": ["https://x.com/"], "config": {"custom_search": {"s": {"patron": "sin stock"}}}}
    r = api.post(f"/api/clients/{cid}/profiles", json=bueno)
    assert r.status_code == 201, r.text
    r = api.patch(f"/api/clients/{cid}/profiles/{r.json()['id']}", json=malo)
    assert r.status_code == 422
