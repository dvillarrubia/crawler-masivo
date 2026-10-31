"""Juego de evaluacion y medidas de una pregunta a Jev (#8, decision 159).
Ninguna llamada sale a la red: el modelo es `FakeLLM` (conftest)."""

from __future__ import annotations

import importlib.util
import json
import unicodedata
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


def test_un_umbral_comun_que_solo_decide_los_no_se_avisa():
    """El caso del soft 404 v2 (decision 169): 60 «no» seguros y bien, 29 «si»
    reales que Jev contesta con poca confianza. El umbral comun sale, pero de
    los «si» casi no decide ninguno: `por_respuesta` lo cuenta y `avisos` lo
    dice."""
    filas = ([{"etiqueta": "false", "respuesta": "false", "confianza": 0.95}] * 60
             + [{"etiqueta": "true", "respuesta": "true", "confianza": 0.95}] * 2
             + [{"etiqueta": "true", "respuesta": "true", "confianza": 0.7}] * 21
             + [{"etiqueta": "true", "respuesta": "false", "confianza": 0.6}] * 6
             + [{"etiqueta": "false", "respuesta": "true", "confianza": 0.72}] * 5)
    m = E.metricas(filas, objetivo=0.9)
    assert m["umbral"] == 0.95
    si = m["por_respuesta"]["true"]
    assert si["reales"] == 29 and si["decididas"] == 2 and si["umbral_propio"] is None
    assert si["recuperadas"] == round(2 / 29, 4)
    assert m["por_respuesta"]["false"]["decididas"] == 60
    assert m["por_respuesta"]["false"]["reales"] == 65
    assert any("decidido el 7 %" in a and "«true»" in a for a in m["avisos"])


def test_una_respuesta_decidida_bajo_el_objetivo_se_avisa():
    """El umbral comun llega, pero el «no» decidido solo no (decision 169)."""
    filas = ([{"etiqueta": "true", "respuesta": "true", "confianza": 0.9}] * 60
             + [{"etiqueta": "false", "respuesta": "false", "confianza": 0.9}] * 28
             + [{"etiqueta": "true", "respuesta": "false", "confianza": 0.9}] * 2)
    m = E.metricas(filas, objetivo=0.9)
    assert m["umbral"] == 0.9
    assert m["por_respuesta"]["false"]["precision_minima"] < 0.9
    assert any("«false» decididos" in a for a in m["avisos"])


def test_sin_umbral_no_hay_avisos_ni_decididas():
    filas = [{"etiqueta": "true", "respuesta": "false", "confianza": 0.9}] * 5
    m = E.metricas(filas, objetivo=0.9)
    assert m["umbral"] is None and m["avisos"] == []
    assert "decididas" not in m["por_respuesta"]["false"]


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


def test_evaluar_cuenta_una_vez_cada_estado_repetido():
    """La misma pagina con y sin www es la misma entrada para Jev (decision
    169): cuenta una vez, y se dice si las copias tenian otra etiqueta."""
    S = _script()
    from shared.preguntas import obtener

    p = obtener("es_soft_404")
    estado = {"titulo": "A", "h1": "A", "contenido": "x", "palabras": 1, "ruta": "/a"}
    juego = [{"id": f"https://{h}x.com/a", "pregunta": p.nombre, "version": p.version,
              "estado": estado, "etiqueta": e} for h, e in (("", "true"), ("www.", "true"), ("m.", "false"))]
    juego.append({"id": "https://x.com/b", "pregunta": p.nombre, "version": p.version,
                  "estado": {**estado, "ruta": "/b"}, "etiqueta": "false"})
    respuestas = [{"id": f["id"], "pregunta": p.nombre, "version": p.version, "respuesta": "true",
                   "confianza": 0.9} for f in juego]
    inf = S.evaluar(juego, respuestas, objetivo=0.9)
    assert inf["evaluables"] == 2
    assert inf["estados_repetidos_quitados"] == 2 and inf["repetidos_con_otra_etiqueta"] == 1


