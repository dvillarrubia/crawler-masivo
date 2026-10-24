"""Juego de precision de una respuesta rara (decision 179), con datos sinteticos.

Un conjunto amplio donde la verdad la fija el test: se sabe cuantos «true» hay,
cuantos ve Jev y cuantos se inventa, y se comprueba que la precision sale la
de verdad, que el recall no se afirma y que el control es lo unico que lo
estima. Sin red ni base de datos.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from shared import evaluacion_decisiones as E


def _script():
    ruta = Path(__file__).resolve().parent.parent / "scripts" / "evaluar_decisiones.py"
    spec = importlib.util.spec_from_file_location("evaluar_decisiones_script_precision", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _conjunto(n=2000, reales=40, vistos=30, inventados=10, conf_vp=0.9, conf_fp=0.6):
    """(conjunto sin etiquetar, respuestas de Jev, verdad por id).

    `reales` paginas son «true»; Jev contesta «true» en `vistos` de ellas
    (con `conf_vp`) y en `inventados` que no lo son (con `conf_fp`). Precision
    real de «true»: vistos / (vistos + inventados); recall: vistos / reales."""
    from shared.preguntas import obtener

    p = obtener("es_pagina_legal")
    conjunto, respuestas, verdad = [], [], {}
    for i in range(n):
        uid = f"https://x.com/p{i:05d}"
        real = i < reales
        if real and i < vistos:
            resp, conf = "true", conf_vp
        elif not real and i < reales + inventados:
            resp, conf = "true", conf_fp
        else:
            resp, conf = "false", 0.95
        verdad[uid] = "true" if real else "false"
        conjunto.append({"id": uid, "job_id": "j", "url_id": i, "plantilla": "x.com|/:slug",
                         "ambito": "url", "pregunta": p.nombre, "version": p.version,
                         "estado": {"titulo": f"Pagina {i}", "h1": "", "contenido": f"texto {i}"},
                         "etiqueta": None, "etiquetado_por": None, "nota": ""})
        respuestas.append({"id": uid, "pregunta": p.nombre, "version": p.version, "respuesta": resp,
                           "confianza": conf})
    return conjunto, respuestas, verdad


def _etiquetar(juego, verdad):
    return [{**f, "etiqueta": verdad[f["id"]], "etiquetado_por": "test"} for f in juego]


def test_la_precision_sale_exacta_y_el_recall_no_se_afirma():
    S = _script()
    conjunto, respuestas, verdad = _conjunto()
    juego, resumen = S.precision(conjunto, respuestas, "sí", n_positivos=None, n_control=100, semilla=3)
    positivos = [f for f in juego if f["origen"] == "positivo_jev"]
    assert resumen["respuesta"] == "true" and resumen["respuestas_x"] == 40
    assert len(positivos) == 40 and resumen["todos_los_positivos"]
    assert sum(1 for f in juego if f["en_control"]) == 100
    assert {f["origen"] for f in juego} == {"positivo_jev", "control"}

    inf = S.evaluar(_etiquetar(juego, verdad), respuestas, objetivo=0.9)
    assert inf["respuesta"] == "true" and inf["evaluables"] == 40
    assert inf["precision"] == 0.75 and inf["aciertos"] == 30          # 30 / 40, la de verdad
    assert inf["precision_minima"] == pytest.approx(E.wilson_inferior(30, 40), abs=1e-4)
    # El recall de verdad es 30 / 40; el juego no lo afirma (los 10 «true»
    # que Jev no vio no estan entre los positivos) ni da umbral comun.
    assert inf["recall"] is None and "recall_no_estimable" in inf
    assert inf["umbral"] is None and "clases" not in inf and "acierto" not in inf
    assert inf["control"]["ejemplos"] == 100
    assert any("control" in a and "no dice nada" in a for a in inf["avisos"])


def test_los_si_con_confianza_alta_tienen_umbral_propio():
    """60 aciertos con 0,9 y 5 inventados con 0,6: la precision es 60/65 y
    el «true» llega solo por encima de 0,9, cosa que el juego aleatorio de
    200 (con 6 «true») nunca podria decir."""
    S = _script()
    conjunto, respuestas, verdad = _conjunto(n=2000, reales=60, vistos=60, inventados=5)
    juego, _ = S.precision(conjunto, respuestas, "true", n_positivos=None, n_control=50, semilla=0)
    inf = S.evaluar(_etiquetar(juego, verdad), respuestas, objetivo=0.9)
    assert inf["precision"] == round(60 / 65, 4)
    assert inf["umbral_propio"] == 0.9
    assert inf["precision_minima"] < 0.9      # todas juntas no llegan; por encima de 0,9, si

    aleatorio = E.muestra_estratificada({"x": list(range(2000))}, 200, semilla=0)
    si_aleatorios = sum(1 for i in aleatorio if i < 65)
    assert si_aleatorios < E.MIN_CUBIERTAS    # por eso hace falta este juego


def test_las_de_mas_confianza_meten_los_empates_y_lo_dicen():
    S = _script()
    conjunto, respuestas, verdad = _conjunto(n=500, reales=20, vistos=20, inventados=10)
    juego, resumen = S.precision(conjunto, respuestas, "true", n_positivos=5, n_control=0, semilla=0)
    # Las 20 con 0,9 empatan en el corte: entran todas, y ninguna de 0,6.
    assert resumen["positivos_en_juego"] == 20 and resumen["confianza_minima"] == 0.9
    assert not resumen["todos_los_positivos"] and len(juego) == 20
    inf = S.evaluar(_etiquetar(juego, verdad), respuestas, objetivo=0.9)
    assert inf["precision"] == 1.0
    assert any("mas confianza" in a for a in inf["avisos"])


def test_un_control_que_cae_en_un_positivo_cuenta_en_los_dos():
    sel, resumen = E.seleccion_de_precision(
        [{"id": str(i), "respuesta": "true" if i < 5 else "false", "confianza": 0.8} for i in range(10)],
        "true", n_control=10, semilla=0)
    assert resumen["control"] == 10 and len(sel) == 10
    assert all(v["en_control"] for v in sel.values())
    assert sum(v["origen"] == "positivo_jev" for v in sel.values()) == 5


def test_el_control_estima_el_recall_con_su_tamano():
    filas = ([{"etiqueta": "true", "respuesta": "true", "confianza": 0.9, "origen": "positivo_jev",
               "en_control": i < 8} for i in range(30)]
             + [{"etiqueta": "true", "respuesta": "false", "confianza": 0.9, "origen": "control",
                 "en_control": True} for _ in range(8)]
             + [{"etiqueta": "false", "respuesta": "false", "confianza": 0.9, "origen": "control",
                 "en_control": True} for _ in range(84)])
    inf = E.metricas_precision(filas, "true")
    assert inf["precision"] == 1.0 and inf["recall"] is None
    assert inf["control"]["reales_x"] == 16 and inf["control"]["recall_x"] == 0.5
    assert inf["control"]["recall_x_minimo"] == pytest.approx(E.wilson_inferior(8, 16), abs=1e-4)


def test_faltan_positivos_por_etiquetar_se_dice():
    S = _script()
    conjunto, respuestas, verdad = _conjunto(n=300, reales=10, vistos=10, inventados=0)
    juego, _ = S.precision(conjunto, respuestas, "true", n_positivos=None, n_control=20, semilla=0)
    etiquetado = _etiquetar(juego, verdad)
    for f in etiquetado:
        if f["origen"] == "positivo_jev" and f["id"].endswith("0"):
            f["etiqueta"] = None
    inf = S.evaluar(etiquetado, respuestas, objetivo=0.9)
    assert inf["sin_etiqueta"] == 1 and any("por etiquetar" in a for a in inf["avisos"])


def test_con_otras_respuestas_no_se_evalua():
    """Las respuestas de otra pasada (otro fichero) no son las del juego: un
    positivo que ahora dice «false» no puede contar en la precision de «true»."""
    S = _script()
    conjunto, respuestas, verdad = _conjunto(n=300, reales=10, vistos=10, inventados=0)
    juego, _ = S.precision(conjunto, respuestas, "true", n_positivos=None, n_control=20, semilla=0)
    otras = [{**r, "respuesta": "false"} for r in respuestas]
    with pytest.raises(SystemExit, match="no son las respuestas"):
        S.evaluar(_etiquetar(juego, verdad), otras, objetivo=0.9)


def test_de_punta_a_punta_por_la_linea_de_ordenes(tmp_path, capsys):
    S = _script()
    conjunto, respuestas, verdad = _conjunto(n=400, reales=12, vistos=9, inventados=3)
    (tmp_path / "c.jsonl").write_text("\n".join(json.dumps(f) for f in conjunto))
    (tmp_path / "r.jsonl").write_text("\n".join(json.dumps(r) for r in respuestas))
    salida = tmp_path / "p.jsonl"
    assert S.main(["precision", str(tmp_path / "c.jsonl"), str(tmp_path / "r.jsonl"),
                   "--respuesta", "true", "--control", "30", "--salida", str(salida)]) == 0
    juego = [json.loads(l) for l in salida.read_text().splitlines()]
    assert sum(f["origen"] == "positivo_jev" for f in juego) == 12
    assert all(f["juego_precision"]["conjunto"] == 400 for f in juego)
    (tmp_path / "e.jsonl").write_text("\n".join(json.dumps(f) for f in _etiquetar(juego, verdad)))
    capsys.readouterr()
    assert S.main(["evaluar", str(tmp_path / "e.jsonl"), str(tmp_path / "r.jsonl")]) == 0
    inf = json.loads(capsys.readouterr().out)
    assert inf["precision"] == 0.75 and inf["recall"] is None


def test_no_se_saca_de_otro_juego_de_precision_ni_con_una_respuesta_que_no_existe():
    S = _script()
    conjunto, respuestas, _ = _conjunto(n=100, reales=5, vistos=5, inventados=0)
    with pytest.raises(SystemExit, match="no es una respuesta"):
        S.precision(conjunto, respuestas, "quizas", n_positivos=None, n_control=10, semilla=0)
    juego, _ = S.precision(conjunto, respuestas, "true", n_positivos=None, n_control=10, semilla=0)
    with pytest.raises(SystemExit, match="ya es un juego de precision"):
        S.precision(juego, respuestas, "true", n_positivos=None, n_control=10, semilla=0)
