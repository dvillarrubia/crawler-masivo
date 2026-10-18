"""La pestana Comparar con el mapeo de migracion (decision 131), ejecutando
`frontend/app.js` en Chromium: las reglas del textarea se mandan como la
lista JSON que espera `/diff` (y solo con la casilla de migracion marcada), un
422 de una regex rota llega al usuario con su motivo, y las incidencias nuevas
se reparten por tipo entre URL existente y URL nueva.

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


def _evaluar(expr: str):
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await nav.new_page()
            errores = []
            pagina.on("pageerror", lambda e: errores.append(str(e)))
            await pagina.set_content("<html><body></body></html>")
            await pagina.add_script_tag(content="window.lucide = { createIcons() {} };")
            for f in FUENTES:
                await pagina.add_script_tag(content=f)
            salida = await pagina.evaluate(expr)
            await nav.close()
            assert errores == []
            return salida

    return asyncio.run(run())


# Un componente con `api` sustituida: anota la ruta pedida y devuelve `resp`
# (o lanza `error`, como hace `api()` con un 422).
MONTAR = """
  const a = app();
  a.$nextTick = f => f && f();
  a.job = {id: 'J1', seeds: ['https://x.com/']};
  a.diffOtro = 'J0';
  window.__pedidas = [];
  window.api = async (ruta) => {
    window.__pedidas.push(ruta);
    if (window.__error) throw new Error(window.__error);
    return window.__resp || {comparable: true};
  };
"""


def test_sin_casilla_de_migracion_no_se_manda_ningun_mapeo():
    r = _evaluar("async () => {" + MONTAR + """
      a.diffMapRutas = '^/a/(.*)$ => /b/\\\\1';
      await a.cargarDiff();
      return window.__pedidas;
    }""")
    assert r == ["/jobs/J1/diff/J0"]


def test_las_reglas_viajan_como_lista_json_con_el_host():
    r = _evaluar("async () => {" + MONTAR + r"""
      a.diffMigracion = true;
      a.diffMapHost = ' viejo.com=x.com ';
      a.diffMapRutas = '^/es/hoteles/([^/]+)\\.html$ => /es/hotel/\\1/\n\n  ^/ficha\\?id=(\\d+)$ => /p/\\1  \n^/(?=x)=>y => /z';
      await a.cargarDiff();
      return {ruta: window.__pedidas[0], reglas: a.reglasMapRutas()};
    }""")
    ruta = r["ruta"]
    assert ruta.startswith("/jobs/J1/diff/J0?")
    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit(ruta).query)
    assert q["map_host"] == ["viejo.com=x.com"]
    reglas = json.loads(q["map_rutas"][0])
    # Las lineas vacias no son reglas; el separador lleva espacios, asi que un
    # `=>` pegado dentro de la regex no la parte.
    assert reglas == [[r"^/es/hoteles/([^/]+)\.html$", r"/es/hotel/\1/"],
                      [r"^/ficha\?id=(\d+)$", r"/p/\1"],
                      [r"^/(?=x)=>y", "/z"]]


def test_un_422_llega_con_el_motivo_del_servidor():
    r = _evaluar("async () => {" + MONTAR + """
      a.diffMigracion = true;
      a.diffMapRutas = '^/a/( => /b';
      window.__error = "map_rutas: regla 1: el patron '^/a/(' no compila: missing ), unterminated subpattern";
      await a.cargarDiff();
      return a.diff;
    }""")
    assert r["comparable"] is False
    assert "no compila" in r["motivo"] and "[object" not in r["motivo"]


def test_las_nuevas_se_reparten_por_tipo_entre_url_existente_y_nueva():
    diff = {"comparable": True, "incidencias": {"comparable": True, "nuevas": {
        "total": 5, "por_tipo": [{"tipo": "missing_h1", "severidad": "warning", "total": 5}],
        "en_url_existente": {"total": 2, "por_tipo": [{"tipo": "missing_h1", "total": 2}]},
        "en_url_nueva": {"total": 3, "por_tipo": [{"tipo": "missing_h1", "total": 3}]},
        "url_nueva_afirmable": True}}}
    r = _evaluar("async () => {" + MONTAR + "window.__resp = " + json.dumps(diff) + """;
      await a.cargarDiff();
      return [a.nuevasPorUrl('missing_h1'), a.nuevasPorUrl('otro')];
    }""")
    assert r == [{"existente": 2, "nueva": 3}, {"existente": 0, "nueva": 0}]


def test_un_diff_anterior_a_la_131_no_inventa_el_reparto():
    r = _evaluar("async () => {" + MONTAR + """
      window.__resp = {comparable: true, incidencias: {comparable: true, nuevas: {total: 1, por_tipo: []}}};
      await a.cargarDiff();
      return a.nuevasPorUrl('missing_h1');
    }""")
    assert r is None


def test_los_rotulos_de_lo_nuevo_estan_en_castellano():
    r = _evaluar("""() => ['se_alejan_de_la_home', 'pierden_enlaces_internos', 'pierden_pagerank',
      'pierden_datos_estructurados', 'pierden_hreflang', 'cambia_la_description'].map(fmt.reglaDiff)
      .concat(['meta_description', 'sd_tipos', 'hreflang', 'inlinks', 'click_depth', 'pagerank_score'].map(fmt.campoDiff))""")
    assert all(" " in t for t in r), r
