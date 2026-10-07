"""
Pure extraction helpers -- no Scrapy imports.

Every public function receives either a ``parsel.Selector`` or raw HTML
bytes/str and returns plain Python data structures.  This keeps the
extraction logic testable without bringing in a full Scrapy response.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

from w3lib.url import canonicalize_url

# Las reglas robots viven en `shared` porque el analyzer, cuya imagen no
# copia `crawler/`, necesita leerlas igual. Se reexportan aqui para no
# romper a quien ya importaba `extractors.robots_tokens`.
from shared.robots import (  # noqa: F401
    robots_bad_separators,
    robots_tokens,
)

# ---- regex helpers ---------------------------------------------------------
_WHITESPACE = re.compile(r"\s+")

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
    """Safely parse an integer from an HTML attribute value."""
    if not value:
        return None
    cleaned = value.strip().rstrip("px%").strip()
    try:
        return int(cleaned)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------

def compute_url_hash(url: str) -> str:
    """Return the hex SHA-256 of the canonicalized URL."""
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


_DEFAULT_PORTS = {"http": 80, "https": 443}


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
    if (port is not None and port == _DEFAULT_PORTS.get(parts.scheme)) or host.endswith("."):
        userinfo = netloc.rpartition("@")[0]
        host_limpio = host.rstrip(".")
        if ":" in host_limpio:  # IPv6
            host_limpio = f"[{host_limpio}]"
        netloc = host_limpio
        if port is not None and port != _DEFAULT_PORTS.get(parts.scheme):
            netloc += f":{port}"
        if userinfo:
            netloc = f"{userinfo}@{netloc}"
    path = _remove_dot_segments(parts.path)
    if netloc == parts.netloc and path == parts.path:
        return canon
    return urlunsplit((parts.scheme, netloc, path, parts.query, parts.fragment))


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
    path = urlparse(url).path.lower()
    ext_map = {
        ".html": "html", ".htm": "html",
        ".css": "css",
        ".js": "js", ".mjs": "js",
        ".jpg": "image", ".jpeg": "image", ".png": "image",
        ".gif": "image", ".svg": "image", ".webp": "image", ".ico": "image",
        ".pdf": "pdf",
        ".woff": "font", ".woff2": "font", ".ttf": "font", ".eot": "font",
    }
    for ext, rtype in ext_map.items():
        if path.endswith(ext):
            return rtype

    return "other"


def is_internal_url(url: str, allowed_hosts: set[str]) -> bool:
    """Check whether *url* belongs to one of the *allowed_hosts*."""
    try:
        host = normalize_host(urlparse(url).hostname)
    except ValueError:
        return False
    if not host:
        return False
    # Strip leading www. for comparison
    bare = host.removeprefix("www.")
    return bare in allowed_hosts or host in allowed_hosts


def effective_base_url(selector, page_url: str) -> str:
    """Return the base URL used to resolve relative links on this page.

    Honours a ``<base href>`` element when present (resolved against the
    page URL), matching how browsers -- and Screaming Frog -- resolve
    relative URLs.  Falls back to *page_url* when no usable base tag
    exists, so callers can always pass the result straight to ``urljoin``.
    """
    try:
        base_href = selector.css("base[href]::attr(href)").get()
    except Exception:
        base_href = None
    if base_href and base_href.strip():
        try:
            return urljoin(page_url, base_href.strip())
        except Exception:
            return page_url
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


def rel_tokens(rel: str | None) -> set[str]:
    """Tokens de un atributo `rel`, en minusculas.

    La sintaxis es por espacios, pero hay plantillas que escriben
    `rel="nofollow,noopener"`: separando solo por espacios salia un unico token
    `nofollow,noopener` y el enlace contaba como follow.
    """
    if not rel:
        return set()
    return {t for t in re.split(r"[\s,]+", rel.lower()) if t}


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------

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
    def _meta_all(name: str) -> list[str]:
        """All content="" values of <meta name|property="...">, matching the
        attribute value case-insensitively (real-world markup uses
        ``name="Description"``, ``name="ROBOTS"``, etc. — Screaming Frog and
        Google both match these)."""
        vals = selector.xpath(
            f"//meta[translate(@name, '{_XP_UPPER}', '{_XP_LOWER}') = $n"
            f" or translate(@property, '{_XP_UPPER}', '{_XP_LOWER}') = $n]/@content",
            n=name.lower(),
        ).getall()
        return [v for v in (_clean(x) for x in vals) if v]

    def _meta(name: str) -> str | None:
        vals = _meta_all(name)
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

    # Robots: a page may carry SEVERAL robots meta tags; directives combine
    # and the most restrictive wins downstream, so join all of them.
    robots_vals = _meta_all("robots")
    meta_robots = ", ".join(robots_vals) if robots_vals else None

    # Robots dirigido a un bot concreto: `<meta name="googlebot" content="noindex">`
    # saca la pagina del indice de Google aunque el `robots` generico diga
    # `index`. Antes solo se leia el generico, asi que ese noindex no contaba.
    googlebot_vals = _meta_all("googlebot")
    meta_robots_googlebot = ", ".join(googlebot_vals) if googlebot_vals else None

    canonical = _resolve(base_url, _clean(selector.css('link[rel="canonical"]::attr(href)').get()))

    return {
        "title": title_text,
        "title_len": len(title_text) if title_text else None,
        "meta_description": desc,
        "meta_description_len": len(desc) if desc else None,
        "meta_keywords": _meta("keywords"),
        "meta_robots": meta_robots,
        "meta_robots_googlebot": meta_robots_googlebot,
        "canonical_href": canonical,
        # OG
        "og_title": _meta("og:title"),
        "og_description": _meta("og:description"),
        "og_image": _resolve(base_url, _meta("og:image")),
        "og_url": _resolve(base_url, _meta("og:url")),
        "og_type": _meta("og:type"),
        # Twitter
        "twitter_card": _meta("twitter:card"),
        "twitter_title": _meta("twitter:title"),
        "twitter_description": _meta("twitter:description"),
        # Pagination
        "rel_next": _resolve(
            base_url, _clean(selector.css('link[rel="next"]::attr(href)').get())
        ),
        "rel_prev": _resolve(
            base_url, _clean(selector.css('link[rel="prev"]::attr(href)').get())
        ),
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

    for a in selector.xpath(
        f"descendant-or-self::a[@href]{_XPATH_FUERA_DEL_DOM}"
        f" | descendant-or-self::area[@href]{_XPATH_FUERA_DEL_DOM}"
    ):
        raw_href = a.attrib.get("href", "").strip()
        if not raw_href or raw_href.startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
            continue

        normalized = absolutize_url(base_url, raw_href)
        if normalized is None:
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
        link_position = _detect_link_position(a)

        # Target attribute (_blank, _self, _parent, _top, or custom)
        target = _clean(a.attrib.get("target", ""))

        # Alt text from child <img> elements (useful for image links)
        child_imgs = a.css("img")
        alt_text_parts: list[str] = []
        for img in child_imgs:
            alt = img.attrib.get("alt", "")
            if alt and alt.strip():
                alt_text_parts.append(alt.strip())
        alt_text = _clean(" ".join(alt_text_parts)) if alt_text_parts else None

        # Follow: True unless rel contains "nofollow" or the page itself is
        # marked nofollow (meta robots / X-Robots-Tag).
        follow = "nofollow" not in rel_tokens(rel) and not page_nofollow

        # Link type classification
        has_child_imgs = len(child_imgs) > 0
        has_text = bool(anchor_text)
        if has_child_imgs and not has_text:
            link_type = "image"
        elif has_child_imgs and has_text:
            link_type = "image_text"
        else:
            link_type = "hyperlink"

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


# Class/id tokens that reveal a template region. Matched as WHOLE tokens
# (split on non-alphanumerics), never as substrings: "canvas" must not read
# as nav, "elementor-widget" must not read as sidebar.
_POSITION_TOKENS: dict[str, frozenset[str]] = {
    "nav": frozenset({"nav", "navbar", "navigation", "menubar", "breadcrumb", "breadcrumbs"}),
    "footer": frozenset({"footer", "colophon"}),
    "header": frozenset({"header", "masthead", "topbar"}),
    "sidebar": frozenset({"sidebar", "aside"}),
}
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


def _classify_node(node) -> tuple[str | None, bool]:
    """
    Classify one ancestor element.

    Returns ``(kind, structural)`` where ``kind`` is a link position
    (``nav``/``header``/``footer``/``sidebar``/``content``), ``"content+"``
    for a *strong* content marker, or ``None`` when the element says
    nothing. ``structural`` is True when the verdict comes from the tag
    name or an ARIA landmark role rather than from class/id hints.
    """
    tag = (node.xpath("name()").get() or "").lower()
    if tag in ("nav", "header", "footer"):
        return tag, True
    if tag == "aside":
        return "sidebar", True
    if tag in ("main", "article"):
        return "content+", True

    role = (node.attrib.get("role", "") or "").strip().lower()
    if role in _POSITION_ROLES:
        kind = _POSITION_ROLES[role]
        return ("content+" if kind == "content" else kind), True

    raw_cls = node.attrib.get("class", "") or ""
    raw_id = (node.attrib.get("id", "") or "").strip()
    cls, node_id = raw_cls.lower(), raw_id.lower()
    tokens = _hint_tokens(raw_cls + " " + raw_id)
    for kind, words in _POSITION_TOKENS.items():
        if tokens & words:
            return kind, False

    class_tokens = set(cls.split())
    if class_tokens & _CONTENT_STRONG:
        return "content+", False
    if class_tokens & _CONTENT_WEAK or node_id in _CONTENT_IDS:
        return "content", False
    return None, False


def _detect_link_position(a_selector) -> str:
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
    """
    ancestors = [
        n for n in a_selector.xpath("ancestor::*")
        if (n.xpath("name()").get() or "").lower() not in ("html", "body")
    ]
    kinds = [_classify_node(n) for n in reversed(ancestors)]  # nearest-first

    for i, (kind, _structural) in enumerate(kinds):
        if kind is None:
            continue
        outer = kinds[i + 1:]
        if kind in ("nav", "sidebar"):
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
    for link in selector.css('link[rel="alternate"][hreflang]'):
        lang = _clean(link.attrib.get("hreflang", ""))
        href = _resolve(base_url, _clean(link.attrib.get("href", "")))
        if lang and href:
            results.append({"lang": lang, "href": href})
    return results


