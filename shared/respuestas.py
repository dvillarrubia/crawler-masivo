"""¿Es esta respuesta la pagina del sitio, o un muro puesto delante?

Con `HTTPERROR_ALLOW_ALL` toda respuesta llega al spider y se guarda, asi que
un desafio de Cloudflare servido con 200, un captcha o el cuerpo binario que
devuelve un F5 cuando no le gusta la huella TLS (decision 11) se guardaban como
paginas normales: con su titulo «Just a moment...», sin h1, con 12 palabras,
**indexables** y con sus incidencias de pagina. Y el job acababa `completed`,
que es como se lee un sitio limpio.

Una sola funcion pura, `clasificar_respuesta`, para el spider y para quien la
necesite despues (decisiones 23, 39 y 62: dos copias del mismo criterio acaban
discrepando). La usa tambien `shared/descarga_cruda.py`, que es la puerta de
las herramientas de control: el «esto es un muro» es el mismo en los dos
caminos.

**El criterio es conservador a proposito.** Un falso positivo esconde una
pagina real del informe —se queda sin incidencias, sin indexabilidad y, si hay
muchas, para el rastreo—, asi que una clase de muro solo se da con una senal
INEQUIVOCA (la cabecera `cf-mitigated`, un 429) o con DOS que se confirman: un
titular o un marcador de desafio **y** una pagina que no tiene contenido (pocas
palabras o un codigo de error). Un articulo que habla de captchas, o titulado
«Access denied: la serie», tiene texto y no pasa. Lo que no se sabe juzgar es
`contenido`, no `desconocido`: `desconocido` es solo lo que de verdad no se
puede leer (un 2xx HTML con el cuerpo vacio).

Sin dependencias: lo importa la imagen de la API, que copia `shared/` y no
lleva parsel.
"""

from __future__ import annotations

import re
from collections import Counter, deque
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

CONTENIDO = "contenido"
CHALLENGE_WAF = "challenge_waf"
CAPTCHA = "captcha"
RATE_LIMITED = "rate_limited"
RESPUESTA_BINARIA = "respuesta_binaria"
LOGIN_REQUERIDO = "login_requerido"
DESCONOCIDO = "desconocido"

CLASES = (
    CONTENIDO, CHALLENGE_WAF, CAPTCHA, RATE_LIMITED, RESPUESTA_BINARIA,
    LOGIN_REQUERIDO, DESCONOCIDO,
)

# Las que NO son la pagina: no se extraen, no son indexables, no llevan
# incidencias de pagina, cuentan para la salud del job y se repiten al reanudar.
# `login_requerido` y `desconocido` no estan: un 401 o una redireccion al login
# son la respuesta verdadera del sitio, y un cuerpo vacio no dice que haya un
# muro delante.
NO_VALIDAS = frozenset({CHALLENGE_WAF, CAPTCHA, RATE_LIMITED, RESPUESTA_BINARIA})

# Lo que dice `indexability_status` de una pagina de muro. Antes que el codigo:
# un 403 de Cloudflare no es «Client Error (403)» del sitio, y quien lo lea en el
# CSV tiene que saber que la pagina no se llego a ver.
ESTADO_INDEXABILIDAD = {
    CHALLENGE_WAF: "Bloqueada (WAF)",
    CAPTCHA: "Bloqueada (captcha)",
    RATE_LIMITED: "Limitada (rate limit)",
    RESPUESTA_BINARIA: "Respuesta no valida (binaria)",
}

# Motivos de `jobs.finish_reason` de un rastreo parado por su salud.
BLOQUEADO_POR_WAF = "bloqueado_por_waf"
MOTIVOS_BLOQUEO = frozenset({BLOQUEADO_POR_WAF, RATE_LIMITED})

# Titulos con los que un WAF responde a una peticion que ha bloqueado (decision
# 62). Se casan al PRINCIPIO del titulo y por palabra entera: «Security
# checklist for WordPress» empieza por «security check» y es un articulo.
TITULOS_DE_BLOQUEO = (
    "just a moment",
    "access denied",
    "pardon our interruption",
    "attention required",
    "request unsuccessful",
    "are you a robot",
    "checking your browser",
    "security check",
    "403 forbidden",
)
# Los de captcha van aparte solo para nombrar la clase.
TITULOS_DE_CAPTCHA = (
    "are you a robot",
    "captcha",
    "verify you are human",
    "human verification",
)

