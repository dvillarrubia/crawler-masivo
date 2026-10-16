"""Texto visible de un elemento lxml: lo que el navegador pinta, linea a linea.

Vivia en `crawler/seo_crawler/extractors.py`. Se muda aqui porque la
extraccion a medida (decision 104) lo necesita tambien en la API —el endpoint
de prueba de reglas tiene que dar el MISMO texto que el rastreo— y la imagen
de la API no copia `crawler/`. `extractors.py` lo vuelve a exportar, como hace
con `shared.dominios` y `shared.robots`. No importa nada de Scrapy.
"""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")

# Elements that start/end a line when flattening HTML to text.
_BLOCK_TAGS: frozenset[str] = frozenset({
    "address", "article", "aside", "blockquote", "br", "button", "dd",
    "details", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
    "label", "legend", "li", "main", "nav", "ol", "option", "p", "pre",
    "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "ul",
})


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
