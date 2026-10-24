"""La interfaz de la accesibilidad con axe (decision 174), ejecutando
`frontend/app.js` en Chromium: las claves del modal solo se mandan si se
cambian (como las demas sondas, para seguir heredando del cliente y el
perfil), el nombre del impacto y el ambito del Explorador con los dos tipos.
La tarjeta del Resumen se comprueba con su marcado (`data-testid`).

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


def _config(cuerpo: str = "") -> dict:
    return _evaluar("() => { const f = _freshForm(); " + cuerpo + " return _configDesdeForm(f); }")


def test_sin_tocarlo_no_se_manda_y_cambiado_si():
    cb = _config()["crawl_behavior"]
    assert "axe_muestra" not in cb and "axe_por_plantilla" not in cb
    cb = _config("f.axe_muestra = '30'; f.axe_por_plantilla = 2;")["crawl_behavior"]
    assert cb["axe_muestra"] == 30 and cb["axe_por_plantilla"] == 2


def test_impacto_y_ambito():
    r = _evaluar("""() => ({
        critico: impactoAxe('critical'), leve: impactoAxe('minor'), raro: impactoAxe(null),
        ambito: EXP_TABS.find(v => v.key === 'a_a11y'),
        nombres: [fmt.issue('accesibilidad_grave'), fmt.issue('accesibilidad_menor')],
        detalle: fmt.detalles({regla: 'image-alt', impacto: 'crítico', impacto_axe: 'critical', wcag: '1.1.1', url_ayuda: 'https://u', nodos: 3}),
    })""")
    assert r["critico"] == ["Crítico", "red"] and r["leve"][0] == "Leve" and r["raro"][0] == "—"
    assert r["ambito"]["ambito"] == ["accesibilidad_grave", "accesibilidad_menor"]
    assert r["nombres"] == ["Accesibilidad: fallo WCAG crítico o grave",
                            "Accesibilidad: fallo WCAG moderado o leve"]
    assert r["detalle"] == "regla de axe image-alt · impacto crítico · WCAG 1.1.1 · elementos 3"


def test_la_tarjeta_no_afirma_sin_paginas_evaluadas():
    html = (RAIZ / "frontend" / "index.html").read_text(encoding="utf-8")
    tarjeta = html.split('data-testid="tarjeta-accesibilidad"', 1)[1].split("</template>\n\n", 1)[0]
    assert 'data-testid="accesibilidad-sin-evaluar"' in tarjeta
    assert "No se puede decir nada" in tarjeta
    assert "30-40" in tarjeta and "sin afirmar" in tarjeta
