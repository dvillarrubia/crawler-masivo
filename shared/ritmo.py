"""Que limita la velocidad de un rastreo (#275, decision 227).

Por host, Scrapy no pasa de dos techos (ley de Little):

- **la pausa**: con `DOWNLOAD_DELAY`, una peticion por intervalo y por host,
  haya la concurrencia que haya: `1 / pausa`;
- **el servidor**: `concurrencia / latencia`, con la concurrencia por dominio
  o, con AutoThrottle, su objetivo si es menor (AutoThrottle ajusta el retraso
  a `latencia / objetivo`).

El techo es el menor. Si el rastreo va bastante por debajo de los dos, lo que
lo frena es el propio proceso (el parseo, la escritura) o el reparto entre
hosts, no el sitio. Funcion pura: el spider la llama al cerrar con lo que
midio y lo guarda en `jobs.ritmo`.
"""

from __future__ import annotations

from typing import Any

# Por debajo de esta fraccion del techo, no lo marca el sitio sino el proceso.
ALCANZADO = 0.8
POR_DEBAJO = 0.5


def resumen(*, respuestas: int, segundos: float, latencia_s: float | None, pausa_s: float,
            concurrencia_dominio: int | None, autothrottle: bool,
            objetivo_autothrottle: float | None) -> dict[str, Any] | None:
    """El ritmo de un rastreo y su techo. None si no hay con que medirlo
    (menos de 20 respuestas o menos de un minuto: el arranque lo domina)."""
    if respuestas < 20 or segundos < 60:
        return None
    real = respuestas / segundos
    techo_pausa = 1 / pausa_s if pausa_s and pausa_s > 0 else None
    concurrencia = concurrencia_dominio
    if autothrottle and objetivo_autothrottle:
        concurrencia = (min(concurrencia, objetivo_autothrottle) if concurrencia
                        else objetivo_autothrottle)
    techo_servidor = (concurrencia / latencia_s
                      if concurrencia and latencia_s and latencia_s > 0 else None)
    techos = [t for t in (techo_pausa, techo_servidor) if t]
    techo = min(techos) if techos else None
    if techo is None:
        limita = None
    elif real < POR_DEBAJO * techo:
        limita = "proceso"
    elif techo_pausa is not None and techo == techo_pausa:
        limita = "pausa"
    else:
        limita = "servidor"
    return {
        "urls_s": round(real, 3),
        "respuestas": respuestas,
        "segundos": round(segundos),
        "latencia_s": round(latencia_s, 3) if latencia_s else None,
        "pausa_s": pausa_s or 0,
        "concurrencia": concurrencia,
        "techo_pausa": round(techo_pausa, 3) if techo_pausa else None,
        "techo_servidor": round(techo_servidor, 3) if techo_servidor else None,
        "techo": round(techo, 3) if techo else None,
        "limita": limita,
        "alcanzado": bool(techo and real >= ALCANZADO * techo),
    }
