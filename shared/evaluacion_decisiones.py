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
"""

from __future__ import annotations

import math
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
        and d["recuperadas"] < RECUPERADAS_MINIMAS]
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
