"""La pestana Comparar ejecutando `frontend/app.js` en Chromium: no manda
ningun mapeo (la migracion es aparte, revision de UX del 25-oct, 2.13), un
error llega con su motivo, lo guardado sale al instante (V21), el desplegable
agrupa por tipo (V22) y las incidencias nuevas se reparten por tipo entre URL
existente y URL nueva (decision 131).

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


def test_comparar_no_manda_ningun_mapeo():
    """Comparar es solo el seguimiento: el mapeo de una migracion va por
    «Validar migración» (revision de UX del 25-oct, 2.13)."""
    r = _evaluar("async () => {" + MONTAR + """
      await a.cargarDiff();
      return [window.__pedidas, 'diffMigracion' in a, typeof a.reglasMapRutas];
    }""")
    assert r == [["/jobs/J1/diff/J0"], False, "undefined"]


def test_un_error_llega_con_el_motivo_del_servidor():
    r = _evaluar("async () => {" + MONTAR + """
      window.__error = "son dos origenes distintos (a.com y b.com)";
      await a.cargarDiff();
      return a.diffVista();
    }""")
    assert r["comparable"] is False
    assert "origenes distintos" in r["motivo"] and "[object" not in r["motivo"]


def test_lo_guardado_sale_al_instante_mientras_llega_el_diff():
    """V21: con la comparacion automatica guardada, Comparar la pinta ya y el
    diff entero la sustituye al llegar; una respuesta vieja no pisa otra nueva."""
    r = _evaluar("async () => {" + MONTAR + """
      a.job = {id: 'J1', seeds: ['https://x.com/'], analisis_version: 'v2', name: 'hoy',
               comparacion: {comparado_con: 'J0', comparable: true, analisis_version: 'v2',
                             alertas: [{regla: 'x'}], urls: {en_ambos: 5, nuevas: 1, desaparecidas: 0},
                             incidencias: {comparable: true, nuevas: {total: 3, por_tipo: []}}}};
      let soltar;
      window.api = (ruta) => new Promise(res => { soltar = () => res({comparable: true, cambios: {}, urls: {en_ambos: 6}}); });
      const p = a.cargarDiff();
      const antes = a.diffVista();
      const cargando = a.diffCargando;
      soltar(); await p;
      const despues = a.diffVista();
      // Otro censo elegido: lo guardado no es suyo.
      a.diffOtro = 'J9';
      const otro = a.diffVista();
      return {guardada: antes._guardada, alertas: antes.alertas.length, cambios: antes.cambios,
              inc: antes.incidencias.nuevas.total, cargando, despuesGuardada: !!despues._guardada,
              en_ambos: despues.urls.en_ambos, otro};
    }""")
    assert r == {"guardada": True, "alertas": 1, "cambios": None, "inc": 3, "cargando": True,
                 "despuesGuardada": False, "en_ambos": 6, "otro": None}


def test_lo_guardado_no_ensena_incidencias_si_el_censo_se_reanalizo():
    r = _evaluar("async () => {" + MONTAR + """
      a.job = {id: 'J1', seeds: ['https://x.com/'], analisis_version: 'v3',
               comparacion: {comparado_con: 'J0', comparable: true, analisis_version: 'v2',
                             incidencias: {comparable: true, nuevas: {total: 3, por_tipo: []}}}};
      window.api = () => new Promise(() => {});
      a.cargarDiff();
      const d = a.diffVista();
      return [d.incidencias, d._incidenciasViejas];
    }""")
    assert r == [None, True]


def test_el_desplegable_agrupa_por_tipo_y_no_por_tamano():
    """V22: la replica grande es una prueba aunque tenga el tamano de un censo;
    solo salen los del mismo host y se piden los del cliente."""
    r = _evaluar("async () => {" + MONTAR + """
      a.job = {id: 'J1', cliente_id: 'C1', seeds: ['https://x.com/'], total_urls_crawled: 5000};
      window.api = async (ruta) => { window.__pedidas.push(ruta); return {items: [
        {id: 'J1', status: 'completed', seeds: ['https://x.com/'], tipo_efectivo: 'censo'},
        {id: 'A', status: 'completed', seeds: ['https://x.com/'], tipo_efectivo: 'censo', total_urls_crawled: 4900},
        {id: 'B', status: 'completed', seeds: ['https://x.com/'], tipo_efectivo: 'prueba', total_urls_crawled: 5100},
        {id: 'C', status: 'completed', seeds: ['https://x.com/'], tipo_efectivo: 'canario', total_urls_crawled: 26},
        {id: 'D', status: 'completed', seeds: ['https://otro.com/'], tipo_efectivo: 'censo'},
      ]}; };
      await a.cargarCandidatosDiff();
      return {pedida: window.__pedidas[0], grupos: a.gruposDiff().map(g => [g.etiqueta, g.items.map(j => j.id)])};
    }""")
    assert "client_id=C1" in r["pedida"] and "status=completed" in r["pedida"]
    assert r["grupos"] == [["Censos", ["A"]], ["Canarios", ["C"]], ["Pruebas", ["B"]]]


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
