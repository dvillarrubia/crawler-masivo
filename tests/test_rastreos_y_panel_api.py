"""Tipo de rastreo, PATCH de un job, serie del cliente y panel (UX-07/08 de
docs/AUDITORIA_UX.md), EJECUTADOS con TestClient y SQLite.

Lo que se sujeta: que el tipo deducido del nombre y el filtro SQL digan lo
mismo, que asignar cliente no toque la configuracion ni lo rastreado, que la
serie cuente filas y no el contador del job (decision 21) y que el panel
compare censos con censos, no con canarios.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.clientes import tipo_de_rastreo  # noqa: E402
from shared.database import get_session  # noqa: E402
from shared.models import Base, Client, Issue, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _job(s, nombre, dias, cliente=None, errores=0, paginas=3, **kw):
    j = Job(id=uuid.uuid4(), name=nombre, status="completed", seeds=["https://x.com/"],
            config={"max_depth": 3}, created_at=T0 + timedelta(days=dias),
            cliente_id=cliente.id if cliente else None, total_urls_crawled=1, **kw)
    s.add(j)
    s.flush()
    for n in range(paginas):
        u = Url(job_id=j.id, url=f"https://x.com/{n}", url_hash=f"{j.id}{n}", is_internal=True,
                is_html=True, status_group="2xx", status_code=200, indexable=True)
        s.add(u)
        s.flush()
        if n < errores:
            s.add(Issue(job_id=j.id, url_id=u.id, issue_type="4xx_error", severity="error"))
    return j


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    from api.routers import clientes, jobs

    engine = create_engine(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    acme = Client(nombre="Acme", slug="acme", dominios=["x.com"])
    s.add(acme)
    s.flush()
    ids = {
        "viejo": _job(s, "acme censo julio", 0, acme, errores=1).id,
        "canario": _job(s, "CANARY - acme", 5, acme, errores=3, paginas=3).id,
        "nuevo": _job(s, "acme censo octubre", 10, acme, errores=2, paginas=5).id,
        "huerfano": _job(s, "Druni - prueba de techo", 3).id,
    }
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(jobs.router)
    app.include_router(clientes.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    monkeypatch.setattr(jobs, "get_redis", lambda: None)
    return TestClient(app), Sesion, acme.id, ids


@pytest.mark.parametrize("nombre,tipo,esperado", [
    ("CANARY - progym", None, "canario"),
    ("Druni - VALIDACION conc 4", None, "prueba"),
    ("Lopesan JS - muestra de ritmo", None, "prueba"),
    ("lopesan.com (censo v4)", None, "censo"),
    ("CANARY - progym", "censo", "censo"),   # lo marcado manda
])
def test_tipo_de_rastreo(nombre, tipo, esperado):
    assert tipo_de_rastreo(nombre, tipo) == esperado


def test_el_filtro_sql_y_la_ficha_dicen_el_mismo_tipo(entorno):
    api, _, _, ids = entorno
    for tipo in ("censo", "canario", "prueba"):
        items = api.get(f"/api/jobs?tipo={tipo}").json()["items"]
        assert items and all(j["tipo_efectivo"] == tipo for j in items), tipo


def test_buscar_y_sin_cliente(entorno):
    api, _, _, ids = entorno
    assert {j["id"] for j in api.get("/api/jobs?sin_cliente=true").json()["items"]} == {str(ids["huerfano"])}
    assert api.get("/api/jobs?buscar=OCTUBRE").json()["total"] == 1


def test_patch_asigna_cliente_y_tipo_sin_tocar_lo_rastreado(entorno):
    api, Sesion, acme, ids = entorno
    jid = ids["huerfano"]
    r = api.patch(f"/api/jobs/{jid}", json={"cliente_id": str(acme), "tipo": "censo"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["cliente_id"] == str(acme) and d["tipo"] == "censo" and d["tipo_efectivo"] == "censo"
    assert d["client_id"] == "acme"                  # sin texto viejo, el slug
    assert d["config"] == {"max_depth": 3}           # la configuracion no se toca
    # Quitar el tipo vuelve a deducirlo del nombre; quitar el cliente lo deja sin cliente.
    d = api.patch(f"/api/jobs/{jid}", json={"tipo": None, "cliente_id": None}).json()
    assert d["tipo"] is None and d["tipo_efectivo"] == "prueba" and d["cliente_id"] is None
    assert api.patch(f"/api/jobs/{jid}", json={"cliente_id": str(uuid.uuid4())}).status_code == 422


def test_la_serie_cuenta_filas_y_va_de_nuevo_a_viejo(entorno):
    api, _, acme, ids = entorno
    serie = api.get(f"/api/clients/{acme}/serie").json()
    assert [x["id"] for x in serie] == [str(ids["nuevo"]), str(ids["canario"]), str(ids["viejo"])]
    nuevo = serie[0]
    assert nuevo["urls"] == 5                        # filas, no total_urls_crawled (=1)
    assert nuevo["paginas_html"] == 5 and nuevo["indexables"] == 5 and nuevo["errores"] == 2
    assert serie[1]["tipo"] == "canario"


def test_el_panel_compara_censo_con_censo(entorno):
    api, _, acme, ids = entorno
    d = api.get("/api/clients/panel").json()
    assert d["rastreos_sin_cliente"] == 1
    p = next(c for c in d["clientes"] if c["id"] == str(acme))
    assert p["ultimo"]["id"] == str(ids["nuevo"])
    # El anterior es el censo de julio, no el canario de en medio.
    assert p["anterior"]["id"] == str(ids["viejo"])


def test_la_ficha_del_job_lleva_su_salud(entorno):
    """La causa de un rastreo bloqueado viaja en la respuesta del job: es lo
    que pinta la ficha (decision 103)."""
    api, Sesion, _, ids = entorno
    s = Sesion()
    j = s.get(Job, ids["huerfano"])
    j.status, j.finish_reason = "failed", "bloqueado_por_waf"
    j.health = {"por_clase": {"contenido": 1, "challenge_waf": 49},
                "causa": "bloqueado_por_waf"}
    s.commit()
    s.close()
    d = api.get(f"/api/jobs/{ids['huerfano']}").json()
    assert d["finish_reason"] == "bloqueado_por_waf"
    assert d["health"]["por_clase"]["challenge_waf"] == 49
