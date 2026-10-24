"""Accesibilidad con axe-core por muestra de plantilla (decision 174, Ola 8).

Lee `capturas_alternativas` con `perfil='axe'`, que llena la sonda del spider
(`seo_crawler/sonda_axe.py`) con una muestra por plantilla. Una violacion de
axe sale de la PLANTILLA casi siempre (el menu, el pie, el formulario de
busqueda), asi que va agrupada: **una fila por (regla, plantilla)**, con las
paginas afectadas sobre las muestreadas de esa plantilla, los nodos y
ejemplos, anclada en la URL menor (decisiones 36, 64 y 88).

- ``accesibilidad_grave`` (warning): impacto `critical` o `serious` de axe.
- ``accesibilidad_menor`` (info): `moderate` o `minor`.

Nunca `error`: la accesibilidad no es un factor de posicionamiento y un error
topa la nota SEO del censo (decision 92). El impacto de axe va en los detalles.

No se afirma (decisiones 67 y 89): lo que axe deja en `incomplete` (sin
aviso; la API lo cuenta aparte como «a revisar a mano»), una captura
`no_evaluada` (muro, redireccion, error, axe que no termino) ni una pagina del
censo que ya no es una HTML 2xx valida.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect

from shared.accesibilidad import NOMBRE_IMPACTO, TIPO_POR_IMPACTO, agregar, filas_evaluadas

logger = logging.getLogger(__name__)


def avisar(an) -> None:
    """Best-effort entero: un fallo aqui no tumba el analisis del censo."""
    try:
        _avisar(an)
    except Exception:  # noqa: BLE001
        logger.exception("Accesibilidad: no se pudieron generar los avisos de %s", an.job_id)


def _avisar(an) -> None:
    from analysis.analyzer import _clave_de_grupo
    from shared.models import CapturaAlternativa as C

    an._flush_issues()
    try:
        if not inspect(an.session.connection()).has_table(C.__tablename__):
            return
    except Exception:  # noqa: BLE001
        return
    with an.session.begin_nested():
        filas = filas_evaluadas(an.session, an.job_id)
    if not filas:
        return
    datos = agregar(filas)
    pendientes = []
    for (regla, plantilla), g in sorted(datos["violaciones"].items()):
        tipo, severidad = TIPO_POR_IMPACTO.get(g["impacto"], ("accesibilidad_menor", "info"))
        paginas = g["paginas"]
        pendientes.append((paginas[0]["url_id"], tipo, severidad, {
            "regla": regla, "plantilla": plantilla,
            "impacto": NOMBRE_IMPACTO.get(g["impacto"]), "impacto_axe": g["impacto"],
            "wcag": ", ".join(g["criterios"]) or None, "nivel": g["nivel"],
            "ayuda": g["ayuda"], "url_ayuda": g["url_ayuda"],
            "paginas_afectadas": len(paginas),
            "paginas_muestreadas": datos["muestreadas"].get(plantilla, 0),
            "nodos": g["nodos"],
            "ejemplos": [{"url": p["url"], "nodos": p["nodos"], "elementos": p["ejemplos"]}
                         for p in paginas[: an._MUESTRA_EJEMPLOS]],
        }, _clave_de_grupo(f"{plantilla}|{regla}")))
    for url_id, tipo_axe, severidad, detalles, clave in pendientes:
        an._add_issue(url_id, tipo_axe, severidad, detalles, group_key=clave)
    an._flush_issues()
