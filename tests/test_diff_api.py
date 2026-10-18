"""`GET /api/jobs/{id}/diff/{otro}` con lo de la decision 131, EJECUTADO con
TestClient y SQLite: los campos nuevos salen de la base (tipos de datos
estructurados, hreflang sin los del `<body>`, description, profundidad), el
mapeo de rutas empareja una migracion y una regex rota es un 422 que dice
cual. Y las incidencias nuevas se separan entre URL existente y URL nueva.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import (  # noqa: E402
    Base, Hreflang, HtmlMeta, Issue, Job, StructuredData, Url,
)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


N = 40
MIGRACION = [[r"^/hoteles/(h\d+)\.html$", r"/hotel/\1/"]]


def _censo(s, dia, ruta_de, *, sin_producto=False, con_regresion=False, producto_roto=False):
    """N fichas de hotel. `ruta_de(i)` da la ruta de la ficha i."""
    j = Job(id=uuid.uuid4(), name=f"censo {dia}", status="completed",
            finish_reason="finished", seeds=["https://x.com/"], config={},
            crawler_version="v1", analisis_version="a1",
            started_at=datetime(2026, 9, dia, tzinfo=timezone.utc))
    s.add(j)
    s.flush()
    for i in range(N):
        ruta = ruta_de(i)
        u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=f"{dia}-{i}",
                host="x.com", path=ruta, is_internal=True, is_html=True,
                status_code=200, indexable=True, indexability_status="Indexable",
                word_count=800, unique_inlinks_count=50, click_depth=2,
                pagerank_score=60)
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title=f"Hotel {i}", meta_description=f"Hotel {i} junto al mar"))
        s.add(StructuredData(url_id=u.id, format="jsonld", schema_type="BreadcrumbList"))
        if producto_roto:
            # Se lee con el lector permisivo y tiene tipo, pero Google lo
            # descarta entero (decision 80).
            s.add(StructuredData(url_id=u.id, format="jsonld", schema_type="Hotel",
                                 json_invalido="coma final antes de '}'"))
        elif not sin_producto:
            s.add(StructuredData(url_id=u.id, format="jsonld", schema_type="Hotel"))
        s.add(Hreflang(url_id=u.id, lang="es", href=f"https://x.com{ruta}", en_body=False))
        s.add(Hreflang(url_id=u.id, lang="EN", href=f"https://x.com/en{ruta}"))
        # Uno en el <body>: Google no lo lee, asi que no es un idioma de la
        # pagina y no puede «perderse» (decision 56).
        if dia == 1:
            s.add(Hreflang(url_id=u.id, lang="de", href=f"https://x.com/de{ruta}", en_body=True))
        if con_regresion and i == 0:
            s.add(Issue(job_id=j.id, url_id=u.id, issue_type="missing_h1", severity="warning"))
    if con_regresion:
        u = Url(job_id=j.id, url="https://x.com/nueva", url_hash=f"{dia}-nueva", host="x.com",
                path="/nueva", is_internal=True, is_html=True, status_code=200,
                indexable=True, indexability_status="Indexable", word_count=800)
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title="Nueva"))
        s.add(Issue(job_id=j.id, url_id=u.id, issue_type="missing_h1", severity="warning"))
    s.commit()
    return j.id


@pytest.fixture()
def entorno(tmp_path):
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/d.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), Sesion


def _diff(api, a, b, **params):
    q = "&".join(f"{k}={quote(v)}" for k, v in params.items())
    return api.get(f"/api/jobs/{a}/diff/{b}" + (f"?{q}" if q else ""))


def test_los_campos_nuevos_salen_de_la_base(entorno):
    api, Sesion = entorno
    s = Sesion()
    viejo = _censo(s, 1, lambda i: f"/h{i}")
    nuevo = _censo(s, 2, lambda i: f"/h{i}", sin_producto=True)
    r = _diff(api, nuevo, viejo).json()
    assert r["comparable"]
    sd = r["cambios"]["sd_tipos"]
    assert sd["total"] == N
    assert sd["ejemplos"][0]["pierde"] == ["Hotel"]
    # El hreflang del body no cuenta, y la caja del codigo tampoco.
    assert r["cambios"]["hreflang"]["total"] == 0
    assert r["cambios"]["meta_description"]["total"] == 0
    assert r["cambios"]["click_depth"]["total"] == 0
    al = {a["regla"]: a for a in r["alertas"]}
    assert al["pierden_datos_estructurados"]["paginas"] == N


def test_una_migracion_empareja_con_el_mapeo_de_rutas(entorno):
    api, Sesion = entorno
    s = Sesion()
    viejo = _censo(s, 1, lambda i: f"/hoteles/h{i}.html")
    # Dos fichas no migraron (siguen con otra forma): 38 de 40 = 95%.
    nuevo = _censo(s, 2, lambda i: f"/hotel/h{i}/" if i >= 2 else f"/otra/h{i}")
    sin = _diff(api, viejo, nuevo).json()
    assert sin["urls"]["en_ambos"] == 0
    r = _diff(api, viejo, nuevo, map_rutas=json.dumps(MIGRACION)).json()
    m = r["mapeo_rutas"]
    assert (m["emparejadas"], m["urls_del_anterior"]) == (N - 2, N)
    assert m["pct_emparejadas"] == 95.0
    assert m["reglas"][0]["reescritas"] == N
    assert r["urls"]["desaparecidas"] == 2
    # Lo mismo pidiendolo desde el otro censo: el mapeo va sobre el ANTERIOR.
    r2 = _diff(api, nuevo, viejo, map_rutas=json.dumps(MIGRACION)).json()
    assert r2["mapeo_rutas"]["emparejadas"] == N - 2


@pytest.mark.parametrize("valor, texto", [
    ('[["^/hoteles/(", "/hotel/"]]', "no compila"),
    ('[["^/hoteles/(.*)$", "/hotel/\\\\2"]]', "reemplazo"),
    ("no es json", "lista JSON"),
])
def test_una_regex_rota_es_un_422_que_dice_cual(entorno, valor, texto):
    api, Sesion = entorno
    s = Sesion()
    a, b = _censo(s, 1, lambda i: f"/h{i}"), _censo(s, 2, lambda i: f"/h{i}")
    resp = _diff(api, a, b, map_rutas=valor)
    assert resp.status_code == 422
    assert texto in resp.json()["detail"]


def test_las_incidencias_nuevas_se_separan_por_url(entorno):
    api, Sesion = entorno
    s = Sesion()
    viejo = _censo(s, 1, lambda i: f"/h{i}")
    nuevo = _censo(s, 2, lambda i: f"/h{i}", con_regresion=True)
    inc = _diff(api, viejo, nuevo).json()["incidencias"]
    assert inc["comparable"]
    assert inc["nuevas"]["total"] == 2
    assert inc["nuevas"]["en_url_existente"]["total"] == 1
    assert inc["nuevas"]["en_url_existente"]["ejemplos"] == ["https://x.com/h0"]
    assert inc["nuevas"]["en_url_nueva"]["ejemplos"] == ["https://x.com/nueva"]


def test_un_json_ld_que_google_descarta_es_un_tipo_perdido(entorno):
    """Una coma final hace que Google tire el bloque entero (decision 80):
    para el resultado enriquecido es como si el marcado no estuviera, aunque
    el lector permisivo saque su tipo."""
    api, Sesion = entorno
    s = Sesion()
    viejo = _censo(s, 1, lambda i: f"/h{i}")
    nuevo = _censo(s, 2, lambda i: f"/h{i}", producto_roto=True)
    r = _diff(api, viejo, nuevo).json()
    assert r["cambios"]["sd_tipos"]["total"] == N
    assert r["cambios"]["sd_tipos"]["ejemplos"][0]["pierde"] == ["Hotel"]
    al = {a["regla"]: a for a in r["alertas"]}
    assert al["pierden_datos_estructurados"]["paginas"] == N
