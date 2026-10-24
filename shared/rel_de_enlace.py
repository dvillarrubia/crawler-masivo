"""El atributo ``rel`` de un enlace: sus tokens y cuales le quitan autoridad.

Vive en `shared/` por lo mismo que las reglas de robots (decisiones 23, 39 y
64): lo usan el spider —para marcar ``links.follow``— y el analizador —para
saber si un ``follow=False`` viene del ``rel`` del enlace o de la directiva de
la pagina (decision 183)—. Dos copias de «que es un nofollow» acaban
discrepando, y la del analizador se escribe en SQL: por eso la regla en SQL
esta aqui, al lado de la de Python, y un test comprueba que las dos dan lo
mismo sobre los mismos ``rel``.
"""

from __future__ import annotations

import re

from sqlalchemy import case, func, literal, or_

# Las tres pistas con las que Google dice "no pases autoridad por aqui". Para
# el buscador son equivalentes; lo que cambia es lo que declaran (publicidad,
# contenido de usuario, o simplemente nada).
REL_SIN_AUTORIDAD: frozenset[str] = frozenset({"nofollow", "ugc", "sponsored"})

# Lo que separa tokens: el espacio en blanco ASCII de HTML (mas \v), el
# espacio duro, y la coma, porque hay plantillas que escriben
# `rel="nofollow,noopener"` y separando solo por espacios salia un unico token
# `nofollow,noopener` y el enlace contaba como follow. Explicito y no `\s`
# para que el SQL de abajo pueda hacer exactamente lo mismo.
SEPARADORES_REL = " \t\n\r\f\v\xa0,"
_SEPARA = re.compile("[" + re.escape(SEPARADORES_REL) + "]+")


def rel_tokens(rel: str | None) -> set[str]:
    """Tokens de un atributo `rel`, en minusculas."""
    if not rel:
        return set()
    return {t for t in _SEPARA.split(rel.lower()) if t}


def rel_sin_autoridad(rel: str | None) -> bool:
    """El ``rel`` lleva ``nofollow``, ``ugc`` o ``sponsored`` como token."""
    return bool(REL_SIN_AUTORIDAD & rel_tokens(rel))


def sql_rel_sin_autoridad(columna):
    """La misma regla que :func:`rel_sin_autoridad`, como expresion SQL (vale
    en Postgres y en SQLite): cada separador pasa a espacio, se rodea de
    espacios y se busca `` nofollow `` como palabra entera. Asi `nofollowed` o
    `x-ugc` no son ``nofollow``, como no lo son para el spider."""
    texto = func.lower(func.coalesce(columna, ""))
    for sep in SEPARADORES_REL:
        if sep != " ":
            texto = func.replace(texto, literal(sep), literal(" "))
    rodeado = literal(" ") + texto + literal(" ")
    return or_(*[rodeado.like(f"% {r} %") for r in sorted(REL_SIN_AUTORIDAD)])


def sql_nofollow_por_rel(columna_follow, columna_rel):
    """True si el enlace no pasa senales por su propio ``rel`` (no por la
    directiva de la pagina), NULL si no: para una columna de una temporal de
    millones de filas, que asi no engorda. El ``rel`` solo se mira cuando
    ``follow`` ya es False."""
    return case(
        (columna_follow.is_(False), case((sql_rel_sin_autoridad(columna_rel), True), else_=None)),
        else_=None,
    )
