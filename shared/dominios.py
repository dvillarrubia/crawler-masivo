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
        try:
            import tldextract

            _PSL = tldextract.TLDExtract(
                suffix_list_urls=(), include_psl_private_domains=True
            )
        except Exception:  # pragma: no cover - sin tldextract
            _PSL = False
    if _PSL is False:
        partes = host.split(".")
        return ".".join(partes[-2:]) if len(partes) >= 2 else host
    try:
        extraido = _PSL(host)
    except Exception:
        return host
    if extraido.domain and extraido.suffix:
        return f"{extraido.domain}.{extraido.suffix}"
    return host
