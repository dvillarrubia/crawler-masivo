"""Si la lectura de los sitemaps de un censo fue completa (decision 205).

Con algun fichero que no se pudo leer (lectura parcial, un fichero que no
respondio 200 o uno que respondio 200 sin un sitemap reconocible, como los 370
vacios de Lopesan), «No incluida en el sitemap» no se puede afirmar: la pagina
puede estar en el fichero que falta. Tampoco su diferencia frente a otro censo,
si cualquiera de los dos se leyo a medias.

Es la misma regla que `resumenSitemaps(job).dudoso` del frontend (revision de
UX del 25-oct, 3.6; V13); tests/test_sitemaps_lectura.py ejecuta las dos sobre
los mismos casos para que no discrepen.
"""

from __future__ import annotations

from typing import Any

# El tipo cuya cifra depende de haber leido todos los sitemaps.
TIPOS_DE_SITEMAP = ("not_in_sitemap",)


def lectura_dudosa(sitemaps: Any) -> bool:
    """True si `jobs.sitemaps` dice que algun fichero no se pudo leer. Sin
    resumen (no se pidieron sitemaps, o un censo anterior a la decision 126)
    no hay lectura que dudar: False."""
    if not isinstance(sitemaps, dict):
        return False
    if sitemaps.get("parcial"):
        return True
    for f in sitemaps.get("ficheros") or []:
        if not isinstance(f, dict):
            continue
        status = f.get("status")
        if status is not None and status != 200:
            return True
        formato = f.get("formato")
        if (not formato or formato == "desconocido") and status == 200:
            return True
    return False


def sin_tipos_de_sitemap(inc: dict, dudoso: bool) -> dict:
    """Los totales de `comparacion.incidencias` (nuevas, resueltas, siguen)
    sin los tipos de sitemap cuando `dudoso`. `sin_sitemap` dice si se quito
    algo, para decirlo junto a la cifra."""
    salida: dict[str, Any] = {"sin_sitemap": False}
    for campo in ("nuevas", "resueltas", "siguen"):
        bloque = inc.get(campo)
        if not isinstance(bloque, dict):
            salida[campo] = None
            continue
        total = bloque.get("total")
        quitar = 0
        if dudoso:
            quitar = sum(int(f.get("total") or 0) for f in bloque.get("por_tipo") or []
                         if isinstance(f, dict) and f.get("tipo") in TIPOS_DE_SITEMAP)
        if quitar:
            salida["sin_sitemap"] = True
        salida[campo] = None if total is None else total - quitar
    return salida
