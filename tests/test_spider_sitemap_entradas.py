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
