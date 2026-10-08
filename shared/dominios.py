"""Que cuenta como "el mismo sitio", segun la Public Suffix List.

Vive en `shared/` y no en el extractor por lo mismo que las reglas de robots
(decision 23): lo necesitan el spider —para decidir el alcance del rastreo— y
el analyzer —para decidir si un canonical se va de casa—, y la imagen del
analisis solo copia `shared/` y `analysis/`. Tenerlo por duplicado seria tener
dos criterios de "mismo sitio" que acabarian discrepando.
"""

from __future__ import annotations

_PSL = None


def dominio_registrable(host: str) -> str:
    """Dominio que se puede registrar, segun la Public Suffix List.

    Cortar por las dos ultimas etiquetas daba `co.uk` para
    `www.competidor.co.uk`, asi que cualquier `.co.uk` salia interno para una
    semilla `.co.uk`: el rastreo se metia en el sitio de la competencia y sus
    paginas entraban en el informe del cliente. Devuelve el host tal cual si la
    lista no reconoce el sufijo (IPs, `localhost`, hosts internos).
    """
    global _PSL
    if not host:
        return ""
    if _PSL is None:
        # Sin respaldo a proposito. Habia un `except` que caia a las dos
        # ultimas etiquetas, que es EXACTAMENTE la regla que esta funcion
        # existe para no usar: con ella, cualquier `.co.uk` sale interno para
        # una semilla `.co.uk`. Un fallo de empaquetado tiene que verse como
        # un fallo de empaquetado, no reaparecer como un criterio de SEO
        # distinto segun la imagen donde corra el codigo. Ninguna de las tres
        # imagenes la declaraba: el crawler la heredaba de Scrapy, `analysis/`
        # no la tenia y la API tampoco.
        import tldextract

        _PSL = tldextract.TLDExtract(
            suffix_list_urls=(), include_psl_private_domains=True
        )
    try:
        extraido = _PSL(host)
    except Exception:
        return host
    if extraido.domain and extraido.suffix:
        return f"{extraido.domain}.{extraido.suffix}"
    return host
