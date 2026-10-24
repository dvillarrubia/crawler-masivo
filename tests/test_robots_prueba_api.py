"""robots.txt de prueba por la API (decision 171), con TestClient y sin Protego
(la imagen de la API no lo lleva): `POST /api/robots/probar` y
`GET /api/jobs/{id}/robots/cuerpo`, el punto de partida del bloque de la UI.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ROBOTS = """User-agent: *
Disallow: /privado/
Allow: /privado/publico

User-agent: OAI-SearchBot
User-agent: GPTBot
Disallow: /

Sitemap: https://x.com/sitemap.xml
Noindex: /viejo
"""


@pytest.fixture()
def cliente():
    from api.routers import robots_prueba

    app = FastAPI()
    app.include_router(robots_prueba.router)
    return TestClient(app)


def test_veredicto_y_linea_para_cada_par(cliente):
    r = cliente.post("/api/robots/probar", json={
        "robots_txt": ROBOTS,
        "urls": ["https://x.com/privado/a", "/privado/publico", "  ", "/blog"],
        "agentes": ["Googlebot", "gptbot", "oai-searchbot"],
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["modo"] == "google"
    # `gptbot` y `oai-searchbot` son claves del catalogo: valen por su token.
    assert d["agentes"] == ["Googlebot", "GPTBot", "OAI-SearchBot"]
    filas = {(x["agente"], x["url"]): x for x in d["resultados"]}
    assert len(filas) == 9                       # la URL vacia no cuenta
    g = filas[("Googlebot", "https://x.com/privado/a")]
    assert (g["permitida"], g["linea"], g["texto"], g["grupo"]) == (
        False, 2, "Disallow: /privado/", "User-agent: *")
    assert filas[("Googlebot", "/privado/publico")]["linea"] == 3
    assert filas[("Googlebot", "/blog")]["linea"] is None
    assert filas[("Googlebot", "/blog")]["permitida"] is True
    o = filas[("OAI-SearchBot", "/blog")]
    assert (o["permitida"], o["linea"], o["grupo"], o["linea_grupo"]) == (
        False, 7, "User-agent: OAI-SearchBot", 5)
    assert filas[("GPTBot", "/blog")]["linea_grupo"] == 6
    diag = d["diagnostico"]
    assert diag["sitemaps"] == [{"linea": 9, "url": "https://x.com/sitemap.xml"}]
    assert [(x["linea"], x["motivo"]) for x in diag["ignoradas"]] == [(10, "directiva_desconocida")]


def test_por_defecto_googlebot(cliente):
    r = cliente.post("/api/robots/probar", json={"robots_txt": ROBOTS, "urls": ["/privado/x"]})
    assert r.status_code == 200
    assert [x["agente"] for x in r.json()["resultados"]] == ["Googlebot"]


def test_ua_entero_en_modo_google_es_422(cliente):
    ua = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    r = cliente.post("/api/robots/probar", json={"robots_txt": ROBOTS, "urls": ["/"],
                                                 "agentes": [ua]})
    assert r.status_code == 422
    assert "rastreador" in r.json()["detail"]
    r = cliente.post("/api/robots/probar", json={"robots_txt": ROBOTS, "urls": ["/privado/a"],
                                                 "agentes": [ua], "modo": "rastreador"})
    assert r.status_code == 200
    assert r.json()["resultados"][0]["permitida"] is False


def test_sin_urls_o_con_demasiadas(cliente):
    assert cliente.post("/api/robots/probar", json={"robots_txt": ROBOTS, "urls": []}
                        ).status_code == 422
    assert cliente.post("/api/robots/probar", json={"robots_txt": ROBOTS, "urls": [" "]}
                        ).status_code == 422
    assert cliente.post("/api/robots/probar", json={"robots_txt": ROBOTS,
                                                    "urls": ["/"] * 501}).status_code == 422
    assert cliente.post("/api/robots/probar", json={"robots_txt": ROBOTS, "urls": ["/"],
                                                    "agentes": ["a"] * 21}).status_code == 422
    assert cliente.post("/api/robots/probar", json={"robots_txt": ROBOTS, "urls": ["/"],
                                                    "modo": "otro"}).status_code == 422


def test_robots_vacio_todo_permitido(cliente):
    r = cliente.post("/api/robots/probar", json={"robots_txt": "", "urls": ["/a"]})
    assert r.status_code == 200
    x = r.json()["resultados"][0]
    assert x["permitida"] is True and x["grupo"] is None


def test_la_app_lo_monta():
    """El router esta en la app de verdad (y, como todo `/api`, protegido: api/auth.py)."""
    from api.main import app

    rutas = set(app.openapi()["paths"])
    assert "/api/robots/probar" in rutas
    assert "/api/jobs/{job_id}/robots/cuerpo" in rutas


# -- El robots.txt guardado de un censo ------------------------------------------

def test_cuerpo_guardado(tmp_path):
    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    from api.routers import results
    from shared.database import get_session
    from shared.models import Base, Job, RobotsTxt
    from shared.robots_txt import fila_de_respuesta

    @compiles(BigInteger, "sqlite")
    def _bigint_sqlite(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(engine, tables=[Job.__table__, RobotsTxt.__table__])
    Sesion = sessionmaker(bind=engine)
    j = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=j, name="t", status="completed", seeds=["https://x.com/"], config={}))
        s.flush()
        s.add(RobotsTxt(job_id=j, **fila_de_respuesta(
            "https://x.com/robots.txt", status_code=200, cuerpo=ROBOTS.encode())))
        s.add(RobotsTxt(job_id=j, **fila_de_respuesta(
            "https://caido.x.com/robots.txt", status_code=503, cuerpo=b"error")))
        s.commit()

    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        with Sesion() as s:
            yield s

    app.dependency_overrides[get_session] = _sesion
    c = TestClient(app)
    r = c.get(f"/api/jobs/{j}/robots/cuerpo", params={"origen": "https://x.com"})
    assert r.status_code == 200, r.text
    assert r.json()["cuerpo"] == ROBOTS
    # Una URL del host vale como origen.
    assert c.get(f"/api/jobs/{j}/robots/cuerpo",
                 params={"origen": "https://x.com/robots.txt"}).status_code == 200
    assert c.get(f"/api/jobs/{j}/robots/cuerpo",
                 params={"origen": "https://caido.x.com"}).status_code == 404
    assert c.get(f"/api/jobs/{j}/robots/cuerpo",
                 params={"origen": "https://otro.com"}).status_code == 404
    assert c.get(f"/api/jobs/{uuid.uuid4()}/robots/cuerpo",
                 params={"origen": "https://x.com"}).status_code == 404


def test_tope_de_comprobaciones(cliente):
    """Un robots.txt de 20.000 reglas con 500 URLs y 20 agentes tardaba 44 s:
    se rechaza antes de evaluar."""
    enorme = "User-agent: *\n" + "".join(f"Disallow: /{i}/*.pdf$\n" for i in range(20000))
    r = cliente.post("/api/robots/probar", json={
        "robots_txt": enorme, "urls": [f"/x/{i}" for i in range(500)],
        "agentes": [f"Bot{chr(65 + i)}" for i in range(20)]})
    assert r.status_code == 422
    assert "Demasiadas comprobaciones" in r.json()["detail"]
    r = cliente.post("/api/robots/probar", json={"robots_txt": enorme, "urls": ["/x/1.pdf"]})
    assert r.status_code == 200
    assert r.json()["diagnostico"]["reglas"] == 20000


def test_tope_cuenta_comodines_y_largo_de_url(cliente):
    """120 reglas de 2.000 comodines contra 500 URLs de 4 KiB y 20 agentes son
    1,2 M «reglas x pares», por debajo del tope viejo, y minutos de CPU: el
    coste cuenta los trozos entre comodines y lo que mide la URL."""
    import time

    pat = "/" + "*x" * 2000 + "*y"
    txt = "User-agent: *\n" + "".join(f"Disallow: {pat}{i}\n" for i in range(120))
    urls = ["/" + "x" * 4000 + str(i) for i in range(500)]
    t0 = time.perf_counter()
    r = cliente.post("/api/robots/probar", json={
        "robots_txt": txt, "urls": urls, "agentes": [f"Bot{chr(65 + i)}" for i in range(20)]})
    assert r.status_code == 422, r.text
    assert "trozos" in r.json()["detail"]
    assert time.perf_counter() - t0 < 5
    # Un robots.txt normal con 500 URLs y 20 agentes pasa.
    normal = "User-agent: *\n" + "".join(f"Disallow: /*?{k}=\n" for k in "abcdefghij") + \
        "".join(f"Disallow: /priv{i}/\n" for i in range(40))
    r = cliente.post("/api/robots/probar", json={
        "robots_txt": normal, "urls": [f"/blog/articulo-{i}?utm=1" for i in range(500)],
        "agentes": [f"Bot{chr(65 + i)}" for i in range(20)]})
    assert r.status_code == 200


def test_coste_peor_caso_aceptado_cabe_en_segundos():
    """En el tope, el peor caso medido (agujas largas que recorren la URL,
    reglas simples contra URLs largas) no pasa de unos pocos segundos."""
    import time

    from api.routers.robots_prueba import MAX_COMPROBACIONES, coste
    from shared.robots_regla import diagnostico, evaluar_lote

    casos = [
        ("User-agent: *\n" + "".join(f"Disallow: /*{'x' * 1000}y{i}\n" for i in range(200)),
         ["/" + "x" * 4090] * 100),
        ("User-agent: *\n" + "".join(f"Disallow: /p{i}/\n" for i in range(2000)),
         [f"/x/{i}" for i in range(500)]),
    ]
    for txt, urls in casos:
        c = coste(diagnostico(txt), urls, 1)
        t0 = time.perf_counter()
        evaluar_lote(txt, urls, ["Googlebot"])
        por_unidad = (time.perf_counter() - t0) / c
        # ~40-75 ns medidos; holgura x4 para una maquina de CI lenta.
        assert por_unidad * MAX_COMPROBACIONES < 5, por_unidad
