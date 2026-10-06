"""
PageRank interno: peso de las aristas, nodos que cuentan e iteracion.

Todo lo de aqui son funciones puras (numpy, sin base de datos). El analyzer
monta las aristas en SQL y llama a `pagerank`; los tests prueban el modelo
sobre grafos pequenos sin Postgres.

**La repeticion medida pone techo al peso de la posicion.** El peso sale de
la posicion del enlace (`content` 1, `nav` 0,25...), que se adivina por
etiquetas y clases y falla en 13 de 24 plantillas realistas: un
`<div class="main-menu">`, un menu en Tailwind o los filtros de una categoria
salian como `content` con peso maximo. Ahora se mide cuanto se repite cada
enlace (destino + anchor) entre las paginas de origen, en todo el sitio y en
su seccion, y lo que se repite en mas del 40% no puede pesar mas que un
enlace de menu, lo reconozca o no el HTML.

#24 proponia sustituir la posicion por `1 - sqrt(repeticion)`. Se descarto:
con esa curva lo que recibe una pagina en total (repeticion x peso) deja de
crecer a partir del 44% de las paginas y cae despues, asi que un enlace en
TODAS las paginas transmite menos que el mismo enlace en la mitad. Eso no es
descontar la plantilla, es castigar estar bien enlazado. Con un techo, un
enlace de plantilla vale poco pero sigue sumando por cada pagina en la que
esta. En 4 censos (seobide, workoholics, Lopesan, tucanaldesalud) los dos
dan un top parecido; donde difieren, el techo deja arriba lo que el sitio
enlaza desde todas partes -- en Lopesan, las paginas legales del pie. Eso es
un dato del sitio para el informe, no un fallo del modelo: separar paginas
comerciales de legales es trabajo del tipo de pagina (#12).

**Sumideros.** Antes entraban en el teletransporte TODAS las URLs internas
(404, redirecciones, PDFs, noindex) y la masa de las paginas sin salida se
repartia tambien entre ellas. Ahora el teletransporte y la masa colgante van
solo a las paginas 200 indexables: el resto recibe PageRank por sus enlaces
entrantes (asi se mide cuanto se desperdicia) pero no regala nada.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

# Peso por posicion del enlace. La tabla cubre TODO el vocabulario que emite
# extract_links. Antes solo declaraba cuatro valores y `nav` --el 59,9% de
# los enlaces de un sitio real-- caia en un 0.5 por defecto, mas que header y
# footer.
PESO_POSICION: dict[str | None, float] = {
    "content": 1.0,
    "sidebar": 0.4,
    "aside": 0.35,
    "nav": 0.25,
    "header": 0.25,
    "footer": 0.15,
    "form": 0.1,
    None: 0.3,
}

# Hasta REP_EDITORIAL de las paginas un enlace puede ser editorial (un
# servicio enlazado desde varios articulos) y conserva su peso. Por encima, el
# techo es REP_EDITORIAL / repeticion: lo que recibe el destino en total
# (repeticion x peso) se queda plano en vez de crecer, hasta que el enlace
# pesa lo que uno de menu (TECHO_PLANTILLA, al 40%); de ahi en adelante suma
# como plantilla. Nunca baja al repetirse mas: una rampa lineal entre el 10% y
# el 30% si bajaba (de 0,100 a 0,075), el mismo defecto que el 1 - sqrt.
# Medido en 4 censos con techos del 30% y del 50%: el mismo top 15.
REP_EDITORIAL = 0.1
TECHO_PLANTILLA = PESO_POSICION["nav"]

# Con pocas paginas de origen la fraccion es ruido: en un rastreo de 4
# paginas, un enlace en 2 ya "se repite en el 50%". Por debajo de esto no se
# mide la repeticion y queda el peso por posicion.
MIN_FUENTES_REPETICION = 20

# Repeticion por seccion (host + primer segmento de ruta): el formulario de
# contacto de las fichas de servicio o los filtros de una categoria no llegan
# al 10% del sitio, pero estan en casi todas las paginas de su seccion. Con 20
# se escapaba el formulario de seobide (seccion de 19 paginas, en 16).
MIN_FUENTES_SECCION = 10

# Categorias de nodo. Solo INDEXABLE entra en el teletransporte.
INDEXABLE = "indexable"
NO_INDEXABLE = "no_indexable"   # 200 HTML con noindex o canonicalizada
REDIRECCION = "redireccion"
ERROR = "error"                 # 4xx y 5xx
RECURSO = "recurso"             # 200 no HTML: PDF, imagen...
SIN_RESPUESTA = "sin_respuesta"  # sin codigo y sin redireccion: timeout, DNS

CATEGORIAS = (INDEXABLE, NO_INDEXABLE, REDIRECCION, ERROR, RECURSO, SIN_RESPUESTA)

# Lo que acaba aqui no vuelve a salir hacia el sitio: se pierde.
DESPERDICIADO = (ERROR, SIN_RESPUESTA)


def techo_por_repeticion(repeticion: float) -> float:
    """Peso maximo de un enlace presente en `repeticion` (0-1) de las paginas."""
    if repeticion <= REP_EDITORIAL:
        return 1.0
    return max(REP_EDITORIAL / repeticion, TECHO_PLANTILLA)


def peso_arista(posicion: str | None, repeticion: float | None) -> float:
    """Peso de un enlace: el de su posicion, con techo si se repite."""
    peso = PESO_POSICION.get(posicion, PESO_POSICION[None])
    if repeticion is None:
        return peso
    return min(peso, techo_por_repeticion(repeticion))


def _sql_posicion(col_posicion: str) -> str:
    casos = " ".join(
        f"WHEN '{pos}' THEN {peso}"
        for pos, peso in PESO_POSICION.items() if pos is not None
    )
    return f"(CASE {col_posicion} {casos} ELSE {PESO_POSICION[None]} END)"


def sql_peso_arista(col_repeticion: str, col_posicion: str) -> str:
    """`peso_arista` en SQL. Sale de las mismas constantes, asi que el SQL y
    los tests no pueden divergir."""
    techo = (
        f"LEAST(1.0, GREATEST({REP_EDITORIAL} / GREATEST({col_repeticion}, 1e-9), "
        f"{TECHO_PLANTILLA}))"
    )
    return f"LEAST({_sql_posicion(col_posicion)}, {techo})"


def sql_peso_posicion(col_posicion: str) -> str:
    """Peso solo por posicion (rastreos demasiado pequenos para medir)."""
    return _sql_posicion(col_posicion)


def categoria(
    status_code: int | None,
    is_html: bool | None,
    indexable: bool | None,
    redirect_url: str | None,
) -> str:
    """En que categoria cae una URL para el teletransporte y el reparto."""
    if redirect_url or (status_code is not None and 300 <= status_code < 400):
        return REDIRECCION
    if status_code is None:
        return SIN_RESPUESTA
    if status_code >= 400:
        return ERROR
    if 200 <= status_code < 300:
        if not is_html:
            return RECURSO
        return INDEXABLE if indexable else NO_INDEXABLE
    return SIN_RESPUESTA


def pagerank(
    n: int,
    src: np.ndarray,
    dst: np.ndarray,
    w: np.ndarray,
    teletransporte: np.ndarray | None = None,
    damping: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-6,
) -> np.ndarray:
    """PageRank ponderado con teletransporte restringido. Suma 1.

    `src`, `dst` son indices 0..n-1 y `w` el peso de cada arista (se
    normaliza por el peso saliente de su origen). `teletransporte` es una
    mascara booleana de los nodos que reciben el salto aleatorio y la masa de
    los nodos sin salida; si es None o esta vacia, entran todos.
    """
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    src = np.asarray(src, dtype=np.int64)
    dst = np.asarray(dst, dtype=np.int64)
    w = np.asarray(w, dtype=np.float64)

    if teletransporte is None or not np.any(teletransporte):
        v = np.full(n, 1.0 / n)
    else:
        mascara = np.asarray(teletransporte, dtype=bool)
        v = mascara / mascara.sum()

    peso_saliente = np.bincount(src, weights=w, minlength=n)
    colgantes = peso_saliente == 0
    with np.errstate(divide="ignore", invalid="ignore"):
        w_norm = np.where(peso_saliente[src] > 0, w / peso_saliente[src], 0.0)

    pr = v.copy()
    for _ in range(max_iter):
        aporte = np.bincount(dst, weights=pr[src] * w_norm, minlength=n)
        nuevo = damping * aporte + ((1.0 - damping) + damping * pr[colgantes].sum()) * v
        dif = np.abs(nuevo - pr).max()
        pr = nuevo
        if dif < tol:
            break
    return pr


def reparto(pr: np.ndarray, categorias: Sequence[str]) -> dict[str, float]:
    """Fraccion del PageRank total que acaba en cada categoria."""
    total = float(pr.sum()) or 1.0
    cats = np.asarray(categorias)
    return {c: round(float(pr[cats == c].sum()) / total, 4) for c in CATEGORIAS}


def desperdiciado(reparto_: Mapping[str, float]) -> float:
    """Fraccion del PageRank que acaba en errores o URLs sin respuesta."""
    return round(sum(reparto_.get(c, 0.0) for c in DESPERDICIADO), 4)


# Decadas de PageRank que cubre la escala 0-100. Cada 25 puntos son "diez
# veces menos autoridad que la pagina mas fuerte del sitio".
#
# Cuatro, y no es arbitrario: medido en blogs.uoc.edu (34.704 paginas), el
# maximo es 393 veces la mediana y 4.515 veces el percentil 25, o sea entre 2,6
# y 3,7 decadas de rango util. Con 4 cabe el sitio entero y queda margen; con
# menos, media web se pegaria al 0.
DECADAS_ESCALA = 4.0


def puntuacion_log(pr):
    """PageRank a una escala 0-100 logaritmica relativa al MAXIMO del sitio.

    La escala 0-10 lineal de siempre no distingue la pagina 250 de la 25.000:
    la mas fuerte vale 10 y el resto se apelotona en 0,00xx — medido, 29.483 de
    34.704 paginas por debajo de 0,1, el 85% indistinguible entre si.

    Relativa al maximo y no min-max sobre los logaritmos: probado con min-max,
    el 78% de las paginas acababa en el decil mas alto, porque el minimo de la
    distribucion es un valor extremo (una pagina aislada) que estira la escala
    entera. Al anclarla al maximo, cada 25 puntos son una decada y el numero
    significa lo mismo en cualquier censo.

    Las paginas con 0 se quedan en 0: no reciben nada y eso es el dato.
    """
    import numpy as np

    crudo = np.asarray(pr, dtype=np.float64)
    maximo = crudo.max() if crudo.size else 0.0
    if maximo <= 0:
        return np.zeros(crudo.shape)
    with np.errstate(divide="ignore"):
        decadas = np.log10(np.where(crudo > 0, crudo, np.nan) / maximo)
    puntos = np.round(np.clip(1.0 + decadas / DECADAS_ESCALA, 0.0, 1.0) * 100.0)
    return np.where(crudo > 0, np.nan_to_num(puntos), 0.0)
