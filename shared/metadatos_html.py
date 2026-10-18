"""Lo que el HTML declara de si mismo, leido igual en el spider y el analizador.

Decision 108. El spider guarda el dato crudo (el ``content`` del viewport, el
``lang``, el ``href`` del canonical tal como se escribio, el charset del meta)
y el analizador decide con estas funciones. Viven en `shared/` porque la imagen
del analisis no copia `crawler/` (decisiones 23, 39 y 64): si el spider
clasificara con una copia y el analizador con otra, acabarian discrepando.

Nada de esto lleva Scrapy ni w3lib: la imagen del analisis no los instala.
"""

from __future__ import annotations

import codecs
import re
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Forma de una URL escrita en el HTML (canonical, og:image)
# ---------------------------------------------------------------------------

ABSOLUTA = "absoluta"
RELATIVA_A_RAIZ = "relativa_a_raiz"          # /x
RELATIVA = "relativa"                        # x, ../x, ?p=2
RELATIVA_AL_PROTOCOLO = "relativa_al_protocolo"  # //h/x
CON_FRAGMENTO = "con_fragmento"              # lleva '#'
VACIA = "vacia"                              # href="" (la propia pagina)

FORMAS_RELATIVAS = frozenset({RELATIVA_A_RAIZ, RELATIVA, RELATIVA_AL_PROTOCOLO})

_RE_ESQUEMA = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def forma_de_href(href: str | None) -> str | None:
    """Como esta escrita una URL: absoluta, relativa (de tres maneras), con
    fragmento o vacia. None si no hay URL.

    El fragmento manda sobre lo demas: `/a#b` y `https://x.com/a#b` son los
    dos «con fragmento», que es lo que cambia lo que Google hace con ellos (lo
    quita). `//cdn.x.com/a.jpg` no es relativa a la pagina: es relativa al
    protocolo, otra cosa (FreeCrawl las mezcla).
    """
    if href is None:
        return None
    h = href.strip()
    if not h:
        return VACIA
    if "#" in h:
        return CON_FRAGMENTO
    if h.startswith("//"):
        return RELATIVA_AL_PROTOCOLO
    if _RE_ESQUEMA.match(h):
        return ABSOLUTA
    if h.startswith("/"):
        return RELATIVA_A_RAIZ
    return RELATIVA


def quitar_fragmento(url: str) -> str:
    """La URL sin su `#...`, que es lo que Google usa de un canonical."""
    return url.split("#", 1)[0]


# ---------------------------------------------------------------------------
# Meta viewport
# ---------------------------------------------------------------------------

VIEWPORT_VACIO = "vacio"
VIEWPORT_ANCHO_FIJO = "ancho_fijo"
VIEWPORT_SIN_DEVICE_WIDTH = "sin_device_width"


def _claves_viewport(content: str) -> dict[str, str]:
    claves: dict[str, str] = {}
    for trozo in re.split(r"[,;]", content):
        if "=" in trozo:
            k, v = trozo.split("=", 1)
            claves[k.strip().lower()] = v.strip().strip("'\"").lower()
        elif trozo.strip():
            claves[trozo.strip().lower()] = ""
    return claves


def motivo_viewport(content: str | None) -> str | None:
    """Por que un viewport no adapta la pagina al movil, o None si la adapta.

    - ``width=device-width``: correcto.
    - ``width=1024`` (un numero): la pagina se pinta a ese ancho y el movil la
      reduce; es la web de escritorio de siempre.
    - sin ``width`` pero con ``initial-scale``: los navegadores moviles toman
      el ancho del dispositivo (es lo que acepta Lighthouse), correcto.
    - sin ninguno de los dos, o sin ``content``: como si no hubiera viewport,
      el movil pinta a 980 px.

    ``user-scalable=no`` y ``maximum-scale`` son accesibilidad (impiden hacer
    zoom), no adaptacion al movil: no hacen incorrecto el viewport.
    """
    if content is None or not content.strip():
        return VIEWPORT_VACIO
    claves = _claves_viewport(content)
    ancho = claves.get("width")
    if ancho is not None:
        if ancho == "device-width":
            return None
        if re.match(r"^\d+(\.\d+)?(px)?$", ancho):
            return VIEWPORT_ANCHO_FIJO
    if "initial-scale" in claves:
        return None
    return VIEWPORT_SIN_DEVICE_WIDTH


# ---------------------------------------------------------------------------
# Idioma
# ---------------------------------------------------------------------------

def idioma_principal(etiqueta: str | None) -> str | None:
    """El primer subtag de una etiqueta de idioma, en minusculas.

    `es-ES` -> `es`, `zh-Hant-TW` -> `zh`, `pt_BR` -> `pt`. La region y el
    alfabeto no se comparan: `es-MX` frente a `es-ES` no es un idioma distinto.
    """
    if not etiqueta:
        return None
    principal = re.split(r"[-_]", etiqueta.strip(), maxsplit=1)[0].lower()
    return principal or None


# ---------------------------------------------------------------------------
# Charset
# ---------------------------------------------------------------------------

# WHATWG Encoding: el navegador lee `iso-8859-1`, `latin1` y `us-ascii` como
# windows-1252, y `iso-8859-9` como windows-1254. Sin esto, `latin1` frente a
# `windows-1252` saldria como incoherente siendo la misma codificacion.
_WHATWG = {
    "iso8859-1": "cp1252",
    "ascii": "cp1252",
    "cp1252": "cp1252",
    "iso8859-9": "cp1254",
    "iso8859-11": "cp874",
}

_RE_CHARSET_CT = re.compile(r"""charset\s*=\s*["']?\s*([^"';,\s]+)""", re.I)


def normalizar_charset(nombre: str | None) -> str | None:
    """Nombre canonico de una codificacion, o el nombre en minusculas si
    Python no la conoce (no se inventa una equivalencia)."""
    if not nombre:
        return None
    limpio = nombre.strip().strip("'\"").strip().lower()
    if not limpio:
        return None
    try:
        canonico = codecs.lookup(limpio).name
    except LookupError:
        return limpio
    return _WHATWG.get(canonico, canonico)


def charset_de_content_type(content_type: str | None) -> str | None:
    """El `charset=` de una cabecera Content-Type, normalizado."""
    if not content_type:
        return None
    m = _RE_CHARSET_CT.search(content_type)
    return normalizar_charset(m.group(1)) if m else None


def diagnostico_charset(
    cabecera: str | None, meta: str | None, bom: str | None,
) -> tuple[str | None, str | None]:
    """(estado, cual_manda) con lo que declaran la cabecera, el meta y el BOM.

    El navegador, y Scrapy, deciden por este orden: BOM, cabecera, meta (en los
    1.024 primeros bytes). ``estado``:

    - ``"ausente"``: no hay ninguno de los tres;
    - ``"incoherente"``: hay dos o mas y, normalizados, no coinciden;
    - ``None``: lo declarado es coherente.

    Recibe los valores ya normalizados (`normalizar_charset`); `meta` vacio
    (`""`) es «medido y no hay».
    """
    declarados = {k: v for k, v in (("bom", bom), ("cabecera", cabecera),
                                    ("meta", meta or None)) if v}
    if not declarados:
        return "ausente", None
    manda = next(iter(declarados))
    if len(set(declarados.values())) > 1:
        return "incoherente", manda
    return None, manda
