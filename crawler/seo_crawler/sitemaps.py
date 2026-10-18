"""XML sitemap parsing helpers — pure functions, no Scrapy imports.

Covers the sitemap protocol (https://www.sitemaps.org/protocol.html):
``<urlset>`` files, ``<sitemapindex>`` files, gzip-compressed bodies, and
``Sitemap:`` directives in robots.txt. Namespace-agnostic on purpose —
real-world sitemaps use the standard namespace, no namespace at all, or
Google extensions, and Screaming Frog accepts them all.
"""

from __future__ import annotations

import gzip
import logging
import os
import zlib
from dataclasses import dataclass, field
from urllib.parse import urljoin

_GZIP_MAGIC = b"\x1f\x8b"

# Hard caps so a hostile/broken sitemap tree cannot blow up the crawl.
#
# 50 se quedaba muy corto con CMS que parten el sitemap por plantilla: un
# Liferay real declaraba 395 hijos, asi que se leia el 12% y el resto de URLs
# quedaba sin membresia. Ahora son 500 y es configurable por entorno, porque el
# numero adecuado depende del sitio. El tope sigue existiendo como proteccion
# ante un arbol de sitemaps hostil o roto; cuando se alcanza, el spider deja la
# membresia en NULL en vez de afirmar que las URLs no estan en el sitemap.
logger = logging.getLogger(__name__)

MAX_SITEMAP_FILES = int(os.getenv("MAX_SITEMAP_FILES", "500"))
MAX_URLS_PER_SITEMAP = int(os.getenv("MAX_URLS_PER_SITEMAP", "50000"))
# Tope del XML ya descomprimido. Scrapy acota la DESCARGA (1 GB por defecto),
# que no sirve de nada frente a una bomba gzip: lo que hay que acotar es lo
# que sale.
MAX_SITEMAP_BYTES = int(os.getenv("MAX_SITEMAP_BYTES", str(50 * 1024 * 1024)))
_TROZO = 256 * 1024


def parse_robots_sitemaps(robots_txt: str, base_url: str = "") -> list[str]:
    """Extract ``Sitemap:`` directive URLs from a robots.txt body.

    The directive is case-insensitive and may appear anywhere in the file
    (it is not tied to a User-agent group). Relative values (non-standard
    but seen in the wild) are resolved against *base_url*.
    """
    found: list[str] = []
    seen: set[str] = set()
    for line in (robots_txt or "").splitlines():
        # Strip comments, then match the directive prefix.
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() != "sitemap":
            continue
        url = value.strip()
        if not url:
            continue
        if base_url:
            url = urljoin(base_url, url)
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def _maybe_gunzip(body: bytes) -> bytes:
    """Descomprime un sitemap .xml.gz, con tope (ver :func:`_descomprimir`)."""
    return _descomprimir(body)[0]


def _descomprimir(body: bytes) -> tuple[bytes, int, bool]:
    """``(xml, bytes_descomprimidos, cortado)`` de un sitemap, con tope.

    `gzip.decompress()` a secas descomprime lo que haga falta: unos pocos KB
    comprimidos pueden ser gigas en memoria del worker (una bomba gzip se
    escribe en una linea, y `MAX_SITEMAP_BYTES` de XML repetido comprime a
    casi nada). No hace falta mala fe: un sitemap de verdad de un sitio enorme
    tambien puede no caber. Y el parser va con `huge_tree=True`, que apaga las
    protecciones de libxml2, asi que el tope tiene que estar aqui.

    Se descomprime a trozos y se para al pasar el tope. Lo que se corta se
    AVISA en el log y se devuelve vacio en vez de medio XML: medio sitemap
    parsea "bien" y se queda con las URLs que entraron, que es justo el fallo
    silencioso que no queremos (decision 7: lo roto se reporta, no se filtra).

    `bytes_descomprimidos` es el tamano del XML que lee Google —su limite de
    50 MB es sobre el fichero sin comprimir (decision 126)—; si se corto, es
    lo que se llevaba al cortar, que ya pasa del tope: un minimo, no el total.
    Sin gzip es el tamano del cuerpo tal cual.
    """
    if body[:2] != _GZIP_MAGIC:
        return body, len(body), False
    try:
        descompresor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        trozos: list[bytes] = []
        total = 0
        for inicio in range(0, len(body), _TROZO):
            trozo = descompresor.decompress(body[inicio : inicio + _TROZO], _TROZO * 8)
            while trozo:
                total += len(trozo)
                if total > MAX_SITEMAP_BYTES:
                    logger.warning(
                        "Sitemap comprimido descartado: pasa de %d bytes al "
                        "descomprimir (%d comprimidos). Sube MAX_SITEMAP_BYTES "
                        "si el sitio lo tiene asi de verdad.",
                        MAX_SITEMAP_BYTES, len(body),
                    )
                    return b"", total, True
                trozos.append(trozo)
                trozo = descompresor.flush(_TROZO * 8)
        return b"".join(trozos), total, False
    except Exception:
        return body, len(body), False


