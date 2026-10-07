"""Agrupar URLs por la FORMA de su ruta, sin reglas de ningun cliente.

Los scripts de control (`check_js_templates.py`, `check_content_quality.py`)
muestrean por plantilla: si el agrupamiento es malo, lo que se mide es azar.
Agrupar por "N niveles" juntaba plantillas que no tienen nada que ver —una
ficha de producto y una pagina legal de dos tramos caen en el mismo monton— y
las reglas genericas que habia estaban escritas para un cliente de hoteles, asi
que en cualquier otro sitio no casaba ninguna.

Las reglas POR CLIENTE siguen mandando: viajan en `config.templates` del job
(`projects/<cliente>/config.json`). Esto es el repuesto cuando no las trae.
"""

from __future__ import annotations

import os
import re

_RE_FECHA = re.compile(r"^\d{4}([-/]\d{1,2}){0,2}$")
_RE_NUM = re.compile(r"^\d+$")
# Codigos de idioma de verdad. Con `^[a-z]{2}$` a secas, `/tv/` pasaba por
# idioma y la seccion de video se comparaba con la portada del sitio.
IDIOMAS: frozenset[str] = frozenset((
    "es ca eu gl en fr de it pt nl pl ru sv da fi no cs sk hu ro bg el tr he ar "
    "zh ja ko hi th vi id ms uk sr hr sl et lv lt is ga cy mt sq mk bs"
).split())
_RE_IDIOMA = re.compile(r"^([a-z]{2})(-[a-z]{2})?$")


def es_idioma(tramo: str) -> bool:
    """True si el tramo es un codigo de idioma (`es`, `pt-br`)."""
    casa = _RE_IDIOMA.match((tramo or "").lower())
    return bool(casa) and casa.group(1) in IDIOMAS
# Un identificador de CMS dentro del tramo: `12345-nombre-del-producto`,
# `p/987`, `sku_4455`. Dos URLs que solo se diferencian en el numero son la
# misma plantilla.
_RE_CON_NUMERO = re.compile(r"\d{3,}")

MAX_TRAMOS_EN_LA_FORMA = 3


def firma_de_ruta(path: str) -> str:
    """Forma de la ruta: ``/2018-05-17/dia-del-reciclaje`` -> ``/:fecha/:slug``.

    Dos paginas con la misma forma casi siempre salen de la misma plantilla, y
    cuando no, se ve en las cifras y se afina subiendo las muestras.
    """
    tramos = [t for t in (path or "").strip("/").split("/") if t]
    if not tramos:
        return "/ (home)"
    partes: list[str] = []
    for tramo in tramos:
        bajo = tramo.lower()
        if _RE_FECHA.match(bajo):
            partes.append(":fecha")
        elif _RE_NUM.match(bajo):
            partes.append(":num")
        elif es_idioma(bajo) and not partes:
            partes.append(":idioma")
        elif "." in bajo:
            partes.append(":fichero" + os.path.splitext(bajo)[1])
        elif _RE_CON_NUMERO.search(bajo):
            partes.append(":id-slug")
        else:
            partes.append(bajo)
    # El ultimo tramo identifica la PAGINA, no la plantilla: sin normalizarlo,
    # cada articulo sale como plantilla propia con una sola muestra.
    if len(partes) > 1:
        forma = partes[:-1][:MAX_TRAMOS_EN_LA_FORMA] + [":slug"]
    elif partes[0].startswith(":"):
        # Un solo tramo que ya es un comodin dice algo (`/es` es la home de un
        # idioma, `/sitemap.xml` un fichero): se conserva.
        forma = partes
    else:
        forma = [":slug"]
    return "/" + "/".join(forma) + f" ({len(partes)}n)"
