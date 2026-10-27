"""Cierre de lo que la auditoria independiente del 27-oct dejo abierto en la
fase 3 de la revision de UX (decisiones 202, 203 y 204), ejecutando
`frontend/app.js` en Chromium.

- Problemas: una fila por tipo con el desglose de gravedad, y las paginas del
  tipo solo si se saben (sumar las de cada gravedad contaria dos veces la que
  tenga las dos). El contador de la pestana son tipos de error o aviso.
- «No incluida en el sitemap» no entra en la diferencia con el censo anterior
  si no se leyeron todos los sitemaps.
- Las cifras de errores son paginas (la misma que la tarjeta del censo); con
  una API anterior salen de la evolucion o se dice que son incidencias.
- Alertas del cliente agrupadas por regla, las info aparte.
- Las frases de Google enlazan a sus paginas solo si se sabe de que grupo
  salen.
- Una API anterior no ensena «Method Not Allowed» ni «Unexpected token '<'».

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import asyncio
import json
import re
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


# Lo que devuelve /issues/resumen: canibalizacion en dos gravedades, con las
# paginas distintas del tipo (decision 202); una API anterior no trae
# `paginas_tipo`.
RESUMEN = [
    {"issue_type": "status_4xx", "severity": "error", "incidencias": 41, "paginas": 41, "paginas_tipo": 41,
     "prioridad": 30, "ejemplos": [{"url_id": 1, "url": "https://x/a"}]},
    {"issue_type": "canibalizacion_por_consulta", "severity": "warning", "incidencias": 39, "paginas": 39,
     "paginas_tipo": 190, "prioridad": 10, "ejemplos": [{"url_id": 2, "url": "https://x/b"}]},
    {"issue_type": "canibalizacion_por_consulta", "severity": "info", "incidencias": 157, "paginas": 157,
     "paginas_tipo": 190, "prioridad": 1, "ejemplos": [{"url_id": 2, "url": "https://x/b"}, {"url_id": 3, "url": "https://x/c"}]},
    {"issue_type": "image_missing_alt", "severity": "info", "incidencias": 5, "paginas": 5, "paginas_tipo": 5,
     "prioridad": 0.5, "ejemplos": []},
]


def test_una_fila_por_tipo_con_el_desglose():
    r = _evaluar("() => { const f = filasPorTipo(%s); return f.map(x => ({t: x.issue_type, s: x.severity, "
                 "p: x.paginas, i: x.incidencias, d: desgloseGravedad(x), e: x.ejemplos.map(e => e.url_id)})) }"
                 % json.dumps(RESUMEN))
    assert [x["t"] for x in r] == ["status_4xx", "canibalizacion_por_consulta", "image_missing_alt"]
    canib = r[1]
    # La gravedad mas alta, las paginas distintas de la API (190, no 196) y el desglose.
    assert canib["s"] == "warning" and canib["p"] == 190 and canib["i"] == 196
    assert canib["d"] == "39 aviso · 157 info"
    assert canib["e"] == [2, 3]                       # sin repetir la pagina 2


def test_sin_las_paginas_del_tipo_no_se_suman():
    sin = [{k: v for k, v in x.items() if k != "paginas_tipo"} for x in RESUMEN]
    r = _evaluar("() => filasPorTipo(%s)[1].paginas" % json.dumps(sin))
    assert r is None
    # Quitadas las info («Qué arreglar primero»), el tipo ya no esta entero:
    # sus paginas distintas tampoco valen y la fila es la del aviso.
    r = _evaluar("() => { const t = %s; return filasPorTipo(t.filter(x => x.severity !== 'info'), t)"
                 ".map(x => [x.issue_type, x.paginas, (x.partes || []).length]) }" % json.dumps(RESUMEN))
    assert r == [["status_4xx", 41, 1], ["canibalizacion_por_consulta", 39, 1]]


def test_problemas_y_el_contador_de_la_pestana():
    r = _evaluar("""() => {
        const a = app();
        a.issuesResumen = %s;
        const todas = a.resumenIssues('');
        return {
            todas: todas.map(x => x.issue_type),
            misma: a.resumenIssues('') === todas,       // por identidad: cambioDeTipo la compara
            info: a.resumenIssues('info').map(x => x.issue_type + ':' + x.paginas),
            contador: a.tiposQueMirar({ issues_by_type: [
                { issue_type: 'status_4xx', severity: 'error', count: 41 },
                { issue_type: 'canibalizacion_por_consulta', severity: 'warning', count: 39 },
                { issue_type: 'canibalizacion_por_consulta', severity: 'info', count: 157 },
                { issue_type: 'image_missing_alt', severity: 'info', count: 11000 }] }),
        };
    }""" % json.dumps(RESUMEN))
    assert r["todas"] == ["status_4xx", "canibalizacion_por_consulta", "image_missing_alt"]
    assert r["misma"] is True
    # Con un filtro de gravedad, las filas de esa gravedad tal cual.
    assert r["info"] == ["canibalizacion_por_consulta:157", "image_missing_alt:5"]
    # Dos tipos que mirar, no 11.237 incidencias.
    assert r["contador"] == 2


def test_la_diferencia_no_cuenta_lo_que_no_se_puede_afirmar():
    r = _evaluar("""() => {
        const a = app();
        a.issuesResumen = [{ issue_type: 'not_in_sitemap', severity: 'info', paginas: 3209 },
                           { issue_type: 'status_4xx', severity: 'error', paginas: 41 }];
        const diff = { ok: true, ref: 'frente al 19 oct', porTipo: {
            not_in_sitemap: { nuevas: 66, resueltas: 2, siguen: 3141 },
            status_4xx: { nuevas: 3, resueltas: 0, siguen: 38 } }, dudosos: ['not_in_sitemap'] };
        const filas = a.resumenIssues('');
        return filas.map(f => (a.cambioDeTipo(f, diff) || {}).texto || null);
    }""")
    assert r == [None, "+3"]


def test_la_duda_de_los_sitemaps_sale_de_los_ficheros_vacios():
    job = {"sitemaps": {"pedidos": 3, "leidos": 3, "ficheros": [
        {"formato": "urlset", "status": 200, "devueltas": 10},
        {"formato": "desconocido", "status": 200, "bytes": 0},
        {"formato": "desconocido", "status": 200, "bytes": 0}]}}
    r = _evaluar("() => [dudaSitemaps(%s), dudaSitemaps({ sitemaps: { ficheros: [{ formato: 'urlset', status: 200, devueltas: 3 }] } })]"
                 % json.dumps(job))
    assert "2 de los 3 ficheros de sitemap respondieron 200 vacíos" in r[0]
    assert "no se puede afirmar" in r[0]
    assert r[1] is None


def test_las_cifras_de_errores_son_paginas():
    r = _evaluar("""() => {
        const a = app();
        a.clienteEvolucion = { metricas: { urls_con_error: { etiqueta: 'URLs con error', mejor: 'baja' } },
            series: [{ clave: 'p', puntos: [{ job_id: 'j1', urls_con_error: 72, errores: 85, comparable: true }] }] };
        return {
            nueva: a.paginasConError({ id: 'j9', paginas_con_error: 70, errores: 83 }),
            deLaEvolucion: a.paginasConError({ id: 'j1', errores: 85 }),
            sinSaber: a.paginasConError({ id: 'j2', errores: 85 }),
            etiqueta: a.etiquetaMetrica('urls_con_error'),
            graficadas: METRICAS_GRAFICADAS[0],
        };
    }""")
    assert r == {"nueva": 70, "deLaEvolucion": 72, "sinSaber": None,
                 "etiqueta": "Páginas con error", "graficadas": "urls_con_error"}


def test_alertas_agrupadas_por_regla_y_las_info_aparte():
    incidentes = [
        {"id": "a", "clave": "paginas_404", "regla": "paginas_404", "severidad": "warning", "estado": "abierto",
         "abierto_en": "2026-10-19T03:00:00Z", "nombre_serie": "Semanal", "abierto_job_id": "j19",
         "titulo": "Páginas que ya no se encuentran", "detalle": None},
        {"id": "b", "clave": "paginas_404", "regla": "paginas_404", "severidad": "critical", "estado": "visto",
         "abierto_en": "2026-10-21T03:00:00Z", "nombre_serie": "Semanal bis", "abierto_job_id": "j21",
         "ultimo_job_id": "j21", "titulo": "Páginas que ya no se encuentran", "detalle": None},
        {"id": "c", "clave": "indexables_suben", "regla": "indexables_suben", "severidad": "info", "estado": "abierto",
         "abierto_en": "2026-10-21T03:00:00Z", "titulo": "Suben las indexables", "detalle": None},
    ]
    r = _evaluar("""() => {
        const a = app();
        a.incidentes = %s;
        const g = a.gruposIncidentes(false), f = a.gruposIncidentes(true);
        return { graves: g.map(x => [x.clave, x.severidad, x.items.map(i => i.id), x.ultimo.id, x.estados, x.series]),
                 aFavor: f.map(x => x.clave) };
    }""" % json.dumps(incidentes))
    assert r["graves"] == [["paginas_404", "critical", ["a", "b"], "b", ["abierto", "visto"], ["Semanal", "Semanal bis"]]]
    assert r["aFavor"] == ["indexables_suben"]


TRAFICO = {
    "comparable": True,
    "caidas": [
        {"cambio": "title", "texto": "cambia el title", "paginas": 2, "diferencia": -50,
         "ejemplos": [{"url": "https://x.com/a"}, {"url": "https://x.com/b"}]},
        {"cambio": "sin_cambio", "paginas": 3, "diferencia": -20, "ejemplos": [{"url": "https://x.com/c"}]},
    ],
    "subidas": [
        {"cambio": "title", "texto": "cambia el title", "paginas": 1, "diferencia": 109,
         "ejemplos": [{"url": "https://x.com/es/?q=1"}]},
    ],
}


def test_cada_frase_de_google_enlaza_a_sus_paginas_si_se_sabe():
    r = _evaluar("""() => { const t = %s; return [
        paginasDeFraseTrafico('2 páginas perdieron 50 clics; coincide con que en ellas cambia el title entre los dos rastreos.', t),
        paginasDeFraseTrafico('3 páginas perdieron 20 clics sin cambio técnico en el rastreo: puede ser demanda, competencia o la página de resultados.', t),
        paginasDeFraseTrafico('1 página ganó 109 clics; coincide con que en ella cambia el title.', t),
        paginasDeFraseTrafico('7 páginas perdieron 9 clics; coincide con que en ellas cambia el title entre los dos rastreos.', t),
        paginasDeFraseTrafico('La caída es general en todo el sitio, no de unas páginas concretas.', t),
        rutaCorta('https://x.com/es/?q=1'),
    ] }""" % json.dumps(TRAFICO))
    assert r[0] == ["https://x.com/a", "https://x.com/b"]
    assert r[1] == ["https://x.com/c"]
    assert r[2] == ["https://x.com/es/?q=1"]
    assert r[3] == [] and r[4] == []                   # ningun grupo con 7: sin enlaces
    assert r[5] == "/es/?q=1"


def test_frases_iguales_de_dos_grupos_no_se_adivinan():
    t = json.loads(json.dumps(TRAFICO))
    t["caidas"].append(dict(t["caidas"][0], ejemplos=[{"url": "https://x.com/z"}]))
    r = _evaluar("() => paginasDeFraseTrafico('2 páginas perdieron 50 clics; coincide con que en ellas cambia el title entre los dos rastreos.', %s)"
                 % json.dumps(t))
    assert r == []


def test_el_panel_dice_de_cuantos_enlaces_son_los_200():
    r = _evaluar("""() => {
        const a = app();
        const doscientos = Array.from({ length: 200 }, (_, n) => ({ id: n }));
        return [
            a.cuentaEnlacesPanel({ inlinks: doscientos, inlinks_count: 4720 }, 'inlinks'),
            a.cuentaEnlacesPanel({ inlinks: doscientos }, 'inlinks'),
            a.cuentaEnlacesPanel({ inlinks: doscientos, inlinks_count: 200 }, 'inlinks'),
            a.cuentaEnlacesPanel({ outlinks: [{ id: 1 }], outlinks_count: 1 }, 'outlinks'),
        ];
    }""")
    assert r == ["200 de 4.720", "200+", "200", "1"]


def test_una_api_anterior_no_ensena_el_error_en_ingles():
    r = _evaluar("""async () => {
        const probar = async (respuesta) => {
            window.fetch = async () => respuesta;
            try { await api('/clients/config-resuelta', { method: 'POST' }); return 'sin error'; }
            catch (e) { return [e.message, !!e.sinRuta]; }
        };
        return [
            await probar(new Response(JSON.stringify({ detail: 'Method Not Allowed' }), { status: 405, headers: { 'Content-Type': 'application/json' } })),
            await probar(new Response('<!DOCTYPE html><html></html>', { status: 200, headers: { 'Content-Type': 'text/html' } })),
            await probar(new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404, headers: { 'Content-Type': 'application/json' } })),
            await probar(new Response(JSON.stringify({ detail: 'Cliente no encontrado' }), { status: 404, headers: { 'Content-Type': 'application/json' } })),
        ];
    }""")
    sin_ruta = "Este servidor todavía no tiene esta función: es una versión anterior."
    assert r[0] == [sin_ruta, True]
    assert r[1] == [sin_ruta, True]
    assert r[2] == [sin_ruta, True]
    # Un 404 con su motivo es del dato, no de la version: se dice tal cual.
    assert r[3] == ["Cliente no encontrado", False]


def test_sin_lo_de_serie_el_resumen_no_dice_undefined():
    r = _evaluar("""() => {
        const a = app();
        a.cfgRastreo = cfgNuevo('rastreo', { base: {}, propia: {}, ctx: {} });
        a.cfgRastreo.resueltaError = 'Este servidor todavía no tiene esta función: es una versión anterior.';
        const sin = a.resumenConfig();
        a.cfgRastreo = cfgNuevo('rastreo', { base: { max_urls: null, concurrent_requests_per_domain: 4, render_js: false }, propia: {}, ctx: {} });
        return [sin, a.resumenConfig()];
    }""")
    assert "undefined" not in r[0] and "sin tope" not in r[0]
    assert r[0] == "lo de serie del servidor"
    assert r[1] == "sin tope de URLs · 4 a la vez por dominio · sin render JS"


def test_el_tipo_del_modal_de_serie():
    r = _evaluar("""() => {
        const a = app();
        a.createForm = _freshForm();
        const sinPerfil = a.tipoRastreoModal();
        a.createForm.profile_id = 'p1';
        const conPerfil = a.tipoRastreoModal();
        a.createForm.tipo = 'canario';
        return [sinPerfil, conPerfil, a.tipoRastreoModal()];
    }""")
    assert r == ["prueba", "censo", "canario"]


def test_los_textos_que_la_auditoria_vio_mal():
    """V42 y UX-20/14: lo que la auditoria del 27-oct encontro. Lee el fuente
    a proposito (es una lista de cadenas que no deben volver); lo que se
    ejecuta esta en los tests de arriba."""
    html = (RAIZ / "frontend" / "index.html").read_text(encoding="utf-8")
    js = (RAIZ / "frontend" / "app.js").read_text(encoding="utf-8")
    api = (RAIZ / "api" / "routers" / "results.py").read_text(encoding="utf-8")
    for mal in ("Max URLs", "auditoria completa", "tenian un ID", 'data-tip="Que ha cambiado',
                "x-text=\"t.siguen || ''\"", "'PageRank ' + semanticAlpha", "+ ' clics desde la home'",
                "Inlinks <span", "Outlinks <span", "'Semana hasta el ' + r.semana",
                "</i> Subir cuenta", "x-text=\"cat.icon\"", "aviso a ' + ((job.config"):
        assert mal not in html, mal
    for mal in ("'Title frente al contenido'", "'Title genérico'"):
        assert mal not in js, mal
    assert 'name="Internacionalizacion"' not in api and 'name="Datos Estructurados"' not in api
    # La tabla de Comparar con miles separados.
    assert re.search(r"t\.siguen \? fmt\.num\(t\.siguen\)", html)
