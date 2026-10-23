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
lo decidido alcanza la precision objetivo cubriendo al menos `MIN_CUBIERTAS`
ejemplos. Con menos ejemplos no hay umbral: una pregunta sin evaluar no puede
firmar respuestas (`decisiones.estado_por_confianza`).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable

PRECISION_OBJETIVO = 0.95
# Ejemplos que tienen que quedar por encima del umbral para poder elegirlo: con
# 5 aciertos de 5 la precision es 1,0 y no dice nada.
MIN_CUBIERTAS = 30
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
    nivel = int(round(v))
    if niveles is not None:
        nivel = max(0, min(niveles - 1, nivel))
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
                if isinstance(f.get("confianza"), (int, float))]
    salida["ece"], salida["fiabilidad"] = _ece(con_conf)
    salida["curva"] = curva_cobertura(con_conf, n)
    salida["umbral"] = elegir_umbral(salida["curva"], objetivo=objetivo, min_cubiertas=min_cubiertas)
    salida["objetivo"] = objetivo
    tokens = [f["tokens"] for f in validas if isinstance(f.get("tokens"), (int, float))]
    costes = [f["coste"] for f in validas if isinstance(f.get("coste"), (int, float))]
    salida["tokens_medios"] = round(sum(tokens) / len(tokens), 1) if tokens else None
    salida["coste_medio_usd"] = round(sum(costes) / len(costes), 8) if costes else None
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
        curva.append({"umbral": round(umbral, 4), "cubiertas": len(encima),
                      "cobertura": round(len(encima) / total, 4) if total else 0.0,
                      "precision": round(sum(encima) / len(encima), 4) if encima else None})
    return curva


def elegir_umbral(curva: list[dict], *, objetivo: float = PRECISION_OBJETIVO,
                  min_cubiertas: int = MIN_CUBIERTAS) -> float | None:
    """El umbral MAS BAJO (mas cobertura) con el que lo que queda encima llega a
    la precision objetivo y cubre al menos `min_cubiertas` ejemplos. Y que
    siga llegando con cualquier umbral mas alto: un pico suelto de precision
    con un umbral intermedio no es un umbral, es suerte."""
    validos = [p for p in curva if p["cubiertas"] >= min_cubiertas and p["precision"] is not None]
    for i, p in enumerate(validos):
        if all(q["precision"] >= objetivo for q in validos[i:]):
            return p["umbral"]
    return None


def repetibilidad(primera: dict[str, str | None], segunda: dict[str, str | None]) -> dict[str, Any]:
    """La misma pagina (misma clave) juzgada dos veces: que parte coincide."""
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
    kappa = (obs - esperado) / (1 - esperado) if esperado < 1 else 1.0
    return {"comparadas": n, "acuerdo": round(obs, 4), "kappa": round(kappa, 4)}


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
        for plantilla in sorted(colas):
            if colas[plantilla] and len(salida) < n:
                salida.append(colas[plantilla].pop())
    return salida