def extract_structured_data(html_body: str, url: str = "") -> list[dict[str, Any]]:
    """
    Extract JSON-LD, Microdata, and RDFa using *extruct*.

    Returns a list of dicts with: raw, format, schema_type.
    """
    results: list[dict[str, Any]] = []

    try:
        import extruct

        data = extruct.extract(
            html_body,
            base_url=url,
            syntaxes=["json-ld", "microdata", "rdfa"],
            uniform=True,
        )
    except Exception:
        return results

    for fmt, items in data.items():
        if not isinstance(items, list):
            continue
        for item in items:
            schema_type = None
            if isinstance(item, dict):
                schema_type = item.get("@type")
                if isinstance(schema_type, list):
                    schema_type = ", ".join(str(t) for t in schema_type)
                elif schema_type is not None:
                    schema_type = str(schema_type)
            # RDFa items without a schema type are xhtml/ARIA role
            # annotations (tabs, dialogs, buttons) — noise on any site.
            if fmt == "rdfa" and not schema_type:
                continue
            results.append({
                "raw": item,
                "format": fmt.replace("-", ""),  # jsonld, microdata, rdfa
                "schema_type": schema_type,
            })

    return results


def extract_resources(selector, base_url: str) -> list[dict[str, Any]]:
    """
    Collect references to images, scripts, stylesheets, and other assets.

    Returns a list of dicts with: url, resource_type, alt_text, width,
    height, is_mixed_content.
    """
    is_https = base_url.startswith("https://")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(
        raw_url: str,
        rtype: str,
        alt: str | None = None,
        width: str | None = None,
        height: str | None = None,
    ):
        if not raw_url:
            return
        try:
            absolute = urljoin(base_url, raw_url.strip())
            normalized = normalize_url(absolute)
        except ValueError:
            return
        if normalized in seen:
            return
        seen.add(normalized)

        mixed = is_https and absolute.startswith("http://")
        # OJO: alt="" y alt ausente NO son lo mismo. Un alt vacio marca la
        # imagen como decorativa, que es lo CORRECTO segun WCAG para iconos y
        # adornos; que falte el atributo si es un fallo. _clean("") devuelve
        # None y colapsaba ambos casos, de modo que el analyzer reportaba como
        # "sin alt" miles de imagenes correctamente marcadas como decorativas.
        # Se conserva la cadena vacia tal cual para poder distinguirlos.
        alt_text = "" if (alt is not None and not alt.strip()) else _clean(alt)
        results.append({
            "url": normalized,
            "resource_type": rtype,
            "alt_text": alt_text,
            "width": _parse_int(width),
            "height": _parse_int(height),
            "is_mixed_content": mixed,
        })

    # Images
    for img in selector.xpath(f"descendant-or-self::img[@src]{_XPATH_FUERA_DEL_DOM}"):
        _add(
            img.attrib.get("src", ""),
            "image",
            img.attrib.get("alt"),
            img.attrib.get("width"),
            img.attrib.get("height"),
        )

    # Stylesheets
    for link in selector.css('link[rel="stylesheet"][href]'):
        _add(link.attrib.get("href", ""), "css")

    # Scripts
    for script in selector.css("script[src]"):
        _add(script.attrib.get("src", ""), "js")

    # srcset images (first URL only per element for simplicity)
    for img in selector.xpath(f"descendant-or-self::*[@srcset]{_XPATH_FUERA_DEL_DOM}"):
        srcset = img.attrib.get("srcset", "")
        # srcset=" " o "," no tiene candidatos: split()[0] lanzaba IndexError
        # y se perdia la extraccion de toda la pagina.
        candidato = srcset.split(",")[0].split()
        first_url = candidato[0] if candidato else ""
        _add(
            first_url,
            "image",
            img.attrib.get("alt"),
            img.attrib.get("width"),
            img.attrib.get("height"),
        )

    return results


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


