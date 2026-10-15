"""Donde acaba el ``<head>`` para un navegador, y que metadatos quedan fuera.

Funcion pura, sin Scrapy, como las de ``extractors``. Responde a dos
preguntas que el arbol de lxml no puede contestar:

- **Se corto el head antes de tiempo?** Google lo documenta asi: "once Google
  detects an invalid element in the <head>, it assumes the end of the <head>
  element and stops reading any further elements in the <head>"
  (developers.google.com/search/docs/crawling-indexing/valid-page-metadata,
  actualizada el 2025-12-10). Es lo mismo que hace el parser de HTML5: un
  ``<img>``, un ``<div>`` o un texto suelto en el head lo cierran, y lo que
  viene detras —el ``meta robots``, el canonical, el titulo— acaba en el body.
- **Que metadatos estan en el body**, se haya cortado el head o no.

Por que un tokenizador propio y no un arbol:

- **lxml** (libxml2) no es un parser de HTML5 y discrepa de Chromium justo en
  los casos que importan: deja en el head un ``meta`` que va detras de un
  ``<svg>`` o de un elemento personalizado, y un ``meta`` tras ``</head>`` lo
  saca de los dos (ni head ni body). Por eso ``has_meta_outside_head`` y
  ``canonical_in_body`` no sirven para esto.
- **html5lib** si sigue la especificacion, pero la de "scripting
  desactivado": un ``<noscript><img></noscript>`` en el head —el pixel de
  Facebook, el ``<noscript><iframe>`` de GTM pegado donde no toca, presentes
  en media web— le corta el head, y en Chromium (y en el renderizador de
  Google) no lo corta. Tampoco conoce ``<template>`` en el head. Daria falsos
  positivos en masa. Ademas construye el arbol entero en Python.

Lo que se sigue es el comportamiento de Chromium con scripting activado,
comprobado caso por caso (``tests/test_head_html.py``). Cuando la lista de
Google y el navegador no coinciden (``basefont``, ``bgsound``, ``noframes``:
el navegador los deja en el head y Google no los nombra), se sigue al
navegador: es el lado en que no se afirma un corte que quiza no existe.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Lo que el parser de HTML5 deja dentro del head ("in head" insertion mode).
_EN_HEAD = frozenset({
    "base", "basefont", "bgsound", "link", "meta", "title", "noscript",
    "noframes", "style", "script", "template",
})

# Contenido que el navegador lee como texto: dentro no hay etiquetas. Con
# scripting activado, `noscript` tambien (en el head y en el body).
_TEXTO_CRUDO = frozenset({
    "title", "textarea", "noscript", "noframes", "iframe", "noembed", "xmp",
    "script", "style", "plaintext",
})

_EXTRANJEROS = frozenset({"svg", "math"})

# Etiquetas que, abiertas dentro de un <svg>/<math>, lo cierran y vuelven a
# HTML (especificacion de HTML5, "parsing main inforeign"). `meta` esta: un
# `<svg><meta name=robots>` deja el meta en el body como HTML.
_SALEN_DE_EXTRANJERO = frozenset({
    "b", "big", "blockquote", "body", "br", "center", "code", "dd", "div",
    "dl", "dt", "em", "embed", "h1", "h2", "h3", "h4", "h5", "h6", "head",
    "hr", "i", "img", "li", "listing", "menu", "meta", "nobr", "ol", "p",
    "pre", "ruby", "s", "small", "span", "strong", "strike", "sub", "sup",
    "table", "tt", "u", "ul", "var",
})

_ESPACIO_HTML = " \t\n\f\r"

# Que metadatos interesan fuera del head. Los `meta` por su `name`.
_META_RELEVANTES = frozenset({"robots", "googlebot", "description"})

# Mas alla del limite de Googlebot no se mira: lo que hay detras ni se
# descarga, asi que si esta o no en el head da igual (eso lo dice
# `html_supera_2mb`). Acota ademas el coste en las paginas de 13 MB.
LIMITE_CARACTERES = 2 * 1024 * 1024

# Si ya estamos en el body y en lo que queda no hay ningun candidato, se
# deja de tokenizar: es el caso de casi todas las paginas.
_CANDIDATO = re.compile(
    r"<title[\s>/]|<meta\b[^>]*\bname\s*=\s*[\"']?\s*(?:robots|googlebot|description)\b"
    r"|<link\b[^>]*\b(?:canonical|hreflang)\b",
    re.IGNORECASE,
)

_TROZO = 64 * 1024

# Lo que se guarda como motivo del corte (`html_meta.head_cortado_por`,
# VARCHAR(64)). HTMLParser acepta como nombre de etiqueta cualquier cosa sin
# espacios —`<metaproperty="og:title"content="...">` es UNA etiqueta—, y una
# cadena de mas de 64 caracteres tumbaria el INSERT de html_meta en Postgres
# (y con el, lo pendiente del lote). Un nombre que no lo parece va a `#otro`,
# que ademas no parte los grupos de `head_cortado` en una fila por errata.
_NOMBRE_VALIDO = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_MAX_NOMBRE = 40


def _nombre_de_corte(motivo: str) -> str:
    if motivo.startswith("#"):
        return motivo
    if len(motivo) <= _MAX_NOMBRE and _NOMBRE_VALIDO.fullmatch(motivo):
        return motivo
    return "#otro"


def _rel(valor: str | None) -> set[str]:
    if not valor:
        return set()
    return {t for t in re.split(r"[\s,]+", valor.lower()) if t}


class _LectorHead(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # antes -> head -> tras_head -> body
        self.modo = "antes"
        self.cortado_por: str | None = None
        self.fuera: set[str] = set()
        self._plantilla = 0
        # Pila de contextos SVG/MathML abiertos en el body: "f" = extranjero
        # (svg, math), "h" = punto de integracion (``foreignObject``), dentro
        # del cual se vuelve a leer HTML.
        self._pila: list[str] = []

    @property
    def _en_extranjero(self) -> bool:
        return bool(self._pila) and self._pila[-1] == "f"

    def _salir_de_extranjero(self) -> None:
        """Un elemento HTML dentro de un svg lo cierra hasta el ultimo HTML."""
        while self._en_extranjero:
            self._pila.pop()

    # -- utilidades -------------------------------------------------------

    def _cortar(self, motivo: str) -> None:
        """Lo que no cabe en el head lo cierra y abre el body."""
        if self.modo in ("antes", "head"):
            self.cortado_por = _nombre_de_corte(motivo)
        self.modo = "body"

    def _anotar(self, tag: str, attrs: dict[str, str | None]) -> None:
        """Un metadato que el navegador deja en el body."""
        if tag == "title":
            self.fuera.add("title")
        elif tag == "meta":
            nombre = (attrs.get("name") or "").strip().lower()
            if nombre in _META_RELEVANTES:
                self.fuera.add(nombre)
        elif tag == "link":
            rel = _rel(attrs.get("rel"))
            if "canonical" in rel:
                self.fuera.add("canonical")
            if "alternate" in rel and attrs.get("hreflang"):
                self.fuera.add("hreflang")

    # -- eventos del tokenizador -----------------------------------------

    def handle_starttag(self, tag, attrs_lista):
        attrs = dict(attrs_lista)
        if tag in _TEXTO_CRUDO and tag not in ("script", "style"):
            # Lo que haya dentro es texto (scripting activado), aunque lleve
            # `<img>`: es lo que separa a Chromium de html5lib.
            self.set_cdata_mode(tag)

        if self._plantilla:
            # El contenido de un <template> no esta en el documento.
            if tag == "template":
                self._plantilla += 1
            return

        if self.modo in ("antes", "head", "tras_head"):
            if tag == "html":
                return
            if tag == "head":
                if self.modo == "antes":
                    self.modo = "head"
                return
            if tag == "body" or tag == "frameset":
                self.modo = "body"
                return
            if tag in _EN_HEAD and not (self.modo == "tras_head" and tag == "noscript"):
                # Tras </head> el navegador los devuelve al head (error de
                # sintaxis, pero los mete ahi): no se pierden.
                if self.modo == "antes":
                    self.modo = "head"
                if tag == "template":
                    self._plantilla = 1
                return
            self._cortar(tag)
            # Y se procesa como elemento del body (sigue abajo).

        # -- body --
        if self._en_extranjero:
            if tag in _SALEN_DE_EXTRANJERO:
                self._salir_de_extranjero()
            else:
                if tag in _EXTRANJEROS:
                    self._pila.append("f")
                elif tag == "foreignobject":
                    self._pila.append("h")
                return
        if tag in _EXTRANJEROS:
            self._pila.append("f")
            return
        if tag == "template":
            self._plantilla = 1
            return
        self._anotar(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        # En HTML la barra de `<div/>` no cierra nada; en SVG si.
        if tag in _EXTRANJEROS:
            if self.modo in ("antes", "head", "tras_head") and not self._plantilla:
                self._cortar(tag)
            return
        if self._en_extranjero and tag not in _SALEN_DE_EXTRANJERO:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self._plantilla:
            if tag == "template":
                self._plantilla -= 1
            return
        if self.modo == "head" and tag in ("head", "body", "html"):
            self.modo = "tras_head"
            return
        if self._pila:
            if tag in _EXTRANJEROS and self._pila[-1] == "f":
                self._pila.pop()
            elif tag == "foreignobject" and self._pila[-1] == "h":
                self._pila.pop()

    def handle_data(self, data):
        # El texto de un <title>, <script>, <noscript>… es su contenido, no un
        # texto suelto en el head.
        if self._plantilla or self.modo == "body" or self.cdata_elem:
            return
        if data.strip(_ESPACIO_HTML):
            # Un texto suelto en el head lo cierra. `&nbsp;` tambien: no es
            # espacio para HTML (comprobado en Chromium).
            self._cortar("#texto")


def leer_head(html: str | None) -> dict | None:
    """Donde corta el navegador el ``<head>`` y que metadatos deja en el body.

    Devuelve ``{"cortado_por": str | None, "fuera_del_head": list[str]}``:

    - ``cortado_por``: la etiqueta que cerro el head antes de su ``</head>``
      (``"img"``, ``"div"``…) o ``"#texto"`` si fue un texto suelto; ``None``
      si el head acabo donde debia.
    - ``fuera_del_head``: los metadatos que quedan en el body, ordenados, de
      entre ``canonical``, ``description``, ``googlebot``, ``hreflang``,
      ``robots`` y ``title`` (solo el ``<title>`` de HTML, no el de un SVG).

    Solo mira los primeros :data:`LIMITE_CARACTERES` caracteres. Devuelve
    ``None`` si el HTML no se pudo leer: «no se sabe» no es «esta bien».
    """
    if not html:
        return {"cortado_por": None, "fuera_del_head": []}
    texto = html[:LIMITE_CARACTERES]
    lector = _LectorHead()
    try:
        for i in range(0, len(texto), _TROZO):
            lector.feed(texto[i:i + _TROZO])
            # Lo pendiente del tokenizador va delante: una etiqueta partida
            # entre dos trozos solo casa si se juntan.
            if lector.modo == "body" and not _CANDIDATO.search(
                lector.rawdata + texto[i + _TROZO:]
            ):
                break
        else:
            lector.close()
    except Exception:  # noqa: BLE001 - HTML imposible: mejor no saber que saber mal
        return None
    return {"cortado_por": lector.cortado_por, "fuera_del_head": sorted(lector.fuera)}
