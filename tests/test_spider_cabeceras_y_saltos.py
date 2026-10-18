"""El SeoSpider REAL contra un sitio local (#149): esquemas que no son web,
hreflang en la cabecera `Link` y cadenas de redireccion largas.

Se lanza en un subproceso (el reactor de Twisted no se reinicia); el arnes esta
en ``spider_harness.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
BASE = "http://127.0.0.1:{port}"


def _pagina(cuerpo: str, head: str = "", cabeceras: dict | None = None) -> list:
    return [200, cabeceras or {},
            f"<html><head><title>T</title>{head}</head><body>{cuerpo}</body></html>"]


def _rastrear(sitio: dict, **config) -> list[dict]:
    entrada = {"seeds": [BASE + "/home"], "rutas": sitio,
               "config": {"max_depth": 3, "use_sitemap": False, **config}}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    return json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])


def _ruta(url: str) -> str:
    return "/" + url.split("/", 3)[-1]


def test_un_enlace_ftp_o_ws_al_propio_host_no_se_rastrea_ni_es_arista():
    sitio = {
        "/home": _pagina('<a href="/ok">ok</a>'
                         '<a href="ftp://127.0.0.1/f.zip">ftp</a>'
                         '<a href="ws://127.0.0.1/socket">ws</a>'
                         '<a href="JavaScript:void(0)">menu</a>'
                         '<a href="sms:+34600000000">sms</a>'
                         '<a href="http://u:p@127.0.0.1:{port}/privado">cred</a>'),
        "/ok": _pagina("<p>ok</p>"),
        "/privado": _pagina("<p>privado</p>"),
    }
    items = _rastrear(sitio)
    urls = {i["url"] for i in items if i["_tipo"] == "PageItem"}
    assert {_ruta(u) for u in urls} == {"/home", "/ok", "/privado"}
    destinos = [i["to_url"] for i in items if i["_tipo"] == "LinkItem"]
    assert not any(d.split(":", 1)[0] not in ("http", "https") for d in destinos)
    # Las credenciales no llegan ni al enlace ni a la URL.
    assert not any("@" in d for d in destinos) and not any("@" in u for u in urls)
    assert any(d.endswith("/privado") for d in destinos)


def test_un_pdf_con_hreflang_en_la_cabecera_lo_guarda():
    # Relativas: el arnes solo sustituye {port} en el cuerpo.
    enlace = ('</en/guide.pdf>; rel="alternate"; hreflang="en", '
              '</es/guia.pdf>; rel=alternate; hreflang=es, '
              '</estilo.css>; rel=preload')
    sitio = {
        "/home": _pagina('<a href="/es/guia.pdf">guia</a>'),
        "/es/guia.pdf": [200, {"Content-Type": "application/pdf", "Link": enlace},
                         "%PDF-1.4 x"],
    }
    items = _rastrear(sitio)
    pdf = next(i for i in items if i["_tipo"] == "PageItem" and i["url"].endswith(".pdf"))
    hl = [i for i in items if i["_tipo"] == "HreflangItem" and i["url_hash"] == pdf["url_hash"]]
    assert sorted((h["lang"], _ruta(h["href"]), h["origen"]) for h in hl) == [
        ("en", "/en/guide.pdf", "cabecera"), ("es", "/es/guia.pdf", "cabecera")]


def test_una_pagina_con_hreflang_en_head_y_cabecera_no_lo_repite():
    enlace = '</en/>; rel="alternate"; hreflang="en", </de/>; rel="alternate"; hreflang="de"'
    sitio = {"/home": _pagina("<p>x</p>",
                              head='<link rel="alternate" hreflang="en" href="/en/">'
                                   '<link rel="alternate" hreflang="en" href="/en/#top">',
                              cabeceras={"Link": enlace})}
    items = _rastrear(sitio)
    hl = [i for i in items if i["_tipo"] == "HreflangItem"]
    assert sorted((h["lang"], h["origen"]) for h in hl) == [("de", "cabecera"), ("en", "html")]


def test_una_cadena_de_25_se_sigue_20_saltos_y_registra_el_ultimo():
    sitio = {"/home": _pagina('<a href="/r0">r</a>')}
    for i in range(25):
        sitio[f"/r{i}"] = [301, {"Location": f"/r{i + 1}"}, ""]
    sitio["/r25"] = _pagina("<p>fin</p>")
    items = _rastrear(sitio)
    saltos = {_ruta(i["url"]): i for i in items
              if i["_tipo"] == "PageItem" and _ruta(i["url"]).startswith("/r")}
    assert sorted(saltos, key=lambda r: int(r[2:])) == [f"/r{i}" for i in range(21)]
    # El ultimo seguido queda con su destino, sin pedirlo.
    assert saltos["/r20"]["redirect_url"].endswith("/r21")
