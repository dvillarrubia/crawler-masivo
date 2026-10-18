"""robots.txt por bot y la linea que bloquea una URL, por la API (decision 129).

EJECUTADO con TestClient contra SQLite y sin Protego (la imagen de la API no
lo lleva): `GET /robots` devuelve el catalogo de bots y lo que escribio el
analisis por host, y la ficha de una URL bloqueada trae la linea que la
bloquea, para Googlebot y para nuestro rastreo, calculada con el cuerpo
guardado (vale para cualquier censo con `robots_txt`, sin re-analizar).
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import Base, Job, RobotsTxt, Url  # noqa: E402
from shared.robots_txt import fila_de_respuesta  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


ROBOTS = b"""User-agent: *
Disallow: /privado/

User-agent: Googlebot
Disallow: /solo-google/
"""
J = uuid.uuid4()
VIEJO = uuid.uuid4()
BOTS = {"oai-searchbot": {"alcance": "todo"}}


@pytest.fixture()
def api(tmp_path):
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    ids = {}
    with Sesion() as s:
        for jid in (J, VIEJO):
            s.add(Job(id=jid, name="t", status="completed", seeds=["https://x.com/"],
                      config={"robots_mode": "respect"}))
        s.flush()
        fila = fila_de_respuesta("https://x.com/robots.txt", status_code=200,
                                 cabeceras={"content-type": "text/plain"}, cuerpo=ROBOTS)
        s.add(RobotsTxt(job_id=J, evaluacion={"googlebot": {"estado": "procesado"},
                                              "bots": BOTS}, **fila))
        for ruta, status, bloq, bloq_g in (("/privado/a", None, True, False),
                                           ("/solo-google/a", 200, False, True),
                                           ("/libre", 200, False, False)):
            u = Url(job_id=J, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com",
                    scheme="https", path=ruta, is_internal=True, is_html=True,
                    status_code=status, blocked_by_robots=bloq, bloqueada_googlebot=bloq_g,
                    indexability_status="Blocked by robots.txt" if (bloq or bloq_g) else "Indexable")
            s.add(u)
            s.flush()
            ids[ruta] = u.id
        u = Url(job_id=VIEJO, url="https://x.com/privado/a", url_hash="v", host="x.com",
                scheme="https", path="/privado/a", is_internal=True, status_code=None,
                blocked_by_robots=True, indexability_status="Blocked by robots.txt")
        s.add(u)
        s.flush()
        ids["viejo"] = u.id
        s.commit()
    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        with Sesion() as s:
            yield s

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), ids


def test_robots_por_host_y_catalogo_de_bots(api):
    c, _ = api
    d = c.get(f"/api/jobs/{J}/robots").json()
    tipos = {b["token"]: b["tipo"] for b in d["bots"]}
    assert tipos["GPTBot"] == "entrenamiento" and tipos["OAI-SearchBot"] == "busqueda_ia"
    [h] = d["hosts"]
    assert (h["origen"], h["estado"], h["bots"]) == ("https://x.com", "procesado", BOTS)
    assert c.get(f"/api/jobs/{VIEJO}/robots").json()["hosts"] == []


def test_la_ficha_de_una_url_bloqueada_por_el_rastreo_dice_la_linea(api):
    c, ids = api
    r = c.get(f"/api/jobs/{J}/urls/{ids['/privado/a']}").json()["robots_regla"]
    assert r["googlebot"] is None
    assert (r["rastreador"]["linea"], r["rastreador"]["texto"]) == (2, "Disallow: /privado/")
    assert r["rastreador"]["grupo"] == "User-agent: *"
    assert r["robots_url"] == "https://x.com/robots.txt"


def test_la_ficha_de_una_url_que_solo_googlebot_no_puede_pedir(api):
    c, ids = api
    r = c.get(f"/api/jobs/{J}/urls/{ids['/solo-google/a']}").json()["robots_regla"]
    assert r["rastreador"] is None
    assert (r["googlebot"]["linea"], r["googlebot"]["grupo"]) == (5, "User-agent: Googlebot")


def test_sin_bloqueo_o_sin_robots_guardado_no_hay_linea(api):
    c, ids = api
    assert c.get(f"/api/jobs/{J}/urls/{ids['/libre']}").json()["robots_regla"] is None
    assert c.get(f"/api/jobs/{VIEJO}/urls/{ids['viejo']}").json()["robots_regla"] is None


def test_con_varios_hosts_la_ficha_usa_el_robots_de_su_host(tmp_path):
    """La ficha lee solo la fila de su host (cada una lleva hasta 500 KiB de
    cuerpo); con dos hosts, la linea es la del robots.txt del suyo."""
    from api.routers import results

    engine = create_engine(f"sqlite:///{tmp_path}/h.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="t", status="completed", seeds=["https://x.com/"],
                  config={"robots_mode": "respect"}))
        s.flush()
        for host, cuerpo in (("x.com", b"User-agent: *\nDisallow: /a/\n"),
                             ("blog.x.com", b"# blog\n\nUser-agent: *\nDisallow: /a/\n")):
            fila = fila_de_respuesta(f"https://{host}/robots.txt", status_code=200,
                                     cabeceras={"content-type": "text/plain"}, cuerpo=cuerpo)
            s.add(RobotsTxt(job_id=jid, evaluacion={"googlebot": {"estado": "procesado"}}, **fila))
        u = Url(job_id=jid, url="https://blog.x.com/a/1", url_hash="b", host="blog.x.com",
                scheme="https", path="/a/1", is_internal=True, status_code=None,
                blocked_by_robots=True, bloqueada_googlebot=True,
                indexability_status="Blocked by robots.txt")
        s.add(u)
        s.commit()
        uid = u.id
    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        with Sesion() as s:
            yield s

    app.dependency_overrides[get_session] = _sesion
    c = TestClient(app)
    r = c.get(f"/api/jobs/{jid}/urls/{uid}").json()["robots_regla"]
    assert r["robots_url"] == "https://blog.x.com/robots.txt"
    assert r["googlebot"]["linea"] == 4
    hosts = c.get(f"/api/jobs/{jid}/robots").json()["hosts"]
    assert [h["origen"] for h in hosts] == ["https://blog.x.com", "https://x.com"]
