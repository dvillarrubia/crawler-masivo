"""Titulos que no dicen nada: el placeholder del CMS (decision 121).

`title_too_short` ya avisa de «Inicio», «Home» o «Blog» por la longitud. Lo que
se escapa es el titulo que se le olvido a alguien rellenar: «Sin título»,
«Untitled document», «Nueva página»... casi siempre con la marca detras
(«Sin título | Marca»), asi que pasa de los 10 caracteres y ningun aviso lo
ve. Es **determinista**: una lista cerrada, en las lenguas de
`senal_titulo` (es/en/ca/eu/fr/de/pt/it). Lo que no esta en la lista no se
afirma: «Electrodomésticos» o un titulo de una palabra no es un error
demostrable, y el juicio de «no identifica la pagina» es de #16 (con modelo).

Lo que NO es placeholder aunque lo parezca: «Inicio | Marca» en la portada es
su titulo logico, y «Home» lo es en ingles. No estan en la lista.

La marca se quita antes de comparar: es el segmento (partido con el mismo
separador que el soft 404, `_RE_SEPARADOR`) que aparece en mas de la mitad de
los titulos del host. Se compara con :func:`forma`, no con `lower()`:
«Página» y «Pagina», o «Título» y «título.», son el mismo texto.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from shared.soft404 import _RE_SEPARADOR, normalizar, sin_acentos

# Puntuacion que se quita de los extremos: «Título.» es «Título».
_EXTREMOS = " .,;:!¡?¿-–—_·|/\\\"'«»“”()[]…*"

# Ya en `forma`: minusculas, sin acentos, sin puntuacion en los extremos.
PLACEHOLDERS: frozenset[str] = frozenset({
    # es
    "sin titulo", "titulo", "pagina", "nueva pagina", "pagina nueva",
    "pagina sin titulo", "documento sin titulo", "documento", "titulo de la pagina",
    "hola mundo", "otro sitio realizado con wordpress", "mi sitio",
    # en
    "untitled", "untitled document", "untitled page", "new page", "page",
    "page title", "default title", "document", "title", "my site", "my blog",
    "just another wordpress site", "hello world", "test page",
    # ca
    "sense titol", "titol", "pagina nova", "nova pagina", "document sense titol",
    # eu
    "izenburugabea", "izenbururik gabe", "orri berria", "dokumentua",
    # fr
    "sans titre", "titre", "nouvelle page", "document sans titre", "page sans titre",
    # de
    "ohne titel", "unbenannt", "unbenanntes dokument", "neue seite", "titel", "seite",
    # pt
    "sem titulo", "documento sem titulo", "pagina sem titulo",
    # it
    "senza titolo", "nuova pagina", "titolo", "documento senza titolo",
    # La pagina por defecto del servidor, servida en lugar de la del sitio.
    "welcome to nginx", "apache2 ubuntu default page", "apache2 debian default page",
    "iis windows server", "test page for the apache http server",
})


def forma(texto: str | None) -> str:
    """El texto para comparar: `normalizar`, sin acentos y sin la puntuacion
    de los extremos."""
    return sin_acentos(normalizar(texto)).strip(_EXTREMOS).strip()


def segmentos(titulo: str | None) -> list[str]:
    """Los trozos del titulo partido por sus separadores, en :func:`forma`."""
    return [s for s in (forma(t) for t in _RE_SEPARADOR.split(titulo or "")) if s]


def marcas_del_host(titulos: Iterable[str]) -> frozenset[str]:
    """Los segmentos que aparecen en mas de la mitad de los titulos del host.

    Solo cuentan los titulos con dos segmentos o mas: en «Marca» a secas no se
    sabe que es la marca. Con un solo titulo no hay mitad que valga.
    """
    n = 0
    cuenta: Counter[str] = Counter()
    for t in titulos:
        n += 1
        segs = segmentos(t)
        if len(segs) >= 2:
            cuenta.update(set(segs))
    if n < 2:
        return frozenset()
    return frozenset(s for s, c in cuenta.items() if c * 2 > n)


def es_title_generico(titulo: str | None, marcas: frozenset[str] = frozenset()) -> bool:
    """¿El titulo entero, o lo que queda al quitarle la marca, es un placeholder?"""
    if forma(titulo) in PLACEHOLDERS:
        return True
    segs = segmentos(titulo)
    if len(segs) < 2:
        return False
    resto = [s for s in segs if s not in marcas]
    if len(resto) == 1:
        return resto[0] in PLACEHOLDERS
    # Todos los segmentos son «marca» (el sitio entero se llama asi): basta con
    # que uno sea el placeholder y el otro la marca de verdad.
    return not resto and any(s in PLACEHOLDERS for s in segs)


def description_igual_title(description: str | None, titulo: str | None) -> bool:
    """La description repite el titulo. Una que solo EMPIEZA por el titulo no."""
    d, t = forma(description), forma(titulo)
    return bool(d) and d == t


__all__ = ["PLACEHOLDERS", "forma", "segmentos", "marcas_del_host",
           "es_title_generico", "description_igual_title"]
