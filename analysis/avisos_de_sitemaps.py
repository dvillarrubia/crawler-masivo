"""Avisos de los ficheros de sitemap y de sus entradas (decision 126).

Tres incidencias, de lo que el spider dejo en `jobs.sitemaps` (un resumen por
fichero leido) y en `sitemap_entries` (una fila por fichero y URL interna):

- ``sitemap_supera_limite`` (warning): un fichero con mas de 50.000 entradas o
  mas de 50 MB sin comprimir, los limites que Google documenta para un
  sitemap y para un indice. **Uno por fichero**: es el fichero lo que hay que
  partir, no cada URL suya.
- ``lastmod_futuro`` (warning): entradas cuyo ``lastmod`` es posterior a la
  hora a la que se LEYO el fichero mas un dia de margen. **Uno por fichero**,
  con cuantas y ejemplos: el lastmod lo escribe el generador, y una fecha
  futura es un fallo de la plantilla del sitemap, no de cada pagina.
- ``en_varios_sitemaps`` (info): URLs declaradas en mas de un fichero. **Uno
  por combinacion de ficheros**, con `paginas_afectadas` y ejemplos: que el
  sitemap de paginas y el «sitemap completo» repitan 3.000 URLs es UN hallazgo
  del generador (decisiones 53, 64 y 73).

Ninguna tiene pagina propia —un fichero de sitemap no es una URL del censo—:
se anclan en la raiz del host del fichero si se rastreo, y si no en la URL de
id mas bajo de ese host o del censo, con `group_key` del fichero (o de la
combinacion) para que la comparacion entre censos identifique el hallazgo
(decision 88). Lo de `en_varios_sitemaps` se ancla en la URL de id mas bajo de
las afectadas que este en el censo.

No lee `links` (decision 95). Sin `jobs.sitemaps` (rastreo anterior) o sin la
tabla no se emite nada: «no se sabe» no es «sitemap limpio».
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select

from shared import sitemaps as S

logger = logging.getLogger(__name__)

TIPOS = ("sitemap_supera_limite", "lastmod_futuro", "en_varios_sitemaps")

MUESTRA = 5
LOTE = 2000
# Combinaciones de ficheros distintas que se avisan como mucho. Lo normal son
# una o dos (el indice por secciones y un sitemap «completo»); pasado esto es
# un generador que reparte las URLs al azar, y se dice en el log.
MAX_COMBINACIONES = 200


def _clave(valor: str | None) -> str | None:
    from analysis.analyzer import _clave_de_grupo

    return _clave_de_grupo(valor)


def _leido(fichero: dict, respaldo: datetime) -> datetime:
    try:
        instante = datetime.fromisoformat(fichero["leido_at"])
    except (KeyError, TypeError, ValueError):
        return respaldo
    return instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)


class _Anclas:
    """La URL del censo en la que se cuelga un aviso de un fichero."""

    def __init__(self, az):
        self.az = az
        self._cache: dict[str, int | None] = {}
        self._minimo: int | None = None

    def de_fichero(self, url: str) -> int | None:
        from shared.models import Url

        p = urlparse(url)
        clave = f"{p.scheme}://{p.hostname or ''}"
        if clave not in self._cache:
            filtro = (Url.job_id == self.az.job_id, Url.host == (p.hostname or ""))
            ancla = self.az.session.execute(
                select(Url.id).where(*filtro, Url.path.in_(["/", ""]))
                .order_by((Url.scheme != p.scheme), Url.id).limit(1)).scalar()
            if ancla is None:
                ancla = self.az.session.execute(
                    select(func.min(Url.id)).where(*filtro)).scalar()
            self._cache[clave] = ancla
        if self._cache[clave] is not None:
            return self._cache[clave]
        if self._minimo is None:
            self._minimo = self.az.session.execute(
                select(func.min(Url.id)).where(Url.job_id == self.az.job_id)).scalar()
        return self._minimo


def _hay_tabla(az) -> bool:
    from shared.models import SitemapEntry

    try:
        with az.session.begin_nested():
            az.session.execute(select(SitemapEntry.id).limit(1)).first()
        return True
    except Exception:  # noqa: BLE001 -- base sin migrar
        return False


def avisar(az) -> dict[str, Any] | None:
    """Emite los tres tipos y devuelve un resumen (para el log y los tests)."""
    from shared.models import Job

    job = az.session.get(Job, az.job_id)
    resumen_spider = getattr(job, "sitemaps", None) if job is not None else None
    if not resumen_spider:
        return None
    ficheros = [f for f in resumen_spider.get("ficheros") or [] if f.get("status") == 200]
    respaldo = (getattr(job, "started_at", None) or getattr(job, "created_at", None)
                or datetime.now(timezone.utc))
    if respaldo.tzinfo is None:
        respaldo = respaldo.replace(tzinfo=timezone.utc)
    anclas = _Anclas(az)
    resumen: dict[str, Any] = {"ficheros": len(ficheros)}

    resumen["sitemap_supera_limite"] = _supera_limite(az, ficheros, anclas)
    if _hay_tabla(az):
        recortadas = bool(resumen_spider.get("entradas_recortadas"))
        resumen["lastmod_futuro"] = _lastmod_futuro(az, ficheros, respaldo, anclas)
        resumen["en_varios_sitemaps"] = _en_varios(az, anclas, recortadas)
    az._flush_issues()
    logger.info("Sitemaps: %s", resumen)
    return resumen


def _supera_limite(az, ficheros: list[dict], anclas: _Anclas) -> int:
    n = 0
    for f in ficheros:
        motivos = S.motivos_de_limite(f.get("declaradas"), f.get("bytes"))
        if not motivos:
            continue
        ancla = anclas.de_fichero(f["url"])
        if ancla is None:
            continue
        n += 1
        az._add_issue(ancla, "sitemap_supera_limite", "warning", {
            "fichero": f["url"],
            "formato": f.get("formato"),
            "motivos": motivos,
            "declaradas": f.get("declaradas"),
            "bytes": f.get("bytes"),
            # Si el gzip se corto, `bytes` es un minimo: pasa del tope y no se leyo.
            "cortado": bool(f.get("cortado")),
            "limite_urls": S.LIMITE_URLS,
            "limite_bytes": S.LIMITE_BYTES,
        }, group_key=_clave(f["url"]))
    return n


def _lastmod_futuro(az, ficheros: list[dict], respaldo: datetime, anclas: _Anclas) -> int:
    from shared.models import SitemapEntry as E

    leidos = {f["url"]: _leido(f, respaldo) for f in ficheros}
    por_fichero: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "con_lastmod": 0, "ilegibles": 0, "ejemplos": [], "maximo": None})
    filas = az.session.execute(
        select(E.fichero, E.url, E.lastmod)
        .where(E.job_id == az.job_id, E.lastmod.isnot(None))
        .order_by(E.id)
        .execution_options(yield_per=LOTE))
    for fichero, url, lastmod in filas:
        d = por_fichero[fichero]
        d["con_lastmod"] += 1
        instante = S.leer_lastmod(lastmod)
        if instante is None:
            d["ilegibles"] += 1
            continue
        leido = leidos.get(fichero, respaldo)
        if not S.es_futuro(instante, leido):
            continue
        d["n"] += 1
        if len(d["ejemplos"]) < MUESTRA:
            d["ejemplos"].append({"url": url, "lastmod": lastmod})
        if d["maximo"] is None or instante > d["maximo"][0]:
            d["maximo"] = (instante, lastmod)
    emitidos = 0
    for fichero, d in sorted(por_fichero.items()):
        if not d["n"]:
            continue
        ancla = anclas.de_fichero(fichero)
        if ancla is None:
            continue
        emitidos += 1
        az._add_issue(ancla, "lastmod_futuro", "warning", {
            "fichero": fichero,
            "entradas_futuras": d["n"],
            "entradas_con_lastmod": d["con_lastmod"],
            "lastmod_ilegibles": d["ilegibles"],
            "lastmod_maximo": d["maximo"][1],
            "leido_at": leidos.get(fichero, respaldo).isoformat(),
            "margen_horas": int(S.MARGEN_LASTMOD.total_seconds() // 3600),
            "ejemplos": d["ejemplos"],
        }, group_key=_clave(fichero))
    return emitidos


def _en_varios(az, anclas: _Anclas, recortadas: bool) -> int:
    from shared.models import SitemapEntry as E, Url

    repetidas = [h for (h,) in az.session.execute(
        select(E.url_hash).where(E.job_id == az.job_id)
        .group_by(E.url_hash).having(func.count(func.distinct(E.fichero)) > 1))]
    if not repetidas:
        return 0
    por_url: dict[str, dict[str, Any]] = {}
    for i in range(0, len(repetidas), LOTE):
        for url_hash, url, fichero, lastmod in az.session.execute(
                select(E.url_hash, E.url, E.fichero, E.lastmod)
                .where(E.job_id == az.job_id, E.url_hash.in_(repetidas[i:i + LOTE]))):
            d = por_url.setdefault(url_hash, {"url": url, "ficheros": set(), "lastmods": set()})
            d["ficheros"].add(fichero)
            d["lastmods"].add(lastmod)
    combinaciones: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for url_hash, d in por_url.items():
        combinaciones[tuple(sorted(d["ficheros"]))].append(url_hash)

    ids: dict[str, int] = {}
    hashes = sorted(por_url)
    for i in range(0, len(hashes), LOTE):
        ids.update({h: i_ for h, i_ in az.session.execute(
            select(Url.url_hash, Url.id).where(Url.job_id == az.job_id,
                                               Url.url_hash.in_(hashes[i:i + LOTE])))})
    orden = sorted(combinaciones.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    if len(orden) > MAX_COMBINACIONES:
        logger.warning("Sitemaps: %d combinaciones de ficheros con URLs repetidas; se "
                       "avisan las %d mayores", len(orden), MAX_COMBINACIONES)
    emitidos = 0
    for ficheros, miembros in orden[:MAX_COMBINACIONES]:
        en_censo = sorted(ids[h] for h in miembros if h in ids)
        ancla = en_censo[0] if en_censo else anclas.de_fichero(ficheros[0])
        if ancla is None:
            continue
        # Por url de los afectados, para que la muestra no dependa del orden de
        # lectura (y salga igual en dos censos iguales).
        urls = sorted(por_url[h]["url"] for h in miembros)
        distintos = sum(1 for h in miembros if len(por_url[h]["lastmods"]) > 1)
        emitidos += 1
        az._add_issue(ancla, "en_varios_sitemaps", "info", {
            "ficheros": list(ficheros),
            "paginas_afectadas": len(miembros),
            "en_el_censo": len(en_censo),
            "ejemplos": urls[:MUESTRA],
            # Con lastmod distinto segun el fichero: el sitio se contradice.
            "lastmod_distinto": distintos,
            # Con el tope de entradas alcanzado faltan ficheros: la cuenta es
            # un minimo, no el total.
            "entradas_recortadas": recortadas,
        }, group_key=_clave("|".join(ficheros)))
    return emitidos
