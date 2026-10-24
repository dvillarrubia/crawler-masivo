"""Lo que se sabe de un WordPress por su REST API publica (#33, decision 161).

En `shared/` porque lo usan los dos lados: el spider decide que pedir y como
leer cada respuesta, y el analisis decide que tipos cuentan como contenido
publicado (decisiones 23 y 39: dos copias acaban discrepando).

Solo comportamiento documentado de WordPress, sin codigo suyo:

- La raiz se descubre por la cabecera ``Link: <...>; rel="https://api.w.org/"``
  o el ``<link>`` del ``<head>``; con los enlaces permanentes desactivados es
  ``?rest_route=/``. Si no se anuncia (hay temas que lo quitan: la home de
  blogs.uoc.edu no lo lleva y su ``/wp-json/`` responde), se prueba
  ``/wp-json/`` en el origen.
- ``per_page`` va de 1 a 100, y pedir una pagina mas alla de
  ``X-WP-TotalPages`` es un 400: se pagina por esa cabecera, nunca «hasta que
  venga vacia». Una pagina puede venir con MENOS de ``per_page`` aunque haya
  mas (los medios de un borrador se cuentan y no se devuelven: en
  wordpress.org/news, ``media?per_page=1`` devuelve ``[]`` con 1.685 en
  ``X-WP-Total``).
- Los adjuntos tienen estado ``inherit``, no ``publish``.
- ``/wp/v2/users`` no se pide nunca: expone los nombres de usuario, no aporta
  nada a SEO y es lo primero que pide una herramienta de ataque.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ADAPTADOR = "wordpress"

# El `rel` con el que WordPress anuncia su REST API (rest-api.php).
REL_API = "https://api.w.org/"

POR_PAGINA = 100

# Tipos de entrada del nucleo que no son paginas publicas del sitio: menus,
# patrones, plantillas del editor de sitio, estilos y fuentes. Los adjuntos se
# leen aparte (endpoint `media`, otros campos y otro hallazgo).
TIPOS_INTERNOS = frozenset({
    "attachment", "nav_menu_item", "wp_block", "wp_template", "wp_template_part",
    "wp_global_styles", "wp_navigation", "wp_font_family", "wp_font_face",
})
# Taxonomias del nucleo que no tienen archivo publico (o no uno que importe).
TAXONOMIAS_INTERNAS = frozenset({
    "nav_menu", "wp_pattern_category", "wp_theme", "wp_template_part_area",
    "link_category", "post_format",
})
# Los dos tipos que existen en todo WordPress y que siempre son contenido. Un
# tipo propio (producto, evento...) solo cuenta como contenido publicado si el
# rastreo encontro al menos una entrada suya: hay tipos con `rest_base` que no
# tienen pagina publica (autores invitados de Co-Authors Plus, formularios) y
# darlos por «publicados y no rastreados» seria inventar el hallazgo.
TIPOS_SIEMPRE_PUBLICOS = frozenset({"post", "page"})

# Campos que se piden de cada tipo (`_fields`): lo justo para emparejar y
# juzgar. Sin `content` ni `yoast_head`, que multiplican el peso por cien.
CAMPOS_ENTRADA = "id,link,status,type,modified_gmt"
CAMPOS_MEDIO = "id,link,status,post,mime_type,source_url"
CAMPOS_TERMINO = "id,link,count,taxonomy,parent"
CAMPOS_INDICE = "name,url,home,namespaces,show_on_front,page_for_posts"

# Namespace de la REST API -> plugin SEO que lo registra.
PLUGINS_SEO = (
    ("yoast/v1", "Yoast SEO"),
    ("rankmath/v1", "Rank Math"),
    ("aioseo/v1", "All in One SEO"),
    ("seopress/v1", "SEOPress"),
)

_RE_BASE = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.IGNORECASE)
_RE_LINK = re.compile(r"<([^>]+)>\s*;([^,]*)")


def es_indice(datos) -> bool:
    """Si una respuesta JSON es el indice de una REST API de WordPress: la
    que sirve el nucleo lleva siempre el namespace ``wp/v2``."""
    return (isinstance(datos, dict) and isinstance(datos.get("namespaces"), list)
            and "wp/v2" in datos["namespaces"])


def plugin_seo(namespaces) -> str | None:
    nombres = set(namespaces or ())
    for ns, nombre in PLUGINS_SEO:
        if ns in nombres:
            return nombre
    return None


def raiz_por_defecto(url: str) -> str | None:
    """``/wp-json/`` en el origen de *url*."""
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    return urlunsplit((p.scheme, p.netloc, "/wp-json/", "", ""))


def raiz_de_cabecera(valor: str | None, base: str) -> str | None:
    """La raiz que anuncia una cabecera ``Link`` (``rel="https://api.w.org/"``),
    absoluta respecto a *base*, o None."""
    from urllib.parse import urljoin

    for href, params in _RE_LINK.findall(valor or ""):
        rels = re.findall(r'rel\s*=\s*"?([^";]+)"?', params, re.IGNORECASE)
        if any(r.strip() == REL_API for r in rels):
            return urljoin(base, href.strip())
    return None


def url_rest(raiz: str, ruta: str, params: dict | None = None) -> str:
    """La URL de *ruta* (``wp/v2/posts``) bajo *raiz*.

    Con enlaces permanentes la raiz es ``https://x.com/wp-json/`` y la ruta se
    concatena; sin ellos es ``https://x.com/?rest_route=/`` y la ruta va en el
    parametro. Los demas parametros, en orden fijo para que dos peticiones
    iguales sean la misma URL.
    """
    p = urlsplit(raiz)
    consulta = dict(parse_qsl(p.query, keep_blank_values=True))
    extra = list((params or {}).items())
    ruta = ruta.strip("/")
    if "rest_route" in consulta:
        base = (consulta.pop("rest_route") or "/").rstrip("/")
        pares = [*consulta.items(), ("rest_route", f"{base}/{ruta}" if ruta else base or "/"),
                 *extra]
        return urlunsplit((p.scheme, p.netloc, p.path or "/", urlencode(pares, safe="/,"), ""))
    camino = (p.path.rstrip("/") + "/" + ruta) if ruta else (p.path or "/")
    return urlunsplit((p.scheme, p.netloc, camino,
                       urlencode([*consulta.items(), *extra], safe="/,"), ""))


def tipos_de_contenido(tipos) -> list[tuple[str, str]]:
    """``(slug, rest_base)`` de los tipos de ``wp/v2/types`` que pueden ser
    paginas publicas: fuera los internos del nucleo y los de otro namespace o
    con una ruta que no es un nombre (``font-families/(?P<id>...)``)."""
    salida: list[tuple[str, str]] = []
    if not isinstance(tipos, dict):
        return salida
    for slug, datos in tipos.items():
        if not isinstance(datos, dict) or slug in TIPOS_INTERNOS:
            continue
        base = datos.get("rest_base")
        if not isinstance(base, str) or not _RE_BASE.match(base):
            continue
        if (datos.get("rest_namespace") or "wp/v2") != "wp/v2":
            continue
        salida.append((str(slug), base))
    return sorted(salida, key=lambda t: (t[0] not in TIPOS_SIEMPRE_PUBLICOS, t[0]))


def taxonomias(taxs, tipos: set[str]) -> list[tuple[str, str]]:
    """``(slug, rest_base)`` de las taxonomias de ``wp/v2/taxonomies`` que
    clasifican alguno de *tipos* y tienen archivo publico."""
    salida: list[tuple[str, str]] = []
    if not isinstance(taxs, dict):
        return salida
    for slug, datos in taxs.items():
        if not isinstance(datos, dict) or slug in TAXONOMIAS_INTERNAS:
            continue
        base = datos.get("rest_base")
        if not isinstance(base, str) or not _RE_BASE.match(base):
            continue
        if (datos.get("rest_namespace") or "wp/v2") != "wp/v2":
            continue
        if tipos and not (set(datos.get("types") or ()) & tipos):
            continue
        salida.append((str(slug), base))
    return sorted(salida)


def url_de_pagina(link: str | None, n: int) -> str | None:
    """La pagina *n* del archivo cuya pagina 1 es *link*.

    Con enlaces permanentes, ``<link>page/<n>/`` (lo que enlaza el paginador
    del nucleo); sin ellos (``?cat=3``), el parametro ``paged``. None si
    *link* no es una URL http(s).
    """
    if not link:
        return None
    p = urlsplit(link)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    if p.query:
        pares = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != "paged"]
        return urlunsplit((p.scheme, p.netloc, p.path or "/",
                           urlencode([*pares, ("paged", str(n))]), ""))
    camino = (p.path or "/")
    if not camino.endswith("/"):
        camino += "/"
    return urlunsplit((p.scheme, p.netloc, f"{camino}page/{n}/", "", ""))


def entero(valor) -> int | None:
    """Una cabecera ``X-WP-Total`` / ``X-WP-TotalPages`` como entero."""
    try:
        n = int(str(valor).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None
