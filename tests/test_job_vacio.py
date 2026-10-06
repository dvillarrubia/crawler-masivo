"""Un rastreo con cero URLs no es un sitio limpio.

Con `robots_mode=respect` y un `Disallow: /`, Scrapy descarta la semilla con
IgnoreRequest, `handle_error` no registraba nada y Scrapy terminaba con codigo
0: el job quedaba `completed` con 0 URLs, que se lee igual que un sitio vacio y
en orden. Con la comparacion entre censos (#32) eso declararia desaparecido el
sitio entero.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")

ARNES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")


def _rastrear(**extra):
    entrada = {"seeds": ["http://localhost:{port}/"], "config": {"max_depth": 1}, **extra}
    proc = subprocess.run(
        [sys.executable, ARNES, json.dumps(entrada)],
        capture_output=True, text=True, timeout=120,
    )
    items = []
    if "@@ITEMS@@" in proc.stdout:
        items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    return items, proc.stdout + proc.stderr


def test_con_disallow_total_no_se_guarda_ninguna_pagina():
    items, salida = _rastrear(robots_prohibe_todo=True, config={"max_depth": 1,
                                                                "robots_mode": "respect"})
    paginas = [i for i in items if i["_tipo"] == "PageItem"]
    assert paginas == [], "robots.txt prohibe todo: no deberia guardarse nada"
    # Y el spider lo dice, que es lo que permite al worker distinguir
    # "robots lo bloquea todo" de "no se sabe por que".
    assert "robots.txt prohibe la semilla" in salida


def test_sin_disallow_el_mismo_sitio_si_se_rastrea():
    """Control: que el test anterior no pase por otra razon."""
    items, _ = _rastrear(config={"max_depth": 1, "robots_mode": "respect"})
    paginas = [i for i in items if i["_tipo"] == "PageItem"]
    assert paginas, "sin Disallow deberia rastrear"


def test_el_motivo_de_cero_urls_distingue_robots():
    """`_por_que_cero_urls` traduce la marca del spider a un finish_reason.

    Sin la marca es `sin_urls` (hay que mirarlo); con ella,
    `robots_bloquea_todo`, que es un hallazgo que se cuenta en una frase.
    """
    pytest.importorskip("redis")
    from crawler import worker

    class RedisDeMentira:
        def __init__(self, bloqueado):
            self.bloqueado = bloqueado

        def get(self, clave):
            return "1" if (self.bloqueado and "robots_bloquea_semillas" in clave) else None

    original = worker.redis_lib.Redis.from_url
    try:
        worker.redis_lib.Redis.from_url = lambda *a, **k: RedisDeMentira(True)
        assert worker._por_que_cero_urls("x") == "robots_bloquea_todo"
        worker.redis_lib.Redis.from_url = lambda *a, **k: RedisDeMentira(False)
        assert worker._por_que_cero_urls("x") == "sin_urls"
    finally:
        worker.redis_lib.Redis.from_url = original
