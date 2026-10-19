"""El censo en Excel (decision 145), EJECUTADO con TestClient y SQLite: se
abre con openpyxl y se miran las hojas, las filas y que los nombres y las
fichas salen del catalogo del frontend."""

from __future__ import annotations

import io
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
openpyxl = pytest.importorskip("openpyxl")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from api.excel import catalogo  # noqa: E402
from shared.database import get_session  # noqa: E402
from shared.models import Base, HtmlMeta, Issue, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def test_el_catalogo_se_lee_del_frontend():
    nombres, fichas = catalogo()
    assert len(fichas) > 100 and len(nombres) > 100
    assert fichas["4xx_error"]["arreglo"].startswith("Si el contenido existe")
    assert nombres["h1_missing"]


@pytest.fixture
def api(tmp_path):
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/x.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    j = Job(id=uuid.uuid4(), name="acme semanal", status="completed", seeds=["https://www.x.com/"], config={},
            gsc_resumen={"fuera_del_censo": [{"url": "https://www.x.com/vieja", "clics": 9, "impresiones": 90}]})
    s.add(j)
    s.flush()
    for i in range(2500):
        u = Url(job_id=j.id, url=f"https://www.x.com/p{i}", url_hash=f"h{i}", is_internal=True, is_html=True,
                status_code=404 if i < 3 else 200, status_group="4xx" if i < 3 else "2xx",
                indexable=i >= 3, gsc_clics=i % 7)
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title=f"Página {i}"))
        if i < 3:
            s.add(Issue(job_id=j.id, url_id=u.id, issue_type="4xx_error", severity="error", details={"status_code": 404}))
        if i % 500 == 0:
            s.add(Issue(job_id=j.id, url_id=u.id, issue_type="h1_missing", severity="warning", details={}))
    s.commit()
    jid = j.id
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
    return TestClient(app), jid


def test_el_excel_tiene_sus_hojas_y_sus_filas(api):
    cliente, jid = api
    r = cliente.get(f"/api/jobs/{jid}/export.xlsx")
    assert r.status_code == 200
    assert "www.x.com-" in r.headers["content-disposition"] and r.headers["content-type"].endswith("sheet")
    wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True)
    assert wb.sheetnames == ["Resumen", "Qué arreglar", "URLs", "Incidencias", "Google sin rastrear"]
    urls = list(wb["URLs"].iter_rows(values_only=True))
    assert len(urls) == 2501 and urls[0][0] == "URL"
    assert any(f[4] == "Página 7" for f in urls[1:20])      # el titulo, de html_meta
    prio = list(wb["Qué arreglar"].iter_rows(values_only=True))
    nombres, fichas = catalogo()
    fila = next(f for f in prio[1:] if f[-1] == "4xx_error")
    assert fila[1] == nombres["4xx_error"] and fila[9] == fichas["4xx_error"]["arreglo"]
    assert len(list(wb["Incidencias"].iter_rows())) == 1 + 3 + 5
    assert list(wb["Google sin rastrear"].iter_rows(values_only=True))[1] == ("https://www.x.com/vieja", 9, 90)


def test_un_job_que_no_existe_es_404(api):
    cliente, _ = api
    assert cliente.get(f"/api/jobs/{uuid.uuid4()}/export.xlsx").status_code == 404
