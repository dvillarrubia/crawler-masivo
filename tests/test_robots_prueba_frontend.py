"""El bloque «Probar un robots.txt» (decision 171), ejecutando `frontend/app.js`
en Chromium con `fetch` simulado: lo que manda a `POST /api/robots/probar`
(una URL y un agente por linea, sin vacias) y que «partir del guardado» carga el
cuerpo del host. Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("playwright")

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = [(RAIZ / "frontend" / n).read_text(encoding="utf-8") for n in ("incidencias.js", "app.js")]

EXPR = """async () => {
  const pedidas = [];
  window.fetch = async (url, opts) => {
    pedidas.push({ url, metodo: (opts && opts.method) || 'GET',
                   cuerpo: opts && opts.body ? JSON.parse(opts.body) : null });
    const datos = url.includes('/robots/cuerpo')
      ? { cuerpo: 'User-agent: *\\nDisallow: /x' }
      : { modo: 'google', agentes: ['Googlebot'], resultados: [{ permitida: false, linea: 2 }],
          diagnostico: { grupos: 1, sitemaps: [], ignoradas: [], recortado: false } };
    return { ok: true, status: 200, json: async () => datos };
  };
  const a = app();
  a.job = { id: 'J' };
  await a.robotsPruebaCargar('https://x.com');
  a.robotsPrueba.urls = '/x\\n\\n  /y  \\n';
  a.robotsPrueba.agentes = 'Googlebot\\n';
  await a.robotsPruebaEvaluar();
  return { pedidas, texto: a.robotsPrueba.texto, resultado: a.robotsPrueba.resultado,
           error: a.robotsPrueba.error, motivo: a.robotsPruebaMotivo('regla_sin_user_agent') };
}"""


def test_manda_lo_que_se_escribe_y_carga_el_guardado():
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
            salida = await pagina.evaluate(EXPR)
            await nav.close()
            return salida

    s = asyncio.run(run())
    assert s["error"] is None
    assert s["texto"] == "User-agent: *\nDisallow: /x"
    carga, prueba = s["pedidas"]
    assert carga["url"].endswith("/jobs/J/robots/cuerpo?origen=https%3A%2F%2Fx.com")
    assert prueba["url"].endswith("/robots/probar") and prueba["metodo"] == "POST"
    assert prueba["cuerpo"] == {"robots_txt": "User-agent: *\nDisallow: /x",
                                "urls": ["/x", "/y"], "agentes": ["Googlebot"], "modo": "google"}
    assert s["resultado"]["resultados"][0]["linea"] == 2
    assert "ningún grupo" in s["motivo"]
