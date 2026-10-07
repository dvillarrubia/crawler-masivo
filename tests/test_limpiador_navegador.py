"""El limpiador que corre EN EL NAVEGADOR no puede borrar la pagina.

Se ejecuta antes de capturar el HTML, asi que un falso positivo aqui no se
lleva solo el texto: se lleva los enlaces de esa zona y el rastreo no vuelve a
verlos. Los patrones casan por subcadena, de modo que `cookie-policy` casa con
el contenedor del contenido de la propia pagina de cookies.

Necesita Chromium: se salta si no esta (en local), corre en el contenedor del
crawler y en CI.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

pytest.importorskip("playwright")

RAIZ = Path(__file__).resolve().parent.parent
FUENTE = (RAIZ / "crawler" / "seo_crawler" / "spiders" / "seo_spider.py").read_text()
_ns: dict = {}
exec(re.search(r'_BOILERPLATE_REMOVAL_JS = """.*?"""', FUENTE, re.S).group(0), _ns)
LIMPIADOR = _ns["_BOILERPLATE_REMOVAL_JS"]

_PARRAFO = "<p>" + ("Texto legal de la pagina con palabras de sobra. " * 10) + "</p>"


def _limpiar(html: str) -> str:
    from playwright.async_api import async_playwright

    async def run() -> str:
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover - sin navegador instalado
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await nav.new_page()
            await pagina.set_content(html)
            await pagina.evaluate(LIMPIADOR)
            salida = await pagina.content()
            await nav.close()
            return salida

    return asyncio.run(run())


def test_la_pagina_de_cookies_no_se_borra_a_si_misma():
    html = (f"<html><body><main class='cookie-policy-content'><h1>Politica de cookies</h1>"
            f"{_PARRAFO}<a href='/mas'>Mas informacion</a></main></body></html>")
    salida = _limpiar(html)
    assert "Texto legal" in salida
    assert "/mas" in salida  # el enlace sobrevive: es lo que sigue el rastreo


def test_un_contenedor_de_contenido_con_el_nombre_sospechoso_sobrevive():
    html = (f"<html><body><div id='privacy-notice-body'><h1>Privacidad</h1>{_PARRAFO}"
            f"<a href='/derechos'>Tus derechos</a></div></body></html>")
    salida = _limpiar(html)
    assert "Texto legal" in salida
    assert "/derechos" in salida


def test_el_aviso_de_cookies_de_verdad_si_se_borra():
    html = (f"<html><body><div class='cookie-banner'><p>Utilizamos cookies. Aceptar</p>"
            f"<a href='/cookies'>Politica</a></div>"
            f"<main><h1>Ficha</h1>{_PARRAFO}</main></body></html>")
    salida = _limpiar(html)
    assert "Utilizamos cookies" not in salida
    assert "Texto legal" in salida


def test_el_body_no_se_borra_por_su_clase_de_estado():
    html = f"<html><body class='cookie-bar-active'><main>{_PARRAFO}</main></body></html>"
    assert "Texto legal" in _limpiar(html)
