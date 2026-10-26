"""La ficha del cliente con cuatro pestanas y una sola referencia para «frente
al anterior» (revision de UX del 25-oct, 2.1 y 2.3), ejecutando
`frontend/app.js` en Chromium.

- Las rutas: `#/clientes/<id>` es Estado; historial, alertas y configuracion
  tienen su tramo, y el viejo `rastreos` abre Historial.
- La diferencia de errores de la tabla se mide contra `comparado_con` (el
  censo con el que se comparo, el de sus alertas), no contra la fila de
  debajo: con dos programaciones semanales la fila de debajo es de la otra.

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright")

from test_evolucion_frontend import _evaluar  # noqa: E402

# Los cinco censos de Lopesan del 26-oct: dos programaciones mezcladas.
SERIE = [
    {"id": "j26", "tipo": "censo", "status": "completed", "errores": 85, "comparado_con": "j19", "created_at": "2026-10-26T10:00:00Z"},
    {"id": "j21", "tipo": "censo", "status": "completed", "errores": 83, "comparado_con": "j14", "created_at": "2026-10-21T01:00:00Z"},
    {"id": "j19", "tipo": "censo", "status": "completed", "errores": 89, "comparado_con": "j14", "created_at": "2026-10-19T09:00:00Z"},
    {"id": "j14", "tipo": "censo", "status": "completed", "errores": 123, "comparado_con": None, "created_at": "2026-10-14T01:00:00Z"},
]


def test_la_referencia_es_la_de_la_comparacion_y_no_la_fila_de_debajo():
    r = _evaluar("""() => { const a = app(); a.clienteSerie = %s;
        return a.clienteSerie.map(j => (a.referenciaDe(j) || {}).id || null); }""" % json.dumps(SERIE))
    assert r == ["j19", "j14", "j14", None]


def test_rutas_de_las_pestanas_y_alias():
    r = _evaluar("""() => { const a = app(); a.view = 'client'; a.cliente = { id: 'c1', nombre: 'Lopesan' };
        const out = {};
        for (const t of ['estado', 'historial', 'alertas', 'config']) { a.clienteTab = t; out[t] = a._ruta(); }
        a.clienteTab = 'historial';
        out.migas = a.migas().map(m => m.texto);
        out.alias = [CLIENTE_SLUG_TAB.rastreos, CLIENTE_SLUG_TAB.configuracion, CLIENTE_SLUG_TAB.config];
        return out; }""")
    assert r["estado"] == "#/clientes/c1"
    assert r["historial"] == "#/clientes/c1/historial"
    assert r["alertas"] == "#/clientes/c1/alertas"
    assert r["config"] == "#/clientes/c1/configuracion"
    assert r["migas"] == ["Clientes", "Lopesan", "Historial"]
    assert r["alias"] == ["historial", "config", "config"]
