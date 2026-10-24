"""El trafico de Google entre censos en la ficha del cliente (decision 165),
EJECUTADO con TestClient y SQLite: el ultimo de cada perfil, con las frases del
mismo redactor que la ficha del rastreo y el aviso de Slack."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import Base, Client, CrawlProfile, Job  # noqa: E402
from shared.trafico_entre_censos import textos  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


TRAFICO = {"comparable": True, "pendiente": False, "general": False,
           "ventana_anterior": ["2026-09-01", "2026-09-28"], "ventana_nueva": ["2026-09-29", "2026-10-26"],
           "clics_anterior": 1000, "clics_nuevo": 800, "variacion": -0.2,
           "caidas": [{"cambio": "canonical", "texto": "cambia el canonical", "paginas": 3,
                       "clics_antes": 300, "clics_despues": 100, "diferencia": -200, "ejemplos": [],
                       "plantillas": []}],
           "subidas": [], "rupturas": [], "estacional": None}


@pytest.fixture
def entorno(tmp_path):
    from api.routers import clientes

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    acme = Client(nombre="Acme", slug="acme", dominios=["x.com"])
    s.add(acme)
    s.flush()
    web = CrawlProfile(cliente_id=acme.id, nombre="web", seeds=["https://x.com/"])
    blog = CrawlProfile(cliente_id=acme.id, nombre="blog", seeds=["https://blog.x.com/"])
    rival = CrawlProfile(cliente_id=acme.id, nombre="rival", seeds=["https://rival.com/"], rol="competidor")
    s.add_all([web, blog, rival])
    s.flush()

    def job(nombre, perfil, dia, comparacion):
        j = Job(id=uuid.uuid4(), name=nombre, status="completed", seeds=["https://x.com/"], config={},
                cliente_id=acme.id, profile_id=perfil.id, comparacion=comparacion,
                created_at=datetime(2026, 10, dia, tzinfo=timezone.utc),
                started_at=datetime(2026, 10, dia, tzinfo=timezone.utc))
        s.add(j)
        return j

    job("web viejo", web, 1, {"trafico": {"comparable": False, "pendiente": False, "motivo": "viejo"}})
    nuevo = job("web nuevo", web, 20, {"comparado_con": "x", "trafico": TRAFICO})
    job("web sin trafico", web, 22, {"comparado_con": "y"})
    job("blog", blog, 21, {"trafico": {"comparable": False, "pendiente": True,
                                       "motivo": "Search Console aún no tiene datos hasta el 2026-10-19."}})
    # Uno de la competencia guardado antes de que se dejara de calcular: no sale.
    job("rival", rival, 23, {"comparado_con": "z", "trafico": TRAFICO})
    s.commit()
    ids = {"acme": acme.id, "nuevo": str(nuevo.id)}
    s.close()
    app = FastAPI()
    app.include_router(clientes.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), ids


def test_el_ultimo_trafico_de_cada_perfil_con_las_frases_del_redactor(entorno):
    cliente, ids = entorno
    r = cliente.get(f"/api/clients/{ids['acme']}/trafico")
    assert r.status_code == 200
    filas = r.json()
    # El mas reciente primero; «web sin trafico» no cuenta, el viejo de web
    # tampoco, y la competencia no sale (no esta en el Search Console del cliente).
    assert [f["job"] for f in filas] == ["blog", "web nuevo"]
    por_perfil = {f["perfil"]: f for f in filas}
    assert set(por_perfil) == {"web", "blog"}
    assert por_perfil["web"]["job_id"] == ids["nuevo"]
    assert por_perfil["web"]["textos"] == textos(TRAFICO)
    assert por_perfil["blog"]["pendiente"] is True and "aún no tiene datos" in por_perfil["blog"]["textos"][0]


def test_cliente_que_no_existe(entorno):
    cliente, _ = entorno
    assert cliente.get(f"/api/clients/{uuid.uuid4()}/trafico").status_code == 404