def test_reetiquetar_deja_el_juego_a_ciegas_para_la_version_nueva(tmp_path):
    """Decision 180: una etiqueta de la v1 no vale para una v2 con otras
    opciones o niveles, y verla empuja a repetirla. `reetiquetar` da los mismos
    estados para la version del catalogo, sin etiqueta ni nota, y despues
    `evaluar` ya los acepta."""
    S = _script()
    from shared.preguntas import obtener

    p = obtener("intencion_pagina")
    estado = {"ruta": "/a", "titulo": "A", "h1": "A", "contenido": "x"}
    viejo = [{"id": "https://x.com/a", "pregunta": p.nombre, "version": p.version - 1,
              "estado": estado, "etiqueta": "navigational", "etiquetado_por": "claude",
              "nota": "Normativa académica. [duda: informational]", "plantilla": "x.com|/a"}]
    with pytest.raises(SystemExit, match="version"):
        S.evaluar(viejo, [], objetivo=0.9)
    entrada, salida = tmp_path / "v1.jsonl", tmp_path / "v2.jsonl"
    entrada.write_text(json.dumps(viejo[0]) + "\n", encoding="utf-8")
    assert S.main(["reetiquetar", str(entrada), "--salida", str(salida)]) == 0
    (fila,) = [json.loads(x) for x in salida.read_text(encoding="utf-8").splitlines()]
    assert fila["version"] == p.version and fila["estado"] == estado and fila["id"] == viejo[0]["id"]
    assert fila["etiqueta"] is None and fila["etiquetado_por"] is None and fila["nota"] == ""
    assert fila["plantilla"] == "x.com|/a"
    assert S.evaluar([fila], [], objetivo=0.9)["sin_etiqueta"] == 1
    with pytest.raises(ValueError, match="ya es de la version"):
        E.para_reetiquetar([fila], p.version)


def test_las_fugas_se_buscan_por_huella_de_cada_fragmento_del_title(tmp_path):
    """Decision 182: los titles de un juego se guardan como huellas de sus
    fragmentos (sin la marca hace falta saber cual es) y un ejemplo citado en
    unas instrucciones casa con cualquiera de ellos, sin importar mayusculas ni
    espacios. El apostrofo de «site's» no abre una cita."""
    juego = [{"estado": {"titulo": "Becas y ayudas | Marca  Campus"}},
             {"estado": {"titulo": "Otra cosa - Marca"}}, {"estado": {}}]
    h = E.huellas_de_juego(juego, "titulo")
    assert E.huella("becas y ayudas") in h and E.huella("marca campus") in h
    assert E.fragmentos_de_title("A – B: C") >= {"a", "b: c", "a – b: c"}
    texto = ("The site's page. Examples ('becas Y  ayudas', 'Nada') and a list "
             "(Uno, Otra cosa).")
    assert E.ejemplos_de_instrucciones(texto)[:2] == ["becas Y  ayudas", "Nada"]
    assert E.fugas(texto, h) == [("becas Y  ayudas", "becas y ayudas"), ("Otra cosa", "otra cosa")]
    # Comillas dobles o tipograficas, y acentos compuestos o descompuestos en
    # cualquiera de los dos lados: una huella vieja de un title en NFD tambien casa.
    nfd = unicodedata.normalize("NFD", "Formació")
    h2 = {E.huella(nfd.lower())} | E.huellas_de_juego([{"estado": {"titulo": "Redacció"}}], "titulo")
    assert E.huella("redacció") in h2
    otras = 'Uses "Formació" and \u201cRedacci' + unicodedata.normalize("NFD", "ó") + '\u201d.'
    assert [e for e, _ in E.fugas(otras, h2)] == ["Formació", "Redacci" + unicodedata.normalize("NFD", "ó")]

    S = _script()
    entrada, salida = tmp_path / "juego.jsonl", tmp_path / "huellas.txt"
    entrada.write_text("".join(json.dumps(f) + "\n" for f in juego), encoding="utf-8")
    assert S.main(["huellas", str(entrada), "--salida", str(salida)]) == 0
    lineas = salida.read_text(encoding="utf-8").splitlines()
    assert lineas[0].startswith("#") and set(x for x in lineas if not x.startswith("#")) == h



