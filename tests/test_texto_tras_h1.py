"""El texto que sigue al h1, guardado aparte del contenido principal
(decision 187): en una ficha, la caja de compra que trafilatura tira a veces.

La ficha de Planeta Huerto de la proteina Energy Feelings se guardo sin su
precio (42,27 €): trafilatura se quedo con el bloque «También puede
interesarte» y Jev comparo el marcado con el precio de otro producto.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from parsel import Selector

from seo_crawler import extractors as ex

FICHA = (
    "<html><body><header><nav><a href='/'>Inicio</a></nav></header>"
    "<main><div class='galeria'><img alt='foto'></div>"
    "<section class='caja'><a href='/marca'>Energy Feelings</a>"
    "<h1>Proteína Vegana Vainilla 1 kg</h1>"
    "<div class='precio'><span>42,27 €</span></div>"
    "<div>En stock</div><button>Añadir al carrito</button></section>"
    "<section><h2>También puede interesarte</h2>"
    "<ul><li>Proteína chocolate 1 kg <span>32,50 €</span></li></ul></section>"
    "</main><footer>© Tienda</footer></body></html>"
)


def test_empieza_en_el_h1_y_sigue_en_orden_de_lectura():
    t = ex.extract_texto_tras_h1(Selector(text=FICHA))
    lineas = t.splitlines()
    assert lineas[0] == "Proteína Vegana Vainilla 1 kg"
    assert lineas[1:4] == ["42,27 €", "En stock", "Añadir al carrito"]
    # Lo de antes del h1 (la marca, la galeria, el menu) no entra.
    assert "Energy Feelings" not in t and "Inicio" not in t


def test_tope_de_caracteres_sin_cortar_en_seco():
    t = ex.extract_texto_tras_h1(Selector(text=FICHA), max_chars=60)
    assert len(t) <= 60
    assert t.startswith("Proteína Vegana Vainilla 1 kg\n42,27 €")
    # Lo que no cabe no aparece a medias si quedan menos de 20 caracteres.
    assert "32,50" not in t


def test_h1_fuera_de_main():
    html = ("<html><body><div class='hero'><h1>Hotel Mar</h1><p>Desde 99 € la noche</p></div>"
            "<main><p>Texto del hotel con habitaciones.</p></main></body></html>")
    t = ex.extract_texto_tras_h1(Selector(text=html))
    assert t.splitlines()[:2] == ["Hotel Mar", "Desde 99 € la noche"]


def test_sin_h1_nada():
    assert ex.extract_texto_tras_h1(Selector(text="<html><body><main><p>Hola</p></main></body></html>")) is None
    assert ex.extract_texto_tras_h1(Selector(text="<html><body><h1> </h1></body></html>")) is None


HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
LD = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product",'
      '"name":"Proteína","offers":{"@type":"Offer","price":"42.27","priceCurrency":"EUR",'
      '"availability":"https://schema.org/InStock"}}</script>')


def test_el_spider_lo_guarda_solo_con_marcado_verificable():
    pytest.importorskip("scrapy")
    ficha = FICHA.replace("<html><body>", f"<html><head><title>Ficha</title>{LD}</head><body>")
    otra = FICHA.replace("<html><body>", "<html><head><title>Sin marcado</title></head><body>")
    rutas = {
        "/": [200, {}, "<html><head><title>Inicio</title></head><body>"
                       "<a href='/ficha'>f</a><a href='/otra'>o</a></body></html>"],
        "/ficha": [200, {}, ficha],
        "/otra": [200, {}, otra],
    }
    entrada = {"seeds": ["http://localhost:{port}/"], "config": {"max_depth": 1}, "rutas": rutas}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    paginas = {i["url_hash"]: i["url"].rsplit("/", 1)[1] for i in items if i["_tipo"] == "PageItem"}
    contenido = {paginas[i["url_hash"]]: i for i in items if i["_tipo"] == "ContentItem"}
    assert contenido["ficha"]["texto_tras_h1"].startswith("Proteína Vegana Vainilla 1 kg\n42,27 €")
    assert contenido["otra"].get("texto_tras_h1") is None
