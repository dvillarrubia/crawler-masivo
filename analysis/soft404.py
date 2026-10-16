"""Soft 404 determinista en el analisis (decision 105).

Las reglas puras viven en `shared/soft404.py`; aqui se cruzan con el censo y
se emiten las incidencias. Se juzgan las paginas 200, HTML e internas.

Dos tipos, para que lo dudoso no se lea como afirmado:

- `soft_404`: seguro. La severidad es la consecuencia (decision 64):
  `error` si es indexable y esta en el sitemap (Search Console lo archivaba
  asi: «La URL enviada parece un soft 404»); `warning` si es indexable; `info`
  si ya esta fuera del indice por noindex o canonical — Google no la va a
  indexar de todos modos, y lo que queda es el enlace roto disfrazado: el
  sitio enlaza una pagina de error que responde 200. Medido en www.uoc.edu:
  las 25 paginas de error con 200 del censo eran todas canonicalizadas o
  noindex; quitarlas del analisis las escondia.
- `soft_404_candidato`: `info`, con `details.candidato_llm = true` y los
  motivos. Es lo que la fase con modelo (#11) tiene que mirar; aqui no se
  afirma.

Un aviso por hallazgo (decision 36): una pagina con varias senales lleva UNA
incidencia con todas; la seguras tapan a las dudosas; y una pagina que ya es
`soft_404` no lleva ademas `low_word_count`, que es el mismo hallazgo visto
peor (la pagina tiene poco texto porque es una pagina de error).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import String, cast, delete, func, or_, select, update

from analysis import near_duplicates as nd
from shared import soft404 as s4
from shared.descarga_cruda import TITULOS_DE_BLOQUEO
from shared.plantillas import firma_de_ruta

logger = logging.getLogger(__name__)

# Con menos coincidencias que esto, una huella no se tacha de «plantilla»:
# dos paginas de error en un sitio de tres paginas son dos paginas de error.
MIN_COINCIDENCIAS_AMBIGUAS = 3
MUESTRA = 20
_PREFIJO_TEXTO = 1500


def _umbral(t: dict, clave: str, defecto):
    v = t.get(clave)
    return defecto if v is None else v


def _margen(palabras: int) -> int:
    """Diferencia de palabras con la que una Jaccard de 0,9 aun es posible.

    Con un 10% de diferencia de tamano la Jaccard ya no llega a 0,9; el margen
    es mas ancho a proposito (25% + 5) para no perder nada por el eco de la URL.
    """
    return max(10, int(0.25 * palabras) + 5)


def _cerca(a: int, b: int) -> bool:
    return abs(a - b) <= _margen(max(a, b))


def _sondas_utiles(sondas: list[dict]) -> list[dict]:
    """Las sondas cuya respuesta es la pagina de error del sitio.

    200 (el soft 404 de serie) o 404/410 con cuerpo (la pagina de error que el
    sitio sirve bien, y que otras URLs pueden servir con 200). Una sonda que
    redirige no deja huella: el destino es una pagina real —muchas veces la
    home— y tomarla por «la pagina de error» marcaria la home.
    """
    utiles = []
    for s in sondas or []:
        if s.get("redirige_a") or s.get("error"):
            continue
        if s.get("status") not in (200, 404, 410):
            continue
        if not (s.get("texto") or s.get("titulo")):
            continue
        # Un muro de WAF con 200 no es la pagina de error del sitio: con su
        # huella, cada pagina bloqueada del censo saldria como soft 404, y eso
        # es un bloqueo (decisiones 62 y 82), no un hallazgo del sitio.
        titulo = (s.get("titulo") or "").strip().lower()
        if any(t in titulo for t in TITULOS_DE_BLOQUEO):
            continue
        utiles.append(s)
    return utiles


def analizar(an) -> dict[str, Any]:
    """Emite las incidencias de soft 404 del job de `an` y devuelve el resumen."""
    from shared.models import HtmlMeta, Heading, Issue, Job, PageContent, StructuredData, Url

    session = an.session
    job = session.query(Job).filter(Job.id == an.job_id).one_or_none()
    t = ((job.config or {}).get("analysis_thresholds") or {}) if job else {}
    similitud_min = float(_umbral(t, "soft404_similitud", nd.UMBRAL_SIMILITUD))
    min_palabras = int(_umbral(t, "soft404_min_palabras_igualdad", 5))
    max_prop = float(_umbral(t, "soft404_max_proporcion_huella", 0.5))
    min_word_count = an.min_word_count

    previo = (getattr(job, "soft404", None) or {}) if job else {}
    sondas = list(previo.get("sondas") or []) if isinstance(previo, dict) else []

    base = (
        Url.job_id == an.job_id, Url.is_html.is_(True), Url.is_internal.is_(True),
        Url.status_code == 200,
    )

    # -- Pasada 1: titulo, h1, principio del contenido ------------------------
    filas = session.execute(
        select(Url.id, Url.url, Url.host, Url.path, Url.content_word_count, Url.in_sitemap,
               Url.indexable,
               HtmlMeta.title, func.substr(PageContent.content_text, 1, _PREFIJO_TEXTO))
        .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
        .outerjoin(PageContent, PageContent.url_id == Url.id)
        .where(*base)
    ).all()
    if not filas:
        return _guardar(session, job, {"paginas_juzgadas": 0, "sondas": len(sondas)})

    ids = {f[0] for f in filas}
    h1s: dict[int, str] = {}
    sub = select(Url.id).where(*base).scalar_subquery()
    for url_id, texto in session.execute(
        select(Heading.url_id, Heading.text)
        .where(Heading.url_id.in_(sub), Heading.tag == "h1",
               or_(Heading.oculto.is_(None), Heading.oculto.is_(False)))
        .order_by(Heading.url_id, Heading.position)
    ):
        if url_id not in h1s and texto:
            h1s[url_id] = texto

    info: dict[int, dict] = {}
    seguras: dict[int, list[dict]] = defaultdict(list)
    dudosas: dict[int, list[dict]] = defaultdict(list)
    for url_id, url, host, path, cwc, in_sitemap, indexable, titulo, prefijo in filas:
        info[url_id] = {"url": url, "host": (host or "").lower(), "path": path or "/",
                        "cwc": cwc, "in_sitemap": in_sitemap, "titulo": titulo,
                        "indexable": bool(indexable)}
        h1 = h1s.get(url_id)
        r = s4.senal_titulo(titulo, h1)
        if r is not None:
            fuerza, campo, frase = r
            senal = {"senal": "titulo_de_error", "campo": campo, "frase": frase,
                     "valor": (h1 if campo == "h1" else titulo or "")[:200]}
            # Una pagina de error no tiene contenido propio. Con un titular de
            # error y `min_word_count` palabras o mas, es un articulo que habla
            # de un error, no un error: candidato. Sin recuento (censos
            # anteriores a la columna), cuentan las del principio guardado.
            palabras = cwc if cwc is not None else len((prefijo or "").split())
            if fuerza == "seguro" and palabras >= min_word_count:
                fuerza = "dudoso"
                senal["content_word_count"] = palabras
            (seguras if fuerza == "seguro" else dudosas)[url_id].append(senal)
        frase = s4.frase_sin_resultados(titulo, h1, prefijo)
        if frase:
            if s4.es_busqueda(url):
                seguras[url_id].append({"senal": "busqueda_sin_resultados", "frase": frase})
            elif cwc is not None and cwc < min_word_count:
                # Un listado que dice «no hay productos» sin ser el buscador:
                # una categoria vacia es un soft 404 de manual, pero la frase
                # tambien sale en un filtro sin resultados dentro de una pagina
                # con contenido. Con poco texto propio, candidato; sin eso, nada.
                dudosas[url_id].append({"senal": "listado_vacio", "frase": frase,
                                        "content_word_count": cwc})

    # -- Pasada 2: la huella de las sondas -------------------------------------
    utiles = _sondas_utiles(sondas)
    por_host: dict[str, list[dict]] = defaultdict(list)
    for s in utiles:
        s = dict(s)
        s["_huella"] = s4.huella(s.get("texto"), s.get("url"), s.get("titulo"))
        s["_firma"] = nd.firma(s["_huella"].texto)
        s["_coinciden"] = []
        s["_portada"] = False
        por_host[(s.get("host") or "").lower()].append(s)

    alcance_plantilla: dict[tuple[str, str], int] = defaultdict(int)
    alcance_host: dict[str, int] = defaultdict(int)
    for d in info.values():
        d["plantilla"] = firma_de_ruta(d["path"])
        alcance_host[d["host"]] += 1
        alcance_plantilla[(d["host"], d["plantilla"])] += 1

    if por_host:
        # Solo se lee el texto entero de las paginas cuyo tamano puede casar
        # con alguna sonda: una Jaccard de 0,9 no es posible con un 25% de
        # diferencia de palabras, y la igualdad exacta menos. Sin recuento
        # (rastreos anteriores a la columna) se leen todas.
        rangos = []
        for host, lista in por_host.items():
            for s in lista:
                p = s["_huella"].palabras
                margen = _margen(p)
                rangos.append((host, max(0, p - margen), p + margen))
        condiciones = [Url.content_word_count.is_(None)] + [
            Url.content_word_count.between(lo, hi) for _, lo, hi in rangos]
        consulta = (
            select(Url.id, PageContent.content_text)
            .join(PageContent, PageContent.url_id == Url.id)
            .where(*base, func.lower(Url.host).in_(list(por_host)), or_(*condiciones))
            .execution_options(yield_per=1000)
        )
        for url_id, texto in session.execute(consulta):
            d = info.get(url_id)
            if d is None:
                continue
            candidatas = [s for s in por_host.get(d["host"], [])
                          if s.get("plantilla") in (None, d["plantilla"])]
            if not candidatas:
                continue
            h = s4.huella(texto, d["url"], d["titulo"])
            firma_pagina = None
            for s in candidatas:
                metodo = s4.misma_huella(s["_huella"], h, min_palabras)
                parecido = 1.0 if metodo else None
                if metodo is None and s["_firma"] is not None and _cerca(s["_huella"].palabras, h.palabras):
                    if firma_pagina is None:
                        firma_pagina = nd.firma(h.texto)
                    if firma_pagina is not None:
                        v = nd.similitud(s["_firma"], firma_pagina)
                        if v >= similitud_min:
                            metodo, parecido = "minhash", round(v, 3)
                if metodo is None:
                    continue
                if s4.es_portada(d["path"]):
                    # La sonda devuelve lo mismo que la portada: el sitio sirve
                    # la home para lo que no existe. La home no es la pagina de
                    # error, y las demas que casan son alias o URLs rotas: no
                    # se distingue sin mirar.
                    s["_portada"] = True
                    continue
                s["_coinciden"].append((url_id, metodo, parecido))

    resumen_sondas = []
    candidatos_agrupados = []
    for host, lista in por_host.items():
        for s in lista:
            coinciden = s["_coinciden"]
            alcance = (alcance_host[host] if s.get("plantilla") is None
                       else alcance_plantilla[(host, s["plantilla"])])
            proporcion = len(coinciden) / alcance if alcance else 0.0
            ambigua = s["_portada"] or (
                len(coinciden) >= MIN_COINCIDENCIAS_AMBIGUAS and proporcion > max_prop)
            resumen_sondas.append({
                "url": s.get("url"), "status": s.get("status"), "plantilla": s.get("plantilla"),
                "coincidencias": len(coinciden), "alcance": alcance,
                "proporcion": round(proporcion, 3), "ambigua": ambigua,
                "devuelve_portada": s["_portada"],
            })
            if not coinciden:
                continue
            if ambigua:
                candidatos_agrupados.append((s, coinciden, alcance, proporcion))
                continue
            for url_id, metodo, parecido in coinciden:
                seguras[url_id].append({
                    "senal": "huella_de_la_sonda", "metodo": metodo, "similitud": parecido,
                    "sonda": s.get("url"), "sonda_status": s.get("status"),
                    "sonda_origen": s.get("origen"),
                })

    # -- Productos descatalogados ------------------------------------------------
    # Google NO trata un producto agotado como soft 404 por si solo: pide
    # mantener la pagina si es util y marcar la disponibilidad. Agotado
    # (OutOfStock, SoldOut) no se emite. Descatalogado (Discontinued) es la
    # pagina de algo que ya no volvera, y Google si puede acabar tratandola
    # como soft 404 si no ofrece nada mas: candidato, nunca afirmado.
    disponibilidad: dict[int, set[str]] = defaultdict(set)
    for url_id, raw in session.execute(
        select(StructuredData.url_id, StructuredData.raw)
        .where(StructuredData.url_id.in_(sub),
               or_(cast(StructuredData.raw, String).ilike("%discontinued%"),
                   cast(StructuredData.raw, String).ilike("%outofstock%"),
                   cast(StructuredData.raw, String).ilike("%soldout%")))
    ):
        disponibilidad[url_id] |= s4.disponibilidades(raw)
    agotados = 0
    for url_id, valores in disponibilidad.items():
        if url_id not in ids or not valores:
            continue
        if valores <= {"discontinued"}:
            dudosas[url_id].append({"senal": "producto_descatalogado",
                                    "disponibilidad": sorted(valores)})
        elif valores <= {"outofstock", "soldout", "discontinued"}:
            agotados += 1

    # -- Emitir ---------------------------------------------------------------------
    cuenta: dict[str, int] = defaultdict(int)
    for url_id, lista in seguras.items():
        d = info[url_id]
        for x in lista:
            cuenta[x["senal"]] += 1
        severidad = ("info" if not d["indexable"]
                     else "error" if d["in_sitemap"] else "warning")
        an._add_issue(url_id, "soft_404", severidad,
                      {"senales": lista, "en_sitemap": bool(d["in_sitemap"]),
                       "indexable": d["indexable"]})
    for url_id, lista in dudosas.items():
        # Lo dudoso solo en indexables: en una pagina que ya esta fuera del
        # indice, un indicio sin certeza no lleva a ninguna decision.
        if url_id in seguras or not info[url_id]["indexable"]:
            continue
        for x in lista:
            cuenta["candidato_" + x["senal"]] += 1
        an._add_issue(url_id, "soft_404_candidato", "info",
                      {"candidato_llm": True, "motivos": lista})
    from analysis.analyzer import _clave_de_grupo

    for s, coinciden, alcance, proporcion in candidatos_agrupados:
        paginas = sorted(u for u, _, _ in coinciden)
        an._add_issue(
            paginas[0], "soft_404_candidato", "info",
            {"candidato_llm": True,
             "motivos": [{"senal": "sonda_devuelve_portada" if s["_portada"] else "huella_de_plantilla",
                          "sonda": s.get("url"), "sonda_status": s.get("status"),
                          "plantilla": s.get("plantilla"), "proporcion": round(proporcion, 3)}],
             "paginas_afectadas": len(paginas), "alcance": alcance,
             "paginas_ejemplo": [info[u]["url"] for u in paginas[:MUESTRA]]},
            group_key=_clave_de_grupo(f"sonda|{s.get('clave') or s.get('url')}"),
        )
        cuenta["candidato_agrupado"] += 1
    an._flush_issues()

    # La pagina de error con poco texto no es ADEMAS contenido escaso.
    tapadas = 0
    lista = sorted(seguras)
    for i in range(0, len(lista), 5000):
        r = session.execute(
            delete(Issue).where(Issue.job_id == an.job_id, Issue.issue_type == "low_word_count",
                                Issue.url_id.in_(lista[i:i + 5000])))
        tapadas += r.rowcount or 0

    resumen = {
        "paginas_juzgadas": len(info),
        "soft_404": len(seguras),
        "soft_404_indexables": sum(1 for u in seguras if info[u]["indexable"]),
        "soft_404_en_sitemap": sum(1 for u in seguras if info[u]["in_sitemap"] and info[u]["indexable"]),
        "candidatos": sum(1 for u in dudosas if u not in seguras and info[u]["indexable"])
        + len(candidatos_agrupados),
        "por_senal": dict(sorted(cuenta.items())),
        "low_word_count_tapadas": tapadas,
        "productos_agotados_no_emitidos": agotados,
        "sondas": len(sondas),
        "sondas_con_huella": len(utiles),
        "detalle_sondas": resumen_sondas,
    }
    return _guardar(session, job, resumen)


def _guardar(session, job, resumen: dict) -> dict:
    if job is not None:
        from shared.models import Job

        previo = job.soft404 if isinstance(job.soft404, dict) else {}
        nuevo = {**previo, "resumen": resumen}
        session.execute(update(Job).where(Job.id == job.id).values(soft404=nuevo))
        session.flush()
    return resumen
