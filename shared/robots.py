"""
Lectura de directivas robots (meta robots y X-Robots-Tag).

Vive en ``shared`` y no en el extractor porque el analyzer necesita las mismas
reglas y su imagen solo copia ``shared/`` y ``analysis/``. Tenerlo por
duplicado ya salio caro una vez: el extractor tokenizaba y el analyzer hacia
un ``"noindex" in valor``, asi que la misma pagina era indexable para uno y no
para el otro.
"""

from __future__ import annotations

import re
from typing import Any

# Directivas que Google reconoce. Sirven para decidir si un token raro esconde
# directivas de verdad o es basura cualquiera (una fecha, una URL).
DIRECTIVAS = frozenset({
    "all", "index", "follow", "noindex", "nofollow", "none", "noarchive",
    "nosnippet", "noimageindex", "notranslate", "nocache", "noodp", "noydir",
    "indexifembedded", "max-snippet", "max-image-preview", "max-video-preview",
    "unavailable_after",
})

# Las que de verdad restringen algo. `index`, `follow` y `all` son el
# comportamiento por defecto: que se pierdan no cambia nada.
RESTRICTIVAS = frozenset({
    "noindex", "nofollow", "none", "noarchive", "nosnippet", "noimageindex",
    "notranslate", "unavailable_after",
})

_SEPARADOR_VALIDO = re.compile(r"[,\s]+")

# Separadores que NO son validos entre directivas. Los dos puntos quedan fuera
# a proposito: son parte de `max-snippet:-1` y `unavailable_after: <fecha>`.
_SEPARADOR_INVALIDO = re.compile(r"[/|;]+")


def robots_tokens(value: str | None) -> set[str]:
    """Tokenizar una cadena de directivas robots.

    Parte por comas Y por espacios, para que marcado permisivo del mundo real
    como ``content="noindex nofollow"`` (sin comas) se entienda igual, tal y
    como lo leen Google y Screaming Frog. Los tokens salen en minusculas.
    """
    if not value:
        return set()
    return {t.strip().lower() for t in _SEPARADOR_VALIDO.split(value) if t.strip()}


def hay_noindex(value: str | None) -> bool:
    """¿Esta cadena saca la pagina del indice? ``none`` es el atajo de Google."""
    tokens = robots_tokens(value)
    return "noindex" in tokens or "none" in tokens


def robots_bad_separators(value: str | None) -> list[dict[str, Any]]:
    """Encontrar directivas robots pegadas con un separador invalido.

    La sintaxis oficial separa por comas (o por varias etiquetas meta).
    ``content="noindex/nofollow"`` llega como UN token desconocido, y Google
    ignora lo que no reconoce: la pagina se indexa igual y los enlaces se
    siguen igual. No se tokeniza por barra a proposito -- interpretarla seria
    inventarse un bloqueo que el buscador no aplica, y el cliente se quedaria
    creyendo que esa pagina esta fuera del indice. Esto alimenta un issue de
    sintaxis, nunca la indexabilidad calculada.

    Devuelve una entrada por token ofensor, con las directivas que esconde y
    cuales de ellas eran restrictivas (esas son las que se creen tener y no se
    tienen).
    """
    if not value:
        return []

    problemas: list[dict[str, Any]] = []
    for token in _SEPARADOR_VALIDO.split(value):
        token = token.strip().lower()
        if not token or token in DIRECTIVAS:
            continue
        if not _SEPARADOR_INVALIDO.search(token):
            continue

        piezas = [p for p in _SEPARADOR_INVALIDO.split(token) if p]
        directivas = [p for p in piezas if p in DIRECTIVAS]
        if not directivas:
            # Ni una directiva reconocible: una fecha, una URL, ruido. No es
            # un error de sintaxis robots, es otra cosa.
            continue

        problemas.append({
            "token": token,
            "directivas": directivas,
            "ignoradas": [d for d in directivas if d in RESTRICTIVAS],
        })

    return problemas
