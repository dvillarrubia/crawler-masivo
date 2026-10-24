"""`GET /api/jobs/{id}/wordpress` y `/wordpress/entidades` (decision 161),
contra SQLite: lo que esta en el censo se cuenta como en el analisis, por
`url_hash` o por la misma URL con el otro esquema (`url_hash_alt`)."""

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
from shared.models import AdapterEntity, Base, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture()
def api(tmp_path):
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/a.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    with Sesion() as s:
        job = Job(id=uuid.uuid4(), name="t", status="completed", seeds=["https://x.com/"],
                  config={}, adapters={"wordpress": {"detectado": True, "rest": "abierta"}})
        s.add(job)
        s.flush()
        for ruta in ("/a/", "/b/"):
            s.add(Url(job_id=job.id, url=f"https://x.com{ruta}", url_hash=f"h{ruta}",
                      host="x.com", path=ruta, is_internal=True, is_html=True, status_code=200))
        s.add_all([
            AdapterEntity(job_id=job.id, adaptador="wordpress", tipo="post", url="https://x.com/a/",
                          url_hash="h/a/"),
            # `home` en http: la del censo es la https, por el hash alternativo.
            AdapterEntity(job_id=job.id, adaptador="wordpress", tipo="post", url="http://x.com/b/",
                          url_hash="otro", url_hash_alt="h/b/"),
            AdapterEntity(job_id=job.id, adaptador="wordpress", tipo="post", url="https://x.com/c/",
                          url_hash="h/c/", url_hash_alt="h-http/c/"),
        ])
        s.commit()
        jid = job.id

    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        s = Sesion()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), jid


def test_en_el_censo_cuenta_tambien_el_otro_esquema(api):
    cliente, jid = api
    r = cliente.get(f"/api/jobs/{jid}/wordpress")
    assert r.status_code == 200, r.text
    assert r.json()["por_tipo"] == [{"tipo": "post", "leidas": 3, "en_el_censo": 2}]
    assert r.json()["resumen"]["rest"] == "abierta"


def test_solo_no_rastreadas(api):
    cliente, jid = api
    r = cliente.get(f"/api/jobs/{jid}/wordpress/entidades", params={"solo_no_rastreadas": True})
    assert [e["url"] for e in r.json()["items"]] == ["https://x.com/c/"]
    assert r.json()["total"] == 1
