"""`/stats`, `/issues` y `/links` EJECUTADOS con TestClient y SQLite.

Las tarjetas de la ficha (UX-13 de docs/AUDITORIA_UX.md) leen `paginas_html`,
`indexables` y `no_indexables`: tienen que contar solo paginas HTML internas
con 2xx, que es donde la indexabilidad significa algo, y dejar fuera lo que
aun no se ha analizado (`indexable` NULL). Problemas va por gravedad (UX-04) y
Enlaces por destino (UX-02).
"""

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
from shared.models import Base, Issue, Job, Link, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _url(job, n, **kw):
    d = dict(job_id=job.id, url=f"https://x.com/{n}", url_hash=f"h{n}", host="x.com",
             is_internal=True, is_html=True, status_code=200, status_group="2xx")
    d.update(kw)
    return Url(**d)


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
    urls = [
        _url(job, 1, indexable=True),
        _url(job, 2, indexable=True),
        _url(job, 3, indexable=False),                       # noindex o canonical fuera
        _url(job, 4, indexable=None),                        # sin analizar: no cuenta
        _url(job, 5, status_code=301, status_group="3xx", indexable=False),  # salto: no es pagina
        _url(job, 6, is_html=False, indexable=True),         # PDF: no es HTML
        _url(job, 7, is_internal=False, indexable=True),     # externa
    ]
    s.add_all(urls)
    s.flush()
    s.add_all([
        Issue(job_id=job.id, url_id=urls[0].id, issue_type="noindex_page", severity="info", details={}),
        Issue(job_id=job.id, url_id=urls[1].id, issue_type="title_too_long", severity="warning", details={}),
        Issue(job_id=job.id, url_id=urls[2].id, issue_type="canonical_cross_domain", severity="error", details={}),
    ])
    s.add_all([
        Link(job_id=job.id, from_url_id=urls[0].id, to_url="https://x.com/b", to_url_hash="b", is_internal=True),
        Link(job_id=job.id, from_url_id=urls[1].id, to_url="https://x.com/a", to_url_hash="a", is_internal=True),
        Link(job_id=job.id, from_url_id=urls[2].id, to_url="https://x.com/b", to_url_hash="b", is_internal=True),
    ])
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


def test_las_tarjetas_cuentan_solo_paginas_html_internas_con_2xx(entorno):
    api, jid = entorno
    d = api.get(f"/api/jobs/{jid}/stats").json()
    assert d["total_urls"] == 7
    assert d["paginas_html"] == 4          # 1, 2, 3 y 4
    assert d["indexables"] == 2            # 1 y 2
    assert d["no_indexables"] == 1         # 3; la 4 esta sin analizar


def test_problemas_salen_por_gravedad(entorno):
    api, jid = entorno
    items = api.get(f"/api/jobs/{jid}/issues").json()["items"]
    assert [i["severity"] for i in items] == ["error", "warning", "info"]


def test_enlaces_salen_agrupados_por_destino(entorno):
    api, jid = entorno
    d = api.get(f"/api/jobs/{jid}/links").json()
    assert d["total"] == 3
    assert [l["to_url"] for l in d["items"]] == ["https://x.com/a", "https://x.com/b", "https://x.com/b"]


def test_click_depth_sin_calcular_no_es_sin_camino(entorno):
    """Un censo analizado antes de la decision 90 tiene click_depth NULL en
    todas sus URLs: la ficha no puede decir «sin camino desde la home»."""
    api, jid = entorno
    assert api.get(f"/api/jobs/{jid}/stats").json()["click_depth_calculada"] is False


def test_resumen_por_tipo_cuenta_paginas_y_no_repite_ejemplos(entorno, tmp_path):
    """La vista agrupada de Problemas (UX-04): paginas distintas aparte de
    incidencias, ejemplos sin repetir URL y de mas grave a menos."""
    api, jid = entorno
    from shared.models import Issue as I
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    s = sessionmaker(bind=create_engine(f"sqlite:///{tmp_path}/s.db"))()
    u1 = s.query(Url).filter(Url.url == "https://x.com/1").one()
    # Un hreflang sin retorno por idioma: tres incidencias, una pagina.
    for lang in ("en", "de", "fr"):
        s.add(I(job_id=u1.job_id, url_id=u1.id, issue_type="hreflang_missing_return",
                severity="warning", details={"lang": lang}))
    s.commit()
    s.close()
    r = api.get(f"/api/jobs/{jid}/issues/resumen").json()
    assert [x["severity"] for x in r] == sorted([x["severity"] for x in r], key=["error", "warning", "info"].index)
    h = next(x for x in r if x["issue_type"] == "hreflang_missing_return")
    assert h["incidencias"] == 3 and h["paginas"] == 1
    assert len(h["ejemplos"]) == 1
