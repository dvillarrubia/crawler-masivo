"""Las sondas de soft 404 con el SeoSpider real (decision 105).

Lo que no pueden hacer: gastar `max_urls` ni salir como URL del censo. Y lo que
tienen que hacer: pedirse, una por host de las semillas y una por plantilla.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")


def _rastrear(config: dict):
    entrada = {
        "seeds": ["http://localhost:{port}/", "http://127.0.0.1:{port}/seccion/uno"],
        "config": {"max_depth": 2, "exclude_patterns": ["*/tag/*"], **config},
        "rutas": {"/seccion/uno": [200, {}, "<html><head><title>u</title></head><body><p>uno</p></body></html>"]},
        "log_level": "INFO",
    }
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    peticiones = json.loads(proc.stdout.split("@@PETICIONES@@", 1)[1].split("@@ITEMS@@")[0])
    paginas = [i for i in items if i["_tipo"] == "PageItem"]
    return paginas, [p for _, _, p in peticiones], proc.stderr


@pytest.fixture(scope="module")
def con_sondas():
    return _rastrear({})


def test_se_pide_una_sonda_por_host_y_por_plantilla(con_sondas):
    _, rutas, log = con_sondas
    sondas = [r for r in rutas if "/zz" in r]
    assert any(r.startswith("/zz") for r in sondas), rutas     # raiz del host
    assert any(r.startswith("/seccion/zz") for r in sondas), rutas  # plantilla
    assert "Sonda soft 404" in log


def test_la_sonda_no_es_una_url_del_censo(con_sondas):
    paginas, _, _ = con_sondas
    assert not [p for p in paginas if "/zz" in p["url"]]


def test_la_sonda_no_gasta_max_urls():
    sin, rutas_sin, _ = _rastrear({"max_urls": 4, "crawl_behavior": {"soft404_sondas": 0}})
    con, rutas_con, _ = _rastrear({"max_urls": 4})
    assert not [r for r in rutas_sin if "/zz" in r]
    assert [r for r in rutas_con if "/zz" in r]
    assert len(con) == len(sin)
