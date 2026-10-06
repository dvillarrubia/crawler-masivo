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

from urllib.parse import urlsplit, urlunsplit

from shared.robots import hay_noindex, robots_tokens

# Bot cuyas directivas especificas manda. Configurable por si algun dia se
# audita para otro buscador.
BOT_POR_DEFECTO = "googlebot"

_PUERTOS_POR_DEFECTO = {"http": "80", "https": "443"}


def misma_url(a: str | None, b: str | None) -> bool:
    """¿Son la misma URL a efectos de canonical?

    Se ignoran el fragmento, el puerto por defecto, la caja del host y la barra
    final cuando la ruta esta vacia. NO se ignoran el esquema ni el `www`:
    un canonical de https a http, o de sin-www a www, es una canonicalizacion
    real y hay que verla.
    """
    if not a or not b:
        return False
    return _clave_url(a) == _clave_url(b)


def _clave_url(url: str) -> tuple[str, str, str, str]:
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


def _directivas(meta_robots, x_robots, bot: str) -> set[str]:
    """Directivas que aplican a *bot*, combinando todas las fuentes.

    `meta_robots` puede ser una cadena (el `robots` generico) o un mapa
    nombre -> contenido con las metas por bot. `x_robots` puede ser una cadena
    o una lista de cabeceras; cada una admite el prefijo `googlebot:`.
    """
    tokens: set[str] = set()

    if isinstance(meta_robots, dict):
        for nombre, valor in meta_robots.items():
            if (nombre or "").strip().lower() in ("robots", bot):
                tokens |= robots_tokens(valor)
    else:
        tokens |= robots_tokens(meta_robots)

    cabeceras = x_robots if isinstance(x_robots, (list, tuple)) else [x_robots]
    for cabecera in cabeceras:
        if not cabecera:
            continue
        # "googlebot: noindex" aplica solo a ese bot; sin prefijo, a todos.
        if ":" in cabecera:
            posible_bot, resto = cabecera.split(":", 1)
            posible_bot = posible_bot.strip().lower()
            # Ojo: `max-snippet:-1` tambien lleva dos puntos y NO es un bot.
            if posible_bot and posible_bot not in robots_tokens(cabecera) and " " not in posible_bot:
                if posible_bot in ("robots", bot):
                    tokens |= robots_tokens(resto)
                continue
        tokens |= robots_tokens(cabecera)

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
) -> tuple[bool, str]:
    """``(es_indexable, motivo)``. El motivo describe por que NO lo es.

    El orden importa: se devuelve la razon que un SEO miraria primero. Una
    pagina bloqueada por robots.txt no se mira el canonical, porque el buscador
    no va a llegar a leerlo.
    """
    if bloqueada_por_robots:
        return (False, "Blocked by robots.txt")

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


__all__ = ["estado_indexabilidad", "es_noindex", "misma_url", "hay_noindex"]
