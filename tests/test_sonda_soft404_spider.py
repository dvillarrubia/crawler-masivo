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


def _rastrear(config: dict, seeds: list[str] | None = None, con_host: bool = False):
    entrada = {
        "seeds": seeds or ["http://localhost:{port}/", "http://127.0.0.1:{port}/seccion/uno"],
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
    if con_host:
        return paginas, [(h, p) for _, h, p in peticiones], proc.stderr
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


def test_el_host_al_que_redirige_la_semilla_tambien_tiene_sonda():
    """La semilla `localhost/` redirige a `127.0.0.1/home`, y el censo vive en
    127.0.0.1. Antes solo se sondeaba el host de la semilla: sin sonda para
    127.0.0.1, sus paginas no se comparaban con ninguna huella."""
    _, peticiones, _ = _rastrear({}, seeds=["http://localhost:{port}/"], con_host=True)
    assert "127.0.0.1" in {h for h, p in peticiones if p.startswith("/zz")}, peticiones


class _Spider:
    """Lo minimo del spider para llamar a los metodos del mixin."""

    job_id = "j"
    job_config: dict = {}
    render_js = False

    def __init__(self):
        from seo_crawler.sonda_soft404 import SondaSoft404Mixin

        for nombre in ("_sonda_request", "_repetir_sonda_en_destino", "_fila_sonda", "parse_sonda"):
            setattr(self, nombre, getattr(SondaSoft404Mixin, nombre).__get__(self))
        self._sondas_max, self._sondas, self._sondas_claves = 12, [], {"x.com|raiz"}
        self.anotadas = []

    def _should_follow(self, url):
        return True

    def _anotar_sonda(self, fila):
        self.anotadas.append(fila)

    def parse_sonda_cb(self, *a):
        pass

    sonda_error = parse_sonda_cb


def test_la_sonda_que_solo_cambia_de_esquema_se_repite_en_el_destino():
    """`http://x.com/zz…` -> 301 `https://x.com/zz…`: esa redireccion es la del
    sitio a https, no la respuesta a una URL que no existe. Antes la sonda se
    daba por perdida y el host se quedaba sin huella."""
    from scrapy.http import HtmlResponse, Request

    spider = _Spider()
    req = Request("http://x.com/zz00", meta={"sonda_soft404": "x.com|raiz"})
    resp = HtmlResponse("http://x.com/zz00", status=301, request=req,
                        headers={"Location": "https://x.com/zz00"}, body=b"")
    nuevas = list(spider.parse_sonda(resp))
    assert [r.url for r in nuevas] == ["https://x.com/zz00"]
    assert nuevas[0].meta["sonda_repetida"] is True
    assert spider.anotadas[0]["redirige_a"] == "https://x.com/zz00"
    # Y una sola vez: la repetida que vuelve a redirigir no se repite.
    resp2 = HtmlResponse("https://x.com/zz00", status=301, request=nuevas[0],
                         headers={"Location": "http://x.com/zz00"}, body=b"")
    assert list(spider.parse_sonda(resp2)) == []
    # Una redireccion a otra ruta (la home) no es canonicalizacion: no se repite.
    req3 = Request("http://x.com/a/zz01", meta={"sonda_soft404": "x.com|/a/:slug"})
    resp3 = HtmlResponse("http://x.com/a/zz01", status=302, request=req3,
                         headers={"Location": "https://x.com/"}, body=b"")
    assert list(spider.parse_sonda(resp3)) == []


def test_el_script_planifica_la_raiz_de_cada_host_del_censo():
    import importlib.util
    import pathlib

    ruta = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "sonda_soft404.py"
    spec = importlib.util.spec_from_file_location("sonda_script", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plan = mod.planificar("j", ["http://x.com/"], ["https://www.x.com/a", "https://www.x.com/b/c"], 12)
    claves = [c for c, _, _ in plan]
    assert "x.com|raiz" in claves and "www.x.com|raiz" in claves
