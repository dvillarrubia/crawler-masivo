"""Juego de evaluacion y medidas de una pregunta a Jev (#8, decision 159).
Ninguna llamada sale a la red: el modelo es `FakeLLM` (conftest)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from shared import evaluacion_decisiones as E


def _fila(etiqueta, respuesta, confianza=None, tokens=None, coste=None):
    return {"etiqueta": etiqueta, "respuesta": respuesta, "confianza": confianza,
            "tokens": tokens, "coste": coste}


def test_acierto_precision_y_recall_por_clase():
    filas = ([_fila("true", "true")] * 8 + [_fila("true", "false")] * 2
             + [_fila("false", "false")] * 9 + [_fila("false", "true")])
    m = E.metricas(filas)
    assert m["acierto"] == 0.85 and m["evaluables"] == 20
    assert m["clases"]["true"] == {"precision": round(8 / 9, 4), "recall": 0.8, "f1": pytest.approx(0.8421, abs=1e-4),
                                   "reales": 10, "predichas": 9}
    assert m["matriz"]["true"] == {"true": 8, "false": 2}


def test_lo_sin_etiqueta_o_sin_respuesta_no_cuenta_y_se_dice():
    m = E.metricas([_fila("true", "true"), _fila(None, "true"), _fila("false", None)])
    assert (m["evaluables"], m["sin_etiqueta"], m["sin_respuesta"]) == (1, 1, 1)
    assert E.metricas([_fila(None, "true")])["acierto"] is None


def test_ece_de_un_modelo_bien_y_mal_calibrado():
    bien = [(0.9, i < 9) for i in range(10)]
    mal = [(0.99, i < 5) for i in range(10)]
    assert E._ece(bien)[0] == 0.0
    assert E._ece(mal)[0] == pytest.approx(0.49)


def test_el_umbral_es_el_mas_bajo_que_aguanta_la_precision_con_margen():
    # 80 aciertos con confianza alta, y 20 fallos con baja
    con_conf = [(0.95, True)] * 80 + [(0.6, False)] * 20
    curva = E.curva_cobertura(con_conf, 100)
    assert curva[-1]["precision"] == 1.0 and curva[-1]["precision_minima"] == pytest.approx(0.954, abs=1e-3)
    assert E.elegir_umbral(curva, objetivo=0.9, min_cubiertas=30) == 0.95
    assert E.elegir_umbral(curva, objetivo=0.9, min_cubiertas=81) is None       # pocas cubiertas
    assert E.elegir_umbral(curva, objetivo=0.7, min_cubiertas=30) == 0.6        # 0,8 con cota ~0,71


def test_29_de_30_no_basta_para_un_95():
    curva = E.curva_cobertura([(0.9, True)] * 29 + [(0.9, False)], 30)
    assert curva[0]["precision"] == pytest.approx(0.9667, abs=1e-4)
    assert E.elegir_umbral(curva, objetivo=0.95, min_cubiertas=30) is None
    assert E.wilson_inferior(29, 30) == pytest.approx(0.833, abs=1e-3)


def test_un_pico_suelto_no_es_umbral():
    curva = [{"umbral": 0.5, "cubiertas": 100, "precision_minima": 0.7},
             {"umbral": 0.7, "cubiertas": 60, "precision_minima": 0.97},
             {"umbral": 0.9, "cubiertas": 40, "precision_minima": 0.85}]
    assert E.elegir_umbral(curva, objetivo=0.95, min_cubiertas=30) is None


def test_confianzas_raras_no_entran_en_la_calibracion():
    m = E.metricas([_fila("true", "true", -0.3), _fila("true", "true", True), _fila("true", "true", 0.9)])
    assert m["ece"] == pytest.approx(0.1) and len(m["fiabilidad"]) == 1


@pytest.mark.parametrize("tipo, etiqueta, opciones, esperado", [
    ("noul", "True", None, "true"), ("noul", "sí", None, "true"), ("noul", True, None, "true"),
    ("noul", " no ", None, "false"), ("noul", "quizas", None, None), ("noul", "", None, None),
    ("choice", "Legal", {"legal": None, "otra": None}, "legal"), ("choice", "x", {"legal": None, "otra": None}, None),
    ("score", 2, ["a", "b", "c"], "2"), ("score", "5", ["a", "b", "c"], None),
])
def test_la_etiqueta_humana_se_normaliza(tipo, etiqueta, opciones, esperado):
    assert E.normalizar_etiqueta(tipo, etiqueta, opciones) == esperado


@pytest.mark.parametrize("tipo, respuesta, niveles, esperado", [
    ("noul", "true", None, "true"), ("choice", "ficha", None, "ficha"),
    ("score", "2.6", 4, "3"), ("score", "7.0", 4, "3"), ("score", "-1.0", 4, "0"),
    ("score", "2.5", 4, "3"), ("score", "1.5", 4, "2"),
    ("score", "nan", 4, None), ("score", None, 4, None),
])
def test_respuesta_en_el_vocabulario_de_la_etiqueta(tipo, respuesta, niveles, esperado):
    assert E.etiqueta_de_respuesta(tipo, respuesta, niveles) == esperado


def test_repetibilidad_y_acuerdo():
    a = {"u1": "true", "u2": "false", "u3": "true", "u4": None}
    b = {"u1": "true", "u2": "true", "u3": "true", "u4": "true"}
    r = E.repetibilidad(a, b)
    assert (r["comparadas"], r["iguales"], r["distintas"]) == (3, 2, ["u2"])
    k = E.acuerdo({"a": "x", "b": "x", "c": "y", "d": "y"}, {"a": "x", "b": "x", "c": "y", "d": "y"})
    assert (k["acuerdo"], k["kappa"]) == (1.0, 1.0)
    siempre = E.acuerdo({str(i): "x" for i in range(10)}, {str(i): "x" for i in range(10)})
    assert siempre["acuerdo"] == 1.0 and siempre["kappa"] is None     # 0/0: no se sabe


def test_con_mas_plantillas_que_ejemplos_no_se_queda_fuera_un_cliente():
    por = {f"a|/p{i}": [f"a{i}"] for i in range(150)} | {f"b|/p{i}": [f"b{i}"] for i in range(150)}
    m = E.muestra_estratificada(por, 200, semilla=3)
    de_b = sum(1 for x in m if x.startswith("b"))
    assert 70 <= de_b <= 130


def test_la_muestra_reparte_entre_plantillas_y_es_determinista():
    por = {"fichas": list(range(1000)), "legal": ["aviso", "privacidad"], "blog": list(range(100, 130))}
    m = E.muestra_estratificada(por, 10, semilla=1)
    assert len(m) == 10 and "aviso" in m and "privacidad" in m
    assert m == E.muestra_estratificada(por, 10, semilla=1)
    assert len(E.muestra_estratificada({"a": [1]}, 10)) == 1


# --- el script, de punta a punta, sin red -------------------------------------------

def _script():
    """El script cargado por su ruta, sin tocar `sys.path` (solo `main()`
    prepara las rutas, y los tests no la llaman salvo para `responder`)."""
    ruta = Path(__file__).resolve().parent.parent / "scripts" / "evaluar_decisiones.py"
    spec = importlib.util.spec_from_file_location("evaluar_decisiones_script", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _juego(tmp_path):
    from shared.preguntas import obtener

    p = obtener("es_pagina_legal")
    juego = [{"id": f"https://x.com/{i}", "job_id": "j", "url_id": i, "plantilla": "/:slug",
              "pregunta": p.nombre, "version": p.version,
              "estado": {"titulo": f"Pagina {i}", "h1": "", "contenido": "texto"},
              "etiqueta": "true" if i < 3 else "false", "etiquetado_por": "persona", "nota": ""}
             for i in range(6)]
    ruta = tmp_path / "juego.jsonl"
    ruta.write_text("\n".join(json.dumps(f) for f in juego))
    return ruta, juego


def test_sin_clave_responder_no_llama_a_nadie(tmp_path):
    S = _script()

    ruta, juego = _juego(tmp_path)
    estimacion, respuestas = S.responder(juego, confirmar=True)
    assert respuestas is None and estimacion["desactivada"] == "desactivado"
    assert S.main(["responder", str(ruta), "--salida", str(tmp_path / "r.jsonl"), "--confirmar"]) == 2
    assert not (tmp_path / "r.jsonl").exists()


def test_con_clave_pero_sin_token_tampoco(tmp_path, monkeypatch, fake_llm):
    S = _script()
    from shared import decisiones as dec

    fake = fake_llm(prohibido=True)
    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("ENRICHMENT_TOKEN", raising=False)
    _, juego = _juego(tmp_path)
    estimacion, respuestas = S.responder(juego, confirmar=True, capa=dec.capa_desde_entorno(transport=fake.transport))
    assert respuestas is None and "ENRICHMENT_TOKEN" in estimacion["desactivada"]
    assert fake.intentos_prohibidos == 0


def test_con_clave_sin_confirmar_solo_estima(tmp_path, monkeypatch, fake_llm):
    S = _script()
    from shared import decisiones as dec

    fake = fake_llm()
    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("ENRICHMENT_TOKEN", "x" * 16)
    _, juego = _juego(tmp_path)
    estimacion, respuestas = S.responder(juego, confirmar=False, capa=dec.capa_desde_entorno(transport=fake.transport))
    assert respuestas is None and estimacion["llamadas"] == 6 and fake.llamadas == 0


def test_responder_y_evaluar(tmp_path, monkeypatch, fake_llm):
    S = _script()
    from shared import decisiones as dec

    fake = fake_llm(p_noul=0.9)                 # siempre «true» con 0,9
    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("ENRICHMENT_TOKEN", "x" * 16)
    _, juego = _juego(tmp_path)
    _, respuestas = S.responder(juego, confirmar=True, capa=dec.capa_desde_entorno(transport=fake.transport))
    assert fake.llamadas == 6 and {r["respuesta"] for r in respuestas} == {"true"}
    informe = S.evaluar(juego, respuestas, objetivo=0.9)
    assert informe["acierto"] == 0.5 and informe["clases"]["true"]["precision"] == 0.5
    assert informe["umbral"] is None and informe["pregunta"] == "es_pagina_legal"
    assert informe["etiquetas_invalidas"] == 0 and informe["respuestas_fuera_del_juego"] == 0


def test_evaluar_rechaza_un_juego_de_otra_version(tmp_path):
    S = _script()
    _, juego = _juego(tmp_path)
    for f in juego:
        f["version"] = 99
    with pytest.raises(SystemExit, match="version"):
        S.evaluar(juego, [], objetivo=0.9)
