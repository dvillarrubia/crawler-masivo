"""Cuanto se usa cada parte del Explorador (#131).

La investigacion del Explorador (docs/INVESTIGACION_EXPLORADOR.md) no pudo
medir el uso real: los logs de la API se borran en cada despliegue. Antes de
invertir en lo grande (vistas por ambito, agrupar por plantilla) hay que saber
que pestanas, filtros, ordenes y columnas se piden de verdad.

Un contador por dia en Redis (`uso:explorador:AAAA-MM-DD`, un hash) con
caducidad de 90 dias. Cuenta NOMBRES de parametros, nunca valores: ni URLs ni
textos buscados, que pueden ser de un cliente. Y no puede romper nada: si Redis
falla, la consulta sigue y el contador se pierde.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)

PREFIJO = "uso:explorador:"
CADUCIDAD_S = 90 * 24 * 3600

# Parametros que no son una eleccion de quien mira (paginacion, el propio
# marcador de vista, el modo ligero).
_IGNORAR = {"page", "page_size", "vista", "ligero", "columnas", "sort_dir", "pestana",
            # Los pone la pestaña lateral, que ya se cuenta como `pestana:`.
            "status_group", "resource_type"}


def _claves(qp, *, csv: bool) -> list[str]:
    claves = ["csv" if csv else "consultas"]
    if qp.get("pestana"):
        claves.append("pestana:" + qp.get("pestana")[:40])
    for nombre, valor in qp.items():
        if nombre in _IGNORAR or valor in (None, ""):
            continue
        if nombre == "sort_by":
            claves.append("orden:" + valor[:60])
        elif nombre == "issue_type":
            claves += ["problema:" + t.strip()[:60] for t in valor.split(",") if t.strip()]
        elif nombre == "severity":
            claves.append("gravedad:" + valor[:20])
        elif nombre in ("vacio", "con_valor"):
            claves += [f"filtro:{c.strip()[:40]}:{nombre}" for c in valor.split(",") if c.strip()]
        elif nombre == "search":
            claves.append("busqueda")
        else:
            # <col>_gte / <col>_lte / <campo>_contains / booleanos: el nombre
            # del filtro, sin el valor.
            base = nombre
            for suf in ("_gte", "_lte"):
                if base.endswith(suf):
                    base = base[: -len(suf)]
            claves.append("filtro:" + base[:60])
    if qp.get("page") not in (None, "", "1"):
        claves.append("paginado")
    if csv:
        claves += ["columna:" + c.strip()[:60] for c in (qp.get("columnas") or "").split(",") if c.strip()]
    return claves


def anotar(redis, qp, *, csv: bool = False) -> None:
    """Suma una consulta del Explorador. Best-effort: nunca lanza."""
    try:
        if redis is None or qp.get("vista") != "explorador":
            return
        clave = PREFIJO + date.today().isoformat()
        pipe = redis.pipeline()
        for k in set(_claves(qp, csv=csv)):
            pipe.hincrby(clave, k, 1)
        pipe.expire(clave, CADUCIDAD_S)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 — un contador no tumba una consulta
        log.debug("No se pudo anotar el uso del Explorador: %s", exc)


def leer(redis, dias: int = 30) -> dict:
    """Totales de los ultimos `dias`, agrupados por tipo y de mas a menos."""
    hoy = date.today()
    total: dict[str, int] = {}
    con_datos = 0
    for n in range(dias):
        h = redis.hgetall(PREFIJO + (hoy - timedelta(days=n)).isoformat()) or {}
        if h:
            con_datos += 1
        for k, v in h.items():
            k = k.decode() if isinstance(k, bytes) else k
            total[k] = total.get(k, 0) + int(v)
    grupos: dict[str, list] = {}
    for k, v in total.items():
        tipo, _, resto = k.partition(":")
        if not resto:
            continue
        grupos.setdefault(tipo, []).append({"nombre": resto, "veces": v})
    for lista in grupos.values():
        lista.sort(key=lambda x: -x["veces"])
    return {
        "dias": dias,
        "dias_con_datos": con_datos,
        "consultas": total.get("consultas", 0),
        "csv": total.get("csv", 0),
        "paginado": total.get("paginado", 0),
        "busqueda": total.get("busqueda", 0),
        "por_tipo": grupos,
    }
