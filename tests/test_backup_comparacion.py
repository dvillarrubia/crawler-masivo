"""Un censo importado de un backup conserva lo que la comparacion necesita
para no mentir (decision 131): el motivo de cierre (un truncado no permite
afirmar ausencias, decision 67), las versiones (66, 72) y la salud (103).
Antes se perdian, y el censo de Lopesan parado en 8.765 URLs por `max_urls`
se comparaba como entero. Contra SQLite."""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.comparacion import comparar_censos, paginas_de_censo, resumen_de_job  # noqa: E402
from shared.models import Base, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def test_un_censo_truncado_sigue_truncado_al_importarlo(tmp_path, monkeypatch):
    import shared.database
    from api import backup

    engine = create_engine(f"sqlite:///{tmp_path}/b.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    monkeypatch.setattr(backup, "SessionLocal", Sesion)
    ids = []
    with Sesion() as s:
        for dia, motivo, rutas in ((1, "max_urls_reached", ("/a",)),
                                   (2, "finished", ("/a", "/b"))):
            job = Job(id=uuid.uuid4(), name=f"censo {dia}", status="completed",
                      finish_reason=motivo, crawler_version="c1", analisis_version="a1",
                      health={"causa": None}, seeds=["https://x.com/"], config={},
                      started_at=datetime(2026, 9, dia, tzinfo=timezone.utc))
            s.add(job)
            s.flush()
            for r in rutas:
                s.add(Url(job_id=job.id, url=f"https://x.com{r}", url_hash=r, host="x.com",
                          path=r, is_internal=True, is_html=True, status_code=200,
                          indexability_status="Indexable"))
            ids.append(job.id)
        s.commit()

    importados = []
    for jid in ids:
        datos = b"".join(backup.stream_backup_zip(jid))
        with Sesion() as s:
            r = backup.import_backup_zip(io.BytesIO(datos), False, s)
            importados.append(uuid.UUID(str(r["new_job_id"])))

    with Sesion() as s:
        viejo, nuevo = (s.get(Job, i) for i in importados)
        assert viejo.finish_reason == "max_urls_reached"
        assert (viejo.crawler_version, viejo.analisis_version) == ("c1", "a1")
        assert viejo.health == {"causa": None}
        # Y la consecuencia: la comparacion entre los dos importados no
        # afirma que falte nada ni compara el grafo.
        r = comparar_censos(paginas_de_censo(s, viejo.id), paginas_de_censo(s, nuevo.id),
                            job_a=resumen_de_job(viejo), job_b=resumen_de_job(nuevo))
    assert r["concluyente"] is False
    assert r["cambios"]["inlinks"]["total"] is None
