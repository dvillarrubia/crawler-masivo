"""Retencion de censos por perfil contra Postgres (decision 223).

Las URLs arrastran sus hijas por ON DELETE CASCADE, que SQLite no hace. Se
salta sin una BD de pruebas:

    PAGERANK_TEST_DATABASE_URL=postgresql+psycopg2://crawler:crawler@postgres:5432/crawler_test pytest tests/test_retencion_db.py

NUNCA apuntarlo a la BD de trabajo: crea tablas si faltan.

El perfil conserva 2 censos. Sus jobs, del mas viejo al mas nuevo:

    f0 failed | c1 completed | c2 completed (referencia de una programacion
    activa) | c3 completed | c4 completed | c5 completed | f6 failed

Se vacian f0, c1 y c3; se quedan c2 (referencia), c4 y c5 (los dos mas
recientes completados) y f6 (mas nuevo que ellos).
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.models import (  # noqa: E402
    Base, Client, CrawlProfile, HtmlMeta, Issue, Job, Link, ProfileRun, Programacion, Url,
)

CONFIG = {"max_depth": 5, "retencion": {"activo": True, "conservar": 2}}


@pytest.fixture()
def db():
    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.rollback()
    s.close()
    engine.dispose()


def _job(db, perfil, nombre, status, hace_dias, programacion=None):
    j = Job(id=uuid.uuid4(), name=nombre, status=status, seeds=["https://x.com/"],
            config=perfil.config, cliente_id=perfil.cliente_id, profile_id=perfil.id,
            programacion_id=programacion.id if programacion else None,
            created_at=datetime.now(timezone.utc) - timedelta(days=hace_dias),
            started_at=datetime.now(timezone.utc) - timedelta(days=hace_dias),
            finish_reason="finished")
    db.add(j)
    db.flush()
    paginas = []
    for ruta in ("/", "/a"):
        u = Url(job_id=j.id, url="https://x.com" + ruta, url_hash=f"{j.id}{ruta}", host="x.com",
                path=ruta, scheme="https", is_internal=True, is_html=True, crawl_depth=1,
                status_code=200, status_group="2xx")
        db.add(u)
        db.flush()
        db.add(HtmlMeta(url_id=u.id, title=f"t {ruta}"))
        paginas.append(u)
    db.add(Issue(job_id=j.id, url_id=paginas[1].id, issue_type="missing_h1", severity="warning"))
    db.add(Link(job_id=j.id, from_url_id=paginas[0].id, to_url="https://x.com/a",
                to_url_hash=f"{j.id}/a", is_internal=True, follow=True, repeticiones=1))
    db.flush()
    return j


@pytest.fixture()
def perfil(db):
    cliente = Client(id=uuid.uuid4(), nombre="X", slug=f"x-{uuid.uuid4().hex[:8]}")
    db.add(cliente)
    db.flush()
    p = CrawlProfile(id=uuid.uuid4(), cliente_id=cliente.id, nombre="Sitio",
                     seeds=["https://x.com/"], config=CONFIG)
    prog = Programacion(id=uuid.uuid4(), nombre="x (mensual)", profile_id=p.id,
                        cliente_id=cliente.id, hereda_perfil=True, seeds=[],
                        cron="0 3 1 * *", activa=True)
    db.add_all([p, prog])
    db.flush()
    jobs = {
        "f0": _job(db, p, "f0", "failed", 70),
        "c1": _job(db, p, "c1", "completed", 60),
        "c2": _job(db, p, "c2", "completed", 50, programacion=prog),
        "c3": _job(db, p, "c3", "completed", 40),
        "c4": _job(db, p, "c4", "completed", 30),
        "c5": _job(db, p, "c5", "completed", 20),
        "f6": _job(db, p, "f6", "failed", 10),
    }
    db.commit()
    yield p, prog, jobs
    for j in jobs.values():
        db.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": str(j.id)})
    db.delete(prog)
    db.delete(p)
    db.delete(cliente)
    db.commit()


def _filas(db, job):
    jid = job.id
    return {
        "urls": db.query(Url).filter(Url.job_id == jid).count(),
        "issues": db.query(Issue).filter(Issue.job_id == jid).count(),
        "links": db.query(Link).filter(Link.job_id == jid).count(),
        "html_meta": db.execute(text("SELECT count(*) FROM html_meta m JOIN urls u ON u.id = m.url_id"
                                     " WHERE u.job_id = :j"), {"j": str(jid)}).scalar(),
    }


def test_vacia_los_sobrantes_y_conserva_su_evolucion(db, perfil):
    from shared import retencion

    p, _, jobs = perfil
    resumen = retencion.aplicar(db, jobs["f6"].id)
    db.expire_all()
    vaciados = {n for n, j in jobs.items() if db.get(Job, j.id).status == "vaciado"}
    assert vaciados == {"f0", "c1", "c3"}
    assert {v["job_id"] for v in resumen["vaciados"]} == {str(jobs[n].id) for n in vaciados}
    for n in vaciados:
        assert _filas(db, jobs[n]) == {"urls": 0, "issues": 0, "links": 0, "html_meta": 0}, n
    for n in ("c2", "c4", "c5", "f6"):
        assert _filas(db, jobs[n]) == {"urls": 2, "issues": 1, "links": 1, "html_meta": 2}, n
    # Los completados vaciados conservan su punto, con las cifras de antes.
    for n in ("c1", "c3"):
        fila = db.query(ProfileRun).filter(ProfileRun.job_id == jobs[n].id).one()
        assert fila.urls == 2 and fila.urls_2xx == 2, n


def test_la_evolucion_sigue_pintando_los_vaciados(db, perfil):
    from shared import evolucion, retencion

    p, _, jobs = perfil
    retencion.aplicar(db, jobs["f6"].id)
    datos = evolucion.leer(db, p.cliente_id, profile_id=p.id)
    puntos = {pt["name"]: pt for serie in datos["series"] for pt in serie["puntos"]}
    assert puntos["c1"]["status"] == "vaciado" and puntos["c1"]["comparable"] is True
    assert puntos["c1"]["urls"] == 2
    # Y nadie la rehace con ceros.
    assert evolucion.registrar(db, jobs["c1"].id) is None
    assert db.query(ProfileRun).filter(ProfileRun.job_id == jobs["c1"].id).one().urls == 2


def test_lo_vaciado_no_es_completed_para_nadie(db, perfil):
    """La base del incremental y la referencia de la comparacion buscan
    `completed`: un vaciado no lo es aunque sea el unico con estado
    incremental."""
    from shared import retencion
    from shared.incremental import _base_del_perfil

    p, _, jobs = perfil
    db.get(Job, jobs["c3"].id).incremental = {"modo": "completo"}
    db.commit()
    retencion.aplicar(db, jobs["f6"].id)
    assert db.query(Job).filter(Job.profile_id == p.id, Job.status == "completed").count() == 3
    assert _base_del_perfil(db, db.get(Job, jobs["f6"].id)) is None


def test_apagada_o_sin_sobrantes_no_toca_nada(db, perfil):
    from shared import retencion

    p, _, jobs = perfil
    p.config = {**CONFIG, "retencion": {"activo": False, "conservar": 2}}
    db.commit()
    assert retencion.aplicar(db, jobs["f6"].id) is None
    p.config = {**CONFIG, "retencion": {"activo": True, "conservar": 8}}
    db.commit()
    assert retencion.aplicar(db, jobs["f6"].id)["vaciados"] == []
    db.expire_all()
    assert all(db.get(Job, j.id).status != "vaciado" for j in jobs.values())


def test_a_medias_se_termina_en_la_siguiente_pasada(db, perfil, monkeypatch):
    """Sin tiempo, el job queda `vaciado` con filas, y vuelve a salir en
    `a_vaciar` hasta que no le queda ninguna. Aplicarla otra vez no hace mas."""
    from shared import retencion

    p, _, jobs = perfil
    monkeypatch.setattr(retencion, "TROZO", 1)
    r = retencion.vaciar(db, db.get(Job, jobs["c1"].id), hasta=time.monotonic() - 1)
    assert r["vaciado"] is False
    db.expire_all()
    assert db.get(Job, jobs["c1"].id).status == "vaciado"
    assert _filas(db, jobs["c1"])["urls"] == 2
    assert jobs["c1"].id in {j.id for j in retencion.a_vaciar(db, p.id, 2)}
    monkeypatch.setattr(retencion, "TROZO", 200_000)
    retencion.aplicar(db, jobs["f6"].id)
    assert _filas(db, jobs["c1"]) == {"urls": 0, "issues": 0, "links": 0, "html_meta": 0}
    assert retencion.a_vaciar(db, p.id, 2) == []
    assert retencion.aplicar(db, jobs["f6"].id)["vaciados"] == []


def test_el_worker_no_falla_si_la_retencion_revienta(monkeypatch):
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "crawler"))
    import worker
    from shared import retencion

    def revienta(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(retencion, "aplicar", revienta)
    worker._aplicar_retencion(str(uuid.uuid4()))  # no lanza