# Estos tres son tambien el titulo de un error HONESTO del servidor: el 403 de
# Apache de un directorio sin indice se titula «403 Forbidden» (medido: 12 en el
# censo de www.uoc.edu, todos de verdad). Con un codigo de error son la
# respuesta del sitio —un enlace interno a algo prohibido, que es un hallazgo— y
# solo cuentan como muro servidos con 2xx y sin contenido: el wp-login de
# blogs.uoc.edu respondia 200, «403 Forbidden» y 3 palabras, y salia indexable.
TITULOS_GENERICOS = ("access denied", "403 forbidden", "security check")


def _re_titulos(titulos) -> re.Pattern:
    return re.compile(r"^\s*(?:" + "|".join(re.escape(t) for t in titulos) + r")\b")


_RE_TITULO_MURO = _re_titulos(TITULOS_DE_BLOQUEO)
_RE_TITULO_PROPIO = _re_titulos(
    [t for t in TITULOS_DE_BLOQUEO if t not in TITULOS_GENERICOS])
_RE_TITULO_CAPTCHA = _re_titulos(TITULOS_DE_CAPTCHA)

# Marcadores del CUERPO que solo aparecen en la pagina de desafio de cada
# proveedor, no en las paginas normales que sirve. OJO: Cloudflare inyecta
# `/cdn-cgi/challenge-platform/scripts/jsd/main.js` en paginas NORMALES (su
# deteccion de bots), asi que «challenge-platform» a secas no vale; el desafio
# lleva `_cf_chl_opt` y `orchestrate/`. Y ni asi bastan solos: van con «la
# pagina no tiene contenido» (ver `_sin_contenido`).
_MARCADORES_WAF = (
    "_cf_chl_opt",                          # Cloudflare, desafio JS / gestionado
    "/cdn-cgi/challenge-platform/h/",       # Cloudflare, orquestador del desafio
    "cf-browser-verification",              # Cloudflare, desafio antiguo
    "_incapsula_resource",                  # Imperva / Incapsula
    "incapsula incident id",
    "the requested url was rejected. please consult with your administrator",  # F5 ASM
    "awswafintegration",                    # AWS WAF, desafio
    "gokuprops",                            # AWS WAF, desafio
    "errors.edgesuite.net",                 # Akamai, pagina de bloqueo
)
_MARCADORES_CAPTCHA = (
    "captcha-delivery.com",                 # DataDome
    "px-captcha",                           # PerimeterX / HUMAN
    "_pxcaptcha",
)

# Por debajo de esto, una pagina «no tiene contenido». Un desafio de Cloudflare
# deja 10-20 palabras; la pagina de rechazo de F5, unas 25. La pagina real mas
# corta de un sitio (un aviso legal, una ficha) pasa de largo.
PALABRAS_DE_UN_MURO = 150

# Cuanto cuerpo se mira. Un desafio pesa 5-30 kB; leer mas solo gasta.
_BYTES_A_MIRAR = 256_000

# Bytes de control que no tiene ningun HTML: todo menos tabulador, saltos de
# linea y avance de pagina. En bytes al azar son el 11% (29 de 256 valores).
_CONTROL = frozenset(list(range(0, 9)) + [11] + list(range(14, 32)) + [127])
_UMBRAL_CONTROL = 0.05
_MAGIAS_BINARIAS = (b"\x1f\x8b", b"\x28\xb5\x2f\xfd", b"PK\x03\x04", b"%PDF")

_RE_TITLE = re.compile(rb"<title[^>]*>(.*?)</title", re.I | re.S)
_RE_SCRIPTS = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>", re.I | re.S)
_RE_TAGS = re.compile(r"<[^>]+>")
_RE_ENTIDADES = re.compile(r"&#?\w+;")
_RE_PALABRA = re.compile(r"\w+", re.U)

# Una redireccion a otro host es «login requerido» solo si el destino lo dice
# sin dudas: en el host (`login.`, `sso.`, `auth.`, `accounts.`) o en la ruta.
_HOSTS_LOGIN = ("login.", "sso.", "auth.", "accounts.", "signin.", "idp.", "cas.")
_RE_RUTA_LOGIN = re.compile(
    r"/(?:login|signin|sign-in|log-in|sso|saml2?|oauth2?|adfs|cas/login|"
    r"wp-login\.php|auth/realms)(?:[/?.#]|$)", re.I)


def _cabecera(cabeceras: Mapping[str, Any] | None, nombre: str) -> str:
    if not cabeceras:
        return ""
    for k, v in cabeceras.items():
        if str(k).lower() == nombre:
            if isinstance(v, (list, tuple)):
                v = ", ".join(str(x) for x in v)
            if isinstance(v, bytes):
                v = v.decode("latin-1", "replace")
            return str(v)
    return ""


def es_titulo_de_muro(titulo: str | None) -> bool:
    """¿Es el `<title>` de un muro conocido? Al principio y por palabra entera."""
    t = (titulo or "").strip().lower()
    return bool(t) and bool(_RE_TITULO_MURO.match(t) or _RE_TITULO_CAPTCHA.match(t))


