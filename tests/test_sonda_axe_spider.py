"""axe por muestra de plantilla con el SeoSpider real (decision 174).

La descarga de produccion (curl_cffi y Playwright) contra un sitio local con
fallos de accesibilidad conocidos: imagen sin alt, boton sin nombre, poco
contraste, un campo sin etiqueta; una pagina con CSP estricta, otra con un
cargador AMD (`define.amd`) que haria que axe no se publicase en `window`, otra
cuyo fallo solo existe tras el JavaScript, y una limpia. Las pruebas con axe
necesitan el Chromium de Playwright (en CI se saltan; en la imagen del crawler
corren).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")
pytest.importorskip("scrapy_impersonate")

from test_credenciales_y_cookies import _chromium_disponible  # noqa: E402

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROTA = ('<img src="/x.png"><button></button><p style="color:#bbb;background:#fff">poco contraste</p>'
        '<input type="text">')
LIMPIA = '<main><h1>Hola</h1><p>Texto legible.</p><img src="/x.png" alt="Logo"></main>'
CSP = {"Content-Security-Policy": "default-src 'self'; script-src 'self'"}


def _pagina(cuerpo: str, head: str = "", lang: str = "es") -> str:
    return f'<!doctype html><html lang="{lang}"><head><title>T</title>{head}</head><body>{cuerpo}</body></html>'


RUTAS = {
    "/p/home": [200, {}, _pagina(LIMPIA + "".join(f'<a href="{r}">{r}</a>' for r in (
        "/f/a-1", "/f/a-2", "/f/a-3", "/csp/c-1", "/amd/m-1", "/js/j-1", "/limpia/l-1")))],
    **{f"/f/a-{i}": [200, {}, _pagina(ROTA)] for i in range(1, 4)},
    "/csp/c-1": [200, CSP, _pagina('<img src="/x.png"><button></button>')],
    "/amd/m-1": [200, {}, _pagina('<img src="/x.png">',
                                  head="<script>window.define = function () {}; define.amd = {};</script>")],
    "/js/j-1": [200, {}, _pagina(LIMPIA + "<div id=d></div><script>document.getElementById('d')"
                                 ".innerHTML = '<img src=\"/y.png\">';</script>")],
    "/limpia/l-1": [200, {}, _pagina(LIMPIA)],
}


def _rastrear(render: bool, chromium: str | None = None, **cb) -> dict:
    entrada = {
        "seeds": ["http://localhost:{port}/p/home"],
        "config": {"max_depth": 1, "use_sitemap": False, "render_js": render,
                   "crawl_behavior": {"soft404_sondas": 0, "render_crudo_muestra": 0, **cb}},
        "rutas": RUTAS, "descarga_real": True, "ua_real": True,
    }
    if chromium:
        entrada["chromium"] = chromium
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=600, cwd=RAIZ)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-6000:]
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    paginas = [i for i in items if i["_tipo"] == "PageItem"]
    por_hash = {i["url_hash"]: i["url"] for i in paginas}
    capturas = {}
    for i in items:
        if i["_tipo"] == "CapturaAlternativaItem" and i.get("perfil") == "axe":
            ruta = "/" + por_hash[i["url_hash"]].split("/", 3)[-1]
            assert ruta not in capturas, f"dos capturas de {ruta}"
            capturas[ruta] = i
    return {"paginas": paginas, "capturas": capturas}


def _reglas(c: dict) -> set[str]:
    return {v["regla"] for v in (c.get("medidas") or {}).get("violaciones") or []}


def test_apagado_de_serie_no_renderiza_nada():
    r = _rastrear(render=False)
    assert r["capturas"] == {}


@pytest.fixture(scope="module")
def chromium():
    ruta = _chromium_disponible()
    if ruta is None:
        pytest.skip("sin Chromium para Playwright")
    return ruta


def test_sin_render_axe_ve_los_fallos_de_cada_plantilla(chromium):
    sin = _rastrear(render=False)
    r = _rastrear(render=False, chromium=chromium, axe_muestra=50, axe_por_plantilla=2)
    c = r["capturas"]
    assert all(x["perfil"] == "axe" and x["modo"] == "render" for x in c.values())
    assert all(x["estado"] == "evaluada" for x in c.values()), {k: x.get("detalle") for k, x in c.items()}
    # Dos por plantilla como mucho.
    assert sum(1 for k in c if k.startswith("/f/")) == 2
    f = next(x for k, x in c.items() if k.startswith("/f/"))
    assert {"image-alt", "button-name", "color-contrast", "label"} <= _reglas(f), _reglas(f)
    cc = next(v for v in f["medidas"]["violaciones"] if v["regla"] == "color-contrast")
    assert cc["criterios"] == ["1.4.3"] and cc["nivel"] == "AA" and cc["nodos"] == 1
    assert cc["ejemplos"] and cc["ejemplos"][0]["selector"]
    # Textos de ayuda en castellano (locale vendorizado) y la version de axe.
    img = next(v for v in f["medidas"]["violaciones"] if v["regla"] == "image-alt")
    assert img["impacto"] == "critical" and "imágenes" in img["ayuda"].lower(), img["ayuda"]
    assert f["medidas"]["axe"].startswith("4.") and isinstance(f["detalle"]["ms_axe"], int)
    # CSP estricta y cargador AMD: se evalua igual.
    assert {"image-alt", "button-name"} <= _reglas(c["/csp/c-1"])
    assert "image-alt" in _reglas(c["/amd/m-1"])
    # Lo que solo existe tras el JavaScript lo ve axe (el render es suyo).
    assert "image-alt" in _reglas(c["/js/j-1"])
    assert _reglas(c["/limpia/l-1"]) == set()
    # Una sonda no es una URL del censo ni gasta max_urls.
    urls = [p["url"] for p in r["paginas"]]
    assert len(urls) == len(set(urls))
    assert sorted(urls) == sorted(p["url"] for p in sin["paginas"])


def test_con_render_y_el_tope_total(chromium):
    r = _rastrear(render=True, chromium=chromium, axe_muestra=2, axe_por_plantilla=5)
    assert len(r["capturas"]) == 2
    assert all(x["estado"] == "evaluada" for x in r["capturas"].values())
