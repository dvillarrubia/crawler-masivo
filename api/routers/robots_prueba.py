"""robots.txt de prueba (Ola 7.3, decision 171).

`POST /api/robots/probar`: dado un robots.txt pegado, una lista de URLs (o
rutas) y unos agentes, el veredicto de cada par y la linea que decide. Sirve
para validar un robots.txt ANTES de subirlo.

- **No descarga nada**: ni el robots.txt ni las URLs. Todo sale del texto.
- **La misma logica que el crawler**: `shared.robots_regla`, la que da la linea
  en la ficha de una URL bloqueada y que `tests/test_robots_regla.py` contrasta
  con Protego en miles de casos al azar (0 discrepancias en 2,1 M veredictos
  reales, decision 129). Dos modos:
  - `google` (por defecto): el agente es un TOKEN (`Googlebot`, `GPTBot`) y el
    grupo se elige como Google, por token entero (decision 113); es como el
    analisis evalua Googlebot y los bots de IA.
  - `rastreador`: el agente es la cadena de User-Agent entera y el grupo se
    elige como Protego, buscando el nombre del grupo dentro de ella; es como
    decidio nuestro rastreo (Scrapy, decision 98).
- `diagnostico`: lineas que el parser se salta (directiva desconocida, regla
  antes de cualquier `User-agent`), los `Sitemap` y si pasa de 500 KiB.

Puro calculo, sin BD; el tope de tamano y el de coste (`coste`: trozos entre
comodines y longitud de las URLs, no solo reglas x pares) acotan lo que cuesta.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/api/robots", tags=["robots"])

# Un robots.txt de mas de 500 KiB se lee recortado (lo de detras no existe para
# Google); se admite algo mas para poder decir que se recorta.
MAX_ROBOTS_BYTES = 1024 * 1024
MAX_URLS = 500
MAX_AGENTES = 20
# Tope de lo que cuesta emparejar, en unidades de ~70 ns (decision 171). Un
# emparejamiento no cuesta lo mismo siempre: `_Patron.casa` hace un `find` por
# cada trozo entre comodines (diagnostico["trozos"]), y en una regla con `*`
# cada `find` puede recorrer la URL entera (una sin `*` es un `startswith`).
# Medido: ~70 ns por trozo y URL, hasta ~1,1 us por regla con comodin y KiB de
# URL (agujas largas o casi coincidentes: ~16 unidades por KiB) y ~8 us por par
# (agente, URL) de normalizar y montar la fila. Con 15 M unidades el peor caso
# medido ronda 1 s. Contar solo reglas x pares dejaba pasar 120 reglas de 2.000
# comodines contra URLs de 4 KiB (minutos de CPU).
MAX_COMPROBACIONES = 15_000_000
_UNIDADES_POR_BYTE_DE_URL = 16 / 1024
_UNIDADES_POR_PAR = 100


def coste(diag: dict, urls: list[str], agentes: int) -> int:
    """Cota de las unidades de emparejamiento de la prueba, por agente: cada
    trozo de regla contra cada URL, cada regla con comodin recorriendo cada URL
    y lo fijo de cada par."""
    trozos = max(diag.get("trozos") or 0, diag.get("reglas") or 0, 1)
    con_comodin = diag.get("reglas_con_comodin") or 0
    largo = sum(len(u) for u in urls)
    return int(agentes * (trozos * len(urls) + con_comodin * largo * _UNIDADES_POR_BYTE_DE_URL
                          + _UNIDADES_POR_PAR * len(urls)))


_TOKEN = re.compile(r"[A-Za-z_-]+")


class PruebaRobots(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    robots_txt: str = Field(..., max_length=MAX_ROBOTS_BYTES)
    urls: list[str] = Field(..., min_length=1, max_length=MAX_URLS)
    agentes: list[str] = Field(default_factory=lambda: ["Googlebot"], min_length=1,
                               max_length=MAX_AGENTES)
    modo: Literal["google", "rastreador"] = "google"


def _agente(valor: str, modo: str) -> str:
    from shared.robots_txt import BOTS

    v = (valor or "").strip()
    if not v or len(v) > 500:
        raise HTTPException(422, "Cada agente es un token (Googlebot) o, en modo "
                                 "rastreador, un User-Agent de hasta 500 caracteres")
    if modo == "google":
        # La clave del catalogo (`oai-searchbot`) vale por su token.
        v = BOTS.get(v.lower(), v)
        if not _TOKEN.fullmatch(v):
            raise HTTPException(
                422, f"«{v[:80]}» no es un token de producto: en modo google va el "
                     "nombre del grupo (Googlebot, GPTBot...). Para probar un "
                     "User-Agent entero, modo rastreador.")
    return v


@router.post("/probar")
def probar_robots(cuerpo: PruebaRobots):
    """Veredicto y linea que decide para cada (agente, URL), sin descargar nada.

    Cada fila de `resultados`: `agente`, `url`, `permitida`, `linea` (None si
    ninguna regla casa: permitida porque nada la prohibe), `texto` (la linea tal
    cual), `directiva`, `valor`, `grupo` y `linea_grupo` (el `User-agent` que
    aplico; None si ninguno, y entonces todo esta permitido). `/robots.txt`
    siempre se puede pedir.
    """
    from shared.robots_regla import diagnostico, evaluar_lote

    if len(cuerpo.robots_txt.encode("utf-8")) > MAX_ROBOTS_BYTES:
        raise HTTPException(413, "El robots.txt pasa de 1 MiB")
    urls = []
    for u in cuerpo.urls:
        u = (u or "").strip()
        if not u:
            continue
        if len(u) > 4096:
            raise HTTPException(422, "Una URL pasa de 4.096 caracteres")
        urls.append(u)
    if not urls:
        raise HTTPException(422, "Falta al menos una URL o ruta")
    agentes = list(dict.fromkeys(_agente(a, cuerpo.modo) for a in cuerpo.agentes))
    diag = diagnostico(cuerpo.robots_txt)
    if coste(diag, urls, len(agentes)) > MAX_COMPROBACIONES:
        raise HTTPException(
            422, f"Demasiadas comprobaciones ({diag['reglas']} reglas con {diag['trozos']} "
                 f"trozos entre comodines x {len(urls)} URLs x {len(agentes)} agentes): "
                 "reduce las URLs, su longitud o los agentes")
    resultados = evaluar_lote(cuerpo.robots_txt, urls, agentes,
                              como_google=cuerpo.modo == "google")
    return {
        "modo": cuerpo.modo,
        "agentes": agentes,
        "diagnostico": diag,
        "resultados": resultados,
    }