def _titulo(cuerpo: bytes) -> str:
    m = _RE_TITLE.search(cuerpo[:64_000])
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1).decode("utf-8", "replace")).strip().lower()


def palabras_visibles(texto: str) -> int:
    """Estimacion barata de las palabras visibles: sin scripts, estilos ni
    etiquetas. No es `extract_word_count` (que no esta en `shared/`); solo hace
    falta para separar «12 palabras» de «900»."""
    sin_scripts = _RE_SCRIPTS.sub(" ", texto)
    sin_tags = _RE_ENTIDADES.sub(" ", _RE_TAGS.sub(" ", sin_scripts))
    return len(_RE_PALABRA.findall(sin_tags))


def _es_binario(cuerpo: bytes) -> bool:
    muestra = cuerpo[:4096]
    if not muestra:
        return False
    if any(muestra.startswith(m) for m in _MAGIAS_BINARIAS):
        return True
    control = sum(1 for b in muestra if b in _CONTROL)
    return control / len(muestra) > _UMBRAL_CONTROL


def _es_login(url_origen: str, destino: str | None) -> bool:
    if not destino:
        return False
    try:
        o, d = urlparse(url_origen or ""), urlparse(destino)
    except ValueError:
        return False
    host_o, host_d = (o.hostname or "").lower(), (d.hostname or "").lower()
    if not host_d or host_d == host_o:
        return False
    return host_d.startswith(_HOSTS_LOGIN) or bool(_RE_RUTA_LOGIN.search(d.path or ""))


def clasificar_respuesta(
    status: int | None,
    cabeceras: Mapping[str, Any] | None = None,
    cuerpo: bytes | None = None,
    *,
    content_type: str | None = None,
    url: str = "",
    destino: str | None = None,
) -> tuple[str, str | None]:
    """``(clase, motivo)`` de una respuesta. El motivo dice que senal decidio.

    - `status`, `cabeceras` (nombre -> valor; listas o bytes valen) y `cuerpo`
      tal como llegaron (ya descomprimido).
    - `destino`: el `Location` de una redireccion, ya absoluto.
    """
    cuerpo = cuerpo or b""
    if content_type is None:
        content_type = _cabecera(cabeceras, "content-type")
    ct = (content_type or "").lower()

    # Inequivocas: el propio WAF lo dice en la cabecera.
    mitigado = _cabecera(cabeceras, "cf-mitigated").lower()
    if "challenge" in mitigado:
        return CHALLENGE_WAF, "cabecera cf-mitigated: challenge"
    if status == 429:
        return RATE_LIMITED, "HTTP 429"
    if status == 503 and _cabecera(cabeceras, "retry-after"):
        return RATE_LIMITED, "HTTP 503 con Retry-After"
    if status == 401:
        return LOGIN_REQUERIDO, "HTTP 401"
    if status is not None and 300 <= status < 400:
        if _es_login(url, destino):
            return LOGIN_REQUERIDO, f"redireccion a un login de otro host ({destino})"
        return CONTENIDO, None

    es_html = "html" in ct or (not ct and cuerpo[:512].lstrip()[:1] == b"<")
    if not es_html:
        return CONTENIDO, None
    if not cuerpo.strip():
        if status is not None and 200 <= status < 300 and status != 204:
            return DESCONOCIDO, "HTML declarado con el cuerpo vacio"
        return CONTENIDO, None

    # Lo que llega declarado como HTML y no se puede leer: el caso de la
    # decision 11, un F5 que contesta con basura binaria a una huella TLS que
    # no le gusta. Se guardaba con 1 URL y el job terminaba como si nada.
    if _es_binario(cuerpo):
        return RESPUESTA_BINARIA, "cuerpo declarado HTML con bytes binarios"

    texto_bajo = cuerpo[:_BYTES_A_MIRAR].decode("utf-8", "replace").lower()
    titulo = _titulo(cuerpo)
    marcador_waf = next((m for m in _MARCADORES_WAF if m in texto_bajo), None)
    marcador_captcha = next((m for m in _MARCADORES_CAPTCHA if m in texto_bajo), None)
    titulo_captcha = bool(_RE_TITULO_CAPTCHA.match(titulo))
    titulo_propio = bool(_RE_TITULO_PROPIO.match(titulo))
    titulo_generico = not titulo_propio and bool(_RE_TITULO_MURO.match(titulo))
    if not (marcador_waf or marcador_captcha or titulo_captcha or titulo_propio
            or titulo_generico):
        return CONTENIDO, None

    # Segunda senal: la pagina no tiene contenido. Un codigo de error confirma
    # un marcador o un titulo PROPIO de un WAF; un titulo generico solo cuenta
    # con 2xx (ver TITULOS_GENERICOS).
    error = status is not None and status >= 400
    pocas = palabras_visibles(texto_bajo) < PALABRAS_DE_UN_MURO
    if marcador_captcha or titulo_captcha:
        if error or pocas:
            return CAPTCHA, (f"marcador {marcador_captcha!r}" if marcador_captcha
                             else f"titulo {titulo[:60]!r}")
        return CONTENIDO, None
    if marcador_waf or titulo_propio:
        if error or pocas:
            return CHALLENGE_WAF, (f"marcador {marcador_waf!r}" if marcador_waf
                                   else f"titulo {titulo[:60]!r}")
        return CONTENIDO, None
    if titulo_generico and not error and pocas:
        return CHALLENGE_WAF, f"titulo {titulo[:60]!r} servido con {status}"
    return CONTENIDO, None


