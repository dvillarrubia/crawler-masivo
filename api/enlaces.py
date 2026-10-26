"""Pestaña Enlaces con filtros en el servidor (decision 195; revision de UX del
25-oct, 2.8).

La lista de enlaces de un censo era una tabla de ~926.000 filas sin filtros,
ordenada por el hash del destino: no contestaba ni «quien enlaza a X» ni «que
enlaces externos estan rotos». Aqui viven los filtros, el estado del destino y
la vista agrupada por destino, con las DOS fuentes que tiene un censo:

- **`links`** (rastreos en curso, o un censo que se esta re-analizando):
  SIEMPRE filtrando por `links.job_id` (decisiones 37 y 41). El estado del
  destino sale de subconsultas sobre `urls` y `respuestas_sondeadas`, cada una
  filtrada por SU `job_id`, en un `IN (... UNION ALL ...)` o un `NOT EXISTS`
  al nivel de arriba del WHERE: Postgres los resuelve con un semi-join o un
  anti-join por hash, sin unir `links` con `urls` ni recorrer la tabla de
  todos los rastreos.
- **El Parquet** de un censo archivado (decisiones 95 y 150), que en
  produccion es casi todo censo completado: los mismos filtros como expresion
  de pyarrow, que solo lee las columnas que pide y, como el fichero va
  ordenado por destino, salta los grupos de filas que no tocan al filtrar por
  un destino concreto. El estado del destino, con los hashes de cada estado
  sacados de `urls` y de las sondas del job.

Las dos fuentes clasifican igual (`grupo_interno` y `grupo_sonda` son la
referencia y las subconsultas la reproducen) y un test exige que la API
devuelva LO MISMO antes y despues de archivar, filtro a filtro.

**Lo que no se puede afirmar no se afirma** (67, 70, 89, 91): un destino que
no esta en el censo ni se sondeo es `sin_dato`, no «bien»; un 403 o un 5xx de
un tercero es `no_concluyente`, y solo `roto` y `redirige_a_error` de la sonda
(los `ROTOS` de `shared/sondas.py`, los mismos que dan el aviso de enlace
externo roto) cuentan como roto.

Los conteos por aparicion son `SUM(repeticiones)` (decision 94); «enlaces»
son filas (un enlace distinto en una pagina) y «paginas» origenes distintos.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, fields, replace
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session, aliased, joinedload

from shared import archivo_enlaces
from shared import sondas as S
from shared.models import Link, RespuestaSondeada, Url

# Estado del destino, en el orden en que se listan: lo que hay que arreglar,
# primero. `sin_dato` va detras de `no_concluyente` (de ese al menos hay una
# respuesta) y `ok` el ultimo.
GRUPOS = ("roto", "redireccion", "no_concluyente", "sin_dato", "ok")
RANGO = {g: i for i, g in enumerate(GRUPOS)}
POSICIONES = ("nav", "header", "content", "sidebar", "footer", "sin")


@dataclass(frozen=True)
class FiltrosEnlaces:
    destino: str = ""           # la URL de destino contiene esto
    destino_exacto: bool = False  # ... o es exactamente esto
    destino_hash: str = ""      # el destino por su hash (quien enlaza a X)
    origen: str = ""            # la URL de la pagina que enlaza contiene esto
    ancla: str = ""             # el texto del enlace (o el alt de su imagen)
    tipo: str = ""              # interno | externo
    follow: str = ""            # si | no
    posicion: str = ""          # nav | header | content | sidebar | footer | sin
    estado: str = ""            # uno de GRUPOS
    codigo: int | None = None   # codigo HTTP del destino (el del censo o la sonda)

    def sin_estado(self) -> "FiltrosEnlaces":
        return replace(self, estado="")

    def clave(self) -> tuple:
        return tuple(getattr(self, f.name) for f in fields(self))

    def activos(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)
                if getattr(self, f.name) not in ("", None, False)}


# ---------------------------------------------------------------------------
# Estado del destino
# ---------------------------------------------------------------------------
def grupo_interno(status: int | None) -> str:
    """Un destino que esta en el censo (`urls` del job), por su codigo."""
    if status is None or status < 200:
        return "sin_dato"
    if status >= 400:
        return "roto"
    if status >= 300:
        return "redireccion"
    return "ok"


def grupo_sonda(veredicto: str | None, status: int | None) -> str:
    """Un destino que no es del censo y se pidio con la sonda (decision 123)."""
    if veredicto in S.ROTOS:
        return "roto"
    if veredicto == S.OK:
        return "redireccion" if status is not None and 300 <= status < 400 else "ok"
    return "no_concluyente"


@dataclass
class Destino:
    grupo: str
    status: int | None = None
    url_id: int | None = None       # su ficha, si esta en el censo
    veredicto: str | None = None    # el de la sonda, si no esta en el censo


def destinos_conocidos(db: Session, job_id) -> dict[str, Destino]:
    """El estado de cada URL de la que el job sabe algo: sus `urls` y, para las
    que no son del censo, sus sondas. Por `job_id` en las dos tablas (sus
    indices); en un censo grande son decenas de miles de filas, no millones."""
    salida: dict[str, Destino] = {}
    for h, st, uid in db.execute(
            select(Url.url_hash, Url.status_code, Url.id).where(Url.job_id == job_id)):
        salida[h] = Destino(grupo_interno(st), st, uid)
    for h, ver, st in db.execute(
            select(RespuestaSondeada.url_hash, RespuestaSondeada.veredicto,
                   RespuestaSondeada.status_code)
            .where(RespuestaSondeada.job_id == job_id)):
        if h not in salida:
            salida[h] = Destino(grupo_sonda(ver, st), st, None, ver)
    return salida


def destinos_de(db: Session, job_id, hashes) -> dict[str, Destino]:
    """Lo mismo, solo para unos destinos (los de una pagina de resultados)."""
    lista = sorted({h for h in hashes if h})
    salida: dict[str, Destino] = {}
    for i in range(0, len(lista), 1000):
        tramo = lista[i:i + 1000]
        for h, st, uid in db.execute(
                select(Url.url_hash, Url.status_code, Url.id)
                .where(Url.job_id == job_id, Url.url_hash.in_(tramo))):
            salida[h] = Destino(grupo_interno(st), st, uid)
        for h, ver, st in db.execute(
                select(RespuestaSondeada.url_hash, RespuestaSondeada.veredicto,
                       RespuestaSondeada.status_code)
                .where(RespuestaSondeada.job_id == job_id,
                       RespuestaSondeada.url_hash.in_(tramo))):
            if h not in salida:
                salida[h] = Destino(grupo_sonda(ver, st), st, None, ver)
    return salida


def _destino_o_sin_dato(conocidos: dict[str, Destino], h: str) -> Destino:
    return conocidos.get(h) or Destino("sin_dato")


# ---------------------------------------------------------------------------
# Fuente 1: la tabla `links` (SQL)
# ---------------------------------------------------------------------------
def _sub_urls(job_id, cond):
    return select(Url.url_hash).where(Url.job_id == job_id, cond)


def _sub_sondas(job_id, cond):
    """Sondas del job que no tapa una URL del censo (el censo manda, como en
    `destinos_conocidos`)."""
    U2 = aliased(Url)
    R = RespuestaSondeada
    tapada = exists().where(U2.job_id == job_id, U2.url_hash == R.url_hash)
    return select(R.url_hash).where(R.job_id == job_id, ~tapada, cond)


def pred_estado_sql(job_id, grupo: str):
    """El estado del destino como condicion sobre `links`, igual que
    `grupo_interno`/`grupo_sonda`. Va al nivel de arriba del WHERE para que
    Postgres lo haga semi-join (IN) o anti-join (NOT EXISTS) por hash."""
    R = RespuestaSondeada
    st = Url.status_code
    if grupo == "sin_dato":
        U2 = aliased(Url)
        U3 = aliased(Url)
        R2 = aliased(RespuestaSondeada)
        con_codigo = exists().where(U2.job_id == job_id, U2.url_hash == Link.to_url_hash,
                                    U2.status_code >= 200)
        tapada = exists().where(U3.job_id == job_id, U3.url_hash == R2.url_hash)
        sondeada = exists().where(R2.job_id == job_id, R2.url_hash == Link.to_url_hash, ~tapada)
        return and_(~con_codigo, ~sondeada)
    if grupo == "roto":
        sub = _sub_urls(job_id, st >= 400).union_all(
            _sub_sondas(job_id, R.veredicto.in_(sorted(S.ROTOS))))
    elif grupo == "redireccion":
        sub = _sub_urls(job_id, and_(st >= 300, st < 400)).union_all(
            _sub_sondas(job_id, and_(R.veredicto == S.OK, R.status_code >= 300,
                                     R.status_code < 400)))
    elif grupo == "ok":
        sub = _sub_urls(job_id, and_(st >= 200, st < 300)).union_all(
            _sub_sondas(job_id, and_(R.veredicto == S.OK, or_(
                R.status_code.is_(None), R.status_code < 300, R.status_code >= 400))))
    elif grupo == "no_concluyente":
        sub = _sub_sondas(job_id, R.veredicto.notin_(sorted(S.ROTOS | {S.OK})))
    else:
        raise ValueError(grupo)
    return Link.to_url_hash.in_(sub)


def variantes_exactas(url: str) -> list[str]:
    """«Es exactamente» con y sin la barra final: quien pega
    `.../hotel-1` buscando el enlace a `.../hotel-1/` se quedaba con cero
    resultados (revision de UX del 25-oct, 2.8). Son dos destinos distintos
    y cada fila sigue diciendo el suyo; solo se buscan los dos."""
    url = url.strip()
    if not url:
        return []
    if not url.endswith("/"):
        return [url, url + "/"]
    otra = url[:-1]
    # `https://` sin mas no tiene variante util
    return [url] if otra.endswith(("/", ":")) else [url, otra]


def _like(texto: str) -> str:
    return "%" + texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def condiciones_sql(job_id, f: FiltrosEnlaces) -> list:
    conds: list = [Link.job_id == job_id]
    if f.destino_hash:
        conds.append(Link.to_url_hash == f.destino_hash)
    if f.destino:
        conds.append(Link.to_url.in_(variantes_exactas(f.destino)) if f.destino_exacto
                     else Link.to_url.ilike(_like(f.destino), escape="\\"))
    if f.origen:
        conds.append(Link.from_url_id.in_(
            select(Url.id).where(Url.job_id == job_id,
                                 Url.url.ilike(_like(f.origen), escape="\\"))))
    if f.ancla:
        patron = _like(f.ancla)
        conds.append(or_(Link.anchor_text.ilike(patron, escape="\\"),
                         Link.alt_text.ilike(patron, escape="\\")))
    if f.tipo == "interno":
        conds.append(or_(Link.is_internal.is_(True), Link.is_internal.is_(None)))
    elif f.tipo == "externo":
        conds.append(Link.is_internal.is_(False))
    if f.follow == "si":
        conds.append(or_(Link.follow.is_(True), Link.follow.is_(None)))
    elif f.follow == "no":
        conds.append(Link.follow.is_(False))
    if f.posicion == "sin":
        conds.append(Link.link_position.is_(None))
    elif f.posicion:
        conds.append(Link.link_position == f.posicion)
    if f.codigo is not None:
        R = RespuestaSondeada
        conds.append(Link.to_url_hash.in_(
            _sub_urls(job_id, Url.status_code == f.codigo).union_all(
                _sub_sondas(job_id, R.status_code == f.codigo))))
    if f.estado:
        conds.append(pred_estado_sql(job_id, f.estado))
    return conds


# ---------------------------------------------------------------------------
# Fuente 2: el Parquet de un censo archivado
# ---------------------------------------------------------------------------
# Filas por lote al recorrer el fichero; los tests lo bajan para cruzar lotes.
LOTE = 50_000


def comprobar_fichero(job) -> None:
    """Un censo archivado cuyo fichero no esta (la API sin el volumen montado,
    un fichero borrado a mano): se dice, en vez de devolver cero enlaces."""
    if not archivo_enlaces.ruta(job.id).exists():
        raise HTTPException(
            status_code=409,
            detail="Los enlaces de este censo están archivados y el fichero no está "
                   "disponible en este servidor: no se pueden listar.")


def _dataset(job_id):
    import pyarrow.dataset as ds

    return ds.dataset(archivo_enlaces.ruta(job_id), format="parquet")


def _ids_de_origen(db: Session, job_id, texto: str) -> list[int]:
    return [i for (i,) in db.execute(
        select(Url.id).where(Url.job_id == job_id,
                             Url.url.ilike(_like(texto), escape="\\")))]


def _contiene(campo: str, texto: str):
    import pyarrow.compute as pc

    return pc.match_substring(pc.coalesce(pc.field(campo), ""), texto, ignore_case=True)


def _hashes_por_grupo(conocidos: dict[str, Destino]) -> dict[str, list[str]]:
    salida: dict[str, list[str]] = {g: [] for g in GRUPOS}
    for h, d in conocidos.items():
        salida[d.grupo].append(h)
    return salida


def expr_estado_parquet(conocidos: dict[str, Destino], grupo: str):
    """El estado del destino como expresion de pyarrow: pertenecer a la lista
    de hashes de ese estado, o, para `sin_dato`, a ninguna de las otras (un
    destino que el job no conoce no tiene fila en `conocidos`)."""
    import pyarrow as pa
    import pyarrow.compute as pc

    por_grupo = _hashes_por_grupo(conocidos)
    campo = pc.field("to_url_hash")
    if grupo == "sin_dato":
        otros = [h for g in GRUPOS if g != "sin_dato" for h in por_grupo[g]]
        return ~campo.isin(pa.array(otros, pa.string()))
    return campo.isin(pa.array(por_grupo[grupo], pa.string()))


def expr_parquet(db: Session, job_id, f: FiltrosEnlaces,
                 conocidos: Callable[[], dict[str, Destino]]):
    """Los filtros como expresion de pyarrow (None = sin filtro). `conocidos`
    es perezoso: solo se lee de la BD si un filtro de estado lo necesita."""
    import pyarrow as pa
    import pyarrow.compute as pc

    partes = []
    if f.destino_hash:
        partes.append(pc.field("to_url_hash") == f.destino_hash)
    if f.destino:
        partes.append(pc.field("to_url").isin(pa.array(variantes_exactas(f.destino), pa.string()))
                      if f.destino_exacto else _contiene("to_url", f.destino))
    if f.origen:
        partes.append(pc.field("from_url_id").isin(
            pa.array(_ids_de_origen(db, job_id, f.origen), pa.int64())))
    if f.ancla:
        partes.append(_contiene("anchor_text", f.ancla) | _contiene("alt_text", f.ancla))
    if f.tipo == "interno":
        partes.append(pc.coalesce(pc.field("is_internal"), True))
    elif f.tipo == "externo":
        partes.append(~pc.coalesce(pc.field("is_internal"), True))
    if f.follow == "si":
        partes.append(pc.coalesce(pc.field("follow"), True))
    elif f.follow == "no":
        partes.append(~pc.coalesce(pc.field("follow"), True))
    if f.posicion == "sin":
        partes.append(pc.field("link_position").is_null())
    elif f.posicion:
        partes.append(pc.field("link_position") == f.posicion)
    if f.codigo is not None:
        hashes = [h for h, d in conocidos().items() if d.status == f.codigo]
        partes.append(pc.field("to_url_hash").isin(pa.array(hashes, pa.string())))
    if f.estado:
        partes.append(expr_estado_parquet(conocidos(), f.estado))
    if not partes:
        return None
    expr = partes[0]
    for p in partes[1:]:
        expr = expr & p
    return expr


def _y(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return a & b


# ---------------------------------------------------------------------------
# Cache de lo que se calcula sobre un Parquet (que no cambia hasta que se
# restaura): pasar de pagina no vuelve a recorrer el fichero.
# ---------------------------------------------------------------------------
_CACHE: "OrderedDict[tuple, Any]" = OrderedDict()
_CACHE_MAX = 16
_CACHE_LOCK = threading.Lock()


def _firma_fichero(job_id) -> tuple:
    st = os.stat(archivo_enlaces.ruta(job_id))
    return (str(job_id), st.st_mtime_ns, st.st_size)


def _cacheado(clave: tuple, calcular: Callable[[], Any], ttl: float | None = None) -> Any:
    """`ttl` None: hasta que cambie la clave (el Parquet, con su firma). Con
    `ttl`, la cifra caduca a los `ttl` segundos (los enlaces de un rastreo en
    marcha, que crecen)."""
    ahora = time.monotonic()
    with _CACHE_LOCK:
        if clave in _CACHE:
            hecho, valor = _CACHE[clave]
            if ttl is None or ahora - hecho < ttl:
                _CACHE.move_to_end(clave)
                return valor
            del _CACHE[clave]
    valor = calcular()
    with _CACHE_LOCK:
        _CACHE[clave] = (ahora, valor)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return valor


# ---------------------------------------------------------------------------
# Lo que se calcula sobre `links` (rastreos en curso)
# ---------------------------------------------------------------------------
# Revision de UX del 25-oct, 2.8: los conteos por estado y la agregacion por
# destino recorren TODOS los enlaces del job que pasan los filtros. Sobre un
# rastreo grande en marcha eso es trabajo en la BD de produccion mientras
# rastrea, asi que: (1) se guarda `TTL_VIVO` segundos —pasar de pagina o
# volver a la pestaña no repite la agregacion, y las cifras de un rastreo en
# marcha van con ese retraso como mucho—, y (2) cada calculo tiene un tope de
# `TOPE_VIVO_S`: pasado, se dice que no se puede agrupar ahora, en vez de
# dejar la consulta horas compitiendo con el rastreo. Nunca una cifra a medias.
TTL_VIVO = 30.0
TOPE_VIVO_S = 20.0
MENSAJE_TOPE = ("Este rastreo tiene demasiados enlaces para contarlos ahora mientras "
                "rastrea. Filtra por destino, página de origen o texto del enlace, "
                "o vuelve cuando acabe.")


def _es_cancelada(e: Exception) -> bool:
    """El `statement_timeout` de Postgres (SQLSTATE 57014)."""
    orig = getattr(e, "orig", None)
    return getattr(orig, "pgcode", None) == "57014"


def _en_vivo(db: Session, clave: tuple, calcular: Callable[[], Any]) -> Any:
    from sqlalchemy.exc import DBAPIError

    from shared.almacenamiento import _tope_de_consulta, es_postgres

    def con_tope():
        if es_postgres(db):
            _tope_de_consulta(db, TOPE_VIVO_S)
        try:
            return calcular()
        except DBAPIError as e:
            if not _es_cancelada(e):
                raise
            db.rollback()
            raise HTTPException(status_code=503, detail=MENSAJE_TOPE) from e

    return _cacheado(("vivo",) + clave, con_tope, ttl=TTL_VIVO)


def vaciar_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# Lista de enlaces
# ---------------------------------------------------------------------------
def _ventana(conteos: list[tuple[str, int]], offset: int, limite: int,
             leer: Callable[[str, int, int], list]) -> list:
    """Las filas `[offset, offset+limite)` de una lista ordenada por grupos,
    pidiendo a cada grupo solo el tramo que cae en la pagina."""
    salida: list = []
    for g, n in conteos:
        if len(salida) >= limite:
            break
        if offset >= n:
            offset -= n
            continue
        salida.extend(leer(g, offset, limite - len(salida)))
        offset = 0
    return salida


def _leer_parquet(job_id, expr, saltar: int, cuantas: int) -> list[dict]:
    salida: list[dict] = []
    for lote in _dataset(job_id).to_batches(columns=list(archivo_enlaces.COLUMNAS),
                                             filter=expr, batch_size=LOTE):
        n = lote.num_rows
        if saltar >= n:
            saltar -= n
            continue
        trozo = lote.slice(saltar, cuantas - len(salida)).to_pylist()
        saltar = 0
        salida.extend(trozo)
        if len(salida) >= cuantas:
            break
    return salida


def contar_por_estado_sql(db: Session, job_id, base: list) -> dict[str, int]:
    """Cuantos enlaces hay de cada estado del destino, en UNA pasada por
    `links` (antes, un COUNT por estado: cinco recorridos). Cada estado es el
    mismo predicado de `pred_estado_sql`; Postgres resuelve los `IN` como
    subplanes con tabla hash, sin unir con `urls`. Los estados no se solapan
    (el censo tapa a la sonda), asi que el orden del CASE no cambia nada."""
    grupo = case(*[(pred_estado_sql(job_id, g), g) for g in GRUPOS if g != "sin_dato"],
                 else_="sin_dato").label("grupo")
    sub = select(grupo).where(*base).subquery()
    cuentas = {g: int(n) for g, n in db.execute(
        select(sub.c.grupo, func.count()).group_by(sub.c.grupo))}
    return {g: cuentas.get(g, 0) for g in GRUPOS}


def listar_enlaces(db: Session, job, f: FiltrosEnlaces, orden: str,
                   page: int, page_size: int) -> dict[str, Any]:
    """Filas de enlaces filtradas. `orden=estado`: los de destino roto
    primero, luego redirecciones, no concluyentes, sin dato y los que van
    bien; dentro de cada estado, juntos los del mismo destino. `orden=destino`
    es el orden de antes (solo por destino), el del fichero y el del indice.

    Devuelve filas con la forma de un `Link` (dict o el objeto del ORM) y
    `por_estado`: cuantas filas hay de cada estado con los demas filtros (sin
    el de estado), para que la barra diga cuantas quedan en cada uno."""
    offset = (page - 1) * page_size
    archivado = archivo_enlaces.esta_archivado(job)
    _conocidos: list = []

    def conocidos() -> dict[str, Destino]:
        if not _conocidos:
            _conocidos.append(destinos_conocidos(db, job.id))
        return _conocidos[0]

    if archivado:
        comprobar_fichero(job)
        firma = _firma_fichero(job.id)
        base = expr_parquet(db, job.id, f.sin_estado(), conocidos)
        dataset = _dataset(job.id)

        if orden == "estado":
            def contar():
                return {g: dataset.count_rows(filter=_y(base, expr_estado_parquet(conocidos(), g)))
                        for g in GRUPOS}
            por_estado = _cacheado(("conteo",) + firma + f.sin_estado().clave(), contar)
            grupos = [f.estado] if f.estado else list(GRUPOS)
            conteos = [(g, por_estado[g]) for g in grupos]

            def leer(g, saltar, cuantas):
                return _leer_parquet(job.id, _y(base, expr_estado_parquet(conocidos(), g)),
                                     saltar, cuantas)
        else:
            expr = expr_parquet(db, job.id, f, conocidos)
            total = _cacheado(("total",) + firma + f.clave(),
                              lambda: dataset.count_rows(filter=expr) if expr is not None
                              else archivo_enlaces.contar(job.id))
            por_estado = None
            conteos = [("todos", total)]

            def leer(g, saltar, cuantas):
                return _leer_parquet(job.id, expr, saltar, cuantas)
        filas = _ventana(conteos, offset, page_size, leer)
    else:
        base = condiciones_sql(job.id, f.sin_estado())

        def consulta(extra):
            return db.query(Link).filter(*base, *extra)

        if orden == "estado":
            por_estado = _en_vivo(db, ("conteo", str(job.id)) + f.sin_estado().clave(),
                                  lambda: contar_por_estado_sql(db, job.id, base))
            grupos = [f.estado] if f.estado else list(GRUPOS)
            conteos = [(g, por_estado[g]) for g in grupos]

            def leer(g, saltar, cuantas):
                return (consulta([pred_estado_sql(job.id, g)])
                        .options(joinedload(Link.from_url_rel))
                        .order_by(Link.to_url_hash, Link.id)
                        .offset(saltar).limit(cuantas).all())
        else:
            extra = [pred_estado_sql(job.id, f.estado)] if f.estado else []
            por_estado = None
            conteos = [("todos", _en_vivo(db, ("total", str(job.id)) + f.clave(),
                                          lambda: consulta(extra).count()))]

            def leer(g, saltar, cuantas):
                # (to_url_hash, id) y no id: con `ORDER BY id` Postgres recorre
                # la clave primaria de TODA la tabla (2 min en Lopesan); asi
                # sirve `ix_links_to_hash (job_id, to_url_hash)`.
                return (consulta(extra).options(joinedload(Link.from_url_rel))
                        .order_by(Link.to_url_hash, Link.id)
                        .offset(saltar).limit(cuantas).all())
        filas = _ventana(conteos, offset, page_size, leer)

    total = sum(n for _, n in conteos)
    return {"filas": filas, "total": total, "por_estado": por_estado,
            "archivado": archivado}


# ---------------------------------------------------------------------------
# Vista por destino
# ---------------------------------------------------------------------------
# (hash, url, interno, paginas, enlaces, apariciones, nofollow)
FilaDestino = tuple


def _agregar_sql(db: Session, job_id, f: FiltrosEnlaces) -> list[FilaDestino]:
    interno = case((Link.is_internal.is_(False), 0), else_=1)
    nofollow = case((Link.follow.is_(False), 1), else_=0)
    q = (select(Link.to_url_hash, func.min(Link.to_url), func.max(interno),
                func.count(func.distinct(Link.from_url_id)), func.count(),
                func.coalesce(func.sum(Link.repeticiones), 0), func.sum(nofollow))
         .where(*condiciones_sql(job_id, f))
         .group_by(Link.to_url_hash))
    return [(h, u, bool(i), int(p), int(n), int(a), int(nf or 0))
            for h, u, i, p, n, a, nf in db.execute(q)]


def _agregar_tabla(t):
    import pyarrow.compute as pc

    t = t.append_column("interno_n", pc.cast(pc.coalesce(t["is_internal"], True), "int8"))
    t = t.append_column("nofollow_n", pc.cast(pc.invert(pc.coalesce(t["follow"], True)), "int32"))
    return t.group_by("to_url_hash", use_threads=False).aggregate([
        ("to_url", "min"), ("interno_n", "max"), ("from_url_id", "count_distinct"),
        ("from_url_id", "count"), ("repeticiones", "sum"), ("nofollow_n", "sum"),
    ])


def _agregar_parquet(job_id, expr) -> list[FilaDestino]:
    """Agrupa por destino recorriendo el fichero por lotes, sin cargarlo
    entero. El fichero va ordenado por destino, asi que todas las filas de un
    destino son contiguas: las del ULTIMO destino de cada lote se guardan y se
    suman al siguiente, y cada destino se agrega una sola vez (las paginas
    distintas no se pueden sumar entre lotes)."""
    import pyarrow as pa
    import pyarrow.compute as pc

    columnas = ["to_url_hash", "to_url", "is_internal", "from_url_id", "repeticiones", "follow"]
    partes = []
    resto = None
    for lote in _dataset(job_id).to_batches(columns=columnas, filter=expr, batch_size=LOTE):
        if lote.num_rows == 0:
            continue
        t = pa.Table.from_batches([lote])
        if resto is not None:
            t = pa.concat_tables([resto, t])
        ultimo = t["to_url_hash"][-1]
        es_ultimo = pc.equal(t["to_url_hash"], ultimo)
        resto = t.filter(es_ultimo)
        listo = t.filter(pc.invert(es_ultimo))
        if listo.num_rows:
            partes.append(_agregar_tabla(listo))
    if resto is not None and resto.num_rows:
        partes.append(_agregar_tabla(resto))
    salida: list[FilaDestino] = []
    for p in partes:
        cols = [p[c].to_pylist() for c in (
            "to_url_hash", "to_url_min", "interno_n_max", "from_url_id_count_distinct",
            "from_url_id_count", "repeticiones_sum", "nofollow_n_sum")]
        for h, u, i, pg, n, a, nf in zip(*cols):
            salida.append((h, u, bool(i), int(pg), int(n), int(a or 0), int(nf or 0)))
    return salida


def agregados(db: Session, job, f: FiltrosEnlaces) -> list[FilaDestino]:
    """Un agregado por destino con los filtros de enlace (sin los de estado y
    codigo, que son del destino y se aplican despues)."""
    base = replace(f, estado="", codigo=None)
    if archivo_enlaces.esta_archivado(job):
        comprobar_fichero(job)
        clave = ("destinos",) + _firma_fichero(job.id) + base.clave()
        return _cacheado(clave, lambda: _agregar_parquet(
            job.id, expr_parquet(db, job.id, base, lambda: {})))
    return _en_vivo(db, ("destinos", str(job.id)) + base.clave(),
                    lambda: _agregar_sql(db, job.id, base))


ORDENES_DESTINO = ("estado", "paginas", "url")


def listar_destinos(db: Session, job, f: FiltrosEnlaces, orden: str,
                    page: int, page_size: int) -> dict[str, Any]:
    filas = agregados(db, job, f)
    conocidos = destinos_conocidos(db, job.id)
    clasificadas = []
    por_estado = {g: 0 for g in GRUPOS}
    for fila in filas:
        d = _destino_o_sin_dato(conocidos, fila[0])
        if f.codigo is not None and d.status != f.codigo:
            continue
        por_estado[d.grupo] += 1
        if f.estado and d.grupo != f.estado:
            continue
        clasificadas.append((fila, d))
    if orden == "paginas":
        clasificadas.sort(key=lambda x: (-x[0][3], -x[0][5], x[0][1] or ""))
    elif orden == "url":
        clasificadas.sort(key=lambda x: x[0][1] or "")
    else:
        clasificadas.sort(key=lambda x: (RANGO[x[1].grupo], -x[0][3], -x[0][5], x[0][1] or ""))
    total = len(clasificadas)
    pagina = clasificadas[(page - 1) * page_size: page * page_size]
    anclas = anclas_de(db, job, f, [fila[0] for fila, _ in pagina])
    items = []
    for (h, u, interno, paginas, enlaces, apariciones, nofollow), d in pagina:
        items.append({
            "to_url": u, "to_url_hash": h, "is_internal": interno,
            "paginas": paginas, "enlaces": enlaces, "apariciones": apariciones,
            "nofollow": nofollow, "anclas": anclas.get(h, []),
            "estado_destino": d.grupo, "status_destino": d.status,
            "veredicto_sonda": d.veredicto, "url_id": d.url_id,
        })
    return {"items": items, "total": total, "por_estado": por_estado,
            "archivado": archivo_enlaces.esta_archivado(job)}


def anclas_de(db: Session, job, f: FiltrosEnlaces, hashes: list[str],
              cuantas: int = 3) -> dict[str, list[dict]]:
    """Las anclas mas usadas de cada destino (por apariciones), con los mismos
    filtros de enlace. El texto es el del enlace o, si no tiene, el alt de su
    imagen; sin ninguno de los dos, «» (la interfaz dice «sin texto»)."""
    if not hashes:
        return {}
    base = replace(f, estado="", codigo=None)
    cuenta: dict[str, dict[str, int]] = {}
    if archivo_enlaces.esta_archivado(job):
        import pyarrow as pa
        import pyarrow.compute as pc

        expr = _y(expr_parquet(db, job.id, base, lambda: {}),
                  pc.field("to_url_hash").isin(pa.array(hashes, pa.string())))
        for lote in _dataset(job.id).to_batches(
                columns=["to_url_hash", "anchor_text", "alt_text", "repeticiones"],
                filter=expr, batch_size=LOTE):
            for h, an, alt, rep in zip(*(lote.column(i).to_pylist() for i in range(4))):
                texto = (an or alt or "").strip()
                d = cuenta.setdefault(h, {})
                d[texto] = d.get(texto, 0) + (rep or 1)
    else:
        q = (select(Link.to_url_hash, Link.anchor_text, Link.alt_text,
                    func.coalesce(func.sum(Link.repeticiones), 0))
             .where(*condiciones_sql(job.id, base), Link.to_url_hash.in_(hashes))
             .group_by(Link.to_url_hash, Link.anchor_text, Link.alt_text))
        for h, an, alt, n in db.execute(q):
            texto = (an or alt or "").strip()
            d = cuenta.setdefault(h, {})
            d[texto] = d.get(texto, 0) + int(n)
    return {h: [{"texto": t, "veces": n} for t, n in
                sorted(d.items(), key=lambda x: (-x[1], x[0]))[:cuantas]]
            for h, d in cuenta.items()}


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------
def resumen(db: Session, job) -> dict[str, Any]:
    """Cifras de la pestaña: enlaces y destinos internos y externos, y los
    destinos externos por estado. Es lo que pone «Externas» en la barra del
    Explorador (V25): el censo no guarda URLs externas —no se rastrean— pero
    sus enlaces si, y sin esto la barra decia «0»."""
    filas = agregados(db, job, FiltrosEnlaces())
    conocidos = destinos_conocidos(db, job.id)
    out: dict[str, Any] = {
        "enlaces": 0, "apariciones": 0,
        "internos": {"enlaces": 0, "apariciones": 0, "destinos": 0},
        "externos": {"enlaces": 0, "apariciones": 0, "destinos": 0},
        "destinos_por_estado": {
            "internos": {g: 0 for g in GRUPOS}, "externos": {g: 0 for g in GRUPOS}},
    }
    for h, _u, interno, _p, enlaces, apariciones, _nf in filas:
        lado = "internos" if interno else "externos"
        out["enlaces"] += enlaces
        out["apariciones"] += apariciones
        out[lado]["enlaces"] += enlaces
        out[lado]["apariciones"] += apariciones
        out[lado]["destinos"] += 1
        out["destinos_por_estado"][lado][_destino_o_sin_dato(conocidos, h).grupo] += 1
    out["destinos"] = out["internos"]["destinos"] + out["externos"]["destinos"]
    out["archivado"] = archivo_enlaces.esta_archivado(job)
    out["sondas"] = db.query(RespuestaSondeada.id).filter(
        RespuestaSondeada.job_id == job.id).first() is not None
    return out
