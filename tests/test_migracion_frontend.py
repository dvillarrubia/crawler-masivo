"""«Validar migración» (revision de UX del 25-oct, 2.13; decision 192),
ejecutando `frontend/app.js` en Chromium.

Lo importante: las reglas que salen de los casos comunes (sin regex) se
aplican en el servidor (`shared.comparacion._aplicar_rutas`, la primera que
casa gana) EXACTAMENTE como en la vista previa del navegador. Se comprueba
corriendo las dos: la vista previa en Chromium y las reglas en Python.

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright")

from shared.comparacion import _aplicar_mapa, _aplicar_rutas, compilar_mapa_rutas  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = [(RAIZ / "frontend" / n).read_text(encoding="utf-8") for n in ("incidencias.js", "app.js")]


def _evaluar(expr: str, arg=None):
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
            salida = await pagina.evaluate(expr, arg)
            await nav.close()
            assert errores == []
            return salida

    return asyncio.run(run())


URLS = [
    "https://viejo.com/",
    "https://viejo.com/es/",
    "https://viejo.com/es/hoteles/gran-canaria.html",
    "https://viejo.com/es/hoteles/gran-canaria.html?utm=1",
    "https://viejo.com/es/blog/",
    "https://viejo.com/en/hoteles/x.html",
    "https://viejo.com/es/hoteles/2024.html",
    "https://viejo.com/es/ofertas",
    "https://viejo.com/es/ofertas?p=2",
    "https://viejo.com/hoteles-es/a/",
    "https://otro.com/es/hoteles/a.html",
    "https://viejo.com/es/a$b/c\\d.html",
]

CASOS = [
    {"prefijo": True, "prefDe": "/es/", "prefA": "/"},
    {"final": "quitar_ext"},
    {"final": "quitar_ext_barra"},
    {"final": "poner_barra"},
    {"final": "quitar_barra"},
    {"texto": True, "textoDe": "/hoteles/", "textoA": "/hotel/"},
    {"prefijo": True, "prefDe": "/es/", "prefA": "/", "final": "quitar_ext_barra"},
    {"prefijo": True, "prefDe": "/es/", "prefA": "/$1\\x/", "texto": True,
     "textoDe": "hoteles", "textoA": "hotel", "final": "quitar_ext"},
    {"prefijo": True, "prefDe": "/es/", "prefA": "/es-es/", "texto": True,
     "textoDe": ".", "textoA": "-", "final": "poner_barra"},
    {"dominio": True, "hostDe": "viejo.com", "hostA": "nuevo.com",
     "prefijo": True, "prefDe": "/es/", "prefA": "/"},
    {"prefijo": True, "prefDe": "/es/", "prefA": "/", "final": "quitar_barra"},
    {"texto": True, "textoDe": "/hoteles/", "textoA": "/", "final": "quitar_barra"},
    {"prefijo": True, "prefDe": "/es/", "prefA": "/es-es/", "texto": True,
     "textoDe": "/blog/", "textoA": "/", "final": "quitar_barra"},
    {"texto": True, "textoDe": "gran-canaria.html", "textoA": "gc.html", "final": "quitar_ext"},
]


def _servidor(urls, reglas, map_host):
    """Lo que hace el servidor con esas reglas (decision 131)."""
    mapa = {}
    for par in (map_host or "").split(","):
        if "=" in par:
            a, b = par.split("=", 1)
            mapa[a.strip().lower()] = b.strip().lower()
    compiladas = compilar_mapa_rutas(reglas)
    return [_aplicar_rutas(_aplicar_mapa(u, mapa), compiladas)[0] for u in urls]


@pytest.mark.parametrize("casos", CASOS, ids=[json.dumps(c) for c in CASOS])
def test_la_vista_previa_es_lo_que_hace_el_servidor(casos):
    r = _evaluar("""([casos, urls]) => {
      const a = app();
      a.migCasos = { ...migCasosVacios(), ...casos };
      const reglas = a.migReglas(), host = a.migMapHost();
      return { api: reglasParaApi(reglas), host,
               previa: urls.map(u => aplicarMigracion(u, reglas, mapaDeHosts(host)).url) };
    }""", [casos, URLS])
    assert r["api"], "los casos tienen que dar alguna regla"
    assert r["previa"] == _servidor(URLS, r["api"], r["host"])


def test_dos_casos_a_la_vez_se_aplican_los_dos():
    """La primera regla que casa gana: con una regla por caso, la URL con
    carpeta Y extension solo cambiaria una cosa. Se genera una por combinacion."""
    r = _evaluar("""() => {
      const reglas = reglasDeMigracion({ ...migCasosVacios(), prefijo: true, prefDe: '/es/', prefA: '/',
                                         final: 'quitar_ext_barra' });
      return reglasParaApi(reglas);
    }""")
    # Carpeta y final, su borde (la ruta acaba en la carpeta), solo el final
    # y solo la carpeta.
    assert len(r) == 4
    salida = _servidor(["https://x.com/es/a.html", "https://x.com/es/b/", "https://x.com/c.html"], r, "")
    assert salida == ["https://x.com/a/", "https://x.com/b/", "https://x.com/c/"]


BORDES = [
    # La raiz de la carpeta o la URL que acaba en el trozo: estos se comen la
    # barra o la extension, y aun asi cambian los dos (hallazgo de la revision
    # de 2.13: /blog/ quedaba en /blog y no en /).
    ({"prefijo": True, "prefDe": "/blog/", "prefA": "/", "final": "quitar_barra"},
     {"https://www.lopesan.com/blog/": "https://www.lopesan.com/",
      "https://www.lopesan.com/blog/?p=2": "https://www.lopesan.com/?p=2",
      "https://www.lopesan.com/blog/a/": "https://www.lopesan.com/a",
      "https://www.lopesan.com/hoteles/": "https://www.lopesan.com/hoteles"}),
    ({"texto": True, "textoDe": "/hoteles/", "textoA": "/hotel/", "final": "quitar_barra"},
     {"https://x.com/es/hoteles/": "https://x.com/es/hotel",
      "https://x.com/es/hoteles/a/": "https://x.com/es/hotel/a"}),
    ({"prefijo": True, "prefDe": "/es/", "prefA": "/", "final": "quitar_barra"},
     {"https://x.com/es/": "https://x.com/", "https://x.com/es/a/": "https://x.com/a"}),
    ({"prefijo": True, "prefDe": "/es/", "prefA": "/es-es/", "final": "quitar_barra"},
     {"https://x.com/es/": "https://x.com/es-es"}),
    ({"texto": True, "textoDe": "/hoteles/", "textoA": "/", "final": "quitar_barra"},
     {"https://x.com/es/hoteles/": "https://x.com/es", "https://x.com/hoteles/": "https://x.com/"}),
    ({"texto": True, "textoDe": "gran-canaria.html", "textoA": "gc.html", "final": "quitar_ext"},
     {"https://x.com/es/gran-canaria.html": "https://x.com/es/gc"}),
    ({"texto": True, "textoDe": "/hoteles", "textoA": "/hotel", "final": "poner_barra"},
     {"https://x.com/es/hoteles": "https://x.com/es/hotel/"}),
]


@pytest.mark.parametrize("casos,esperado", BORDES, ids=[json.dumps(c) for c, _ in BORDES])
def test_la_carpeta_o_el_trozo_al_final_tambien_cambian(casos, esperado):
    urls = list(esperado)
    r = _evaluar("""([casos, urls]) => {
      const reglas = reglasDeMigracion({ ...migCasosVacios(), ...casos });
      return { api: reglasParaApi(reglas), previa: urls.map(u => aplicarMigracion(u, reglas, {}).url) };
    }""", [casos, urls])
    assert r["previa"] == list(esperado.values())
    assert _servidor(urls, r["api"], "") == list(esperado.values())


def test_las_regex_a_mano_se_previsualizan_y_se_mandan_tal_cual():
    reglas_txt = "^/es/hoteles/(?P<n>[^/]+)\\.html$ => /hotel/\\g<n>/\n\n^/ficha\\?id=(\\d+)$ => /p/\\1"
    r = _evaluar("""(txt) => {
      const a = app();
      a.migAvanzado = true; a.migReglasTexto = txt;
      const reglas = a.migReglas();
      return { api: reglasParaApi(reglas),
               previa: ['https://x.com/es/hoteles/sol.html', 'https://x.com/ficha?id=7', 'https://x.com/otra']
                 .map(u => aplicarMigracion(u, reglas, {}).url) };
    }""", reglas_txt)
    assert r["api"] == [["^/es/hoteles/(?P<n>[^/]+)\\.html$", "/hotel/\\g<n>/"], ["^/ficha\\?id=(\\d+)$", "/p/\\1"]]
    assert r["previa"] == _servidor(["https://x.com/es/hoteles/sol.html", "https://x.com/ficha?id=7",
                                     "https://x.com/otra"], r["api"], "")


def test_una_regex_que_el_navegador_no_lee_no_rompe_la_vista_previa():
    r = _evaluar("""() => {
      const a = app();
      a.migAvanzado = true; a.migReglasTexto = '^/a/( => /b';
      a.migMuestra = ['https://x.com/a/1'];
      return a.migVistaPrevia();
    }""")
    assert len(r) == 1 and "Regla 1" in r[0]["error"]


def test_calcular_pide_el_diff_de_siempre_con_el_mapeo():
    r = _evaluar("""async () => {
      const a = app();
      a.$nextTick = f => f && f();
      a.job = {id: 'J1', seeds: ['https://nuevo.com/']};
      a.migCandidatos = [{id: 'J0', created_at: '2026-01-01', seeds: ['https://viejo.com/']},
                         {id: 'J1', created_at: '2026-02-01', seeds: ['https://nuevo.com/']}];
      a.migAntes = 'J0'; a.migDespues = 'J1';
      a.migElegido();
      a.migCasos.prefijo = true; a.migCasos.prefDe = '/es/'; a.migCasos.prefA = '/';
      window.__pedidas = [];
      window.api = async (ruta) => { window.__pedidas.push(ruta);
        return {comparable: true, urls: {en_ambos: 9, nuevas: 1, desaparecidas: 1},
                mapeo_rutas: {urls_del_anterior: 10, emparejadas: 9, pct_emparejadas: 90.0}}; };
      await a.calcularMigracion();
      return {ruta: window.__pedidas[0], dominio: [a.migCasos.dominio, a.migMapHost()], cifra: a.migEmparejadas()};
    }""")
    ruta = r["ruta"]
    assert ruta.startswith("/jobs/J1/diff/J0?")
    q = parse_qs(urlsplit(ruta).query)
    # Dominios distintos: el cambio de dominio se propone ya relleno.
    assert r["dominio"] == [True, "viejo.com=nuevo.com"]
    assert q["map_host"] == ["viejo.com=nuevo.com"]
    assert json.loads(q["map_rutas"][0]) == [["^/es/(.*?)(\\?.*)?$", "/\\g<1>\\g<2>"]]
    assert r["cifra"] == {"de": 10, "si": 9, "pct": 90.0}


def test_antes_posterior_a_despues_no_deja_calcular():
    r = _evaluar("""async () => {
      const a = app();
      a.job = {id: 'J1'};
      a.migCandidatos = [{id: 'J0', created_at: '2026-01-01'}, {id: 'J1', created_at: '2026-02-01'}];
      a.migAntes = 'J1'; a.migDespues = 'J0';
      window.__pedidas = [];
      window.api = async (r) => { window.__pedidas.push(r); return {}; };
      const problema = a.migProblemaPareja();
      await a.calcularMigracion();
      a.migIntercambiar();
      return [problema, window.__pedidas.length, a.migPaso, a.migProblemaPareja(), a.migAntes];
    }""")
    assert "posterior" in r[0]
    assert r[1:] == [0, 1, "", "J0"]


def test_la_ruta_guarda_los_censos_el_paso_y_los_casos():
    """Un enlace reproduce la validacion; el de Comparar con el mapeo de antes
    (map_host / map_rutas) abre el asistente con lo mismo."""
    r = _evaluar("""async () => {
      const a = app();
      a.job = {id: 'J1'}; a.view = 'detail'; a.detailTab = 'migracion';
      a.migAntes = 'J0'; a.migDespues = 'J1'; a.migPaso = 2;
      a.migCasos = { ...migCasosVacios(), prefijo: true, hostDe: 'x.com', hostA: 'x.com' };
      const h = a._ruta();
      a.migLeerRuta(new URLSearchParams(h.split('?')[1]));
      return [h, a._migDesdeRuta];
    }""")
    h, leida = r
    q = parse_qs(urlsplit(h.replace("#", "", 1)).query)
    assert h.startswith("#/rastreos/J1/migracion?")
    assert q["antes"] == ["J0"] and q["paso"] == ["2"] and "despues" not in q
    # Los dominios de relleno no viajan si no se ha marcado el cambio.
    assert json.loads(q["casos"][0]) == {"prefijo": True}
    assert leida == {"antes": "J0", "despues": "", "casos": {"prefijo": True}, "rutas": "", "paso": 2}


def test_el_censo_abierto_reinicia_lo_elegido():
    r = _evaluar("""() => {
      const a = app();
      a.migAntes = 'X'; a.migPaso = 3; a.migDiff = {comparable: true}; a.migCasos.prefijo = true;
      a._migReiniciar();
      return [a.migAntes, a.migPaso, a.migDiff, a.migCasos.prefijo];
    }""")
    assert r == ["", 1, None, False]


def test_la_vista_previa_no_afirma_que_falte_sin_haberlo_visto_todo():
    """La comprobacion busca por trozo: si trae mas de lo que cabe en la
    pagina y la exacta no esta, no se dice que falte (hallazgo de la revision
    de 2.13). Pide ordenado por URL, que pone la exacta delante."""
    r = _evaluar("""async () => {
      const a = app();
      a.$nextTick = f => f && f();
      a.migDespues = 'J1'; a.migPaso = 2;
      a.migMuestra = ['https://x.com/a/', 'https://x.com/b/', 'https://x.com/c/'];
      window.__pedidas = [];
      window.api = async (ruta) => { window.__pedidas.push(ruta);
        const q = new URLSearchParams(ruta.split('?')[1]), s = q.get('search');
        if (s === 'https://x.com/a/') return {total: 900, items: [{url: 'https://x.com/a/1'}]};
        if (s === 'https://x.com/b/') return {total: 1, items: [{url: 'https://x.com/b/1'}]};
        return {total: 300, items: [{url: 'https://x.com/c/'}]}; };
      await a.comprobarVistaPrevia();
      const f = u => ({despues: u});
      return {existe: a.migExiste, textos: ['a', 'b', 'c'].map(x => a.migTextoExiste(f('https://x.com/' + x + '/'))),
              pedida: window.__pedidas[0]};
    }""")
    assert r["existe"] == {"https://x.com/a/": "sin_comprobar", "https://x.com/b/": False, "https://x.com/c/": True}
    assert r["textos"][0].startswith("no se ha podido comprobar")
    assert r["textos"][1].startswith("no aparece tal cual")
    assert r["textos"][2] == "está en el censo de después"
    q = parse_qs(urlsplit(r["pedida"]).query)
    assert q["sort_by"] == ["url"] and q["page_size"] == ["200"]
