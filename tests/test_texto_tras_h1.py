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


def test_tope_de_caracteres_cortando_en_un_espacio():
    # 29 + 1 + 7 + 1 caracteres caben; de «En stock» caben 5 (menos de 20: fuera).
    t = ex.extract_texto_tras_h1(Selector(text=FICHA), max_chars=43)
    assert t == "Proteína Vegana Vainilla 1 kg\n42,27 €"
    # Una linea larga que no cabe entra hasta el ultimo espacio, no a medias.
    largo = FICHA.replace("<div>En stock</div>",
                          "<div>Envío gratis en pedidos de más de cuarenta euros a península</div>")
    t = ex.extract_texto_tras_h1(Selector(text=largo), max_chars=80)
    ultima = t.splitlines()[-1]
    assert len(t) <= 80 and "Envío gratis en pedidos".startswith(ultima[:23])
    assert not ultima.endswith(("ped", "pedid", "cuar"))


def test_empieza_en_el_h1_aunque_su_texto_salga_antes():
    """Un bloque anterior con el mismo nombre y otro precio (un recomendado,
    una miga de pan, la marca enlazada) no es el comienzo: es el h1."""
    html = ("<html><body><main><section><p>Proteína X</p><p>19,99 €</p><p>Ver</p></section>"
            "<p>Texto entre medias.</p><h1>Proteína X</h1><p>42,27 €</p></main></body></html>")
    assert ex.extract_texto_tras_h1(Selector(text=html)).splitlines()[:2] == ["Proteína X", "42,27 €"]
    # Igual salvo mayusculas justo antes: la deduplicacion no se come el h1.
    html = "<html><body><main><div>PROTEÍNA X</div><h1>Proteína X</h1><p>9 €</p></main></body></html>"
    assert ex.extract_texto_tras_h1(Selector(text=html)).splitlines()[-1] == "9 €"


def test_un_icono_con_el_caracter_de_la_marca_no_mueve_el_comienzo():
    html = ("<html><body><p><span class='icon'>&#xe000;</span> Envío</p><p>Otro 5 €</p>"
            "<main><h1>Prod</h1><p>9 €</p></main></body></html>")
    assert ex.extract_texto_tras_h1(Selector(text=html)) == "Prod\n9 €"


def test_un_h1_sin_texto_no_tapa_al_siguiente():
    html = ("<html><body><main><h1><a href='/'><img alt=''></a></h1>"
            "<h1>Prod</h1><p>9 €</p></main></body></html>")
    assert ex.extract_texto_tras_h1(Selector(text=html)) == "Prod\n9 €"


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
