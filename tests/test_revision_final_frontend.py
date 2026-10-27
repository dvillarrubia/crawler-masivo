"""Lo que la revision final de la fase 3 encontro abierto (revision de UX del
25-oct, cierre del 27-oct; decision 205), EJECUTANDO `frontend/index.html`
con Alpine en Chromium (el arnes de test_movil_frontend.py) y, lo puro,
`frontend/app.js` suelto.

- La portada no mezcla unidades: con una API sin `paginas_con_error`, la fila
  del ultimo cliente abierto seguia diciendo paginas (de su evolucion) bajo
  la cabecera «errores (incidencias)».
- El Historial no resta paginas a incidencias en la flecha de cada fila.
- «Revisado con IA» de la ficha: `.bloque` gana a `.veredicto-lista li`.
- «No incluida en el sitemap» sale de los totales del Estado del cliente, del
  informe y de Problemas si cualquiera de los dos censos leyo sus sitemaps a
  medias, y se dice.
- El enlace de una frase de Google abre la ficha aunque ese censo ya este
  cargado.

Necesita Chromium y poder bajar Alpine de su CDN: si no, se salta.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright")

import test_movil_frontend as m  # noqa: E402
from test_pulido_frontend import _evaluar  # noqa: E402

CLI, JOB = m.CLI, m.JOB
J1, J2 = m._job(1)["id"], m._job(2)["id"]
# Una API anterior a la decision 202: la portada no trae `paginas_con_error`;
# la evolucion del cliente si sabe las paginas de dos de sus rastreos.
EVOLUCION = {
    "metricas": {"urls_con_error": {"etiqueta": "URLs con error", "mejor": "baja"},
                 "errores": {"etiqueta": "Errores", "mejor": "baja"}},
    "series": [{"clave": "p", "nombre": "semanal", "puntos": [
        {"job_id": JOB, "created_at": "2026-10-20T01:00:00Z", "urls_con_error": 72, "errores": 85, "comparable": True},
        {"job_id": J2, "created_at": "2026-10-22T01:00:00Z", "urls_con_error": 60, "errores": 83, "comparable": True}]}],
}

PORTADA_E_HISTORIAL = """() => {
  const t = sel => [...document.querySelectorAll(sel)].map(e => e.textContent.replace(/\\s+/g, ' ').trim());
  const fila = [...document.querySelectorAll('[data-testid=panel-cartera] tbody tr')][0];
  const celda = fila ? fila.querySelector('td[data-label="Páginas con error"]') : null;
  return {
    cabecera: t('[data-testid=panel-cartera] thead th').find(x => /error/i.test(x)) || null,
    portada: celda ? celda.textContent.replace(/\\s+/g, ' ').trim() : null,
    serie: t('[data-testid=tabla-serie] tbody td[data-label="Páginas con error"]'),
  };
}"""


def test_la_portada_no_cambia_de_unidad_tras_abrir_el_cliente(monkeypatch):
    monkeypatch.setitem(m.RESPUESTAS, f"clients/{CLI}/evolucion", EVOLUCION)
    rutas = ["#/clientes", f"#/clientes/{CLI}/historial", "#/clientes"]
    # Dos visitas a la misma ruta: el diccionario se quedaria con la ultima.
    salida1, errores1 = m._medir(rutas[:1], 1440, PORTADA_E_HISTORIAL)
    salida2, errores2 = m._medir(rutas, 1440, PORTADA_E_HISTORIAL)
    assert errores1 == [] and errores2 == []
    antes = salida1["#/clientes"]["extra"]
    despues = salida2["#/clientes"]["extra"]
    assert antes["cabecera"] == "errores (incidencias)"
    assert antes["portada"].startswith("85")
    # Despues de abrir el cliente (y cargar su evolucion), la misma cifra.
    assert despues["cabecera"] == "errores (incidencias)"
    assert despues["portada"].startswith("85"), despues["portada"]


def test_el_historial_no_resta_paginas_a_incidencias(monkeypatch):
    monkeypatch.setitem(m.RESPUESTAS, f"clients/{CLI}/evolucion", EVOLUCION)
    salida, errores = m._medir([f"#/clientes/{CLI}/historial"], 1440, PORTADA_E_HISTORIAL)
    assert errores == []
    serie = salida[f"#/clientes/{CLI}/historial"]["extra"]["serie"]
    # Fila 0: 72 paginas; su referencia (fila 1) no sabe las paginas: sin flecha.
    assert serie[0] == "72"
    # Fila 1: 84 incidencias frente a las 83 incidencias de la fila 2, no a
    # sus 60 paginas (antes «84 incid. ▲24»).
    assert serie[1] == "84 incid. ▲1", serie[1]


def test_revisado_con_ia_va_en_lineas(monkeypatch):
    salida, errores = m._medir(["#/clientes"], 1440, """() => {
      const ul = document.createElement('ul'); ul.className = 'veredicto-lista';
      const li = document.createElement('li'); li.className = 'bloque';
      const otro = document.createElement('li');
      ul.append(li, otro); document.body.append(ul);
      return [getComputedStyle(li).display, getComputedStyle(otro).display];
    }""")
    assert errores == []
    assert salida["#/clientes"]["extra"] == ["block", "flex"]


# La comparacion del ultimo censo con «No incluida en el sitemap» dentro.
COMPARACION = {
    "comparado_con": J1, "comparable": True, "fecha_anterior": "2026-10-19T01:00:00Z", "alertas": [],
    "incidencias": {
        "comparable": True,
        "nuevas": {"total": 240, "por_tipo": [{"tipo": "not_in_sitemap", "severidad": "info", "total": 200},
                                              {"tipo": "status_4xx", "severidad": "error", "total": 40}]},
        "resueltas": {"total": 775, "por_tipo": [{"tipo": "not_in_sitemap", "severidad": "info", "total": 700},
                                                 {"tipo": "status_4xx", "severidad": "error", "total": 75}]},
        "siguen": {"total": 3000, "por_tipo": [{"tipo": "not_in_sitemap", "severidad": "info", "total": 2900},
                                               {"tipo": "status_4xx", "severidad": "error", "total": 100}]},
    },
}
DUDOSO = {"pedidos": 3, "leidos": 3, "ficheros": [
    {"formato": "urlset", "status": 200, "devueltas": 10},
    {"formato": "desconocido", "status": 200, "bytes": 0}]}
ENTERO = {"pedidos": 1, "leidos": 1, "ficheros": [{"formato": "urlset", "status": 200, "devueltas": 10}]}

ESTADO = """() => [...document.querySelectorAll('[data-testid=estado-incidencias] div')]
  .filter(e => getComputedStyle(e).display !== 'none').map(e => e.textContent.trim())"""


@pytest.mark.parametrize("aqui,antes,esperado", [
    (DUDOSO, ENTERO, ["40 incidencias nuevas y 75 resueltas", "Sin contar «No incluida en el sitemap»"]),
    (ENTERO, DUDOSO, ["40 incidencias nuevas y 75 resueltas", "Sin contar «No incluida en el sitemap»"]),
    (ENTERO, ENTERO, ["240 incidencias nuevas y 775 resueltas"]),
])
def test_el_estado_no_cuenta_lo_que_no_se_puede_afirmar(monkeypatch, aqui, antes, esperado):
    monkeypatch.setitem(m.RESPUESTAS, f"jobs/{JOB}", m._job(0, comparacion=COMPARACION, sitemaps=aqui))
    monkeypatch.setitem(m.RESPUESTAS, f"jobs/{J1}", m._job(1, sitemaps=antes))
    salida, errores = m._medir([f"#/clientes/{CLI}"], 1440, ESTADO)
    assert errores == []
    textos = salida[f"#/clientes/{CLI}"]["extra"]
    assert len(textos) == len(esperado), textos
    for t, e in zip(textos, esperado):
        assert t.startswith(e), (t, e)


# ---- Lo puro, con app.js suelto ----

def _job_comparado(sitemaps):
    return {"id": "J", "analisis_version": "v1", "sitemaps": sitemaps,
            "comparacion": {**COMPARACION, "analisis_version": "v1", "analisis_version_anterior": "v1"}}


def test_problemas_mira_tambien_los_sitemaps_del_censo_anterior():
    expr = """() => {
        const a = app();
        a.job = %s;
        a.versionAnterior = { jobId: 'J', version: 'v1', dudaSitemaps: %s };
        a.cuentaSinFiltro = { jobId: 'J', porTipo: { not_in_sitemap: 3100, status_4xx: 140 } };
        const d = a.diffProblemas();
        return { ok: d.ok, nuevas: d.nuevas, resueltas: d.resueltas, dudosos: d.dudosos,
                 aviso: d.avisos.some(x => x.startsWith('Sin contar')) };
    }"""
    r = _evaluar(expr % (json.dumps(_job_comparado(ENTERO)), "true"))
    assert r == {"ok": True, "nuevas": 40, "resueltas": 75, "dudosos": ["not_in_sitemap"], "aviso": True}
    r = _evaluar(expr % (json.dumps(_job_comparado(ENTERO)), "false"))
    assert r == {"ok": True, "nuevas": 240, "resueltas": 775, "dudosos": [], "aviso": False}


def test_el_informe_con_una_api_anterior_quita_lo_dudoso_en_la_app():
    expr = """() => {
        const a = app();
        a.job = %s;
        a.informe = { job: { id: 'J' }, comparacion: { incidencias: %s } };
        a.versionAnterior = %s;
        return a.incidenciasInforme();
    }"""
    viejo = {"nuevas": 240, "resueltas": 775, "siguen": 3000}
    # Sin saber aun como leyo el anterior sus sitemaps: no se pinta.
    assert _evaluar(expr % (json.dumps(_job_comparado(ENTERO)), json.dumps(viejo), "null")) is None
    r = _evaluar(expr % (json.dumps(_job_comparado(ENTERO)), json.dumps(viejo),
                         "{ jobId: 'J', version: 'v1', dudaSitemaps: true }"))
    assert r == {"nuevas": 40, "resueltas": 75, "siguen": 100, "sin_sitemap": True}
    # Una API con la decision 205 ya lo trae hecho: tal cual.
    nuevo = {"nuevas": 240, "resueltas": 775, "siguen": 3000, "sin_sitemap": False}
    r = _evaluar(expr % (json.dumps(_job_comparado(DUDOSO)), json.dumps(nuevo), "null"))
    assert r == nuevo


def test_la_frase_de_google_abre_la_ficha_si_el_censo_ya_estaba_cargado():
    r = _evaluar("""() => {
        const a = app();
        const pedidas = [];
        a.abrirFichaPorUrl = u => { pedidas.push(u); };
        a.job = { id: 'J' };
        a.view = 'client';
        a._fichaPendiente = { jobId: 'J', url: 'https://x/vieja' };
        const hash = location.hash;
        a.abrirFichaDeCenso('J', 'https://x/a');
        return { pedidas, pendiente: a._fichaPendiente, mismoHash: location.hash === hash };
    }""")
    assert r == {"pedidas": ["https://x/a"], "pendiente": None, "mismoHash": True}
