"""Lo que se juzga de un fichero de sitemap y de sus entradas (decision 126).

En `shared/` y no en el crawler porque lo usan los dos lados: el spider guarda
lo que leyo (`crawler/seo_crawler/sitemaps.py`) y el analisis lo juzga, y la
imagen del analisis solo copia `shared/` y `analysis/` (decision 23).

Los limites son los de Google, no los topes del rastreador: «All formats limit
a single sitemap to 50MB (uncompressed) or 50,000 URLs» (Search Central, «Build
and submit a sitemap»), y lo mismo para un indice («up to 50,000 sitemaps»).
`MAX_URLS_PER_SITEMAP` y `MAX_SITEMAP_BYTES` del crawler se configuran por
entorno y protegen al worker; que alguien los suba no puede cambiar lo que se
avisa.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

LIMITE_URLS = 50_000
LIMITE_BYTES = 50 * 1024 * 1024

# Un lastmod se da por futuro si pasa de la hora a la que se LEYO el fichero
# mas este margen: un sitio en UTC+14 puede escribir «mañana» sin mentir, y una
# fecha sin hora se lee como las 00:00 UTC de ese dia.
MARGEN_LASTMOD = timedelta(days=1)

# Formato W3C Datetime (el que pide el protocolo): YYYY, YYYY-MM, YYYY-MM-DD,
# y con hora hh:mm[:ss[.s]] y zona (Z o +hh:mm). Google admite tambien la hora
# sin zona, que aqui se lee como UTC.
_LASTMOD = re.compile(
    r"^(?P<y>\d{4})(?:-(?P<m>\d{2})(?:-(?P<d>\d{2})"
    r"(?:[T ](?P<H>\d{2}):(?P<M>\d{2})(?::(?P<S>\d{2})(?:[.,]\d+)?)?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?)?)?)?$",
    re.IGNORECASE,
)


def leer_lastmod(valor: str | None) -> datetime | None:
    """El ``lastmod`` como instante en UTC, o None si no se puede leer.

    No adivina: «18/10/2026» o «hace 2 dias» son None, no una fecha. Lo que no
    se puede leer no se juzga como futuro (se cuenta aparte).
    """
    if not valor:
        return None
    m = _LASTMOD.match(valor.strip())
    if not m:
        return None
    try:
        tz = timezone.utc
        if m["tz"] and m["tz"].upper() != "Z":
            signo = -1 if m["tz"][0] == "-" else 1
            digitos = m["tz"][1:].replace(":", "")
            tz = timezone(signo * timedelta(hours=int(digitos[:2]), minutes=int(digitos[2:])))
        instante = datetime(
            int(m["y"]), int(m["m"] or 1), int(m["d"] or 1),
            int(m["H"] or 0), int(m["M"] or 0), int(m["S"] or 0), tzinfo=tz,
        )
    except ValueError:  # 2026-02-30, 25:00, zona fuera de rango
        return None
    return instante.astimezone(timezone.utc)


def es_futuro(lastmod: datetime | None, leido: datetime) -> bool:
    """Si un lastmod ya leido es posterior a la lectura del fichero + margen."""
    if lastmod is None:
        return False
    if leido.tzinfo is None:
        leido = leido.replace(tzinfo=timezone.utc)
    return lastmod > leido + MARGEN_LASTMOD


def motivos_de_limite(declaradas: int | None, tamano: int | None) -> list[str]:
    """Que limite de Google pasa un fichero: ``urls``, ``bytes``, los dos o
    ninguno. Con el dato a None no se afirma nada."""
    motivos = []
    if declaradas is not None and declaradas > LIMITE_URLS:
        motivos.append("urls")
    if tamano is not None and tamano > LIMITE_BYTES:
        motivos.append("bytes")
    return motivos


# ---------------------------------------------------------------------------
# Hreflang del sitemap (decision 167)
# ---------------------------------------------------------------------------
# Se guarda en `sitemap_entries.hreflang` como [[idioma, href], ...]. El href
# del mismo origen que la entrada se guarda sin el (`/en/pagina`): en un sitio
# con 20 idiomas son 20 URLs por entrada que repiten `https://www.host`, y
# quitarlo ahorra ~25 bytes por anotacion sin perder nada (`urljoin` lo
# devuelve). NULL = no se sabe (fila anterior a la columna o pasada del tope de
# anotaciones); [] = la entrada no declara ninguna.


def _origen(url: str) -> str:
    partes = urlsplit(url)
    if not partes.scheme or not partes.netloc:
        return ""
    return f"{partes.scheme}://{partes.netloc}"


def compactar_hreflang(url_entrada: str, pares) -> list[list[str]]:
    """Las anotaciones ``(idioma, href)`` de una entrada, como se guardan."""
    origen = _origen(url_entrada)
    salida: list[list[str]] = []
    for idioma, href in pares or ():
        if not idioma or not href:
            continue
        idioma = idioma.replace("\x00", "")[:20]
        href = href.replace("\x00", "")
        # Lo que queda tras el origen tiene que volver igual: `//fr/a` (una
        # ruta con doble barra, un fallo de CMS que existe) se leeria como
        # URL relativa al protocolo con host `fr`, y se guarda entera.
        if (origen and href.startswith(origen + "/")
                and not href.startswith(origen + "//")):
            href = href[len(origen):]
        salida.append([idioma, href])
    return salida


def expandir_hreflang(url_entrada: str, guardado) -> list[tuple[str, str]] | None:
    """Lo inverso de :func:`compactar_hreflang`. None si no se sabe."""
    if guardado is None:
        return None
    salida: list[tuple[str, str]] = []
    for par in guardado:
        try:
            idioma, href = par[0], par[1]
        except (TypeError, IndexError, KeyError):
            continue
        if not idioma or not href:
            continue
        if href.startswith("/") and not href.startswith("//"):
            href = urljoin(url_entrada, href)
        salida.append((idioma, href))
    return salida


def estado_hreflang_sitemap(resumen: dict | None) -> tuple[bool, bool]:
    """``(guardado, completo)`` del hreflang del sitemap de un censo.

    - *guardado*: el rastreo ya guardaba el hreflang del sitemap (la marca
      ``hreflang`` de `jobs.sitemaps`). Sin ella, la columna esta a NULL y no
      dice nada: ni que haya ni que no haya.
    - *completo*: se leyeron todos los ficheros pedidos, ninguno se corto ni se
      quedo en el tope de URLs por fichero, y no se recorto ninguna entrada ni
      ninguna anotacion. Solo entonces que una URL no tenga anotaciones en el
      sitemap es un hecho: si no, puede tenerlas en lo que no se leyo y su
      ausencia no se afirma (decision 167).
    """
    if not isinstance(resumen, dict) or not resumen.get("hreflang"):
        return False, False
    if (resumen.get("parcial") or resumen.get("entradas_recortadas")
            or resumen.get("hreflang_recortado")):
        return True, False
    for f in resumen.get("ficheros") or ():
        if not isinstance(f, dict):
            continue
        if f.get("cortado"):
            return True, False
        if (f.get("formato") == "urlset" and f.get("declaradas") is not None
                and f.get("devueltas") is not None
                and f["devueltas"] < f["declaradas"]):
            return True, False
    return True, True
