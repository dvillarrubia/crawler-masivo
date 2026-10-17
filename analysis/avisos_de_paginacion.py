"""Paginacion: lo que Google hace con las paginas 2+ de un listado (decision 122).

Google dejo de usar ``rel=next``/``rel=prev`` para indexar en 2019. Lo que SI
decide que pasa con una serie paginada es:

- si las paginas 2+ se pueden indexar y rastrear (un ``noindex`` o un canonical
  a la pagina 1 las saca, y con ellas lo que solo se enlaza desde ahi);
- si a la pagina siguiente se llega por un ``<a href>`` que Google pueda
  seguir. Un boton «cargar mas» o un enlace por JavaScript no lo es.

Siete incidencias:

- ``paginacion_canonical_a_la_primera`` (warning): pagina 2+ con el canonical a
  la 1 de SU serie. Google pide que cada pagina de la serie sea su propia
  canonica; con la 1, las 2+ pueden no indexarse y lo que enlazan pierde
  camino. Un canonical a otra URL («ver todo», o la variante limpia de una
  paginacion con parametros basura) no es este tipo: esa pagina no es del
  listado y la cubren los checks de canonical.
- ``paginacion_noindex``: paginas 2+ en ``noindex``. Es una practica corriente
  y no un error en si (``info``); pasa a ``warning`` cuando deja paginas
  indexables colgando solo de ella, porque entonces el arreglo es ese
  ``noindex`` de plantilla y no enlazar ficha a ficha (decision 64).
- ``solo_enlazada_desde_paginacion_excluida``: pagina indexable a la que solo
  llegan enlaces seguibles desde paginas 2+ en ``noindex`` o canonicalizadas a
  la 1. Una por pagina destino, y sustituye a ``orphan_page``,
  ``sitemap_orphan`` y ``not_reachable_from_home`` en ella (decision 36). Es
  ``info`` cuando la serie de la que cuelga ya lleva el ``warning`` (ahi se
  arregla) y ``warning`` si no (una serie entera en ``noindex`` a proposito: lo
  que hay que hacer es enlazar la pagina desde otra parte).
- ``solo_enlazada_desde_paginacion_profunda`` (info): paginas indexables a las
  que solo enlazan paginas 2+ indexables. Se alcanzan, pero por la cola del
  listado: en cuanto entren elementos nuevos se van una pagina mas lejos.
- ``paginacion_sin_enlace`` (warning): la pagina anuncia la siguiente
  (``rel=next``, o un boton «cargar mas» de la decision 106) y no tiene NINGUN
  ``<a>`` a ella. Si enlaza la pagina siguiente con otra URL (el
  ``?e-page-<id>=2`` de Elementor frente a un ``rel`` a ``/page/2/``) hay
  enlace, y lo que falla es el ``rel``. Lo que se afirma es eso, no que la
  siguiente sea inalcanzable (decisiones 67 y 70).
- ``paginacion_siguiente_rota`` (warning): ``rel=next``/``prev`` hacia una URL
  rastreada que responde 4xx/5xx (sin el 429, ``_es_error``; un muro, decision
  103, no es la pagina). Un destino sin rastrear no se afirma.
- ``paginacion_rel_incorrecto`` (info): ``rel`` a un 3xx, fuera de la serie, o
  que no coincide con el ``<a>`` que la paginacion SI pinta. Google no lo usa;
  es un fallo de plantilla y otros buscadores si lo leen.

Todas menos ``solo_enlazada_desde_paginacion_excluida`` son de PLANTILLA
(decision 64): una fila por (host, forma de la serie) con ``paginas_afectadas``
y ejemplos, anclada en la pagina de URL menor y con la plantilla en
``group_key`` (88). El ancla se mueve entre censos si se rastrea otra pagina:
``shared.comparacion.ANCLADOS_POR_GRUPO`` los casa por la clave, no por la URL.

Una cadena de ``rel`` sin forma de paginacion cuyas paginas 2+ enlaza el resto
del sitio es una cadena de entradas (``adjacent_posts_rel_link`` de WordPress),
no un listado, y se descarta.

**Ninguna lectura nueva de ``links``** (#52, decisiones 37 y 41). Lo de enlaces
sale de la temporal ``aristas_tmp`` que ya monta ``compute_link_counts``, con
tres columnas que se rellenan en la MISMA lectura: ``origen_pag_excluida``,
``origen_pag_profunda`` y ``rel_destino_url``. Las marca una temporal pequena,
``paginacion_tmp``, con las paginas que ``analizar_paginacion`` dejo en
memoria. Con los enlaces archivados (decision 95) ``run_all`` ya se niega a
analizar, y este paso va dentro de ``run_all``.

No cambia como reparte una pagina paginada (decision 38): ``origen_cuenta`` y
el PageRank siguen igual. Es otra pregunta.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from sqlalchemy import (BigInteger, Boolean, Column, MetaData, Table, and_, func, insert,
                        select, text, update)
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
    "solo_enlazada_desde_paginacion_profunda",
    "paginacion_sin_enlace",
    "paginacion_siguiente_rota",
    "paginacion_rel_incorrecto",
)
# Los de plantilla: los casa la comparacion por `group_key` (decision 88).
# `shared.comparacion.ANCLADOS_POR_GRUPO` repite la lista porque la imagen de
# la API no lleva `analysis/`; un test comprueba que coinciden.
TIPOS_DE_PLANTILLA = tuple(t for t in TIPOS if t != "solo_enlazada_desde_paginacion_excluida")

_SEVERIDAD = {
    "paginacion_canonical_a_la_primera": "warning",
    "paginacion_noindex": "info",
    "solo_enlazada_desde_paginacion_profunda": "info",
    "paginacion_sin_enlace": "warning",
    "paginacion_siguiente_rota": "warning",
    "paginacion_rel_incorrecto": "info",
}
_TIPO_DE_MOTIVO = {"noindex": "paginacion_noindex",
                   "canonical_a_la_primera": "paginacion_canonical_a_la_primera"}

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


def _serie_de_url(url: str):
    """La serie de *url*: la de su marcador, o la propia URL si no lleva."""
    p = pagina_de(url, debiles=True)
    return p.serie if p is not None else clave_comparable(url)


@dataclass
class _Siguiente:
    """Lo que una pagina anuncia como su siguiente (``rel=next`` o boton)."""

    url: str
    siguiente: str
    aceptables: frozenset          # ids rastreados que valen como «la siguiente»
    n: int | None                  # numero de la siguiente, si su URL lo dice
    plantilla: str
    rel: bool                      # lo dice un rel=next (si no, solo un boton)
    boton: bool = False
    cadena: tuple | None = None    # serie de solo-`rel` (para descartar cadenas)


def _estado(az) -> None:
    az._paginacion_excluidas = {}      # id -> "noindex" | "canonical_a_la_primera"
    az._paginacion_profundas = set()   # paginas 2+ que siguen contando
    az._paginacion_plantilla = {}      # id de pagina 2+ -> plantilla
    az._paginacion_serie = {}          # id de pagina 2+ -> serie
    az._paginacion_promueve = {}       # id excluida -> su serie no es «toda noindex»
    az._paginacion_cadena = {}         # id de pagina 2+ de solo-rel -> serie
    az._paginacion_rel_next = {}       # id -> _Siguiente
    az._paginacion_grupos = defaultdict(lambda: defaultdict(list))
    az._enr_de_paginacion = set()      # filas de la 106 que son la paginacion
    az._solo_desde_paginacion = {}
    az._paginacion_colgadas = set()


# ---------------------------------------------------------------------------
# Paso 1: urls + html_meta del job (antes de compute_link_counts)
# ---------------------------------------------------------------------------
def analizar_paginacion(az) -> None:
    """Reconoce las paginas 2+ y deja en ``az`` lo que ``compute_link_counts``
    necesita. No emite nada: casi todo depende de los enlaces (lo que cuelga de
    la serie, si la cadena de `rel` es un listado, si hay `<a>` a la siguiente)
    y se emite en :func:`desde_aristas`.

    Lee ``urls`` y ``html_meta`` filtradas por job, como
    ``analyze_indexability``, y los botones de ``enlaces_no_rastreables``
    (decision 106) por su ``job_id``: nada de ``links``.
    """
    from analysis.analyzer import _es_error
    from shared.models import HtmlMeta, Url

    _estado(az)

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

    def _por_clave(clave):
        nonlocal por_clave
        if por_clave is None:
            por_clave = {}
            for u, v in por_url.items():
                try:
                    por_clave.setdefault(clave_comparable(u), v)
                except Exception:
                    continue
        return por_clave.get(clave)

    def resolver(url: str | None):
        if not url:
            return None
        hallada = _tal_cual(url)
        if hallada is not None:
            return hallada
        try:
            return _por_clave(clave_comparable(url))
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
    noindex_de = {f.id: bool(f.noindex) for f in paginas}

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
    # Tambien es evidencia que OTRA pagina anuncie con `rel` una URL debil de
    # esa serie: la ficha `x.html` con `rel=next` a `x.html?p=2` (progym).
    for f in paginas:
        for href in (f.rel_next, f.rel_prev):
            destino = _absoluta(f.url, href)
            if destino and _PUEDE_SER_PAGINA.search(destino):
                pd = pagina_de(destino, debiles=True)
                if pd is not None and pd.debil:
                    debiles[(pd.serie, pd.forma)]["rel"] = True
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

    def serie_toda_noindex(serie) -> bool:
        """¿La pagina 1 de *serie* se rastreo y es noindex? (un archivo por
        fecha entero en noindex: una decision sobre el listado)."""
        hallada = _por_clave(serie)
        return hallada is not None and noindex_de.get(hallada[0], False)

    botones = _botones(az, {f.id for f in paginas})

    def anotar(tipo: str, plantilla: str, serie, pid: int, url: str, ejemplo: dict) -> None:
        az._paginacion_grupos[tipo][plantilla].append((url, pid, serie, ejemplo))

    for f in paginas:
        p = forma_de.get(f.id)
        segunda = (p is not None and p.n >= 2) or (p is None and f.id in prev_de)
        host = _host(f.url)
        canonical = _absoluta(f.url, f.canonical_href) or _absoluta(f.url, f.canonical_header)
        if p is not None:
            serie, plantilla = p.serie, _plantilla(host, p.serie[2], p.forma)
        elif segunda:
            serie, plantilla = cabeza(f.id), _plantilla(host, _ruta(f.url), "rel")
        else:
            serie, plantilla = clave_comparable(f.url), None

        # Una variante canonicalizada a OTRA serie no es del listado: un
        # filtro, un `?x=ooo` o un id de sesion de una paginacion que vive en
        # otra parte (en blogs.uoc.edu, paginaciones enteras con parametros
        # basura). Ni es pagina 2+ ni su `rel` es el del listado. Canonicalizar
        # a la 1 de su PROPIA serie no la saca: es el aviso de abajo.
        a_la_primera = False
        if canonical and not misma_url(canonical, f.url):
            if p is not None and es_primera_de(canonical, p.serie):
                a_la_primera = segunda
            elif p is None and segunda and clave_comparable(canonical) == serie:
                a_la_primera = True
            elif _serie_de_url(canonical) != serie:
                continue

        if segunda:
            az._paginacion_plantilla[f.id] = plantilla
            az._paginacion_serie[f.id] = serie
            if p is None:
                az._paginacion_cadena[f.id] = serie
            if a_la_primera:
                az._paginacion_excluidas[f.id] = "canonical_a_la_primera"
                anotar("paginacion_canonical_a_la_primera", plantilla, serie, f.id, f.url,
                       {"url": f.url, "canonical": canonical})
            elif f.noindex:
                az._paginacion_excluidas[f.id] = "noindex"
                az._paginacion_promueve[f.id] = not serie_toda_noindex(serie)
                anotar("paginacion_noindex", plantilla, serie, f.id, f.url, {"url": f.url})
            else:
                az._paginacion_profundas.add(f.id)

        cadena = (serie if (p is None and (segunda or f.rel_next)) else None)
        ruta_serie = serie[2]

        # rel=next / rel=prev de cualquier pagina (tambien la 1).
        for rel, href in (("next", f.rel_next), ("prev", f.rel_prev)):
            destino = _absoluta(f.url, href)
            if not destino or misma_url(destino, f.url):
                continue
            pd = pagina_de(destino, debiles=True)
            pl = (_plantilla(host, pd.serie[2], pd.forma) if pd is not None
                  else plantilla or _plantilla(host, _ruta(f.url), "rel"))
            ejemplo = {"url": f.url, "rel": rel, "destino": destino}
            # Fuera de la serie: a otro host, o a una ruta que no es la del
            # listado (para una pagina con marcador; una de solo-`rel` no
            # tiene forma con la que comparar).
            ruta_destino = (pd.serie if pd is not None else clave_comparable(destino))[2]
            if _host(destino) != host or (
                    (p is not None or pd is not None) and ruta_destino != ruta_serie):
                anotar("paginacion_rel_incorrecto", pl, serie, f.id, f.url,
                       dict(ejemplo, motivo="fuera_de_la_serie"))
                continue
            hallada = resolver(destino)
            if rel == "next":
                # Los ids que valen como «la siguiente»: la URL y su variante
                # con o sin barra, si se rastrearon.
                aceptables = {v[0] for v in (
                    por_url.get(destino), por_url.get(destino.rstrip("/")),
                    por_url.get(destino.rstrip("/") + "/"), hallada) if v is not None}
                az._paginacion_rel_next[f.id] = _Siguiente(
                    f.url, destino, frozenset(aceptables), pd.n if pd is not None else None,
                    pl, True, cadena=cadena)
            if hallada is None:
                continue
            _did, codigo, valida = hallada
            if not valida:
                continue  # un muro no es la pagina (103): como sin rastrear
            if _es_error(codigo):
                anotar("paginacion_siguiente_rota", pl,
                       pd.serie if pd is not None else serie, f.id, f.url,
                       dict(ejemplo, status_code=codigo))
            elif codigo is not None and 300 <= codigo < 400:
                anotar("paginacion_rel_incorrecto", pl, serie, f.id, f.url,
                       dict(ejemplo, motivo="destino_redireccion", status_code=codigo))

        # Un boton «cargar mas» (decision 106) a la pagina n+1 de su serie es
        # la paginacion, aunque la pagina 2 no se rastreara ni haya `rel`: es
        # el caso tipico. Lo dice `paginacion_sin_enlace`, no un segundo aviso.
        if f.id in botones and not (p is None and segunda):
            n = p.n if p is not None else 1
            for fila in botones[f.id]:
                pd = pagina_de(fila.destino, debiles=True)
                if pd is None or pd.serie != serie or pd.n != n + 1:
                    continue
                az._enr_de_paginacion.add(fila.id)
                sig = az._paginacion_rel_next.get(f.id)
                if sig is not None:
                    sig.boton = True
                    continue
                hallada = resolver(fila.destino)
                az._paginacion_rel_next[f.id] = _Siguiente(
                    f.url, fila.destino, frozenset({hallada[0]} if hallada else ()),
                    pd.n, _plantilla(host, pd.serie[2], pd.forma), False, boton=True)

    logger.info(
        "Paginacion: %d paginas 2+ reconocidas, %d excluidas (noindex o canonical a la 1), "
        "%d con la siguiente anunciada (rel o boton)", len(az._paginacion_plantilla),
        len(az._paginacion_excluidas), len(az._paginacion_rel_next))


def _botones(az, ids: set[int]) -> dict[int, list[Any]]:
    """Filas de ``enlaces_no_rastreables`` (106) con destino interno, por origen.

    Una lectura por ``job_id`` (la tabla solo tiene los botones y
    ``javascript:`` del censo, no los enlaces) y el filtro por origen en Python.
    """
    from sqlalchemy import inspect

    from shared.models import EnlaceNoRastreable as E

    if not inspect(az.session.connection()).has_table(E.__tablename__):
        return {}
    out: dict[int, list[Any]] = defaultdict(list)
    for fila in az.session.execute(
        select(E.id, E.from_url_id, E.destino)
        .where(E.job_id == az.job_id, E.destino.isnot(None), E.destino_interno.is_(True))
    ).all():
        if fila.from_url_id in ids:
            out[fila.from_url_id].append(fila)
    return out


# ---------------------------------------------------------------------------
# Paso 2: dentro de compute_link_counts, sobre la misma lectura de `links`
# ---------------------------------------------------------------------------
def preparar_temporal(az):
    """Crea ``paginacion_tmp`` con las paginas que interesan a las aristas, o
    devuelve None si no hay ninguna (y entonces la lectura de ``links`` queda
    exactamente como estaba)."""
    excluidas = getattr(az, "_paginacion_excluidas", None) or {}
    profundas = getattr(az, "_paginacion_profundas", None) or set()
    con_rel = getattr(az, "_paginacion_rel_next", None) or {}
    ids = set(excluidas) | set(profundas) | set(con_rel)
    az.session.execute(text("DROP TABLE IF EXISTS paginacion_tmp"))
    if not ids:
        return None
    tabla = Table(
        "paginacion_tmp", MetaData(),
        Column("url_id", BigInteger, primary_key=True),
        Column("excluida", Boolean),
        Column("profunda", Boolean),
        Column("con_rel", Boolean),
        prefixes=["TEMPORARY"],
    )
    tabla.create(az.session.connection())
    filas = [{"url_id": i, "excluida": i in excluidas, "profunda": i in profundas,
              "con_rel": i in con_rel} for i in sorted(ids)]
    for n in range(0, len(filas), 5000):
        az.session.execute(insert(tabla), filas[n:n + 5000])
    return tabla


def borrar_temporal(az) -> None:
    az.session.execute(text("DROP TABLE IF EXISTS paginacion_tmp"))


def desde_aristas(az, aristas) -> None:
    """Todo lo que depende de los enlaces, y la emision de los avisos de
    plantilla. Solo lee la temporal y ``urls`` por clave primaria. Va antes de
    la profundidad de clic, que no repite lo que se dice aqui."""
    az._solo_desde_paginacion = {}
    az._paginacion_colgadas = set()
    if not hasattr(az, "_paginacion_grupos"):
        return  # el paso 1 no corrio
    if az._paginacion_excluidas or az._paginacion_profundas or az._paginacion_rel_next:
        _descartar_cadenas_de_entradas(az, aristas)
        _solo_desde_paginacion(az, aristas)
        _profundas(az, aristas)
        _sin_enlace(az, aristas)
    _emitir_plantillas(az)
    az._flush_issues()


def _descartar_cadenas_de_entradas(az, aristas) -> None:
    """Quita las series de solo-``rel`` que no son un listado.

    Una cadena de ``rel=next/prev`` entre paginas rastreadas no dice por si sola
    que haya un listado: WordPress (``adjacent_posts_rel_link``) la pinta entre
    entradas sueltas. La diferencia se ve en los enlaces: a la pagina 3 de un
    listado se llega desde el listado; a una entrada, desde la portada y las
    categorias. Si **mas de la mitad** de las paginas 2+ de la cadena reciben un
    enlace seguible desde una pagina que cuenta y es de FUERA de la cadena, es
    una cadena de entradas. Las series con marcador no pasan por aqui.
    """
    cadena = az._paginacion_cadena
    if not cadena:
        return
    miembros: dict[tuple, set[int]] = defaultdict(set)
    for i, serie in cadena.items():
        miembros[serie].add(i)
    for i, sig in az._paginacion_rel_next.items():
        if sig.cadena is not None:
            miembros[sig.cadena].add(i)
    desde_fuera: set[int] = set()
    ids = sorted(cadena)
    for n in range(0, len(ids), 2000):
        for destino, origen in az.session.execute(
            select(aristas.c.destino_id, aristas.c.origen_id).distinct().where(
                aristas.c.destino_id.in_(ids[n:n + 2000]),
                aristas.c.seguible.is_(True),
                aristas.c.origen_cuenta.is_(True),
                aristas.c.origen_id != aristas.c.destino_id,
            )
        ).all():
            if origen not in miembros[cadena[destino]]:
                desde_fuera.add(destino)
    descartadas: set[tuple] = set()
    por_serie: dict[tuple, list[int]] = defaultdict(list)
    for i, serie in cadena.items():
        por_serie[serie].append(i)
    for serie, prof in por_serie.items():
        if sum(1 for i in prof if i in desde_fuera) * 2 > len(prof):
            descartadas.add(serie)
    if not descartadas:
        return
    fuera = {i for s in descartadas for i in miembros[s]}
    for d in (az._paginacion_excluidas, az._paginacion_plantilla, az._paginacion_serie,
              az._paginacion_cadena, az._paginacion_rel_next, az._paginacion_promueve):
        for i in fuera:
            d.pop(i, None)
    az._paginacion_profundas -= fuera
    for por_plantilla in az._paginacion_grupos.values():
        for pl in list(por_plantilla):
            por_plantilla[pl] = [e for e in por_plantilla[pl] if e[1] not in fuera]
            if not por_plantilla[pl]:
                del por_plantilla[pl]
    # Sus aristas dejan de ser «de paginacion». La temporal es nuestra.
    lista = sorted(fuera)
    for n in range(0, len(lista), 2000):
        az.session.execute(
            update(aristas).where(aristas.c.origen_id.in_(lista[n:n + 2000]))
            .values(origen_pag_excluida=None, origen_pag_profunda=None))
    logger.info("Paginacion: %d cadenas de rel descartadas (entradas, no listados), %d paginas",
                len(descartadas), len(fuera))


def _solo_desde_paginacion(az, aristas) -> None:
    from analysis.analyzer import _es_destino_de_redireccion
    from shared.models import Url

    if not az._paginacion_excluidas:
        return
    excl = aristas.c.origen_pag_excluida.is_(True)
    base = and_(aristas.c.destino_id.isnot(None), aristas.c.origen_id != aristas.c.destino_id,
                aristas.c.seguible.is_(True))
    # Los destinos de las aristas seguibles que salen de la paginacion
    # excluida, y de ellos los que no tienen NINGUN enlace seguible desde una
    # pagina que cuenta y no esta excluida: un EXISTS por el indice de destino.
    # Un `nofollow` no es un camino (90) y una noindex no cuenta (38); una
    # canonicalizada a la 1 si cuenta como entrante y por eso se resta.
    otro = aristas.alias("otro")
    hay_camino = (
        select(otro.c.destino_id).where(
            otro.c.destino_id == aristas.c.destino_id,
            otro.c.seguible.is_(True),
            otro.c.origen_cuenta.is_(True),
            otro.c.origen_pag_excluida.isnot(True),
            otro.c.origen_id != otro.c.destino_id,
        ).exists()
    )
    agregado = (
        select(
            aristas.c.destino_id.label("destino_id"),
            func.count(func.distinct(aristas.c.origen_id)).label("paginas"),
            func.coalesce(func.sum(aristas.c.veces), 0).label("enlaces"),
            func.count(func.distinct(aristas.c.origen_id))
            .filter(aristas.c.origen_cuenta.is_(True)).label("cuentan"),
        )
        .where(base, excl, ~hay_camino)
        .group_by(aristas.c.destino_id)
        .subquery()
    )
    filas = az.session.execute(
        select(agregado.c.destino_id, agregado.c.paginas, agregado.c.enlaces,
               agregado.c.cuentan, Url.in_sitemap, Url.url, Url.indexable,
               Url.unique_inlinks_count)
        .join(Url, Url.id == agregado.c.destino_id)
        .where(
            Url.job_id == az.job_id,
            Url.is_html.is_(True), Url.is_internal.is_(True), Url.status_code == 200,
            (Url.crawl_depth.is_(None)) | (Url.crawl_depth > 0),
            ~_es_destino_de_redireccion(),
        )
    ).all()
    if not filas:
        return
    # Indexables: aviso por pagina. Una pagina 2+ excluida que solo cuelga de
    # paginas excluidas de su plantilla: se cuenta en el aviso de la serie
    # (`solo_enlazadas_por_la_serie`), no como `orphan_page` («nadie la
    # enlaza» es falso). El resto, sin cambios: conserva su aviso de siempre.
    detalle = {}
    candidatas_colgadas = {}
    for f in filas:
        if f.indexable is True:
            d = {"paginas_que_enlazan": int(f.paginas), "enlaces": int(f.enlaces or 0),
                 "en_sitemap": bool(f.in_sitemap), "con_parametros": "?" in (f.url or ""),
                 "motivos": defaultdict(int), "ejemplos_origen": [], "_url": f.url,
                 "_grupos": set()}
            otros = int(f.unique_inlinks_count or 0) - int(f.cuentan or 0)
            if otros > 0:
                # Entrantes que cuentan y no son un camino: `nofollow` desde
                # paginas indexables (38, 90). Se dice, no se calla.
                d["otros_entrantes"] = otros
            detalle[f.destino_id] = d
        elif f.destino_id in az._paginacion_excluidas:
            candidatas_colgadas[f.destino_id] = set()
    o = aliased(Url)
    ids = sorted(set(detalle) | set(candidatas_colgadas))
    for n in range(0, len(ids), 5000):
        for destino_id, origen_id, url in az.session.execute(
            select(aristas.c.destino_id, aristas.c.origen_id, o.url).distinct()
            .join(o, o.id == aristas.c.origen_id)
            .where(base, excl, aristas.c.destino_id.in_(ids[n:n + 5000]))
            .order_by(aristas.c.destino_id, o.url)
        ).all():
            motivo = az._paginacion_excluidas.get(origen_id, "noindex")
            grupo = (_TIPO_DE_MOTIVO[motivo], az._paginacion_plantilla.get(origen_id),
                     origen_id)
            if destino_id in candidatas_colgadas:
                candidatas_colgadas[destino_id].add(grupo[1])
                continue
            d = detalle[destino_id]
            d["motivos"][motivo] += 1
            d["_grupos"].add(grupo)
            if len(d["ejemplos_origen"]) < MUESTRA:
                d["ejemplos_origen"].append(url)
    for d in detalle.values():
        d["motivos"] = dict(d["motivos"])
    az._solo_desde_paginacion = detalle
    for i, plantillas in candidatas_colgadas.items():
        if az._paginacion_plantilla.get(i) in plantillas:
            az._paginacion_colgadas.add(i)


def _profundas(az, aristas) -> None:
    """``solo_enlazada_desde_paginacion_profunda``: indexables cuyos enlaces
    seguibles que cuentan salen todos de paginas 2+ que cuentan."""
    from analysis.analyzer import _es_destino_de_redireccion
    from shared.models import Url

    if not az._paginacion_profundas:
        return
    otro = aristas.alias("otro")
    hay_otro = (
        select(otro.c.destino_id).where(
            otro.c.destino_id == aristas.c.destino_id,
            otro.c.seguible.is_(True),
            otro.c.origen_cuenta.is_(True),
            otro.c.origen_pag_profunda.isnot(True),
            otro.c.origen_pag_excluida.isnot(True),
            otro.c.origen_id != otro.c.destino_id,
        ).exists()
    )
    o = aliased(Url)
    filas = az.session.execute(
        select(aristas.c.destino_id, Url.url, aristas.c.origen_id, o.url).distinct()
        .join(Url, Url.id == aristas.c.destino_id)
        .join(o, o.id == aristas.c.origen_id)
        .where(
            aristas.c.origen_pag_profunda.is_(True),
            aristas.c.seguible.is_(True),
            aristas.c.destino_id.isnot(None),
            aristas.c.origen_id != aristas.c.destino_id,
            ~hay_otro,
            Url.job_id == az.job_id,
            Url.is_html.is_(True), Url.is_internal.is_(True), Url.status_code == 200,
            Url.indexable.is_(True),
            (Url.crawl_depth.is_(None)) | (Url.crawl_depth > 0),
            ~_es_destino_de_redireccion(),
        )
    ).all()
    # Por destino, el origen de URL menor decide la plantilla (y el ejemplo).
    elegido: dict[int, tuple[str, int, str]] = {}
    for destino_id, destino_url, origen_id, origen_url in filas:
        if destino_id in az._paginacion_plantilla:
            continue  # una pagina del propio listado: no es «lo que enlaza»
        actual = elegido.get(destino_id)
        if actual is None or origen_url < actual[2]:
            elegido[destino_id] = (destino_url, origen_id, origen_url)
    for destino_id, (destino_url, origen_id, origen_url) in elegido.items():
        pl = az._paginacion_plantilla.get(origen_id)
        if pl is None:
            continue
        az._paginacion_grupos["solo_enlazada_desde_paginacion_profunda"][pl].append(
            (origen_url, origen_id, az._paginacion_serie.get(origen_id),
             {"url": destino_url, "ejemplo_origen": origen_url}))


def _sin_enlace(az, aristas) -> None:
    from shared.models import Url

    rel_next = az._paginacion_rel_next
    if not rel_next:
        return
    ids = sorted(rel_next)
    # Siguiente rastreada: un `<a>` a ella es una arista con su id.
    enlazadas: set[int] = set()
    con_id = [pid for pid in ids if rel_next[pid].aceptables]
    for n in range(0, len(con_id), 2000):
        lote = con_id[n:n + 2000]
        objetivo = sorted({d for pid in lote for d in rel_next[pid].aceptables})
        for origen_id, destino_id in az.session.execute(
            select(aristas.c.origen_id, aristas.c.destino_id).distinct()
            .where(aristas.c.origen_id.in_(lote), aristas.c.destino_id.in_(objetivo))
        ).all():
            if destino_id in rel_next[origen_id].aceptables:
                enlazadas.add(origen_id)
    # Lo que enlazan las demas: sin rastrear (el spider no sigue rel=next), la
    # URL que la misma lectura de `links` dejo en la temporal; rastreados, la
    # de `urls` por clave primaria. Afirmar que NO hay enlace exige no
    # encontrar ninguno (67): vale la pagina siguiente con cualquier URL.
    destinos: dict[int, list[str]] = defaultdict(list)
    resto = [pid for pid in ids if pid not in enlazadas]
    d = aliased(Url)
    for n in range(0, len(resto), 5000):
        lote = resto[n:n + 5000]
        for origen_id, url in az.session.execute(
            select(aristas.c.origen_id, aristas.c.rel_destino_url).distinct()
            .where(aristas.c.origen_id.in_(lote), aristas.c.rel_destino_url.isnot(None))
        ).all():
            destinos[origen_id].append(url)
        for origen_id, url in az.session.execute(
            select(aristas.c.origen_id, d.url).distinct()
            .join(d, d.id == aristas.c.destino_id)
            .where(aristas.c.origen_id.in_(lote))
        ).all():
            destinos[origen_id].append(url)

    for pid in ids:
        sig = rel_next[pid]
        if pid in enlazadas or _enlazada(sig.siguiente, destinos.get(pid, ())):
            continue
        ejemplo = {"url": sig.url, "siguiente": sig.siguiente}
        if _enlaza_la_pagina(sig.n, destinos.get(pid, ())):
            # Hay `<a>` a la pagina siguiente, con otra URL: lo que falla es el
            # rel (un boton sin rel que tambien tiene su `<a>` no falla nada).
            if sig.rel:
                az._paginacion_grupos["paginacion_rel_incorrecto"][sig.plantilla].append(
                    (sig.url, pid, _serie_de_url(sig.siguiente),
                     {"url": sig.url, "rel": "next", "destino": sig.siguiente,
                      "motivo": "no_coincide_con_el_enlace"}))
            continue
        if sig.boton:
            ejemplo["boton_js"] = True
        az._paginacion_grupos["paginacion_sin_enlace"][sig.plantilla].append(
            (sig.url, pid, _serie_de_url(sig.siguiente), ejemplo))


def _enlaza_la_pagina(n: int | None, destinos) -> bool:
    """¿Alguno de *destinos* es una pagina numero *n*, con cualquier URL?

    Generoso a proposito: para afirmar que NO hay enlace hay que no encontrar
    ninguno (67). En progym las fichas declaran ``rel=next`` a
    ``ficha.html?p=2`` y su paginador enlaza ``home.html?p=2``.
    """
    if n is None or n < 2:
        return False
    marca = str(n)
    for u in destinos:
        if marca not in u:
            continue
        p = pagina_de(u, debiles=True)
        if p is not None and p.n == n:
            return True
    return False


def _emitir_plantillas(az) -> None:
    """Una fila por (tipo, plantilla), con la severidad que dicta lo que deja
    colgando cada serie (decision 64)."""
    from analysis.analyzer import _clave_de_grupo

    # Que filas de serie dejan indexables colgando solo de ellas. Una serie
    # entera en noindex (su pagina 1 tambien) es una decision sobre el listado:
    # no se promueve, y el `warning` es de la pagina, que hay que enlazar desde
    # otra parte.
    dependientes: dict[tuple, set[int]] = defaultdict(set)
    for uid, d in az._solo_desde_paginacion.items():
        promovida = False
        for tipo, pl, origen_id in d["_grupos"]:
            if tipo == "paginacion_canonical_a_la_primera" or \
                    az._paginacion_promueve.get(origen_id, False):
                dependientes[(tipo, pl)].add(uid)
                promovida = True
        d["_en_serie"] = promovida
    colgadas: dict[tuple, int] = defaultdict(int)
    for i in az._paginacion_colgadas:
        tipo = _TIPO_DE_MOTIVO[az._paginacion_excluidas[i]]
        colgadas[(tipo, az._paginacion_plantilla[i])] += 1

    for tipo_de_serie in TIPOS_DE_PLANTILLA:
        tipo = tipo_de_serie
        for plantilla, paginas in sorted(az._paginacion_grupos.get(tipo, {}).items()):
            if not paginas:
                continue
            paginas = sorted(paginas, key=lambda e: (e[0], e[1]))
            detalles: dict[str, Any] = {
                "alcance": "plantilla",
                "plantilla": plantilla,
                "series": len({e[2] for e in paginas}),
                "paginas_afectadas": len({e[1] for e in paginas})
                if tipo != "solo_enlazada_desde_paginacion_profunda" else len(paginas),
                "ejemplos": [e[3] for e in paginas[:MUESTRA]],
            }
            severidad = _SEVERIDAD[tipo]
            if tipo in _TIPO_DE_MOTIVO.values():
                dep = sorted(az._solo_desde_paginacion[i]["_url"]
                             for i in dependientes.get((tipo, plantilla), ()))
                if dep:
                    detalles["indexables_que_solo_cuelgan_de_ella"] = len(dep)
                    detalles["ejemplos_indexables"] = dep[:MUESTRA]
                    severidad = "warning"
                if colgadas.get((tipo, plantilla)):
                    detalles["solo_enlazadas_por_la_serie"] = colgadas[(tipo, plantilla)]
            if tipo == "paginacion_sin_enlace":
                botones = sum(1 for e in paginas if e[3].get("boton_js"))
                if botones:
                    detalles["con_boton_js"] = botones
            if tipo == "paginacion_rel_incorrecto":
                motivos: dict[str, int] = defaultdict(int)
                for e in paginas:
                    motivos[e[3]["motivo"]] += 1
                detalles["motivos"] = dict(motivos)
            # La clave sin el host: la comparacion entre censos casa estos
            # tipos por (tipo, host de la URL, clave), y asi un `map_host`
            # (pre.x.com = x.com) tambien los casa (decision 67).
            az._add_issue(paginas[0][1], tipo_de_serie, severidad, detalles,
                          group_key=_clave_de_grupo(plantilla.split(" ", 1)[-1]))


def avisar_solo_desde_paginacion(az, excluir) -> set[int]:
    """Emite ``solo_enlazada_desde_paginacion_excluida`` y devuelve los ids
    —con las paginas 2+ contadas en su serie—, para que ``orphan_page`` y
    ``sitemap_orphan`` no repitan el hallazgo. *excluir*: las que ya llevan
    ``canonical_sin_enlazar`` (101), que pide algo mas concreto."""
    ids = set(getattr(az, "_paginacion_colgadas", None) or ())
    for uid, d in sorted((getattr(az, "_solo_desde_paginacion", None) or {}).items()):
        if uid in excluir:
            continue
        detalles = {k: v for k, v in d.items() if not k.startswith("_")}
        az._add_issue(uid, "solo_enlazada_desde_paginacion_excluida",
                      "info" if d.get("_en_serie") else "warning", detalles)
        ids.add(uid)
    return ids


def sustituidas(az) -> set[int]:
    """Las paginas cuyo aviso de enlazado lo da la paginacion: no llevan
    ``sitemap_orphan`` ni ``not_reachable_from_home`` (36)."""
    return set(getattr(az, "_solo_desde_paginacion", None) or ()) | set(
        getattr(az, "_paginacion_colgadas", None) or ())


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
