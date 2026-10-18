"""Respuesta frente a render por muestra de plantilla (decision 140).

Con el SeoSpider real y la descarga de produccion contra un sitio local cuyo
JavaScript cambia la pagina: quita un `noindex` del HTML, cambia el canonical,
anade el menu y el texto, o no toca nada. Las pruebas con render necesitan el
Chromium de Playwright (en CI se saltan; en la imagen del crawler corren).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from seo_crawler.sonda_render import campos_que_cambian, orientar

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_orientar_pone_render_y_crudo_y_los_enlaces_que_solo_da_el_js():
    render = {"noindex": False, "canonical": "https://x.com/a", "enlaces": [f"/s{i}" for i in range(12)],
              "palabras": 300, "title": "T"}
    crudo = {"noindex": True, "canonical": "https://x.com/a", "enlaces": [], "palabras": 20, "title": "T"}
    d = {x["campo"]: x for x in orientar(render, crudo)}
    assert set(d) == {"noindex", "enlaces", "palabras"}
    assert (d["noindex"]["render"], d["noindex"]["crudo"]) == (False, True)
    assert d["enlaces"]["faltan"] == 12 and (d["enlaces"]["render"], d["enlaces"]["crudo"]) == (12, 0)
    assert "base" not in d["palabras"] and "alternativa" not in d["palabras"]


def test_volatiles_en_las_dos_direcciones():
    a = {"enlaces": [f"/s{i}" for i in range(10)], "title": "x"}
    b = {"enlaces": [f"/s{i}" for i in range(20)], "title": "x"}
    # `diferencias(a, b)` no ve los 10 que sobran en b; la union si.
    assert campos_que_cambian(a, b) == {"enlaces"}


pytest.importorskip("scrapy")
pytest.importorskip("scrapy_impersonate")

from test_credenciales_y_cookies import _chromium_disponible  # noqa: E402

PROSA = " ".join(f"palabra{i}" for i in range(120))
MENU = "".join(f'<a href="/seccion-{i}">Sección {i}</a>' for i in range(12))


def _pagina(titulo="T", canonical=None, robots=None, texto=PROSA, script=""):
    partes = [f"<title>{titulo}</title>"]
    if canonical:
        partes.append(f'<link rel="canonical" href="{canonical}">')
    if robots:
        partes.append(f'<meta name="robots" content="{robots}">')
    return (f"<html><head>{''.join(partes)}</head><body><nav id=nav></nav>"
            f"<main><h1>H</h1><article id=art><p>{texto}</p></article></main>{script}</body></html>")


QUITA_NOINDEX = "<script>document.querySelector('meta[name=robots]').remove()</script>"
CAMBIA_CANONICAL = ("<script>document.querySelector('link[rel=canonical]')"
                    ".setAttribute('href', '/otra')</script>")
PONE_MENU = ("<script>document.getElementById('nav').innerHTML = " + json.dumps(MENU) + ";"
             "document.getElementById('art').innerHTML += '<p>' + "
             + json.dumps(" ".join(f"extra{i}" for i in range(400))) + " + '</p>';</script>")

RUTAS = {
    "/p/home": [200, {}, _pagina(texto=PROSA + "".join(f' <a href="{r}">{r}</a>' for r in (
        "/noi/a-1", "/can/c-1", "/js/j-1", "/igual/i-1", "/vol/v-1",
        "/lista/l-1", "/lista/l-2", "/lista/l-3", "/lista/l-4")))],
    "/noi/a-1": [200, {}, _pagina(robots="noindex", script=QUITA_NOINDEX)],
    "/can/c-1": [200, {}, _pagina(canonical="/can/c-1", script=CAMBIA_CANONICAL)],
    "/js/j-1": [200, {}, _pagina(texto="poco", script=PONE_MENU)],
    "/igual/i-1": [200, {}, _pagina()],
    "/vol/v-1": [200, {}, _pagina(titulo="Oferta {aleatorio}")],
    **{f"/lista/l-{i}": [200, {}, _pagina(titulo=f"Lista {i}")] for i in range(1, 5)},
}


def _rastrear(render: bool, chromium: str | None = None, **cb) -> dict:
    entrada = {
        "seeds": ["http://localhost:{port}/p/home"],
        "config": {"max_depth": 1, "use_sitemap": False, "render_js": render,
                   "crawl_behavior": {"render_crudo_por_plantilla": 2, "soft404_sondas": 0, **cb}},
        "rutas": RUTAS, "descarga_real": True, "ua_real": True, "ver_cabeceras": True,
    }
    if chromium:
        entrada["chromium"] = chromium
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=300, cwd=RAIZ)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-6000:]
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    paginas = [i for i in items if i["_tipo"] == "PageItem"]
    por_hash = {i["url_hash"]: i["url"] for i in paginas}
    capturas = {}
    for i in items:
        if i["_tipo"] == "CapturaAlternativaItem":
            ruta = "/" + por_hash[i["url_hash"]].split("/", 3)[-1]
            assert ruta not in capturas, f"dos capturas de {ruta}"
            capturas[ruta] = i
    return {"paginas": paginas, "capturas": capturas}


def _campos(c) -> dict:
    return {d["campo"]: d for d in c["diferencias"] or []}


def test_sin_render_y_sin_pedirlo_no_se_renderiza_nada():
    r = _rastrear(render=False)
    assert r["capturas"] == {}


@pytest.fixture(scope="module")
def chromium():
    ruta = _chromium_disponible()
    if ruta is None:
        pytest.skip("sin Chromium para Playwright")
    return ruta


def _comprobar(c: dict, perfil: str, modo: str) -> None:
    assert all(x["perfil"] == perfil and x["modo"] == modo for x in c.values()), c
    d = _campos(c["/noi/a-1"])
    assert set(d) == {"noindex"} and (d["noindex"]["crudo"], d["noindex"]["render"]) == (True, False)
    d = _campos(c["/can/c-1"])
    assert set(d) == {"canonical"} and d["canonical"]["render"].endswith("/otra")
    d = _campos(c["/js/j-1"])
    assert {"enlaces", "palabras"} <= set(d), c["/js/j-1"]
    assert d["enlaces"]["faltan"] == 12 and d["palabras"]["render"] > d["palabras"]["crudo"]
    assert c["/igual/i-1"]["estado"] == "comparada" and c["/igual/i-1"]["diferencias"] == []
    assert c["/vol/v-1"]["diferencias"] == [] and c["/vol/v-1"]["volatiles"] == ["title"]
    assert sum(1 for k in c if k.startswith("/lista/")) == 2


def test_con_render_la_otra_version_es_el_html_del_servidor(chromium):
    r = _rastrear(render=True, chromium=chromium)
    _comprobar(r["capturas"], "crudo", "sin_render")
    urls = [p["url"] for p in r["paginas"]]
    assert len(urls) == len(set(urls)), "una sonda acabo como URL del censo"


def test_sin_render_se_puede_pedir_la_muestra_renderizada(chromium):
    r = _rastrear(render=False, chromium=chromium, render_crudo_muestra=20)
    _comprobar(r["capturas"], "render", "render")
