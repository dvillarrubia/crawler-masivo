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


URL_BD = __import__("os").environ.get("PAGERANK_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL (advisory locks)")
def test_no_se_archiva_un_censo_que_se_esta_analizando(tmp_path, monkeypatch):
    """El re-analisis de la referencia (decision 93) y el archivado (95) a la vez.

    El censo que se re-analiza para comparar es uno SUPERADO, que es justo lo
    que se archiva; y el archivado lo lanza el cierre de cualquier rastreo, asi
    que el de otro rastreo del mismo sitio puede caer en medio. Sin candado,
    el archivado borraria `links` a mitad del analisis y los entrantes y el
    PageRank del censo saldrian a cero sin un solo error. Con el candado del
    job, que comparten los dos: mientras se analiza no se archiva nada, y
    mientras se archiva el analisis no entra.
    """
    import analysis.analyzer as az
    from analysis.analyzer import candado_de_job, run_analysis
    from sqlalchemy import func, select, text

    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    monkeypatch.setattr("shared.database.engine", engine, raising=False)
    s = sessionmaker(bind=engine)()
    hace = datetime.now(timezone.utc) - timedelta(days=30)
    job = Job(id=uuid.uuid4(), name="referencia", status="completed",
              seeds=["https://x.com/"], config={}, started_at=hace, completed_at=hace)
    s.add(job)
    s.flush()
    u = Url(job_id=job.id, url="https://x.com/", url_hash="h", is_internal=True,
            is_html=True, status_code=200)
    s.add(u)
    s.flush()
    s.add(Link(job_id=job.id, from_url_id=u.id, to_url=u.url, to_url_hash="h",
               is_internal=True, follow=True))
    s.commit()

    def filas():
        return s.execute(select(func.count()).select_from(Link)
                         .where(Link.job_id == job.id)).scalar()

    try:
        # 1) Se esta analizando: el archivado se retira sin tocar nada.
        with candado_de_job(str(job.id)) as dentro:
            assert dentro is True
            with pytest.raises(archivo_enlaces.SeEstaAnalizando):
                archivo_enlaces.archivar(s, job.id)
            s.rollback()
        assert filas() == 1, "los enlaces siguen en `links`"
        s.refresh(job)
        assert job.enlaces_archivados is None
        assert not archivo_enlaces.ruta(job.id).exists()

        # 2) Se esta archivando: el analisis no entra (ni abre sesion).
        otra = engine.connect()
        tx = otra.begin()
        clave = az._clave_candado(str(job.id))
        assert otra.execute(text("SELECT pg_try_advisory_xact_lock(:k)"),
                            {"k": clave}).scalar()
        monkeypatch.setattr(az, "SessionLocal",
                            lambda: pytest.fail("analizo un censo que se archivaba"))
        run_analysis(str(job.id))
        tx.rollback()
        otra.close()

        # 3) Suelto, archivar funciona como siempre.
        archivo_enlaces.archivar(s, job.id)
        assert filas() == 0
    finally:
        s.rollback()
        for t in ("links", "urls"):
            s.execute(text(f"DELETE FROM {t} WHERE job_id = :j"), {"j": job.id})
        s.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": job.id})
        s.commit()
        s.close()
        engine.dispose()
