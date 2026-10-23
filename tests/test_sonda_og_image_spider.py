"""La sonda que mide la og:image, con el SeoSpider real (decision 154).

El sitio vive en `localhost` y la og:image de una pagina en un «CDN» propio en
127.0.0.2. Las imagenes son GIF de cabecera escrita a mano (ancho y alto en
bytes ASCII, para que viajen en el JSON del arnes). Se mira lo que LLEGA al
tercero: un GET con `Range` de 64 KB, sin HEAD delante.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")
pytest.importorskip("scrapy_impersonate")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = "http://127.0.0.2:{port}"


def _gif(ancho: int, alto: int) -> str:
    cabecera = b"GIF89a" + struct.pack("<HH", ancho, alto)
    assert all(b < 0x80 for b in cabecera), "el arnes manda el cuerpo como UTF-8"
    return cabecera.decode("ascii") + "\x00" * 20


def _html(og: str, cuerpo: str = "") -> str:
    return (f'<html><head><title>t</title><meta property="og:image" content="{og}">'
            f"</head><body>{cuerpo}</body></html>")


GIF = {"Content-Type": "image/gif"}
# /s/dos y /s/tres son la misma plantilla (/s/...) con imagenes distintas: con
# `og_image_por_plantilla=1` solo se mide una, la que el rastreo vea antes.
RUTAS = {
    "/home": [200, {}, _html(f"{EXT}/og-grande.gif",
                             '<a href="/s/dos">2</a><a href="/s/tres">3</a>'
                             '<a href="/n/uno">n</a><a href="/m/uno">m</a>')],
    "/s/dos": [200, {}, _html("/img/a.gif")],
    "/s/tres": [200, {}, _html("/img/b.gif")],
    "/n/uno": [200, {}, _html("/img/og-pequena.gif")],
    "/m/uno": [200, {}, _html("/img/rota.gif")],
    "/img/og-pequena.gif": [200, GIF, _gif(256, 100)],
    "/img/a.gif": [200, GIF, _gif(1100, 630)],
    "/img/b.gif": [200, GIF, _gif(1100, 630)],
}
EXTERNOS = {
    "127.0.0.2": {
        "/robots.txt": [200, {"Content-Type": "text/plain"}, "User-agent: *\nAllow: /\n"],
        "/og-grande.gif": [200, GIF, _gif(1100, 630)],
    },
}


def _rastrear(crawl_behavior: dict):
    entrada = {
        "seeds": ["http://localhost:{port}/home"],
        "config": {"max_depth": 3, "use_sitemap": False,
                   "crawl_behavior": {"soft404_sondas": 0, **crawl_behavior}},
        "rutas": RUTAS, "externos": EXTERNOS,
        "http_real": True, "descarga_real": True, "log_level": "INFO",
    }
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=240, cwd=RAIZ)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-6000:]
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    externas = json.loads(proc.stdout.split("@@EXTERNAS@@", 1)[1].split("\n", 1)[0])
    filas = {i["url"].rsplit("/", 1)[1]: i for i in items
             if i["_tipo"] == "RespuestaSondeadaItem"}
    return filas, externas, proc.stderr


@pytest.fixture(scope="module")
def con_muestra():
    return _rastrear({"og_image_muestra": 10, "og_image_por_plantilla": 1})


def test_la_og_image_se_mide_con_un_get_de_64_kb(con_muestra):
    filas, externas, log = con_muestra
    grande = filas.get("og-grande.gif")
    assert grande, (sorted(filas), log[-3000:])
    assert grande["uso"] == "og_image" and grande["metodo"] == "GET"
    assert (grande["ancho"], grande["alto"]) == (1100, 630)
    assert grande["veredicto"] == "ok"
    peticiones = [(m, c.get("range")) for ip, m, r, c in externas if r == "/og-grande.gif"]
    assert peticiones == [("GET", "bytes=0-65535")], peticiones


def test_la_interna_tambien_y_la_rota_queda_rota(con_muestra):
    filas, _, _ = con_muestra
    assert (filas["og-pequena.gif"]["ancho"], filas["og-pequena.gif"]["alto"]) == (256, 100)
    rota = filas["rota.gif"]
    assert rota["veredicto"] == "roto" and rota["ancho"] is None


def test_el_tope_por_plantilla_deja_la_segunda_sin_pedir(con_muestra):
    filas, _, _ = con_muestra
    medidas = [n for n in ("a.gif", "b.gif") if n in filas]
    assert len(medidas) == 1, sorted(filas)
    assert filas[medidas[0]]["ancho"] == 1100


def test_con_la_muestra_a_cero_no_se_pide_ninguna():
    filas, externas, _ = _rastrear({"og_image_muestra": 0})
    assert not [f for f in filas.values() if f["uso"] == "og_image"]
    assert not [r for _, _, r, _ in externas if r.endswith(".gif")]
