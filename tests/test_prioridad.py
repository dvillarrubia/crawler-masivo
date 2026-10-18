"""Prioridad de las incidencias (Ola 4.3, decision 136): gravedad x importancia
de las paginas, EJECUTADA contra SQLite por el endpoint de verdad."""

from __future__ import annotations

import uuid

import pytest

from shared.prioridad import PESO_GRAVEDAD, importancia


def test_la_importancia_va_de_0_1_a_1_y_crece_con_lo_que_debe():
    assert importancia(100, 0, True) == 1.0
    assert importancia(None, None, None) == pytest.approx(0.25 * 0.55 * 0.85)
    assert importancia(0, 9, False) >= 0.1
    assert importancia(90, 1, True) > importancia(40, 1, True)       # mas autoridad
    assert importancia(90, 1, True) > importancia(90, 5, True)       # mas cerca de la home
    assert importancia(90, 1, True) > importancia(90, 1, False)      # declarada en el sitemap
    assert importancia(90, None, True) == importancia(90, 7, True)   # sin camino = lo mas lejos
    assert importancia(250, -3, True) == 1.0                         # valores fuera de rango


def test_cada_gravedad_vale_diez_veces_la_siguiente():
    assert PESO_GRAVEDAD["error"] == 10 * PESO_GRAVEDAD["warning"] == 100 * PESO_GRAVEDAD["info"]


pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import Base, Issue, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture()
def entorno(tmp_path):
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    job = Job(id=uuid.uuid4(), name="censo", status="completed", seeds=["https://x.com/"], config={})
    s.add(job)
    s.flush()

    def url(n, pr, cd, sm, seg="x"):
        u = Url(job_id=job.id, url=f"https://x.com/{n}", url_hash=f"h{n}", host="x.com",
                is_internal=True, is_html=True, status_code=200, status_group="2xx",
                pagerank_score=pr, click_depth=cd, in_sitemap=sm, segmento=seg)
        s.add(u)
        return u

    home = url("home", 100, 0, True, "portada")
    fichas = [url(f"ficha{i}", 85, 2, True, "fichas") for i in range(4)]
    etiquetas = [url(f"tag{i}", 5, 6, False, "tags") for i in range(6)]
    s.flush()

    def issue(u, tipo, sev, **details):
        s.add(Issue(job_id=job.id, url_id=u.id, issue_type=tipo, severity=sev, details=details))

    # Un error en dos etiquetas perdidas: por gravedad va primero.
    for u in etiquetas[:2]:
        issue(u, "missing_h1", "error")
    # Un aviso en la home y las fichas: lo que hay que arreglar primero.
    for u in [home, *fichas]:
        issue(u, "title_too_long", "warning")
    # Un hreflang sin retorno emite una por idioma: la pagina cuenta una vez.
    for lang in ("en", "de", "fr"):
        issue(fichas[0], "hreflang_missing_return", "warning", lang=lang)
    # Un hallazgo de plantilla en 40 paginas, anclado en la home.
    issue(home, "image_missing_alt", "info", paginas_afectadas=40)
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


def _por_tipo(filas):
    return {f["issue_type"]: f for f in filas}


def test_por_gravedad_sigue_siendo_el_orden_de_siempre(entorno):
    api, jid = entorno
    filas = api.get(f"/api/jobs/{jid}/issues/resumen").json()
    assert filas[0]["issue_type"] == "missing_h1"
    assert all(f["prioridad"] is not None for f in filas)


def test_por_prioridad_sube_el_aviso_en_las_paginas_que_importan(entorno):
    api, jid = entorno
    filas = api.get(f"/api/jobs/{jid}/issues/resumen?orden=prioridad").json()
    orden = [f["issue_type"] for f in filas]
    assert orden.index("title_too_long") < orden.index("missing_h1")
    assert [f["prioridad"] for f in filas] == sorted((f["prioridad"] for f in filas), reverse=True)


def test_la_suma_es_la_de_la_funcion_pura_y_cada_pagina_cuenta_una_vez(entorno):
    api, jid = entorno
    t = _por_tipo(api.get(f"/api/jobs/{jid}/issues/resumen").json())
    esperado = importancia(100, 0, True) + 4 * importancia(85, 2, True)
    assert t["title_too_long"]["importancia"] == pytest.approx(esperado, abs=0.01)
    assert t["title_too_long"]["prioridad"] == pytest.approx(10 * esperado, abs=0.1)
    # Tres incidencias de la misma ficha: su importancia una sola vez.
    assert t["hreflang_missing_return"]["incidencias"] == 3
    assert t["hreflang_missing_return"]["importancia"] == pytest.approx(importancia(85, 2, True), abs=0.01)
    assert t["missing_h1"]["importancia"] == pytest.approx(2 * importancia(5, 6, False), abs=0.01)


def test_un_hallazgo_de_muchas_paginas_no_pasa_del_sitio_entero(entorno):
    """40 paginas afectadas en un censo de 11: el tope es el sitio. Sin el,
    en Lopesan `image_missing_alt` sumaba 18.527 paginas de 3.848."""
    api, jid = entorno
    t = _por_tipo(api.get(f"/api/jobs/{jid}/issues/resumen").json())
    sitio = importancia(100, 0, True) + 4 * importancia(85, 2, True) + 6 * importancia(5, 6, False)
    assert t["image_missing_alt"]["importancia"] == pytest.approx(sitio, abs=0.01)


def test_con_segmento_solo_cuentan_sus_paginas(entorno):
    api, jid = entorno
    t = _por_tipo(api.get(f"/api/jobs/{jid}/issues/resumen?segmento=fichas&orden=prioridad").json())
    assert t["title_too_long"]["importancia"] == pytest.approx(4 * importancia(85, 2, True), abs=0.01)
    # El de plantilla no se reparte por segmento (decision 125): sale con 0.
    assert t["image_missing_alt"]["importancia"] == 0
