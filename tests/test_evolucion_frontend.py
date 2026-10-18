"""Las graficas de evolucion de la ficha del cliente y la tendencia del panel
(decision 130), ejecutando `frontend/app.js` en Chromium.

- Un punto no comparable (fallido, truncado, canario) no entra en la linea NI
  en la escala: va abajo con su motivo. Un rastreo bloqueado de 3 URLs en una
  serie de miles hundiria la escala.
- Entre dos puntos con distinta version del analisis el tramo es discontinuo
  y hay un corte con su porque (decision 72).
- La tendencia del panel no pone flecha a traves de ese corte (decision 91).

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("playwright")

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = [(RAIZ / "frontend" / n).read_text(encoding="utf-8") for n in ("incidencias.js", "app.js")]


def _p(n, errores, comparable=True, motivo=None, cambio=None):
    return {"job_id": f"j{n}", "name": f"censo {n}", "fecha": f"2026-09-{n:02d}T03:00:00Z",
            "comparable": comparable, "motivo": motivo, "errores": errores,
            "cambio_version": cambio}


PUNTOS = [
    _p(1, 100), _p(2, 120),
    _p(3, 0, comparable=False, motivo="failed"),          # bloqueado: 0 errores
    _p(4, 110),
    _p(5, 400, cambio={"analisis": True, "rastreo": False,
                       "analisis_antes": "abc1234", "analisis_despues": "def5678"}),
]


def _evaluar(expr: str):
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await nav.new_page()
            errores: list[str] = []
            pagina.on("pageerror", lambda e: errores.append(str(e)))
            await pagina.set_content("<html><body></body></html>")
            await pagina.add_script_tag(content="window.lucide = { createIcons() {} };")
            for f in FUENTES:
                await pagina.add_script_tag(content=f)
            salida = await pagina.evaluate(expr)
            await nav.close()
            assert not errores, errores
            return salida

    return asyncio.run(run())


def test_lo_no_comparable_queda_fuera_de_la_linea_y_de_la_escala():
    m = _evaluar("() => modeloGrafica(" + json.dumps(PUNTOS) + ", 'errores')")
    assert len(m["puntos"]) == 4 and len(m["fuera"]) == 1
    assert "rastreo fallido" in m["fuera"][0]["titulo"]
    assert m["min"] == 100 and m["max"] == 400          # el 0 del fallido no cuenta
    assert len(m["tramos"]) == 3                        # 1-2, 2-4, 4-5: el 3 no une
    assert [t["discontinuo"] for t in m["tramos"]] == [False, False, True]
    (corte,) = m["cortes"]
    assert corte["analisis"] and "abc1234 → def5678" in corte["titulo"]
    assert "puede ser nuestro" in corte["titulo"]
    # El corte cae entre el punto 4 y el 5.
    assert m["puntos"][2]["x"] < corte["x"] < m["puntos"][3]["x"]


def test_el_svg_pinta_puntos_cruces_y_corte_escapando_los_textos():
    puntos = PUNTOS[:2] + [dict(_p(3, 0, comparable=False, motivo="canario"), name="<b>x</b>")]
    svg = _evaluar("() => svgGrafica(modeloGrafica(" + json.dumps(puntos) + ", 'errores'))")
    assert svg.startswith("<svg") and svg.count("<circle") == 2 and svg.count("×") == 1
    assert "<b>" not in svg and "&lt;b&gt;" in svg
    svg = _evaluar("() => svgGrafica(modeloGrafica(" + json.dumps(PUNTOS) + ", 'errores'))")
    assert "evo-corte" in svg and "discontinua" in svg


def test_la_tendencia_del_panel_no_cruza_un_cambio_de_version():
    r = _evaluar("""() => [
      textoTendencia({direccion: 'no_comparable', motivo: 'Cambió la versión del análisis'}),
      textoTendencia({direccion: 'sube', delta: 28, mejora: false, antes: 12,
                      fecha_antes: '2026-09-02T00:00:00Z', ahora: 40,
                      fecha_ahora: '2026-09-09T00:00:00Z'}),
      textoTendencia({direccion: 'baja', delta: -3, mejora: true, antes: 12,
                      fecha_antes: '2026-09-02T00:00:00Z', cambio_version: {rastreo: true}}),
      textoTendencia(null), sparkline([5]), sparkline([1, 3, 2]).startsWith('<svg'),
      sparkline([1, 3, 2, 9, 8], [3])]""")
    no_comp, sube, baja, nada, corta, larga, partida = r
    assert no_comp["texto"] == "≠ versión" and no_comp["clase"] == "neutra"
    assert sube["texto"] == "▲ 28" and sube["clase"] == "peor"
    # El censo del que sale «ahora» va en el titulo: en el panel no tiene por
    # que ser el de la columna «último censo».
    assert "→" in sube["titulo"] and "40" in sube["titulo"]
    assert baja["texto"] == "▼ 3" and baja["clase"] == "mejor" and "otra versión del crawler" in baja["titulo"]
    assert nada is None and corta == "" and larga is True
    # Cortada en el cambio de version: dos tramos y un enlace gris discontinuo.
    assert partida.count('class="evo-linea"') == 2 and partida.count("discontinua") == 1