# Etiquetas cuyo texto el navegador no pinta nunca. ``template`` importa
# especialmente: los frameworks meten dentro un DOM alternativo completo, que
# duplicaba el recuento. ``title`` y ``desc`` dentro del body solo aparecen
# dentro de un ``svg`` (son el tooltip del icono). ``iframe`` solo contiene el
# texto de respaldo para navegadores sin soporte.
_TAGS_NO_RENDERIZADOS: frozenset[str] = frozenset({
    "script", "style", "noscript", "template", "iframe", "title", "desc",
})


# Clases de utilidad cuyo significado lo fija el framework, no el sitio:
# `d-none` es `display:none !important` en todas las versiones de Bootstrap,
# `hidden` lo mismo en Tailwind, `is-hidden` en Bulma, `invisible` es
# `visibility:hidden` en los dos. Se casan por TOKEN completo para no tocar las
# variantes por anchura (`d-md-none` oculta solo a partir de md). Lo demas que
# oculte desde una hoja de estilos necesita el render y no se puede saber aqui.
_CLASES_OCULTAS: frozenset[str] = frozenset({
    "d-none", "hidden", "hide", "is-hidden", "invisible",
})


def _nodo_oculto(node) -> bool:
    """True si el navegador no pinta el nodo (etiqueta, ``hidden``, clase o estilo).

    Ve el ``display:none`` escrito en linea y las clases de utilidad de los
    frameworks (``_CLASES_OCULTAS``); el que viene de una hoja de estilos propia
    necesitaria el render. Es un subconjunto, pero es el que usan los
    acordeones, los menus moviles y los bloques de depuracion.
    ``hidden="until-found"`` SI es contenido: el navegador lo revela al buscar
    en la pagina, y Google lo indexa.
    """
    if node.tag in _TAGS_NO_RENDERIZADOS:
        return True
    oculto = node.get("hidden")
    if oculto is not None and oculto.strip().lower() != "until-found":
        return True
    clase = node.get("class")
    if clase and not _CLASES_OCULTAS.isdisjoint(clase.lower().split()):
        return True
    estilo = node.get("style")
    if estilo:
        comprimido = _WHITESPACE.sub("", estilo).lower()
        if "display:none" in comprimido or "visibility:hidden" in comprimido:
            return True
    return False


