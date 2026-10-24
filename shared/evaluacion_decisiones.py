"""Medir una pregunta a Jev contra un juego etiquetado (#8, decision 159).

Funciones puras, sin red ni base de datos: reciben filas y devuelven cifras.
Las usa `scripts/evaluar_decisiones.py`. Tres medidas, en este orden de coste
(comentario de #8 tras revisar jev-seo):

1. **Repetibilidad** (`repetibilidad`): la misma pagina juzgada dos veces. Casi
   gratis, y descarta una pregunta inestable antes de etiquetar nada.
2. **Acuerdo con un segundo juez** (`acuerdo`): sirve para comparar dos
   redacciones de la misma pregunta. **No es acierto.**
3. **Acierto frente a la etiqueta humana** (`metricas`): lo unico que fija el
   umbral de `decidido` (decision 84). Cuenta la etiqueta de la persona, nunca
   la de Jev.

De `metricas` sale el **umbral por pregunta**: la confianza minima con la que
lo decidido alcanza la precision objetivo **con margen estadistico** (la cota
inferior de Wilson al 95 %, no la precision a secas: 29 de 30 es «0,97» y su
cota es 0,83) cubriendo al menos `MIN_CUBIERTAS` ejemplos. Sin eso no hay
umbral: una pregunta sin evaluar no puede firmar respuestas
(`decisiones.estado_por_confianza`).

Con una respuesta rara (9 «si» de 200), la precision de esa respuesta se mide
con un **juego de precision** (`seleccion_de_precision`, `metricas_precision`;
decision 179): todas las veces que Jev la dio en un conjunto amplio, mas un
control aleatorio. Da su precision y su umbral propio, nunca su recall.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any, Iterable

# Sobre la cota inferior de Wilson, no sobre la precision observada: con 0,9 y
# todo acertado hacen falta ~35 ejemplos por encima del umbral; con 0,95, ~75.
PRECISION_OBJETIVO = 0.9
Z_WILSON = 1.96
# Ejemplos que tienen que quedar por encima del umbral para poder elegirlo: con
# 5 aciertos de 5 la precision es 1,0 y no dice nada.
MIN_CUBIERTAS = 30
# Una respuesta con al menos estas etiquetas reales de la que el umbral comun
# decide menos de esta parte se avisa (decision 169).
MIN_REALES_AVISO = 10
RECUPERADAS_MINIMAS = 0.25
CUBOS_ECE = 10


def etiqueta_de_respuesta(tipo: str, respuesta: str | None, niveles: int | None = None) -> str | None:
    """La respuesta de Jev en el mismo vocabulario que la etiqueta humana.

    - noul: "true" / "false";
    - choice: la opcion;
    - score: el indice del nivel mas cercano ("0", "1"...), porque la persona
      etiqueta un nivel y Jev devuelve un numero continuo (`repr(float)`).
    """
    if respuesta is None:
        return None
    if tipo != "score":
        return str(respuesta)
    try:
        v = float(respuesta)
    except (TypeError, ValueError):
        return None
    if math.isnan(v):
        return None
    # Redondeo hacia arriba en el empate (2,5 es el nivel 3), no el bancario.
    nivel = int(math.floor(v + 0.5))
    if niveles is not None:
        nivel = max(0, min(niveles - 1, nivel))
    return str(nivel)


def wilson_inferior(aciertos: int, n: int, z: float = Z_WILSON) -> float | None:
    """Cota inferior del intervalo de Wilson de una proporcion."""
    if not n:
        return None
    p = aciertos / n
    centro = p + z * z / (2 * n)
    margen = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centro - margen) / (1 + z * z / n)


_SINONIMOS_NOUL = {"true": "true", "si": "true", "sí": "true", "yes": "true", "1": "true",
                   "false": "false", "no": "false", "0": "false"}


def normalizar_etiqueta(tipo: str, etiqueta, opciones=None) -> str | None:
    """La etiqueta humana en el vocabulario de la pregunta, o None si no es
    valida. `True`, «sí» o «Si» son «true» en un noul; un choice casa sin
    mayusculas con sus opciones; un score es el indice del nivel."""
    if etiqueta is None or isinstance(etiqueta, float) and math.isnan(etiqueta):
        return None
    if isinstance(etiqueta, bool):
        texto = "true" if etiqueta else "false"
    else:
        texto = str(etiqueta).strip()
    if not texto:
        return None
    if tipo == "noul":
        return _SINONIMOS_NOUL.get(texto.lower())
    if tipo == "choice":
        validas = {str(o).lower(): str(o) for o in (opciones or {})}
        return validas.get(texto.lower()) if validas else texto
    try:
        nivel = int(texto)
    except ValueError:
        return None
    if opciones is not None and not 0 <= nivel < len(opciones):
        return None
    return str(nivel)


def _filas_validas(filas: Iterable[dict]) -> list[dict]:
    return [f for f in filas if f.get("etiqueta") not in (None, "") and f.get("respuesta") is not None]


def metricas(filas: Iterable[dict], *, objetivo: float = PRECISION_OBJETIVO,
             min_cubiertas: int = MIN_CUBIERTAS) -> dict[str, Any]:
    """Acierto, precision y recall por clase, calibracion y umbral.

    Cada fila: {"etiqueta": humana, "respuesta": de Jev (ya en el vocabulario
    de la etiqueta), "confianza": 0-1 o None, "tokens": int|None,
    "coste": float|None}. Las filas sin etiqueta o sin respuesta no cuentan, y
    se dice cuantas son.
    """
    todas = list(filas)
    validas = _filas_validas(todas)
    n = len(validas)
    salida: dict[str, Any] = {"ejemplos": len(todas), "evaluables": n,
                              "sin_etiqueta": sum(1 for f in todas if f.get("etiqueta") in (None, "")),
                              "sin_respuesta": sum(1 for f in todas if f.get("respuesta") is None)}
    if not n:
        return {**salida, "acierto": None, "clases": {}, "ece": None, "umbral": None, "curva": [],
                "fiabilidad": []}
    aciertos = [f["etiqueta"] == f["respuesta"] for f in validas]
    salida["acierto"] = round(sum(aciertos) / n, 4)

    clases = sorted({f["etiqueta"] for f in validas} | {f["respuesta"] for f in validas})
    por_clase = {}
    for c in clases:
        vp = sum(1 for f in validas if f["respuesta"] == c and f["etiqueta"] == c)
        predichas = sum(1 for f in validas if f["respuesta"] == c)
        reales = sum(1 for f in validas if f["etiqueta"] == c)
        precision = vp / predichas if predichas else None
        recall = vp / reales if reales else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision and recall else (0.0 if precision is not None and recall is not None else None))
        por_clase[c] = {"precision": _r(precision), "recall": _r(recall), "f1": _r(f1),
                        "reales": reales, "predichas": predichas}
    salida["clases"] = por_clase
    salida["matriz"] = {e: dict(Counter(f["respuesta"] for f in validas if f["etiqueta"] == e))
                        for e in sorted({f["etiqueta"] for f in validas})}

    con_conf = [(float(f["confianza"]), ok) for f, ok in zip(validas, aciertos)
                if isinstance(f.get("confianza"), (int, float)) and not isinstance(f.get("confianza"), bool)
                and 0.0 <= float(f["confianza"]) <= 1.0]
    salida["ece"], salida["fiabilidad"] = _ece(con_conf)
    salida["curva"] = curva_cobertura(con_conf, n)
    salida["umbral"] = elegir_umbral(salida["curva"], objetivo=objetivo, min_cubiertas=min_cubiertas)
    salida["objetivo"] = objetivo
    salida["por_respuesta"] = _por_respuesta(validas, clases, salida["umbral"], objetivo, min_cubiertas)
    salida["avisos"] = [
        f"con el umbral {salida['umbral']} solo sale decidido el {round(100 * d['recuperadas'])} % "
        f"de los «{c}» reales: el umbral lo sostienen las otras respuestas"
        for c, d in salida["por_respuesta"].items()
        if d.get("recuperadas") is not None and d["reales"] >= MIN_REALES_AVISO
        and d["recuperadas"] < RECUPERADAS_MINIMAS] + [
        f"con el umbral {salida['umbral']} los «{c}» decididos tienen cota {d['precision_minima']}, "
        f"por debajo del objetivo {objetivo}: esa respuesta no llega sola"
        for c, d in salida["por_respuesta"].items()
        if d.get("decididas") and d.get("precision_minima") is not None
        and d["precision_minima"] < objetivo]
    tokens = [f["tokens"] for f in validas if isinstance(f.get("tokens"), (int, float))]
    costes = [f["coste"] for f in validas if isinstance(f.get("coste"), (int, float))]
    salida["tokens_medios"] = round(sum(tokens) / len(tokens), 1) if tokens else None
    salida["coste_medio_usd"] = round(sum(costes) / len(costes), 8) if costes else None
    return salida


def _por_respuesta(validas: list[dict], clases: list[str], umbral: float | None,
                   objetivo: float, min_cubiertas: int) -> dict[str, dict]:
    """Lo mismo, separado por lo que contesta Jev (decision 169). Un umbral
    comun lo pueden arrastrar las respuestas de una sola clase: en el soft 404
    v2, con 0,90 se decidian 62 paginas, 60 de ellas «no», y de los 29 soft 404
    reales solo 2 salian decididos. Por respuesta se ve que se decide de cada
    una con el umbral elegido (cuantas, con que cota y que parte de las reales
    recupera) y que umbral tendria esa respuesta sola."""
    salida = {}
    for c in clases:
        suyas = [f for f in validas if f["respuesta"] == c
                 and isinstance(f.get("confianza"), (int, float)) and not isinstance(f.get("confianza"), bool)]
        con_conf = [(float(f["confianza"]), f["etiqueta"] == c) for f in suyas]
        reales = sum(1 for f in validas if f["etiqueta"] == c)
        d: dict[str, Any] = {"respuestas": len(suyas), "reales": reales,
                             "umbral_propio": elegir_umbral(curva_cobertura(con_conf, len(con_conf)),
                                                            objetivo=objetivo, min_cubiertas=min_cubiertas)}
        if umbral is not None:
            encima = [ok for conf, ok in con_conf if conf >= umbral]
            inferior = wilson_inferior(sum(encima), len(encima))
            d.update({"decididas": len(encima), "aciertos": sum(encima),
                      "precision_minima": _r(inferior),
                      "recuperadas": _r(sum(encima) / reales) if reales else None})
        salida[c] = d
    return salida


def _r(v: float | None) -> float | None:
    return round(v, 4) if v is not None else None


def _ece(con_conf: list[tuple[float, bool]], cubos: int = CUBOS_ECE) -> tuple[float | None, list[dict]]:
    """Error de calibracion esperado: la media, pesada por ejemplos, de la
    distancia entre la confianza declarada y el acierto real en cada cubo."""
    if not con_conf:
        return None, []
    grupos: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for conf, ok in con_conf:
        grupos[min(cubos - 1, int(conf * cubos))].append((conf, ok))
    total = len(con_conf)
    ece = 0.0
    fiabilidad = []
    for i in sorted(grupos):
        g = grupos[i]
        conf_media = sum(c for c, _ in g) / len(g)
        acierto = sum(1 for _, ok in g if ok) / len(g)
        ece += len(g) / total * abs(conf_media - acierto)
        fiabilidad.append({"desde": i / cubos, "hasta": (i + 1) / cubos, "ejemplos": len(g),
                           "confianza_media": round(conf_media, 4), "acierto": round(acierto, 4)})
    return round(ece, 4), fiabilidad


def curva_cobertura(con_conf: list[tuple[float, bool]], total: int) -> list[dict]:
    """Para cada umbral posible (cada confianza distinta), que parte se
    decidiria sola y con que precision."""
    curva = []
    for umbral in sorted({c for c, _ in con_conf}):
        encima = [ok for c, ok in con_conf if c >= umbral]
        inferior = wilson_inferior(sum(encima), len(encima))
        curva.append({"umbral": round(umbral, 4), "cubiertas": len(encima),
                      "cobertura": round(len(encima) / total, 4) if total else 0.0,
                      "precision": round(sum(encima) / len(encima), 4) if encima else None,
                      "precision_minima": round(inferior, 4) if inferior is not None else None})
    return curva


def elegir_umbral(curva: list[dict], *, objetivo: float = PRECISION_OBJETIVO,
                  min_cubiertas: int = MIN_CUBIERTAS) -> float | None:
    """El umbral MAS BAJO (mas cobertura) con el que lo que queda encima llega a
    la precision objetivo —su cota inferior de Wilson— y cubre al menos
    `min_cubiertas` ejemplos. Y que siga llegando con cualquier umbral mas
    alto que cubra el minimo: un pico suelto no es un umbral, es suerte."""
    validos = [p for p in curva if p["cubiertas"] >= min_cubiertas and p.get("precision_minima") is not None]
    for i, p in enumerate(validos):
        if all(q["precision_minima"] >= objetivo for q in validos[i:]):
            return p["umbral"]
    return None


# --- juego de precision de una respuesta (decision 179) ----------------------------
#
# Con la respuesta interesante rara (paginas_sustitutas 9 de 200), un juego
# aleatorio no trae positivos bastantes: la cota de Wilson del «si» se queda en
# 0,35-0,65 aunque Jev acierte todos. El juego de precision etiqueta TODAS las
# veces que Jev contesto X en un conjunto amplio (o las N con mas confianza)
# mas una muestra aleatoria de control. La precision de X sale exacta para ese
# conjunto; el recall, no: los X reales que Jev no vio solo aparecen en el
# control.

ORIGEN_POSITIVO = "positivo_jev"
ORIGEN_CONTROL = "control"
CONTROL_PRECISION = 100


def _es_confianza(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= float(v) <= 1.0


def seleccion_de_precision(respondidas: list[dict], respuesta: str, *, n_positivos: int | None = None,
                           n_control: int = CONTROL_PRECISION,
                           semilla: int = 0) -> tuple[dict[str, dict], dict[str, Any]]:
    """Que filas de un conjunto amplio ya respondido entran en el juego de
    precision de `respuesta`, y de donde sale cada una.

    `respondidas`: [{"id", "respuesta" (en el vocabulario de la etiqueta),
    "confianza"}], una por fila con respuesta. Devuelve ({id: {"origen",
    "en_control"}}, resumen). Positivos: todas las filas con esa respuesta o,
    con `n_positivos`, las de mas confianza, con los empates del corte dentro
    (asi «todo X con confianza >= c esta en el juego» sigue siendo cierto y su
    precision tambien). Control: una muestra uniforme de TODO el conjunto
    (tambien puede caer en un positivo, que queda `en_control`), lo unico del
    juego que se parece al conjunto entero. Determinista con la semilla."""
    import random

    filas = sorted(respondidas, key=lambda r: str(r["id"]))
    suyas = [r for r in filas if r.get("respuesta") == respuesta]
    suyas.sort(key=lambda r: (-(float(r["confianza"]) if _es_confianza(r.get("confianza")) else -1.0),
                              str(r["id"])))
    elegidas = suyas
    corte = None
    if n_positivos is not None and n_positivos < len(suyas):
        if n_positivos <= 0:
            raise ValueError("n_positivos tiene que ser 1 o mas")
        ultima = suyas[n_positivos - 1].get("confianza")
        if not _es_confianza(ultima):
            raise ValueError("no se pueden elegir las de mas confianza: hay respuestas sin confianza")
        corte = float(ultima)
        elegidas = [r for r in suyas if _es_confianza(r.get("confianza")) and float(r["confianza"]) >= corte]
    seleccion = {str(r["id"]): {"origen": ORIGEN_POSITIVO, "en_control": False} for r in elegidas}
    control = random.Random(semilla).sample(filas, min(max(n_control, 0), len(filas)))
    for r in control:
        clave = str(r["id"])
        if clave in seleccion:
            seleccion[clave]["en_control"] = True
        else:
            seleccion[clave] = {"origen": ORIGEN_CONTROL, "en_control": True}
    resumen = {"respuesta": respuesta, "conjunto": len(filas), "respuestas_x": len(suyas),
               "positivos_en_juego": len(elegidas), "todos_los_positivos": len(elegidas) == len(suyas),
               "confianza_minima": corte, "control": len(control), "semilla": semilla}
    return seleccion, resumen


def metricas_precision(filas: Iterable[dict], respuesta: str, *, objetivo: float = PRECISION_OBJETIVO,
                       min_cubiertas: int = MIN_CUBIERTAS,
                       todos_los_positivos: bool = True,
                       confianza_minima: float | None = None) -> dict[str, Any]:
    """Lo que dice un juego de precision de la respuesta `respuesta`.

    Cada fila como en `metricas`, mas "origen" (`positivo_jev` | `control`) y
    "en_control". La precision de X y su cota de Wilson se calculan sobre los
    positivos (todas las X de Jev del conjunto, o las de confianza >= el corte),
    y el umbral propio de X, con su curva: todas las X por encima de cualquier
    confianza del juego estan etiquetadas. El recall NO: los X reales que Jev
    contesto con otra cosa no estan entre los positivos, y el juego tampoco
    sirve para el acierto, la ECE ni el umbral comun (los positivos estan
    sobrerrepresentados). Lo unico que los estima es el control, una muestra
    uniforme del conjunto, y se da aparte, con su tamano.

    Solo vale con las respuestas de las que salio el juego, y si son otras se
    niega (ValueError): todas sus filas salieron de filas con respuesta, asi
    que una sin ella (otro fichero, otra version) ya lo demuestra; un positivo
    que ya no dice X, tambien, y una fila de control que dice X cuando el
    juego trae todas las X (o con confianza >= `confianza_minima`, el corte de
    las de mas confianza), tambien: seria un positivo y no lo es."""
    todas = list(filas)
    positivos = [f for f in todas if f.get("origen") == ORIGEN_POSITIVO]
    sin_respuesta = sum(1 for f in todas if f.get("respuesta") is None)
    if sin_respuesta:
        raise ValueError(f"{sin_respuesta} filas del juego no tienen respuesta (de esta pregunta y version) "
                         "en este fichero: no son las respuestas con las que se saco el juego")
    otra = sum(1 for f in positivos if f["respuesta"] != respuesta)
    if otra:
        raise ValueError(f"{otra} positivos del juego no tienen la respuesta «{respuesta}» en estas "
                         "respuestas: no son las respuestas con las que se saco el juego")
    nuevas = sum(1 for f in todas if f.get("origen") != ORIGEN_POSITIVO and f["respuesta"] == respuesta
                 and (todos_los_positivos or confianza_minima is None
                      or (_es_confianza(f.get("confianza")) and float(f["confianza"]) >= confianza_minima)))
    if nuevas:
        raise ValueError(f"{nuevas} filas de control dicen «{respuesta}» en estas respuestas y el juego "
                         "las habria metido como positivos: no son las respuestas con las que se saco el juego")
    validas = _filas_validas(positivos)
    aciertos = sum(1 for f in validas if f["etiqueta"] == respuesta)
    con_conf = [(float(f["confianza"]), f["etiqueta"] == respuesta) for f in validas
                if _es_confianza(f.get("confianza"))]
    curva = curva_cobertura(con_conf, len(con_conf))
    umbral_propio = elegir_umbral(curva, objetivo=objetivo, min_cubiertas=min_cubiertas)
    cota = wilson_inferior(aciertos, len(validas))
    sin_etiqueta = sum(1 for f in positivos if f.get("etiqueta") in (None, ""))
    salida: dict[str, Any] = {
        "respuesta": respuesta, "positivos": len(positivos), "evaluables": len(validas),
        "sin_etiqueta": sin_etiqueta, "aciertos": aciertos,
        "precision": _r(aciertos / len(validas)) if validas else None,
        "precision_minima": _r(cota), "objetivo": objetivo,
        "umbral_propio": umbral_propio, "curva": curva,
        "recall": None,
        "recall_no_estimable": (
            f"Los positivos son las respuestas «{respuesta}» de Jev: los «{respuesta}» reales que Jev "
            "contesto con otra cosa no estan entre ellos, asi que el juego no dice cuantos se le "
            "escapan. Solo el control (muestra aleatoria del conjunto) lo estima."),
        "umbral": None,
        "umbral_no_sale_de_este_juego": (
            "El umbral comun pide la precision de todas las respuestas en una muestra que se parezca "
            "al conjunto; aqui los positivos estan sobrerrepresentados. Se pone con un juego aleatorio, "
            "y este dice si la respuesta rara llega sola (umbral_propio)."),
    }
    control = [f for f in todas if f.get("en_control")]
    inf_control = metricas(control, objetivo=objetivo, min_cubiertas=min_cubiertas)
    reales = [f for f in _filas_validas(control) if f["etiqueta"] == respuesta]
    vistos = sum(1 for f in reales if f["respuesta"] == respuesta)
    salida["control"] = {
        "ejemplos": len(control), "evaluables": inf_control["evaluables"],
        "acierto": inf_control["acierto"], "reales_x": len(reales),
        "recall_x": _r(vistos / len(reales)) if reales else None,
        "recall_x_minimo": _r(wilson_inferior(vistos, len(reales))),
        "nota": ("Muestra aleatoria del conjunto amplio: estima el recall, el acierto y la calibracion "
                 "del conjunto, con su tamano. Con pocos «%s» reales (%d) el recall es orientativo."
                 % (respuesta, len(reales))),
    }
    avisos = []
    if sin_etiqueta:
        avisos.append(f"faltan {sin_etiqueta} positivos por etiquetar: la precision ya no es la de todas "
                      f"las «{respuesta}» del juego, sino la de las etiquetadas")
    if not todos_los_positivos:
        avisos.append(f"el juego trae las «{respuesta}» de mas confianza, no todas: la precision es la "
                      "de esas (confianza >= el corte), no la de cualquier respuesta "
                      f"«{respuesta}»")
    if cota is not None and cota < objetivo:
        avisos.append(f"la cota de la precision de «{respuesta}» es {_r(cota)}, por debajo del "
                      f"objetivo {objetivo}")
    if validas and umbral_propio is None:
        avisos.append(f"«{respuesta}» no tiene umbral propio: ninguna confianza deja {min_cubiertas} o mas "
                      "por encima con la cota sobre el objetivo")
    if len(reales) < MIN_REALES_AVISO:
        avisos.append(f"el control trae {len(reales)} «{respuesta}» reales: su recall no dice nada")
    salida["avisos"] = avisos
    return salida


def repetibilidad(primera: dict[str, str | None], segunda: dict[str, str | None]) -> dict[str, Any]:
    """La misma pagina (misma clave) juzgada dos veces: que parte coincide.
    Las respuestas tienen que llegar ya en el vocabulario de la etiqueta
    (`etiqueta_de_respuesta`): en un score, 2,6 y 2,61 son el mismo nivel."""
    comunes = [k for k in primera if k in segunda and primera[k] is not None and segunda[k] is not None]
    iguales = sum(1 for k in comunes if primera[k] == segunda[k])
    return {"comparadas": len(comunes), "iguales": iguales,
            "repetibilidad": round(iguales / len(comunes), 4) if comunes else None,
            "distintas": sorted(k for k in comunes if primera[k] != segunda[k])[:20]}


def acuerdo(juez_a: dict[str, str | None], juez_b: dict[str, str | None]) -> dict[str, Any]:
    """Acuerdo entre dos jueces (dos modelos, o dos redacciones) con la kappa de
    Cohen: el acuerdo que no se explica por azar. No es acierto."""
    comunes = [k for k in juez_a if k in juez_b and juez_a[k] is not None and juez_b[k] is not None]
    n = len(comunes)
    if not n:
        return {"comparadas": 0, "acuerdo": None, "kappa": None}
    obs = sum(1 for k in comunes if juez_a[k] == juez_b[k]) / n
    ca = Counter(juez_a[k] for k in comunes)
    cb = Counter(juez_b[k] for k in comunes)
    esperado = sum(ca[c] * cb[c] for c in set(ca) | set(cb)) / (n * n)
    # Si los dos dan siempre lo mismo, la kappa es 0/0: no se sabe. Un juez que
    # dice siempre «false» no es perfecto.
    kappa = (obs - esperado) / (1 - esperado) if esperado < 1 else None
    return {"comparadas": n, "acuerdo": round(obs, 4), "kappa": round(kappa, 4) if kappa is not None else None}


def muestra_estratificada(por_plantilla: dict[str, list[Any]], n: int, *, semilla: int = 0) -> list[Any]:
    """Hasta `n` elementos repartidos entre plantillas por turnos: primero uno de
    cada plantilla, despues el segundo... Asi una plantilla de 20.000 fichas no
    se come el juego y una de 12 paginas legales tambien sale. Determinista con
    la semilla: el mismo juego se puede volver a sacar."""
    import random

    azar = random.Random(semilla)
    colas = {}
    for plantilla in sorted(por_plantilla):
        elementos = list(por_plantilla[plantilla])
        azar.shuffle(elementos)
        colas[plantilla] = elementos
    salida: list[Any] = []
    while len(salida) < n and any(colas.values()):
        # El orden de las plantillas se baraja en cada vuelta: con mas plantillas
        # que `n`, una vuelta a medias por orden alfabetico dejaba fuera a todo
        # un cliente (o a las rutas que empiezan por «/z»).
        orden = sorted(p for p in colas if colas[p])
        azar.shuffle(orden)
        for plantilla in orden:
            if len(salida) >= n:
                break
            salida.append(colas[plantilla].pop())
    return salida


def para_reetiquetar(juego: list[dict], version: int) -> list[dict]:
    """El mismo juego (mismos estados e ids) listo para etiquetar A CIEGAS con
    otra version de la pregunta (decision 180): la version nueva y sin la
    etiqueta, quien la puso ni la nota de antes. Una etiqueta de la v1 no vale
    para la v2 si cambian las opciones o lo que significa cada nivel, y verla
    empujaria a repetirla. `evaluar` exige que el juego sea de la version del
    catalogo, asi que sin esto un juego viejo no se puede volver a medir."""
    salida = []
    for fila in juego:
        if fila.get("version") == version:
            raise ValueError(f"{fila.get('id')}: el juego ya es de la version {version}")
        nueva = {k: v for k, v in fila.items() if k not in ("etiqueta", "etiquetado_por", "nota")}
        salida.append({**nueva, "version": version, "etiqueta": None, "etiquetado_por": None,
                       "nota": ""})
    return salida


# --- Fugas de un juego a las instrucciones (decision 182) ---
# Un ejemplo de las instrucciones copiado de un title del juego con el que se
# mide infla la medida (lecciones de la 169 y la 180). Para vigilarlo sin meter
# en el repo la LISTA de titles de los clientes, se guardan HUELLAS de sus
# fragmentos. No los ocultan: son sha256 sin sal de textos cortos y se podrian
# recuperar probando un diccionario; solo evitan tener la lista legible.

_SEPARADORES_TITLE = re.compile(r"\s[|\-\u2013\u2014:]\s|\|")
_ENTRE_COMILLAS = re.compile(r"(?<![A-Za-z])'([^']+?)'(?![A-Za-z])")
_ENTRE_OTRAS_COMILLAS = re.compile(r'"([^"]+?)"|\u2018([^\u2019]+?)\u2019|\u201c([^\u201d]+?)\u201d|\u00ab([^\u00bb]+?)\u00bb')
_ENTRE_PARENTESIS = re.compile(r"\(([^()]*)\)")


def fragmentos_de_title(texto: str) -> set[str]:
    """El texto entero y cada trozo entre separadores de title («|», « - »,
    « – », « : »), en minusculas y con los espacios juntados. Asi «Becas |
    Marca» casa con un ejemplo «Becas» y la marca no hace falta conocerla."""
    t = re.sub(r"\s+", " ", unicodedata.normalize("NFC", texto or "").strip().lower())
    salida = {t} | {trozo.strip() for trozo in _SEPARADORES_TITLE.split(t)}
    return {f for f in salida if f}


def huella(fragmento: str) -> str:
    return hashlib.sha256(fragmento.encode("utf-8")).hexdigest()[:16]


def huellas_de_juego(filas: Iterable[dict], campo: str) -> set[str]:
    """Huellas de los fragmentos de `estado[campo]` de cada fila de un juego."""
    salida: set[str] = set()
    for fila in filas:
        valor = (fila.get("estado") or {}).get(campo)
        if isinstance(valor, str):
            salida |= {huella(f) for f in fragmentos_de_title(valor)}
    return salida


def ejemplos_de_instrucciones(texto: str) -> list[str]:
    """Lo que unas instrucciones citan como ejemplo: lo que va entre comillas
    (simples sin contar el apostrofo de «site's», dobles, tipograficas o
    angulares) y cada elemento de una lista entre parentesis. Sobra algo de
    ruido, y se escapa lo que no tiene esa forma: un ejemplo sin comillas fuera
    de un parentesis, una parafrasis, una traduccion o un sinonimo de un title
    del juego («Work» frente a «Projects») no casan con ninguna huella. Es una
    red para la copia literal, no prueba que no haya fuga."""
    salida = [m.strip() for m in _ENTRE_COMILLAS.findall(texto)]
    salida += [next(g for g in m if g).strip() for m in _ENTRE_OTRAS_COMILLAS.findall(texto)]
    for lista in _ENTRE_PARENTESIS.findall(texto):
        salida += [x.strip().strip("'") for x in lista.split(",")]
    return list(dict.fromkeys(e for e in salida if e))


def fugas(texto: str, huellas: set[str]) -> list[tuple[str, str]]:
    """(ejemplo, fragmento) de cada ejemplo de `texto` con un fragmento cuya
    huella esta en las de un juego. Los fragmentos se comparan en NFC y en
    NFD: unas huellas generadas antes de normalizar pueden venir de un title
    con los acentos descompuestos."""
    return [(e, f) for e in ejemplos_de_instrucciones(texto)
            for f in sorted(fragmentos_de_title(e))
            if huella(f) in huellas or huella(unicodedata.normalize("NFD", f)) in huellas]
