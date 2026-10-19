"""El analisis y los enlaces archivados (decisiones 95 y 150): `run_all` se
niega sobre un censo archivado —sin filas en `links` pondria a cero los
entrantes, el PageRank y la profundidad—, y `run_analysis` lo restaura, lo
analiza y lo vuelve a archivar. Aparte del resto de
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


@pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL (COPY y candados)")
def test_reanalizar_un_censo_archivado_da_lo_mismo_y_lo_deja_archivado(tmp_path, monkeypatch):
    """Decision 150: cada censo se archiva al acabar su analisis, asi que todo
    re-analisis (a mano o el de la referencia, decision 93) cae sobre uno
    archivado. `run_analysis` restaura con COPY, analiza y vuelve a archivar:
    las cifras del grafo salen identicas a las del analisis con los enlaces en
    `links`, el texto raro de las anclas vuelve tal cual y `links` se queda
    vacia para el job."""
    import analysis.analyzer as az
    from sqlalchemy import func, select, text

    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    monkeypatch.setattr("shared.database.engine", engine, raising=False)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr(az, "SessionLocal", Sesion)
    s = Sesion()
    hace = datetime.now(timezone.utc) - timedelta(days=30)
    job = Job(id=uuid.uuid4(), name="archivado", status="completed",
              seeds=["https://x.com/"], config={}, started_at=hace, completed_at=hace)
    s.add(job)
    s.flush()
    urls = {}
    for ruta_ in ("/", "/a", "/b", "/c"):
        u = Url(job_id=job.id, url=f"https://x.com{ruta_}", url_hash=f"h{ruta_}",
                is_internal=True, is_html=True, status_code=200, crawl_depth=0)
        s.add(u)
        s.flush()
        urls[ruta_] = u
    raras = ["tab\taqui", "salto\nde linea", "barra \\N y \\t", None, "ñandú «comillas» \"x\""]
    aristas = [("/", "/a"), ("/", "/b"), ("/a", "/b"), ("/b", "/"), ("/b", "/c")]
    for i, (de, a) in enumerate(aristas):
        s.add(Link(job_id=job.id, from_url_id=urls[de].id, to_url=urls[a].url,
                   to_url_hash=urls[a].url_hash, is_internal=True, follow=True,
                   anchor_text=raras[i], link_position="content",
                   repeticiones=2 if i == 0 else 1))
    s.commit()

    def foto():
        s.expire_all()
        return {u.url: (u.inlinks_count, u.unique_inlinks_count, u.outlinks_count,
                        u.click_depth, u.pagerank_score)
                for u in s.query(Url).filter(Url.job_id == job.id)}

    def filas_en_links():
        return s.execute(select(func.count()).select_from(Link)
                         .where(Link.job_id == job.id)).scalar()

    try:
        az.run_analysis(str(job.id))
        antes = foto()
        assert antes["https://x.com/b"][0] == 2, "el analisis de verdad ha corrido"
        anclas = sorted(map(repr, (r.anchor_text for r in s.query(Link)
                                   .filter(Link.job_id == job.id))))

        archivo_enlaces.archivar(s, job.id)
        assert filas_en_links() == 0
        s.query(Url).filter(Url.job_id == job.id).update(
            {"inlinks_count": None, "pagerank_score": None, "click_depth": None})
        s.commit()

        az.run_analysis(str(job.id))

        assert foto() == antes
        s.refresh(job)
        assert job.enlaces_archivados, "vuelve a quedar archivado"
        assert filas_en_links() == 0, "y `links` vacia para el job"
        assert sorted(map(repr, (r["anchor_text"] for r in archivo_enlaces.recorrer(job.id)))) == anclas
        assert sum(r["repeticiones"] for r in archivo_enlaces.recorrer(job.id)) == 6
    finally:
        s.rollback()
        for t in ("issues", "links", "urls"):
            s.execute(text(f"DELETE FROM {t} WHERE job_id = :j"), {"j": job.id})
        s.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": job.id})
        s.commit()
        s.close()
        engine.dispose()
