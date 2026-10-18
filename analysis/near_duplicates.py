"""
Contenido casi duplicado (MinHash + LSH).

Hasta ahora solo se detectaba el duplicado byte-identico (`urls.body_hash`),
que en un sitio real casi no existe: basta un precio o una miga de pan
distinta para que dos fichas practicamente iguales dejen de parecerlo. Lo que
si existe --y es lo que canibaliza-- son paginas que comparten el 95% del
texto.

**Por que MinHash y no simhash.** Simhash es mas barato (un entero por pagina)
pero su distancia no se puede ensenar a nadie: medido sobre un texto de 300
palabras, cambiar 3 da 0,92 de "similitud" y cambiar 10 ya da 0,81. Un informe
que dice "estas dos paginas se parecen un 81%" cuando comparten el 97% del
texto no es utilizable. La firma MinHash estima la Jaccard de los trigramas,
que si significa lo que parece: 0,9 es "comparten nueve de cada diez trozos de
redaccion".

Todo lo de aqui son funciones puras sobre texto: ni base de datos ni Scrapy.
El analyzer las usa sobre `page_content.content_text`, que es el contenido sin
plantilla; con el body entero la cabecera y el pie ahogarian la senal y todo
el sitio saldria duplicado de todo.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections import defaultdict
from typing import Iterable, Sequence

import numpy as np
import regex

# Tamano de la firma. La similitud estimada tiene un error tipico de
# sqrt(s(1-s)/FIRMA): con 64 muestras son ~4 puntos de desviacion y hasta 7 de
# desvio real, medido -- una pagina al 93% se reportaba al 87,5% y se caia del
# umbral del 90%. Con 256 baja a ~2 puntos, que ya no cambia el veredicto.
# Cuesta lineal: firmar 2.000 paginas de 400 palabras pasa de 1 s a 4 s.
FIRMA = 256

# Un shingle es una ventana de K palabras consecutivas. Con palabras sueltas,
# dos paginas con el mismo vocabulario y distinto discurso salen identicas;
# con K=3 hace falta que compartan la redaccion. En las escrituras sin espacios
# son K caracteres (ver `_SIN_ESPACIOS`).
K_SHINGLE = 3

# Por debajo de esto la firma es ruido: dos fichas de 12 palabras coinciden
# por casualidad. Se dejan fuera en vez de inventar duplicados.
MIN_PALABRAS = 30

# En las escrituras sin espacios (ver `_SIN_ESPACIOS`) cada caracter es un
# token, y un caracter dice menos que una palabra: una palabra china tiene de
# media entre 1,5 y 2 caracteres, una japonesa algo mas. Para el minimo se
# cuentan por eso como media palabra: un texto CJK necesita 60 caracteres (dos
# o tres frases) para tener firma, y 25 no la tienen. Sin calibrar contra un
# censo real --no hay ninguno CJK en la BD--; el tailandes o el jemer, que
# cuentan tambien sus marcas vocalicas, llegan al minimo con menos palabras.
CARACTERES_POR_PALABRA = 2

# Similitud a partir de la cual dos paginas se consideran casi duplicadas.
# 0,9 es tambien el valor por defecto de Screaming Frog.
UMBRAL_SIMILITUD = 0.9

# Bandas del LSH: la firma se parte en BANDAS trozos de FILAS posiciones y dos
# paginas son candidatas si algun trozo coincide entero. La probabilidad de
# proponer una pareja es 1-(1-s^FILAS)^BANDAS, una curva en S cuyo punto medio
# esta en (1/BANDAS)^(1/FILAS). El reparto TIENE que seguir al umbral: con
# bandas fijas para el 90%, bajar el umbral a 0,6 no encuentra nada mas, porque
# esas parejas no llegan siquiera a medirse. Se elige por eso en
# `reparto_bandas`, no como constante.
#
# Repartos disponibles (BANDAS x FILAS = FIRMA) y donde cae su punto medio:
#
#   8 x 32 -> 0,94     16 x 16 -> 0,84     32 x 8 -> 0,65     64 x 4 -> 0,35
#
# Se coge el de filas mas anchas --el que menos candidatas genera-- que aun
# proponga al menos el 95% de las parejas que estan justo en el umbral. Mirar
# solo el punto medio no basta: para un umbral de 0,95 el reparto 8x32, con su
# medio en 0,94, solo propondria el 82%.
_REPARTOS = ((8, 32), (16, 16), (32, 8), (64, 4))

# Por debajo de esto el LSH empieza a dejarse parejas de verdad y "casi
# duplicado" deja de querer decir nada. No se prohibe, se avisa.
UMBRAL_MINIMO_FIABLE = 0.6


def punto_medio(bandas: int, filas: int) -> float:
    """Similitud a la que un reparto propone la mitad de las parejas."""
    return (1.0 / bandas) ** (1.0 / filas)


# Recall minimo exigido justo en el umbral. Por encima del umbral sube rapido.
_RECALL_MINIMO = 0.95


def deteccion(bandas: int, filas: int, s: float) -> float:
    """Probabilidad de que un reparto proponga una pareja de similitud ``s``."""
    return 1.0 - (1.0 - s ** filas) ** bandas


def reparto_bandas(umbral: float = UMBRAL_SIMILITUD) -> tuple[int, int]:
    """Bandas y filas apropiadas para buscar parejas a partir de ``umbral``.

    De los repartos posibles se queda el de filas mas anchas que llegue al
    recall minimo en el umbral; si ninguno llega --umbrales muy bajos-- el
    mejor de todos.
    """
    for bandas, filas in _REPARTOS:
        if deteccion(bandas, filas, umbral) >= _RECALL_MINIMO:
            return bandas, filas
    return max(_REPARTOS, key=lambda r: deteccion(r[0], r[1], umbral))


_PRIMO = (1 << 61) - 1  # primo de Mersenne: aritmetica modular barata
_MAX32 = np.uint64(0xFFFFFFFF)

# Escrituras que no separan las palabras con espacios. Partir por no-palabra
# dejaba una frase entera por token (`中文没有空格` era UNA palabra), asi que
# dos textos japoneses casi iguales no compartian casi ningun trigrama y uno de
# nueve frases no llegaba al minimo. Sin diccionario no se puede segmentar en
# palabras, asi que se parte en caracteres: los shingles pasan a ser trigramas
# de caracteres, que es lo habitual para medir parecido en CJK. `ー` (la marca
# de vocal larga del katakana) es de escritura comun y se anade a mano.
_SIN_ESPACIOS = r"\p{Han}\p{Hiragana}\p{Katakana}\p{Thai}\p{Lao}\p{Khmer}\p{Myanmar}ー"

# Un token es un caracter de esas escrituras (grupo 1) o una racha de letras,
# numeros, `_` y marcas combinantes (grupo 2). Las marcas (\p{M}) van DENTRO de
# la palabra: el `\w` de `re` no las casa, y el hindi se rompia en trozos sin
# vocales (`हिन्दी` daba `ह, न, द`), igual que cualquier escritura con matras o
# un texto latino en NFD. Sin marcas ni esas escrituras, `[\p{L}\p{N}_]` es
# exactamente el `\w` de `re` (comprobado sobre todos los puntos de codigo
# asignados): el texto latino da los mismos tokens que antes, y la misma firma.
# Una marca CONTINUA una palabra pero no la empieza: suelta, detras de un emoji
# o de un signo, no es texto, y como token propio cambiaba los shingles de
# cualquier texto latino con emojis (revision de #153, medido en blogs.uoc.edu).
# Y solo las que escriben (Mn, Mc): las envolventes (Me, el `⃣` de `1️⃣`) son
# adorno.
_EMPIEZA = rf"\p{{L}}\p{{N}}_--[{_SIN_ESPACIOS}]"
_SIGUE = rf"\p{{L}}\p{{N}}\p{{Mn}}\p{{Mc}}_--[{_SIN_ESPACIOS}]"
_TOKEN = regex.compile(
    rf"([{_SIN_ESPACIOS}])|([{_EMPIEZA}][{_SIGUE}]*)", regex.V1
)

# Selectores de variacion (`❤️` es `❤` + U+FE0F; los de ideogramas, U+E0100 en
# adelante): eligen como se PINTA un caracter, no cual es. Fuera antes de
# tokenizar, para que `A️B` siga siendo una palabra y `❤` y `❤️` no difieran.
_SELECTORES = regex.compile(r"\p{Variation_Selector}+")

# Formas de ancho completo y medio ancho (`１２３`, `ＡＢＣ`, `ｶﾀｶﾅ`), que el
# japones usa mezcladas con las normales. Solo ese bloque pasa por NFKC: NFKC
# entero cambiaria tambien `1º` por `1o` o `m²` por `m2`, y eso es la firma de
# todo texto en castellano.
_ANCHO = regex.compile(r"[\uFF00-\uFFEF]+")


def _coeficientes(n: int = FIRMA) -> tuple[np.ndarray, np.ndarray]:
    """Las n permutaciones del MinHash.

    Fijas y deterministas a proposito: dos ejecuciones distintas --y dos
    censos distintos-- tienen que dar la misma firma para el mismo texto.
    """
    rng = np.random.default_rng(20260914)
    a = rng.integers(1, _PRIMO, size=n, dtype=np.int64).astype(np.uint64)
    b = rng.integers(0, _PRIMO, size=n, dtype=np.int64).astype(np.uint64)
    return a, b


_A, _B = _coeficientes()


def _tokens(texto: str) -> tuple[list[str], int]:
    """Tokens en minusculas y cuantos son caracteres de escrituras sin espacios."""
    texto = unicodedata.normalize("NFC", texto)
    texto = _SELECTORES.sub("", texto)
    texto = _ANCHO.sub(lambda m: unicodedata.normalize("NFKC", m.group()), texto)
    tokens: list[str] = []
    caracteres = 0
    for caracter, palabra in _TOKEN.findall(texto.lower()):
        if caracter:
            caracteres += 1
            tokens.append(caracter)
        else:
            tokens.append(palabra)
    return tokens, caracteres


def tokenizar(texto: str) -> list[str]:
    """Partir en palabras en minusculas, sin puntuacion.

    En las escrituras sin espacios (chino, japones, tailandes...) cada caracter
    es un token; las marcas combinantes no parten la palabra.
    """
    return _tokens(texto)[0]


def longitud_suficiente(tokens: int, caracteres: int) -> bool:
    """Si un texto de ``tokens`` tokens (``caracteres`` de ellos sin espacios) da firma.

    Las palabras cuentan una; los caracteres sin espacios, 1/CARACTERES_POR_PALABRA.
    """
    palabras = tokens - caracteres
    return palabras * CARACTERES_POR_PALABRA + caracteres >= MIN_PALABRAS * CARACTERES_POR_PALABRA


def shingles(palabras: Sequence[str], k: int = K_SHINGLE) -> list[str]:
    """Ventanas de ``k`` palabras consecutivas."""
    if len(palabras) < k:
        return [" ".join(palabras)] if palabras else []
    return [" ".join(palabras[i : i + k]) for i in range(len(palabras) - k + 1)]


def _hash32(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=4).digest(), "big")


def firma(texto: str | None) -> np.ndarray | None:
    """Firma MinHash de un texto, o ``None`` si es demasiado corto.

    Cada posicion guarda el minimo de una permutacion de los hashes de los
    shingles. Dos textos parecidos comparten casi todos los shingles, asi que
    sus minimos coinciden en casi todas las posiciones: la fraccion de
    posiciones iguales estima la Jaccard de sus conjuntos de shingles.
    """
    if not texto:
        return None
    palabras, caracteres = _tokens(texto)
    if not longitud_suficiente(len(palabras), caracteres):
        return None

    unicos = {_hash32(s) for s in shingles(palabras)}
    if not unicos:
        return None

    h = np.fromiter(unicos, dtype=np.uint64, count=len(unicos))
    # (a*h + b) mod p, vectorizado sobre shingles x permutaciones.
    mezcla = (_A[:, None] * h[None, :] + _B[:, None]) % np.uint64(_PRIMO)
    return (mezcla.min(axis=1) & _MAX32).astype(np.uint32)


def similitud(a: np.ndarray, b: np.ndarray) -> float:
    """Fraccion de posiciones que dos firmas comparten (estima la Jaccard)."""
    return float(np.count_nonzero(a == b)) / len(a)


def clave_exacta(f: np.ndarray) -> bytes:
    """Clave hashable de una firma, para agrupar las identicas de una pasada."""
    return f.tobytes()


def parejas_candidatas(
    firmas: dict[int, np.ndarray],
    umbral: float = UMBRAL_SIMILITUD,
) -> set[tuple[int, int]]:
    """Parejas de ids cuyas firmas comparten alguna banda entera.

    Filtro barato previo a medir: sin esto habria que comparar todas contra
    todas, que en un censo de 20.000 paginas son 200 millones de parejas. El
    reparto de bandas se elige segun el umbral (ver `reparto_bandas`).
    """
    bandas, filas = reparto_bandas(umbral)

    candidatas: set[tuple[int, int]] = set()
    for banda in range(bandas):
        ini, fin = banda * filas, (banda + 1) * filas
        cubos: dict[bytes, list[int]] = defaultdict(list)
        for uid, f in firmas.items():
            cubos[f[ini:fin].tobytes()].append(uid)

        for grupo in cubos.values():
            if len(grupo) < 2:
                continue
            for i, a in enumerate(grupo):
                for b in grupo[i + 1 :]:
                    candidatas.add((a, b) if a < b else (b, a))
    return candidatas


def emparejar(
    firmas: dict[int, np.ndarray],
    umbral: float = UMBRAL_SIMILITUD,
) -> dict[int, list[tuple[int, float]]]:
    """Para cada id, los ids casi iguales y cuanto se parecen, de mas a menos."""
    vecinos: dict[int, list[tuple[int, float]]] = defaultdict(list)

    for a, b in parejas_candidatas(firmas, umbral):
        s = similitud(firmas[a], firmas[b])
        if s < umbral:
            continue
        vecinos[a].append((b, s))
        vecinos[b].append((a, s))

    for lista in vecinos.values():
        lista.sort(key=lambda par: (-par[1], par[0]))
    return dict(vecinos)


def jaccard(texto_a: str, texto_b: str) -> float:
    """Jaccard exacta de los trigramas de dos textos. Para tests y verificacion."""
    a = set(shingles(tokenizar(texto_a)))
    b = set(shingles(tokenizar(texto_b)))
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def agrupar_identicas(
    firmas: dict[int, np.ndarray],
) -> tuple[dict[int, np.ndarray], dict[int, list[int]]]:
    """Colapsar las paginas de firma identica en un representante.

    Sin esto, un grupo de 500 paginas iguales --que en un sitio con fichas
    vacias o paginados clonados es lo normal-- generaria 125.000 parejas en
    cada banda. Se compara una sola vez por grupo y luego se reparte.
    """
    grupos: dict[bytes, list[int]] = defaultdict(list)
    for uid, f in firmas.items():
        grupos[clave_exacta(f)].append(uid)

    representantes: dict[int, np.ndarray] = {}
    miembros: dict[int, list[int]] = {}
    for ids in grupos.values():
        ids.sort()
        representantes[ids[0]] = firmas[ids[0]]
        miembros[ids[0]] = ids
    return representantes, miembros


def analizar(
    firmas: dict[int, np.ndarray],
    umbral: float = UMBRAL_SIMILITUD,
    max_ejemplos: int = 5,
) -> dict[int, dict]:
    """Cuantas casi-duplicadas tiene cada pagina, cual es la mas parecida y ejemplos.

    Solo devuelve entrada para las paginas que tienen alguna. Las de firma
    identica salen con similitud 1,0 entre si.
    """
    representantes, miembros = agrupar_identicas(firmas)
    vecinos = emparejar(representantes, umbral)

    resultado: dict[int, dict] = {}
    for rep, ids in miembros.items():
        emparejados = vecinos.get(rep, [])
        fuera = sum(len(miembros[otro]) for otro, _ in emparejados)

        for uid in ids:
            gemelas = [x for x in ids if x != uid]
            total = len(gemelas) + fuera
            if not total:
                continue

            ejemplos = [(x, 1.0) for x in gemelas]
            for otro, s in emparejados:
                ejemplos.extend((x, s) for x in miembros[otro])

            resultado[uid] = {
                "count": total,
                "closest": max(s for _, s in ejemplos),
                "ejemplos": ejemplos[:max_ejemplos],
            }
    return resultado
