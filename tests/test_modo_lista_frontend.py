"""El formulario de lanzar con un perfil en modo lista (decision 128),
ejecutando `frontend/app.js` en Chromium: se llama a `_formDesdeConfig` y a
`_configDesdeForm` y se mira lo que se mandaria.

Un perfil con `max_depth: 0` y sin `use_sitemap` hereda «sin sitemap» en la API
(`JobConfig._lista_sin_sitemap`). El modal precarga `config_heredada` —la
parcial, sin `use_sitemap`—, la casilla se quedaba marcada por defecto y el job
se mandaba con `use_sitemap: true` EXPLICITO: la lista sembraba el sitemap
entero.

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("playwright")

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = [(RAIZ / "frontend" / n).read_text(encoding="utf-8") for n in ("incidencias.js", "app.js")]


def _evaluar(expr: str):
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await nav.new_page()
            await pagina.set_content("<html><body></body></html>")
            await pagina.add_script_tag(content="window.lucide = { createIcons() {} };")
            for f in FUENTES:
                await pagina.add_script_tag(content=f)
            salida = await pagina.evaluate(expr)
            await nav.close()
            return salida

    return asyncio.run(run())


def _lanzado_con(cfg: str):
    return _evaluar(
        "() => { const f = _freshForm(); "
        "f.max_depth = 3; _formDesdeConfig(f, " + cfg + "); return _configDesdeForm(f); }")


def test_un_perfil_en_modo_lista_se_lanza_sin_sitemap():
    c = _lanzado_con("{max_depth: 0}")
    assert c["max_depth"] == 0
    assert c["use_sitemap"] is False


def test_si_el_perfil_pide_el_sitemap_se_respeta():
    assert _lanzado_con("{max_depth: 0, use_sitemap: true}")["use_sitemap"] is True


def test_un_perfil_normal_no_toca_el_sitemap():
    assert _lanzado_con("{max_depth: 5}")["use_sitemap"] is True
