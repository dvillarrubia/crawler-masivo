"""Avisos sobre los enlaces internos (decision 106).

Cuatro incidencias:

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
sitio — que es justo lo que la decision 64 dice que deja de ser un aviso. Lo
que cuenta como «el mismo enlace» depende del aviso, y el umbral es siempre
``enlaces_plantilla_min_paginas`` paginas de origen distintas (10 por defecto,
el de la seccion del PageRank, decision 29):

- a un 3xx/4xx/5xx: el mismo DESTINO. Si 10 paginas o mas enlazan la URL que
  redirige, un aviso en ella (``alcance: plantilla``); si es una URL rota,
  ninguno nuevo — ya tiene su ``4xx_error`` o ``5xx_error`` y sus entrantes
  dicen desde donde se enlaza (decision 36);
- no rastreable: el mismo COMPONENTE, (tipo, posicion, ancla o atributo) —por
  destino no, porque el selector de idioma cambia de destino en cada pagina—;
  una incidencia por componente, en la pagina de URL menor.

``utm_en_enlace_interno`` va por DESTINO siempre: la URL con ``utm`` es el
hallazgo (una URL con parametros que Google puede rastrear como otra pagina),
y el mismo banner con el mismo ``utm`` es la misma correccion aunque salga en
tres paginas o en tres mil. Si esa URL se rastreo, la incidencia va en ella y
sustituye a su ``url_has_parameters``, que dice lo mismo con menos (36).

Lo que ya dice otro aviso no se repite (36): un enlace con ``utm`` a una URL que
redirige o falla lo cuentan los avisos de respuesta, y una URL que redirige a
una pagina con ``solo_enlazada_por_redireccion`` (decision 63) no lleva el de
redireccion: los dos piden lo mismo, enlazar la URL final.

Una incidencia que no tiene pagina propia (la de plantilla de un no rastreable,
la de un ``utm`` que no se rastreo) se ancla en la pagina de URL MENOR, no en la
de id menor: el id depende del orden de rastreo, y la comparacion entre censos
identifica el hallazgo por (tipo, URL, clave) — otra pagina de anclaje lo daria
por resuelto y por nuevo a la vez (decision 88).

Todo se calcula sobre la temporal de aristas de ``compute_link_counts`` o sobre
la tabla nueva filtrada por ``job_id``: ninguna lectura de ``links`` unida con
``urls`` (decision 37). Va en un modulo propio para no tocar los checks
existentes del analizador.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import and_, case, delete, func, literal, or_, select
from sqlalchemy.orm import aliased

logger = logging.getLogger(__name__)

# Cuantos ejemplos se guardan en los detalles de una incidencia.
MUESTRA = 5
# Cuantos destinos se listan en una incidencia por origen.
MUESTRA_DESTINOS = 10

UMBRAL_PLANTILLA_DEFECTO = 10

# Los tipos que emite este modulo. Cada uno lleva su ficha y su nombre en el
# frontend (`incidencias.js`) y entra en el ambito «Enlazado» del explorador.
TIPOS = (
    "enlace_no_rastreable",
    "enlace_interno_a_error",
    "enlace_interno_a_redireccion",
    "utm_en_enlace_interno",
)


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
    # Un destino que es un muro (decision 103) no se ha visto: ni redirige ni
    # da error, asi que el enlace no se avisa.
    from shared.respuestas import NO_VALIDAS

    d_vista = or_(d.response_class.is_(None), d.response_class.notin_(sorted(NO_VALIDAS)))
    clase = case(
        (~d_vista, literal(None)),
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
        .where(base, d_vista, d.status_group.in_(("3xx", "4xx", "5xx")))
        .group_by(aristas.c.destino_id, clase)
    ).all()

    # La URL que redirige a una pagina con `solo_enlazada_por_redireccion`
    # (decision 63): ese aviso ya pide apuntar los enlaces a la URL final, y es
    # el mas fuerte de los dos (la pagina final no tiene ni un enlace directo).
    # Repetirlo aqui seria el mismo hallazgo dos veces (36).
    ya_avisadas = _redirecciones_a_solo_enlazadas(az, {
        r.destino_id for r in por_destino if r.clase == "3xx"})
    por_destino = [r for r in por_destino if r.destino_id not in ya_avisadas]

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


def _redirecciones_a_solo_enlazadas(az, ids: set[int]) -> set[int]:
    """De esas URLs que redirigen, las que llevan a una pagina final que recibe
    `solo_enlazada_por_redireccion` (mismo criterio, misma funcion)."""
    if not ids:
        return set()
    from analysis.analyzer import criterio_solo_por_redireccion
    from shared.models import Url

    x = aliased(Url)
    y = aliased(Url)
    fuera: set[int] = set()
    lista = list(ids)
    for i in range(0, len(lista), 5000):
        fuera.update(az.session.execute(
            select(x.id).join(y, and_(y.job_id == x.job_id, y.url == x.redirect_url))
            .where(x.id.in_(lista[i:i + 5000]), x.is_internal.is_(True),
                   *criterio_solo_por_redireccion(y, az.job_id))
        ).scalars())
    return fuera


def _avisar_utm(az, aristas, o, base_sin_destino) -> None:
    from shared.models import Issue, Link, Url
    from shared.respuestas import NO_VALIDAS

    d = aliased(Url)
    filas = az.session.execute(
        select(
            aristas.c.utm_hash,
            func.max(aristas.c.destino_id).label("destino_id"),
            func.count(func.distinct(aristas.c.origen_id)).label("origenes"),
            func.sum(aristas.c.veces).label("enlaces"),
            func.min(o.url).label("ejemplo_url"),
        )
        .select_from(aristas)
        .join(o, o.id == aristas.c.origen_id)
        .where(base_sin_destino)
        .group_by(aristas.c.utm_hash)
    ).all()
    if not filas:
        return

    destinos = {f.destino_id for f in filas if f.destino_id is not None}
    info_destino: dict[int, tuple[str, bool | None, str | None]] = {}
    if destinos:
        for i, url, indexable, grupo, clase_d in az.session.execute(
                select(d.id, d.url, d.indexable, d.status_group, d.response_class)
                .where(d.id.in_(destinos))).all():
            # Un muro no dice que responde el destino (decision 103).
            info_destino[i] = (url, indexable, None if clase_d in NO_VALIDAS else grupo)

    # La pagina de ejemplo (y de anclaje, si el destino no se rastreo) es la de
    # URL menor, no la de id menor: el id cambia con el orden de rastreo.
    por_url = {}
    ejemplos = [f.ejemplo_url for f in filas if f.ejemplo_url]
    for i in range(0, len(ejemplos), 5000):
        por_url.update(az.session.execute(
            select(o.url, o.id).where(o.job_id == az.job_id,
                                      o.url.in_(ejemplos[i:i + 5000]))).all())

    sustituidas: list[int] = []
    for f in filas:
        if f.destino_id is not None and f.destino_id in info_destino:
            url, indexable, grupo = info_destino[f.destino_id]
            if grupo in ("3xx", "4xx", "5xx"):
                # El enlace ya lo cuentan los avisos de respuesta (o el
                # `4xx_error` de la URL, si es de plantilla), y quitar el utm
                # es la misma correccion: enlazar la URL limpia y final. Un
                # aviso por hallazgo (36); el `url_has_parameters` se queda.
                continue
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
            url_id, indexable, rastreado = por_url.get(f.ejemplo_url), None, False
            if url_id is None:
                continue
        # Si la URL con utm se puede indexar, es una pagina duplicada que el
        # propio sitio reparte: warning. Si se canonicaliza o no se rastreo, lo
        # que queda es la sesion de Analytics reiniciada y el rastreo gastado:
        # info. No se afirma que se indexe lo que no se ha visto.
        az._add_issue(
            url_id, "utm_en_enlace_interno", "warning" if indexable else "info",
            {"destino": url, "destino_rastreado": rastreado,
             "destino_indexable": indexable,
             "paginas_que_enlazan": int(f.origenes), "enlaces": int(f.enlaces or 0),
             "ejemplos_origen": [f.ejemplo_url] if f.ejemplo_url else []},
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

    # Origen: una pagina HTML que responde 200, como en los avisos de respuesta.
    # Un 404 o un 500 con su menu entero ya tiene su aviso, y sus enlaces no se
    # arreglan editandolo (decision 57). Y una pagina re-rastreada que ahora
    # falla no puede conservar los de cuando respondia. Se une con `urls` por
    # clave primaria despues de filtrar la tabla por su job_id (37).
    origen = aliased(Url)
    filas = az.session.execute(
        select(E.id, E.from_url_id, E.crawlability, E.href, E.destino, E.destino_interno,
               E.destino_hash, E.anchor_text, E.etiqueta, E.link_position, E.repeticiones,
               origen.url.label("origen_url"))
        .join(origen, origen.id == E.from_url_id)
        .where(E.job_id == az.job_id, origen.status_code == 200,
               origen.is_html.is_(True))
        .order_by(E.from_url_id, E.id)
    ).all()
    # El boton que carga la pagina siguiente de un listado ya lo cuenta la
    # paginacion (`paginacion_sin_enlace`, decision 122), o no hace falta
    # porque la serie tambien enlaza esa pagina con un <a href>: un aviso por
    # hallazgo (36).
    de_paginacion = getattr(az, "_enr_de_paginacion", None) or set()
    filas = [f for f in filas if f.id not in de_paginacion]
    url_de = {f.from_url_id: f.origen_url for f in filas}

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
        # Por URL, no por id: el id depende del orden de rastreo y la pagina de
        # anclaje identifica el hallazgo entre censos (decision 88).
        paginas = sorted(paginas_por_firma[k], key=lambda i: url_de[i])
        az._add_issue(
            paginas[0], "enlace_no_rastreable",
            "warning" if acc["con_destino_interno"] else "info",
            {"alcance": "plantilla", "tipo": f.crawlability, "etiqueta": f.etiqueta,
             "posicion": f.link_position, "ancla": f.anchor_text,
             "href_ejemplo": (f.href or "")[:200], "destino_ejemplo": f.destino,
             "destinos_internos_distintos": len(acc["destinos"]),
             "destinos_fuera_del_censo": _fuera(acc),
             "paginas_afectadas": len(paginas), "apariciones": acc["enlaces"],
             "paginas_ejemplo": [url_de[i] for i in paginas[:MUESTRA]]},
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