# ---------------------------------------------------------------------------
# Salud del rastreo
# ---------------------------------------------------------------------------

VENTANA_POR_DEFECTO = 50
UMBRAL_POR_DEFECTO = 0.8


def causa_de_bloqueo(recuentos: Mapping[str, int], umbral: float,
                     minimo: int = 1) -> str | None:
    """Motivo de bloqueo si las respuestas no validas llegan al umbral.

    `recuentos` es clase -> respuestas. Devuelve `rate_limited` si lo que mas
    pesa entre las no validas es el 429, y `bloqueado_por_waf` si no. Con menos
    de `minimo` respuestas no se juzga.
    """
    total = sum(n for c, n in recuentos.items() if c)
    if total < max(1, minimo):
        return None
    malas = {c: n for c, n in recuentos.items() if c in NO_VALIDAS and n}
    if not malas or sum(malas.values()) / total < umbral:
        return None
    rl = malas.get(RATE_LIMITED, 0)
    return RATE_LIMITED if rl * 2 > sum(malas.values()) else BLOQUEADO_POR_WAF


class VentanaDeSalud:
    """Las ultimas `ventana` respuestas, y si el rastreo esta bloqueado.

    Por ventana y no por total: un sitio que bloquea a partir de la URL 2.000
    tiene un total del 5% de muros cuando ya lleva 100 seguidas. Solo se juzga
    con la ventana LLENA, para que tres muros al arrancar no paren un rastreo;
    el rastreo corto que acaba entero detras de un muro (el de una URL de la
    decision 11) lo juzga el worker al cerrar, sobre el total.
    """

    def __init__(self, ventana: int = VENTANA_POR_DEFECTO,
                 umbral: float = UMBRAL_POR_DEFECTO,
                 previas: Iterable[str] = ()):
        self.ventana = max(1, int(ventana))
        self.umbral = float(umbral)
        self._ultimas: deque[str] = deque(maxlen=self.ventana)
        self.totales: Counter[str] = Counter()
        # Las de una ejecucion anterior del mismo job (reanudacion): asi un job
        # bloqueado que se reanuda no necesita otras 50 respuestas para volver
        # a saberlo. Solo llenan la ventana; los totales son de esta ejecucion.
        for clase in previas:
            if clase:
                self._ultimas.append(clase)

    def anotar(self, clase: str | None) -> None:
        if not clase:
            return
        self._ultimas.append(clase)
        self.totales[clase] += 1

    def recuentos(self) -> dict[str, int]:
        return dict(Counter(self._ultimas))

    def veredicto(self) -> str | None:
        if len(self._ultimas) < self.ventana:
            return None
        return causa_de_bloqueo(Counter(self._ultimas), self.umbral)


def resumen_de_salud(recuentos: Mapping[str, int], umbral: float,
                     causa: str | None = None) -> dict[str, Any]:
    """Lo que se guarda en `jobs.health`: recuentos por clase y la causa."""
    por_clase = {c: int(n) for c, n in recuentos.items() if c and n}
    total = sum(por_clase.values())
    no_validas = sum(n for c, n in por_clase.items() if c in NO_VALIDAS)
    return {
        "por_clase": por_clase,
        "respuestas": total,
        "no_validas": no_validas,
        "proporcion_no_validas": round(no_validas / total, 4) if total else None,
        "umbral": umbral,
        "causa": causa,
    }


__all__ = [
    "CLASES", "NO_VALIDAS", "ESTADO_INDEXABILIDAD", "MOTIVOS_BLOQUEO",
    "BLOQUEADO_POR_WAF", "TITULOS_DE_BLOQUEO", "clasificar_respuesta",
    "es_titulo_de_muro", "causa_de_bloqueo", "VentanaDeSalud", "resumen_de_salud",
]