def _segmentos_visibles(el, forzar_raiz: bool = False) -> list[tuple[str, str | None]]:
    """Aplana un elemento lxml a (linea, etiqueta del bloque que la abre).

    Mantener las fronteras de bloque es lo que distingue ``Zapa<span>tillas``
    (una palabra) de ``<p>Precio<p>Oferta`` (dos lineas): un ``//text()`` pelado
    unido con espacios partia la primera en dos palabras y pegaba la segunda en
    una sola linea. Los espacios de indentacion se colapsan, asi que el texto
    que sale es el que se lee, no el sangrado de la plantilla.

    La etiqueta viaja con la linea porque una celda de tabla y un titular
    repetido no se tratan igual al deduplicar (ver ``_dedupe_segmentos``).
    """
    partes: list[tuple[str, str | None]] = []

    def anda(node, con_cola: bool, tag_padre: str | None, es_raiz: bool = False) -> None:
        if not isinstance(node.tag, str):  # comentario / PI
            if con_cola and node.tail:
                partes.append((node.tail, None))
            return
        if _nodo_oculto(node) and not (es_raiz and forzar_raiz):
            # El nodo no se pinta, pero su cola va DESPUES de el y si se pinta.
            if con_cola and node.tail:
                partes.append((node.tail, None))
            return
        bloque = node.tag in _BLOCK_TAGS
        if bloque:
            partes.append(("\n", node.tag))
        if node.text:
            partes.append((node.text, None))
        hijo_tag = node.tag if bloque else tag_padre
        for hijo in node:
            anda(hijo, True, hijo_tag)
        if bloque:
            # Al cerrar el bloque, lo que venga despues pertenece al padre.
            partes.append(("\n", tag_padre))
        if con_cola and node.tail:
            partes.append((node.tail, None))

    anda(el, False, None, es_raiz=True)

    salida: list[tuple[str, str | None]] = []
    acumulado: list[str] = []
    tag_actual: str | None = None

    def volcar() -> None:
        linea = _WHITESPACE.sub(" ", "".join(acumulado)).strip()
        acumulado.clear()
        if linea:
            salida.append((linea, tag_actual))

    for texto, tag in partes:
        if texto == "\n":
            volcar()
            tag_actual = tag
        else:
            acumulado.append(texto)
    volcar()
    return salida


