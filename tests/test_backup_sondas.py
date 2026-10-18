"""El backup lleva las respuestas sondeadas y la importacion las cuelga del
censo NUEVO (decisiones 123 y 124). No tienen URL: solo se remapea el job.
Contra SQLite."""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.models import Base, Job, RespuestaSondeada, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def test_ida_y_vuelta_con_el_job_nuevo(tmp_path, monkeypatch):
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
        s.add(Url(job_id=job.id, url="https://x.com/", url_hash="h", host="x.com",
                  path="/", is_internal=True, is_html=True, status_code=200))
        s.add(RespuestaSondeada(job_id=job.id, url_hash="e1", url="https://otro.com/x",
                                host="otro.com", uso="enlace", interno=False, metodo="GET",
                                status_code=404, veredicto="roto"))
        s.add(RespuestaSondeada(job_id=job.id, url_hash="e2", url="https://muerto.com/",
                                host="muerto.com", uso="enlace", interno=False,
                                veredicto="dominio_inexistente", host_caido=True))
        s.commit()
        jid = job.id

    datos = b"".join(backup.stream_backup_zip(jid))
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        assert len(z.read("respuestas_sondeadas.jsonl").splitlines()) == 2
    with Sesion() as s:
        r = backup.import_backup_zip(io.BytesIO(datos), False, s)
        nuevo = uuid.UUID(str(r["new_job_id"]))
        assert r["rows_imported"]["respuestas_sondeadas"] == 2
    with Sesion() as s:
        filas = {(f.url_hash, f.veredicto, f.host_caido) for f in
                 s.query(RespuestaSondeada).filter(RespuestaSondeada.job_id == nuevo)}
    assert filas == {("e1", "roto", None), ("e2", "dominio_inexistente", True)}
