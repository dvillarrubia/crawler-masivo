"""El backup lleva los enlaces no rastreables y la importacion los devuelve
colgando de las URLs del censo NUEVO (decision 106). Contra SQLite."""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.models import Base, EnlaceNoRastreable, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def test_ida_y_vuelta_con_los_url_id_nuevos(tmp_path, monkeypatch):
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
        for ruta in ("/a", "/b"):
            u = Url(job_id=job.id, url=f"https://x.com{ruta}", url_hash=f"h{ruta}",
                    host="x.com", path=ruta, is_internal=True, is_html=True,
                    status_code=200, status_group="2xx")
            s.add(u)
            s.flush()
            s.add(EnlaceNoRastreable(job_id=job.id, from_url_id=u.id,
                                     crawlability="href_fuera_de_a", etiqueta="span",
                                     href="/oculta", destino="https://x.com/oculta",
                                     destino_interno=True, destino_hash="h/oculta",
                                     anchor_text=f"desde {ruta}", link_position="content",
                                     repeticiones=2))
        s.commit()
        jid = job.id

    datos = b"".join(backup.stream_backup_zip(jid))
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        assert len(z.read("enlaces_no_rastreables.jsonl").splitlines()) == 2
    with Sesion() as s:
        nuevo = uuid.UUID(str(backup.import_backup_zip(io.BytesIO(datos), False, s)["new_job_id"]))
    with Sesion() as s:
        filas = {(u.path, e.anchor_text, e.repeticiones, e.destino_hash)
                 for e, u in s.query(EnlaceNoRastreable, Url)
                 .join(Url, Url.id == EnlaceNoRastreable.from_url_id)
                 .filter(EnlaceNoRastreable.job_id == nuevo)}
        assert all(u_job == nuevo for (u_job,) in s.query(Url.job_id).join(
            EnlaceNoRastreable, EnlaceNoRastreable.from_url_id == Url.id).filter(
                EnlaceNoRastreable.job_id == nuevo))
    assert filas == {("/a", "desde /a", 2, "h/oculta"), ("/b", "desde /b", 2, "h/oculta")}
