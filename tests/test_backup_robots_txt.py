"""El backup lleva el robots.txt de cada host y la importacion lo devuelve
colgando del job NUEVO (decisiones 112 y 113); borrar el job lo quita. Contra
SQLite."""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.models import Base, Job, RobotsTxt, Url  # noqa: E402
from shared.robots_txt import fila_de_respuesta  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar(tmp_path, monkeypatch):
    import shared.database
    from api import backup

    engine = create_engine(f"sqlite:///{tmp_path}/b.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    monkeypatch.setattr(backup, "SessionLocal", Sesion)
    with Sesion() as s:
        job = Job(id=uuid.uuid4(), name="censo", status="completed",
                  seeds=["https://x.com/"], config={})
        s.add(job)
        s.flush()
        s.add(Url(job_id=job.id, url="https://x.com/", url_hash="h", host="x.com", path="/",
                  status_code=200, status_group="2xx", bloqueada_googlebot=False))
        s.add(RobotsTxt(job_id=job.id, **fila_de_respuesta(
            "https://x.com/robots.txt", status_code=200,
            cabeceras={"content-type": "text/plain"}, cuerpo=b"User-agent: *\nAllow: /\n")))
        s.add(RobotsTxt(job_id=job.id, **{**fila_de_respuesta(
            "https://b.x.com/robots.txt", status_code=503, intentos=3),
            "comprobacion": {"status_code": 503, "error": None}}))
        s.commit()
    return Sesion, job.id


def test_ida_y_vuelta(tmp_path, monkeypatch):
    from api import backup

    Sesion, jid = _montar(tmp_path, monkeypatch)
    datos = b"".join(backup.stream_backup_zip(jid))
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        assert len(z.read("robots_txt.jsonl").splitlines()) == 2
    with Sesion() as s:
        nuevo = uuid.UUID(str(backup.import_backup_zip(io.BytesIO(datos), False, s)["new_job_id"]))
    with Sesion() as s:
        filas = {(f.host, f.status_code, f.intentos, f.cuerpo, (f.comprobacion or {}).get("status_code"))
                 for f in s.query(RobotsTxt).filter(RobotsTxt.job_id == nuevo)}
        assert s.query(Url.bloqueada_googlebot).filter(Url.job_id == nuevo).scalar() is False
    assert filas == {("x.com", 200, 1, "User-agent: *\nAllow: /\n", None),
                     ("b.x.com", 503, 3, None, 503)}


def test_borrar_job_se_lleva_su_robots_txt(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from api.routers import jobs

    Sesion, jid = _montar(tmp_path, monkeypatch)
    monkeypatch.setattr(jobs, "get_redis", lambda: None)
    with Sesion() as s:
        r = jobs.borrar_job(s, s.get(Job, jid))
        assert r["filas"]["robots_txt"] == 2
    with Sesion() as s:
        assert s.query(RobotsTxt).count() == 0
