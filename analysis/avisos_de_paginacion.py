"""Paginacion: lo que Google hace con las paginas 2+ de un listado (decision 122).

Google dejo de usar ``rel=next``/``rel=prev`` para indexar en 2019. Lo que SI
decide que pasa con una serie paginada es:

- si las paginas 2+ se pueden indexar y rastrear (un ``noindex`` o un canonical
  a la pagina 1 las saca, y con ellas lo que solo se enlaza desde ahi);
- si a la pagina siguiente se llega por un ``<a href>`` que Google pueda
  seguir. Un boton «cargar mas» o un enlace por JavaScript no lo es.

Cinco incidencias:

- ``paginacion_canonical_a_la_primera`` (warning): pagina 2+ con el canonical a
  la 1 de SU serie. Google pide que cada pagina de la serie sea su propia
  canonica; con la 1, las 2+ pueden no indexarse y lo que enlazan pierde
  camino. Un canonical a otra URL («ver todo») no es este tipo: lo cubren los
  checks de canonical.
- ``paginacion_noindex`` (info): paginas 2+ en ``noindex``. Es una practica
  corriente y no un error en si; el hallazgo esta en lo que cuelga solo de
  ellas (el siguiente).
- ``solo_enlazada_desde_paginacion_excluida`` (warning): pagina indexable a la
  que solo llegan enlaces desde paginas 2+ en ``noindex`` o canonicalizadas a
  la 1. Una por pagina destino (es la pagina la que hay que enlazar), y
  sustituye a ``orphan_page`` y a ``sitemap_orphan`` en ella (decision 36).
- ``paginacion_sin_enlace`` (warning): la pagina declara ``rel=next`` X y no
  tiene ningun ``<a>`` rastreable hacia X. Lo que se afirma es eso, no que X
  sea inalcanzable: en un censo truncado puede estar enlazada desde otro sitio
  (decisiones 67 y 70).
- ``paginacion_siguiente_rota`` (warning): ``rel=next``/``prev`` hacia una URL
  rastreada que responde 4xx/5xx (sin el 429, ``_es_error``). Un destino sin
  rastrear no se afirma.

Las cuatro de serie son de PLANTILLA (decision 64): sale del listado, y se
arregla una vez. Una fila por (host, forma de la serie) con ``paginas_afectadas``
y ejemplos, anclada en la pagina de URL menor (decision 88).

**Ninguna lectura nueva de ``links``** (#52, decisiones 37 y 41). Lo de enlaces
sale de la temporal ``aristas_tmp`` que ya monta ``compute_link_counts``, con
dos columnas que se rellenan en la MISMA lectura: ``origen_pag_excluida`` y
``rel_destino_url``. Las marca una temporal pequena, ``paginacion_tmp``, con
las paginas que ``analizar_paginacion`` dejo en memoria. Con los enlaces
archivados (decision 95) ``run_all`` ya se niega a analizar, y este paso va
dentro de ``run_all``.

No cambia como reparte una pagina paginada (decision 38): ``origen_cuenta`` y
el PageRank siguen igual. Es otra pregunta.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any
from urllib.parse import urljoin, urlsplit

from sqlalchemy import (BigInteger, Boolean, Column, MetaData, Table, and_, func, insert,
                        select, text)
from sqlalchemy.orm import aliased

from shared.indexabilidad import misma_url
from shared.paginacion import clave_comparable, es_primera_de, pagina_de
from shared.plantillas import firma_de_ruta
from shared.respuestas import NO_VALIDAS

logger = logging.getLogger(__name__)

MUESTRA = 5

TIPOS = (
    "paginacion_canonical_a_la_primera",
    "paginacion_noindex",
    "solo_enlazada_desde_paginacion_excluida",
    "paginacion_sin_enlace",
    "paginacion_siguiente_rota",
)

# Filtro barato antes de `pagina_de` (que normaliza con w3lib): una URL sin
# ninguno de estos rasgos no puede tener marcador de pagina.
_PUEDE_SER_PAGINA = re.compile(r"(?i)pag|pg|[?&]p=|/\d+/?(?:$|\?)")
# Pasos maximos al seguir `rel=prev` hasta la pagina 1.
_MAX_PASOS = 500


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _ruta(url: str) -> str:
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return "/"


def _plantilla(host: str, ruta_serie: str, forma: str) -> str:
    return f"{host} {firma_de_ruta(ruta_serie)} {forma}"


def _absoluta(base: str, href: str | None) -> str | None:
    href = (href or "").strip()
    if not href:
        return None
    try:
        return urljoin(base, href)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Paso 1: urls + html_meta del job (antes de compute_link_counts)
# ---------------------------------------------------------------------------
def analizar_paginacion(az) -> None:
    """Reconoce las paginas 2+, emite los avisos que no dependen de enlaces y
    deja en ``az`` lo que ``compute_link_counts`` necesita.

    Lee ``urls`` y ``html_meta`` filtradas por job, como
    ``analyze_indexability``: nada de ``links``.
    """
    from analysis.analyzer import _clave_de_grupo, _es_error
    from shared.models import HtmlMeta, Url

    az._paginacion_excluidas = {}
    az._paginacion_rel_next = {}

    filas = az.session.execute(
        select(Url.id, Url.url, Url.status_code, Url.is_html, Url.is_internal,
               Url.noindex, Url.response_class, HtmlMeta.canonical_href,
               HtmlMeta.canonical_header, HtmlMeta.rel_next, HtmlMeta.rel_prev)
        .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
        .where(Url.job_id == az.job_id)
    ).all()
    if not filas:
        return

    # URL rastreada -> (id, status, valida). Primero por la cadena tal cual
    # (las de `urls` ya van normalizadas); la clave, solo si hace falta.
    por_url: dict[str, tuple[int, int | None, bool]] = {}
    for f in filas:
        valida = f.response_class is None or f.response_class not in NO_VALIDAS
        por_url[f.url] = (f.id, f.status_code, valida)
    por_clave: dict[tuple, tuple[int, int | None, bool]] | None = None

    def _tal_cual(url: str):
        # La misma cadena, o con/sin la barra final: casi siempre basta, y la
        # clave (w3lib) de todo el censo solo se calcula si no.
        for v in (url, url.rstrip("/"), url.rstrip("/") + "/"):
            hallada = por_url.get(v)
            if hallada is not None:
                return hallada
        return None

    def resolver(url: str | None):
        nonlocal por_clave
        if not url:
            return None
        hallada = _tal_cual(url)
        if hallada is not None:
            return hallada
        if por_clave is None:
            por_clave = {}
            for u, v in por_url.items():
                try:
                    por_clave.setdefault(clave_comparable(u), v)
                except Exception:
                    continue
        try:
            return por_clave.get(clave_comparable(url))
        except Exception:
            return None

    # Paginas candidatas: HTML internas, 200 y que son la respuesta del sitio.
    paginas = []
    for f in filas:
        if not (f.is_html and f.is_internal and f.status_code == 200):
            continue
        if f.response_class is not None and f.response_class in NO_VALIDAS:
            continue
        paginas.append(f)

    # --- Que paginas son 2+, y de que serie -------------------------------
    forma_de: dict[int, Any] = {}
    debiles: dict[tuple, dict[str, Any]] = defaultdict(lambda: {"n": set(), "rel": False})
    for f in paginas:
        if not _PUEDE_SER_PAGINA.search(f.url):
            continue
        p = pagina_de(f.url, debiles=True)
        if p is None:
            continue
        forma_de[f.id] = p
        if p.debil:
            g = debiles[(p.serie, p.forma)]
            g["n"].add(p.n)
            g["rel"] = g["rel"] or bool(f.rel_next or f.rel_prev)
    # Un marcador debil (?p=N, /N final) cuenta si alguna pagina de la serie
    # declara rel=next/prev, o si en el censo estan la 2 y la 3. Dos valores
    # cualesquiera no bastan: dos fichas `/producto/123` y `/producto/456`
    # serian una «serie».
    for pid, p in list(forma_de.items()):
        if p.debil:
            g = debiles[(p.serie, p.forma)]
            if not (g["rel"] or {2, 3} <= g["n"]):
                del forma_de[pid]

    # rel=prev: la pagina 1 de la serie es donde acaba la cadena hacia atras.
    # Asi tambien se reconoce una serie cuya URL no tiene marcador
    # (`?start=20`, `?offset=`).
    prev_de: dict[int, str] = {}
    for f in paginas:
        prev = _absoluta(f.url, f.rel_prev)
        if prev and not misma_url(prev, f.url):
            prev_de[f.id] = prev
    url_de_id = {f.id: f.url for f in paginas}
    cabezas: dict[int, tuple] = {}

    def cabeza(pid: int):
        if pid not in cabezas:
            cabezas[pid] = _cabeza(pid)
        return cabezas[pid]

    def _cabeza(pid: int):
        cur, url, vistos = pid, url_de_id[pid], set()
        for _ in range(_MAX_PASOS):
            prev = prev_de.get(cur)
            if not prev:
                return clave_comparable(url)
            hallada = resolver(prev)
            if hallada is None or hallada[0] in vistos or hallada[0] not in url_de_id:
                return clave_comparable(prev)
            vistos.add(cur)
            cur, url = hallada[0], url_de_id[hallada[0]]
        return clave_comparable(url)

    grupos: dict[str, dict[str, dict[str, Any]]] = {
        t: {} for t in ("paginacion_canonical_a_la_primera", "paginacion_noindex",
                        "paginacion_siguiente_rota")}

    def anotar(tipo: str, plantilla: str, serie, url: str, ejemplo: dict) -> None:
        g = grupos[tipo].setdefault(plantilla, {"series": set(), "paginas": []})
        g["series"].add(serie)
        g["paginas"].append((url, ejemplo))

    for f in paginas:
        p = forma_de.get(f.id)
        segunda = (p is not None and p.n >= 2) or (p is None and f.id in prev_de)
        host = _host(f.url)
        if segunda:
            if p is not None:
                serie, plantilla = p.serie, _plantilla(host, p.serie[2], p.forma)
            else:
                serie = None
                plantilla = _plantilla(host, _ruta(f.url), "rel")
            canonical = _absoluta(f.url, f.canonical_href) or _absoluta(f.url, f.canonical_header)
            a_la_primera = False
            if canonical and not misma_url(canonical, f.url):
                if p is not None and es_primera_de(canonical, p.serie):
                    a_la_primera = True
                elif f.id in prev_de and clave_comparable(canonical) == cabeza(f.id):
                    a_la_primera = True
            if a_la_primera:
                az._paginacion_excluidas[f.id] = "canonical_a_la_primera"
                anotar("paginacion_canonical_a_la_primera", plantilla,
                       serie if serie is not None else cabeza(f.id), f.url,
                       {"url": f.url, "canonical": canonical})
            elif f.noindex:
                az._paginacion_excluidas[f.id] = "noindex"
                anotar("paginacion_noindex", plantilla,
                       serie if serie is not None else cabeza(f.id), f.url, {"url": f.url})

        # rel=next / rel=prev de cualquier pagina (tambien la 1).
        for rel, href in (("next", f.rel_next), ("prev", f.rel_prev)):
            destino = _absoluta(f.url, href)
            if not destino or misma_url(destino, f.url) or _host(destino) != host:
                continue
            hallada = resolver(destino)
            if rel == "next":
                # Los ids que valen como «la siguiente»: la URL y su variante
                # con o sin barra, si se rastrearon.
                aceptables = {v[0] for v in (
                    por_url.get(destino), por_url.get(destino.rstrip("/")),
                    por_url.get(destino.rstrip("/") + "/"), hallada) if v is not None}
                az._paginacion_rel_next[f.id] = (f.url, destino, frozenset(aceptables))
            if hallada is None:
                continue
            _did, codigo, valida = hallada
            if valida and _es_error(codigo):
                pd = pagina_de(destino, debiles=True)
                plantilla = (_plantilla(host, pd.serie[2], pd.forma) if pd is not None
                             else _plantilla(host, _ruta(f.url), "rel"))
                anotar("paginacion_siguiente_rota", plantilla,
                       pd.serie if pd is not None else clave_comparable(f.url), f.url,
                       {"url": f.url, "rel": rel, "destino": destino, "status_code": codigo})

    ids_por_url = {f.url: f.id for f in paginas}
    severidad = {"paginacion_canonical_a_la_primera": "warning",
                 "paginacion_noindex": "info",
                 "paginacion_siguiente_rota": "warning"}
    for tipo_de_serie, por_plantilla in grupos.items():
        for plantilla, g in sorted(por_plantilla.items()):
            g["paginas"].sort(key=lambda par: par[0])
            ancla = ids_por_url[g["paginas"][0][0]]
            az._add_issue(ancla, tipo_de_serie, severidad[tipo_de_serie], {
                "alcance": "plantilla",
                "plantilla": plantilla,
                "series": len(g["series"]),
                "paginas_afectadas": len(g["paginas"]),
                "ejemplos": [e for _u, e in g["paginas"][:MUESTRA]],
            }, group_key=_clave_de_grupo(plantilla))

    logger.info(
        "Paginacion: %d paginas 2+ reconocidas, %d excluidas (noindex o canonical a la 1), "
        "%d con rel=next", sum(1 for p in forma_de.values() if p.n >= 2) + sum(
            1 for pid in prev_de if pid not in forma_de),
        len(az._paginacion_excluidas), len(az._paginacion_rel_next))
    az._flush_issues()


# ---------------------------------------------------------------------------
# Paso 2: dentro de compute_link_counts, sobre la misma lectura de `links`
# ---------------------------------------------------------------------------
def preparar_temporal(az):
    """Crea ``paginacion_tmp`` con las paginas que interesan a las aristas, o
    devuelve None si no hay ninguna (y entonces la lectura de ``links`` queda
    exactamente como estaba)."""
    excluidas = getattr(az, "_paginacion_excluidas", None) or {}
    con_rel = getattr(az, "_paginacion_rel_next", None) or {}
    ids = set(excluidas) | set(con_rel)
    az.session.execute(text("DROP TABLE IF EXISTS paginacion_tmp"))
    if not ids:
        return None
    tabla = Table(
        "paginacion_tmp", MetaData(),
        Column("url_id", BigInteger, primary_key=True),
        Column("excluida", Boolean),
        Column("con_rel", Boolean),
        prefixes=["TEMPORARY"],
    )
    tabla.create(az.session.connection())
    filas = [{"url_id": i, "excluida": i in excluidas, "con_rel": i in con_rel}
             for i in sorted(ids)]
    for n in range(0, len(filas), 5000):
        az.session.execute(insert(tabla), filas[n:n + 5000])
    return tabla


def borrar_temporal(az) -> None:
    az.session.execute(text("DROP TABLE IF EXISTS paginacion_tmp"))


def desde_aristas(az, aristas) -> None:
    """``solo_enlazada_desde_paginacion_excluida`` (se calcula aqui y se emite
    en ``analyze_links``, que decide frente a ``orphan_page``) y
    ``paginacion_sin_enlace``. Solo lee la temporal y ``urls`` por clave
    primaria."""
    az._solo_desde_paginacion = {}
    if not getattr(az, "_paginacion_excluidas", None) and not getattr(
            az, "_paginacion_rel_next", None):
        return
    _solo_desde_paginacion(az, aristas)
    _sin_enlace(az, aristas)
    az._flush_issues()


def _solo_desde_paginacion(az, aristas) -> None:
    from analysis.analyzer import _es_destino_de_redireccion
    from shared.models import Url

    if not az._paginacion_excluidas:
        return
    excl = aristas.c.origen_pag_excluida.is_(True)
    # Las mismas condiciones que los entrantes de `compute_link_counts`.
    base = and_(aristas.c.destino_id.isnot(None), aristas.c.origen_id != aristas.c.destino_id)
    # Solo se agregan las aristas que salen de la paginacion excluida (no el
    # grafo entero): si los origenes que cuentan de esas aristas son TODOS
    # los que cuentan del destino (`unique_inlinks_count`, recien escrito),
    # no le llega nada de fuera. Una noindex no cuenta (38); una
    # canonicalizada a la 1 si, y por eso se resta.
    agregado = (
        select(
            aristas.c.destino_id.label("destino_id"),
            func.count(func.distinct(aristas.c.origen_id)).label("paginas"),
            func.coalesce(func.sum(aristas.c.veces), 0).label("enlaces"),
            func.count(func.distinct(aristas.c.origen_id))
            .filter(aristas.c.origen_cuenta.is_(True)).label("cuentan"),
        )
        .where(base, excl)
        .group_by(aristas.c.destino_id)
        .subquery()
    )
    filas = az.session.execute(
        select(agregado.c.destino_id, agregado.c.paginas, agregado.c.enlaces,
               Url.in_sitemap, Url.url)
        .join(Url, Url.id == agregado.c.destino_id)
        .where(
            func.coalesce(Url.unique_inlinks_count, 0) == agregado.c.cuentan,
            Url.job_id == az.job_id,
            Url.is_html.is_(True), Url.is_internal.is_(True), Url.status_code == 200,
            Url.indexable.is_(True),
            (Url.crawl_depth.is_(None)) | (Url.crawl_depth > 0),
            ~_es_destino_de_redireccion(),
        )
    ).all()
    if not filas:
        return
    detalle = {f.destino_id: {"paginas_que_enlazan": int(f.paginas),
                              "enlaces": int(f.enlaces or 0),
                              "en_sitemap": bool(f.in_sitemap),
                              "con_parametros": "?" in (f.url or ""),
                              "motivos": defaultdict(int), "ejemplos_origen": []}
               for f in filas}
    o = aliased(Url)
    ids = sorted(detalle)
    for n in range(0, len(ids), 5000):
        for destino_id, origen_id, url in az.session.execute(
            select(aristas.c.destino_id, aristas.c.origen_id, o.url).distinct()
            .join(o, o.id == aristas.c.origen_id)
            .where(base, excl, aristas.c.destino_id.in_(ids[n:n + 5000]))
            .order_by(aristas.c.destino_id, o.url)
        ).all():
            d = detalle[destino_id]
            d["motivos"][az._paginacion_excluidas.get(origen_id, "noindex")] += 1
            if len(d["ejemplos_origen"]) < MUESTRA:
                d["ejemplos_origen"].append(url)
    for d in detalle.values():
        d["motivos"] = dict(d["motivos"])
    az._solo_desde_paginacion = detalle


def avisar_solo_desde_paginacion(az, excluir) -> set[int]:
    """Emite ``solo_enlazada_desde_paginacion_excluida`` y devuelve los ids,
    para que ``orphan_page`` y ``sitemap_orphan`` no repitan el hallazgo.
    *excluir*: las que ya llevan ``canonical_sin_enlazar`` (101), que pide
    algo mas concreto."""
    ids = set()
    for uid, d in sorted((getattr(az, "_solo_desde_paginacion", None) or {}).items()):
        if uid in excluir:
            continue
        az._add_issue(uid, "solo_enlazada_desde_paginacion_excluida", "warning", dict(d))
        ids.add(uid)
    return ids


def _sin_enlace(az, aristas) -> None:
    from analysis.analyzer import _clave_de_grupo

    rel_next = az._paginacion_rel_next
    if not rel_next:
        return
    ids = sorted(rel_next)
    # Siguiente rastreada: un `<a>` a ella es una arista con su id.
    enlazadas: set[int] = set()
    con_id = [pid for pid in ids if rel_next[pid][2]]
    for n in range(0, len(con_id), 2000):
        lote = con_id[n:n + 2000]
        objetivo = sorted({d for pid in lote for d in rel_next[pid][2]})
        for origen_id, destino_id in az.session.execute(
            select(aristas.c.origen_id, aristas.c.destino_id).distinct()
            .where(aristas.c.origen_id.in_(lote), aristas.c.destino_id.in_(objetivo))
        ).all():
            if destino_id in rel_next[origen_id][2]:
                enlazadas.add(origen_id)
    # Sin rastrear (el spider no sigue rel=next): la URL del destino, que la
    # misma lectura de `links` dejo en la temporal solo para los destinos sin
    # id de estas paginas.
    destinos: dict[int, list[str]] = defaultdict(list)
    resto = [pid for pid in ids if pid not in enlazadas]
    for n in range(0, len(resto), 5000):
        for origen_id, url in az.session.execute(
            select(aristas.c.origen_id, aristas.c.rel_destino_url).distinct()
            .where(aristas.c.origen_id.in_(resto[n:n + 5000]),
                   aristas.c.rel_destino_url.isnot(None))
        ).all():
            destinos[origen_id].append(url)

    grupos: dict[str, dict[str, Any]] = {}
    for pid in ids:
        url, siguiente, _ids = rel_next[pid]
        if pid in enlazadas or _enlazada(siguiente, destinos.get(pid, ())):
            continue
        host = _host(url)
        pd = pagina_de(siguiente, debiles=True)
        plantilla = (_plantilla(host, pd.serie[2], pd.forma) if pd is not None
                     else _plantilla(host, _ruta(url), "rel"))
        g = grupos.setdefault(plantilla, {"paginas": [], "series": set()})
        g["paginas"].append((url, pid, siguiente))
        g["series"].add(pd.serie if pd is not None else clave_comparable(url))

    for plantilla, g in sorted(grupos.items()):
        g["paginas"].sort()
        az._add_issue(g["paginas"][0][1], "paginacion_sin_enlace", "warning", {
            "alcance": "plantilla",
            "plantilla": plantilla,
            "series": len(g["series"]),
            "paginas_afectadas": len(g["paginas"]),
            "ejemplos": [{"url": u, "siguiente": s} for u, _p, s in g["paginas"][:MUESTRA]],
        }, group_key=_clave_de_grupo(plantilla))


def _enlazada(siguiente: str, destinos) -> bool:
    """¿Alguno de *destinos* (los ``<a>`` de la pagina) es *siguiente*?"""
    if not destinos:
        return False
    corta = siguiente.split("#")[0].rstrip("/")
    for d in destinos:
        if d.split("#")[0].rstrip("/") == corta:
            return True
    try:
        clave = clave_comparable(siguiente)
    except Exception:
        return False
    # La clave normaliza con w3lib (~14 us): solo para los que se le parecen.
    digitos = re.findall(r"\d+", siguiente)
    marca = digitos[-1] if digitos else ""
    for d in destinos:
        if marca and marca not in d:
            continue
        try:
            if clave_comparable(d) == clave:
                return True
        except Exception:
            continue
    return False
