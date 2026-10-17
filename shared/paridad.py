"""Comparacion de dos capturas de la misma URL (decision 119).

Una captura es lo que se mide de una respuesta con las mismas funciones que
el censo (`seo_crawler.sonda_paridad.huella_paridad`): title, primer H1 que
se pinta, description, canonical, `noindex`/`nofollow` por directiva, palabras
del contenido PRINCIPAL y destinos internos seguibles. Aqui solo se comparan
dos diccionarios, sin Scrapy, para que el spider (que decide si hace falta la
peticion de control) y los tests usen la misma regla.

Lo que no se copia de FreeCrawl: el robots como cadena (`noindex, follow`
frente a `follow,noindex` no es una diferencia), las palabras de la pagina
entera (el menu cuenta igual en las dos) y el canonical como texto (relativo
frente a absoluto es el mismo).
"""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import urljoin

from shared.indexabilidad import misma_url

# Palabras del contenido principal: diferencia si la menor se queda un 30% o
# mas por debajo de la mayor, y solo cuando la mayor pasa de 50 (en una pagina
# de 12 palabras, 4 de diferencia son un 33% y no dicen nada). El 30% es el
# punto de partida de FreeCrawl, no una medida nuestra: se revisa con los
# primeros censos (decision 119).
UMBRAL_PALABRAS = 0.30
PALABRAS_MINIMAS = 50
# Enlaces internos seguibles que estan en una captura y faltan en la otra:
# diferencia si faltan al menos 5 y al menos el 20% de los de la base. Un
# menu hamburguesa que se monta al pulsar se lleva decenas; un «relacionados»
# que cambia, dos o tres (y eso lo caza el control de volatilidad).
UMBRAL_ENLACES = 0.20
ENLACES_MINIMOS = 5
MAX_EJEMPLOS_ENLACES = 10

CAMPOS_TEXTO = ("title", "h1", "description")
CAMPOS = ("noindex", "canonical", "nofollow", *CAMPOS_TEXTO, "palabras", "enlaces")


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    limpio = " ".join(str(valor).split())
    return limpio or None


def canonical_efectivo(captura: dict) -> str | None:
    """La URL que la captura declara como canonica; sin canonical, ella misma."""
    url = captura.get("url")
    canonical = _texto(captura.get("canonical"))
    if canonical:
        return urljoin(url or "", canonical)
    return url


def vary_por_user_agent(vary: str | None) -> bool:
    """True si la cabecera `Vary` dice que la respuesta cambia con el UA."""
    tokens = {t.strip().lower() for t in (vary or "").split(",")}
    return "user-agent" in tokens or "*" in tokens


def diferencias(base: dict, alternativa: dict) -> list[dict[str, Any]]:
    """Lo que cambia de `base` a `alternativa`, campo a campo.

    Una entrada por campo que difiere: ``{"campo", "base", "alternativa"}`` y,
    en los enlaces, ``faltan`` (cuantos de la base no estan en la
    alternativa), ``sobran`` y unos ``ejemplos`` de los que faltan. Un campo
    que una de las dos no midio (None en palabras, sin lista de enlaces) no se
    compara: no medido no es «cero».
    """
    salida: list[dict[str, Any]] = []
    if bool(base.get("noindex")) != bool(alternativa.get("noindex")):
        salida.append({"campo": "noindex", "base": bool(base.get("noindex")),
                       "alternativa": bool(alternativa.get("noindex"))})
    ca, cb = canonical_efectivo(base), canonical_efectivo(alternativa)
    if ca and cb and not misma_url(ca, cb):
        salida.append({"campo": "canonical", "base": ca, "alternativa": cb})
    if bool(base.get("nofollow")) != bool(alternativa.get("nofollow")):
        salida.append({"campo": "nofollow", "base": bool(base.get("nofollow")),
                       "alternativa": bool(alternativa.get("nofollow"))})
    for campo in CAMPOS_TEXTO:
        ta, tb = _texto(base.get(campo)), _texto(alternativa.get(campo))
        if ta != tb:
            salida.append({"campo": campo, "base": ta, "alternativa": tb})
    pa, pb = base.get("palabras"), alternativa.get("palabras")
    if isinstance(pa, int) and isinstance(pb, int):
        mayor = max(pa, pb)
        if mayor >= PALABRAS_MINIMAS and abs(pa - pb) / mayor >= UMBRAL_PALABRAS:
            salida.append({"campo": "palabras", "base": pa, "alternativa": pb})
    ea, eb = base.get("enlaces"), alternativa.get("enlaces")
    if isinstance(ea, (list, set, tuple)) and isinstance(eb, (list, set, tuple)):
        sa, sb = set(ea), set(eb)
        faltan = sa - sb
        if sa and len(faltan) >= max(ENLACES_MINIMOS, math.ceil(UMBRAL_ENLACES * len(sa))):
            salida.append({
                "campo": "enlaces", "base": len(sa), "alternativa": len(sb),
                "faltan": len(faltan), "sobran": len(sb - sa),
                "ejemplos": sorted(faltan)[:MAX_EJEMPLOS_ENLACES],
            })
    return salida


def resumen(captura: dict) -> dict[str, Any]:
    """Lo que se guarda de una captura: sin la lista entera de enlaces."""
    salida = {k: v for k, v in captura.items() if k != "enlaces"}
    if isinstance(captura.get("enlaces"), (list, set, tuple)):
        salida["enlaces"] = len(set(captura["enlaces"]))
    return salida


__all__ = [
    "UMBRAL_PALABRAS", "PALABRAS_MINIMAS", "UMBRAL_ENLACES", "ENLACES_MINIMOS",
    "CAMPOS", "canonical_efectivo", "vary_por_user_agent", "diferencias", "resumen",
]
