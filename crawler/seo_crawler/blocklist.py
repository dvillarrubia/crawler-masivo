"""Que peticiones NO merece la pena cargar en el navegador.

Funcion pura, sin imports de Scrapy ni de Playwright, para poder probarla.

El filtro por `resource_type` ya descarta imagenes, fuentes y media, pero la
analitica y la publicidad entran como `script` y `xhr`: son justo lo que mas
tarda en una pagina con muchas etiquetas, y no aportan NADA a un analisis SEO
(no generan enlaces, ni texto, ni metadatos).

La lista es deliberadamente corta y conservadora: solo dominios cuya unica
funcion es medir o anunciar. Ante la duda, NO se anade — bloquear de mas
significa perder contenido sin enterarse, que es el peor fallo posible en un
rastreador y ya nos ha costado dos veces (ver CLAUDE.md 9, 10, 10b).

Se comprueba contra el HOST de la URL, nunca contra la URL entera: buscar
"ads" como subcadena bloquearia `/anuncios-de-empleo` o `ads.example.com/blog`.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Tipos de recurso que el navegador no necesita descargar para que podamos
# leer el HTML renderizado.
TIPOS_BLOQUEADOS: frozenset[str] = frozenset({
    "image", "media", "font", "texttrack", "eventsource",
    "websocket", "manifest", "other",
})

# Dominios de analitica, tag managers, publicidad y mapas de calor. Se bloquea
# el dominio y sus subdominios.
DOMINIOS_BLOQUEADOS: frozenset[str] = frozenset({
    # analitica y tag managers
    "google-analytics.com",
    "googletagmanager.com",
    "analytics.google.com",
    "segment.com",
    "segment.io",
    "mixpanel.com",
    "amplitude.com",
    "matomo.cloud",
    "scorecardresearch.com",
    "quantserve.com",
    "chartbeat.com",
    "newrelic.com",
    "nr-data.net",
    # mapas de calor y grabacion de sesion
    "hotjar.com",
    "hotjar.io",
    "clarity.ms",
    "mouseflow.com",
    "fullstory.com",
    "luckyorange.com",
    "crazyegg.com",
    # publicidad
    "doubleclick.net",
    "googleadservices.com",
    "googlesyndication.com",
    "adservice.google.com",
    "adnxs.com",
    "criteo.com",
    "criteo.net",
    "taboola.com",
    "outbrain.com",
    "pubmatic.com",
    "rubiconproject.com",
    "openx.net",
    "casalemedia.com",
    "33across.com",
    "smartadserver.com",
    "teads.tv",
    # pixeles de redes sociales (el pixel, no el contenido incrustado)
    "connect.facebook.net",
    "analytics.tiktok.com",
    "ads-twitter.com",
    "ads.linkedin.com",
    "bat.bing.com",
    "snap.licdn.com",
})


def _host_bloqueado(host: str) -> bool:
    """True si *host* es uno de los dominios bloqueados o un subdominio suyo."""
    host = host.lower().strip(".")
    if not host:
        return False
    partes = host.split(".")
    # Se prueba el host entero y cada sufijo: `www.a.hotjar.com` -> `hotjar.com`
    for i in range(len(partes) - 1):
        if ".".join(partes[i:]) in DOMINIOS_BLOQUEADOS:
            return True
    return False


def debe_abortarse(resource_type: str | None, url: str | None) -> bool:
    """Decide si el navegador puede saltarse esta peticion.

    *resource_type* es el de Playwright (`image`, `script`, `xhr`…) y *url* la
    URL completa de la peticion. El documento principal nunca se bloquea.
    """
    if resource_type == "document":
        return False
    if resource_type in TIPOS_BLOQUEADOS:
        return True
    if not url:
        return False
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return _host_bloqueado(host)
