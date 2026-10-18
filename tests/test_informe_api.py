"""El informe para el cliente (Ola 6.2, decision 138), EJECUTADO contra SQLite:
junta nota, cifras, prioridades y comparacion, y dice cuando no se puede leer
como el sitio entero."""

from __future__ import annotations

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
from shared.models import Base, Client, Issue, Job, Url  # noqa: E402


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
    c = Client(id=uuid.uuid4(), nombre="Acme", slug="acme", dominios=[], config_defecto={},
               integraciones=[])
    s.add(c)
    s.flush()

    def censo(nombre, motivo="finished", **kw):
        j = Job(id=uuid.uuid4(), name=nombre, status="completed", finish_reason=motivo,
                seeds=["https://www.x.com/blog/", "https://www.x.com/"], config={},
                cliente_id=c.id, **kw)
        s.add(j)
        s.flush()
        home = Url(job_id=j.id, url="https://www.x.com/", url_hash="h", host="www.x.com",
                   is_internal=True, is_html=True, status_code=200, status_group="2xx",
                   indexable=True, pagerank_score=100, click_depth=0, in_sitemap=True)
        tag = Url(job_id=j.id, url="https://www.x.com/tag/a", url_hash="t", host="www.x.com",
                  is_internal=True, is_html=True, status_code=200, status_group="2xx",
                  indexable=True, pagerank_score=5, click_depth=6, in_sitemap=False)
        s.add_all([home, tag])
        s.flush()
        s.add_all([
            Issue(job_id=j.id, url_id=home.id, issue_type="title_too_long", severity="warning", details={}),
            Issue(job_id=j.id, url_id=tag.id, issue_type="missing_h1", severity="error", details={}),
            Issue(job_id=j.id, url_id=home.id, issue_type="missing_csp", severity="info",
                  details={"paginas_afectadas": 2}),
        ])
        return j

    entero = censo("entero")
    cortado = censo("cortado", motivo="max_urls_reached",
                    js_check={"grafo_fiable": False})
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
    return TestClient(app), entero.id, cortado.id


def test_el_informe_junta_nota_cifras_y_prioridades(entorno):
    api, entero, _ = entorno
    d = api.get(f"/api/jobs/{entero}/informe").json()
    assert d["cliente"]["nombre"] == "Acme"
    assert d["job"]["sitio"] == "https://www.x.com"        # la raiz, no la primera semilla
    assert 0 <= d["nota"]["global"] <= 100 and d["nota"]["categorias"]
    assert d["cifras"]["paginas_html"] == 2 and d["cifras"]["indexables"] == 2
    tipos = [r["issue_type"] for r in d["prioridades"]]
    assert set(tipos) == {"title_too_long", "missing_h1", "missing_csp"}
    assert [r["prioridad"] for r in d["prioridades"]] == sorted(
        (r["prioridad"] for r in d["prioridades"]), reverse=True)
    csp = next(r for r in d["prioridades"] if r["issue_type"] == "missing_csp")
    assert csp["agregadas"] == 1 and csp["por_segmento"] is None
    assert d["fiabilidad"] == []
    assert d["comparacion"] is not None


def test_un_censo_cortado_o_sin_los_enlaces_de_js_lo_dice(entorno):
    api, _, cortado = entorno
    d = api.get(f"/api/jobs/{cortado}/informe?prioridades=1").json()
    assert len(d["prioridades"]) == 1 and d["tipos_de_incidencia"] == 3
    assert any("máximo de URLs" in t for t in d["fiabilidad"])
    assert any("JavaScript" in t for t in d["fiabilidad"])


def test_un_job_que_no_existe_es_404(entorno):
    api, *_ = entorno
    assert api.get(f"/api/jobs/{uuid.uuid4()}/informe").status_code == 404
