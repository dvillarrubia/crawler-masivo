"""Una sola funcion para decidir si una pagina es indexable, y por que no.

Vive en ``shared`` por la misma razon que ``shared/robots.py``: el spider y el
analyzer tienen que dar la MISMA respuesta y sus imagenes no comparten codigo
(la del analisis solo copia ``shared/`` y ``analysis/``). Tenerlo por duplicado
ya salio caro: el spider comparaba el canonical contra la URL de partida de una
cadena de redirecciones y el analyzer contra la final, asi que la misma pagina
era "Canonicalised" para uno e "Indexable" para el otro —1.254 paginas de un
censo de 34.704— y las dos columnas del mismo CSV se contradecian.

Casos que las dos versiones anteriores fallaban, cada uno con su criterio SEO:

- **Robots por bot.** `<meta name="googlebot" content="noindex">` saca la pagina
  del indice de Google aunque el `robots` generico diga `index`. Lo que manda es
  la directiva mas especifica para el bot que nos importa.
- **Varias cabeceras `X-Robots-Tag`.** Un servidor puede mandar dos; leyendo
  solo la ultima, `noindex` + `noarchive` salia indexable. Se combinan todas.
- **Canonical en la cabecera HTTP `Link`.** Google lo respeta igual que el del
  HTML. Si no hay canonical en el HTML, manda el de la cabecera.
- **204 y demas 2xx que no son 200.** Una respuesta sin contenido no tiene nada
  que indexar. Solo el 200 es indexable.
- **Comparar URLs.** `https://x.com:443/a` y `https://x.com/a` son la MISMA
  (el puerto por defecto no cuenta), pero `http://` frente a `https://`, y `www`
  frente a sin `www`, son DISTINTAS: ahi hay una canonicalizacion de verdad y
  taparla es perder el hallazgo.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from shared.respuestas import ESTADO_INDEXABILIDAD, NO_VALIDAS
from shared.robots import DIRECTIVAS, hay_noindex, robots_tokens

# Bot cuyas directivas especificas manda. Configurable por si algun dia se
# audita para otro buscador.
BOT_POR_DEFECTO = "googlebot"

_PUERTOS_POR_DEFECTO = {"http": "80", "https": "443"}

# Las cabeceras `X-Robots-Tag` se guardan en UNA columna separadas por salto de
# linea, no por coma. Cada cabecera puede ir dirigida a un bot
# (`googlebot: noindex`) y unidas con coma se perdia donde acababa cada una:
# `bingbot: noindex, googlebot: noindex` se leia como una sola dirigida a
# bingbot y la pagina salia indexable. El salto de linea no puede aparecer
# dentro de una cabecera HTTP, y `robots_tokens` lo trata como separador.
SEPARADOR_CABECERAS = "\n"


def misma_url(a: str | None, b: str | None) -> bool:
    """¿Son la misma URL a efectos de canonical?

    Se ignoran el fragmento, el puerto por defecto, la caja del host y la barra
    final cuando la ruta esta vacia. NO se ignoran el esquema ni el `www`:
    un canonical de https a http, o de sin-www a www, es una canonicalizacion
    real y hay que verla.
    """
    if not a or not b:
        return False
    return clave_url(a) == clave_url(b)


def clave_url(url: str) -> tuple[str, str, str, str]:
    """Clave de igualdad de :func:`misma_url`, para usarla en un diccionario.

    El analyzer busca el destino de un canonical o de un hreflang entre las URLs
    rastreadas; si lo indexara con otro normalizador, la misma pareja de URLs
    seria "la misma" para la indexabilidad y "otra" para el canonical roto o el
    retorno del hreflang (decision 39).
    """
    # w3lib primero, para la codificacion y el orden de los parametros: la
    # misma URL se escribe `intel%C2%B7ligencia` en el enlace y `intel·ligencia`
    # en el canonical, y sin normalizar eso salian 83 paginas de un censo como
    # "Canonicalised" siendo autocanonicas. Pasa en catalan y en cualquier
    # idioma con acentos en la URL.
    try:
        from w3lib.url import canonicalize_url

        url = canonicalize_url(url, keep_fragments=False)
    except Exception:
        pass
    partes = urlsplit(url.strip())
    host = (partes.hostname or "").lower()
    puerto = partes.port
    if puerto is not None and str(puerto) != _PUERTOS_POR_DEFECTO.get(partes.scheme, ""):
        host = f"{host}:{puerto}"
    ruta = partes.path or "/"
    if ruta == "":
        ruta = "/"
    return (partes.scheme.lower(), host, ruta, partes.query)


# Forma de un nombre de bot en `X-Robots-Tag: <bot>: <directivas>`: un token
# sin espacios. Lo que lleva espacios antes de los dos puntos es el trozo de
# una fecha (`25-Jun-2010 15:00:00 GMT`), no un bot.
_NOMBRE_DE_BOT = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def x_robots_por_bot(cabecera: str | None) -> list[tuple[str | None, str]]:
    """Partir UNA cabecera `X-Robots-Tag` en ``(bot, directivas)``.

    ``bot`` es ``None`` para lo que no va dirigido a nadie (vale para todos).
    Un ``nombre:`` abre el ambito de ese bot y dura hasta el siguiente, asi que
    `bingbot: noindex, nofollow` es todo de Bing y en
    `bingbot: noindex, googlebot: noindex` el segundo `noindex` es de Google.

    Solo es bot un prefijo que NO es una directiva y tiene forma de token. Las
    directivas con valor llevan dos puntos (`max-snippet:-1`,
    `unavailable_after: 2020-01-01`) y las fechas tambien (`15:00:00`), y antes
    cualquiera de ellas se tomaba por bot y se perdia la cabecera entera: el
    `noindex` de `max-snippet:-1, noindex` no contaba (decision 107).
    """
    trozos: list[tuple[str | None, str]] = []
    ambito: str | None = None
    for trozo in (cabecera or "").split(","):
        cabeza, dos_puntos, resto = trozo.partition(":")
        nombre = cabeza.strip().lower()
        if dos_puntos and nombre not in DIRECTIVAS and _NOMBRE_DE_BOT.match(nombre):
            ambito = nombre
            trozo = resto
        trozos.append((ambito, trozo))
    return trozos


def directivas_x_robots(cabecera: str | None, bot: str = BOT_POR_DEFECTO) -> set[str]:
    """Directivas de UNA cabecera `X-Robots-Tag` que aplican a *bot*.

    Cuentan las que no van dirigidas a nadie, las de `robots:` y las de *bot*
    (sin mirar la caja: `GoogleBot:` es Googlebot). `googlebot-news:` es otro
    bot. Con varias cabeceras, se llama una vez por cada una.
    """
    tokens: set[str] = set()
    for ambito, trozo in x_robots_por_bot(cabecera):
        if ambito in (None, "robots", bot):
            tokens |= robots_tokens(trozo)
    return tokens


def x_robots_para_bot(x_robots, bot: str = BOT_POR_DEFECTO) -> str:
    """El texto de las `X-Robots-Tag` que aplica a *bot*, sin los prefijos.

    Para quien tiene que leer la sintaxis con el mismo criterio que la
    indexabilidad (`robots_invalid_syntax`): un `noindex/nofollow` dirigido a
    otro bot no le quita nada a Google. Acepta lo mismo que `_directivas`: una
    lista de cabeceras o lo guardado en BD, una por linea.
    """
    if isinstance(x_robots, (list, tuple)):
        cabeceras = list(x_robots)
    else:
        cabeceras = (x_robots or "").split(SEPARADOR_CABECERAS)
    return ", ".join(
        trozo.strip()
        for cabecera in cabeceras
        for ambito, trozo in x_robots_por_bot(cabecera)
        if ambito in (None, "robots", bot) and trozo.strip()
    )


def _directivas(meta_robots, x_robots, bot: str) -> set[str]:
    """Directivas que aplican a *bot*, combinando todas las fuentes.

    `meta_robots` puede ser una cadena (el `robots` generico) o un mapa
    nombre -> contenido con las metas por bot. `x_robots` puede ser una cadena
    o una lista de cabeceras; cada una admite prefijos de bot
    (:func:`directivas_x_robots`).
    """
    tokens: set[str] = set()

    if isinstance(meta_robots, dict):
        for nombre, valor in meta_robots.items():
            if (nombre or "").strip().lower() in ("robots", bot):
                tokens |= robots_tokens(valor)
    else:
        tokens |= robots_tokens(meta_robots)

    if isinstance(x_robots, (list, tuple)):
        cabeceras = list(x_robots)
    else:
        # Lo guardado en BD: una cadena con una cabecera por linea.
        cabeceras = (x_robots or "").split(SEPARADOR_CABECERAS)
    for cabecera in cabeceras:
        tokens |= directivas_x_robots(cabecera, bot)

    return tokens


def estado_indexabilidad(
    status_code: int | None,
    meta_robots=None,
    x_robots=None,
    canonical_href: str | None = None,
    page_url: str = "",
    canonical_header: str | None = None,
    bloqueada_por_robots: bool = False,
    bot: str = BOT_POR_DEFECTO,
    response_class: str | None = None,
    canonicals_distintos: int | None = None,
) -> tuple[bool, str]:
    """``(es_indexable, motivo)``. El motivo describe por que NO lo es.

    El orden importa: se devuelve la razon que un SEO miraria primero. Una
    pagina bloqueada por robots.txt no se mira el canonical, porque el buscador
    no va a llegar a leerlo.

    `response_class` es la de `shared/respuestas.py`. Una respuesta que no es
    la pagina —un desafio de un WAF servido con 200, un captcha, un 429, un
    cuerpo binario— no es indexable ni deja de serlo por lo que diga: no se ha
    visto la pagina. Va antes que el codigo porque un 403 de Cloudflare no es
    un «Client Error (403)» del sitio (decision 103).

    `canonicals_distintos` es cuantos destinos distintos declaran los
    canonicals del head. Con mas de uno Google los ignora TODOS y elige por su
    cuenta (decision 115): la pagina no esta canonicalizada a ninguno, y
    tampoco se usa el de la cabecera `Link`, que es otra senal mas del mismo
    conflicto. None = no se sabe (rastreo anterior): manda el primero, como
    antes.
    """
    if bloqueada_por_robots:
        return (False, "Blocked by robots.txt")
    if response_class in NO_VALIDAS:
        return (False, ESTADO_INDEXABILIDAD[response_class])

    if status_code is None:
        return (False, "Sin respuesta")
    if 500 <= status_code < 600:
        return (False, f"Server Error ({status_code})")
    if 400 <= status_code < 500:
        return (False, f"Client Error ({status_code})")
    if 300 <= status_code < 400:
        return (False, f"Redirect ({status_code})")
    if status_code != 200:
        # 204, 206... hay respuesta pero no hay pagina que indexar.
        return (False, f"Sin contenido ({status_code})")

    tokens = _directivas(meta_robots, x_robots, bot)
    if "noindex" in tokens or "none" in tokens:
        return (False, "Noindex")

    if canonicals_distintos is not None and canonicals_distintos > 1:
        return (True, "Indexable")

    # El del HTML manda; si no hay, vale el de la cabecera Link.
    canonical = (canonical_href or "").strip() or (canonical_header or "").strip()
    if canonical and page_url and not misma_url(canonical, page_url):
        return (False, "Canonicalised")

    return (True, "Indexable")


def es_noindex(meta_robots=None, x_robots=None, bot: str = BOT_POR_DEFECTO) -> bool:
    """Solo la directiva, sin mirar el codigo ni el canonical.

    Lo usa el conteo de enlaces entrantes: un enlace desde una pagina noindex no
    cuenta (C3 de #24), y eso depende de la directiva, no de por que otra razon
    la pagina no sea indexable.
    """
    tokens = _directivas(meta_robots, x_robots, bot)
    return "noindex" in tokens or "none" in tokens


def es_nofollow(meta_robots=None, x_robots=None, bot: str = BOT_POR_DEFECTO) -> bool:
    """¿La directiva de pagina convierte todos sus enlaces en nofollow?

    Con las mismas reglas que el `noindex`: un `<meta name="googlebot"
    content="nofollow">` cuenta, y un `X-Robots-Tag: bingbot: nofollow` no.
    """
    tokens = _directivas(meta_robots, x_robots, bot)
    return "nofollow" in tokens or "none" in tokens


__all__ = [
    "estado_indexabilidad", "es_noindex", "es_nofollow", "misma_url", "clave_url",
    "hay_noindex", "directivas_x_robots", "x_robots_por_bot", "x_robots_para_bot",
    "SEPARADOR_CABECERAS",
]