def _localname(tag) -> str:
    """Element tag without its XML namespace, lowercased."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


@dataclass
class EntradaSitemap:
    """Una URL de un sitemap con lo que el sitemap dice de ella.

    - ``lastmod``: el valor tal cual (fecha W3C, a veces con hora). Sin
      interpretar a proposito: Google lo usa solo si es "consistente y
      verificable", y un lastmod que miente (la fecha de hoy en todas las URLs,
      el formato roto) es en si un hallazgo; normalizarlo aqui lo taparia.
    - ``hreflang``: las anotaciones ``<xhtml:link rel="alternate"
      hreflang="..." href="...">`` de esa URL, como ``(idioma, href)``. Es la
      tercera forma de declarar hreflang que acepta Google, junto al ``<link>``
      del HTML y la cabecera HTTP, y la unica que usan muchos sitios grandes:
      sin leerla, su hreflang no existe para el informe.
    """

    url: str
    lastmod: str | None = None
    hreflang: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class LecturaSitemap:
    """Lo que dio la lectura de UN fichero de sitemap (decision 126).

    - ``formato``: ``urlset``, ``sitemapindex``, ``texto``, ``rss``, ``atom``
      o ``desconocido`` (no se reconocio nada: un HTML de error, un JSON).
    - ``declaradas``: cuantas entradas trae el fichero (URLs, o hijos en un
      indice) contadas ENTERAS, tambien las que pasan de
      ``MAX_URLS_PER_SITEMAP`` y no se devuelven: es lo que se compara con el
      limite de 50.000 de Google. Sin repetidas dentro del mismo fichero.
    - ``bytes``: tamano del XML descomprimido (minimo si ``cortado``).
    - ``cortado``: el gzip pasaba de ``MAX_SITEMAP_BYTES`` y no se leyo.
    """

    entradas: list[EntradaSitemap] = field(default_factory=list)
    hijos: list[str] = field(default_factory=list)
    formato: str = "desconocido"
    declaradas: int = 0
    bytes: int = 0
    cortado: bool = False

    @property
    def recortada(self) -> bool:
        """Se devolvieron menos entradas de las que trae el fichero."""
        return self.cortado or self.declaradas > len(self.entradas) + len(self.hijos)


def parse_sitemap(body: bytes | str, base_url: str = "") -> tuple[list[str], list[str]]:
    """Parse a sitemap file into ``(page_urls, child_sitemap_urls)``.

    Handles both ``<urlset>`` (leaf sitemaps: returns page URLs) and
    ``<sitemapindex>`` (index files: returns child sitemap URLs). Gzip
    bodies are decompressed automatically. Returns ``([], [])`` on any
    parse failure — a broken sitemap must never break the crawl.

    Tambien los otros tres formatos que Google acepta como sitemap y que antes
    daban CERO URLs: el de texto plano (una URL por linea, esta en el propio
    protocolo de sitemaps.org), RSS 2.0 y Atom. Que den cero no es inocuo: la
    membresia `in_sitemap` queda en falso para TODAS las URLs del sitio, y de
    ahi salen los huerfanos inflados y la lista de "URLs del sitemap sin
    rastrear" vacia.

    Solo las URLs; ``lastmod`` y hreflang de cada una salen de
    :func:`parse_sitemap_entradas`.
    """
    entradas, hijos = parse_sitemap_entradas(body, base_url)
    return ([e.url for e in entradas], hijos)


def parse_sitemap_entradas(
    body: bytes | str, base_url: str = ""
) -> tuple[list[EntradaSitemap], list[str]]:
    """Como :func:`parse_sitemap`, pero cada URL con su ``lastmod`` y su hreflang.

    Los formatos de texto, RSS y Atom no los llevan: sus entradas salen con
    ``lastmod=None`` y sin hreflang.
    """
    lectura = leer_sitemap(body, base_url)
    return (lectura.entradas, lectura.hijos)


def leer_sitemap(body: bytes | str, base_url: str = "") -> LecturaSitemap:
    """Lee un fichero de sitemap entero: entradas, hijos y lo que se necesita
    para saber si pasa de los limites de Google (decision 126)."""
    if isinstance(body, str):
        raw = body.encode("utf-8", errors="ignore")
    else:
        raw = body
    raw, tamano, cortado = _descomprimir(raw)
    if cortado:
        return LecturaSitemap(bytes=tamano, cortado=True)

    def _plano(formato: str, urls: list[str], declaradas: int) -> LecturaSitemap:
        if not urls:
            formato = "desconocido"
        return LecturaSitemap(entradas=[EntradaSitemap(url=u) for u in urls],
                              formato=formato, declaradas=declaradas, bytes=tamano)

    try:
        from lxml import etree

        # recover=True tolerates the malformed XML that real sites serve.
        root = etree.fromstring(raw, parser=etree.XMLParser(recover=True, huge_tree=True))
    except Exception:
        return _plano("texto", *_parse_sitemap_texto_contado(raw, base_url))
    if root is None:
        return _plano("texto", *_parse_sitemap_texto_contado(raw, base_url))

    root_kind = _localname(root.tag)
    if root_kind in ("rss", "feed"):
        return _plano("rss" if root_kind == "rss" else "atom",
                      *_parse_feed_contado(root, base_url))
    if root_kind not in ("urlset", "sitemapindex"):
        # Ni sitemap ni feed: puede ser el de texto plano (y tambien un HTML de
        # error, que no dara ninguna linea con pinta de URL).
        return _plano("texto", *_parse_sitemap_texto_contado(raw, base_url))

    paginas: list[EntradaSitemap] = []
    child_sitemaps: list[str] = []
    seen: set[str] = set()

    # Walk <url>/<sitemap> entries and pull their <loc> children. Iterating
    # entries (instead of every <loc> in the doc) keeps Google extension
    # blocks (image:loc, video:...) from leaking into the URL list.
    # Se recorre el fichero ENTERO aunque se llegue al tope: lo que pasa de
    # MAX_URLS_PER_SITEMAP no se devuelve, pero se cuenta, porque es lo que
    # dice si el fichero supera el limite de 50.000 de Google (decision 126).
    for entry in root:
        entry_kind = _localname(entry.tag)
        if entry_kind not in ("url", "sitemap"):
            continue
        loc_text = None
        lastmod = None
        hreflang: list[tuple[str, str]] = []
        for child in entry:
            nombre = _localname(child.tag)
            if nombre == "loc" and loc_text is None:
                loc_text = (child.text or "").strip()
            elif nombre == "lastmod" and lastmod is None:
                lastmod = (child.text or "").strip() or None
            elif nombre == "link":
                anotacion = _hreflang_de(child, base_url)
                if anotacion:
                    hreflang.append(anotacion)
        if not loc_text:
            continue
        url = urljoin(base_url, loc_text) if base_url else loc_text
        if url in seen:
            continue
        seen.add(url)
        if entry_kind == "sitemap" or root_kind == "sitemapindex":
            child_sitemaps.append(url)
        elif len(paginas) < MAX_URLS_PER_SITEMAP:
            paginas.append(EntradaSitemap(url=url, lastmod=lastmod, hreflang=hreflang))

    return LecturaSitemap(entradas=paginas, hijos=child_sitemaps, formato=root_kind,
                          declaradas=len(seen), bytes=tamano)


def _hreflang_de(elemento, base_url: str = "") -> tuple[str, str] | None:
    """``(idioma, href)`` de un ``<xhtml:link>`` de sitemap, o None.

    ``rel`` se compara por token, como en el HTML (decision 56): ``Alternate``
    vale. El idioma se devuelve tal cual, sin validar ni pasar a minusculas:
    `X-Default` o `en-UK` son hallazgos que decide el analizador de hreflang,
    no el parser.
    """
    rel = (elemento.get("rel") or "").lower().split()
    if "alternate" not in rel:
        return None
    idioma = (elemento.get("hreflang") or "").strip()
    href = (elemento.get("href") or "").strip()
    if not idioma or not href:
        return None
    return (idioma, urljoin(base_url, href) if base_url else href)


def _parse_sitemap_texto(raw: bytes, base_url: str = "") -> list[str]:
    return _parse_sitemap_texto_contado(raw, base_url)[0]


def _parse_sitemap_texto_contado(raw: bytes, base_url: str = "") -> tuple[list[str], int]:
    """Sitemap de texto plano: una URL absoluta por linea.

    Esta en el protocolo (sitemaps.org) y Google lo acepta. Se exige que la
    linea empiece por http:// o https:// para no confundir un HTML de error o
    un JSON con un sitemap.
    """
    try:
        texto = raw.decode("utf-8", errors="replace")
    except Exception:
        return [], 0
    urls: list[str] = []
    vistas: set[str] = set()
    for linea in texto.splitlines():
        candidata = linea.strip()
        if not candidata or not candidata.lower().startswith(("http://", "https://")):
            continue
        if " " in candidata or "<" in candidata:
            continue
        if candidata in vistas:
            continue
        vistas.add(candidata)
        if len(urls) < MAX_URLS_PER_SITEMAP:
            urls.append(candidata)
    return urls, len(vistas)


def _parse_feed(root, base_url: str = "") -> list[str]:
    return _parse_feed_contado(root, base_url)[0]


def _parse_feed_contado(root, base_url: str = "") -> tuple[list[str], int]:
    """URLs de un RSS 2.0 o de un Atom, que Google acepta como sitemap.

    RSS: ``<rss><channel><item><link>texto</link>``.
    Atom: ``<feed><entry><link href="..."/>`` (el `rel="alternate"`, que es el
    de la pagina; se ignoran `enclosure`, `self` y demas).
    """
    urls: list[str] = []
    vistas: set[str] = set()

    def anadir(valor: str | None) -> None:
        if not valor:
            return
        candidata = valor.strip()
        if not candidata:
            return
        url = urljoin(base_url, candidata) if base_url else candidata
        if url in vistas:
            return
        vistas.add(url)
        if len(urls) < MAX_URLS_PER_SITEMAP:
            urls.append(url)

    for elemento in root.iter():
        nombre = _localname(elemento.tag)
        if nombre == "item":  # RSS
            for hijo in elemento:
                if _localname(hijo.tag) == "link":
                    anadir(hijo.text or hijo.get("href"))
                    break
        elif nombre == "entry":  # Atom
            alternativa = None
            for hijo in elemento:
                if _localname(hijo.tag) != "link":
                    continue
                rel = (hijo.get("rel") or "alternate").strip().lower()
                if rel != "alternate":
                    continue
                alternativa = hijo.get("href")
                break
            anadir(alternativa)
    return urls, len(vistas)
