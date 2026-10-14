"""El analisis se niega a correr sobre un censo con los enlaces archivados
(decision 95): sin filas en `links` pondria a cero los entrantes, el PageRank y
la profundidad, y el censo entero saldria huerfano. Aparte del resto de
test_archivo_enlaces.py porque necesita las dependencias del analisis, que la
imagen de la API no tiene.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("pyarrow")

from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from analysis.analyzer import EnlacesArchivados, SEOAnalyzer  # noqa: E402
from shared import archivo_enlaces  # noqa: E402
from shared.models import Base, Job, Link, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def test_el_analisis_se_niega_sobre_un_censo_archivado(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    engine = create_engine(f"sqlite:///{tmp_path}/a.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    hace = datetime.now(timezone.utc) - timedelta(days=30)
    job = Job(id=uuid.uuid4(), name="censo", status="completed", seeds=["https://x.com/"],
              config={}, started_at=hace, completed_at=hace)
    s.add(job)
    s.flush()
    u = Url(job_id=job.id, url="https://x.com/", url_hash="h", is_internal=True,
            is_html=True, status_code=200)
    s.add(u)
    s.flush()
    s.add(Link(job_id=job.id, from_url_id=u.id, to_url=u.url, to_url_hash="h",
               is_internal=True, follow=True))
    s.commit()

    archivo_enlaces.archivar(s, job.id)
    with pytest.raises(EnlacesArchivados, match="--restaurar"):
        SEOAnalyzer(s, job.id).run_all()

    # Restaurado, el freno desaparece (el run_all entero necesita Postgres)
    archivo_enlaces.restaurar(s, job.id)
    assert SEOAnalyzer(s, job.id)._enlaces_archivados is None
    s.close()
