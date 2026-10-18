"""Respuesta frente a render (decision 140, Ola 7.1): lo que cambia entre el
HTML que responde el servidor y lo que deja el JavaScript.

Lee `capturas_alternativas` con `perfil` `crudo` (censo con render, la otra
version sin el) o `render` (al reves), que el spider llena con una muestra
por plantilla (`seo_crawler/sonda_render.py`). Las diferencias llegan
ORIENTADAS (`render`, `crudo`) y sin los campos volatiles. Es la PLANTILLA la
que monta algo con JavaScript, asi que va agrupado: una fila por (plantilla,
tipo) con `paginas_afectadas` sobre `paginas_muestreadas` y ejemplos, anclada
en la URL menor (decisiones 36, 64 y 88).

Severidad por consecuencia (64):

- ``noindex_solo_en_crudo`` (error): Google lee el HTML inicial, ve el
  `noindex` y NO renderiza; la pagina sale del indice aunque el JavaScript
  quite la directiva. Es lo que el censo con render no ve.
- ``noindex_solo_al_renderizar`` (warning): Google renderiza y aplica el
  `noindex`, pero hasta entonces la trata como indexable; y los rastreadores
  que no ejecutan JavaScript la indexan.
- ``canonical_cambia_al_renderizar`` y ``nofollow_cambia_al_renderizar``
  (warning): dos senales contradictorias; Google puede quedarse con
  cualquiera.
- ``contenido_solo_con_js`` (warning): el texto principal o los enlaces
  internos solo aparecen al renderizar (umbrales de `shared/paridad.py`).
  Google los ve con retraso; los rastreadores de IA, nunca.
- ``cambia_al_renderizar`` (info): title, H1 o description distintos, o el
  render con MENOS texto que la respuesta.
- ``respuesta_cambia_al_renderizar`` (warning): la otra version responde otro
  codigo o redirige, comprobado dos veces.

Una captura `no_evaluada` (muro, error, robots, control fallido) no avisa, y
si la pagina del censo ya no es una HTML 2xx valida, tampoco.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import inspect, select

PERFILES = ("crudo", "render")


def _tipo(d: dict[str, Any]) -> tuple[str, str] | None:
    campo = d.get("campo")
    if campo == "noindex":
        return ("noindex_solo_en_crudo", "error") if d.get("crudo") else \
            ("noindex_solo_al_renderizar", "warning")
    if campo == "canonical":
        return "canonical_cambia_al_renderizar", "warning"
    if campo == "nofollow":
        return "nofollow_cambia_al_renderizar", "warning"
    if campo == "enlaces":
        return "contenido_solo_con_js", "warning"
    if campo == "palabras":
        if (d.get("render") or 0) > (d.get("crudo") or 0):
            return "contenido_solo_con_js", "warning"
        return "cambia_al_renderizar", "info"
    if campo in ("title", "h1", "description"):
        return "cambia_al_renderizar", "info"
    return None


def avisar(an) -> None:
    from analysis.analyzer import _clave_de_grupo
    from shared.models import CapturaAlternativa as C
    from shared.models import Url
    from shared.respuestas import NO_VALIDAS

    an._flush_issues()
    try:
        if not inspect(an.session.connection()).has_table(C.__tablename__):
            return
    except Exception:  # noqa: BLE001
        return
    filas = an.session.execute(
        select(C.url_id, Url.url, C.perfil, C.estado, C.plantilla, C.status_code, C.redirige_a,
               C.diferencias, C.detalle, Url.status_code, Url.response_class)
        .join(Url, Url.id == C.url_id)
        .where(C.job_id == an.job_id, Url.job_id == an.job_id, C.perfil.in_(PERFILES))
    ).all()
    muestreadas: dict[str, int] = defaultdict(int)
    grupos: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for (url_id, url, perfil, estado, plantilla, status, redirige_a, difs, detalle,
         status_base, clase_base) in filas:
        if not (status_base and 200 <= status_base < 300) or clase_base in NO_VALIDAS:
            continue
        if estado not in ("comparada", "respuesta_distinta"):
            continue
        plantilla = plantilla or "?"
        muestreadas[plantilla] += 1
        base = {"url_id": url_id, "url": url, "version": perfil}
        if estado == "respuesta_distinta":
            grupos[(plantilla, "respuesta_cambia_al_renderizar", "warning")].append(
                {**base, "campo": "status", "censo": status_base, "otra_version": status,
                 "redirige_a": redirige_a})
            continue
        for d in difs or []:
            t = _tipo(d)
            if t is not None:
                grupos[(plantilla, *t)].append({**base, **d})

    for (plantilla, tipo_render, severidad), miembros in sorted(grupos.items()):
        miembros.sort(key=lambda m: m["url"])
        paginas = {m["url_id"] for m in miembros}
        campos: dict[str, int] = defaultdict(int)
        for m in miembros:
            campos[m["campo"]] += 1
        ejemplos = [{k: v for k, v in m.items() if k != "url_id"}
                    for m in miembros[: an._MUESTRA_EJEMPLOS]]
        an._add_issue(miembros[0]["url_id"], tipo_render, severidad, {
            "plantilla": plantilla, "campos": dict(campos),
            "paginas_afectadas": len(paginas),
            "paginas_muestreadas": muestreadas[plantilla],
            "version": miembros[0]["version"],
            "ejemplos": ejemplos,
        }, group_key=_clave_de_grupo(f"{plantilla}|{tipo_render}"))
    an._flush_issues()