# -- El informe guardado (#8) ---------------------------------------------------

def test_evaluar_con_informe_lo_guarda_junto_al_juego(tmp_path):
    from shared.preguntas import obtener

    S = _script()
    ruta, juego = _juego(tmp_path)
    p = obtener("es_pagina_legal")
    respuestas = [{"id": f["id"], "pregunta": p.nombre, "version": p.version,
                   "respuesta": "true" if i < 4 else "false", "confianza": 0.5 + i / 20,
                   "tokens": 100, "coste": 0.0001} for i, f in enumerate(juego)]
    rr = tmp_path / "respuestas.jsonl"
    rr.write_text("\n".join(json.dumps(r) for r in respuestas))
    assert S.main(["evaluar", str(ruta), str(rr), "--informe"]) == 0
    texto = (tmp_path / f"INFORME_v{p.version}.md").read_text(encoding="utf-8")
    assert texto.startswith(f"# es_pagina_legal v{p.version}")
    for trozo in ("## Acierto y clases", "Matriz de confusión", "## Calibración",
                  "## Curva de cobertura", "**Sin umbral**", "## Por respuesta", "## Coste",
                  "`juego.jsonl`", "0.1000 USD por mil"):
        assert trozo in texto, trozo
    # Los numeros son los de `evaluar`, no otros
    informe = S.evaluar(juego, respuestas, objetivo=E.PRECISION_OBJETIVO)
    assert f"Acierto: **{100 * informe['acierto']:.1f} %**" in texto


def test_el_informe_marca_el_umbral_y_resume_una_curva_larga():
    curva = [{"umbral": round(0.5 + i / 400, 4), "cubiertas": 200 - i, "cobertura": 0.5,
              "precision": 0.95, "precision_minima": 0.92} for i in range(150)]
    umbral = curva[77]["umbral"]
    texto = E.informe_markdown({"pregunta": "x", "version": 1, "objetivo": 0.9, "umbral": umbral,
                                "curva": curva, "clases": {}, "ejemplos": 200, "evaluables": 200})
    filas = [linea for linea in texto.splitlines() if linea.startswith("| 0.")]
    assert len(filas) <= E.MAX_FILAS_CURVA + 1
    assert any(linea.startswith(f"| {umbral} ←") for linea in filas)


def test_el_informe_de_un_juego_de_precision_no_inventa_recall_ni_coste():
    texto = E.informe_markdown({
        "pregunta": "x", "version": 2, "juego_precision": {"respuesta": "si"}, "respuesta": "si",
        "positivos": 40, "evaluables": 40, "aciertos": 38, "precision": 0.95,
        "precision_minima": 0.83, "umbral_propio": None, "curva": [], "objetivo": 0.9,
        "recall_no_estimable": "Solo el control lo estima.", "control": {"ejemplos": 100},
        "avisos": ["la cota de la precision de «si» es 0.83"]})
    assert "juego de precisión" in texto and "Recall: no se estima" in texto
    assert "## Coste" not in texto and "## Avisos" in texto


def test_un_modelo_perfecto_con_confianzas_distintas_tiene_umbral():
    """Decision 231: la cola de 30-34 cubiertas no puede llegar al 0,9 ni
    acertandolo todo (30 de 30 da 0,886), y hundia el umbral de cualquier
    pregunta con confianzas variadas."""
    assert E.cubiertas_alcanzables(0.9) == 35 and E.cubiertas_alcanzables(0.95) == 73
    con_conf = [(0.5 + i / 400, True) for i in range(200)]
    assert E.elegir_umbral(E.curva_cobertura(con_conf, 200), objetivo=0.9) == 0.5
    # Un fallo en la cola alta sigue impidiendo el umbral si la hunde
    con_conf = [(0.5 + i / 400, True) for i in range(160)] + [(0.95 + i / 1000, i % 4 != 0)
                                                              for i in range(40)]
    curva = E.curva_cobertura(con_conf, 200)
    assert E.elegir_umbral(curva, objetivo=0.9) is None
