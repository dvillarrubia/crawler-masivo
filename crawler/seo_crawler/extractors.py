"""
Pure extraction helpers -- no Scrapy imports.

Every public function receives either a ``parsel.Selector`` or raw HTML
bytes/str and returns plain Python data structures.  This keeps the
extraction logic testable without bringing in a full Scrapy response.
"""

from __future__ import annotations

import fnmatch
import functools
import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

from w3lib.encoding import html_body_declared_encoding, read_bom
from w3lib.url import canonicalize_url

# Las reglas robots viven en `shared` porque el analyzer, cuya imagen no
# copia `crawler/`, necesita leerlas igual. Se reexportan aqui para no
# romper a quien ya importaba `extractors.robots_tokens`.
from shared.dominios import dominio_registrable  # noqa: F401
from shared.indexabilidad import clave_url
# El texto visible vive en `shared/` porque la API lo necesita para la
# extraccion a medida (decision 104) y no puede importar `crawler/`.
from shared.texto_visible import (  # noqa: F401
    _BLOCK_TAGS,
    _CLASES_OCULTAS,
    _TAGS_NO_RENDERIZADOS,
    _WHITESPACE,
    _lineas_visibles,
    _nodo_oculto,
    _segmentos_visibles,
)
from shared.metadatos_html import es_media_movil, forma_de_href, normalizar_charset
from shared.robots import (  # noqa: F401
    robots_bad_separators,
    robots_tokens,
)
# Que `rel` quita autoridad lo decide tambien el analizador (decision 183): la
# regla vive en `shared/` y se reexporta aqui.
from shared.rel_de_enlace import (  # noqa: F401
    REL_SIN_AUTORIDAD as _REL_SIN_AUTORIDAD,
    rel_sin_autoridad,
    rel_tokens,
)

# ---- regex helpers ---------------------------------------------------------

# XPath translate() args for lowercasing attribute values (XPath 1.0 has no
# lower-case(); this is the standard workaround).
_XP_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_XP_LOWER = "abcdefghijklmnopqrstuvwxyz"


def _clean(text: str | None) -> str | None:
    """Collapse whitespace and strip a string, returning None when empty."""
    if not text:
        return None
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def _parse_int(value: str | None) -> int | None:
    """Pixeles de un atributo HTML. Un porcentaje NO son pixeles.

    `width="100%"` se leia como 100 px, que es lo contrario de lo que significa
    (ocupa todo el ancho). Mejor no saberlo que saberlo mal: con 100 px se
    descarta una imagen grande por pequena.
    """
    if not value:
        return None
    limpio = value.strip()
    if limpio.endswith("%"):
        return None
    limpio = limpio.rstrip("px").strip()
    try:
        return int(limpio)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------

# Las dos son puras y cada pagina repite el menu y el pie de todas las demas:
# sin cache se normalizaban ~700 URLs por pagina, casi todas ya vistas (#284).
# El tope las deja en unas decenas de MB.
_CACHE_URLS = 32_768


@functools.lru_cache(maxsize=_CACHE_URLS)
def compute_url_hash(url: str) -> str:
    """Return the hex SHA-256 of the canonicalized URL."""
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


_DEFAULT_PORTS = {"http": 80, "https": 443}


# La Public Suffix List, sin red: tldextract viene con Scrapy y trae una copia
# empotrada. `suffix_list_urls=()` le prohibe salir a internet (un rastreo no
# puede depender de eso) y `include_psl_private_domains=True` hace que
# `usuario.github.io` o `cliente.myshopify.com` cuenten como dominios distintos,
# que es lo correcto: son sitios distintos.
def normalize_host(host: str | None) -> str:
    """Forma comparable de un host: minusculas, sin punto final y en IDNA.

    Sin esto, una semilla `https://españa.com/` guardaba el host en unicode
    mientras los enlaces (normalizados por w3lib) salian en `xn--espaa-rta.com`:
    ningun enlace casaba con la semilla y el rastreo moria en la primera pagina.
    """
    if not host:
        return ""
    host = host.strip().lower().rstrip(".")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def _remove_dot_segments(path: str) -> str:
    """RFC 3986 5.2.4: resolver `/./` y `/../` de una ruta absoluta."""
    if "/." not in path:
        return path
    salida: list[str] = []
    segmentos = path.split("/")
    for i, seg in enumerate(segmentos):
        if seg == ".":
            if i == len(segmentos) - 1:
                salida.append("")
            continue
        if seg == "..":
            if len(salida) > 1:
                salida.pop()
            if i == len(segmentos) - 1:
                salida.append("")
            continue
        salida.append(seg)
    resultado = "/".join(salida)
    return resultado if resultado.startswith("/") else "/" + resultado


@functools.lru_cache(maxsize=_CACHE_URLS)
def normalize_url(url: str) -> str:
    """Canonicalize a URL using w3lib for consistent dedup.

    Encima de w3lib: quita el puerto por defecto y el punto final del host, y
    resuelve `/./` y `/../`. `https://x.com:443/a` y `https://x.com/a` daban
    hashes distintos y la misma pagina se rastreaba dos veces.
    """
    canon = canonicalize_url(url, keep_fragments=False)
    parts = urlsplit(canon)
    host = parts.hostname
    if not host:
        return canon
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = parts.netloc
    # Las credenciales (`https://usuario:clave@x.com/`) se quitan SIEMPRE: con
    # ellas iban a `urls.url`, a los enlaces, a los CSV y a `jobs.seeds`, que
    # lee cualquiera (decision 83), y la misma pagina tenia dos hashes. El
    # `userinfo` acaba en el ULTIMO `@` del netloc: un `@` dentro de la clave
    # va escapado (`%40`). Google no rastrea con ellas; la autenticacion de un
    # staging va en `http.basic_auth_user`/`basic_auth_password`.
    con_credenciales = "@" in netloc
    if (con_credenciales
            or (port is not None and port == _DEFAULT_PORTS.get(parts.scheme))
            or host.endswith(".")):
        host_limpio = host.rstrip(".")
        if ":" in host_limpio:  # IPv6
            host_limpio = f"[{host_limpio}]"
        netloc = host_limpio
        if port is not None and port != _DEFAULT_PORTS.get(parts.scheme):
            netloc += f":{port}"
    path = _remove_dot_segments(parts.path)
    if netloc == parts.netloc and path == parts.path:
        return canon
    return urlunsplit((parts.scheme, netloc, path, parts.query, parts.fragment))


# Lo unico que es una pagina para Google: un enlace `ftp://`, `ws://`, `sms:`,
# `whatsapp:` o `intent:` no es una arista del grafo ni se rastrea (decision 106).
ESQUEMAS_WEB = ("http", "https")


def es_url_web(url: str | None) -> bool:
    """Si *url* es absoluta y http(s), sin distinguir mayusculas."""
    if not url:
        return False
    try:
        return urlsplit(url.strip()).scheme.lower() in ESQUEMAS_WEB
    except ValueError:
        return False


def absolutize_url(base_url: str, href: str) -> str | None:
    """`href` resuelto contra `base_url` y normalizado, o None si no es una URL.

    `urljoin` lanza ValueError con hrefs como `https://[LINK]/x` o
    `http://ex.com]/x`. Sin capturarlo aqui, UN enlace malformado cortaba la
    extraccion entera de la pagina: se perdian enlaces, hreflang, datos
    estructurados, contenido y el seguimiento BFS, y la pagina quedaba sin
    aristas salientes en el PageRank.
    """
    try:
        return normalize_url(urljoin(base_url, href))
    except ValueError:
        return None


def compute_status_group(status_code: int | None) -> str:
    """Map an HTTP status code to a human-friendly group label."""
    if status_code is None:
        return "unknown"
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    if 500 <= status_code < 600:
        return "5xx"
    return "other"


def classify_resource_type(content_type: str | None, url: str) -> str:
    """Guess the resource type from Content-Type header or URL extension."""
    ct = (content_type or "").lower().split(";")[0].strip()

    if "html" in ct:
        return "html"
    # El SVG es su propio tipo. Clasificado como "image" (lo hacen tanto el
    # Content-Type `image/svg+xml` como la extension), `crawl_svg` no tenia
    # ningun efecto: no habia forma de excluirlo ni de incluirlo.
    if "svg" in ct:
        return "svg"
    if ct.startswith("image/"):
        return "image"
    if "css" in ct:
        return "css"
    if "javascript" in ct or "ecmascript" in ct:
        return "js"
    if "pdf" in ct:
        return "pdf"
    if "font" in ct or "woff" in ct:
        return "font"

    # Fallback: look at extension
    tipo = tipo_por_extension(url)
    return tipo or "other"


# Extension -> tipo, solo para las que lo identifican sin dudas. Separado de
# `classify_resource_type` porque hay una decision que depende de distinguir
# "la extension dice que es una imagen" de "no se sabe": pre-filtrar una
# peticion. `producto-2.5-kg` tiene un punto y no es ningun recurso.
_TIPO_POR_EXTENSION: dict[str, str] = {
    ".html": "html", ".htm": "html",
    ".css": "css",
    ".js": "js", ".mjs": "js",
    ".jpg": "image", ".jpeg": "image", ".png": "image",
    ".gif": "image", ".webp": "image", ".ico": "image", ".avif": "image",
    ".svg": "svg",
    ".pdf": "pdf",
    ".woff": "font", ".woff2": "font", ".ttf": "font", ".eot": "font", ".otf": "font",
}


def tipo_por_extension(url: str) -> str | None:
    """Tipo de recurso segun la extension de la URL, o None si no la identifica.

    Devolver None cuando no se sabe es la mitad importante: con `"other"` como
    respuesta por defecto, cualquier URL con un punto en el ultimo tramo
    —`producto-2.5-kg`, `v1.2-guia`— pasaria por recurso.
    """
    ruta = urlparse(url).path.lower()
    segmento = ruta.rstrip("/").rsplit("/", 1)[-1]
    punto = segmento.rfind(".")
    if punto <= 0:
        return None
    return _TIPO_POR_EXTENSION.get(segmento[punto:])


def is_internal_url(url: str, allowed_hosts: set[str]) -> bool:
    """Check whether *url* belongs to one of the *allowed_hosts*.

    Solo una URL web puede ser interna: `ftp://x.com/f.zip` o `ws://x.com/s`
    salian internas, el BFS las pedia y acababan como una peticion FTP o un
    error de esquema contados como paginas del sitio.
    """
    if not es_url_web(url):
        return False
    try:
        host = normalize_host(urlparse(url).hostname)
    except ValueError:
        return False
    if not host:
        return False
    # Strip leading www. for comparison
    bare = host.removeprefix("www.")
    return bare in allowed_hosts or host in allowed_hosts


def url_en_hosts(url: str, hosts, raices=()) -> bool:
    """True si *url* es de *hosts* (con y sin www) o de un subdominio de *raices*.

    Es el criterio de interno del spider (`SeoSpider._is_internal`) como
    funcion pura, para que lo usen tambien el middleware de credenciales y la
    preparacion de la pagina de Playwright, que no ven el spider (decision
    114). `raices` solo trae algo con `crawl_subdomains`.
    """
    if is_internal_url(url, hosts):
        return True
    if not raices or not es_url_web(url):
        return False
    try:
        host = normalize_host(urlparse(url).hostname)
    except ValueError:
        return False
    if not host:
        return False
    # Con punto delante: sin el, `notx.com` salia interno para `x.com`
    return any(host == rd or host.endswith("." + rd) for rd in raices)


def effective_base_url(selector, page_url: str) -> str:
    """Return the base URL used to resolve relative links on this page.

    Honours a ``<base href>`` element when present (resolved against the
    page URL), matching how browsers -- and Screaming Frog -- resolve
    relative URLs.  Falls back to *page_url* when no usable base tag
    exists, so callers can always pass the result straight to ``urljoin``.

    Solo se acepta la base si resuelve a una URL http(s). Comprobado en
    Chromium: una `<base href="javascript:void(0)">` o `data:` se IGNORA y la
    base sigue siendo la URL del documento; con `mailto:` el navegador deja los
    enlaces relativos sin resolver, o sea roto para todos. Antes se aceptaba
    cualquier cosa, asi que en esas plantillas —comunes en portales antiguos y
    en SPA— todos los enlaces relativos se quedaban relativos, salian como
    externos y no se rastreaba ninguno: el grafo interno de esas paginas
    desaparecia.
    """
    try:
        base_href = selector.css("base[href]::attr(href)").get()
    except Exception:
        base_href = None
    if base_href and base_href.strip():
        try:
            resuelta = urljoin(page_url, base_href.strip())
        except Exception:
            return page_url
        if urlparse(resuelta).scheme in ("http", "https"):
            return resuelta
    return page_url


def _resolve(base_url: str | None, href: str | None) -> str | None:
    """Resolve a possibly-relative *href* to an absolute URL.

    Returns *href* unchanged when it is falsy or no *base_url* is given.
    ``urljoin`` is a no-op on already-absolute URLs, so this is safe to
    call unconditionally.
    """
    if not href or not base_url:
        return href
    try:
        return urljoin(base_url, href)
    except Exception:
        return href


def compile_url_patterns(patterns: list[str] | None) -> tuple[list, list[str]]:
    """Compila los `include/exclude_patterns` de un job a comprobadores.

    Devuelve `(comprobadores, invalidos)`. Cada comprobador recibe una URL y
    dice si casa. El tipo se puede declarar con prefijo:

    - `glob:*/tag/*` — comodines de shell (fnmatch) sobre la URL entera.
    - `re:/tag/\\d+` — expresion regular, buscada en cualquier punto.
    - sin prefijo — como siempre: casa si casa como glob O como regex.

    Antes el patron se compilaba en cada URL dentro del callback: un glob como
    `*/tag/*` o `?sort=` no es una regex valida, lanzaba `re.error` y ninguna
    pagina seguia enlaces — el job acababa con una URL y sin error visible.
    Ahora un patron sin prefijo que no compila como regex se usa solo como
    glob, y se devuelve en `invalidos` para avisar. Un `re:` que no compila no
    se puede reinterpretar: se descarta y tambien se avisa.
    """
    comprobadores: list = []
    invalidos: list[str] = []
    for patron in patterns or []:
        if not isinstance(patron, str) or not patron:
            continue
        if patron.startswith("glob:"):
            glob = patron[5:]
            comprobadores.append(lambda url, g=glob: fnmatch.fnmatchcase(url, g))
            continue
        if patron.startswith("re:"):
            try:
                rx = re.compile(patron[3:])
            except re.error:
                invalidos.append(patron)
                continue
            comprobadores.append(lambda url, r=rx: r.search(url) is not None)
            continue
        try:
            rx = re.compile(patron)
        except re.error:
            invalidos.append(patron)
            comprobadores.append(lambda url, g=patron: fnmatch.fnmatchcase(url, g))
            continue
        comprobadores.append(
            lambda url, g=patron, r=rx: fnmatch.fnmatchcase(url, g) or r.search(url) is not None
        )
    return comprobadores, invalidos


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------

def _links_por_rel(selector, token: str, solo_head: bool = False) -> list[str]:
    """``href`` de los ``<link>`` cuyo ``rel`` contiene *token*.

    `rel` es una lista de tokens separados por espacios y no distingue
    mayusculas (`rel="Alternate canonical"` es valido). Comparar la cadena
    entera, como se hacia, dejaba fuera todo lo que no fuera exactamente el
    token en minusculas.
    """
    ambito = "//head//link" if solo_head else "//link"
    minuscula = f"translate(@rel, '{_XP_UPPER}', '{_XP_LOWER}')"
    # Se rodea de espacios para comparar por token entero y no por subcadena:
    # `rel="canonicalize"` no es un canonical.
    consulta = (
        f"{ambito}[@href and contains(concat(' ', normalize-space({minuscula}), ' '), $t)]"
        "/@href"
    )
    return [h for h in selector.xpath(consulta, t=f" {token.lower()} ").getall() if h]


def _primero_resuelto(base_url: str, hrefs: list[str]) -> str | None:
    for href in hrefs:
        resuelto = _resolve(base_url, _clean(href))
        if resuelto:
            return resuelto
    return None


