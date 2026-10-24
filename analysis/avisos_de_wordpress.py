"""Avisos del adaptador WordPress (#33, decision 161).

Cruza lo que la REST API publica del sitio (`adapter_entities`, lo lee el
spider: `crawler/seo_crawler/sonda_wordpress.py`) con el censo. Cuatro
incidencias, todas **agrupadas**: ninguna es de una pagina suelta del censo,
sino del sitio o de su plantilla (decisiones 36, 53, 64 y 73).

- ``wp_publicado_no_rastreado`` (warning), uno por (host, tipo): contenido
  publicado (entradas, paginas, un tipo propio) que el rastreo no encontro y
  que ninguna pagina rastreada enlaza (o solo con nofollow). **Solo se afirma
  en un censo entero** (decisiones 67, 70 y 89): ni truncado (tope de URLs, de
  tiempo, estancado, cancelado) ni cortado por `max_depth` (alguna URL llego a
  la profundidad maxima: lo que cuelga de ellas no se vio), ni fuera del
  alcance del job (patrones, filtros de URL: `en_alcance`). Lo rastreado y sin
  enlaces entrantes NO se repite aqui: ya es `orphan_page` o
  `sitemap_orphan` (un aviso por hallazgo, 36). Un tipo propio solo cuenta si
  el rastreo encontro alguna entrada suya: hay tipos con REST API y sin pagina
  publica.
- ``wp_adjunto_indexable`` (warning), uno por host: paginas de adjunto
  (``?attachment_id=`` o ``/<entrada>/<imagen>/``) que responden 200 en HTML
  y son indexables, del censo o de la muestra que pide el spider. Desde
  WordPress 6.4 una instalacion nueva redirige al fichero; una que se
  actualizo las conserva, y cada imagen subida es una pagina casi vacia.
- ``wp_taxonomia_anemica`` (info), uno por (host, taxonomia): archivos de
  categoria o etiqueta rastreados e indexables con
  ``analysis_thresholds.wp_taxonomia_anemica_max`` entradas o menos (1).
  Los no rastreados no se afirman: se cuentan en el resumen.
- ``wp_paginacion_sin_fin`` (warning si alguna es indexable, si no info), uno
  por host: la pagina ``N+1`` de un archivo con ``N`` entradas responde 200.
  Con cualquier numero de entradas por pagina esa pagina no puede existir.
  Si el sitio entero responde 200 a una URL inexistente (soft 404 de serie,
  decision 105) ya lo dice el soft 404: no se repite.

Se ancla como los avisos de sitemaps (decision 126): en la raiz del host si se
rastreo y si no en la URL menor del host o del censo, o en la URL menor de las
afectadas que esten en el censo; con `group_key` del (host, tipo).

El resumen (lo que se pudo y no se pudo afirmar, y por que) va a
`jobs.adapters["wordpress"]["analisis"]`.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import case, func, select

from shared import wordpress as W

logger = logging.getLogger(__name__)

TIPOS = ("wp_publicado_no_rastreado", "wp_adjunto_indexable", "wp_taxonomia_anemica",
         "wp_paginacion_sin_fin")
MUESTRA = 10
LOTE = 2000


def _clave(valor: str | None) -> str | None:
    from analysis.analyzer import _clave_de_grupo

    return _clave_de_grupo(valor)


def _host(url: str | None) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def avisar_sin_tumbar(az) -> dict[str, Any] | None:
    """`avisar` en un savepoint y con el try cubriendolo entero (decision 74):
    una respuesta rara de la REST API no puede llevarse por delante el
    analisis ni deshacer lo que ya lleva hecho."""
    pendientes = list(az._pending_issues)
    try:
        az._flush_issues()
        pendientes = []
        with az.session.begin_nested():
            return avisar(az)
    except Exception:
        az._pending_issues = pendientes
        logger.exception("WordPress del job %s: fallo; el analisis sigue", az.job_id)
        return None


def _hay_tabla(az) -> bool:
    from shared.models import AdapterEntity

    try:
        with az.session.begin_nested():
            az.session.execute(select(AdapterEntity.id).limit(1)).first()
        return True
    except Exception:  # noqa: BLE001 -- base sin migrar
        return False


def avisar(az) -> dict[str, Any] | None:
    from shared.models import AdapterEntity as E, Job

    job = az.session.get(Job, az.job_id)
    adapters = dict(getattr(job, "adapters", None) or {}) if job is not None else {}
    wp = adapters.get(W.ADAPTADOR)
    if not wp or not wp.get("detectado") or wp.get("rest") != "abierta":
        return None
    if not _hay_tabla(az):
        return None
    entidades = az.session.execute(
        select(E.tipo, E.id_externo, E.url, E.url_hash, E.url_hash_alt, E.en_alcance,
               E.estado, E.status_code, E.cuenta, E.modificado, E.datos)
        .where(E.job_id == az.job_id, E.adaptador == W.ADAPTADOR)
        .order_by(E.id)).all()
    if not entidades:
        return None
    censo = _censo(az, entidades)

    def en_censo(e):
        return censo.get(e.url_hash) or (censo.get(e.url_hash_alt) if e.url_hash_alt else None)

    clases = {t: (d or {}).get("clase") for t, d in (wp.get("tipos") or {}).items()}
    from analysis.avisos_de_sitemaps import _Anclas

    anclas = _Anclas(az)
    resumen: dict[str, Any] = {"entidades": len(entidades)}
    resumen["por_tipo"] = _por_tipo(entidades, clases, en_censo)
    resumen["wp_publicado_no_rastreado"] = _no_rastreados(az, job, entidades, clases,
                                                          en_censo, anclas)
    resumen["wp_adjunto_indexable"] = _adjuntos(az, wp, entidades, clases, en_censo, anclas)
    resumen["wp_taxonomia_anemica"] = _taxonomias(az, entidades, clases, en_censo, anclas)
    resumen["wp_paginacion_sin_fin"] = _paginacion(az, job, entidades, anclas)
    az._flush_issues()
    adapters[W.ADAPTADOR] = {**wp, "analisis": resumen}
    job.adapters = adapters
    logger.info("WordPress: %s", {k: v for k, v in resumen.items() if k != "por_tipo"})
    return resumen


def _censo(az, entidades) -> dict[str, Any]:
    """url_hash -> fila de `urls` de lo que la REST API nombra, por el indice
    unico (job_id, url_hash)."""
    from shared.models import Url

    hashes = sorted({h for e in entidades for h in (e.url_hash, e.url_hash_alt) if h})
    salida: dict[str, Any] = {}
    for i in range(0, len(hashes), LOTE):
        for fila in az.session.execute(
                select(Url.id, Url.url, Url.url_hash, Url.status_code, Url.is_html,
                       Url.indexable, Url.response_class)
                .where(Url.job_id == az.job_id, Url.url_hash.in_(hashes[i:i + LOTE]))):
            salida[fila.url_hash] = fila
    return salida


def _por_tipo(entidades, clases, en_censo) -> dict[str, dict[str, int]]:
    """Por tipo: leidas de la REST API y cuantas estan en el censo."""
    salida: dict[str, dict[str, int]] = defaultdict(lambda: {"leidas": 0, "en_el_censo": 0})
    for e in entidades:
        if e.tipo.startswith("sonda_"):
            continue
        d = salida[e.tipo]
        d["leidas"] += 1
        if en_censo(e):
            d["en_el_censo"] += 1
    return dict(salida)


def _sonda_indexable(e) -> tuple[bool, str | None]:
    """(indexable, motivo) de lo que respondio una sonda, con la misma funcion
    que las paginas del censo (decision 39)."""
    from shared.indexabilidad import estado_indexabilidad
    from shared.respuestas import NO_VALIDAS

    d = e.datos or {}
    if e.status_code != 200 or "html" not in (d.get("content_type") or "").lower():
        return False, None
    if d.get("response_class") in NO_VALIDAS:
        return False, d.get("response_class")
    ok, motivo = estado_indexabilidad(
        200, d.get("meta_robots"), d.get("x_robots_tag"), d.get("canonical"), e.url,
        canonical_header=d.get("canonical_cabecera"),
        canonicals_distintos=d.get("canonicals_distintos"))
    return ok, (None if ok else motivo)


def _html_200(fila) -> bool:
    from shared.respuestas import NO_VALIDAS

    return (fila is not None and fila.status_code == 200 and bool(fila.is_html)
            and fila.response_class not in NO_VALIDAS)


# ---------------------------------------------------------------------------
# 1. Publicado y no rastreado
# ---------------------------------------------------------------------------
def _alcance_del_censo(az, job) -> str | None:
    """Por que en este censo no se puede afirmar que algo no se encontro, o
    None si se puede: truncado, o cortado por la profundidad maxima."""
    from shared.comparacion import MOTIVOS_TRUNCADO
    from shared.config import DEFAULT_MAX_DEPTH
    from shared.models import Url

    motivo = (getattr(job, "finish_reason", None) or "")
    if motivo in MOTIVOS_TRUNCADO:
        return f"truncado ({motivo})"
    config = getattr(job, "config", None) or {}
    # Con filtros de URL el censo tiene otro alcance (decision 70): pueden
    # dejar fuera la paginacion o los archivos (/tag/, /page/N/) desde los
    # que se llega a una entrada que si pasa el filtro.
    filtros = config.get("url_filters") or {}
    if config.get("exclude_patterns") or config.get("include_patterns") \
            or filtros.get("max_url_length") or filtros.get("max_folder_depth"):
        return "alcance (filtros de URL del job)"
    tope = config.get("max_depth", DEFAULT_MAX_DEPTH)
    maxima = az.session.execute(
        select(func.max(Url.crawl_depth)).where(Url.job_id == az.job_id)).scalar()
    if maxima is not None and tope is not None and maxima >= tope:
        return f"profundidad (max_depth {tope} alcanzado)"
    return None


def _enlaces_a(az, hashes: list[str]) -> dict[str, bool]:
    """url_hash -> si alguna pagina rastreada lo enlaza con follow (True) o
    solo con nofollow (False). Filtra `links` por su job_id (decision 37)."""
    from shared.models import Link

    salida: dict[str, bool] = {}
    con_follow = func.max(case((Link.follow.is_(False), 0), else_=1))
    for i in range(0, len(hashes), LOTE):
        for h, alguno in az.session.execute(
                select(Link.to_url_hash, con_follow)
                .where(Link.job_id == az.job_id, Link.to_url_hash.in_(hashes[i:i + LOTE]))
                .group_by(Link.to_url_hash)):
            salida[h] = bool(alguno)
    return salida


def _en_sitemap(az, hashes: list[str]) -> set[str]:
    from shared.models import SitemapEntry

    salida: set[str] = set()
    try:
        with az.session.begin_nested():
            for i in range(0, len(hashes), LOTE):
                salida.update(az.session.execute(
                    select(SitemapEntry.url_hash).distinct().where(
                        SitemapEntry.job_id == az.job_id,
                        SitemapEntry.url_hash.in_(hashes[i:i + LOTE]))).scalars())
    except Exception:  # noqa: BLE001 -- sin la tabla: no se sabe
        return set()
    return salida


def _no_rastreados(az, job, entidades, clases, en_censo, anclas) -> dict[str, Any]:
    contenido = [e for e in entidades if clases.get(e.tipo) == "entrada"]
    # Un tipo propio cuenta si el rastreo encontro alguna entrada suya.
    con_pagina = {e.tipo for e in contenido if en_censo(e)}
    publicables = W.TIPOS_SIEMPRE_PUBLICOS | con_pagina
    resumen: dict[str, Any] = {"emitidos": 0}
    candidatas = [e for e in contenido
                  if (e.estado or "publish") == "publish" and e.url_hash and not en_censo(e)]
    resumen["no_rastreadas"] = len(candidatas)
    resumen["fuera_de_alcance"] = sum(1 for e in candidatas if not e.en_alcance)
    resumen["tipo_sin_pagina_publica"] = sum(
        1 for e in candidatas if e.en_alcance and e.tipo not in publicables)
    candidatas = [e for e in candidatas if e.en_alcance and e.tipo in publicables]
    if not candidatas:
        return resumen
    no_afirmable = _alcance_del_censo(az, job)
    if no_afirmable:
        # Lo que no se puede afirmar no se afirma (decision 67): la cifra va al
        # resumen y la lista a `GET /wordpress/entidades`, sin incidencia.
        resumen["no_afirmable"] = no_afirmable
        resumen["sin_afirmar"] = len(candidatas)
        return resumen
    enlaces = _enlaces_a(az, sorted({e.url_hash for e in candidatas}))
    sigue_nofollow = bool(((getattr(job, "config", None) or {}).get("crawl_behavior") or {})
                          .get("follow_nofollow"))
    grupos: dict[tuple[str, str], list] = defaultdict(list)
    enlazadas = 0
    for e in candidatas:
        estado = enlaces.get(e.url_hash)
        if estado is True or (estado is False and sigue_nofollow):
            # Enlazada y aun asi sin rastrear (un fallo de red, una cola
            # cortada): no se sabe por que, no se afirma.
            enlazadas += 1
            continue
        motivo = "sin_enlaces" if estado is None else "solo_nofollow"
        grupos[(_host(e.url), e.tipo)].append((e, motivo))
    resumen["enlazadas_sin_rastrear"] = enlazadas
    sitemap = _en_sitemap(az, sorted({e.url_hash for v in grupos.values() for e, _ in v}))
    for (host, tipo), miembros in sorted(grupos.items()):
        miembros.sort(key=lambda m: m[0].url or "")
        ancla = anclas.de_fichero(miembros[0][0].url)
        if ancla is None:
            continue
        por_motivo: dict[str, int] = defaultdict(int)
        for _, motivo in miembros:
            por_motivo[motivo] += 1
        resumen["emitidos"] += 1
        az._add_issue(ancla, "wp_publicado_no_rastreado", "warning", {
            "host": host, "tipo": tipo,
            "paginas_afectadas": len(miembros),
            "por_motivo": dict(por_motivo),
            "en_sitemap": sum(1 for e, _ in miembros if e.url_hash in sitemap),
            "ejemplos": [{"url": e.url, "id": e.id_externo, "modificado": e.modificado,
                          "motivo": m} for e, m in miembros[:MUESTRA]],
        }, group_key=_clave(f"{host}|{tipo}"))
    return resumen


# ---------------------------------------------------------------------------
# 2. Paginas de adjunto indexables
# ---------------------------------------------------------------------------
def _adjuntos(az, wp, entidades, clases, en_censo, anclas) -> dict[str, Any]:
    resumen: dict[str, Any] = {"emitidos": 0, "rastreadas": 0, "sondeadas": 0}
    por_host: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"censo": [], "sonda": [], "miradas": 0, "no_indexables": defaultdict(int)})
    vistas: set[str] = set()
    for e in entidades:
        if clases.get(e.tipo) != "medio" or not e.url_hash:
            continue
        fila = en_censo(e)
        if fila is None:
            continue
        vistas.add(e.url_hash)
        resumen["rastreadas"] += 1
        g = por_host[_host(e.url)]
        g["miradas"] += 1
        if _html_200(fila) and fila.indexable:
            g["censo"].append((fila.url, fila.id))
    for e in entidades:
        if e.tipo != "sonda_adjunto" or not e.url_hash or e.url_hash in vistas:
            continue
        vistas.add(e.url_hash)
        resumen["sondeadas"] += 1
        g = por_host[_host(e.url)]
        g["miradas"] += 1
        ok, motivo = _sonda_indexable(e)
        if ok:
            g["sonda"].append(e.url)
        else:
            clave = motivo or (f"redirige ({e.status_code})" if e.status_code and
                               300 <= e.status_code < 400 else f"status {e.status_code}")
            g["no_indexables"][clave[:60]] += 1
    total = ((wp.get("tipos") or {}).get("attachment") or {}).get("total")
    for host, g in sorted(por_host.items()):
        afectadas = len(g["censo"]) + len(g["sonda"])
        if not afectadas:
            continue
        censo = sorted(g["censo"])
        ancla = censo[0][1] if censo else anclas.de_fichero(f"https://{host}/")
        if ancla is None:
            continue
        resumen["emitidos"] += 1
        az._add_issue(ancla, "wp_adjunto_indexable", "warning", {
            "host": host,
            "paginas_afectadas": afectadas,
            "en_el_censo": len(censo),
            "de_la_muestra": len(g["sonda"]),
            "miradas": g["miradas"],
            "adjuntos_en_wordpress": total,
            "no_indexables": dict(g["no_indexables"]),
            "ejemplos": ([u for u, _ in censo] + sorted(g["sonda"]))[:MUESTRA],
        }, group_key=_clave(host))
    return resumen


# ---------------------------------------------------------------------------
# 3. Taxonomias anemicas
# ---------------------------------------------------------------------------
def _taxonomias(az, entidades, clases, en_censo, anclas) -> dict[str, Any]:
    umbral = getattr(az, "wp_taxonomia_anemica_max", 1)
    resumen: dict[str, Any] = {"emitidos": 0, "umbral": umbral, "anemicas_sin_rastrear": 0}
    grupos: dict[tuple[str, str], list] = defaultdict(list)
    leidos: dict[str, int] = defaultdict(int)
    for e in entidades:
        if clases.get(e.tipo) != "termino":
            continue
        leidos[e.tipo] += 1
        if e.cuenta is None or e.cuenta > umbral or not e.url_hash:
            continue
        fila = en_censo(e)
        if fila is None:
            resumen["anemicas_sin_rastrear"] += 1
            continue
        if _html_200(fila) and fila.indexable:
            grupos[(_host(fila.url), e.tipo)].append((fila.url, fila.id, e.cuenta))
    for (host, tipo), miembros in sorted(grupos.items()):
        miembros.sort()
        resumen["emitidos"] += 1
        az._add_issue(miembros[0][1], "wp_taxonomia_anemica", "info", {
            "host": host, "taxonomia": tipo, "umbral": umbral,
            "paginas_afectadas": len(miembros),
            "terminos_leidos": leidos[tipo],
            "sin_entradas": sum(1 for m in miembros if m[2] == 0),
            "ejemplos": [{"url": u, "entradas": c} for u, _, c in miembros[:MUESTRA]],
        }, group_key=_clave(f"{host}|{tipo}"))
    return resumen


# ---------------------------------------------------------------------------
# 5. Paginacion que sigue dando 200 mas alla del total
# ---------------------------------------------------------------------------
def _soft404_de_serie(job) -> set[str]:
    """Hosts cuya sonda de soft 404 de la raiz respondio 200 (decision 105)."""
    sondas = ((getattr(job, "soft404", None) or {}).get("sondas") or [])
    return {(s.get("host") or "").lower() for s in sondas
            if isinstance(s, dict) and not s.get("plantilla") and s.get("status") == 200}


def _paginacion(az, job, entidades, anclas) -> dict[str, Any]:
    from shared.respuestas import NO_VALIDAS

    resumen: dict[str, Any] = {"emitidos": 0, "sondas": 0, "con_200": 0,
                               "cubiertas_por_soft404": 0}
    soft = _soft404_de_serie(job)
    por_host: dict[str, list] = defaultdict(list)
    for e in entidades:
        if e.tipo != "sonda_paginacion":
            continue
        resumen["sondas"] += 1
        d = e.datos or {}
        if e.status_code != 200 or "html" not in (d.get("content_type") or "").lower() \
                or d.get("response_class") in NO_VALIDAS:
            continue
        resumen["con_200"] += 1
        host = _host(e.url)
        if host in soft:
            resumen["cubiertas_por_soft404"] += 1
            continue
        por_host[host].append(e)
    # La pagina 1 de cada archivo sondeado, si esta en el censo (el ancla).
    from shared.models import Url

    hashes = sorted({(e.datos or {}).get("pagina_1_hash") for v in por_host.values() for e in v}
                    - {None})
    paginas_1: dict[str, tuple[str, int]] = {}
    for i in range(0, len(hashes), LOTE):
        paginas_1.update({h: (u, i_) for h, u, i_ in az.session.execute(
            select(Url.url_hash, Url.url, Url.id).where(
                Url.job_id == az.job_id, Url.url_hash.in_(hashes[i:i + LOTE])))})
    for host, sondas in sorted(por_host.items()):
        filas = []
        for e in sorted(sondas, key=lambda x: x.url or ""):
            d = e.datos or {}
            ok, _ = _sonda_indexable(e)
            filas.append({"url": e.url, "indexable": ok, "archivo": d.get("archivo"),
                          "entradas": d.get("entradas"), "n": d.get("n"),
                          "enlaza_a_la_siguiente": d.get("enlaza_a_la_siguiente")})
        en_censo = sorted(paginas_1[h] for h in
                          {(e.datos or {}).get("pagina_1_hash") for e in sondas} if h in paginas_1)
        ancla = en_censo[0][1] if en_censo else anclas.de_fichero(sondas[0].url)
        if ancla is None:
            continue
        resumen["emitidos"] += 1
        az._add_issue(ancla, "wp_paginacion_sin_fin",
                      "warning" if any(f["indexable"] for f in filas) else "info", {
                          "host": host,
                          "paginas_afectadas": len(filas),
                          "sondas_del_host": sum(1 for e in entidades
                                                 if e.tipo == "sonda_paginacion"
                                                 and _host(e.url) == host),
                          "sondas": filas[:MUESTRA],
                      }, group_key=_clave(host))
    return resumen
