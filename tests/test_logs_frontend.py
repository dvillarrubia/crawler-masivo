"""Googlebot en los logs, en el Resumen del rastreo (#35, decision 213),
ejecutando `frontend/app.js` en Chromium. Se salta sin Chromium."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright")

from test_evolucion_frontend import _evaluar  # noqa: E402

RESUMEN = {"import_id": 1, "desde": "2026-10-11T00:00:00+00:00", "hasta": "2026-10-25T00:00:00+00:00",
           "dias": 14.0, "afirmable_ausencia": True, "visitas": 1000, "falsas": 120, "sin_comprobar": 0,
           "parte_movil": 0.82, "indexables": 400, "indexables_visitadas": 300,
           "reparto": {"indexables": 600, "no_indexables": 100, "redirecciones": 100, "errores": 50,
                       "no_en_censo": 150, "con_parametros": 200},
           "no_en_censo": [{"ruta": "/vieja", "visitas": 80, "codigos": {"404": 80}}],
           "otros_bots": {"gptbot": {"visitas": 30, "verificadas": 30, "falsas": 0, "sin_comprobar": 0},
                          "claudebot": {"visitas": 5, "verificadas": 0, "falsas": 0, "sin_comprobar": 5}}}


def _bloque(job):
    return _evaluar("() => bloqueLogs(" + json.dumps(job) + ")")


def test_sin_logs_no_hay_bloque():
    assert _bloque({}) is None


def test_cobertura_presupuesto_falsos_y_otros_bots():
    b = _bloque({"logs_resumen": RESUMEN})
    f = b["frases"]
    assert f[0].startswith("1.000 visitas de Googlebot verificado del ") and "el 82 % con el de móvil" in f[0]
    assert f[1] == "Visitó 300 de las 400 páginas indexables del rastreo (75 %)."
    assert f[2].startswith("En qué se va: 60 % en indexables") and "15 % en URLs que el rastreo no encontró" in f[2]
    assert any("20 % de las visitas son a URLs con parámetros" in x for x in f)
    assert any("120 visitas más dicen ser de Googlebot" in x for x in f)
    assert [x["bot"] for x in b["bots"]] == ["GPTBot", "ClaudeBot"]
    corto = _bloque({"logs_resumen": {**RESUMEN, "afirmable_ausencia": False,
                                      "no_se_afirma": "el log cubre menos de 7 días"}})
    assert any(x == "No se afirma qué páginas no visita: el log cubre menos de 7 días." for x in corto["frases"])
