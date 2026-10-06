"""Tabla de casos de la funcion unica de indexabilidad (criterio de #26).

Una tabla y no un test por caso porque lo que importa es poder leer de un
vistazo que decide el crawler en cada combinacion, y anadir una fila cuando
aparezca un caso nuevo del mundo real.

Cada fila marcada con `# ANTES:` es un caso que las dos implementaciones
anteriores resolvian mal o de forma distinta entre si.
"""

from __future__ import annotations

import pytest

from shared.indexabilidad import es_noindex, estado_indexabilidad, misma_url

URL = "https://x.com/pagina"

# (descripcion, kwargs, indexable esperado, motivo esperado)
CASOS = [
    # --- lo basico ---------------------------------------------------------
    ("200 sin directivas ni canonical", dict(status_code=200), True, "Indexable"),
    ("canonical a si misma", dict(status_code=200, canonical_href=URL), True, "Indexable"),
    ("canonical a otra", dict(status_code=200, canonical_href="https://x.com/otra"),
     False, "Canonicalised"),
    ("noindex", dict(status_code=200, meta_robots="noindex, follow"), False, "Noindex"),
    ("none es el atajo de noindex,nofollow",
     dict(status_code=200, meta_robots="none"), False, "Noindex"),
    ("404", dict(status_code=404), False, "Client Error (404)"),
    ("500", dict(status_code=500), False, "Server Error (500)"),
    ("301", dict(status_code=301), False, "Redirect (301)"),
    ("sin respuesta", dict(status_code=None), False, "Sin respuesta"),
    ("bloqueada por robots.txt manda sobre todo lo demas",
     dict(status_code=200, bloqueada_por_robots=True, canonical_href=URL),
     False, "Blocked by robots.txt"),

    # --- 204: una respuesta sin contenido no se indexa ---------------------
    # ANTES: el spider la daba por indexable (solo descartaba 3xx/4xx/5xx) y el
    # analyzer por no indexable (exigia ==200). Las dos columnas se contradecian.
    ("204 no tiene nada que indexar", dict(status_code=204), False, "Sin contenido (204)"),

    # --- robots especificos de bot ----------------------------------------
    # ANTES: solo se leia <meta name="robots">, asi que un noindex dirigido a
    # Googlebot no contaba.
    ("meta googlebot noindex aunque robots diga index",
     dict(status_code=200, meta_robots={"robots": "index, follow", "googlebot": "noindex"}),
     False, "Noindex"),
    ("meta de OTRO bot no nos afecta",
     dict(status_code=200, meta_robots={"robots": "index", "bingbot": "noindex"}),
     True, "Indexable"),

    # --- varias cabeceras X-Robots-Tag ------------------------------------
    # ANTES: se leia solo la ultima, asi que noindex + noarchive salia indexable.
    ("dos cabeceras X-Robots-Tag, una con noindex",
     dict(status_code=200, x_robots=["noarchive", "noindex"]), False, "Noindex"),
    ("X-Robots-Tag dirigida a googlebot",
     dict(status_code=200, x_robots=["googlebot: noindex"]), False, "Noindex"),
    ("X-Robots-Tag dirigida a otro bot",
     dict(status_code=200, x_robots=["bingbot: noindex"]), True, "Indexable"),
    ("max-snippet lleva dos puntos y NO es un bot",
     dict(status_code=200, x_robots=["max-snippet:-1"]), True, "Indexable"),

    # --- canonical en la cabecera Link ------------------------------------
    # ANTES: se extraia a `canonical_header` y nadie lo miraba.
    ("canonical solo en la cabecera, a otra URL",
     dict(status_code=200, canonical_header="https://x.com/otra"), False, "Canonicalised"),
    ("canonical en la cabecera, a si misma",
     dict(status_code=200, canonical_header=URL), True, "Indexable"),
    ("el del HTML manda sobre el de la cabecera",
     dict(status_code=200, canonical_href=URL, canonical_header="https://x.com/otra"),
     True, "Indexable"),

    # --- comparacion de URLs ----------------------------------------------
    # ANTES: el analyzer usaba w3lib, que no quita el puerto por defecto, asi
    # que un canonical a si misma con ":443" salia "Canonicalised".
    ("el puerto por defecto no cuenta",
     dict(status_code=200, canonical_href="https://x.com:443/pagina"), True, "Indexable"),
    ("la caja del host no cuenta",
     dict(status_code=200, canonical_href="https://X.COM/pagina"), True, "Indexable"),
    ("el fragmento no cuenta",
     dict(status_code=200, canonical_href=URL + "#seccion"), True, "Indexable"),
    ("www frente a sin www SI es canonicalizacion",
     dict(status_code=200, canonical_href="https://www.x.com/pagina"), False, "Canonicalised"),
    ("https a http SI es canonicalizacion",
     dict(status_code=200, canonical_href="http://x.com/pagina"), False, "Canonicalised"),
    ("la query cuenta",
     dict(status_code=200, canonical_href=URL + "?v=2"), False, "Canonicalised"),
    ("el orden de los parametros no cuenta",
     dict(status_code=200, page_url="https://x.com/p?b=1&a=2",
          canonical_href="https://x.com/p?a=2&b=1"), True, "Indexable"),
    # ANTES: 83 paginas de un censo real salian "Canonicalised" siendo
    # autocanonicas, porque la URL llevaba el caracter escapado
    # (`intel%C2%B7ligencia`) y el canonical el literal (`intel·ligencia`).
    # Pasa en catalan y en cualquier idioma con acentos en la URL.
    ("el escapado por ciento es la misma URL",
     dict(status_code=200, page_url="https://x.com/la-intel%C2%B7ligencia/",
          canonical_href="https://x.com/la-intel\u00b7ligencia/"), True, "Indexable"),
]


@pytest.mark.parametrize("descripcion,kwargs,indexable,motivo",
                         CASOS, ids=[c[0] for c in CASOS])
def test_tabla_de_indexabilidad(descripcion, kwargs, indexable, motivo):
    kwargs.setdefault("page_url", URL)
    assert estado_indexabilidad(**kwargs) == (indexable, motivo)


def test_la_barra_final_de_la_raiz_no_cuenta():
    assert misma_url("https://x.com", "https://x.com/")


def test_el_canonical_vacio_no_canonicaliza():
    assert estado_indexabilidad(200, canonical_href="   ", page_url=URL) == (True, "Indexable")


def test_es_noindex_mira_solo_la_directiva():
    """Lo usa el conteo de entrantes (C3 de #24): ahi da igual el codigo."""
    assert es_noindex(meta_robots="noindex") is True
    assert es_noindex(x_robots=["googlebot: none"]) is True
    assert es_noindex(meta_robots="index, follow") is False
    # Un 404 no es noindex: no esta en el indice por otra razon.
    assert es_noindex() is False


def test_la_barra_no_se_interpreta_como_separador():
    """Decision 22 del CLAUDE.md: `noindex/nofollow` no es noindex para Google.

    Si lo tokenizaramos por barra marcariamos como fuera del indice una pagina
    que Google SI indexa, y el cliente se quedaria creyendo lo contrario.
    """
    assert estado_indexabilidad(200, meta_robots="noindex/nofollow", page_url=URL) == (
        True, "Indexable")