def extract_meta(selector, base_url: str | None = None) -> dict[str, Any]:
    """
    Extract SEO-relevant <head> metadata from a *parsel.Selector*.

    When *base_url* is given, URL-valued fields (canonical, og:url,
    og:image, rel next/prev) are resolved to absolute URLs.  This matches
    Screaming Frog: a relative ``<link rel="canonical" href="/x">`` must be
    compared against the page URL as an absolute, otherwise self-referencing
    canonicals are misread as "canonicalised to a different URL".

    Returns a flat dict that maps 1-to-1 with ``HtmlMetaItem`` fields.
    """
    def _meta_all(name: str, con_property: bool = False) -> list[str]:
        """All content="" values of <meta name="...">, case-insensitively.

        El nombre se compara con `normalize-space`: `name=" robots"` con un
        espacio delante no se detectaba, y es un fallo de plantilla comun.

        `con_property` solo para Open Graph y Twitter: `property` es el atributo
        de OG y de RDFa, no de las metas clasicas, asi que un
        `<meta property="description">` —que es RDFa y Google no lee como meta
        description— se contaba como la description de la pagina.
        """
        consulta = (
            f"//meta[normalize-space(translate(@name, '{_XP_UPPER}', '{_XP_LOWER}')) = $n]"
            "/@content"
        )
        if con_property:
            consulta = (
                f"//meta[normalize-space(translate(@name, '{_XP_UPPER}', '{_XP_LOWER}')) = $n"
                f" or normalize-space(translate(@property, '{_XP_UPPER}', '{_XP_LOWER}')) = $n]"
                "/@content"
            )
        vals = selector.xpath(consulta, n=name.lower()).getall()
        return [v for v in (_clean(x) for x in vals) if v]

    def _meta(name: str, con_property: bool = False) -> str | None:
        vals = _meta_all(name, con_property=con_property)
        return vals[0] if vals else None

    # Title: first <title> that is NOT inside an inline <svg> (SVG has its own
    # <title> element which must not shadow the page title), joining all its
    # text nodes.
    title_nodes = selector.xpath("//title[not(ancestor::svg)]")
    title_text = (
        _clean(" ".join(title_nodes[0].xpath(".//text()").getall()))
        if title_nodes else None
    )
    desc = _meta("description")

    # Cuantos `<title>` y cuantas meta description hay, VACIOS INCLUIDOS. El
    # valor se sigue cogiendo como antes —el primer `<title>`, la primera
    # description con texto—, pero con dos no se sabe cual usara Google, y un
    # `<title></title>` vacio delante del bueno (dos plugins de SEO peleandose
    # por la cabecera) se saltaba en silencio. Screaming Frog lo reporta como
    # «multiple»; el analyzer emite `title_multiple` / `description_multiple`.
    title_count = len(title_nodes)
    description_count = len(selector.xpath(
        f"//meta[normalize-space(translate(@name, '{_XP_UPPER}', '{_XP_LOWER}'))"
        " = 'description']"
    ))

    # Robots: a page may carry SEVERAL robots meta tags; directives combine
    # and the most restrictive wins downstream, so join all of them.
    robots_vals = _meta_all("robots")
    meta_robots = ", ".join(robots_vals) if robots_vals else None

    # Robots dirigido a un bot concreto: `<meta name="googlebot" content="noindex">`
    # saca la pagina del indice de Google aunque el `robots` generico diga
    # `index`. Antes solo se leia el generico, asi que ese noindex no contaba.
    googlebot_vals = _meta_all("googlebot")
    meta_robots_googlebot = ", ".join(googlebot_vals) if googlebot_vals else None

    # `rel` es una lista de tokens y no distingue mayusculas: `rel="Canonical"`,
    # `rel="canonical "` y `rel="alternate canonical"` son canonicals validos
    # para Google y antes daban None, de modo que la pagina salia sin canonical.
    canonicals_head = _links_por_rel(selector, "canonical", solo_head=True)
    canonicals_todos = _links_por_rel(selector, "canonical")
    # Google IGNORA un canonical que esta en el body: no se usa como valor, pero
    # se cuenta para poder avisar.
    canonical = _resolve(base_url, _clean(canonicals_head[0])) if canonicals_head else None
    # Lo que decide si Google los ignora es cuantos DESTINOS distintos hay, no
    # cuantas etiquetas: `/a` y `https://x.com/a` dos veces (dos plugins de
    # SEO escribiendo lo mismo) es un canonical, y salia `canonical_multiple`.
    # Solo los del head: los del body Google no los lee (ya es
    # `canonical_in_body`). Un `href=""` no es un destino.
    destinos: set = set()
    for h in canonicals_head:
        resuelto = _resolve(base_url, _clean(h))
        if resuelto:
            try:
                destinos.add(clave_url(resuelto))
            except ValueError:
                destinos.add(resuelto)
    og_image_crudo = _meta("og:image", con_property=True)

    return {
        "title": title_text,
        "title_len": len(title_text) if title_text else None,
        "title_count": title_count,
        "meta_description": desc,
        "meta_description_len": len(desc) if desc else None,
        "meta_description_count": description_count,
        "meta_keywords": _meta("keywords"),
        "meta_robots": meta_robots,
        "meta_robots_googlebot": meta_robots_googlebot,
        "canonical_href": canonical,
        # Con varios canonicals Google los ignora todos, y en el body tambien:
        # el analyzer necesita saberlo para avisar en vez de quedarse con el
        # primero en silencio.
        "canonical_count": len(canonicals_todos),
        "canonical_in_body": len(canonicals_todos) > len(canonicals_head),
        "canonical_distintos": len(destinos),
        # OG
        "og_title": _meta("og:title", con_property=True),
        "og_description": _meta("og:description", con_property=True),
        "og_image": _resolve(base_url, og_image_crudo),
        "og_url": _resolve(base_url, _meta("og:url", con_property=True)),
        "og_type": _meta("og:type", con_property=True),
        # Twitter
        "twitter_card": _meta("twitter:card", con_property=True),
        "twitter_title": _meta("twitter:title", con_property=True),
        "twitter_description": _meta("twitter:description", con_property=True),
        # Pagination
        "rel_next": _primero_resuelto(base_url, _links_por_rel(selector, "next")),
        # `previous` es el sinonimo historico de `prev` y las plantillas lo
        # escriben: daba None y la paginacion salia sin enlace anterior.
        "rel_prev": _primero_resuelto(
            base_url,
            _links_por_rel(selector, "prev") or _links_por_rel(selector, "previous"),
        ),
        # Decision 108: lo que el HTML declara de si mismo, en crudo. El
        # analizador decide con `shared/metadatos_html.py`.
        **_declaraciones_de_pagina(selector, canonicals_head, og_image_crudo),
        "twitter_image": _resolve(base_url, _meta("twitter:image", con_property=True)),
        # Decision 156: la version AMP y la movil que la pagina anuncia. Solo
        # las del head, que son las que Google lee.
        "amphtml": _primero_resuelto(base_url, _links_por_rel(selector, "amphtml",
                                                               solo_head=True)),
        "alternate_movil": _alternate_movil(selector, base_url),
    }


def _alternate_movil(selector, base_url: str | None) -> str | None:
    """El ``href`` del ``<link rel="alternate" media="...">`` que anuncia la
    version movil (las URLs separadas, el m-dot), resuelto; None si no hay.

    Con ``hreflang`` es una anotacion de idioma, no la version movil, aunque
    lleve ``media``. Solo en el head (decision 156).
    """
    minuscula = f"translate(@rel, '{_XP_UPPER}', '{_XP_LOWER}')"
    for nodo in selector.xpath(
        f"//head//link[@href and @media and not(@hreflang) and contains("
        f"concat(' ', normalize-space({minuscula}), ' '), ' alternate ')]"
    ):
        if es_media_movil(nodo.xpath("@media").get()):
            resuelto = _resolve(base_url, _clean(nodo.xpath("@href").get()))
            if resuelto:
                return resuelto
    return None


def _declaraciones_de_pagina(selector, canonicals_head: list[str],
                             og_image_crudo: str | None) -> dict[str, Any]:
    """Viewport, `<html lang>` y la forma en que se escribieron el canonical y
    la og:image (decision 108).

    - ``viewport_count`` es tambien la marca de «medido»: 0 es que no hay
      viewport; NULL, una fila de un rastreo anterior. ``viewport`` es el
      ``content`` del ultimo, que es el que aplica el navegador (``""`` si lo
      tiene vacio).
    - ``html_lang``: el ``lang`` del primer ``<html>`` o, si no lo tiene, su
      ``xml:lang``. ``""`` si el atributo esta pero vacio, None si no esta (el
      mismo criterio que el alt). Recortado a la columna.
    - ``canonical_crudo``: el ``href`` del canonical que se usa, tal como se
      escribio. ``canonical_href`` lo guarda ya resuelto, y la forma —relativa,
      con fragmento— se perdia (`AUDITORIA_Y_VERIFICACION`, seccion 10).
    - ``og_image_forma``: solo la forma; la URL resuelta ya va en ``og_image``.
    """
    viewports = selector.xpath(
        f"//meta[normalize-space(translate(@name, '{_XP_UPPER}', '{_XP_LOWER}')) = 'viewport']"
    )
    # Con varios, el navegador aplica el ULTIMO (comprobado en Chromium con
    # emulacion movil: `device-width` y luego `width=1024` pinta a 1.024 px).
    # Guardar el primero daba `viewport_incorrecto` a una plantilla que se ve
    # bien y callaba la que se ve mal.
    viewport = None
    if viewports:
        viewport = _clean(viewports[-1].xpath("@content").get()) or ""
    lang = selector.xpath("(//html)[1]/@lang").get()
    if lang is None:
        lang = selector.xpath("(//html)[1]/@*[name()='xml:lang']").get()
    if lang is not None:
        lang = (_clean(lang) or "")[:35]
    return {
        "viewport": viewport,
        "viewport_count": len(viewports),
        "html_lang": lang,
        "canonical_crudo": _clean(canonicals_head[0]) if canonicals_head else None,
        "og_image_forma": forma_de_href(og_image_crudo),
    }


def _texto_de_heading(nodo_lxml) -> str | None:
    """Texto de un titular tal como se lee.

    Tres cosas que un ``//text()`` pelado hacia mal: un ``<script>`` o un
    ``<style>`` dentro del titular acababan en el texto; unir los nodos con
    espacio partia las palabras (``Zapa<span>tillas`` daba ``"Zapa tillas"`` y
    ``Pre<span>cio``, ``"Pre cio"``, lo que corrompia ``h1_duplicate`` y los
    recuentos); y un ``<h1>`` que solo lleva el logo quedaba vacio cuando el
    titular esta en el ``alt`` de la imagen, que es de donde lo lee Google.
    """
    texto = " ".join(_lineas_visibles(nodo_lxml, forzar_raiz=True)).strip()
    if texto:
        return _clean(texto)
    # Sin texto propio: el titular puede estar en el alt de la imagen o en el
    # aria-label del propio heading.
    for img in nodo_lxml.iter("img"):
        alt = (img.get("alt") or "").strip()
        if alt:
            return _clean(alt)
    return _clean(nodo_lxml.get("aria-label"))


# Un titular marcado `aria-hidden` no esta en el arbol de accesibilidad: es la
# copia movil o de escritorio de otro, o un adorno. Contarlo daba 3 h1 donde
# hay 1, con un `multiple_h1` falso. Se guarda igual, marcado, para no perder
# el dato: la decision de ignorarlo la toma el analyzer.
def _heading_oculto(nodo_lxml) -> bool:
    """True si el titular no se pinta o no cuenta como titular."""
    for el in (nodo_lxml, *nodo_lxml.iterancestors()):
        if not isinstance(el.tag, str):
            continue
        if el.tag in ("template", "noscript", "svg"):
            return True
        if _nodo_oculto(el):
            return True
        if (el.get("aria-hidden") or "").strip().lower() == "true":
            return True
    return False


def extract_headings(selector) -> list[dict[str, Any]]:
    """Titulares en orden de documento: ``{tag, position, text, oculto}``.

    Se devuelven TODOS, incluidos los que no se pintan (``hidden``,
    ``display:none`` en linea, clase de utilidad, ``aria-hidden``, dentro de
    ``template``/``noscript``/``svg``), marcados con ``oculto``. Antes se
    descartaban en silencio los de ``template``/``noscript``/``svg`` —mientras
    que los enlaces y las imagenes de esos mismos elementos si se guardaban— y
    en cambio los ocultos por ``hidden`` o ``aria-hidden`` si contaban, que es
    justo al reves: de ahi salian los ``multiple_h1`` falsos.

    ``role="heading"`` con ``aria-level`` tambien cuenta: para quien lee la
    pagina con un lector de pantalla ES un titular, y su nivel es el que
    declara (2 por defecto, segun la especificacion ARIA).
    """
    results: list[dict[str, Any]] = []
    pos = 0
    consulta = (
        "descendant-or-self::*[self::h1 or self::h2 or self::h3 or self::h4"
        " or self::h5 or self::h6 or @role='heading']"
    )
    for node in selector.xpath(consulta):
        raiz = node.root
        if not hasattr(raiz, "tag") or not isinstance(raiz.tag, str):
            continue
        tag_name = raiz.tag.lower()
        if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            etiqueta = tag_name
        else:
            nivel = (raiz.get("aria-level") or "2").strip()
            etiqueta = f"h{nivel}" if nivel in ("1", "2", "3", "4", "5", "6") else "h2"
        results.append({
            "tag": etiqueta,
            "position": pos,
            "text": _texto_de_heading(raiz),
            "oculto": _heading_oculto(raiz),
        })
        pos += 1
    return results





def _etiqueta_de_svg(nodo_lxml) -> str | None:
    """``aria-label`` o ``<title>`` de un svg dentro del elemento."""
    for svg in nodo_lxml.iter("svg"):
        etiqueta = (svg.get("aria-label") or "").strip()
        if etiqueta:
            return etiqueta
        for titulo in svg.iter("title"):
            texto = (titulo.text or "").strip()
            if texto:
                return texto
    return None


def extract_links(
    selector,
    base_url: str,
    allowed_hosts: set[str],
    page_nofollow: bool = False,
) -> list[dict[str, Any]]:
    """
    Extract all ``<a>`` and ``<area>`` links from the page.

    Returns a list of dicts with: url, anchor_text, rel, is_internal,
    link_position, target, alt_text, follow, link_type.

    Every href instance is returned -- links are **not** deduplicated within a
    page. This matches Screaming Frog: a page that links to the same target
    from both the nav and the body has two inlinks (and the target's "Unique
    Inlinks" still counts the source page once). Callers that follow links for
    the crawl frontier are responsible for their own dedup.

    ``page_nofollow`` marks every link nofollow regardless of its own ``rel``,
    for pages whose meta robots / X-Robots-Tag carry ``nofollow``/``none`` —
    the way Google (and Screaming Frog) apply page-level directives.
    """
    results: list[dict[str, Any]] = []
    posiciones: dict = {}

    for a in selector.xpath(
        f"descendant-or-self::a[@href]{_XPATH_FUERA_DEL_DOM}"
        f" | descendant-or-self::area[@href]{_XPATH_FUERA_DEL_DOM}"
    ):
        raw_href = a.attrib.get("href", "").strip()
        # El esquema no distingue mayusculas: `JavaScript:void(0)` entraba en
        # `links` y ADEMAS en `enlaces_no_rastreables`, rompiendo la disyuncion
        # de la decision 106, y `Mailto:`/`TEL:` salian como enlaces externos.
        if not raw_href or raw_href.lower().startswith(
                ("javascript:", "mailto:", "tel:", "data:", "#")):
            continue

        normalized = absolutize_url(base_url, raw_href)
        # Lista blanca, no negra: `sms:`, `whatsapp:`, `intent:`, `skype:`,
        # `file:`, `ftp:`, `ws:`... no son paginas que Google pueda rastrear.
        if normalized is None or not es_url_web(normalized):
            continue

        tag_name = (a.xpath("name()").get() or "a").lower()
        if tag_name == "area":
            # Image-map areas have no text content; alt is their anchor.
            anchor_text = _clean(a.attrib.get("alt", ""))
        else:
            # El texto que se LEE: un `<script>` dentro del enlace acababa en el
            # anchor, y unir los nodos con espacio partia las palabras.
            anchor_text = _clean(" ".join(_lineas_visibles(a.root, forzar_raiz=True)))
            if not anchor_text:
                # Un enlace de icono no es un "anchor vacio" si lleva su texto
                # en un atributo: eso es lo que anuncia un lector de pantalla y
                # lo que Google usa como ancla.
                for alternativa in (
                    a.attrib.get("aria-label"),
                    a.attrib.get("title"),
                    _etiqueta_de_svg(a.root),
                ):
                    anchor_text = _clean(alternativa)
                    if anchor_text:
                        break
        rel = _clean(a.attrib.get("rel", ""))

        # Heuristic link position
        link_position = _detect_link_position(a, posiciones)

        # Target attribute (_blank, _self, _parent, _top, or custom)
        target = _clean(a.attrib.get("target", ""))

        # Alt text from child <img> elements (useful for image links)
        child_imgs = a.css("img")
        alt_text_parts: list[str] = []
        for img in child_imgs:
            alt = img.attrib.get("alt", "")
            alt = alt.strip() if alt else ""
            # El mismo alt dos veces es el patron responsive: el enlace lleva
            # el clon de movil y el de escritorio de la misma imagen (o un
            # `<picture>` con su `<img>` de respaldo). Unirlos daba
            # "voto femenino voto femenino" -- medido en 7 de las 58 paginas
            # de control. Google lee la imagen una vez.
            if alt and alt not in alt_text_parts:
                alt_text_parts.append(alt)
        alt_text = _clean(" ".join(alt_text_parts)) if alt_text_parts else None

        # Follow: True unless rel contains "nofollow" or the page itself is
        # marked nofollow (meta robots / X-Robots-Tag).
        #
        # `ugc` y `sponsored` cuentan igual que `nofollow`: Google documenta que
        # los trata como la misma pista y que NO pasan autoridad, asi que un
        # enlace patrocinado o de contenido de usuario no puede sumar al
        # PageRank del destino. Medido en cuatro censos (28,4 M enlaces): 224
        # con `ugc`, 0 con `sponsored`, y de esos solo 12 internos — es
        # correccion de criterio, no un cambio de volumen.
        follow = not rel_sin_autoridad(rel) and not page_nofollow

        # Link type classification. Se decide ANTES de caer al alt: un
        # `<a><img></a>` es un enlace de imagen aunque su ancla salga del alt.
        has_child_imgs = len(child_imgs) > 0
        has_text = bool(anchor_text)
        if has_child_imgs and not has_text:
            link_type = "image"
        elif has_child_imgs and has_text:
            link_type = "image_text"
        else:
            link_type = "hyperlink"

        # Google: "if the link is an image, the alt text acts as the anchor
        # text". Es el ultimo recurso, despues de aria-label y title, porque
        # esos los escribe alguien para ANUNCIAR el enlace y el alt describe la
        # imagen; cuando no hay ninguno, el alt es lo unico que rotula el
        # enlace y es lo que Google toma como ancla. Medido en 58 paginas de
        # control de tres clientes: de 1.276 enlaces sin ancla, 1.068 la
        # recuperan —titulos de articulo, nombres de producto, el logo que
        # enlaza a la home— y los 208 que siguen vacios son iconos de redes
        # sociales sin alt, sin aria-label y sin title: no los rotula nada.
        if not anchor_text and alt_text:
            anchor_text = alt_text

        results.append({
            "url": normalized,
            "anchor_text": anchor_text,
            "rel": rel,
            "is_internal": is_internal_url(normalized, allowed_hosts),
            "link_position": link_position,
            "target": target,
            "alt_text": alt_text,
            "follow": follow,
            "link_type": link_type,
        })

    return results


# Lo que hace que dos apariciones sean EL MISMO enlace a efectos de cualquier
# analisis: todo lo que se guarda de el. Si difiere la posicion (menu y cuerpo)
# o el ancla, son dos enlaces y siguen siendo dos filas.
_CLAVE_ENLACE = (
    "url", "anchor_text", "rel", "is_internal", "link_position",
    "target", "alt_text", "follow", "link_type",
)


