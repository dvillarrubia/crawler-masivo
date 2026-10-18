"""La ficha de una URL dice de que fichero(s) de sitemap viene y con que
lastmod, y el backup lleva `sitemap_entries` y `jobs.sitemaps` (decision 126).
EJECUTADO con TestClient y SQLite."""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import Base, Job, SitemapEntry, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


A = "https://x.com/sitemap-paginas.xml"
B = "https://x.com/sitemap-completo.xml"


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    import shared.database
    from api import backup
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    with Sesion() as s:
        job = Job(id=uuid.uuid4(), name="censo", status="completed", seeds=["https://x.com/"],
                  config={}, sitemaps={"ficheros": [{"url": A, "status": 200}],
                                       "entradas_recortadas": False})
        s.add(job)
        s.flush()
        dos = Url(job_id=job.id, url="https://x.com/dos", url_hash="hdos", host="x.com",
                  path="/dos", is_internal=True, is_html=True, status_code=200, in_sitemap=True)
        fuera = Url(job_id=job.id, url="https://x.com/fuera", url_hash="hfuera", host="x.com",
                    path="/fuera", is_internal=True, is_html=True, status_code=200,
                    in_sitemap=False)
        s.add_all([dos, fuera])
        s.flush()
        s.add_all([
            SitemapEntry(job_id=job.id, url_hash="hdos", url=dos.url, fichero=B,
                         lastmod="2026-02-01"),
            SitemapEntry(job_id=job.id, url_hash="hdos", url=dos.url, fichero=A,
                         lastmod="2026-01-01"),
            # Del sitemap y sin rastrear: tiene fila aqui y no en `urls`.
            SitemapEntry(job_id=job.id, url_hash="hnueva", url="https://x.com/nueva", fichero=A),
        ])
        s.commit()
        ids = {"job": job.id, "dos": dos.id, "fuera": fuera.id}

    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    monkeypatch.setattr(backup, "SessionLocal", Sesion)
    return TestClient(app), Sesion, ids


def test_la_ficha_dice_de_que_ficheros_viene(entorno):
    api, _, ids = entorno
    d = api.get(f"/api/jobs/{ids['job']}/urls/{ids['dos']}").json()
    assert d["sitemaps"] == [{"fichero": B, "lastmod": "2026-02-01"},
                             {"fichero": A, "lastmod": "2026-01-01"}]
    assert api.get(f"/api/jobs/{ids['job']}/urls/{ids['fuera']}").json()["sitemaps"] == []


def test_el_backup_lleva_las_entradas_y_el_resumen(entorno):
    from api import backup

    _, Sesion, ids = entorno
    datos = b"".join(backup.stream_backup_zip(ids["job"]))
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        assert len(z.read("sitemap_entries.jsonl").splitlines()) == 3
    with Sesion() as s:
        r = backup.import_backup_zip(io.BytesIO(datos), False, s)
        nuevo = uuid.UUID(str(r["new_job_id"]))
        assert r["rows_imported"]["sitemap_entries"] == 3
    with Sesion() as s:
        filas = sorted((e.url_hash, e.fichero) for e in
                       s.query(SitemapEntry).filter(SitemapEntry.job_id == nuevo))
        assert filas == sorted([("hdos", A), ("hdos", B), ("hnueva", A)])
        assert s.get(Job, nuevo).sitemaps["ficheros"][0]["url"] == A
