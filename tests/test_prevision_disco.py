"""Si un rastreo cabe en el disco antes de lanzarlo (#281, decision 225).

El veredicto es una funcion pura; la estimacion y el 409 al lanzar, contra
Postgres (`PAGERANK_TEST_DATABASE_URL`, nunca `crawler_db`): sin Postgres no
hay bytes por fila de donde estimar y se lanza como siempre.
"""

from __future__ import annotations

import os
import uuid

import pytest

from shared import almacenamiento as A

GB = 1024 ** 3


def test_el_veredicto_guarda_la_reserva():
    total = 100 * GB
    # Reserva: el 5 % de 100 GB = 5 GB. Libres 30 -> utiles 25.
    assert A.veredicto_de_disco(26 * GB, 30 * GB, total) == "no_cabe"
    assert A.veredicto_de_disco(20 * GB, 30 * GB, total) == "justo"
    assert A.veredicto_de_disco(10 * GB, 30 * GB, total) == "cabe"
    assert A.veredicto_de_disco(None, 30 * GB, total) == "no_se_sabe"
    assert A.veredicto_de_disco(1, None, total) == "no_se_sabe"
    # Con un disco pequeno manda el minimo de 5 GB.
    assert A.veredicto_de_disco(1 * GB, 5 * GB, 10 * GB) == "no_cabe"


def test_la_frase_solo_cuando_hay_algo_que_decir():
    base = {"previstos_bytes": 40 * GB, "libre_bytes": 30 * GB, "urls_previstas": 100000,
            "por_url_bytes": 400 * 1024, "referencia": {"nombre": "Lopesan · 26-oct"}}
    assert A.frase_de_disco({**base, "veredicto": "cabe"}) is None
    assert A.frase_de_disco({**base, "veredicto": "no_se_sabe"}) is None
    frase = A.frase_de_disco({**base, "veredicto": "no_cabe"})
    assert "40.0 GB" in frase and "100.000 URLs" in frase and "Lopesan · 26-oct" in frase
    assert "No cabe" in frase


def test_sin_postgres_no_se_sabe():
    class Sqlite:
        def get_bind(self):
            import types
            return types.SimpleNamespace(dialect=types.SimpleNamespace(name="sqlite"))

    p = A.prevision_de_disco(Sqlite(), cliente_id=uuid.uuid4())
    assert p["veredicto"] == "no_se_sabe" and p["motivo"]


URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")


@pytest.fixture()
def bd():
    if not URL_BD:
        pytest.skip("sin PAGERANK_TEST_DATABASE_URL")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from shared.models import Base, Client, CrawlProfile, Job, Link, Url

    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    cli = Client(id=uuid.uuid4(), nombre="Disco", slug=f"disco-{uuid.uuid4().hex[:8]}")
    s.add(cli)
    s.flush()
    perfil = CrawlProfile(id=uuid.uuid4(), cliente_id=cli.id, nombre="Sitio",
                          seeds=["https://x.com/"], config={})
    s.add(perfil)
    s.flush()
    ref = Job(id=uuid.uuid4(), name="ref", status="completed", seeds=["https://x.com/"], config={},
              cliente_id=cli.id, profile_id=perfil.id, total_urls_crawled=50)
    s.add(ref)
    s.flush()
    for i in range(50):
        u = Url(job_id=ref.id, url=f"https://x.com/{i}", url_hash=f"{ref.id}{i}", is_internal=True,
                status_code=200, is_html=True)
        s.add(u)
        s.flush()
        for k in range(10):
            s.add(Link(job_id=ref.id, from_url_id=u.id, to_url=f"https://x.com/{k}",
                       to_url_hash=f"h{k}", repeticiones=1))
    s.commit()
    s.execute(text("ANALYZE urls; ANALYZE links"))
    s.commit()
    yield s, cli, perfil, ref
    s.rollback()
    s.execute(text("DELETE FROM jobs WHERE cliente_id = :c"), {"c": str(cli.id)})
    s.delete(perfil)
    s.delete(cli)
    s.commit()
    s.close()
    engine.dispose()


def test_estima_por_el_censo_anterior_del_perfil(bd, monkeypatch):
    s, cli, perfil, ref = bd
    monkeypatch.setattr(A, "espacio_en_disco", lambda: {"total": 100 * GB, "libre": 30 * GB})
    p = A.prevision_de_disco(s, cliente_id=cli.id, profile_id=perfil.id, max_urls=1000)
    assert p["referencia"]["job_id"] == str(ref.id) and p["referencia"]["enlaces"] == 500
    assert p["urls_previstas"] == 1000 and p["por_url_bytes"] > 0
    assert abs(p["previstos_bytes"] - p["por_url_bytes"] * 1000) < 1000
    assert p["veredicto"] == "cabe"
    # Sin tope, tantas URLs como el censo de referencia.
    assert A.prevision_de_disco(s, cliente_id=cli.id, profile_id=perfil.id)["urls_previstas"] == 50


def test_los_enlaces_archivados_cuentan(bd, monkeypatch):
    """Archivados en Parquet no estan en `links`, pero el rastreo los
    escribira ahi: cuentan por `enlaces_archivados["filas"]`."""
    from shared.models import Link

    s, cli, perfil, ref = bd
    monkeypatch.setattr(A, "espacio_en_disco", lambda: {"total": 100 * GB, "libre": 30 * GB})
    antes = A.prevision_de_disco(s, cliente_id=cli.id, profile_id=perfil.id)["previstos_bytes"]
    s.query(Link).filter(Link.job_id == ref.id).delete()
    s.get(type(ref), ref.id).enlaces_archivados = {"filas": 500, "bytes": 1}
    s.commit()
    p = A.prevision_de_disco(s, cliente_id=cli.id, profile_id=perfil.id)
    assert p["referencia"]["enlaces"] == 500 and p["previstos_bytes"] == antes


def test_lanzar_lo_que_no_cabe_da_409_salvo_forzando(bd, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import api.routers.jobs as router_jobs
    from api.main import app
    from shared.database import get_session

    s, cli, perfil, ref = bd
    monkeypatch.setattr(A, "espacio_en_disco", lambda: {"total": 100 * GB, "libre": 6 * GB})
    encolados = []
    monkeypatch.setattr(router_jobs, "encolar", lambda r, jid: encolados.append(jid))
    monkeypatch.setattr(router_jobs, "get_redis", lambda: None)
    app.dependency_overrides[get_session] = lambda: s
    try:
        c = TestClient(app)
        cuerpo = {"name": "grande", "cliente_id": str(cli.id), "profile_id": str(perfil.id),
                  "config": {"max_urls": 5_000_000}}
        r = c.post("/api/jobs", json=cuerpo)
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error"] == "no_cabe_en_disco"
        assert "No cabe" in r.json()["detail"]["mensaje"] and encolados == []
        r = c.post("/api/jobs?forzar_disco=true", json=cuerpo)
        assert r.status_code == 201, r.text
        assert len(encolados) == 1
    finally:
        app.dependency_overrides.clear()