def agrupar_enlaces(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Las apariciones IDENTICAS de un enlace en una pagina, en una fila con
    ``repeticiones``.

    Un megamenu sale dos o tres veces por pagina (escritorio, movil, el
    desplegable), y guardar cada aparicion era la mayor parte de la base de
    datos: en Druni, 139 M filas en `links` para 60.399 URLs, el 97% de menu.
    Agrupadas quedan 42 M, y la tabla del censo pasa de ~30 GB a 9 GB.

    No se pierde nada: ``repeticiones`` conserva cuantas veces aparecia, asi
    que los conteos de Screaming Frog (inlinks por instancia, inlinks unicos)
    salen iguales sumandolo. El orden es el de la primera aparicion.
    """
    grupos: dict[tuple, dict[str, Any]] = {}
    for link in links:
        clave = tuple(link.get(k) for k in _CLAVE_ENLACE)
        if clave in grupos:
            grupos[clave]["repeticiones"] += 1
        else:
            grupos[clave] = {**link, "repeticiones": 1}
    return list(grupos.values())


# Elementos que llevan `href` y NO son enlaces de una pagina HTML: el `<link>`
# del head, `<base>` y lo de SVG/MathML (`<use href>`, `<image href>`, que son
# referencias a recursos). Lo que queda —`<span href>`, `<div href>`, `<button
# href>`— es el ejemplo de Google de un enlace que no se puede seguir.
_XPATH_HREF_FUERA_DE_A = (
    "descendant-or-self::*[@href][not(self::a) and not(self::area) and not(self::link)"
    " and not(self::base) and not(self::svg) and not(ancestor::svg) and not(self::math)"
    " and not(ancestor::math)]" + "[not(ancestor::template) and not(ancestor::noscript)]"
    # Un componente web (`<ion-item href>`, `<sl-button href>`) suele pintar un
    # `<a href>` de verdad en su shadow DOM, que Google aplana al renderizar:
    # sin render no se sabe, y no se afirma. Su nombre lleva guion por norma.
    "[not(contains(name(), '-'))]"
)


def extract_enlaces_no_rastreables(
    selector,
    base_url: str,
    allowed_hosts: set[str],
) -> list[dict[str, Any]]:
    """Los enlaces que una persona puede pulsar y Google no puede seguir.

    Disjunto de :func:`extract_links` por construccion: alli solo entra el
    ``<a>``/``<area>`` con un ``href`` que resuelve a una URL; aqui lo que esa
    funcion descarta y Google documenta como no rastreable (ver
    ``shared/enlaces_no_rastreables.py`` para la taxonomia y lo que NO cuenta).
    Por eso nada de esto entra en el grafo, en los entrantes, en el PageRank ni
    en la frontera del rastreo: ni siquiera pasa por la lista que los alimenta.

    Cada dict lleva ``crawlability``, ``etiqueta``, ``href`` (el atributo tal
    cual, o el ``onclick``, recortado), ``destino`` (URL absoluta si se ve a
    donde iria, si no None), ``destino_interno`` (None sin destino),
    ``anchor_text``, ``link_position`` y ``repeticiones`` (agrupados como en la
    decision 94: el megamenu repetido es una fila).
    """
    from shared import enlaces_no_rastreables as enr

    filas: list[dict[str, Any]] = []
    posiciones: dict = {}

    def _ancla(nodo) -> str | None:
        texto = _clean(" ".join(_lineas_visibles(nodo.root, forzar_raiz=True)))
        if not texto:
            texto = _clean(nodo.attrib.get("aria-label") or nodo.attrib.get("title"))
        return texto[: enr.MAX_ANCLA] if texto else None

    def _anadir(nodo, tipo: str, crudo: str, destino_crudo: str | None) -> None:
        destino = absolutize_url(base_url, destino_crudo) if destino_crudo else None
        if destino and urlparse(destino).scheme not in ("http", "https"):
            destino = None
        filas.append({
            "crawlability": tipo,
            "etiqueta": (nodo.xpath("name()").get() or "").lower()[:20] or None,
            "href": (crudo or "")[: enr.MAX_HREF] or None,
            "destino": destino,
            "destino_interno": is_internal_url(destino, allowed_hosts) if destino else None,
            # Para saber despues si el destino esta en el censo, sin unir con
            # `links`: el mismo hash que `urls.url_hash`.
            "destino_hash": compute_url_hash(destino) if destino else None,
            "anchor_text": _ancla(nodo),
            "link_position": _detect_link_position(nodo, posiciones),
        })

    # 1. `<a>` sin un href rastreable.
    for a in selector.xpath(f"descendant-or-self::a{_XPATH_FUERA_DEL_DOM}"):
        attrs = {k.lower(): v for k, v in a.attrib.items()}
        href = attrs.get("href")
        href_limpio = (href or "").strip()
        onclick = (attrs.get("onclick") or "").strip()
        if href is not None and href_limpio.lower().startswith("javascript:"):
            codigo = href_limpio[len("javascript:"):]
            _anadir(a, enr.JAVASCRIPT_HREF, href_limpio,
                    enr.destino_en_script(codigo) or enr.destino_en_script(onclick))
        elif href is not None and enr.es_fragmento_como_ruta(href_limpio):
            _anadir(a, enr.FRAGMENTO_COMO_RUTA, href_limpio, None)
        elif href is None or href_limpio in ("", "#", "#!"):
            ruta = next((attrs[k].strip() for k in enr.ATRIBUTOS_DE_RUTA
                         if (attrs.get(k) or "").strip()), None)
            if ruta:
                _anadir(a, enr.A_SIN_HREF, ruta, ruta)
            elif onclick and enr.menciona_ruta(onclick):
                # Un `<a>` tiene forma de enlace: si su onclick lleva una ruta
                # se guarda, pero el destino solo si se ve la navegacion
                # entera. `getDetalleNoticia('/press/noticias/', 'slug')` va a
                # una noticia que no se puede saber sin ejecutarlo.
                _anadir(a, enr.ONCLICK_SIN_HREF, onclick, enr.destino_en_script(onclick))
            # `href="#"`/`""`/`"#!"` sin manejador, un `onclick` sin ruta
            # («Leer mas», un `scrollTo`) o un `<a name>`: un boton o un ancla.
        if len(filas) >= enr.MAX_POR_PAGINA:
            return agrupar_enlaces_no_rastreables(filas)

    # 2. Otra etiqueta con `href`: el `<span href>` del ejemplo de Google.
    for nodo in selector.xpath(_XPATH_HREF_FUERA_DE_A):
        crudo = (nodo.attrib.get("href") or "").strip()
        if not crudo or crudo.startswith("#") or crudo.lower().startswith(
                ("mailto:", "tel:", "data:", "javascript:")):
            continue
        _anadir(nodo, enr.HREF_FUERA_DE_A, crudo, crudo)
        if len(filas) >= enr.MAX_POR_PAGINA:
            return agrupar_enlaces_no_rastreables(filas)

    # 3. Otra etiqueta con un `onclick` que NAVEGA a una URL reconocible: la
    # tarjeta entera clicable. Sin navegacion es un boton y no se guarda, lleve
    # o no una ruta: `addToCart('/cart/add')`, `this.src='/b.png'` (d106).
    for nodo in selector.xpath(
            f"descendant-or-self::*[@onclick][not(self::a)]{_XPATH_FUERA_DEL_DOM}"):
        onclick = (nodo.attrib.get("onclick") or "").strip()
        destino = enr.destino_en_script(onclick)
        if not destino:
            continue
        # Si la tarjeta ya contiene un `<a href>` al mismo destino, Google
        # tiene el enlace: el onclick es comodidad para la persona.
        dentro = {absolutize_url(base_url, h) for h in nodo.xpath(".//a/@href").getall()}
        if absolutize_url(base_url, destino) in dentro:
            continue
        _anadir(nodo, enr.ONCLICK_EN_OTRA_ETIQUETA, onclick, destino)
        if len(filas) >= enr.MAX_POR_PAGINA:
            break

    return agrupar_enlaces_no_rastreables(filas)


_CLAVE_NO_RASTREABLE = (
    "crawlability", "etiqueta", "href", "destino", "anchor_text", "link_position",
)


def agrupar_enlaces_no_rastreables(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Como :func:`agrupar_enlaces`: lo identico en una fila con ``repeticiones``."""
    grupos: dict[tuple, dict[str, Any]] = {}
    for f in filas:
        clave = tuple(f.get(k) for k in _CLAVE_NO_RASTREABLE)
        if clave in grupos:
            grupos[clave]["repeticiones"] += 1
        else:
            grupos[clave] = {**f, "repeticiones": 1}
    return list(grupos.values())


# Class/id tokens that reveal a template region. Matched as WHOLE tokens
# (split on non-alphanumerics), never as substrings: "canvas" must not read
# as nav, "elementor-widget" must not read as sidebar.
_POSITION_TOKENS: dict[str, frozenset[str]] = {
    # `menu` entra porque `<div class="main-menu">`, `primary-menu`,
    # `mobile-menu` y `menu-item` son el patron mas comun de menu en HTML y
    # salian como CONTENIDO: un menu de cientos de enlaces contado como
    # editorial es justo lo que hacia saltar `high_outlink_count` en todas las
    # paginas del sitio. El riesgo conocido es el `class="menu"` de una carta de
    # restaurante; se asume, porque equivocarse al otro lado afecta a todas las
    # paginas y este caso a una.
    "nav": frozenset({
        "nav", "navbar", "navigation", "menubar", "menu", "megamenu",
        "submenu", "topnav", "mainnav", "sitenav",
        "breadcrumb", "breadcrumbs",
    }),
    "footer": frozenset({"footer", "colophon"}),
    "header": frozenset({"header", "masthead", "topbar"}),
    "sidebar": frozenset({"sidebar", "aside"}),
}

# Ids —no clases— que en los temas de WordPress son la barra lateral. Como id
# es inequivoco (`#secondary` es el aside en los temas Twenty*); como CLASE
# seria un desastre, porque `btn-secondary` de Bootstrap marcaria de barra
# lateral los enlaces de cualquier boton.
_SIDEBAR_IDS: frozenset[str] = frozenset({"secondary", "sidebar", "widgets"})
# WAI-ARIA landmark roles map 1:1 to regions.
_POSITION_ROLES: dict[str, str] = {
    "navigation": "nav", "menubar": "nav", "menu": "nav",
    "banner": "header",
    "contentinfo": "footer",
    "complementary": "sidebar",
    "main": "content",
}
# Content markers, matched on whole *whitespace-separated* class tokens so
# that utility classes such as Bootstrap's ``justify-content-between`` or
# Liferay's ``portlet-content`` never count.
#   strong = the article/entry itself. A <header>/<footer> found inside one
#            is the article's own (entry-header, card-footer), not the page's.
#   weak   = site-level wrappers around the main region (#content,
#            .site-content). Enough to stop the walk, not enough to demote
#            a header/footer found inside them.
_CONTENT_STRONG: frozenset[str] = frozenset({
    "entry-content", "post-content", "post-body", "article-body", "article-content",
    "entry", "article", "td-post-content", "elementor-widget-theme-post-content",
})
_CONTENT_WEAK: frozenset[str] = frozenset({
    "content", "main", "main-content", "site-content", "site-main", "page-content",
    "content-area", "layout-content", "primary",
})
_CONTENT_IDS: frozenset[str] = frozenset({"content", "main", "primary", "main-content", "page-content"})
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
# camelCase boundary: CSS-in-JS class names glue words together
# ("NoJsNavigation-styles__NoJsListItemStyled").
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TEMPLATE_KINDS = ("nav", "header", "footer", "sidebar")


def _hint_tokens(hint: str) -> set[str]:
    """Whole lowercase tokens of a class/id string, split on non-alphanumerics
    and on camelCase boundaries."""
    return set(_TOKEN_SPLIT.split(_CAMEL_SPLIT.sub(" ", hint).lower())) - {""}


def _nombre_de(el) -> str:
    """Lo que devuelve el XPath ``name()`` de un elemento lxml, en minusculas."""
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag.startswith("{"):
        local = tag.split("}", 1)[1]
        tag = f"{el.prefix}:{local}" if el.prefix else local
    return tag.lower()


def _classify_node(node) -> tuple[str | None, bool]:
    """
    Classify one ancestor element (lxml, or a parsel Selector).

    Returns ``(kind, structural)`` where ``kind`` is a link position
    (``nav``/``header``/``footer``/``sidebar``/``content``), ``"content+"``
    for a *strong* content marker, or ``None`` when the element says
    nothing. ``structural`` is True when the verdict comes from the tag
    name or an ARIA landmark role rather than from class/id hints.
    """
    node = getattr(node, "root", node)
    tag = _nombre_de(node)
    if tag in ("nav", "header", "footer"):
        return tag, True
    if tag == "aside":
        return "sidebar", True
    if tag in ("main", "article"):
        return "content+", True

    role = (node.get("role", "") or "").strip().lower()
    if role in _POSITION_ROLES:
        kind = _POSITION_ROLES[role]
        return ("content+" if kind == "content" else kind), True

    raw_cls = node.get("class", "") or ""
    raw_id = (node.get("id", "") or "").strip()
    cls, node_id = raw_cls.lower(), raw_id.lower()
    tokens = _hint_tokens(raw_cls + " " + raw_id)
    for kind, words in _POSITION_TOKENS.items():
        if tokens & words:
            return kind, False

    if node_id in _SIDEBAR_IDS:
        return "sidebar", False

    class_tokens = set(cls.split())
    if class_tokens & _CONTENT_STRONG:
        return "content+", False
    if class_tokens & _CONTENT_WEAK or node_id in _CONTENT_IDS:
        return "content", False
    return None, False


def _detect_link_position(a_selector, cache: dict | None = None) -> str:
    """
    Heuristic: classify a link by its nearest meaningful ancestor.

    Returns one of ``nav``, ``header``, ``footer``, ``sidebar``, ``content``.

    The ancestor axis is walked **nearest-first** (it comes back
    outermost-first, so it is reversed; otherwise a top-level wrapper such
    as ``<div class="site-header">`` would tag every link on the page as
    ``header``). ``<html>`` and ``<body>`` never count: themes hang layout
    flags on ``<body>`` (``ast-header-sticky``, ``has-sidebar``) that would
    swallow the whole page.

    Per ancestor, see :func:`_classify_node`; then:

    * ``nav`` / ``sidebar`` win immediately.
    * ``header`` / ``footer`` win unless a *strong* content marker (the
      article itself) lies further out before any template region: then
      it is the article's own header/footer and the walk continues.
    * a content marker stops the walk with ``content`` unless a structural
      template landmark (``<nav>``, ``<header>``, ``<footer>``, ``<aside>``
      or an ARIA landmark) lies further out: mega menus built with a
      misplaced ``<main>`` inside ``<nav>`` are still navigation.

    Falls back to ``content``.

    *cache* (un dict por pagina, que pasa quien recorre todos sus enlaces)
    guarda la clasificacion de cada ancestro: el menu y el pie son ancestros de
    cientos de enlaces y antes se clasificaban, con un XPath por nodo, una vez
    por enlace (#284). Los ancestros se recorren en lxml, del mas cercano al
    mas lejano.
    """
    kinds = []  # nearest-first
    for n in getattr(a_selector, "root", a_selector).iterancestors():
        if cache is not None and n in cache:
            kinds.append(cache[n])
            continue
        if _nombre_de(n) in ("html", "body"):
            continue
        kind = _classify_node(n)
        if cache is not None:
            cache[n] = kind
        kinds.append(kind)

    for i, (kind, _structural) in enumerate(kinds):
        if kind is None:
            continue
        outer = kinds[i + 1:]
        if kind == "nav":
            return kind
        if kind == "sidebar":
            # Un `<aside>` DENTRO de article/main es el aside de esa seccion —un
            # destacado, una nota al margen, las especificaciones de un
            # producto—, no la barra lateral del sitio: sus enlaces son
            # editoriales. Es la misma regla de landmark que ya se aplica a
            # header/footer aqui abajo y al extraer el contenido (decision 45).
            article_scoped = False
            for okind, _ in outer:
                if okind == "content+":
                    article_scoped = True
                    break
                if okind in _TEMPLATE_KINDS:
                    break
            if article_scoped:
                continue
            return kind
        if kind in ("header", "footer"):
            article_scoped = False
            for okind, _ in outer:
                if okind == "content+":
                    article_scoped = True
                    break
                if okind in _TEMPLATE_KINDS:
                    break
            if article_scoped:
                continue  # the article's own header/footer: keep walking
            return kind
        # content / content+
        if any(ok in _TEMPLATE_KINDS and st for ok, st in outer):
            continue  # content-looking block nested inside a real landmark
        return "content"

    return "content"


def extract_hreflang(selector, base_url: str | None = None) -> list[dict[str, Any]]:
    """Extract ``<link rel="alternate" hreflang="...">`` tags.

    When *base_url* is given, hreflang hrefs are resolved to absolute URLs
    so reciprocal (return-tag) validation can match them reliably.
    """
    results: list[dict[str, Any]] = []
    # `rel` es una lista de tokens y sin caja (decision 56): con el selector
    # exacto, `rel="Alternate"` o `rel="alternate nofollow"` daban cero hreflang.
    for link in selector.css("link[hreflang]"):
        if "alternate" not in rel_tokens(link.attrib.get("rel")):
            continue
        lang = _clean(link.attrib.get("hreflang", ""))
        href = _resolve(base_url, _clean(link.attrib.get("href", "")))
        if lang and href:
            results.append({
                "lang": lang,
                "href": href,
                # Google solo lee el hreflang del `<head>` (o de la cabecera
                # HTTP o el sitemap): uno en el `<body>` lo ignora, igual que el
                # canonical (decision 56). Se guarda marcado, no se tira, para
                # que el analyzer pueda decir por que ese idioma no esta
                # anotado aunque la plantilla lo escriba.
                "en_body": not link.xpath("ancestor::head"),
                "origen": ORIGEN_HREFLANG_HTML,
            })
    return deduplicar_hreflang(results)


# De donde sale una anotacion hreflang (`hreflang.origen`). Google lee tres:
# el `<head>`, la cabecera HTTP `Link` (la unica en un PDF) y el sitemap, que
# aun no se guarda (#107). NULL en BD = rastreo anterior: era del HTML.
ORIGEN_HREFLANG_HTML = "html"
ORIGEN_HREFLANG_CABECERA = "cabecera"


def clave_hreflang(lang: str, href: str) -> tuple:
    """Lo que hace que dos anotaciones sean LA MISMA: el codigo sin caja y el
    destino con la clave de `misma_url` (decision 39), que ignora el fragmento,
    el puerto por defecto y el escapado."""
    try:
        destino: Any = clave_url(href)
    except ValueError:
        destino = href.strip()
    return (lang.casefold(), destino)


def deduplicar_hreflang(entradas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Una fila por (idioma, destino), en el orden de la primera aparicion.

    Un plugin y la plantilla escribiendo el mismo `en-GB` daban dos filas y
    DOS `hreflang_broken_target` identicos para una sola anotacion. Gana la
    primera, salvo que fuera del `<body>` y una posterior no: Google lee la del
    head (decision 56), y esa es la que cuenta. El `lang` se guarda como venia.
    """
    salida: list[dict[str, Any]] = []
    posicion: dict[tuple, int] = {}
    for entrada in entradas:
        clave = clave_hreflang(entrada["lang"], entrada["href"])
        i = posicion.get(clave)
        if i is None:
            posicion[clave] = len(salida)
            salida.append(entrada)
        elif salida[i].get("en_body") and not entrada.get("en_body"):
            salida[i] = entrada
    return salida


# El CDATA es sintaxis de XHTML y hay plantillas (Drupal, portales antiguos)
# que envuelven el JSON-LD en el. extruct no lo parsea y el bloque entero
# desaparecia sin aviso: cero datos estructurados en una pagina que si los
# lleva.
_RE_CDATA = re.compile(
    r"(<script[^>]*type\s*=\s*[\"\']application/ld\+json[\"\'][^>]*>)(.*?)(</script>)",
    re.S | re.I,
)
_RE_CDATA_MARCAS = re.compile(r"\s*(?://\s*)?(?:<!\[CDATA\[|\]\]>)\s*")


def _limpiar_cdata(html_body: str) -> str:
    """Quita los envoltorios CDATA de los bloques `application/ld+json`."""
    if "CDATA" not in html_body:
        return html_body

    def sustituir(m):
        return m.group(1) + _RE_CDATA_MARCAS.sub("", m.group(2)) + m.group(3)

    return _RE_CDATA.sub(sustituir, html_body)


def _tipo_normalizado(valor) -> str | None:
    """`@type` como nombre corto: `https://schema.org/Product` -> `Product`.

    RDFa guarda el tipo como IRI completo y JSON-LD y microdatos como nombre
    corto, asi que el mismo tipo salia con dos nombres distintos y ni los
    filtros ni la validacion por tipo casaban.
    """
    if valor is None:
        return None
    if isinstance(valor, list):
        partes = [_tipo_normalizado(v) for v in valor]
        limpias = [p for p in partes if p]
        return ", ".join(limpias) if limpias else None
    texto = str(valor).strip()
    if not texto:
        return None
    if "://" in texto or texto.startswith("schema.org"):
        texto = texto.rstrip("/").rsplit("/", 1)[-1]
    return texto.lstrip("@") or None


def _es_solo_referencia(item: dict) -> bool:
    """Un nodo que solo apunta a otro (`{"@id": ...}`) no es una entidad.

    En el `@graph` de Yoast estan por todas partes. Antes salian como un bloque
    "sin @type", que es un error inventado.
    """
    claves = {k for k in item.keys() if k not in ("@context",)}
    return bool(claves) and claves <= {"@id", "id"}


# Lo que el lector permisivo de extruct quita antes de reintentar: comentarios
# de linea de HTML o JS al principio. Es SU regla, copiada para leer el bloque
# igual que lo leia extruct cuando el JSON estricto falla.
_RE_COMENTARIO_INICIAL = re.compile(r"^\s*(//.*|<!--.*-->)", re.M)


def _motivo_json_invalido(texto: str, exc: json.JSONDecodeError) -> str:
    """Por que no es JSON, en las palabras con que se arregla."""
    resto = texto[exc.pos:exc.pos + 2]
    donde = f"linea {exc.lineno}, columna {exc.colno}"
    if resto.startswith(("//", "/*")) or texto.lstrip().startswith(("//", "/*", "<!--")):
        return f"comentario dentro del JSON ({donde})"
    if resto[:1] in ("}", "]"):
        return f"coma final antes de '{resto[:1]}' ({donde})"
    return f"{exc.msg} ({donde})"


def _leer_json_ld(texto: str) -> tuple[Any, str | None]:
    """Lee un bloque JSON-LD y dice si Google lo podra leer.

    Devuelve ``(datos, motivo)``: ``motivo`` es None si el bloque es JSON de
    verdad y, si no lo es, por que. Google parsea el JSON-LD como JSON
    estricto: una coma final o un comentario hacen que descarte el bloque
    ENTERO, y la pagina se queda sin resultado enriquecido con el marcado
    aparentemente en su sitio. extruct en cambio reintenta con `jstyleson`,
    que perdona las dos cosas, asi que esos bloques se guardaban y validaban
    como buenos: el informe decia «ok» justo donde Google no ve nada (#28).

    Se sigue leyendo con el lector permisivo —como hacia extruct— porque el
    contenido sirve para auditar el resto del bloque y para saber de que tipo
    era; lo que cambia es que el motivo viaja con el y la validacion lo
    convierte en un error. Si ni el permisivo puede, ``datos`` es None.

    ``strict=False`` (caracteres de control dentro de una cadena) se mantiene
    como en extruct: no esta comprobado que Google los rechace y esta tabla no
    se inventa hallazgos.
    """
    try:
        return json.loads(texto, strict=False), None
    except json.JSONDecodeError as exc:
        motivo = _motivo_json_invalido(texto, exc)
    try:
        import jstyleson

        return jstyleson.loads(_RE_COMENTARIO_INICIAL.sub("", texto), strict=False), motivo
    except Exception:
        return None, motivo


def _bloques_json_ld(html_body: str) -> list[tuple[Any, str | None]]:
    """``(datos, motivo)`` por cada `<script type="application/ld+json">`.

    Se leen aqui y no con extruct por dos razones: extruct no dice que bloque
    necesito el lector permisivo, y cuando un bloque no se puede leer ni asi
    lanza una excepcion que se llevaba por delante TODOS los datos
    estructurados de la pagina, microdatos y RDFa incluidos. El selector es el
    mismo que usa extruct, para que cuenten los mismos bloques.
    """
    from extruct.utils import parse_html

    try:
        arbol = parse_html(html_body, encoding="UTF-8")
    except Exception:
        return []
    bloques: list[tuple[Any, str | None]] = []
    for nodo in arbol.xpath('descendant-or-self::script[@type="application/ld+json"]'):
        texto = nodo.xpath("string()")
        # Un <script> vacio no declara nada: no es un bloque roto.
        if not texto or not texto.strip():
            continue
        bloques.append(_leer_json_ld(texto))
    return bloques


_HAY_MICRODATA = re.compile(r"itemscope", re.I)
_HAY_RDFA = re.compile(r"\b(?:typeof|vocab)\s*=|rdf:type|22-rdf-syntax-ns#type", re.I)


def extract_structured_data(html_body: str, url: str = "") -> list[dict[str, Any]]:
    """
    Extract JSON-LD, Microdata, and RDFa using *extruct*.

    Returns a list of dicts with: raw, format, schema_type, json_invalido.

    Un `@graph` se abre en una entidad por nodo. Antes el bloque entero daba una
    sola fila con `schema_type` NULL, y el `@graph` es exactamente lo que emite
    Yoast: en casi todo WordPress no habia forma de filtrar ni de informar por
    tipo, ni de validar cada entidad por separado.

    ``json_invalido`` es el motivo por el que un bloque JSON-LD no es JSON
    estricto (coma final, comentario), que es lo que Google descarta; None si
    es valido o si el bloque no es JSON-LD. Un bloque que no se puede leer de
    ninguna manera sale igual, con ``raw`` None: Google tampoco lo lee, y que
    desapareciera del informe era callarse justo ese hallazgo.
    """
    results: list[dict[str, Any]] = []
    html_body = _limpiar_cdata(html_body)

    data: dict[str, Any] = {"json-ld": []}
    for datos, motivo in _bloques_json_ld(html_body):
        if datos is None:
            results.append({"raw": None, "format": "jsonld", "schema_type": None,
                            "json_invalido": motivo})
            continue
        for item in (datos if isinstance(datos, list) else [datos]):
            if item:
                data["json-ld"].append((item, motivo))

    # extruct parsea la pagina entera por cada sintaxis aunque no haya nada que
    # leer, y era una de las partes mas caras de `parse` (#284). Microdata sin
    # `itemscope` no da ningun item; RDFa sin `typeof`/`vocab` (ni el tipo de
    # RDF escrito entero) solo da items sin @type, que se descartan aqui abajo.
    #
    # Una llamada por sintaxis: con las dos juntas, una excepcion de pyRdfa
    # (basta un `href="http://[x/"` en la pagina) tiraba tambien la microdata.
    sintaxis = [s for s, patron in (("microdata", _HAY_MICRODATA), ("rdfa", _HAY_RDFA))
                if patron.search(html_body)]
    for una in sintaxis:
        try:
            import extruct

            otros = extruct.extract(html_body, base_url=url, syntaxes=[una], uniform=True)
        except Exception:
            continue
        for fmt, items in otros.items():
            if isinstance(items, list):
                data[fmt] = [(item, None) for item in items]

    for fmt, items in data.items():
        formato = fmt.replace("-", "")  # jsonld, microdata, rdfa
        for item, motivo in items:
            if not isinstance(item, dict):
                results.append({"raw": item, "format": formato, "schema_type": None,
                                "json_invalido": motivo})
                continue
            grafo = item.get("@graph")
            nodos = (
                [g for g in grafo if isinstance(g, dict)]
                if isinstance(grafo, list) and grafo
                else [item]
            )
            for nodo in nodos:
                if _es_solo_referencia(nodo):
                    continue
                schema_type = _tipo_normalizado(nodo.get("@type"))
                # RDFa items without a schema type are xhtml/ARIA role
                # annotations (tabs, dialogs, buttons) — noise on any site.
                if formato == "rdfa" and not schema_type:
                    continue
                results.append({
                    "raw": nodo,
                    "format": formato,
                    "schema_type": schema_type,
                    "json_invalido": motivo,
                })

    return results


# Atributos donde los cargadores diferidos esconden la imagen DE VERDAD mientras
# `src` lleva un placeholder `data:`. Sin mirarlos, lo que se auditaba era el
# placeholder y la imagen real no aparecia en ningun informe.
_ATRIBUTOS_DIFERIDOS = (
    "data-src", "data-original", "data-lazy-src", "data-lazy",
    "data-echo", "data-url", "data-img",
)
_ATRIBUTOS_DIFERIDOS_SRCSET = ("data-srcset", "data-lazy-srcset")

# `url(...)` dentro de un atributo style: tambien es una peticion del navegador
# y tambien puede ser contenido mixto.
_RE_URL_CSS = re.compile(r"url\(\s*[\'\"]?([^\)\'\"]+)[\'\"]?\s*\)", re.I)


def parse_srcset(srcset: str) -> list[str]:
    """URLs de un `srcset`, respetando las comas de un `data:` URI.

    `srcset.split(",")[0]` partia un `data:image/gif;base64,R0lG...` justo en su
    coma interna y guardaba `data:image/gif;base64` como si fuera una imagen.
    """
    if not srcset or not srcset.strip():
        return []
    piezas = [p.strip() for p in srcset.split(",")]
    candidatos: list[str] = []
    pendiente = ""
    for pieza in piezas:
        trozo = (pendiente + "," + pieza) if pendiente else pieza
        pendiente = ""
        if not trozo:
            continue
        # Un data: URI sin su carga util: la coma que lo parte es suya.
        primero = trozo.split()[0] if trozo.split() else ""
        if primero.lower().startswith("data:") and "," not in primero:
            pendiente = trozo
            continue
        if primero:
            candidatos.append(primero)
    if pendiente:
        trozos = pendiente.split()
        if trozos:
            candidatos.append(trozos[0])
    return candidatos


def _alt_de_la_imagen_hermana(nodo) -> str | None:
    """Alt del `<img>` del `<picture>` al que pertenece un `<source>`.

    Un `<source>` no lleva alt nunca —no existe el atributo— asi que cada
    `<picture>` con imagen responsive generaba un `image_missing_alt` falso. El
    alt que cuenta es el del `<img>`, que es el que Google y los lectores de
    pantalla leen.
    """
    padre = nodo.getparent()
    while padre is not None and isinstance(padre.tag, str):
        if padre.tag in ("picture", "figure"):
            for img in padre.iter("img"):
                return img.get("alt")
            return None
        if padre.tag in ("body", "html"):
            break
        padre = padre.getparent()
    return None


def extract_resources(selector, base_url: str) -> list[dict[str, Any]]:
    """
    Collect references to images, scripts, stylesheets, and other assets.

    Returns a list of dicts with: url, resource_type, alt_text, width,
    height, is_mixed_content.
    """
    is_https = urlparse(base_url).scheme == "https"
    results: list[dict[str, Any]] = []
    por_url: dict[str, dict[str, Any]] = {}

    def _add(
        raw_url: str,
        rtype: str,
        alt: str | None = None,
        width: str | None = None,
        height: str | None = None,
    ):
        if not raw_url:
            return
        limpio = raw_url.strip()
        # Un `data:` URI no es un recurso que se pueda auditar: va dentro del
        # HTML, no se pide, y no tiene nada que mirar.
        if limpio[:5].lower() == "data:":
            return
        try:
            absolute = urljoin(base_url, limpio)
            normalized = normalize_url(absolute)
        except ValueError:
            return

        # OJO: alt="" y alt ausente NO son lo mismo. Un alt vacio marca la
        # imagen como decorativa, que es lo CORRECTO segun WCAG para iconos y
        # adornos; que falte el atributo si es un fallo. _clean("") devuelve
        # None y colapsaba ambos casos, de modo que el analyzer reportaba como
        # "sin alt" miles de imagenes correctamente marcadas como decorativas.
        # Se conserva la cadena vacia tal cual para poder distinguirlos.
        alt_text = "" if (alt is not None and not alt.strip()) else _clean(alt)

        anterior = por_url.get(normalized)
        if anterior is not None:
            # La misma imagen aparece dos veces y solo una lleva alt: la que
            # vale es la que lo lleva. Quedarse con la primera inventaba un
            # "sin alt" sobre una imagen que si lo tiene en otro sitio.
            if anterior.get("alt_text") is None and alt_text is not None:
                anterior["alt_text"] = alt_text
            if anterior.get("width") is None:
                anterior["width"] = _parse_int(width)
            if anterior.get("height") is None:
                anterior["height"] = _parse_int(height)
            return

        fila = {
            "url": normalized,
            "resource_type": rtype,
            "alt_text": alt_text,
            "width": _parse_int(width),
            "height": _parse_int(height),
            # En mayusculas (`HTTP://`) no casaba: se compara el esquema ya
            # parseado, no el prefijo de la cadena.
            "is_mixed_content": is_https and urlparse(absolute).scheme == "http",
        }
        por_url[normalized] = fila
        results.append(fila)

    def _url_de_imagen(nodo) -> str:
        """`src`, y si es un placeholder `data:`, el atributo diferido."""
        src = (nodo.get("src") or "").strip()
        if src and src[:5].lower() != "data:":
            return src
        for atributo in _ATRIBUTOS_DIFERIDOS:
            valor = (nodo.get(atributo) or "").strip()
            if valor:
                return valor
        for atributo in _ATRIBUTOS_DIFERIDOS_SRCSET:
            candidatos = parse_srcset(nodo.get(atributo) or "")
            if candidatos:
                return candidatos[0]
        return src

    # Images
    for img in selector.xpath(
        f"descendant-or-self::img[@src or @data-src or @data-original"
        f" or @data-lazy-src or @data-srcset]{_XPATH_FUERA_DEL_DOM}"
    ):
        nodo = img.root
        _add(
            _url_de_imagen(nodo),
            "image",
            nodo.get("alt"),
            nodo.get("width"),
            nodo.get("height"),
        )

    # Stylesheets
    for link in selector.css('link[rel="stylesheet"][href]'):
        _add(link.attrib.get("href", ""), "css")

    # Scripts
    for script in selector.css("script[src]"):
        _add(script.attrib.get("src", ""), "js")

    # Otros recursos que el navegador pide y que tambien pueden ser contenido
    # mixto: video, audio, iconos, precargas y los `url()` de un style en linea.
    for medio in selector.css("video[src], audio[src], video source[src], audio source[src]"):
        _add(medio.attrib.get("src", ""), "media")
    for link in selector.css("link[rel][href]"):
        rel = (link.attrib.get("rel") or "").lower()
        if any(t in rel for t in ("preload", "icon", "apple-touch-icon", "manifest")):
            _add(link.attrib.get("href", ""), "other")
    for nodo in selector.css("[style]"):
        for encontrada in _RE_URL_CSS.findall(nodo.attrib.get("style") or ""):
            _add(encontrada, "image")

    # srcset images (first URL only per element for simplicity)
    for img in selector.xpath(f"descendant-or-self::*[@srcset]{_XPATH_FUERA_DEL_DOM}"):
        nodo = img.root
        # El primero que no sea un `data:` (los placeholders de la carga
        # diferida van ahi, y el que hay que auditar es el siguiente).
        candidatos = [c for c in parse_srcset(nodo.get("srcset") or "")
                      if c[:5].lower() != "data:"]
        if not candidatos:
            continue
        # Un `<source>` no tiene alt: el que cuenta es el del `<img>` del
        # `<picture>`. Sin esto, cada imagen responsive generaba un
        # `image_missing_alt` falso.
        alt = nodo.get("alt")
        if alt is None and nodo.tag == "source":
            alt = _alt_de_la_imagen_hermana(nodo)
        _add(candidatos[0], "image", alt, nodo.get("width"), nodo.get("height"))

    return results


# ---------------------------------------------------------------------------
# Videos (decision 155)
# ---------------------------------------------------------------------------

_RE_YOUTUBE = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:embed|v|shorts|live)/|youtu\.be/)([A-Za-z0-9_-]{6,20})",
    re.I)
_RE_VIMEO = re.compile(r"player\.vimeo\.com/video/(\d+)", re.I)
# Una pagina con mas videos que esto es un listado o un reproductor de
# miniaturas; para auditar un sitemap de video basta con saber que los hay.
MAX_VIDEOS = 20


def video_de_url(src: str | None) -> tuple[str, str] | None:
    """``("youtube", id)`` o ``("vimeo", id)`` si *src* es un reproductor
    incrustado de uno de los dos; None si no."""
    if not src:
        return None
    m = _RE_YOUTUBE.search(src)
    if m:
        return "youtube", m.group(1)
    m = _RE_VIMEO.search(src)
    if m:
        return "vimeo", m.group(1)
    return None


def extract_videos(selector, base_url: str | None = None) -> list[dict[str, Any]]:
    """Los videos de la pagina: ``{tipo, id, src, decorativo}``.

    - ``tipo``: ``video`` (un ``<video>`` propio), ``youtube`` o ``vimeo``
      (un ``<iframe>``, ``<embed>`` u ``<object>`` del reproductor, tambien con
      ``data-src`` de carga diferida, y los ``<lite-youtube>``/``<lite-vimeo>``).
    - ``id``: el del proveedor; None en un ``<video>``.
    - ``src``: la URL del video o del reproductor, resuelta. Un ``<video>`` sin
      ``src`` (los reproductores por MSE) cuenta igual, con ``src`` None.
    - ``decorativo``: un ``<video>`` con ``autoplay`` y ``muted`` y sin
      ``controls`` es el fondo animado de una cabecera, no un video que Google
      vaya a indexar como tal (no hay forma de verlo ni de pararlo).

    El ``<noscript>`` SI cuenta: la carga diferida de los plugins deja ahi el
    unico ``<iframe>`` que se ve sin JavaScript (el mismo video dos veces es
    uno). Lo de ``<template>`` no, porque no esta en el DOM. Sin repetidos y
    como mucho ``MAX_VIDEOS``.
    """
    videos: list[dict[str, Any]] = []
    vistos: set[tuple] = set()

    def _anadir(tipo: str, vid: str | None, src: str | None, decorativo: bool = False) -> None:
        clave = (tipo, vid or src or f"#{len(videos)}")
        if clave in vistos or len(videos) >= MAX_VIDEOS:
            return
        vistos.add(clave)
        videos.append({"tipo": tipo, "id": vid, "src": src, "decorativo": decorativo})

    def _absoluta(href: str | None) -> str | None:
        limpio = _clean(href)
        if not limpio or limpio[:5].lower() in ("data:", "blob:"):
            return None
        return _resolve(base_url, limpio) if base_url else limpio

    fuera = "[not(ancestor::template)]"
    for nodo in selector.xpath(f"//*[self::video or self::iframe or self::embed or self::object"
                               f" or self::lite-youtube or self::lite-vimeo]{fuera}"):
        raiz = nodo.root
        etiqueta = (raiz.tag or "").lower() if isinstance(raiz.tag, str) else ""
        if etiqueta == "video":
            src = raiz.get("src") or nodo.xpath("./source/@src").get()
            decorativo = (raiz.get("autoplay") is not None and raiz.get("muted") is not None
                          and raiz.get("controls") is None)
            _anadir("video", None, _absoluta(src), decorativo)
            continue
        if etiqueta in ("lite-youtube", "lite-vimeo"):
            vid = _clean(raiz.get("videoid"))
            if vid:
                _anadir(etiqueta.split("-", 1)[1], vid, None)
            continue
        for atributo in ("src", "data-src", "data-lazy-src", "data"):
            proveedor = video_de_url(raiz.get(atributo))
            if proveedor:
                _anadir(proveedor[0], proveedor[1], _absoluta(raiz.get(atributo)))
                break
    return videos


# ---------------------------------------------------------------------------
# Screaming-Frog-style extraction helpers
# ---------------------------------------------------------------------------

# Lo que vive dentro de `<template>` o `<noscript>` no esta en el DOM: la
# plantilla de un framework no se pinta hasta que JS la clona (y entonces el
# clon SI aparece, asi que contar las dos es contar doble), y el `<noscript>`
# es el respaldo para quien no ejecuta JS, que no es el caso de Googlebot. Medido
# en 58 paginas de control: 583 de 4.409 imagenes eran el respaldo de la carga
# diferida, cada una duplicando la imagen real y pudiendo inventar un
# `image_missing_alt` sobre una imagen que nadie ve.
_XPATH_FUERA_DEL_DOM = "[not(ancestor::template) and not(ancestor::noscript)]"


# Escrituras que no separan las palabras con espacios: sin un diccionario de
# segmentacion lo unico que se puede medir es el numero de caracteres, que es
# lo que cuenta Screaming Frog y lo que usan las herramientas de traduccion.
# Antes se contaba con split(), asi que una pagina japonesa entera daba 1
# palabra: TODAS las paginas CJK salian como low_word_count y quedaban fuera
# del analisis semantico. Un ideograma no es exactamente una palabra (en chino
# una palabra son ~1,5 caracteres, en japones ~2 kana), asi que el recuento
# queda por encima del real; el error es conocido y acotado, y es mucho menor
# que el de contar 1.
_CARACTERES_SIN_ESPACIOS = (
    "\u3040-\u30ff"  # hiragana y katakana
    "\u3400-\u4dbf"  # Han, extension A
    "\u4e00-\u9fff"  # Han, CJK unificado
    "\uf900-\ufaff"  # Han, formas de compatibilidad
    "\u0e00-\u0e7f"  # tailandes
    "\u0e80-\u0eff"  # lao
    "\u1000-\u109f"  # birmano
    "\u1780-\u17ff"  # jemer
)
_RE_SIN_ESPACIOS = re.compile(f"[{_CARACTERES_SIN_ESPACIOS}]")

# Un token sin ninguna letra ni cifra no es una palabra. Los separadores
# suelen ir en su propio nodo de texto ("Zapatillas" | "-" | "Nike", "1.299"
# " EUR "), de modo que cada guion y cada simbolo sumaba una palabra.
_RE_ALGO_QUE_LEER = re.compile(r"[^\W_]", re.UNICODE)


def hash_de_contenido(texto: str | None) -> str | None:
    """SHA-256 del contenido normalizado: minusculas y espacios colapsados.

    `body_hash` es el hash de los BYTES de la respuesta, asi que un token CSRF,
    un nonce de CSP o una marca de tiempo en el HTML hacen que dos paginas
    identicas tengan hashes distintos: en un CMS moderno el duplicado exacto
    por bytes practicamente no existe y `analyze_duplicates` no encontraba
    nada. Google compara el contenido, no los bytes. Se normaliza solo caja y
    espacios: un precio o una fecha distintos SI son una diferencia, y de la
    similitud parcial se encarga MinHash (`analyze_near_duplicates`).
    """
    if not texto:
        return None
    normalizado = _WHITESPACE.sub(" ", texto).strip().casefold()
    if not normalizado:
        return None
    return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()


def contar_palabras(texto: str | None) -> int:
    """Cuenta palabras tolerando escrituras sin espacios y descartando signos.

    Las escrituras sin espacios (han, kana, tailandes, lao, birmano, jemer)
    se cuentan por caracteres; el resto por tokens, ignorando los que no
    tienen ninguna letra ni cifra.
    """
    if not texto:
        return 0
    caracteres = 0
    if _RE_SIN_ESPACIOS.search(texto):
        caracteres = len(_RE_SIN_ESPACIOS.findall(texto))
        texto = _RE_SIN_ESPACIOS.sub(" ", texto)
    palabras = 0
    for token in texto.split():
        if _RE_ALGO_QUE_LEER.search(token):
            palabras += 1
    return palabras + caracteres


def _raiz_del_body(selector):
    """Elemento lxml del ``<body>``, o None si el documento no tiene body."""
    body = selector.css("body")
    if not body:
        return None
    raiz = body[0].root
    return raiz if hasattr(raiz, "tag") else None


def medir_data_nosnippet(selector, main_content: str | None = None) -> dict[str, Any] | None:
    """Lo que ``data-nosnippet`` esconde al fragmento en la pagina (decision
    170, ``shared/data_nosnippet.py``). Best-effort: cualquier fallo da None,
    que es «no medido», y no se lleva la fila de ``html_meta``."""
    try:
        from shared.data_nosnippet import medir

        return medir(_raiz_del_body(selector), main_content)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("No se pudo medir data-nosnippet", exc_info=True)
        return None


def extract_word_count(selector) -> int:
    """Cuenta las palabras del texto visible del ``<body>``.

    Excluye lo que el navegador no pinta (script, style, noscript, template,
    iframe, el title de un svg, lo marcado con ``hidden`` o con un
    ``display:none`` en linea) y cuenta por caracteres las escrituras que no
    separan palabras con espacios.
    """
    raiz = _raiz_del_body(selector)
    if raiz is None:
        return 0
    return contar_palabras("\n".join(_lineas_visibles(raiz)))


def extract_visible_text(selector) -> str:
    """Texto visible del ``<body>``, una linea por bloque.

    Devuelve cadena vacia cuando no hay ``<body>``. Los nodos que solo tienen
    espacios se descartan: cuando se incluian, ``text_ratio`` media sobre todo
    la indentacion de la plantilla (una pagina con "Hola mundo." daba 67,6 %
    en vez de 5,0 %) y por eso ``low_text_ratio`` casi nunca saltaba.
    """
    raiz = _raiz_del_body(selector)
    if raiz is None:
        return ""
    return "\n".join(_lineas_visibles(raiz))


def compute_text_ratio(html_text: str, visible_text: str) -> float:
    """Return the visible-text to raw-HTML size ratio as a percentage (0-100).

    Parameters
    ----------
    html_text:
        The full raw HTML source of the page.
    visible_text:
        The extracted visible text (e.g. from ``extract_visible_text``).

    Returns
    -------
    float
        Ratio expressed as a percentage.  Returns ``0.0`` when *html_text*
        is empty.
    """
    html_len = len(html_text)
    if html_len == 0:
        return 0.0
    text_len = len(visible_text)
    return round((text_len / html_len) * 100, 2)


def compute_folder_depth(url: str) -> int:
    """Count non-empty path segments of *url*.

    Examples
    --------
    >>> compute_folder_depth("https://example.com/")
    0
    >>> compute_folder_depth("https://example.com/blog/post/1")
    3
    >>> compute_folder_depth("https://example.com/blog/post/1/")
    3
    """
    path = urlparse(url).path
    segments = [seg for seg in path.split("/") if seg]
    return len(segments)


# ---------------------------------------------------------------------------
# SERP pixel-width estimation
# ---------------------------------------------------------------------------

# Approximate character widths (in pixels) for Arial at 20px.
# Measured from font metrics; values are rounded to one decimal place.
_ARIAL_20PX_WIDTHS: dict[str, float] = {
    # Lowercase letters
    "a": 9.8,  "b": 9.8,  "c": 8.9,  "d": 9.8,  "e": 9.8,
    "f": 5.6,  "g": 9.8,  "h": 9.8,  "i": 4.4,  "j": 4.4,
    "k": 8.9,  "l": 4.4,  "m": 14.5, "n": 9.8,  "o": 9.8,
    "p": 9.8,  "q": 9.8,  "r": 5.6,  "s": 8.9,  "t": 5.6,
    "u": 9.8,  "v": 8.9,  "w": 12.2, "x": 8.9,  "y": 8.9,
    "z": 8.9,
    # Uppercase letters
    "A": 12.2, "B": 11.1, "C": 11.1, "D": 12.2, "E": 10.0,
    "F": 10.0, "G": 12.2, "H": 12.2, "I": 4.4,  "J": 7.8,
    "K": 11.1, "L": 10.0, "M": 13.3, "N": 12.2, "O": 12.2,
    "P": 10.0, "Q": 12.2, "R": 11.1, "S": 10.0, "T": 10.0,
    "U": 12.2, "V": 11.1, "W": 15.6, "X": 11.1, "Y": 10.0,
    "Z": 10.0,
    # Digits
    "0": 9.8,  "1": 9.8,  "2": 9.8,  "3": 9.8,  "4": 9.8,
    "5": 9.8,  "6": 9.8,  "7": 9.8,  "8": 9.8,  "9": 9.8,
    # Common symbols and punctuation
    " ": 5.0,  "!": 5.6,  '"': 7.1,  "#": 9.8,  "$": 9.8,
    "%": 15.6, "&": 11.7, "'": 3.9,  "(": 5.6,  ")": 5.6,
    "*": 6.7,  "+": 10.3, ",": 5.0,  "-": 5.6,  ".": 5.0,
    "/": 5.6,  ":": 5.6,  ";": 5.6,  "<": 10.3, "=": 10.3,
    ">": 10.3, "?": 9.8,  "@": 17.8, "[": 5.6,  "\\": 5.6,
    "]": 5.6,  "^": 10.3, "_": 9.8,  "`": 5.6,  "{": 5.6,
    "|": 4.6,  "}": 5.6,  "~": 10.3,
}

# Fallback width for characters not in the lookup table.
_ARIAL_20PX_DEFAULT: float = 9.6

# Anchos por rango Unicode para lo que no esta en la tabla de Arial. El 9,6 px
# por caracter de antes daba 221 px para un titulo japones de 23 caracteres que
# en pantalla mide unos 460: la mitad. Como Google corta el titulo por PIXELES,
# ningun titulo CJK salia como truncado.
#
# Los ideogramas y los kana son de ancho completo (la mitad de la caja de texto
# los llama "fullwidth"): ~20 px a 20 px de cuerpo, el doble que una letra
# latina. El tailandes, el arabe y el hebreo son algo mas estrechos que el
# latino. Un emoji ocupa como un ideograma.
_ANCHOS_POR_RANGO: tuple[tuple[int, int, float], ...] = (
    (0x0590, 0x05FF, 8.5),    # hebreo
    (0x0600, 0x06FF, 8.5),    # arabe
    (0x0E00, 0x0E7F, 8.0),    # tailandes
    (0x1100, 0x11FF, 20.0),   # jamo hangul
    (0x2E80, 0x303F, 20.0),   # radicales y puntuacion CJK
    (0x3040, 0x30FF, 20.0),   # hiragana y katakana
    (0x3100, 0x312F, 20.0),   # bopomofo
    (0x3130, 0x318F, 20.0),   # compatibilidad hangul
    (0x3400, 0x4DBF, 20.0),   # Han, extension A
    (0x4E00, 0x9FFF, 20.0),   # Han, CJK unificado
    (0xA960, 0xA97F, 20.0),   # jamo extendido
    (0xAC00, 0xD7AF, 20.0),   # silabas hangul
    (0xF900, 0xFAFF, 20.0),   # Han, compatibilidad
    (0xFF01, 0xFF60, 20.0),   # formas de ancho completo
    (0x1F300, 0x1FAFF, 20.0),  # emoji
    (0x2600, 0x27BF, 20.0),   # simbolos y dingbats que se pintan como emoji
)

# Caracteres que no ocupan nada: el selector de variacion y el unificador de
# cero ancho con los que se construye un emoji de familia o de bandera. Sin
# esto, un emoji compuesto se contaba como tres o cuatro caracteres anchos.
_SIN_ANCHO = frozenset({"\u200d", "\ufe0f", "\ufe0e", "\u200b", "\u2060"})


def _ancho_de_caracter(ch: str) -> float:
    """Ancho en pixeles del caracter a 20 px de cuerpo."""
    ancho = _ARIAL_20PX_WIDTHS.get(ch)
    if ancho is not None:
        return ancho
    if ch in _SIN_ANCHO:
        return 0.0
    punto = ord(ch)
    for inicio, fin, valor in _ANCHOS_POR_RANGO:
        if inicio <= punto <= fin:
            return valor
    return _ARIAL_20PX_DEFAULT


def _estimate_pixel_width(text: str, scale: float = 1.0) -> int:
    """Sum per-character pixel widths using the Arial 20px table.

    Parameters
    ----------
    text:
        The string to measure.
    scale:
        Multiplier applied to each character width.  Use ``1.0`` for 20px
        and ``0.7`` for 14px.

    Returns
    -------
    int
        Total estimated pixel width, rounded to the nearest integer.
    """
    if not text:
        return 0
    total = 0.0
    anterior = ""
    for ch in text:
        # Un emoji compuesto (familia, bandera, profesion) es UN glifo: los
        # trozos que van detras de un unificador de cero ancho no ocupan mas.
        if anterior == "\u200d":
            anterior = ch
            continue
        total += _ancho_de_caracter(ch) * scale
        anterior = ch
    return round(total)


def estimate_title_pixel_width(text: str) -> int:
    """Approximate SERP title pixel width using Arial at 20px.

    Google renders title tags in ~20px Arial (or a similar font) on
    desktop SERPs.  Titles are typically truncated around 580-600 pixels.
    """
    return _estimate_pixel_width(text, scale=1.0)


def estimate_description_pixel_width(text: str) -> int:
    """Approximate SERP description pixel width using Arial at 14px.

    Meta descriptions on desktop SERPs are rendered at roughly 14px,
    which is approximately 70% of the 20px title size.  Descriptions
    are typically truncated around 920-960 pixels.
    """
    return _estimate_pixel_width(text, scale=0.7)


# ---------------------------------------------------------------------------
# Meta-refresh and security helpers
# ---------------------------------------------------------------------------

def extract_meta_refresh(selector) -> str | None:
    """Find ``<meta http-equiv="refresh">`` and return its *content* value.

    Returns ``None`` when no refresh directive is present.
    """
    # http-equiv no distingue mayusculas. Probar tres variantes a mano dejaba
    # fuera `REFresh` y cualquier otra mezcla, que es lo que escriben las
    # plantillas antiguas; la pagina salia sin meta refresh y su destino no se
    # seguia. El extractor del DESTINO ya lo hacia bien con translate(), asi que
    # las dos funciones se contradecian sobre la misma pagina.
    #
    # Pero los espacios SI cuentan: comprobado con Chromium (#107), un
    # `http-equiv="refresh "`, `" refresh"` o con tabulador no redirige. Con
    # `normalize-space` esta funcion veia una redireccion que ningun navegador
    # hace, mientras la del destino (sin normalizar) no la veia. Y como esa, sin
    # las de dentro de `<noscript>`.
    content = selector.xpath(
        f"//meta[translate(@http-equiv, '{_XP_UPPER}', '{_XP_LOWER}') = 'refresh']"
        "[not(ancestor::noscript)]/@content"
    ).get()
    return _clean(content)


_REFRESH_URL_RE = re.compile(r"""^\s*[\d.]*\s*[;,]?\s*(?:url\s*=\s*)?(.*)$""", re.I | re.S)


def extract_meta_refresh_target(selector, base_url: str) -> str | None:
    """Destino absoluto y normalizado de una meta refresh, o None.

    Una refresh sin URL (`content="300"`) solo recarga la pagina: no es una
    redireccion. Se ignoran las que estan dentro de `<noscript>`, como hace
    Scrapy: solo aplican con JavaScript desactivado.
    """
    content = selector.xpath(
        f"//meta[translate(@http-equiv, '{_XP_UPPER}', '{_XP_LOWER}') = 'refresh']"
        "[not(ancestor::noscript)]/@content"
    ).get()
    if not content:
        return None
    m = _REFRESH_URL_RE.match(content)
    destino = (m.group(1) if m else "").strip().strip("'\"").strip()
    if not destino:
        return None
    absoluta = absolutize_url(base_url, destino)
    # `content="0; url=javascript:void(0)"` no lleva a ninguna parte: se tomaba
    # como redireccion a `javascript:void(0)` y el spider intentaba seguirla
    # (B3 de #150). Ni `data:` ni `mailto:` son paginas.
    if not es_url_web(absoluta):
        return None
    return absoluta


# -- Redireccion por JavaScript (decision 108) --------------------------------

# Tipos de `<script>` que el navegador ejecuta. Un `application/ld+json` o un
# `text/template` que dice «location» no redirige a nadie.
_TIPOS_SCRIPT_EJECUTABLE = frozenset({
    "", "text/javascript", "application/javascript", "module",
    "text/ecmascript", "application/ecmascript", "application/x-javascript",
    "text/x-javascript", "text/jscript", "text/livescript",
})
_LOCATION = r"(?<![\w$.])(?:(?:window|document|top|self)\.){0,2}location"
_RE_REDIRECCION_JS = re.compile(
    _LOCATION + r"""(?:\s*\.\s*href)?\s*=(?!=)\s*(["'])([^"'\\\n]*)\1(?=[ \t]*(?:[;}\r\n]|$))"""
    + "|" + _LOCATION + r"""\s*\.\s*(?:replace|assign)\s*\(\s*(["'])([^"'\\\n]*)\3\s*\)"""
)
# Lo que puede ir justo antes de una expresion regular literal (y no de una
# division): sin distinguirlas, el `'` de `/it's/` abria una cadena falsa.
_ANTES_DE_REGEX = set("(,=:[!&|?{};+-*%<>~^")
# Una sentencia que empieza tras estas palabras no se ejecuta siempre.
_PALABRAS_CONDICIONALES = frozenset({"else", "do", "return", "case", "default"})
_MAX_SCRIPT = 200_000


def _sentencias_de_nivel_superior(js: str) -> set[int]:
    """Posiciones de `js` donde empieza una sentencia que se ejecuta SIEMPRE.

    Un escaner minimo de JavaScript, sin dependencias: salta cadenas,
    plantillas, comentarios y expresiones regulares, y cuenta llaves,
    parentesis y corchetes. Una posicion vale si esta a profundidad 0 y lo
    anterior cierra una sentencia (`;`, `}`, el inicio, o un salto de linea
    tras algo que no la deja abierta). Lo que va dentro de una funcion, de un
    `if (...) {` , de un `setTimeout(...)` o de un `addEventListener` queda a
    profundidad mayor que 0; el `if (x) location = ...` sin llaves, tras un
    `)`, y el de despues de un `else` o un `=>`, se descartan: lo condicional
    no se afirma.
    """
    validas: set[int] = set()
    n = len(js)
    i = 0
    profundidad = 0
    ultimo = ""          # ultimo caracter significativo
    ultima_palabra = ""  # ultima palabra significativa
    hubo_salto = False
    while i < n:
        c = js[i]
        if c in " \t\r\n":
            if c == "\n":
                hubo_salto = True
            i += 1
            continue
        # Comentarios al estilo HTML (Anexo B de ECMAScript): `<!--` en
        # cualquier sitio y `-->` a principio de linea son comentarios de una
        # linea. Los portales antiguos envuelven asi el script entero
        # (`<script><!-- ... //--></script>`), y sin esto el `-` del `<!--`
        # dejaba abierta la sentencia y la pagina puente no se veia.
        if (c == "/" and i + 1 < n and js[i + 1] == "/") \
                or js.startswith("<!--", i) \
                or (js.startswith("-->", i) and (hubo_salto or ultimo == "")):
            fin = js.find("\n", i)
            i = n if fin < 0 else fin
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "*":
            fin = js.find("*/", i + 2)
            hubo_salto = hubo_salto or fin < 0 or "\n" in js[i:fin]
            i = n if fin < 0 else fin + 2
            continue
        # Donde empieza algo significativo: ¿es una sentencia que siempre corre?
        if profundidad == 0:
            if ultimo == "" or ultimo in ";}":
                abierta = ultima_palabra in _PALABRAS_CONDICIONALES
            elif hubo_salto:
                # ASI: un salto de linea cierra la sentencia anterior si esta
                # acabo en algo que no pide continuacion.
                abierta = (ultimo == ")" or ultimo in _ANTES_DE_REGEX
                           or ultima_palabra in _PALABRAS_CONDICIONALES)
            else:
                abierta = True
            if not abierta:
                validas.add(i)
        hubo_salto = False
        if c in "'\"`":
            j = i + 1
            while j < n and js[j] != c:
                if js[j] == "\\":
                    j += 1
                elif c != "`" and js[j] == "\n":
                    break
                j += 1
            i = j + 1
            ultimo, ultima_palabra = c, ""
            continue
        if c == "/" and (ultimo == "" or ultimo in _ANTES_DE_REGEX):
            j = i + 1
            en_clase = False
            while j < n and js[j] != "\n":
                if js[j] == "\\":
                    j += 1
                elif js[j] == "[":
                    en_clase = True
                elif js[j] == "]":
                    en_clase = False
                elif js[j] == "/" and not en_clase:
                    break
                j += 1
            i = j + 1
            ultimo, ultima_palabra = "/", ""
            continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (js[j].isalnum() or js[j] in "_$"):
                j += 1
            ultima_palabra = js[i:j]
            ultimo = js[j - 1]
            i = j
            continue
        if c in "({[":
            profundidad += 1
        elif c in ")}]":
            profundidad = max(0, profundidad - 1)
        # `=>` acaba en `>`, que deja abierta la sentencia: el cuerpo de una
        # flecha no corre ya.
        ultimo, ultima_palabra = c, ""
        i += 1
    return validas


def extract_redireccion_js(selector, base_url: str, page_url: str | None = None) -> str | None:
    """Destino de una redireccion por JavaScript que corre SIEMPRE, o None.

    Solo `location = "..."`, `location.href = "..."` y
    `location.replace/assign("...")` con un literal, en un `<script>` en linea
    que se ejecuta (no `ld+json`, ni dentro de `<noscript>` o `<template>`), y
    a nivel superior del script: la pagina puente. Lo que va en una funcion, un
    `if`, un temporizador o un evento no se afirma —FreeCrawl, con el primer
    literal que aparece, daba 4 falsos positivos de 5 casos (repro de la guia
    de #150)—, ni un destino concatenado, ni `#...`, `javascript:` o la propia
    pagina. Devuelve el destino absoluto y normalizado como las URLs del censo
    (`absolutize_url`), para poder buscarlo entre ellas.
    """
    propia = absolutize_url(page_url, "") if page_url else None
    scripts = selector.xpath(
        "//script[not(@src)][not(ancestor::noscript)][not(ancestor::template)]"
    )
    for script in scripts:
        tipo = (script.xpath("@type").get() or "").strip().lower().split(";")[0].strip()
        if tipo not in _TIPOS_SCRIPT_EJECUTABLE:
            continue
        js = "".join(script.xpath("text()").getall())
        if "location" not in js or len(js) > _MAX_SCRIPT:
            continue
        validas = None
        for m in _RE_REDIRECCION_JS.finditer(js):
            if validas is None:
                validas = _sentencias_de_nivel_superior(js)
            if m.start() not in validas:
                continue
            destino = (m.group(2) if m.group(2) is not None else m.group(4)).strip()
            if not destino or destino.startswith("#"):
                continue
            absoluta = absolutize_url(base_url, destino)
            if not absoluta or urlparse(absoluta).scheme not in ("http", "https"):
                continue
            if absoluta == propia:
                continue
            return absoluta
    return None


# -- Charset (decision 108) ---------------------------------------------------

# El navegador solo busca el `<meta charset>` en los 1.024 primeros bytes
# (WHATWG, «prescan»); w3lib mira 4.096. Un meta mas abajo no cuenta.
BYTES_PRESCAN_CHARSET = 1024


def charset_declarado(body: bytes) -> dict[str, Any]:
    """Lo que el cuerpo declara de su codificacion, normalizado.

    - ``charset_meta``: el del `<meta charset>` o `http-equiv` en los 1.024
      primeros bytes; ``""`` si no hay (medido y ausente).
    - ``charset_bom``: el del BOM, o None.
    - ``cuerpo_solo_ascii``: sin bytes por encima de 127 no hay nada que
      adivinar, y un charset ausente no rompe nada.

    La cabecera no va aqui: ya se guarda entera en ``urls.content_type``.
    """
    bom, _ = read_bom(body)
    meta = html_body_declared_encoding(body[:BYTES_PRESCAN_CHARSET])
    return {
        "charset_meta": (normalizar_charset(meta) or "")[:40],
        "charset_bom": (normalizar_charset(bom) or "")[:40] or None,
        "cuerpo_solo_ascii": body.isascii(),
    }


def detect_mixed_content(selector, page_url: str) -> list[str]:
    """Detect HTTP resources loaded on an HTTPS page (mixed content).

    Parameters
    ----------
    selector:
        A ``parsel.Selector`` for the page HTML.
    page_url:
        The fully-qualified URL of the page being inspected.

    Returns
    -------
    list[str]
        HTTP (non-HTTPS) resource URLs found on the page.  Returns an
        empty list if *page_url* itself is not HTTPS.
    """
    parsed_page = urlparse(page_url)
    if parsed_page.scheme != "https":
        return []

    http_resources: list[str] = []
    seen: set[str] = set()

    # Selectors for resource attributes that can reference external URLs.
    resource_selectors = [
        ("img[src]", "src"),
        ("script[src]", "src"),
        ('link[rel="stylesheet"][href]', "href"),
        ("iframe[src]", "src"),
    ]

    for css_sel, attr in resource_selectors:
        for element in selector.css(css_sel):
            raw_url = (element.attrib.get(attr) or "").strip()
            if not raw_url:
                continue
            try:
                absolute = urljoin(page_url, raw_url)
            except ValueError:
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            if urlparse(absolute).scheme == "http":
                http_resources.append(absolute)

    return http_resources


def canonical_de_cabecera(valor_link: str | None, base_url: str = "") -> str | None:
    """Canonical declarado en la cabecera HTTP ``Link`` (RFC 8288).

    Se cogia el PRIMER enlace de la cabecera fuera el que fuera, asi que con
    `</style.css>; rel=preload, <https://x.com/a>; rel="canonical"` se guardaba
    `/style.css` como canonical de la pagina. Tampoco se reconocia `rel=canonical`
    sin comillas —que es valido— ni se resolvia un valor relativo.

    Es la unica forma de declarar un canonical en un PDF o una imagen, asi que
    leerla mal es decir que un PDF canonicaliza a una hoja de estilos.
    """
    for destino, parametros in enlaces_de_cabecera(valor_link, base_url):
        if "canonical" in parametros.get("rel", "").lower().split():
            return destino
    return None


def enlaces_de_cabecera(
    valor_link: str | None, base_url: str = "",
) -> list[tuple[str, dict[str, str]]]:
    """Los enlaces de una cabecera ``Link`` (RFC 8288): ``(destino, parametros)``.

    El destino va resuelto contra *base_url* (si se da); los nombres de los
    parametros, en minusculas, y los valores sin comillas y con su caja. Con un
    parametro repetido vale el primero, como pide la RFC para `rel`. Un `;` o
    una `,` entre comillas (`title="a; b"`) no parten nada.
    """
    if not valor_link:
        return []
    enlaces: list[tuple[str, dict[str, str]]] = []
    for entrada in _separar_cabecera_link(valor_link):
        partes = [p.strip() for p in _partir_fuera_de_comillas(entrada, ";")]
        if not partes or not partes[0].startswith("<") or ">" not in partes[0]:
            continue
        destino = partes[0][1:partes[0].index(">")].strip()
        parametros: dict[str, str] = {}
        for parametro in partes[1:]:
            if "=" not in parametro:
                continue
            nombre, _, bruto = parametro.partition("=")
            nombre = nombre.strip().lower()
            if nombre and nombre not in parametros:
                parametros[nombre] = bruto.strip().strip('"\'').strip()
        enlaces.append((_resolve(base_url, destino) if base_url else destino, parametros))
    return enlaces


def hreflang_de_cabecera(valor_link: str | None, base_url: str = "") -> list[dict[str, Any]]:
    """Las anotaciones hreflang de la cabecera ``Link``.

    Google las lee igual que las del `<head>`, y en un PDF son la unica forma
    de declararlas; antes no se leian en ningun sitio. `rel` se compara por
    token (`rel="alternate nofollow"` vale, decision 56). Deduplicadas como
    las del HTML.
    """
    salida: list[dict[str, Any]] = []
    for destino, parametros in enlaces_de_cabecera(valor_link, base_url):
        lang = _clean(parametros.get("hreflang"))
        if not lang or not destino or "alternate" not in rel_tokens(parametros.get("rel")):
            continue
        salida.append({"lang": lang, "href": destino, "en_body": False,
                       "origen": ORIGEN_HREFLANG_CABECERA})
    return deduplicar_hreflang(salida)


def _partir_fuera_de_comillas(valor: str, separador: str) -> list[str]:
    """Parte *valor* por *separador* salvo dentro de comillas dobles o de <>."""
    trozos: list[str] = []
    actual: list[str] = []
    en_url = en_comillas = False
    for ch in valor:
        if ch == '"' and not en_url:
            en_comillas = not en_comillas
        elif ch == "<" and not en_comillas:
            en_url = True
        elif ch == ">" and not en_comillas:
            en_url = False
        if ch == separador and not en_url and not en_comillas:
            trozos.append("".join(actual))
            actual = []
            continue
        actual.append(ch)
    if actual:
        trozos.append("".join(actual))
    return trozos


def _separar_cabecera_link(valor: str) -> list[str]:
    """Separa los enlaces de una cabecera ``Link`` por las comas de FUERA de <>.

    Una URL puede llevar comas (`<https://x.com/a,b>`), asi que partir por coma
    a secas rompe el enlace en dos. Tampoco cuentan las de un valor entre
    comillas (`title="Ingles, Reino Unido"`).
    """
    return [e for e in (x.strip() for x in _partir_fuera_de_comillas(valor, ",")) if e]


def extract_security_headers(headers: dict) -> dict:
    """Inspect response headers for common security-related directives.

    Parameters
    ----------
    headers:
        A dict (or dict-like) of HTTP response headers.  Keys are expected
        to be strings; look-ups are performed case-insensitively.

    Returns
    -------
    dict
        A dict with boolean flags for HSTS, CSP, X-Content-Type-Options,
        X-Frame-Options, and the ``Referrer-Policy`` value (or ``None``).
    """
    # Build a lower-cased lookup for case-insensitive matching.
    lower_headers: dict[str, str] = {}
    for clave, valor in headers.items():
        if isinstance(valor, bytes):
            valor = valor.decode("utf-8", errors="ignore")
        lower_headers[str(clave).lower()] = valor if isinstance(valor, str) else str(valor)

    def presente(nombre: str) -> bool:
        """La cabecera esta Y dice algo.

        Un `X-Frame-Options:` vacio no protege de nada y se contaba como
        presente, de modo que el informe decia que estaba puesta.
        """
        return bool((lower_headers.get(nombre) or "").strip())

    # `max-age=0` es como no tener HSTS: le dice al navegador que OLVIDE la
    # politica. Se contaba como presente.
    hsts = lower_headers.get("strict-transport-security") or ""
    hsts_valido = bool(hsts.strip()) and not re.search(
        r"max-age\s*=\s*0(?!\d)", hsts, re.I
    )
    # Con `frame-ancestors` en la CSP, X-Frame-Options es redundante: los
    # navegadores modernos hacen caso a la CSP y la ignoran a ella. Pedir las
    # dos era un aviso sobre algo que ya esta resuelto.
    csp = lower_headers.get("content-security-policy") or ""
    tiene_frame_ancestors = "frame-ancestors" in csp.lower()

    return {
        "has_hsts": hsts_valido,
        "has_csp": presente("content-security-policy"),
        "has_x_content_type_options": presente("x-content-type-options"),
        "has_x_frame_options": presente("x-frame-options") or tiene_frame_ancestors,
        "referrer_policy": (lower_headers.get("referrer-policy") or "").strip() or None,
    }


# ---------------------------------------------------------------------------
# HTTP status helpers
# ---------------------------------------------------------------------------

_HTTP_STATUS_TEXT: dict[int, str] = {
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    410: "Gone",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


def http_status_text(status_code: int) -> str:
    """Map an HTTP status code to its standard reason phrase.

    Returns ``"Unknown"`` for codes not in the lookup table.
    """
    return _HTTP_STATUS_TEXT.get(status_code, "Unknown")


# ---------------------------------------------------------------------------
# Content extraction — using trafilatura for site-agnostic boilerplate removal
# ---------------------------------------------------------------------------

# Boilerplate CSS selectors stripped from HTML *before* trafilatura runs.
# Covers cookie-consent libraries, chat widgets, ARIA modals, and common
# non-content structural elements.  Uses lxml.cssselect (always available
# via parsel/Scrapy).
_BOILERPLATE_CSS_SELECTORS: list[str] = [
    # ---- Known consent-management libraries ----
    "#CybotCookiebotDialog", "#CybotCookiebotDialogBodyUnderlay",
    "#onetrust-banner-sdk", "#onetrust-consent-sdk",
    ".osano-cm-window", ".cc-window", ".cc-banner", ".cc-revoke",
    "#tarteaucitronRoot", "#usercentrics-root", "#sp-consent-message",
    "#ez-cookie-dialog", "#catapult-cookie-bar", "#moove_gdpr_cookie_info_bar",
    # ---- Chat widgets ----
    "#hubspot-messages-iframe-container",
    "#intercom-container", "#intercom-frame",
    "#crisp-chatbox",
    "#drift-widget-container", "#drift-frame-chat",
    "#tawk-bubble-container",
    # ---- ARIA modals / HTML5 dialogs ----
    # Solo el ABIERTO: un `<dialog open>` en el HTML servido se pinta encima de
    # la pagina nada mas cargar, que es la forma de un interstitial (cookies,
    # newsletter, selector de pais). El cerrado no va aqui: ver
    # ``_dialog_es_plantilla`` (#27, decision 79).
    "[aria-modal='true']",
    "dialog[open]",
]

# Nombres que delatan un `<dialog>` de INTERFAZ y no de contenido: cuenta,
# carrito, buscador, menu. Solo se aplican a `<dialog>`, no a cualquier
# elemento como las listas de arriba: `account` o `search` sobre un `<div>`
# cualquiera casaria con bloques de contenido (`search-results`, la ficha de
# `account-types` de un banco), pero un MODAL con ese nombre es el de iniciar
# sesion o el del buscador.
_DIALOG_UI_PATTERNS: list[str] = [
    "login", "log-in", "signin", "sign-in", "account", "cuenta",
    "cart", "carrito", "basket", "cesta", "search", "buscador", "busqueda",
    "menu", "share", "compartir",
]

# Substrings matched against element id and class attributes (case-insensitive).
# An element is removed if ANY of its id/class tokens contains one of these.
# (Structural tags — form/nav/aside and page-level header/footer — are
# handled landmark-aware in ``_strip_boilerplate_html``, see below.)
_BOILERPLATE_ID_CLASS_PATTERNS: list[str] = [
    # Cookie / consent / GDPR / privacy
    "cookie-consent", "cookie-banner", "cookie-notice", "cookie-bar",
    "cookie-popup", "cookie-modal", "cookie-wall", "cookie-law",
    "cookie-policy", "cookie-message", "cookie-alert", "cookie-overlay",
    "cookieconsent", "cookiebanner", "cookienotice", "cookiebar",
    "cookies-eu", "cookies-modal", "cookies-overlay",
    "gdpr-banner", "gdpr-notice", "gdpr-popup", "gdpr-overlay", "gdpr-consent",
    "consent-banner", "consent-modal", "consent-popup", "consent-overlay",
    "privacy-banner", "privacy-notice", "privacy-popup",
    # Chat widgets
    "chat-widget", "livechat",
]

# Promotional / engagement blocks: newsletter CTAs, product carousels,
# cross-sell and "related items" grids.  Kept separate from the core
# boilerplate list because on rare layouts a carousel can hold primary
# content — jobs can opt out via ``extraction.strip_promo_blocks``.
_PROMO_ID_CLASS_PATTERNS: list[str] = [
    "newsletter",
    "carousel",
    "cross-sell", "crosssell", "crossselling",
    "upsell", "up-sell",
    "related-product", "relatedproduct",
    "related-post", "relatedpost",
    "recently-viewed", "recentlyviewed",
    "product-recommend", "recommended-product",
    # Subscription / signup CTAs (es + en)
    "subscribe", "subscription", "suscripcion", "suscribete",
    "signup", "sign-up",
    # Social / sharing widgets
    "social-share", "share-button", "sharing-button",
    # Ads / comments embeds
    "advertisement", "adsense", "disqus",
    # Misc engagement widgets
    "wishlist", "lightbox",
]

# Text phrases (lowercase, es + en) that identify small CTA/legal/social
# blocks regardless of their class names — catches bespoke-class noise
# that no id/class pattern can.  Only elements whose TOTAL visible text
# is short (< _PROMO_TEXT_MAX_LEN) are removed, so an article that merely
# mentions one of these phrases in a real paragraph is never affected.
_PROMO_TEXT_PHRASES: list[str] = [
    # Cookies / consent
    "aceptar cookies", "accept cookies", "utilizamos cookies", "we use cookies",
    "política de cookies", "cookie policy",
    # Newsletter / subscription CTAs
    "suscríbete", "suscribete", "subscribe to", "join our newsletter",
    # Social CTAs
    "síguenos en", "siguenos en", "follow us on", "compartir en", "share on",
    # Legal boilerplate
    "todos los derechos reservados", "all rights reserved",
]

_PROMO_TEXT_MAX_LEN = 400  # chars — only prune small blocks

# Cuantas veces la frase puede caber en el bloque para que el bloque SEA la
# frase y no un texto que la MENCIONA. Sin esto, cualquier parrafo de menos de
# 400 caracteres que dijera "politica de cookies" desaparecia: medido en 58
# paginas de control, se borraban 526 bloques y entre ellos los parrafos del
# cuerpo de las paginas de cookies y de privacidad —el contenido por el que esas
# paginas existen—, y un bloque con el h2 "Quienes somos" porque acababa en
# "Siguenos en LinkedIn". El corte sale de los datos: todo lo que esta por
# debajo de 6 es un reclamo ("Suscribete a las novedades y gestiona tus
# intereses", 5,1) y a partir de 7,7 ya es prosa ("Esta Politica de Cookies
# podra ser modificada o actualizada en cualquier momento...", 8,4).
_PROMO_TEXT_MAX_FACTOR = 6.0

_PROMO_TEXT_TAGS: frozenset[str] = frozenset({
    "div", "section", "p", "span", "li", "a", "button",
})


# Landmark semantics for content extraction.
#
# ``<header>`` / ``<footer>`` are NOT boilerplate per se.  Per the HTML spec
# they are the site banner / contentinfo only when they hang directly off
# ``<body>``; nested inside ``<main>``, ``<article>`` or ``<section>`` they
# are the *section's own* header/footer — hero blocks, card titles, article
# bylines — i.e. real content.  Astro / Next / Nuxt component libraries lean
# heavily on this pattern (``<main><header class="hero">…</header></main>``),
# and stripping every ``<header>`` used to wipe whole pages to two words.
# This mirrors the rule ``_detect_link_position`` already applies to links.
_SECTIONING_TAGS: frozenset[str] = frozenset({"main", "article", "section"})

# Removed anywhere: they never carry indexable prose.
_ALWAYS_STRIP_TAGS: frozenset[str] = frozenset({"form", "nav", "aside"})

# Nunca se borran, pase lo que pase: son la pagina, no un bloque de la pagina.
# Una clase en el ``<body>`` describe el ESTADO de la pagina ("la barra de
# cookies esta abierta"), no que la pagina sea una barra de cookies: con
# `<body class="cookie-bar-active">` el contenido salia a 0 palabras. Igual con
# `has-lightbox`, `newsletter-popup-open`, `subscriptions-page` y `signup-page`.
_TAGS_INTOCABLES: frozenset[str] = frozenset({"html", "body", "main", "article"})

# Un bloque de plantilla no es la mitad de la pagina. Una barra de cookies, un
# aviso de newsletter o un chat son una fraccion pequena; cuando el bloque que
# casa por nombre se lleva mas que esto, lo que casa es la pagina misma:
# `privacy-notice-content` ES el cuerpo de la pagina de privacidad,
# `subscription-plans` son los precios de un SaaS, `newsletter-archive` es el
# archivo que da sentido a la URL. Borrarlos deja la pagina en blanco justo en
# las paginas legales, que son las que tienen que estar indexadas tal cual.
_MAX_SHARE_PLANTILLA = 0.4

# Never content, whatever their position.
# ``<video>``/``<audio>``/``<canvas>`` inner text is browser fallback copy
# ("Your browser does not support video") -- never rendered, never content.
_NON_CONTENT_TAGS: frozenset[str] = frozenset({
    "script", "style", "noscript", "template", "svg", "iframe",
    "video", "audio", "canvas",
})

# ARIA landmark roles that mark template regions regardless of tag.
_TEMPLATE_ROLES: frozenset[str] = frozenset({
    "banner", "contentinfo", "navigation", "complementary",
    "dialog", "alertdialog",
})

# A page-level <header> that holds the page's <h1> AND a real paragraph is a
# hero block, not a site banner (site banners never carry prose).
_HERO_MIN_PARAGRAPH_WORDS = 20


# How many previously kept lines a new line is compared against when
# collapsing repeats.  Marquees / letter-shuffle animations / mobile-desktop
# clones repeat the same short line 2-12 times, sometimes alternating with
# a second one (A B A B …); a small window catches both without touching
# legitimately repeated text further apart.
_DEDUPE_WINDOW = 4

# Below this many words in the main container there is nothing to compare
# against — trust trafilatura.
_FALLBACK_MIN_CONTAINER_WORDS = 30
# If trafilatura keeps less than this share of the container's words it is
# discarding real content (cards, grids, accordions, hero blocks, contact
# details).  Recall is what content_text is for (audits, RAG), so the bar
# is deliberately high; a full article keeps ~0.85-0.95.
_FALLBACK_MIN_SHARE = 0.7

_REPEATED_MD_IMAGE = re.compile(r"(!\[[^\]]*\]\([^)\s]+\))(?:\s*\1)+")


def _remove_keep_tail(el) -> None:
    """Detach *el* from its parent WITHOUT losing its tail text.

    lxml's ``parent.remove(el)`` also drops ``el.tail`` — the text that
    follows the element up to the next sibling.  For
    ``<p>Hello <a class="share-button">x</a> world</p>`` that silently
    deletes " world".  Move the tail to the previous sibling (or the
    parent's text) before removing.
    """
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    if tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(el)


_PROSA_MIN_PARRAFOS = 5
_PROSA_MIN_PALABRAS = 20


def _tiene_prosa(el) -> bool:
    """True si el elemento contiene al menos 5 <p> de 20+ palabras."""
    n = 0
    for p in el.iter("p"):
        if contar_palabras(p.text_content()) >= _PROSA_MIN_PALABRAS:
            n += 1
            if n >= _PROSA_MIN_PARRAFOS:
                return True
    return False


def _es_intocable(el) -> bool:
    """True si el elemento es la pagina entera y no un bloque suyo."""
    return isinstance(el.tag, str) and el.tag in _TAGS_INTOCABLES


def _palabras_visibles(el) -> int:
    """Palabras del texto que se pinta dentro del elemento."""
    return contar_palabras("\n".join(_lineas_visibles(el)))


def _is_page_level_landmark(el) -> bool:
    """True when a <header>/<footer> is the site banner / contentinfo.

    Explicit ``role="banner"`` / ``role="contentinfo"`` always wins;
    otherwise the element is page-level only when no ``<main>``,
    ``<article>`` or ``<section>`` encloses it.
    """
    role = (el.get("role") or "").strip().lower()
    if role in ("banner", "contentinfo"):
        return True
    for anc in el.iterancestors():
        if isinstance(anc.tag, str) and anc.tag in _SECTIONING_TAGS:
            return False
    return True


def _looks_like_hero(header_el) -> bool:
    """A page-level <header> that carries the <h1> plus a real paragraph."""
    if header_el.find(".//h1") is None:
        return False
    for p in header_el.iter("p"):
        if contar_palabras(p.text_content()) >= _HERO_MIN_PARAGRAPH_WORDS:
            return True
    return False


def _dialog_es_plantilla(el, patrones: list[str]) -> bool:
    """True si un `<dialog>` cerrado es interfaz (consentimiento, login, promo).

    El `<dialog>` dejo de ser plantilla por etiqueta (#27): un FAQ en modal o
    una guia de tallas estan en el HTML y Google los indexa. Lo que sigue
    fuera se reconoce por lo mismo que el resto de la plantilla (decision 45):

    - el nombre casa con las listas de plantilla y promo, o con las de
      interfaz propias de un modal (``_DIALOG_UI_PATTERNS``);
    - el texto lleva una frase de reclamo o de consentimiento
      (``_PROMO_TEXT_PHRASES``). En un `<p>` suelto exigimos que el bloque SEA
      la frase (``_PROMO_TEXT_MAX_FACTOR``), porque un parrafo puede
      mencionar las cookies; un MODAL que dice «aceptar cookies» es el de
      cookies aunque el texto legal que lo acompane sea largo, y es justo el
      caso que el filtro por parrafo no caza;
    - lleva campos de formulario: un modal con `<input>` es el login, la
      newsletter o el buscador. Un modal de contenido se lee, no se rellena.
    """
    nombre = ((el.get("id") or "") + " " + (el.get("class") or "")).lower()
    if any(p in nombre for p in patrones) or any(
        p in nombre for p in _DIALOG_UI_PATTERNS
    ):
        return True
    texto = el.text_content().lower()
    if any(frase in texto for frase in _PROMO_TEXT_PHRASES):
        return True
    for campo in el.iter("input", "select", "textarea"):
        if (campo.get("type") or "").strip().lower() != "hidden":
            return True
    return False


def _strip_boilerplate_html(
    html: str,
    *,
    strip_promo: bool = True,
    extra_selectors: list[str] | None = None,
) -> str:
    """Remove cookie banners, chat widgets, overlays, and template regions
    (form, nav, aside, page-level header/footer, ARIA landmarks) from raw
    HTML.

    *extra_selectors* are per-job CSS selectors (from
    ``extraction.custom_boilerplate_selectors``) so site-specific noise
    can be stripped via job config without hardcoding it here.

    Header/footer handling is landmark-aware: only the site banner and
    contentinfo are removed; a ``<header>`` inside ``<main>``/``<article>``/
    ``<section>`` (hero, card title, byline) is kept as content, and so is
    a body-level ``<header>`` that holds the ``<h1>`` and a paragraph.

    Every removal preserves the element's tail text.  The cleaned HTML is
    suitable for passing to trafilatura or html2text.
    """
    try:
        from lxml import html as lxml_html
        from lxml.cssselect import CSSSelector

        doc = lxml_html.fromstring(html)

        # 1) Remove by exact CSS selector
        css_selectors = list(_BOILERPLATE_CSS_SELECTORS)
        if extra_selectors:
            css_selectors += list(extra_selectors)
        # El total de palabras visibles se mide UNA vez, sobre el documento
        # entero y antes de borrar nada: es el denominador de la fraccion que
        # decide si un bloque es plantilla o es la pagina.
        total_palabras = _palabras_visibles(doc)

        def es_la_pagina(el) -> bool:
            """True si el bloque se lleva una fraccion grande de la pagina."""
            if total_palabras <= 0:
                return False
            return _palabras_visibles(el) / total_palabras > _MAX_SHARE_PLANTILLA

        # 0) Fuera lo que el navegador no pinta, con la MISMA regla que usan
        #    word_count y text_ratio (`_nodo_oculto`). Hace falta hacerlo aqui
        #    y no solo al aplanar el texto porque trafilatura lee este HTML y no
        #    sabe nada de `hidden` ni de `d-none`: en un cliente real el
        #    megamenu entero (con sus volcados de depuracion) salia en el
        #    contenido de todas las paginas, hasta 7.117 palabras en una.
        doomed = [el for el in doc.iter()
                  if isinstance(el.tag, str) and not _es_intocable(el) and _nodo_oculto(el)]
        for el in doomed:
            _remove_keep_tail(el)

        for css in css_selectors:
            try:
                sel = CSSSelector(css)
                for el in list(sel(doc)):
                    if _es_intocable(el):
                        continue
                    _remove_keep_tail(el)
            except Exception:
                pass

        # 2) Remove by id/class substring pattern.  Collect first: mutating
        #    the tree while ``iter()`` walks it skips siblings.
        patterns = list(_BOILERPLATE_ID_CLASS_PATTERNS)
        if strip_promo:
            patterns += _PROMO_ID_CLASS_PATTERNS
        doomed = []
        for el in doc.iter():
            if _es_intocable(el):
                continue
            el_id = (el.get("id") or "").lower()
            el_class = (el.get("class") or "").lower()
            if not el_id and not el_class:
                continue
            for pattern in patterns:
                if pattern in el_id or pattern in el_class:
                    doomed.append(el)
                    break
        for el in doomed:
            # El nombre casa, pero si el bloque es la pagina lo que hay es un
            # falso positivo del nombre: `id="cookie-policy"` en la pagina de
            # politica de cookies, `privacy-notice-content` en la de privacidad.
            if es_la_pagina(el):
                continue
            _remove_keep_tail(el)

        # 2b) `<dialog>` cerrado: contenido salvo que se le note la plantilla.
        #     Lo cerrado esta en el DOM y Google lo indexa —el FAQ en modal, la
        #     tabla de tallas, las condiciones de envio—, igual que
        #     `hidden="until-found"` (decision 44). Se desenvuelve a <div>
        #     para que trafilatura no lo trate como una caja aparte.
        for el in list(doc.iter("dialog")):
            if _dialog_es_plantilla(el, patterns) and not es_la_pagina(el):
                _remove_keep_tail(el)
            else:
                el.tag = "div"
                el.attrib.pop("role", None)

        # 3) Remove non-content tags and template regions (landmark-aware).
        doomed = []
        for el in doc.iter():
            tag = el.tag
            if not isinstance(tag, str):
                continue
            if _es_intocable(el):
                continue
            role = (el.get("role") or "").strip().lower()
            if tag in _NON_CONTENT_TAGS or tag in _ALWAYS_STRIP_TAGS:
                # Un <nav> o <form> con varios parrafos largos no es plantilla:
                # es contenido que un editor pego con su HTML de origen (visto
                # en WordPress: el cuerpo entero de un post dentro de
                # <header class=site-header><nav>). Un menu real, aunque
                # tenga cientos de enlaces, no tiene parrafos de 20 palabras.
                if tag in ("nav", "form") and _tiene_prosa(el):
                    continue
                # En ASP.NET WebForms la pagina ENTERA va dentro de
                # <form id="aspnetForm">: quitar todo <form> dejaba esos sitios
                # a 0 palabras. Un formulario de verdad (buscador, contacto,
                # filtros) es una fraccion pequena de la pagina.
                if tag == "form" and es_la_pagina(el):
                    continue
                # Un <aside> DENTRO de main/article/section es el aside de esa
                # seccion —un destacado, una nota al margen, las
                # especificaciones de un producto—, no la barra lateral del
                # sitio. Es la misma regla que header/footer (decision 9) y la
                # que aplica _detect_link_position a los enlaces.
                if tag == "aside" and not _is_page_level_landmark(el):
                    # Y se desenvuelve a <div>: trafilatura tira todo <aside>
                    # por su cuenta, asi que dejarlo en pie no bastaba. Es el
                    # mismo arreglo que el <figcaption> con titulos de 4b.
                    el.tag = "div"
                    el.attrib.pop("role", None)
                    continue
                doomed.append(el)
            elif tag in ("header", "footer"):
                if _is_page_level_landmark(el) and not (
                    tag == "header" and _looks_like_hero(el)
                ):
                    doomed.append(el)
            elif role in _TEMPLATE_ROLES:
                # role="complementary" puesto sobre las especificaciones de un
                # producto dentro del articulo es contenido, igual que el
                # <aside> anidado.
                if role == "complementary" and not _is_page_level_landmark(el):
                    continue
                doomed.append(el)
        for el in doomed:
            _remove_keep_tail(el)

        # 4) <address> is content (NAP for local SEO) but trafilatura drops
        #    it; present it as a paragraph.
        for el in list(doc.iter("address")):
            el.tag = "p"

        # 4b) Un <figcaption> con titulos dentro no es el pie de una foto: es
        #     el hero de la pagina montado sobre la imagen de cabecera (patron
        #     <section class=landingpage><figure><figcaption><h1>). trafilatura
        #     tira los pies de figura, y con ellos se iba el h1 y la promesa
        #     principal de las landings. Se desenvuelve a div para que el texto
        #     cuente como contenido; el pie de foto de verdad (texto corto, sin
        #     titulos) se sigue tratando como pie.
        for el in list(doc.iter("figcaption")):
            tiene_titulo = any(
                True for _ in el.iter("h1", "h2", "h3")
            )
            if not tiene_titulo:
                continue
            el.tag = "div"
            padre = el.getparent()
            if padre is not None and padre.tag == "figure":
                padre.tag = "div"

        # 5) Remove small blocks by text phrase (catches bespoke-class
        #    CTA/legal/social widgets that id/class patterns miss).
        if strip_promo:
            doomed = []
            for el in doc.iter():
                if not isinstance(el.tag, str) or el.tag not in _PROMO_TEXT_TAGS:
                    continue
                text = el.text_content()
                if not text or len(text) > _PROMO_TEXT_MAX_LEN:
                    continue
                lower = text.lower()
                frase = next((p for p in _PROMO_TEXT_PHRASES if p in lower), None)
                if frase is None:
                    continue
                # El bloque tiene que SER el reclamo, no mencionarlo.
                limpio = _WHITESPACE.sub(" ", text).strip()
                if len(limpio) > _PROMO_TEXT_MAX_FACTOR * len(frase):
                    continue
                # Un bloque con titulos o con varios parrafos es un articulo
                # corto que CONTIENE el widget (el "Share on Mastodon" al pie
                # de un post de video de 50 palabras), no el widget en si. Se
                # llevaba el post entero: 554 paginas guardadas sin contenido
                # en una red de blogs. El widget de verdad no tiene h1-h3 ni
                # mas de dos parrafos.
                if any(True for _ in el.iter("h1", "h2", "h3")):
                    continue
                if sum(1 for _ in el.iter("p")) > 2:
                    continue
                if es_la_pagina(el):
                    continue
                doomed.append(el)
            for el in doomed:
                _remove_keep_tail(el)

        return lxml_html.tostring(doc, encoding="unicode")
    except Exception:
        return html  # on any failure, return original HTML unchanged


# Una pagina pasaba 5-6 veces por `_strip_boilerplate_html` con la misma entrada
# (contenido, markdown, contenedor principal, texto tras el h1...) y era lo que
# mas CPU gastaba del rastreo (#284). Se guarda el resultado de las ultimas
# paginas: es un `str`, asi que nadie lo puede modificar, y cada llamador sigue
# parseando su propia copia del arbol (`extract_texto_tras_h1` escribe en ella).
@functools.lru_cache(maxsize=4)
def _strip_de_pagina(html: str, strip_promo: bool, extra: tuple[str, ...]) -> str:
    return _strip_boilerplate_html(
        html, strip_promo=strip_promo, extra_selectors=list(extra) or None
    )


# Etiquetas que llevan DATOS, no prosa: una celda o un item repetido es un
# valor mas, no una repeticion que sobre. Deduplicarlas desalineaba las filas
# de una tabla comparativa (de 8 celdas `Si` quedaba 1) y atribuia el precio de
# una ficha a la ficha de al lado.
_TAGS_DATO: frozenset[str] = frozenset({"td", "th", "li", "dd", "dt", "option"})

# Cuantas lineas distintas puede haber en la ventana para que una repeticion se
# considere un ciclo. Una marquesina o una animacion de letras repite 1 o 2
# lineas una y otra vez; una tabla o un listado alternan muchas.
_DEDUPE_MAX_DISTINTAS = 2


def _dedupe_segmentos(segmentos: list[tuple[str, str | None]]) -> str:
    """Colapsa las repeticiones que son un ciclo, no las que son datos.

    Dos condiciones para tirar una linea repetida, y las dos salen de para que
    existe esto (decision 9: marquesinas, animaciones de letras y clones
    movil/escritorio):

    1. No puede venir de una celda ni de un item de lista (``_TAGS_DATO``).
    2. Entre la vez anterior y esta no hay mas de ``_DEDUPE_MAX_DISTINTAS``
       lineas distintas, es decir, lo que se repite es un ciclo corto
       (``A A A`` o ``A B A B``). Con la ventana de 4 a secas, una tabla con
       ``Si``/``No`` perdia 7 de cada 8 celdas y las filas quedaban
       desplazadas: el informe decia lo contrario de lo que pone la pagina.

    Las lineas en blanco se conservan (apretadas a una) para que los parrafos
    de Markdown sobrevivan. La comparacion normaliza espacios y mayusculas.
    """
    out: list[str] = []
    recientes: list[str] = []
    prev_blank = False
    for linea, tag in segmentos:
        if not linea:
            if out and not prev_blank:
                out.append("")
            prev_blank = True
            continue
        clave = _WHITESPACE.sub(" ", linea).casefold()
        if clave in recientes and tag not in _TAGS_DATO:
            # El ciclo es lo que va desde la ULTIMA aparicion hasta aqui: asi
            # `A B A B` se colapsa desde el primer rebote aunque antes hubiera
            # otras lineas en la ventana.
            ciclo = recientes[len(recientes) - 1 - recientes[::-1].index(clave):]
            if len(set(ciclo)) <= _DEDUPE_MAX_DISTINTAS:
                continue  # repeticion de ciclo: la linea en blanco sigue pendiente
        recientes.append(clave)
        if len(recientes) > _DEDUPE_WINDOW:
            recientes.pop(0)
        out.append(linea)
        prev_blank = False
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def _dedupe_lines(text: str) -> str:
    """``_dedupe_segmentos`` sobre texto suelto, sin etiquetas que mirar.

    Es la via de la salida de trafilatura, que ya viene aplanada. Ahi las
    tablas llegan como una fila por linea, asi que la regla del ciclo basta.
    """
    return _dedupe_segmentos([(raw.strip(), None) for raw in text.split("\n")])


def _block_text(el) -> str:
    """Flatten an lxml element to text, one line per block-level element.

    Keeps paragraph boundaries (so later consumers — dedupe, chunking for
    RAG, diffing — see structure) instead of the space-joined blob a bare
    ``//text()`` produces.  Repeated lines are collapsed.

    Comparte el recorrido con ``extract_visible_text``, asi que el contenido
    guardado y las metricas de texto ven lo mismo: ni el texto de respaldo de
    un ``iframe``, ni el ``title`` de un icono svg, ni lo que lleva
    ``hidden`` o un ``display:none`` en linea.
    """
    return _dedupe_segmentos(_segmentos_visibles(el))


# Por encima de esta fraccion de palabras dentro de enlaces, el bloque es
# navegacion y no un hero: un titular con su claim trae texto corrido y, como
# mucho, un boton; una barra superior con el logo en un `<h1 class="logo">` trae
# el megamenu entero. Sin esta comprobacion, el menu completo se antepone al
# contenido de la pagina (reproducido con `<div class="top-bar"><h1>Acme</h1>`).
_HERO_MAX_DENSIDAD_ENLACES = 0.5


def _densidad_de_enlaces(el) -> float:
    """Fraccion de las palabras del bloque que estan dentro de un ``<a>``."""
    total = contar_palabras(el.text_content())
    if not total:
        return 0.0
    en_enlaces = sum(contar_palabras(a.text_content()) for a in el.iter("a"))
    return en_enlaces / total


def _hero_outside_container(container) -> str | None:
    """Texto del bloque que contiene el <h1> cuando cae fuera del contenedor.

    Hay plantillas (landings con imagen de cabecera: el patron
    ``<section class=landingpage>`` + ``<main>`` como hermano) donde el h1 y la
    promesa principal de la pagina viven FUERA de ``<main>``. Tanto trafilatura
    como el fallback se ciñen al contenedor principal, asi que ese bloque
    desaparecia del contenido guardado: justo el titular y el claim, que es lo
    que mas pesa en una pagina comercial. Devuelve solo lo que no este ya en el
    contenido, para no duplicar.
    """
    if container is None:
        return None
    try:
        raiz = container.getroottree().getroot()
    except Exception:
        return None
    for h1 in raiz.iter("h1"):
        if container is h1 or container in h1.iterancestors():
            return None  # el h1 esta dentro del contenedor: nada que recuperar
        bloque = h1
        padre = bloque.getparent()
        while padre is not None and padre.tag not in ("body", "html"):
            bloque = padre
            padre = bloque.getparent()
        if bloque is container or container in bloque.iterdescendants():
            return None  # el bloque envuelve al contenedor: seria todo el texto
        if _densidad_de_enlaces(bloque) > _HERO_MAX_DENSIDAD_ENLACES:
            return None  # es la barra de navegacion, no el hero de la pagina
        texto = _block_text(bloque)
        return texto or None
    return None


def _prepend_hero(texto: str | None, hero: str | None) -> str | None:
    """Pega delante las lineas del hero que no esten ya en el contenido."""
    if not hero:
        return texto
    if not texto:
        return hero
    presentes = {l.strip().lower() for l in texto.splitlines() if l.strip()}
    nuevas = [l for l in hero.splitlines() if l.strip() and l.strip().lower() not in presentes]
    if not nuevas:
        return texto
    return "\n".join(nuevas) + "\n" + texto


# Al recuperar el bloque del <h1> se sube por los ancestros mientras el bloque
# siga siendo pequeno frente al contenedor: un hero son el titular y un par de
# lineas (categoria, fecha, claim), no el articulo entero.
_HERO_MAX_SHARE = 0.3
_HERO_MIN_WORDS_ABS = 60


def _hero_dentro_perdido(container, texto: str | None) -> str | None:
    """Texto del bloque del <h1> cuando esta DENTRO del contenedor y el
    extractor lo ha tirado igualmente.

    [[_hero_outside_container]] cubre el caso del h1 fuera de ``<main>``. Pero
    hay plantillas (``<main><article><div class=hero><h1>``) donde el hero SI
    esta dentro y trafilatura lo descarta por su pinta de cabecera. Si ademas
    conserva bastante del resto del contenedor, la comprobacion de share no se
    dispara y el titular de la pagina desaparece sin que nada lo avise. Se sube
    desde el h1 mientras el bloque siga siendo una fraccion pequena del
    contenedor, para arrastrar el subtitulo del hero pero no el articulo.
    """
    if container is None:
        return None
    h1 = next(container.iter("h1"), None)
    if h1 is None:
        return None
    titulo = _WHITESPACE.sub(" ", h1.text_content()).strip()
    if len(titulo) < 4:
        return None
    # Presente = el titular aparece como LINEA propia. Buscarlo como subcadena
    # da falsos positivos: en un texto largo la marca o el nombre del producto
    # reaparecen a media frase y el hero se daria por recuperado sin estarlo.
    if texto and any(
        _WHITESPACE.sub(" ", ln).strip().lower() == titulo.lower()
        for ln in texto.splitlines()
    ):
        return None
    tope = max(
        _HERO_MIN_WORDS_ABS,
        int(contar_palabras(container.text_content()) * _HERO_MAX_SHARE),
    )
    bloque = h1
    padre = bloque.getparent()
    while padre is not None and padre is not container:
        if contar_palabras(padre.text_content()) > tope:
            break
        bloque = padre
        padre = bloque.getparent()
    return _block_text(bloque) or None


# Tope del texto que sigue al <h1> y se guarda aparte (decision 187). En una
# ficha, el nombre, el precio, el PVPR, el stock y los descuentos por cantidad
# de Planeta Huerto son ~700 caracteres; las variantes con su stock de
# Tiendanimal, algo mas.
TRAS_H1_MAX = 1000
# Caracter de uso privado que marca el h1 al aplanar el documento.
_MARCA_H1 = "\ue000"


def extract_texto_tras_h1(
    selector,
    *,
    strip_promo: bool = True,
    extra_selectors: list[str] | None = None,
    max_chars: int = TRAS_H1_MAX,
) -> str | None:
    """El texto visible que sigue al primer ``<h1>`` en el orden de lectura,
    desde el propio h1 y hasta *max_chars*: en una ficha de producto, la caja
    de compra (nombre, precio, variantes, disponibilidad). Se guarda aparte del
    contenido principal (decision 187).

    El contenido principal lo decide trafilatura, que en las fichas se queda a
    veces con el bloque de recomendados y tira la caja de compra: en Planeta
    Huerto, 178 de 3.000 fichas se guardaban sin su propio precio, y Jev
    comparaba el marcado con el precio de otro producto. Se lee del documento
    entero ya limpio (la caja de compra puede caer fuera de ``<main>``), con
    el h1 del contenedor principal si lo tiene; si no, el primero.
    """
    raw_html = selector.get()
    if not raw_html:
        return None
    container = _main_container(
        raw_html, strip_promo=strip_promo, extra_selectors=extra_selectors
    )
    if container is None:
        return None
    raiz = container.getroottree().getroot()
    # El primer h1 con texto: el del contenedor principal si tiene; si no, el
    # del documento (un h1 que solo envuelve el logo no cuenta).
    h1 = next((h for h in container.iter("h1") if _block_text(h)), None)
    if h1 is None:
        h1 = next((h for h in raiz.iter("h1") if _block_text(h)), None)
    if h1 is None:
        return None
    # Se localiza por POSICION, con una marca dentro del h1 (el arbol es una
    # copia recien parseada): buscandolo por su texto se empezaba en la primera
    # linea igual, que puede ser otro bloque de antes con el mismo nombre y
    # otro precio (revision de la decision 187).
    # U+E000 es tambien el primer glifo de algunas fuentes de iconos: si la
    # pagina ya lo trae, se quita antes de marcar, o se empezaria en ese icono.
    for el in raiz.iter():
        if isinstance(el.tag, str):
            if el.text and _MARCA_H1 in el.text:
                el.text = el.text.replace(_MARCA_H1, "")
            if el.tail and _MARCA_H1 in el.tail:
                el.tail = el.tail.replace(_MARCA_H1, "")
    h1.text = _MARCA_H1 + (h1.text or "")
    cuerpo = next(raiz.iter("body"), raiz)
    lineas = _block_text(cuerpo).splitlines()
    desde = next((k for k, ln in enumerate(lineas) if _MARCA_H1 in ln), None)
    if desde is None:
        return None
    salida: list[str] = []
    largo = 0
    for ln in lineas[desde:]:
        ln = ln.replace(_MARCA_H1, "").strip()
        if not ln:
            continue
        if largo + len(ln) > max_chars:
            # La linea que no cabe entra hasta el ultimo espacio, si queda
            # sitio para algo con sentido.
            trozo = ln[:max(0, max_chars - largo)].rsplit(" ", 1)[0]
            if len(trozo) > 20:
                salida.append(trozo)
            break
        salida.append(ln)
        largo += len(ln) + 1
    return "\n".join(salida) or None


def _main_container(
    html: str, *, strip_promo: bool = True, extra_selectors: list[str] | None = None
):
    """Parse *html*, strip boilerplate on the WHOLE document, then return the
    main content container as an lxml element (or None).

    Stripping before selecting matters: a ``<header>`` inside ``<main>`` is
    only recognisable as content while its ``<main>`` ancestor is present.
    Prefers ``<main>``, ``<article>``, ``[role=main]``; falls back to body.
    """
    try:
        from lxml import html as lxml_html

        stripped = _strip_de_pagina(html, strip_promo, tuple(extra_selectors or ()))
        doc = lxml_html.fromstring(stripped)
        for css in ("main", "article", "[role='main']"):
            found = doc.cssselect(css)
            if found:
                return found[0]
        body = doc.cssselect("body")
        return body[0] if body else doc
    except Exception:
        return None


def _trafilatura_extract(
    html: str,
    *,
    strip_promo: bool = True,
    extra_selectors: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """Use trafilatura to extract clean text and markdown from raw HTML.

    Before running trafilatura, strips known boilerplate elements (cookie
    banners, chat widgets, consent overlays) via ``_strip_boilerplate_html``
    so they never pollute the extracted content.

    Trafilatura uses heuristics (text density, DOM structure, link ratio)
    to isolate editorial content — works on any website without
    site-specific selectors.

    Returns (plain_text, markdown) — either may be None.

    El texto y el markdown los piden por separado ``extract_main_content`` y
    ``extract_main_content_markdown`` sobre la misma pagina: el resultado se
    guarda para que trafilatura no corra cuatro veces (#284).
    """
    try:
        return _trafilatura_de_pagina(html, strip_promo, tuple(extra_selectors or ()))
    except Exception:
        return (None, None)


@functools.lru_cache(maxsize=4)
def _trafilatura_de_pagina(
    html: str, strip_promo: bool, extra: tuple[str, ...]
) -> tuple[str | None, str | None]:
    try:
        import trafilatura

        clean = _strip_de_pagina(html, strip_promo, extra)

        text = trafilatura.extract(
            clean,
            include_links=False,
            include_images=False,
            include_tables=True,
            include_comments=False,
            include_formatting=False,
            favor_recall=True,
        )

        md = trafilatura.extract(
            clean,
            include_links=True,
            include_images=True,
            include_tables=True,
            include_comments=False,
            include_formatting=True,
            output_format="markdown",
            favor_recall=True,
        )

        return (text or None, md or None)
    except Exception:
        return (None, None)


def _fallback_extract_text(
    selector, *, strip_promo: bool = True, extra_selectors: list[str] | None = None
) -> str | None:
    """Fallback text extraction when trafilatura returns nothing or too little.

    Strips boilerplate on the whole document (landmark-aware, per-job
    selectors included), takes ``<main>`` / ``<article>`` / ``[role=main]``
    (else body) and flattens it block by block with repeats collapsed.
    """
    raw_html = selector.get()
    if not raw_html:
        return None
    container = _main_container(
        raw_html, strip_promo=strip_promo, extra_selectors=extra_selectors
    )
    if container is None:
        return None
    text = _block_text(container)
    return text or None


def _should_fall_back(candidate: str | None, reference_words: int) -> bool:
    """Trafilatura kept too small a share of the main container's words."""
    if reference_words < _FALLBACK_MIN_CONTAINER_WORDS:
        return False
    kept = contar_palabras(candidate)
    return kept < reference_words * _FALLBACK_MIN_SHARE


# ---------------------------------------------------------------------------
# Indexability analysis
# ---------------------------------------------------------------------------

def _con_hero(texto: str | None, container) -> str | None:
    """Pega delante el hero interior si el extractor se lo ha dejado fuera."""
    return _prepend_hero(texto, _hero_dentro_perdido(container, texto))


def extract_main_content(
    selector,
    *,
    word_count: int | None = None,
    strip_promo: bool = True,
    extra_selectors: list[str] | None = None,
) -> str | None:
    """Extract the main textual content of a page.

    Uses trafilatura for site-agnostic content extraction, then checks the
    result against the words actually present in the (boilerplate-stripped)
    main container.  When trafilatura kept less than half of them it is
    discarding real content — hub/landing pages with cards, grids,
    accordions, hero blocks — and the block-flattened container text is
    used instead.

    *word_count* (whole-body visible words) is accepted for backwards
    compatibility but no longer drives the decision: it counts nav and
    footer text too, so it over-estimated the reference and let
    two-word extractions through on pages whose content sat in a hero.
    """
    raw_html = selector.get()
    if not raw_html:
        return None

    text, _ = _trafilatura_extract(
        raw_html, strip_promo=strip_promo, extra_selectors=extra_selectors
    )
    if text:
        text = _dedupe_lines(text)

    fallback = _fallback_extract_text(
        selector, strip_promo=strip_promo, extra_selectors=extra_selectors
    )
    container = _main_container(
        raw_html, strip_promo=strip_promo, extra_selectors=extra_selectors
    )
    hero = _hero_outside_container(container)
    if not text:
        return _con_hero(_prepend_hero(fallback, hero), container)

    reference_words = contar_palabras(fallback)
    if fallback and _should_fall_back(text, reference_words) and len(fallback) > len(text):
        return _con_hero(_prepend_hero(fallback, hero), container)
    return _con_hero(_prepend_hero(text, hero), container)


def _get_main_container_html(
    selector, *, strip_promo: bool = True, extra_selectors: list[str] | None = None
) -> str | None:
    """Return the HTML of the main content container after boilerplate
    stripping.  Used only as fallback for markdown extraction."""
    raw_html = selector.get()
    if not raw_html:
        return None
    container = _main_container(
        raw_html, strip_promo=strip_promo, extra_selectors=extra_selectors
    )
    if container is None:
        return None
    try:
        from lxml import html as lxml_html

        html = lxml_html.tostring(container, encoding="unicode")
    except Exception:
        return None
    if not html or not html.strip():
        return None
    return html


def _fallback_extract_markdown(
    selector, *, strip_promo: bool = True, extra_selectors: list[str] | None = None
) -> str | None:
    """Fallback markdown extraction via html2text on the main container."""
    container_html = _get_main_container_html(
        selector, strip_promo=strip_promo, extra_selectors=extra_selectors
    )
    if not container_html:
        return None
    try:
        import html2text

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = False
        h.ignore_emphasis = False
        h.body_width = 0
        h.skip_internal_links = False
        h.inline_links = True
        h.protect_links = True
        h.ignore_tables = False
        h.single_line_break = False

        md = h.handle(container_html)
        md = _REPEATED_MD_IMAGE.sub(r"\1", md)
        md = _dedupe_lines(re.sub(r'\n{3,}', '\n\n', md).strip())
        return md or None
    except Exception:
        return None


def extract_main_content_markdown(
    selector,
    *,
    word_count: int | None = None,
    strip_promo: bool = True,
    extra_selectors: list[str] | None = None,
) -> str | None:
    """Extract the main content as clean Markdown.

    Uses trafilatura's markdown output for site-agnostic extraction.
    Like ``extract_main_content``, measures the trafilatura pass against
    the words in the stripped main container and falls back to html2text
    when trafilatura is too aggressive.
    """
    raw_html = selector.get()
    if not raw_html:
        return None

    text, md = _trafilatura_extract(
        raw_html, strip_promo=strip_promo, extra_selectors=extra_selectors
    )
    if md:
        md = _dedupe_lines(md)

    if not md:
        return _fallback_extract_markdown(
            selector, strip_promo=strip_promo, extra_selectors=extra_selectors
        )

    # Decide with the plain-text pass (markdown syntax inflates word counts)
    # so text and markdown fall back together.
    container = _main_container(
        raw_html, strip_promo=strip_promo, extra_selectors=extra_selectors
    )
    reference_words = contar_palabras(_block_text(container)) if container is not None else 0
    hero = _hero_outside_container(container)
    if _should_fall_back(_dedupe_lines(text) if text else None, reference_words):
        fallback_md = _fallback_extract_markdown(
            selector, strip_promo=strip_promo, extra_selectors=extra_selectors
        )
        if fallback_md and len(fallback_md) > len(md):
            return _con_hero(_prepend_hero(fallback_md, hero), container)

    return _con_hero(_prepend_hero(md, hero), container)


# ---------------------------------------------------------------------------
# Extraccion y busqueda a medida (#34, decision 104)
# ---------------------------------------------------------------------------
def extract_custom(selector, html: str, comp, **kw) -> list[dict]:
    """Las filas de las reglas a medida de una pagina.

    Envuelve `shared.extraccion_a_medida.aplicar`, que es la que usa tambien la
    prueba de reglas de la API: la logica vive en `shared/` para que las dos den
    lo mismo. `kw`: `texto`, `contenido`, `ruta`, `titulo`, `render`,
    `presupuesto` (ver `aplicar`).
    """
    from shared.extraccion_a_medida import aplicar

    raiz = getattr(selector, "root", None)
    if raiz is not None and not hasattr(raiz, "tag"):
        raiz = None
    return aplicar(raiz, html, comp, **kw)
