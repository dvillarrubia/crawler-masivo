"""`GET /api/clients/{id}/evolucion`, la del perfil y la tendencia del panel
(decision 130), EJECUTADOS con TestClient y SQLite.

Un cliente con un perfil y seis ejecuciones —cuatro censos completos, uno
fallido y un canario— mas un rastreo sin perfil: la ficha tiene 4 puntos
comparables en la serie del perfil, el fallido y el canario salen aparte y con
su motivo, y el cambio de version del analisis queda marcado entre dos puntos.
El panel no dice «sube» a traves de ese corte.
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

from shared.database import get_session  # noqa: E402
from shared.evolucion import registrar  # noqa: E402
from shared.models import Base, Client, CrawlProfile, Issue, Job, ProfileRun, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _job(s, nombre, semana, cliente, perfil=None, errores=0, status="completed",
         analisis="v1", finish="finished"):
    j = Job(id=uuid.uuid4(), name=nombre, status=status, seeds=["https://x.com/"],
            config={"max_depth": 3}, created_at=T0 + timedelta(days=7 * semana),
            cliente_id=cliente.id, profile_id=perfil.id if perfil else None,
            analisis_version=analisis, crawler_version="c1", finish_reason=finish,
            pagerank_resumen={"desperdiciado": 0.02, "reparto": {"indexable": 0.9}})
    s.add(j)
    s.flush()
    for n in range(4):
        u = Url(job_id=j.id, url=f"https://x.com/{n}", url_hash=f"{j.id}{n}", is_internal=True,
                is_html=True, status_group="2xx", status_code=200, indexable=True, click_depth=n)
        s.add(u)
        s.flush()
        if n < errores:
            s.add(Issue(job_id=j.id, url_id=u.id, issue_type="status_4xx", severity="error"))
    s.flush()
    registrar(s, j.id)
    return j


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    from api.routers import clientes

    engine = create_engine(f"sqlite:///{tmp_path}/e.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()
    acme = Client(nombre="Acme", slug="acme", dominios=["x.com"])
    s.add(acme)
    s.flush()
    sitio = CrawlProfile(cliente_id=acme.id, nombre="Sitio", seeds=["https://x.com/"])
    s.add(sitio)
    s.flush()
    ids = {
        "s1": _job(s, "acme semanal 1", 1, acme, sitio, errores=1).id,
        "s2": _job(s, "acme semanal 2", 2, acme, sitio, errores=2).id,
        "fallido": _job(s, "acme semanal 3", 3, acme, sitio, status="failed").id,
        "canario": _job(s, "CANARY - acme", 3, acme, sitio, errores=4).id,
        "s3": _job(s, "acme semanal 4", 4, acme, sitio, errores=2).id,
        # Re-analizado con otro codigo: la subida puede ser nuestra.
        "s4": _job(s, "acme semanal 5", 5, acme, sitio, errores=4, analisis="v2").id,
        "suelto": _job(s, "acme a mano", 2, acme, None, errores=1).id,
    }
    # Uno terminado sin agregar (anterior a la tabla).
    viejo = Job(id=uuid.uuid4(), name="acme julio", status="completed", seeds=["https://x.com/"],
                config={}, created_at=T0, cliente_id=acme.id, profile_id=sitio.id)
    s.add(viejo)
    s.commit()
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
    return TestClient(app), Sesion, acme.id, sitio.id, ids


def test_la_ficha_tiene_cuatro_puntos_comparables_y_lo_demas_aparte(entorno):
    api, _, acme, sitio, ids = entorno
    r = api.get(f"/api/clients/{acme}/evolucion")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["sin_agregado"] == 1
    por_clave = {s["clave"]: s for s in d["series"]}
    serie = por_clave[f"perfil:{sitio}"]
    assert serie["nombre"] == "Sitio" and serie["comparables"] == 4
    motivos = {p["job_id"]: p["motivo"] for p in serie["puntos"]}
    assert motivos[str(ids["fallido"])] == "failed"
    assert motivos[str(ids["canario"])] == "canario"
    comparables = [p for p in serie["puntos"] if p["comparable"]]
    assert [p["errores"] for p in comparables] == [1, 2, 2, 4]
    assert comparables[0]["paginas_html"] == 4 and comparables[0]["click_depth_medio"] == 1.5
    assert comparables[0]["pagerank_desperdiciado"] == 2.0
    # El corte de version esta en el ultimo, y solo ahi.
    cambios = [p["cambio_version"] for p in comparables]
    assert cambios[:3] == [None, None, None] and cambios[3]["analisis"] is True
    assert serie["tendencia_errores"]["direccion"] == "no_comparable"
    # El rastreo sin perfil es otra serie (por host), no se mezcla.
    assert "host:x.com" in por_clave


def test_la_del_perfil_solo_trae_el_perfil(entorno):
    api, _, acme, sitio, ids = entorno
    d = api.get(f"/api/clients/{acme}/profiles/{sitio}/evolucion").json()
    assert [s["clave"] for s in d["series"]] == [f"perfil:{sitio}"]
    assert api.get(f"/api/clients/{acme}/profiles/{uuid.uuid4()}/evolucion").status_code == 404
    assert api.get(f"/api/clients/{uuid.uuid4()}/evolucion").status_code == 404


def test_el_panel_lleva_la_tendencia_de_errores(entorno):
    api, Sesion, acme, sitio, ids = entorno
    p = next(c for c in api.get("/api/clients/panel").json()["clientes"] if c["id"] == str(acme))
    ev = p["evolucion"]
    assert ev["ultimo_job"] == str(ids["s4"]) and ev["serie"] == "Sitio"
    assert ev["tendencia"]["direccion"] == "no_comparable"
    assert ev["valores"] == [1, 2, 2, 4]
    # Re-analizado el anterior con el mismo codigo, ya se puede decir.
    with Sesion() as s:
        s.get(Job, ids["s3"]).analisis_version = "v2"
        registrar(s, ids["s3"])
        s.commit()
    p = next(c for c in api.get("/api/clients/panel").json()["clientes"] if c["id"] == str(acme))
    t = p["evolucion"]["tendencia"]
    assert (t["direccion"], t["delta"], t["mejora"]) == ("sube", 2, False)


def test_sin_tabla_el_panel_sale_igual(entorno):
    api, Sesion, acme, _, _ = entorno
    with Sesion() as s:
        ProfileRun.__table__.drop(bind=s.get_bind())
    r = api.get("/api/clients/panel")
    assert r.status_code == 200
    assert all(c["evolucion"] is None for c in r.json()["clientes"])


def test_el_backup_lleva_la_fila_y_la_cuelga_del_job_nuevo(entorno, monkeypatch):
    import io
    import zipfile

    import shared.database
    from api import backup

    _, Sesion, _, _, ids = entorno
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    monkeypatch.setattr(backup, "SessionLocal", Sesion)
    datos = b"".join(backup.stream_backup_zip(ids["s2"]))
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        assert len(z.read("profile_runs.jsonl").splitlines()) == 1
    with Sesion() as s:
        r = backup.import_backup_zip(io.BytesIO(datos), False, s)
        nuevo = uuid.UUID(str(r["new_job_id"]))
        assert r["rows_imported"]["profile_runs"] == 1
    with Sesion() as s:
        fila = s.query(ProfileRun).filter(ProfileRun.job_id == nuevo).one()
    assert fila.errores == 2 and fila.urls == 4


def test_un_censo_importado_es_un_punto_comparable_de_su_serie(entorno, monkeypatch):
    """La fila viaja en el backup para que el censo importado salga en la
    evolucion sin pasos aparte. Si el job importado pierde su
    `analisis_version`, la fila (que la lleva) no casa con la del job y el
    punto sale `desfasado`; y el relleno sin `--todos` no lo arregla, porque
    la fila ya existe. Lo mismo con `finish_reason`: un censo truncado
    importado sin el se leeria como completo y entraria en la linea."""
    import io

    import shared.database
    from api import backup

    cliente, Sesion, acme, sitio, ids = entorno
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    monkeypatch.setattr(backup, "SessionLocal", Sesion)
    with Sesion() as s:
        s.get(Job, ids["s3"]).finish_reason = "max_urls_reached"
        s.commit()
    nuevos = {}
    for k in ("s2", "s3"):
        datos = b"".join(backup.stream_backup_zip(ids[k]))
        with Sesion() as s:
            nuevos[k] = str(backup.import_backup_zip(io.BytesIO(datos), False, s)["new_job_id"])
    with Sesion() as s:
        j = s.get(Job, uuid.UUID(nuevos["s2"]))
        assert (j.analisis_version, j.crawler_version) == ("v1", "c1")
    puntos = {p["job_id"]: p for serie in cliente.get(f"/api/clients/{acme}/evolucion").json()["series"]
              for p in serie["puntos"]}
    assert puntos[nuevos["s2"]]["motivo"] is None and puntos[nuevos["s2"]]["comparable"]
    assert puntos[nuevos["s3"]]["motivo"] == "truncado"