def _lineas_visibles(el, forzar_raiz: bool = False) -> list[str]:
    """Las lineas de ``_segmentos_visibles``, sin la etiqueta.

    ``forzar_raiz`` lee el elemento aunque sea el que esta oculto: lo usan los
    titulares, que se guardan con su texto y una marca, no vacios.
    """
    return [linea for linea, _ in _segmentos_visibles(el, forzar_raiz=forzar_raiz)]


def _raiz_del_body(selector):
    """Elemento lxml del ``<body>``, o None si el documento no tiene body."""
    body = selector.css("body")
    if not body:
        return None
    raiz = body[0].root
    return raiz if hasattr(raiz, "tag") else None


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
    for ch in text:
        width = _ARIAL_20PX_WIDTHS.get(ch, _ARIAL_20PX_DEFAULT)
        total += width * scale
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
    # http-equiv is case-insensitive in HTML; try common variants.
    content = selector.css(
        'meta[http-equiv="refresh"]::attr(content), '
        'meta[http-equiv="Refresh"]::attr(content), '
        'meta[http-equiv="REFRESH"]::attr(content)'
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
    return absolutize_url(base_url, destino)


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
    lower_headers: dict[str, str] = {
        k.lower(): v for k, v in headers.items()
    }

    return {
        "has_hsts": "strict-transport-security" in lower_headers,
        "has_csp": "content-security-policy" in lower_headers,
        "has_x_content_type_options": "x-content-type-options" in lower_headers,
        "has_x_frame_options": "x-frame-options" in lower_headers,
        "referrer_policy": lower_headers.get("referrer-policy") or None,
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
    "[aria-modal='true']",
    "dialog[open]", "dialog",
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

# Elements that start/end a line when flattening HTML to text.
_BLOCK_TAGS: frozenset[str] = frozenset({
    "address", "article", "aside", "blockquote", "br", "button", "dd",
    "details", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
    "label", "legend", "li", "main", "nav", "ol", "option", "p", "pre",
    "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "ul",
})

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
                if not any(phrase in lower for phrase in _PROMO_TEXT_PHRASES):
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

        stripped = _strip_boilerplate_html(
            html, strip_promo=strip_promo, extra_selectors=extra_selectors
        )
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
    """
    try:
        import trafilatura

        clean = _strip_boilerplate_html(
            html, strip_promo=strip_promo, extra_selectors=extra_selectors
        )

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


def compute_indexability_status(
    status_code: int | None,
    meta_robots: str | None,
    x_robots: str | None,
    canonical_href: str | None,
    page_url: str,
) -> tuple[bool, str | None]:
    """Determine whether a page is indexable and, if not, why.

    Parameters
    ----------
    status_code:
        HTTP response status code.
    meta_robots:
        Value of the ``<meta name="robots">`` tag (may be ``None``).
    x_robots:
        Value of the ``X-Robots-Tag`` response header (may be ``None``).
    canonical_href:
        The resolved ``<link rel="canonical">`` href (may be ``None``).
    page_url:
        The URL of the page itself (used for canonical comparison).

    Returns
    -------
    tuple[bool, str | None]
        ``(is_indexable, reason)`` -- *reason* is ``None`` when the page
        is indexable; otherwise a short human-readable explanation.
    """
    # 1. Server errors
    if status_code is not None and 500 <= status_code < 600:
        return False, "5xx Server Error"

    # 2. Client errors
    if status_code is not None and 400 <= status_code < 500:
        return False, "4xx Client Error"

    # 3. Redirects
    if status_code is not None and 300 <= status_code < 400:
        return False, "3xx Redirect"

    # 4. Noindex in meta robots. ``none`` is Google shorthand for
    #    "noindex, nofollow"; tokens are split on commas AND whitespace so
    #    lenient markup like content="noindex nofollow" still counts.
    tokens = robots_tokens(meta_robots)
    if "noindex" in tokens or "none" in tokens:
        return False, "Noindex"

    # 5. Noindex in X-Robots-Tag header (same token rules).
    tokens = robots_tokens(x_robots)
    if "noindex" in tokens or "none" in tokens:
        return False, "Noindex"

    # 6. Canonicalised to a different URL
    if canonical_href:
        normalized_canonical = normalize_url(canonical_href)
        normalized_page = normalize_url(page_url)
        if normalized_canonical != normalized_page:
            return False, "Canonicalised"

    return True, None
