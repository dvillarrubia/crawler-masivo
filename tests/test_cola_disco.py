"""La cola en disco de las URLs de sitemap que no caben en la de Scrapy
(decision 186)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "crawler"))

from seo_crawler.cola_disco import ColaEnDisco  # noqa: E402


def test_fifo_por_tandas_y_meter_entre_medias():
    c = ColaEnDisco()
    assert c.sacar(5) == [] and len(c) == 0
    for i in range(5):
        c.meter(f"https://x.test/{i}")
    assert len(c) == 5
    assert c.sacar(2) == ["https://x.test/0", "https://x.test/1"]
    # Lo que entra despues de empezar a leer sale detras de lo que habia.
    c.meter("https://x.test/5")
    assert c.sacar(10) == [f"https://x.test/{i}" for i in (2, 3, 4, 5)]
    assert len(c) == 0 and c.escritas == 6
    assert c.sacar(1) == []
    c.cerrar()
    assert c.sacar(1) == [] and c.escritas == 6


def test_un_salto_de_linea_no_parte_la_url():
    c = ColaEnDisco()
    c.meter("https://x.test/a\nb")
    c.meter("https://x.test/ñ")
    assert c.sacar(5) == ["https://x.test/ab", "https://x.test/ñ"]
    c.cerrar()
