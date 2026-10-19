"""Search Console cruzado con el rastreo (decision 142, Ola 4.2).

El rastreo dice como esta cada pagina; Search Console, que consigue en Google.
Este paso lee `gsc_paginas` del cliente del job (lo descarga el worker cada
noche, decision 141) en una VENTANA de 90 dias que acaba el dia del rastreo,
y:

- materializa `urls.gsc_clics`, `gsc_impresiones` y `gsc_posicion` (posicion
  media ponderada por impresiones) en las URLs del censo, emparejadas con
  `clave_url` (decision 39), la misma regla con la que se guardaron;
- deja en `jobs.gsc_resumen` la ventana, los totales, cuanto de ese trafico
  cae en URLs del censo y las URLs con clics que el rastreo NO encontro
  (huerfanas de verdad o fuera del alcance: no se afirma cual);
- emite cinco incidencias, solo sobre lo que se puede afirmar:

  ``trafico_en_pagina_perdida`` (error): recibe clics y hoy es un 4xx, un 5xx,
  un noindex o esta bloqueada por robots.txt. Es trafico real que se pierde:
  lo primero del informe.
  ``trafico_en_redireccion`` (info): recibe clics y redirige o esta
  canonicalizada a otra. Normal justo despues de una migracion; si dura, los
  enlaces y el sitemap siguen apuntando a la vieja.
  ``huerfana_con_trafico`` (warning): una pagina que el analisis ya marco como
  huerfana (`orphan_page` o `sitemap_orphan`, con sus exclusiones) y que recibe
  clics. Sustituye a aquel aviso: es el mismo hallazgo, dicho mas fuerte.
  ``indexable_sin_impresiones`` (info): indexable y 200, y CERO impresiones en
  la ventana entera. Solo si los datos cubren toda la ventana y Google no
  corto ningun dia en el tope de filas: con un dia cortado, «sin impresiones»
  puede ser «no cupo» (decision 67).
  ``trafico_en_variante_con_parametros`` (warning): una pagina del censo cuyo
  trafico se lo llevan URLs con parametros (`?partner=`, `?utm_`...) que Google
  muestra en los resultados: la esta indexando duplicada. Medido en Lopesan:
  sus fichas con mas clics salen con `?partner=`.

Sin datos de Search Console del cliente, no hace nada (ni columnas ni avisos):
«0 clics» y «no se sabe» no son lo mismo.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, inspect, select

logger = logging.getLogger(__name__)

VENTANA_DIAS = 90
MAX_FUERA = 25
# Una variante con parametros cuenta como problema si se lleva al menos este
# trafico de la pagina (sumando las dos): por debajo es ruido de campanas.
VARIANTE_MIN_CLICS = 10
VARIANTE_MIN_PARTE = 0.2


def _sin_query(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{(p.hostname or '').lower()}{p.path or '/'}"


def _dia_del_censo(job) -> date:
    t = job.started_at or job.created_at or datetime.now(timezone.utc)
    return t.date()


def avisar(an) -> None:
    from shared.gsc import clave_de_url
    from shared.models import GscEstado, GscPagina, Job, Url

    an._flush_issues()
    try:
        con = an.session.connection()
        if not inspect(con).has_table(GscPagina.__tablename__):
            return
    except Exception:  # noqa: BLE001
        return
    job = an.session.query(Job).filter(Job.id == an.job_id).one_or_none()
    if job is None or not job.cliente_id:
        return
    hasta = _dia_del_censo(job)
    desde = hasta - timedelta(days=VENTANA_DIAS - 1)
    datos = an.session.execute(
        select(GscPagina.url_clave, GscPagina.url, func.sum(GscPagina.clics),
               func.sum(GscPagina.impresiones),
               func.sum(GscPagina.posicion * GscPagina.impresiones))
        .where(GscPagina.cliente_id == job.cliente_id, GscPagina.fecha >= desde,
               GscPagina.fecha <= hasta)
        .group_by(GscPagina.url_clave, GscPagina.url)
    ).all()
    if not datos:
        return
    estados = an.session.query(GscEstado).filter(GscEstado.cliente_id == job.cliente_id).all()
    dias_con_dato = an.session.execute(
        select(func.min(GscPagina.fecha), func.max(GscPagina.fecha))
        .where(GscPagina.cliente_id == job.cliente_id, GscPagina.fecha >= desde,
               GscPagina.fecha <= hasta)).one()
    truncados = sorted(d for e in estados for d in (e.dias_truncados or [])
                       if desde.isoformat() <= d <= hasta.isoformat())
    cubre = (dias_con_dato[0] is not None and dias_con_dato[0] <= desde + timedelta(days=3)
             and not truncados)

    por_clave: dict[str, dict[str, Any]] = {}
    for clave, url, clics, imp, pos_x in datos:
        d = por_clave.setdefault(clave, {"url": url, "clics": 0, "impresiones": 0, "pos_x": 0.0})
        d["clics"] += int(clics or 0)
        d["impresiones"] += int(imp or 0)
        d["pos_x"] += float(pos_x or 0)

    urls = an.session.execute(
        select(Url.id, Url.url, Url.is_internal, Url.is_html, Url.status_code, Url.indexable,
               Url.indexability_status, Url.inlinks_count, Url.noindex, Url.blocked_by_robots,
               Url.crawl_depth)
        .where(Url.job_id == an.job_id)).all()
    del_censo: dict[str, Any] = {}
    por_ruta: dict[str, list] = defaultdict(list)
    for u in urls:
        clave = clave_de_url(u.url)
        del_censo[clave] = u
        if u.is_internal and u.is_html:
            por_ruta[_sin_query(u.url)].append(u)

    actualizaciones = []
    clics_en_censo = 0
    for clave, u in del_censo.items():
        d = por_clave.get(clave)
        if d is None:
            continue
        clics_en_censo += d["clics"]
        actualizaciones.append({
            "id": u.id, "gsc_clics": d["clics"], "gsc_impresiones": d["impresiones"],
            "gsc_posicion": round(d["pos_x"] / d["impresiones"], 1) if d["impresiones"] else None})
    if actualizaciones:
        an.session.bulk_update_mappings(Url, actualizaciones)
    # Las del censo sin fila en Search Console: 0, no NULL, si la ventana se
    # cubre entera (y NULL si no: no se sabe).
    if cubre:
        con_dato = {a["id"] for a in actualizaciones}
        ceros = [{"id": u.id, "gsc_clics": 0, "gsc_impresiones": 0, "gsc_posicion": None}
                 for u in del_censo.values() if u.id not in con_dato and u.is_internal]
        if ceros:
            an.session.bulk_update_mappings(Url, ceros)

    # -- Incidencias de pagina --------------------------------------------------
    from shared.models import Issue as _Issue

    an._flush_issues()
    huerfanas_del_analisis = {uid for (uid,) in an.session.execute(
        select(_Issue.url_id).where(_Issue.job_id == an.job_id,
                                    _Issue.issue_type.in_(("orphan_page", "sitemap_orphan"))))}
    huerfanas_con_trafico: list[int] = []
    for clave, u in del_censo.items():
        if not u.is_internal:
            continue
        d = por_clave.get(clave) or {"clics": 0, "impresiones": 0}
        clics = d["clics"]
        estado = u.indexability_status or ""
        if clics > 0:
            perdida = ((u.status_code or 0) >= 400 or bool(u.noindex) or bool(u.blocked_by_robots)
                       or estado.startswith("Blocked"))
            if perdida:
                an._add_issue(u.id, "trafico_en_pagina_perdida", "error",
                              {"clics": clics, "impresiones": d["impresiones"],
                               "estado": estado or u.status_code, "ventana_dias": VENTANA_DIAS})
                continue
            if (300 <= (u.status_code or 0) < 400) or estado == "Canonicalised" or estado.startswith("Redirect"):
                an._add_issue(u.id, "trafico_en_redireccion", "info",
                              {"clics": clics, "estado": estado or u.status_code,
                               "ventana_dias": VENTANA_DIAS})
                continue
            # Huerfana es lo que el analisis ya marco como tal (`orphan_page` o
            # `sitemap_orphan`, con sus exclusiones: semillas, destinos de
            # redireccion, paginacion). Con sus propias reglas salian 739 en
            # Lopesan: 828 de las 953 indexables sin entrantes eran semillas de
            # un censo sembrado con 3.040 URLs, y el resto destinos de 301.
            if u.id in huerfanas_del_analisis:
                an._add_issue(u.id, "huerfana_con_trafico", "warning",
                              {"clics": clics, "impresiones": d["impresiones"],
                               "ventana_dias": VENTANA_DIAS})
                huerfanas_con_trafico.append(u.id)
        elif (cubre and u.is_html and u.indexable and u.status_code == 200
              and d["impresiones"] == 0):
            an._add_issue(u.id, "indexable_sin_impresiones", "info", {"ventana_dias": VENTANA_DIAS})

    # -- Variantes con parametros y lo que el rastreo no encontro --------------
    fuera = []
    variantes: dict[int, dict[str, Any]] = {}
    for clave, d in por_clave.items():
        if clave in del_censo:
            continue
        candidatas = por_ruta.get(_sin_query(d["url"])) if urlsplit(d["url"]).query else None
        if candidatas:
            # La misma ruta sin parametros esta en el censo: es una variante.
            base = next((c for c in candidatas if not urlsplit(c.url).query), None)
            if base is not None:
                v = variantes.setdefault(base.id, {"clics": 0, "urls": []})
                v["clics"] += d["clics"]
                v["urls"].append((d["clics"], d["url"]))
                continue
        if d["clics"] > 0:
            fuera.append((d["clics"], d["impresiones"], d["url"]))
    por_id = {u.id: u for u in del_censo.values()}
    for uid, v in variantes.items():
        propios = (por_clave.get(clave_de_url(por_id[uid].url)) or {}).get("clics", 0)
        total = propios + v["clics"]
        if v["clics"] >= VARIANTE_MIN_CLICS and total and v["clics"] / total >= VARIANTE_MIN_PARTE:
            v["urls"].sort(reverse=True)
            an._add_issue(uid, "trafico_en_variante_con_parametros", "warning", {
                "clics_variantes": v["clics"], "clics_pagina": propios,
                "variantes": len(v["urls"]),
                "ejemplos": [{"url": url, "clics": c} for c, url in v["urls"][:5]],
                "ventana_dias": VENTANA_DIAS})
    fuera.sort(reverse=True)

    # Un aviso por hallazgo (decision 36): la huerfana con trafico es la misma
    # pagina que `orphan_page` / `sitemap_orphan`, dicho mas fuerte.
    if huerfanas_con_trafico:
        from shared.models import Issue

        an._flush_issues()
        for i in range(0, len(huerfanas_con_trafico), 1000):
            (an.session.query(Issue)
             .filter(Issue.job_id == an.job_id, Issue.url_id.in_(huerfanas_con_trafico[i:i + 1000]),
                     Issue.issue_type.in_(("orphan_page", "sitemap_orphan")))
             .delete(synchronize_session=False))

    inspeccion = _avisar_inspeccion(an, job, del_censo, hasta)

    total_clics = sum(d["clics"] for d in por_clave.values())
    job.gsc_resumen = {
        "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "datos_desde": dias_con_dato[0].isoformat() if dias_con_dato[0] else None,
        "datos_hasta": dias_con_dato[1].isoformat() if dias_con_dato[1] else None,
        "cubre_la_ventana": cubre, "dias_truncados": truncados,
        "clics": total_clics, "impresiones": sum(d["impresiones"] for d in por_clave.values()),
        "urls_con_datos": len(por_clave), "clics_en_urls_del_censo": clics_en_censo,
        "clics_en_variantes": sum(v["clics"] for v in variantes.values()),
        "urls_fuera_del_censo": len(fuera), "clics_fuera_del_censo": sum(c for c, _, _ in fuera),
        "fuera_del_censo": [{"url": url, "clics": c, "impresiones": i} for c, i, url in fuera[:MAX_FUERA]],
        "inspeccion": inspeccion,
    }
    an._flush_issues()
    logger.info("Search Console del job %s: %d clics en la ventana, %d en URLs del censo, %d URLs fuera",
                an.job_id, total_clics, clics_en_censo, len(fuera))


INSPECCION_ANTES_DIAS = 30
INSPECCION_DESPUES_DIAS = 14


def _avisar_inspeccion(an, job, del_censo: dict, dia) -> dict | None:
    """Lo que dice la inspeccion de URLs de Google de las paginas del censo
    (decision 144), con las inspecciones de 30 dias antes a 14 despues del
    rastreo: es una muestra diaria y rotatoria.

    - ``indexable_no_indexada_por_google`` (warning): el rastreo la da por
      indexable (200, sin noindex, autocanonica) y Google dice que no la tiene
      en el indice; en los detalles, el motivo de Google («Rastreada:
      actualmente sin indexar», «Descubierta: actualmente sin indexar»...).
    - ``google_elige_otro_canonical`` (warning): lo que Google vio declarado es
      lo mismo que la pagina declara hoy, y aun asi ha elegido otra URL. Es la
      prueba de que Google ignora la senal.
    - ``google_tiene_canonical_antiguo`` (info): en su ultima visita Google vio
      un canonical distinto del que la pagina declara hoy. Medido en Lopesan:
      fichas en aleman que Google vio canonicalizadas a la version en
      castellano o a `webserver-lopesan-prd.lfr.cloud` (el fallo de
      octubre, ya arreglado) y que hoy son autocanonicas. Decir «Google
      ignora tu canonical» ahi seria falso: se arregla con la siguiente
      visita, o pidiendo la reindexacion.
    """
    from shared.indexabilidad import misma_url
    from shared.models import GscInspeccion

    desde = datetime.combine(dia - timedelta(days=INSPECCION_ANTES_DIAS), datetime.min.time(), timezone.utc)
    hasta = datetime.combine(dia + timedelta(days=INSPECCION_DESPUES_DIAS), datetime.max.time(), timezone.utc)
    try:
        filas = (an.session.query(GscInspeccion)
                 .filter(GscInspeccion.cliente_id == job.cliente_id, GscInspeccion.fecha >= desde,
                         GscInspeccion.fecha <= hasta, GscInspeccion.error.is_(None)).all())
    except Exception:  # noqa: BLE001 — sin la tabla
        an.session.rollback()
        return None
    if not filas:
        return None
    from shared.models import HtmlMeta

    cuenta = {"inspeccionadas": 0, "indexadas": 0, "no_indexadas": 0, "canonical_distinto": 0,
              "canonical_antiguo": 0}
    ids = [del_censo[i.url_clave].id for i in filas if i.url_clave in del_censo]
    canonical_hoy: dict[int, str] = {}
    for k in range(0, len(ids), 1000):
        for uid, href, cab in an.session.execute(
                select(HtmlMeta.url_id, HtmlMeta.canonical_href, HtmlMeta.canonical_header)
                .where(HtmlMeta.url_id.in_(ids[k:k + 1000]))):
            if href or cab:
                canonical_hoy[uid] = href or cab
    for i in filas:
        u = del_censo.get(i.url_clave)
        if u is None or not u.is_internal or u.status_code != 200:
            continue
        cuenta["inspeccionadas"] += 1
        if i.verdict == "PASS":
            cuenta["indexadas"] += 1
        detalles = {"cobertura": i.cobertura, "verdict": i.verdict,
                    "inspeccionada": i.fecha.isoformat() if i.fecha else None,
                    "ultimo_rastreo_google": i.ultimo_rastreo.isoformat() if i.ultimo_rastreo else None}
        if u.indexable and i.verdict != "PASS":
            cuenta["no_indexadas"] += 1
            an._add_issue(u.id, "indexable_no_indexada_por_google", "warning", detalles)
        hoy = canonical_hoy.get(u.id) or u.url
        if i.canonical_usuario and not misma_url(hoy, i.canonical_usuario):
            cuenta["canonical_antiguo"] += 1
            an._add_issue(u.id, "google_tiene_canonical_antiguo", "info",
                          dict(detalles, canonical_hoy=hoy, canonical_visto_por_google=i.canonical_usuario,
                               canonical_de_google=i.canonical_google))
        elif (i.canonical_usuario and i.canonical_google
                and not misma_url(i.canonical_usuario, i.canonical_google)):
            cuenta["canonical_distinto"] += 1
            an._add_issue(u.id, "google_elige_otro_canonical", "warning",
                          dict(detalles, canonical_declarado=i.canonical_usuario,
                               canonical_de_google=i.canonical_google))
    an._flush_issues()
    return cuenta
