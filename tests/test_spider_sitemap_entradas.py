"""El SeoSpider real contra un sitio local con un indice de sitemaps de varios
ficheros (decision 126): de que fichero viene cada URL, con su lastmod, y lo
que se sabe de cada fichero en `jobs.sitemaps`.

Antes `parse_sitemap` tiraba el lastmod y el fichero de origen: la membresia
era un conjunto de hashes sin procedencia, asi que ningun aviso podia decir
«arregla el sitemap de autores».
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def _urlset(*entradas):
    cuerpo = "".join(
        f"<url><loc>http://localhost:{{port}}{ruta}</loc>"
        + (f"<lastmod>{lm}</lastmod>" if lm else "") + "</url>"
        for ruta, lm in entradas)
    return [200, {"Content-Type": "application/xml"},
            f'<?xml version="1.0"?><urlset {NS}>{cuerpo}</urlset>']


@pytest.fixture(scope="module")
def rastreo():
    rutas = {
        "/sitemap_index.xml": [200, {"Content-Type": "application/xml"}, (
            f'<?xml version="1.0"?><sitemapindex {NS}>'
            "<sitemap><loc>http://localhost:{port}/sm-paginas.xml</loc></sitemap>"
            "<sitemap><loc>http://localhost:{port}/sm-todo.xml</loc></sitemap>"
            "<sitemap><loc>http://localhost:{port}/sm-roto.xml</loc></sitemap>"
            "</sitemapindex>")],
        "/sm-paginas.xml": _urlset(("/p1", "2026-01-01"), ("/p2", "2099-01-01"),
                                   ("/p1/", None)),
        # El «sitemap completo» que repite el de paginas, con otro lastmod, y
        # una URL de otro sitio que no puede entrar.
        "/sm-todo.xml": [200, {"Content-Type": "application/xml"}, (
            f'<?xml version="1.0"?><urlset {NS}>'
            "<url><loc>http://localhost:{port}/p1</loc><lastmod>2026-02-01</lastmod></url>"
            "<url><loc>https://otro-sitio.example/x</loc></url>"
            "</urlset>")],
        "/p1": [200, {}, "<html><head><title>p1</title></head><body>p1</body></html>"],
        "/p2": [200, {}, "<html><head><title>p2</title></head><body>p2</body></html>"],
    }
    entrada = {"seeds": ["http://localhost:{port}/"],
               "config": {"max_depth": 1, "exclude_patterns": ["*/tag/*"]},
               "rutas": rutas, "ver_job": True}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    job = json.loads(proc.stdout.split("@@JOB@@", 1)[1].split("\n", 1)[0])
    return items, job


def _por_fichero(items):
    return {i["fichero"].rsplit("/", 1)[1]: i["entradas"]
            for i in items if i["_tipo"] == "SitemapEntradasItem"}


def test_cada_entrada_lleva_su_fichero_y_su_lastmod(rastreo):
    items, _ = rastreo
    ficheros = _por_fichero(items)
    assert set(ficheros) == {"sm-paginas.xml", "sm-todo.xml"}
    paginas = {e["url"]: e["lastmod"] for e in ficheros["sm-paginas.xml"]}
    # `/p1/` y `/p1` son DOS URLs (decision 67): las dos entran.
    assert paginas == {"http://SEMILLA/p1": "2026-01-01",
                       "http://SEMILLA/p2": "2099-01-01",
                       "http://SEMILLA/p1/": None}
    # La URL de otro sitio no entra, igual que no entra en la membresia.
    assert {e["url"]: e["lastmod"] for e in ficheros["sm-todo.xml"]} == {
        "http://SEMILLA/p1": "2026-02-01"}
    # El url_hash es el de la pagina rastreada: es por donde se une con `urls`.
    paginas_rastreadas = {i["url"]: i["url_hash"] for i in items if i["_tipo"] == "PageItem"}
    (p1,) = [e for e in ficheros["sm-paginas.xml"] if e["url"] == "http://SEMILLA/p1"]
    assert p1["url_hash"] == paginas_rastreadas["http://SEMILLA/p1"]


def test_el_job_guarda_lo_que_se_sabe_de_cada_fichero(rastreo):
    _, job = rastreo
    (resumen,) = [a["sitemaps"] for a in job if "sitemaps" in a]
    por_nombre = {f["url"].rsplit("/", 1)[1]: f for f in resumen["ficheros"]}
    assert por_nombre["sitemap_index.xml"]["formato"] == "sitemapindex"
    assert por_nombre["sitemap_index.xml"]["hijos"] == 3
    assert por_nombre["sm-paginas.xml"]["declaradas"] == 3
    assert por_nombre["sm-paginas.xml"]["entradas_guardadas"] == 3
    assert por_nombre["sm-todo.xml"]["declaradas"] == 2
    assert por_nombre["sm-todo.xml"]["internas"] == 1
    assert por_nombre["sm-paginas.xml"]["bytes"] > 0
    assert por_nombre["sm-paginas.xml"]["leido_at"]
    # El hijo que da 404 se anota con su codigo, y la vista queda parcial.
    assert por_nombre["sm-roto.xml"]["status"] == 404
    assert resumen["parcial"] is True
    assert resumen["entradas_guardadas"] == 4 and resumen["entradas_recortadas"] is False


def test_con_tope_de_entradas_se_recorta_y_se_dice(tmp_path):
    rutas = {
        "/sitemap_index.xml": _urlset(("/p1", None), ("/p2", None), ("/p3", None)),
    }
    entrada = {"seeds": ["http://localhost:{port}/"], "config": {"max_depth": 0},
               "rutas": rutas, "ver_job": True}
    entorno = {**os.environ, "SITEMAP_ENTRIES_MAX": "2"}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120, env=entorno)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    job = json.loads(proc.stdout.split("@@JOB@@", 1)[1].split("\n", 1)[0])
    (entradas,) = _por_fichero(items).values()
    assert len(entradas) == 2
    (resumen,) = [a["sitemaps"] for a in job if "sitemaps" in a]
    assert resumen["entradas_recortadas"] is True and resumen["tope_entradas"] == 2
    (f,) = resumen["ficheros"]
    assert f["declaradas"] == 3 and f["entradas_guardadas"] == 2


def _sin_enlaces(n):
    """Un sitio cuya portada no enlaza nada: las paginas solo se conocen por el
    sitemap, que lista `/p1`..`/pn`."""
    rutas = {"/sitemap_index.xml": _urlset(*[(f"/p{i}", None) for i in range(1, n + 1)]),
             "/": [200, {}, "<html><head><title>portada</title></head><body>hola</body></html>"]}
    for i in range(1, n + 1):
        rutas[f"/p{i}"] = [200, {}, f"<html><head><title>p{i}</title></head><body>p{i}</body></html>"]
    return rutas


def test_pasado_el_cupo_las_urls_del_sitemap_esperan_en_disco_y_se_rastrean():
    """Con mas URLs en los sitemaps que `SITEMAP_EN_COLA_MAX`, las de mas
    esperan en disco y entran al vaciarse la cola (decision 186): se rastrean
    todas, y `jobs.sitemaps` dice cuantas esperaron."""
    entrada = {"seeds": ["http://localhost:{port}/"], "config": {"max_depth": 1},
               "rutas": _sin_enlaces(7), "ver_job": True}
    entorno = {**os.environ, "SITEMAP_EN_COLA_MAX": "2"}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120, env=entorno)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    job = json.loads(proc.stdout.split("@@JOB@@", 1)[1].split("\n", 1)[0])
    rastreadas = {i["url"].rsplit("/", 1)[1] for i in items if i["_tipo"] == "PageItem"}
    assert {f"p{i}" for i in range(1, 8)} <= rastreadas
    (resumen,) = [a["sitemaps"] for a in job if "sitemaps" in a]
    assert resumen["semillas_diferidas"] == 5
    # Las entradas del sitemap se guardan todas, esperen o no.
    assert resumen["entradas_guardadas"] == 7


def test_max_urls_corta_aunque_queden_urls_en_disco():
    entrada = {"seeds": ["http://localhost:{port}/"], "config": {"max_depth": 1, "max_urls": 3},
               "rutas": _sin_enlaces(7), "ver_job": True}
    entorno = {**os.environ, "SITEMAP_EN_COLA_MAX": "1"}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120, env=entorno)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    assert len([i for i in items if i["_tipo"] == "PageItem"]) <= 3


URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")


def test_la_membresia_marca_las_rastreadas_aunque_esperaran_en_disco():
    """`urls.in_sitemap` sale de las claves compactas (decision 186): True
    para las del sitemap, tambien las que esperaron en disco, y False para la
    portada, que no esta en el (la vista del sitemap es completa)."""
    if not URL_BD:
        pytest.skip("sin PAGERANK_TEST_DATABASE_URL")
    import socket
    import uuid

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from shared.models import Base, Job, Url

    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    jid = uuid.uuid4()
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        puerto = so.getsockname()[1]
    with Sesion() as s:
        s.add(Job(id=jid, name="test sitemap en disco", status="running", seeds=[], config={}))
        s.commit()
    try:
        rutas = _sin_enlaces(5)
        # Con BD el arnes sirve en 127.0.0.1 (ver el test de reanudar).
        rutas["/sitemap_index.xml"][2] = rutas["/sitemap_index.xml"][2].replace("localhost", "127.0.0.1")
        entrada = {"seeds": ["http://127.0.0.1:{port}/"], "config": {"max_depth": 1},
                   "rutas": rutas, "bd": URL_BD, "job_id": str(jid), "puerto": puerto}
        entorno = {**os.environ, "SITEMAP_EN_COLA_MAX": "2"}
        proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                              capture_output=True, text=True, timeout=120, env=entorno)
        assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
        with Sesion() as s:
            filas = {u.split(str(puerto), 1)[-1]: m for u, m in s.query(Url.url, Url.in_sitemap)
                     .filter(Url.job_id == jid, Url.status_code == 200)}
        assert filas.get("/") is False
        assert {k: v for k, v in filas.items() if k != "/"} == {f"/p{i}": True for i in range(1, 6)}
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": str(jid)})
        engine.dispose()


@pytest.mark.parametrize("sitemap,quedan", [
    # Al reanudar el sitemap ya solo lista URLs de otro sitio: ningun fichero
    # trae entradas internas y la tuberia no llega a borrar nada.
    ([200, {"Content-Type": "application/xml"},
      f'<?xml version="1.0"?><urlset {NS}><url><loc>https://otro.example/x</loc>'
      "</url></urlset>"], set()),
    # O ya no existe.
    ([404, {}, "no"], set()),
    # Con entradas, las del proceso anterior se sustituyen (lo de siempre).
    ([200, {"Content-Type": "application/xml"},
      f'<?xml version="1.0"?><urlset {NS}><url><loc>http://127.0.0.1:{{port}}/p1</loc>'
      "</url></urlset>"], {"/p1"}),
])
def test_al_reanudar_no_quedan_entradas_del_proceso_anterior(sitemap, quedan):
    """`jobs.sitemaps` se sobrescribe con lo leido en ESTE proceso: si las
    entradas del anterior se quedaban, los avisos cruzaban ficheros de un
    rastreo con entradas de otro (revision de la decision 126)."""
    if not URL_BD:
        pytest.skip("sin PAGERANK_TEST_DATABASE_URL")
    import socket
    import uuid

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from shared.models import Base, Job, SitemapEntry

    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    jid = uuid.uuid4()
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        puerto = so.getsockname()[1]
    with Sesion() as s:
        s.add(Job(id=jid, name="test sitemap reanudar", status="running", seeds=[], config={}))
        s.flush()
        s.add(SitemapEntry(job_id=jid, url_hash="h-viejo", url="http://127.0.0.1/viejo",
                           fichero="http://127.0.0.1/sm-viejo.xml", lastmod="2099-01-01"))
        s.commit()
    try:
        entrada = {"seeds": ["http://127.0.0.1:{port}/"], "config": {"max_depth": 0},
                   "rutas": {"/sitemap_index.xml": sitemap},
                   "bd": URL_BD, "job_id": str(jid), "puerto": puerto}
        proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                              capture_output=True, text=True, timeout=120)
        assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
        with Sesion() as s:
            urls = {u.split(str(puerto), 1)[-1] for (u,) in s.query(SitemapEntry.url)
                    .filter(SitemapEntry.job_id == jid)}
            resumen = s.get(Job, jid).sitemaps
        assert urls == quedan
        assert resumen["entradas_guardadas"] == len(quedan)
    finally:
        with engine.begin() as c:
            # El resto de lo del censo se va con el ON DELETE CASCADE del job.
            c.execute(text("DELETE FROM sitemap_entries WHERE job_id = :j"), {"j": str(jid)})
            c.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": str(jid)})
        engine.dispose()
