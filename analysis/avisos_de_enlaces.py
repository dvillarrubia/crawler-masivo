"""Avisos sobre los enlaces internos (decision 106).

Tres incidencias, con un mismo criterio de agregacion:

- ``enlace_no_rastreable``: enlaces que una persona puede pulsar y Google no
  puede seguir (``javascript:``, ``<a onclick>`` sin href, ``<span href>``...).
  Salen de la tabla ``enlaces_no_rastreables``, no de ``links``.
- ``enlace_interno_a_error`` / ``enlace_interno_a_redireccion``: enlaces
  internos a una URL que responde 4xx/5xx o 3xx.
- ``utm_en_enlace_interno``: enlaces internos con parametros ``utm_*``.

**Por pagina de origen, salvo lo que es de plantilla.** La unidad de trabajo es
la pagina donde hay que editar el enlace, y por eso se agrega por origen (una
incidencia por pagina con la cuenta y ejemplos, nunca una por enlace). Pero un
enlace que esta en el menu o en el pie de cientos de paginas se arregla UNA
vez, en la plantilla, y emitido por origen salia en todas las paginas del
sitio — que es justo lo que la decision 64 dice que deja de ser un aviso. Un
enlace (mismo tipo y mismo destino) que aparece en ``enlaces_plantilla_min_
paginas`` paginas distintas o mas (10 por defecto, el mismo umbral que la
seccion del PageRank, decision 29) se cuenta aparte, una vez:

- a un 3xx: una incidencia en la URL que redirige (``alcance: plantilla``);
- a un 4xx/5xx: ninguna nueva — la URL rota ya tiene su ``4xx_error`` o
  ``5xx_error`` y sus entrantes dicen desde donde se enlaza (decision 36);
- no rastreable: una incidencia por enlace distinto, en una pagina de ejemplo.

``utm_en_enlace_interno`` va por DESTINO siempre: la URL con ``utm`` es el
hallazgo (una URL con parametros que Google puede rastrear como otra pagina),
y el mismo banner con el mismo ``utm`` es la misma correccion aunque salga en
tres paginas o en tres mil. Si esa URL se rastreo, la incidencia va en ella y
sustituye a su ``url_has_parameters``, que dice lo mismo con menos (36).

Todo se calcula sobre la temporal de aristas de ``compute_link_counts`` o sobre
la tabla nueva filtrada por ``job_id``: ninguna lectura de ``links`` unida con
``urls`` (decision 37). Va en un modulo propio para no tocar los checks
existentes del analizador.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import and_, case, delete, func, literal, select
from sqlalchemy.orm import aliased

logger = logging.getLogger(__name__)

# Cuantos ejemplos se guardan en los detalles de una incidencia.
MUESTRA = 5
# Cuantos destinos se listan en una incidencia por origen.
MUESTRA_DESTINOS = 10

UMBRAL_PLANTILLA_DEFECTO = 10


def _clave(*partes: Any) -> str | None:
    from analysis.analyzer import _clave_de_grupo

    return _clave_de_grupo("|".join("" if p is None else str(p) for p in partes))


# ---------------------------------------------------------------------------
# Enlaces a 3xx / 4xx / 5xx y con utm, sobre la temporal de aristas
# ---------------------------------------------------------------------------
def avisar_enlaces_a_respuestas(az, aristas) -> None:
    """``enlace_interno_a_error``, ``enlace_interno_a_redireccion`` y
    ``utm_en_enlace_interno``.

    ``aristas`` es la temporal que monta ``compute_link_counts`` (ya solo con
    los enlaces de este job, con el id del destino resuelto). Se une con
    ``urls`` por clave primaria —origen y destino—, que no es lo que prohibe la
    decision 37: el filtro por job ya esta hecho en la temporal.
    """
    from shared.models import Url

    umbral = az.enlaces_plantilla_min_paginas
    o = aliased(Url)
    d = aliased(Url)

    # Origen: una pagina HTML que responde 200. Un 404 o un 500 con su menu
    # entero ya tiene su propio aviso, y sus enlaces no se arreglan editandolo
    # (decision 57). Un `noindex` SI entra: el enlace roto lo pulsa igual una
    # persona.
    base = (
        and_(
            aristas.c.es_interno.is_(True),
            aristas.c.destino_id.isnot(None),
            aristas.c.origen_id != aristas.c.destino_id,
            o.status_code == 200,
            o.is_html.is_(True),
        )
    )
    clase = case(
        (d.status_group == "3xx", literal("3xx")),
        (d.status_group.in_(("4xx", "5xx")), literal("error")),
        else_=literal(None),
    )

    # Destinos problematicos con cuantas paginas los enlazan.
    por_destino = az.session.execute(
        select(
            aristas.c.destino_id,
            clase.label("clase"),
            func.count(func.distinct(aristas.c.origen_id)).label("origenes"),
            func.sum(aristas.c.veces).label("enlaces"),
        )
        .select_from(aristas)
        .join(o, o.id == aristas.c.origen_id)
        .join(d, d.id == aristas.c.destino_id)
        .where(base, d.status_group.in_(("3xx", "4xx", "5xx")))
        .group_by(aristas.c.destino_id, clase)
    ).all()

    de_plantilla = {r.destino_id for r in por_destino if r.origenes >= umbral}
    propios = {r.destino_id for r in por_destino if r.origenes < umbral}

    # Plantilla a un 3xx: una incidencia en la URL que redirige.
    for r in por_destino:
        if r.destino_id in de_plantilla and r.clase == "3xx":
            az._add_issue(
                r.destino_id, "enlace_interno_a_redireccion", "info",
                {"alcance": "plantilla", "paginas_que_enlazan": int(r.origenes),
                 "enlaces": int(r.enlaces or 0)},
            )

    # Por origen, con los destinos que NO son de plantilla.
    if propios:
        porigen: dict[tuple[int, str], dict[str, Any]] = {}
        ids = list(propios)
        for i in range(0, len(ids), 5000):
            filas = az.session.execute(
                select(
                    aristas.c.origen_id, aristas.c.destino_id, clase.label("clase"),
                    d.url, d.status_code, func.sum(aristas.c.veces).label("veces"),
                )
                .select_from(aristas)
                .join(o, o.id == aristas.c.origen_id)
                .join(d, d.id == aristas.c.destino_id)
                .where(base, aristas.c.destino_id.in_(ids[i:i + 5000]))
                .group_by(aristas.c.origen_id, aristas.c.destino_id, clase,
                          d.url, d.status_code)
            ).all()
            for f in filas:
                if f.clase is None:
                    continue
                acc = porigen.setdefault((f.origen_id, f.clase),
                                         {"enlaces": 0, "destinos": 0, "ejemplos": []})
                acc["enlaces"] += int(f.veces or 0)
                acc["destinos"] += 1
                if len(acc["ejemplos"]) < MUESTRA_DESTINOS:
                    acc["ejemplos"].append({"url": f.url, "status_code": f.status_code})
        for (origen_id, cl), acc in porigen.items():
            acc["ejemplos"].sort(key=lambda e: (e["status_code"] or 0, e["url"] or ""))
            if cl == "error":
                az._add_issue(origen_id, "enlace_interno_a_error", "warning",
                              {"alcance": "pagina", **acc})
            else:
                az._add_issue(origen_id, "enlace_interno_a_redireccion", "info",
                              {"alcance": "pagina", **acc})

    _avisar_utm(az, aristas, o, base_sin_destino=and_(
        aristas.c.es_interno.is_(True),
        aristas.c.utm_hash.isnot(None),
        o.status_code == 200,
        o.is_html.is_(True),
    ))
    az._flush_issues()


def _avisar_utm(az, aristas, o, base_sin_destino) -> None:
    from shared.models import Issue, Link, Url

    d = aliased(Url)
    filas = az.session.execute(
        select(
            aristas.c.utm_hash,
            func.max(aristas.c.destino_id).label("destino_id"),
            func.count(func.distinct(aristas.c.origen_id)).label("origenes"),
            func.sum(aristas.c.veces).label("enlaces"),
            func.min(aristas.c.origen_id).label("ejemplo_origen"),
        )
        .select_from(aristas)
        .join(o, o.id == aristas.c.origen_id)
        .where(base_sin_destino)
        .group_by(aristas.c.utm_hash)
    ).all()
    if not filas:
        return

    destinos = {f.destino_id for f in filas if f.destino_id is not None}
    info_destino: dict[int, tuple[str, bool | None]] = {}
    if destinos:
        for i, url, indexable in az.session.execute(
                select(d.id, d.url, d.indexable).where(d.id.in_(destinos))).all():
            info_destino[i] = (url, indexable)

    sustituidas: list[int] = []
    for f in filas:
        if f.destino_id is not None and f.destino_id in info_destino:
            url, indexable = info_destino[f.destino_id]
            url_id = f.destino_id
            rastreado = True
            sustituidas.append(url_id)
        else:
            # No se rastreo (excluida por patron, o fuera del limite): la URL se
            # saca de `links` por (job_id, hash), con el indice ix_links_to_hash.
            url = az.session.execute(
                select(Link.to_url).where(Link.job_id == az.job_id,
                                          Link.to_url_hash == f.utm_hash).limit(1)
            ).scalar()
            url_id, indexable, rastreado = f.ejemplo_origen, None, False
        # Si la URL con utm se puede indexar, es una pagina duplicada que el
        # propio sitio reparte: warning. Si se canonicaliza o no se rastreo, lo
        # que queda es la sesion de Analytics reiniciada y el rastreo gastado:
        # info. No se afirma que se indexe lo que no se ha visto.
        az._add_issue(
            url_id, "utm_en_enlace_interno", "warning" if indexable else "info",
            {"destino": url, "destino_rastreado": rastreado,
             "destino_indexable": indexable,
             "paginas_que_enlazan": int(f.origenes), "enlaces": int(f.enlaces or 0),
             "ejemplos_origen": az._urls_de([f.ejemplo_origen])},
            group_key=_clave(f.utm_hash),
        )

    # Un aviso por hallazgo (decision 36): en la URL con utm, el
    # `url_has_parameters` generico dice lo mismo que este, con menos.
    if sustituidas:
        az._flush_issues()
        for i in range(0, len(sustituidas), 5000):
            az.session.execute(
                delete(Issue).where(
                    Issue.job_id == az.job_id,
                    Issue.issue_type == "url_has_parameters",
                    Issue.url_id.in_(sustituidas[i:i + 5000]),
                ).execution_options(synchronize_session=False)
            )


# ---------------------------------------------------------------------------
# Enlaces que Google no puede seguir
# ---------------------------------------------------------------------------
def _firma_no_rastreable(f) -> tuple:
    """Lo que hace que dos enlaces no rastreables sean EL MISMO componente.

    Por destino no basta: el selector de idioma de Lopesan es un
    ``onclick="window.location.href='/en/<esta pagina>'"`` y el destino cambia
    en cada pagina, asi que por destino salia una incidencia en 201 de 216
    paginas por un solo componente de la plantilla. Lo que se repite es el
    componente: el tipo, donde esta y como se rotula («English» en el menu). Sin
    ancla, el atributo.
    """
    return (f.crawlability, f.link_position, f.anchor_text or f.href)


def avisar_enlaces_no_rastreables(az) -> None:
    """``enlace_no_rastreable``, desde ``enlaces_no_rastreables`` por job_id.

    Severidad = consecuencia (decision 64): ``warning`` cuando se sabe que
    detras hay una URL INTERNA a la que Google no llega por ese enlace;
    ``info`` cuando el codigo no dice a donde va (el ``javascript:void(0)`` de
    un desplegable): no se afirma que se pierda nada. Los detalles dicen ademas
    cuantos de esos destinos no estan en el censo: ni el rastreo ni Google los
    han encontrado por otro camino.
    """
    from sqlalchemy import inspect

    from shared.models import EnlaceNoRastreable as E, Url

    if not inspect(az.session.connection()).has_table(E.__tablename__):
        # Tabla aun sin crear (primer minuto de un despliegue): no hay datos,
        # que no es «no hay enlaces no rastreables». Se dice en el log y el
        # resto del analisis sigue; un rollback aqui se llevaria todo lo hecho.
        logger.warning("Sin tabla enlaces_no_rastreables: no se evaluan (decision 106)")
        return

    from shared.comparacion import MOTIVOS_TRUNCADO
    from shared.models import Job

    umbral = az.enlaces_plantilla_min_paginas
    # En un censo truncado (tope de URLs, de tiempo, estancado) que un destino
    # no este no dice nada: no se llego a el. Lo que no se sabe no se afirma
    # (decision 67): la cifra va a None, no a un numero que se lee como hallazgo.
    motivo = az.session.execute(
        select(Job.finish_reason).where(Job.id == az.job_id)).scalar()
    censo_entero = (motivo or "") not in MOTIVOS_TRUNCADO

    def _fuera(acc) -> int | None:
        return len(acc["fuera"]) if censo_entero else None

    # Destinos que SI estan en el censo. La tabla nueva se filtra por su job_id
    # y se une con `urls` por (job_id, url_hash), el indice unico.
    en_censo = set(az.session.execute(
        select(E.destino_hash).distinct()
        .join(Url, and_(Url.job_id == E.job_id, Url.url_hash == E.destino_hash))
        .where(E.job_id == az.job_id, E.destino_hash.isnot(None))
    ).scalars())

    filas = az.session.execute(
        select(E.from_url_id, E.crawlability, E.href, E.destino, E.destino_interno,
               E.destino_hash, E.anchor_text, E.etiqueta, E.link_position, E.repeticiones)
        .where(E.job_id == az.job_id)
        .order_by(E.from_url_id, E.id)
    ).all()

    paginas_por_firma: dict[tuple, set[int]] = defaultdict(set)
    for f in filas:
        paginas_por_firma[_firma_no_rastreable(f)].add(f.from_url_id)
    de_plantilla = {k for k, v in paginas_por_firma.items() if len(v) >= umbral}

    def _nuevo() -> dict[str, Any]:
        return {"enlaces": 0, "por_tipo": defaultdict(int), "con_destino_interno": 0,
                "destinos": set(), "fuera": set(), "ejemplos": [], "primera": None}

    plantillas: dict[tuple, dict[str, Any]] = defaultdict(_nuevo)
    porigen: dict[int, dict[str, Any]] = defaultdict(_nuevo)
    for f in filas:
        k = _firma_no_rastreable(f)
        acc = plantillas[k] if k in de_plantilla else porigen[f.from_url_id]
        veces = int(f.repeticiones or 1)
        acc["enlaces"] += veces
        acc["por_tipo"][f.crawlability] += veces
        if acc["primera"] is None:
            acc["primera"] = f
        if f.destino_interno:
            acc["con_destino_interno"] += veces
            if f.destino_hash:
                acc["destinos"].add(f.destino_hash)
                if f.destino_hash not in en_censo:
                    acc["fuera"].add(f.destino_hash)
        if k not in de_plantilla and len(acc["ejemplos"]) < MUESTRA:
            acc["ejemplos"].append({
                "tipo": f.crawlability, "etiqueta": f.etiqueta,
                "href": (f.href or "")[:200], "destino": f.destino,
                "ancla": f.anchor_text,
            })

    for k, acc in plantillas.items():
        f = acc["primera"]
        paginas = sorted(paginas_por_firma[k])
        az._add_issue(
            paginas[0], "enlace_no_rastreable",
            "warning" if acc["con_destino_interno"] else "info",
            {"alcance": "plantilla", "tipo": f.crawlability, "etiqueta": f.etiqueta,
             "posicion": f.link_position, "ancla": f.anchor_text,
             "href_ejemplo": (f.href or "")[:200], "destino_ejemplo": f.destino,
             "destinos_internos_distintos": len(acc["destinos"]),
             "destinos_fuera_del_censo": _fuera(acc),
             "paginas_afectadas": len(paginas), "apariciones": acc["enlaces"],
             "paginas_ejemplo": az._urls_de(paginas[:MUESTRA])},
            group_key=_clave(*k),
        )

    for origen_id, acc in porigen.items():
        az._add_issue(
            origen_id, "enlace_no_rastreable",
            "warning" if acc["con_destino_interno"] else "info",
            {"alcance": "pagina", "enlaces": acc["enlaces"],
             "por_tipo": dict(acc["por_tipo"]),
             "con_destino_interno": acc["con_destino_interno"],
             "destinos_fuera_del_censo": _fuera(acc),
             "ejemplos": acc["ejemplos"]},
        )
    az._flush_issues()
