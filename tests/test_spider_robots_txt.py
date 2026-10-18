"""robots.txt que responde 5xx, 429, no responde o 404, con el SeoSpider REAL (decision 112).

El sitio y el arnes estan en ``spider_harness.py``; se lanza en un subproceso
(el reactor de Twisted no se reinicia). Se comprueba:

- la fila que anota `RobotsTxtRegistroMiddleware`: codigo, intentos (con los
  reintentos de Scrapy), error, y la comprobacion al cerrar si fallo;
- que el rastreo SIGUE y guarda las paginas (un robots.txt caido es un
  hallazgo del sitio, no un fallo del dato: decisiones 28 y 32), y que en
  `respect` no se bloquea nada por ello;
- con la tuberia real contra el Postgres de pruebas, que la fila se guarda en
  `robots_txt` y que re-rastrear el mismo job la sustituye, no la duplica.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pytest

pytest.importorskip("scrapy")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
DESAFIO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "respuestas", "cloudflare_desafio_progym.html")

CONFIG = {"max_depth": 1, "use_sitemap": False}
# Los de settings.py: 500 NO se reintenta, 503 y 429 si (dos veces).
AJUSTES = {"RETRY_HTTP_CODES": [502, 503, 504, 408, 429], "RETRY_TIMES": 2}


def _rastrear(robots=None, **extra):
    entrada = {"seeds": ["http://127.0.0.1:{port}/home"], "config": CONFIG,
               "robots_obey": True, "settings": {**AJUSTES, **extra.pop("settings", {})},
               **extra}
    if robots is not None:
        entrada["rutas"] = {"/robots.txt": robots}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    cierre = json.loads(proc.stdout.split("@@CIERRE@@", 1)[1].split("\n", 1)[0])
    peticiones = json.loads(proc.stdout.split("@@PETICIONES@@", 1)[1].split("\n", 1)[0])
    return items, cierre, peticiones


def _paginas(items):
    return {"/" + i["url"].split("/", 3)[-1]: i for i in items if i["_tipo"] == "PageItem"}


def _pide_robots(peticiones):
    return sum(1 for _, _, ruta in peticiones if ruta == "/robots.txt")


@pytest.mark.parametrize("codigo,intentos", [(503, 3), (429, 3), (500, 1)])
def test_robots_5xx_o_429_se_anota_y_el_rastreo_sigue(codigo, intentos):
    items, cierre, peticiones = _rastrear([codigo, {}, "<html><p>caido</p></html>"])
    [fila] = cierre["robots_txt"]
    assert (fila["status_code"], fila["intentos"], fila["error"]) == (codigo, intentos, None)
    assert fila["cuerpo"] is None
    assert fila["response_class"] == ("rate_limited" if codigo == 429 else "contenido")
    # Fallo: se pidio UNA vez mas al quedarse el spider sin trabajo, sin reintentos.
    assert fila["comprobacion"]["status_code"] == codigo
    assert _pide_robots(peticiones) == intentos + 1
    paginas = _paginas(items)
    assert {"/home", "/a", "/mr", "/bad"} <= set(paginas)
    assert not any(p.get("blocked_by_robots") for p in paginas.values())
    assert cierre["reason"] == "finished"


def test_robots_que_no_responde():
    items, cierre, peticiones = _rastrear(
        lentas={"/robots.txt": 4}, settings={"DOWNLOAD_TIMEOUT": 1, "RETRY_TIMES": 1})
    [fila] = cierre["robots_txt"]
    assert fila["status_code"] is None and fila["intentos"] == 2
    assert "Timeout" in fila["error"]
    assert "Timeout" in fila["comprobacion"]["error"]
    assert {"/home", "/a"} <= set(_paginas(items))


def test_robots_404_no_se_comprueba_otra_vez():
    items, cierre, peticiones = _rastrear([404, {}, "no"])
    [fila] = cierre["robots_txt"]
    assert (fila["status_code"], fila["intentos"], fila["comprobacion"]) == (404, 1, None)
    assert _pide_robots(peticiones) == 1
    assert {"/home", "/a"} <= set(_paginas(items))


def test_robots_detras_de_un_muro_es_un_muro_y_no_un_4xx():
    with open(DESAFIO, encoding="utf-8") as f:
        desafio = f.read()
    _, cierre, peticiones = _rastrear(
        [403, {"cf-mitigated": "challenge", "Server": "cloudflare"}, desafio])
    [fila] = cierre["robots_txt"]
    assert (fila["status_code"], fila["response_class"]) == (403, "challenge_waf")
    assert fila["cuerpo"] is None and fila["comprobacion"] is None


def test_robots_200_guarda_el_cuerpo_y_el_hash():
    items, cierre, _ = _rastrear(
        [200, {"Content-Type": "text/plain"}, "User-agent: *\nDisallow: /tag/\n"])
    [fila] = cierre["robots_txt"]
    assert fila["cuerpo"] == "User-agent: *\nDisallow: /tag/\n"
    assert fila["bytes"] == len(fila["cuerpo"]) and len(fila["sha256"]) == 64
    assert fila["recortado"] is False and fila["comprobacion"] is None


def test_en_ignore_se_anota_el_que_pide_el_spider_para_los_sitemaps():
    _, cierre, _ = _rastrear([503, {}, "x"], robots_obey=False, robots_mode="ignore",
                             config={**CONFIG, "use_sitemap": True})
    [fila] = cierre["robots_txt"]
    assert fila["status_code"] == 503


@pytest.fixture()
def bd():
    if not URL_BD:
        pytest.skip("sin PAGERANK_TEST_DATABASE_URL")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from shared.models import Base, Job

    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="test robots.txt", status="running", seeds=[], config={}))
        s.commit()
    yield Sesion, jid
    with engine.begin() as c:
        for tabla in ("robots_txt", "links", "issues", "urls", "jobs"):
            col = "id" if tabla == "jobs" else "job_id"
            c.execute(text(f"DELETE FROM {tabla} WHERE {col} = :j"), {"j": str(jid)})
    engine.dispose()


def test_se_guarda_en_robots_txt_y_re_rastrear_la_sustituye(bd):
    import socket

    from shared.models import RobotsTxt

    Sesion, jid = bd
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        puerto = so.getsockname()[1]
    _rastrear([503, {}, "x"], bd=URL_BD, job_id=str(jid), puerto=puerto)
    with Sesion() as s:
        [fila] = s.query(RobotsTxt).filter(RobotsTxt.job_id == jid).all()
        assert (fila.status_code, fila.intentos, fila.host) == (503, 3, "127.0.0.1")
        assert fila.comprobacion["status_code"] == 503
    # El mismo job, otra ejecucion: el robots.txt ya responde.
    _rastrear([200, {"Content-Type": "text/plain"}, "User-agent: *\nAllow: /\n"],
              bd=URL_BD, job_id=str(jid), puerto=puerto)
    with Sesion() as s:
        [fila] = s.query(RobotsTxt).filter(RobotsTxt.job_id == jid).all()
        assert (fila.status_code, fila.intentos, fila.comprobacion) == (200, 1, None)
        assert fila.cuerpo == "User-agent: *\nAllow: /\n"
