"""Lo que la interfaz PIDE con un segmento elegido (decision 108), ejecutando
`frontend/app.js` en Chromium con `fetch` interceptado: no se lee el fuente,
se llama a los metodos y se mira la URL que mandan.

- La lista de Problemas (`loadIssues`, a la que llevan «Ver las N
  incidencias» y «Ver todas como lista») conserva el segmento: sin el, la
  tarjeta decia «2 paginas del segmento» y la lista ensenaba las de todos.
- La vista agrupada del Explorador manda SIEMPRE `agrupar`, tambien por
  plantilla: si no, el uso (`api/uso.py`) solo veia `agrupar:segmento` y no
  se podian comparar las dos agrupaciones.

Necesita Chromium: se salta si no esta; corre en el contenedor del crawler y
en CI (el trabajo del crawler lo instala).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("playwright")

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = [(RAIZ / "frontend" / n).read_text(encoding="utf-8") for n in ("incidencias.js", "app.js")]

_STUBS = """
window.__llamadas = [];
window.lucide = { createIcons() {} };
window.fetch = async (u) => {
  window.__llamadas.push(String(u));
  return new Response(JSON.stringify({ items: [], pages: 1, total: 0, urls: 0, plantillas: [] }),
                      { status: 200, headers: { 'Content-Type': 'application/json' } });
};
"""


def _llamadas(guion: str) -> list[str]:
    from playwright.async_api import async_playwright

    async def run() -> list[str]:
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover - sin navegador instalado
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await nav.new_page()
            await pagina.set_content("<html><body></body></html>")
            await pagina.add_script_tag(content=_STUBS)
            for f in FUENTES:
                await pagina.add_script_tag(content=f)
            await pagina.evaluate("async () => { const a = app(); a.$nextTick = (f) => f && f(); "
                                  + guion + " }")
            salida = await pagina.evaluate("window.__llamadas")
            await nav.close()
            return salida

    return asyncio.run(run())


def test_la_lista_de_problemas_conserva_el_segmento():
    llamadas = _llamadas("""
        a.job = { id: 'J' };
        a.issuesSegmento = 'fichas de producto';
        a.verTipoDeIncidencia('title_too_short', 'warning');
        await new Promise(r => setTimeout(r, 50));
    """)
    lista = [u for u in llamadas if u.startswith("/api/jobs/J/issues?")]
    assert lista, llamadas
    assert "issue_type=title_too_short" in lista[-1]
    assert "segmento=fichas%20de%20producto" in lista[-1]


def test_sin_segmento_la_lista_no_lo_manda():
    llamadas = _llamadas("""
        a.job = { id: 'J' };
        await a.loadIssues();
    """)
    assert llamadas and all("segmento" not in u for u in llamadas), llamadas


@pytest.mark.parametrize("por", ["plantilla", "segmento"])
def test_la_vista_agrupada_dice_siempre_por_que_agrupa(por):
    llamadas = _llamadas(f"""
        a.job = {{ id: 'J' }};
        a.expAgrupar = true;
        a.expAgruparPor = {json.dumps(por)};
        await a.cargarPlantillas();
    """)
    agrupada = [u for u in llamadas if "/urls/plantillas?" in u]
    assert agrupada, llamadas
    assert f"agrupar={por}" in agrupada[-1]
