"""Intencion de la pagina y de sus busquedas reales (#39, #169 puntos 1 y 2;
decision 176).

Dos modulos del enriquecido (`shared/enriquecido.REGISTRO`) se apoyan aqui:

- **`intencion_pagina`**: `intencion_pagina` y `etapa_embudo` sobre la ruta, el
  titulo, el primer H1 que se pinta y el principio del contenido principal.
  Solo las paginas con algo que juzgar (titulo, H1 o contenido).
- **`intencion_busquedas`**: `intencion_busqueda` sobre las busquedas de Search
  Console (`gsc_consultas`, decision 157) que traen clics a cada pagina en la
  ventana del censo: las `MAX_BUSQUEDAS_POR_PAGINA` con mas clics. El estado es
  la busqueda, el sitio (sin `www.`) y las `marcas` del job, y NADA de la
  pagina: asi la misma busqueda en otra pagina o en otro censo del mismo cliente
  es el mismo estado y sale de la cache (decision 84) sin volver a pagar. Sin
  cifras en el estado por lo mismo: los clics se leen al comparar, no se envian.

Con las dos sale el **desajuste pagina-busquedas ponderado por clics**
(`desajuste`), que hoy es orientativo: ninguna de las preguntas tiene umbral
(#8), todo es `incierto`, y la incidencia `intencion_desalineada`
(`incidencias_intencion_desalineada`) esta preparada pero no emite nada hasta
que las dos lo tengan.

Lo que no se puede afirmar no se afirma: las busquedas de marca o ajenas
(`navigational`) pueden caer en cualquier pagina de esa marca, y las `unclear`
no dicen nada; ninguna de las dos cuenta para el desajuste.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Cuantas busquedas por pagina se preguntan, por clics. Medido el 24-oct-2026
# en Lopesan (censo 1e7ea651, 90 dias): de 3.216 indexables, 1.921 tienen
# busquedas con clics; con 10 por pagina son 10.121 pares y 7.183 busquedas
# distintas que cubren el 77,6 % de los clics con busqueda conocida. Con todas,
# 24.516 pares y 14.943 distintas por el 22 % restante, casi todo cola larga.
MAX_BUSQUEDAS_POR_PAGINA = 10
# Una busqueda sin clics no pesa en un desajuste ponderado por clics.
MIN_CLICS = 1
# Lo que no cuenta para el desajuste (ver el docstring del modulo).
NO_CUENTAN = ("navigational", "unclear")
AMBITO_BUSQUEDA = "busqueda"
# Por encima de estas URLs se lee la ventana entera una vez (ver
# `busquedas_por_url`).
CLAVES_POR_CONSULTA = 500


def _trozos(lista: list, n: int = 500):
    for i in range(0, len(lista), n):
        yield lista[i:i + n]


def _uuid(v):
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


# ---------------------------------------------------------------------------
# Pagina
# ---------------------------------------------------------------------------
def estados_intencion_pagina(session, url_ids: list[int]):
    """Ruta, titulo, primer H1 visible y 600 caracteres de contenido sin
    plantilla (`estados_titulo_h1_contenido`). La ruta ayuda (`/blog/`,
    `/reservar/`) y no rompe la cache entre la URL con y sin `www.`."""
    from shared.enriquecido import estados_titulo_h1_contenido
    from shared.models import Url

    base = estados_titulo_h1_contenido(session, url_ids)
    rutas: dict[int, str] = {}
    for trozo in _trozos(list(url_ids)):
        for uid, path in session.query(Url.id, Url.path).filter(Url.id.in_(trozo)):
            rutas[uid] = path or "/"
    for e in base:
        e.datos = {"ruta": rutas.get(e.url_id, "/"), **e.datos}
    return base


def paginas_con_algo_que_juzgar(session, job_id, url_ids: list[int]) -> list[int]:
    """Las que tienen titulo, H1 visible o contenido: sin nada de eso el modelo
    solo puede contestar `unclear`, y se pagaria por nada."""
    from sqlalchemy import func

    from shared.models import Heading, HtmlMeta, PageContent

    if not url_ids:
        return []
    con: set[int] = set()
    for trozo in _trozos(list(url_ids)):
        con |= {u for (u,) in session.query(HtmlMeta.url_id).filter(
            HtmlMeta.url_id.in_(trozo), func.length(func.trim(func.coalesce(HtmlMeta.title, ""))) > 0)}
        con |= {u for (u,) in session.query(Heading.url_id).filter(
            Heading.url_id.in_(trozo), Heading.tag == "h1", Heading.oculto.isnot(True),
            func.length(func.trim(func.coalesce(Heading.text, ""))) > 0)}
        con |= {u for (u,) in session.query(PageContent.url_id).filter(
            PageContent.url_id.in_(trozo),
            func.length(func.trim(func.coalesce(PageContent.content_text, ""))) > 0)}
    return [u for u in url_ids if u in con]


# ---------------------------------------------------------------------------
# Busquedas de Search Console
# ---------------------------------------------------------------------------
def sitio_del_job(job) -> str:
    """El host de la primera semilla sin `www.`: con y sin `www.` es el mismo
    sitio para quien busca, y dos estados distintos pagarian dos veces
    (decision 169)."""
    semillas = list(getattr(job, "seeds", None) or [])
    host = (urlsplit(str(semillas[0])).hostname or "").lower() if semillas else ""
    return host[4:] if host.startswith("www.") else host


def marcas_del_job(job) -> list[str]:
    return sorted({str(m).strip().lower() for m in ((job.config or {}).get("marcas") or [])
                   if str(m).strip()})


def _job_de(session, url_ids: list[int]):
    from shared.models import Job, Url

    if not url_ids:
        return None
    fila = session.query(Url.job_id).filter(Url.id == url_ids[0]).first()
    return session.get(Job, fila[0]) if fila else None


def busquedas_por_url(session, job, url_ids: list[int]) -> dict[int, list[tuple[str, int]]]:
    """{url_id: [(busqueda, clics), ...]}: las `MAX_BUSQUEDAS_POR_PAGINA` con
    mas clics de cada URL en la ventana del censo, de las propiedades activas
    del cliente. Vacio sin cliente, sin Search Console o sin la tabla (una base
    sin migrar): best-effort, el try cubre la funcion entera (decision 74).

    `clics > 0` fila a fila y no `HAVING sum > 0`: es lo mismo (los clics no
    son negativos) y en Lopesan pasa de 7,2 s a 0,5 s.
    """
    try:
        from sqlalchemy import func

        from shared.gsc import clave_de_url, propiedades_activas, ventana_del_censo
        from shared.models import GscConsulta as C, Url

        if job is None or not job.cliente_id or not url_ids:
            return {}
        propiedades = propiedades_activas(session, job.cliente_id)
        if not propiedades:
            return {}
        desde, hasta = ventana_del_censo(job)
        ids_de_clave: dict[str, list[int]] = {}
        for trozo in _trozos(list(url_ids)):
            for uid, url in session.query(Url.id, Url.url).filter(Url.id.in_(trozo)):
                try:
                    ids_de_clave.setdefault(clave_de_url(url), []).append(uid)
                except Exception:  # noqa: BLE001 -- un href imposible no tiene busquedas
                    continue
        crudo: dict[int, list[tuple[int, int, str]]] = {}
        base = (session.query(C.url_clave, C.consulta, func.sum(C.clics), func.sum(C.impresiones))
                .filter(C.cliente_id == job.cliente_id, C.propiedad.in_(propiedades),
                        C.fecha >= desde, C.fecha <= hasta, C.clics > 0))
        # Pocas URLs (un lote de la ejecucion): con su clave. Muchas (el filtro
        # de todo el censo): UNA lectura de la ventana y se cruza aqui; por
        # trozos de 500 claves se leia la ventana entera una vez por trozo (el
        # indice es por cliente y fecha, no por clave).
        claves = sorted(ids_de_clave)
        consultas = ([base.filter(C.url_clave.in_(claves))] if len(claves) <= CLAVES_POR_CONSULTA
                     else [base])
        for q in consultas:
            for clave, consulta, clics, imp in q.group_by(C.url_clave, C.consulta):
                if clave not in ids_de_clave:
                    continue
                if int(clics or 0) < MIN_CLICS or not (consulta or "").strip():
                    continue
                for uid in ids_de_clave[clave]:
                    crudo.setdefault(uid, []).append((int(clics or 0), int(imp or 0), consulta))
        salida = {}
        for uid, lista in crudo.items():
            # Mas clics, despues mas impresiones y la busqueda: orden total, para
            # que el estado (y su cache) no cambie entre dos lecturas.
            lista.sort(key=lambda t: (-t[0], -t[1], t[2]))
            salida[uid] = [(q, c) for c, _, q in lista[:MAX_BUSQUEDAS_POR_PAGINA]]
        return salida
    except Exception:  # noqa: BLE001
        logger.exception("Busquedas de Search Console para la intencion: fallo; sin busquedas")
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {}


def estado_de_busqueda(sitio: str, marcas: list[str], busqueda: str) -> dict:
    return {"sitio": sitio, "marcas": list(marcas), "busqueda": busqueda}


def estados_intencion_busquedas(session, url_ids: list[int]):
    """Un estado por (pagina, busqueda), con `ambito="busqueda"`. Una pagina con
    diez busquedas son diez estados; la misma busqueda en dos paginas es un solo
    estado distinto (una llamada) y dos filas de `decisions`."""
    from shared.decisiones import Estado

    job = _job_de(session, url_ids)
    if job is None:
        return []
    sitio, marcas = sitio_del_job(job), marcas_del_job(job)
    por_url = busquedas_por_url(session, job, url_ids)
    return [Estado(datos=estado_de_busqueda(sitio, marcas, q), ambito=AMBITO_BUSQUEDA, url_id=u)
            for u in url_ids for q, _ in por_url.get(u, [])]


def paginas_con_busquedas(session, job_id, url_ids: list[int]) -> list[int]:
    from shared.models import Job

    job = session.get(Job, _uuid(job_id))
    con = busquedas_por_url(session, job, url_ids)
    return [u for u in url_ids if con.get(u)]


def motivo_sin_busquedas(session, job_id) -> str:
    """Por que `intencion_busquedas` no tiene trabajo, dicho para quien lanza."""
    from shared.gsc import propiedades_activas, ventana_del_censo
    from shared.models import Job

    job = session.get(Job, _uuid(job_id))
    if job is None or not job.cliente_id:
        return "El rastreo no es de ningun cliente: sin cliente no hay Search Console."
    try:
        propiedades = propiedades_activas(session, job.cliente_id)
    except Exception:  # noqa: BLE001
        session.rollback()
        propiedades = []
    if not propiedades:
        return ("El cliente no tiene Search Console conectado (o aun no se ha descargado nada): "
                "este modulo no tiene busquedas que clasificar.")
    desde, hasta = ventana_del_censo(job)
    return (f"Search Console no tiene busquedas con clics de estas paginas entre el {desde} y el "
            f"{hasta}: este modulo no tiene trabajo.")


# ---------------------------------------------------------------------------
# Desajuste pagina-busquedas (orientativo) e incidencia (apagada)
# ---------------------------------------------------------------------------
def desajuste(intencion_pagina: str | None, busquedas: list[dict],
              solo_decididas: bool = False) -> dict:
    """Reparto de los clics de una pagina por la intencion de sus busquedas y
    cuanto casa con la intencion de la pagina.

    `busquedas`: [{"intencion", "clics", "estado"?}]. No cuentan las
    `navigational` ni las `unclear` (ver el modulo) ni las que no tienen
    respuesta. `desalineada` = la intencion de la pagina se conoce (no es
    `unclear`), hay clics que cuentan, la intencion que mas clics concentra es
    otra y la de la pagina se queda con menos de la mitad: la definicion de
    #39 («no coincide con la que concentra la mayoria»). Con
    `solo_decididas`, solo las busquedas `decidido` (la incidencia de verdad).
    """
    por_intencion: dict[str, int] = {}
    excluidos = 0
    for b in busquedas:
        clics = int(b.get("clics") or 0)
        intencion = b.get("intencion")
        if intencion is None or (solo_decididas and b.get("estado") != "decidido"):
            excluidos += clics
            continue
        por_intencion[intencion] = por_intencion.get(intencion, 0) + clics
    contables = {k: v for k, v in por_intencion.items() if k not in NO_CUENTAN}
    total = sum(contables.values())
    alineados = contables.get(intencion_pagina, 0) if intencion_pagina else 0
    dominante = (sorted(contables.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                 if total else None)
    conocida = intencion_pagina is not None and intencion_pagina != "unclear"
    parte = (alineados / total) if total else None
    return {
        "clics_por_intencion": por_intencion,
        "clics_que_cuentan": total,
        "clics_sin_contar": sum(v for k, v in por_intencion.items() if k in NO_CUENTAN) + excluidos,
        "clics_alineados": alineados,
        "clics_desalineados": total - alineados if conocida else 0,
        "parte_alineada": None if parte is None else round(parte, 4),
        "intencion_dominante": dominante,
        "desalineada": bool(conocida and total and dominante != intencion_pagina and parte < 0.5),
    }


def incidencias_intencion_desalineada(job_id, paginas: list[dict]) -> list[dict]:
    """`intencion_desalineada`, una por pagina (un aviso por hallazgo), con las
    busquedas que la causan. **Apagada**: devuelve [] mientras
    `intencion_pagina` o `intencion_busqueda` no tengan umbral (#8), y aun con
    umbral solo usa respuestas `decidido`. Nadie la llama todavia: el
    enriquecido no emite incidencias (decision 85, pendiente).

    `paginas`: lo que da `intencion_frente_a_busquedas` (con todas las
    busquedas de cada pagina, no solo las que se enseñan)."""
    from shared.preguntas import obtener

    if obtener("intencion_pagina").umbral is None or obtener("intencion_busqueda").umbral is None:
        return []
    salida = []
    for p in paginas:
        if p.get("estado_pagina") != "decidido":
            continue
        d = desajuste(p.get("intencion_pagina"), p.get("busquedas") or [], solo_decididas=True)
        if not d["desalineada"]:
            continue
        principales = [b for b in (p.get("busquedas") or [])
                       if b.get("estado") == "decidido" and b.get("intencion") == d["intencion_dominante"]]
        salida.append({
            "job_id": job_id, "url_id": p["url_id"], "issue_type": "intencion_desalineada",
            "severity": "info", "group_key": None,
            "details": {"intencion_pagina": p.get("intencion_pagina"),
                        "intencion_dominante": d["intencion_dominante"],
                        "clics_desalineados": d["clics_desalineados"],
                        "parte_alineada": d["parte_alineada"],
                        "busquedas": [{"busqueda": b["busqueda"], "clics": b["clics"]}
                                      for b in principales[:5]]}})
    return salida


def intencion_frente_a_busquedas(session, run_id, limite: int = 100) -> dict:
    """Lo que enseña la pantalla del enriquecido: por pagina, su intencion, la
    de sus busquedas con los clics y el desajuste; y el embudo con el trafico
    real (clics de la pagina por etapa y por plantilla, #169 punto 2).

    Lee las decisiones de ESTA ejecucion; los clics se vuelven a leer de
    `gsc_consultas` en la misma ventana (no se guardan en el estado, para que la
    cache no dependa de ellos). Todo orientativo mientras falte un umbral.
    """
    from shared.decisiones import clave_estado
    from shared.models import Decision, EnrichmentRun, Job, Url
    from shared.plantillas import plantilla_de, reglas_de_plantilla
    from shared.preguntas import obtener

    run = session.get(EnrichmentRun, _uuid(run_id))
    job = session.get(Job, run.job_id) if run else None
    if job is None:
        return {"paginas": [], "resumen": None, "embudo": None}

    def filas(pregunta):
        return (session.query(Decision.url_id, Decision.clave, Decision.respuesta,
                              Decision.confianza, Decision.estado)
                .filter(Decision.run_id == run.id, Decision.pregunta == pregunta).all())

    pagina = {u: (r, c, e) for u, _, r, c, e in filas("intencion_pagina")}
    etapa = {u: (r, c, e) for u, _, r, c, e in filas("etapa_embudo")}
    de_busqueda: dict[tuple[int, str], tuple] = {(u, k): (r, c, e)
                                                 for u, k, r, c, e in filas("intencion_busqueda")}
    umbrales = {n: obtener(n).umbral for n in ("intencion_pagina", "etapa_embudo", "intencion_busqueda")}

    # --- desajuste ---------------------------------------------------------
    con_busquedas = sorted({u for u, _ in de_busqueda})
    sitio, marcas = sitio_del_job(job), marcas_del_job(job)
    por_url = busquedas_por_url(session, job, con_busquedas) if con_busquedas else {}
    urls = {}
    ids = sorted(set(con_busquedas) | set(etapa) | set(pagina))
    for trozo in _trozos(ids):
        for uid, url, path, clics in session.query(Url.id, Url.url, Url.path, Url.gsc_clics).filter(
                Url.id.in_(trozo)):
            urls[uid] = (url, path, clics)
    paginas = []
    total_por_intencion: dict[str, int] = {}
    for uid in con_busquedas:
        intencion, conf, est = pagina.get(uid, (None, None, None))
        busquedas = []
        for q, clics in por_url.get(uid, []):
            r = de_busqueda.get((uid, clave_estado(estado_de_busqueda(sitio, marcas, q))))
            busquedas.append({"busqueda": q, "clics": clics, "intencion": r[0] if r else None,
                              "confianza": r[1] if r else None, "estado": r[2] if r else None})
        d = desajuste(intencion, busquedas)
        for k, v in d["clics_por_intencion"].items():
            total_por_intencion[k] = total_por_intencion.get(k, 0) + v
        paginas.append({"url_id": uid, "url": (urls.get(uid) or ("",))[0],
                        "intencion_pagina": intencion, "confianza_pagina": conf, "estado_pagina": est,
                        "busquedas": busquedas, **d})
    paginas.sort(key=lambda p: (-p["clics_desalineados"], -p["clics_que_cuentan"], p["url"]))
    resumen = {
        "paginas_comparadas": sum(1 for p in paginas if p["intencion_pagina"] is not None),
        "paginas_sin_intencion": sum(1 for p in paginas if p["intencion_pagina"] is None),
        "desalineadas": sum(1 for p in paginas if p["desalineada"]),
        "clics_desalineados": sum(p["clics_desalineados"] for p in paginas),
        "clics_que_cuentan": sum(p["clics_que_cuentan"] for p in paginas),
        "clics_por_intencion": total_por_intencion,
        "orientativo": umbrales["intencion_pagina"] is None or umbrales["intencion_busqueda"] is None,
        "incidencia_activa": bool(umbrales["intencion_pagina"] is not None
                                  and umbrales["intencion_busqueda"] is not None),
    } if con_busquedas else None

    # --- embudo con trafico real ---------------------------------------------
    embudo = None
    if etapa:
        reglas = reglas_de_plantilla((job.config or {}).get("templates"))
        por_etapa: dict[str, dict] = {}
        por_plantilla: dict[str, dict[str, int]] = {}
        for uid, (r, _, _) in etapa.items():
            url, path, clics = urls.get(uid, ("", "/", None))
            e = por_etapa.setdefault(r or "sin_respuesta", {"paginas": 0, "clics": 0})
            e["paginas"] += 1
            e["clics"] += int(clics or 0)
            pl = por_plantilla.setdefault(plantilla_de(path or "/", reglas), {})
            pl[r or "sin_respuesta"] = pl.get(r or "sin_respuesta", 0) + int(clics or 0)
        plantillas = sorted(por_plantilla.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
        embudo = {"por_etapa": por_etapa,
                  "por_plantilla": [{"plantilla": k, "clics_por_etapa": v} for k, v in plantillas[:30]],
                  "orientativo": umbrales["etapa_embudo"] is None}
    return {"resumen": resumen, "paginas": paginas[:max(0, int(limite))], "embudo": embudo}


__all__ = [
    "MAX_BUSQUEDAS_POR_PAGINA", "NO_CUENTAN", "estados_intencion_pagina",
    "paginas_con_algo_que_juzgar", "busquedas_por_url", "estados_intencion_busquedas",
    "paginas_con_busquedas", "motivo_sin_busquedas", "desajuste",
    "incidencias_intencion_desalineada", "intencion_frente_a_busquedas", "sitio_del_job",
]
