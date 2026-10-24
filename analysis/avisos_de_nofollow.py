"""El `nofollow` dentro del propio sitio (decision 183).

Tres incidencias, de lo mas grave a lo menos:

- ``solo_enlazada_con_nofollow`` (warning, por pagina): una indexable a la que
  el sitio enlaza, pero SOLO con enlaces ``nofollow`` (por el ``rel`` del
  enlace o por la directiva de la pagina que enlaza), y sin ningun camino desde
  la home. Es la causa de un ``not_reachable_from_home`` y lo sustituye (36):
  los dos dicen «no se llega», este dice por que y que tocar.
- ``pagina_nofollow`` (warning, agrupada por host y plantilla): una indexable
  que declara ``nofollow`` (o ``none``) en su meta robots, su meta googlebot o
  su ``X-Robots-Tag``. Google: «Do not follow the links on this page»; ninguno
  de sus enlaces le sirve para descubrir ni para pasar senales. Casi siempre es
  la plantilla (un plugin, una opcion del CMS), y se agrupa (53, 64, 73).
- ``enlace_interno_nofollow`` (info, por destino): enlaces internos con
  ``rel="nofollow"`` (o ``ugc``/``sponsored``, que Google trata igual) a una
  indexable. Google lo pide para enlaces que uno no respalda, y para los
  propios dice que se use robots.txt; un ``nofollow`` interno es una pista
  para no rastrear ni pasar senales a una pagina que el propio sitio quiere
  en el indice. Va por destino, como ``ancla_generica`` (147): ahi se pierde la
  senal, y el mismo enlace en el menu de cien paginas es una fila con
  ``paginas_con_nofollow`` (``alcance: plantilla`` desde
  ``enlaces_plantilla_min_paginas``), no cien.

**Lo que no se afirma**: un enlace desde una pagina que no cuenta (no 200 o
``noindex``, decisiones 38 y 57) no entra en ningun recuento; un destino que no
es indexable no avisa (un ``nofollow`` al login es lo correcto); y con un tope
por carpeta que corto (172) no se dice que una pagina solo reciba ``nofollow``,
porque la puede enlazar una que no se pidio. Lo mismo que ya hace
``not_reachable_from_home``, que es de donde sale. Tampoco en el destino de una
redireccion interna, que puede recibir ``follow`` a traves del salto.

Todo sale de la temporal de aristas de ``compute_link_counts``: ninguna lectura
mas de ``links`` (decisiones 37 y 41). La columna ``nofollow_rel`` de la
temporal distingue el ``rel`` del enlace de la directiva de la pagina: las dos
dejan ``follow=False`` y se arreglan en sitios distintos. Que ``rel`` cuenta lo
decide ``shared/rel_de_enlace.py``, el mismo modulo que usa el spider.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import aliased

logger = logging.getLogger(__name__)

MUESTRA = 5

# Los tipos que emite este modulo. Cada uno lleva su ficha en `incidencias.js`
# y su ambito en el explorador (`app.js`).
TIPOS = (
    "solo_enlazada_con_nofollow",
    "pagina_nofollow",
    "enlace_interno_nofollow",
)

# La columna ``nofollow_rel`` de la temporal de aristas la calcula
# ``shared.rel_de_enlace.sql_nofollow_por_rel``: la misma regla por tokens con
# la que el spider marca ``links.follow`` (decisiones 23 y 64).


# ---------------------------------------------------------------------------
# solo_enlazada_con_nofollow
# ---------------------------------------------------------------------------
def solo_con_nofollow(az, aristas, candidatas: list[int]) -> set[int]:
    """De las indexables sin camino desde la home que alguien enlaza
    (``candidatas``, las de ``not_reachable_from_home``), emite
    ``solo_enlazada_con_nofollow`` en las que TODOS sus entrantes que cuentan
    son ``nofollow`` y devuelve sus ids, para que no lleven ademas el otro.

    Entrantes que cuentan: internos, de otra pagina, desde una 200 que no es
    ``noindex`` (``origen_cuenta``, el mismo criterio que ``inlinks_count``).
    """
    from shared.models import Url

    from analysis.analyzer import _es_destino_de_redireccion

    if not candidatas:
        return set()
    o = aliased(Url)
    emitidas: set[int] = set()
    for i in range(0, len(candidatas), 5000):
        lote = candidatas[i:i + 5000]
        # El destino de una redireccion interna puede estar enlazado con
        # `follow` a traves del salto (los enlaces apuntan a la URL vieja y en
        # la temporal son del salto, no de ella): «solo con nofollow» no se
        # puede afirmar y se queda con `not_reachable_from_home` (67, 70).
        tras_salto = set(az.session.execute(
            select(Url.id).where(Url.id.in_(lote), Url.job_id == az.job_id,
                                 _es_destino_de_redireccion())).scalars())
        lote = [u for u in lote if u not in tras_salto]
        if not lote:
            continue
        filas = az.session.execute(
            select(
                aristas.c.destino_id,
                func.count(func.distinct(aristas.c.origen_id)).label("origenes"),
                func.count(func.distinct(case((aristas.c.seguible.is_(True), aristas.c.origen_id),
                                              else_=None))).label("seguibles"),
                func.count(func.distinct(case((aristas.c.nofollow_rel.is_(True), aristas.c.origen_id),
                                              else_=None))).label("por_rel"),
            )
            .where(aristas.c.destino_id.in_(lote), aristas.c.es_interno.is_(True),
                   aristas.c.origen_cuenta.is_(True),
                   aristas.c.origen_id != aristas.c.destino_id)
            .group_by(aristas.c.destino_id)
        ).all()
        solo = {f.destino_id: f for f in filas if f.origenes and not f.seguibles}
        if not solo:
            continue
        ejemplos: dict[int, list[str]] = defaultdict(list)
        for did, url in az.session.execute(
                select(aristas.c.destino_id, o.url).distinct()
                .join(o, o.id == aristas.c.origen_id)
                .where(aristas.c.destino_id.in_(list(solo)), aristas.c.es_interno.is_(True),
                       aristas.c.origen_cuenta.is_(True),
                       aristas.c.origen_id != aristas.c.destino_id)
                .order_by(aristas.c.destino_id, o.url)):
            if len(ejemplos[did]) < MUESTRA:
                ejemplos[did].append(url)
        for did, f in solo.items():
            por_rel = int(f.por_rel or 0)
            az._add_issue(did, "solo_enlazada_con_nofollow", "warning", {
                "paginas_que_enlazan": int(f.origenes),
                # Una pagina puede enlazarla con los dos motivos a la vez; los
                # que no son por `rel` son de la directiva de la pagina.
                "con_rel_nofollow": por_rel,
                "con_nofollow_de_pagina": int(f.origenes) - por_rel,
                "paginas_ejemplo": ejemplos[did],
            })
            emitidas.add(did)
    return emitidas


# ---------------------------------------------------------------------------
# enlace_interno_nofollow
# ---------------------------------------------------------------------------
def avisar_enlaces_nofollow(az, aristas, excluir: set[int] | None = None) -> int:
    """``enlace_interno_nofollow`` (info), una fila por destino indexable.

    ``excluir``: los destinos que ya llevan ``solo_enlazada_con_nofollow``, que
    dice lo mismo y mas (36).
    """
    from shared.models import Url

    excluir = excluir or set()
    umbral = az.enlaces_plantilla_min_paginas
    d = aliased(Url)
    base = and_(aristas.c.es_interno.is_(True), aristas.c.origen_cuenta.is_(True),
                aristas.c.destino_id.isnot(None),
                aristas.c.origen_id != aristas.c.destino_id)
    con_rel = func.count(func.distinct(case((aristas.c.nofollow_rel.is_(True), aristas.c.origen_id),
                                            else_=None)))
    # Primero los destinos con algun `nofollow` por `rel`, que son pocos, y
    # solo esos se agrupan: en penguin la temporal son 10 millones de aristas
    # y agruparla entera por destino para quedarse con un punado sobra.
    con_algun_rel = (
        select(aristas.c.destino_id).where(base, aristas.c.nofollow_rel.is_(True)).distinct()
    )
    filas = az.session.execute(
        select(
            aristas.c.destino_id,
            func.count(func.distinct(aristas.c.origen_id)).label("origenes"),
            con_rel.label("con_rel"),
            func.coalesce(func.sum(case((aristas.c.nofollow_rel.is_(True), aristas.c.veces),
                                        else_=0)), 0).label("enlaces"),
        )
        .join(d, d.id == aristas.c.destino_id)
        .where(base, aristas.c.destino_id.in_(con_algun_rel),
               d.status_code == 200, d.is_html.is_(True), d.is_internal.is_(True),
               d.indexable.is_(True))
        .group_by(aristas.c.destino_id)
        .having(con_rel > 0)
    ).all()
    filas = [f for f in filas if f.destino_id not in excluir]
    if not filas:
        return 0

    o = aliased(Url)
    ejemplos: dict[int, list[str]] = defaultdict(list)
    ids = [f.destino_id for f in filas]
    for k in range(0, len(ids), 1000):
        for did, url in az.session.execute(
                select(aristas.c.destino_id, o.url).distinct()
                .join(o, o.id == aristas.c.origen_id)
                .where(base, aristas.c.nofollow_rel.is_(True),
                       aristas.c.destino_id.in_(ids[k:k + 1000]))
                .order_by(aristas.c.destino_id, o.url)):
            if len(ejemplos[did]) < MUESTRA:
                ejemplos[did].append(url)

    for f in filas:
        con = int(f.con_rel)
        az._add_issue(f.destino_id, "enlace_interno_nofollow", "info", {
            "alcance": "plantilla" if con >= umbral else "pagina",
            "paginas_con_nofollow": con,
            "paginas_que_enlazan": int(f.origenes),
            "enlaces": int(f.enlaces or 0),
            "paginas_ejemplo": ejemplos[f.destino_id],
        })
    az._flush_issues()
    logger.info("Job %s: %d indexables con enlaces internos nofollow", az.job_id, len(filas))
    return len(filas)


# ---------------------------------------------------------------------------
# pagina_nofollow
# ---------------------------------------------------------------------------
def avisar_paginas_nofollow(az) -> int:
    """``pagina_nofollow`` (warning), agrupada por (host, plantilla).

    Solo HTML interno 200, indexable y con respuesta valida (103). Una pagina
    ``noindex, nofollow`` no entra: no es indexable y el ``noindex`` ya tiene su
    aviso. La directiva se lee con ``es_nofollow``, la misma regla que el
    spider aplica a los enlaces (23): ``index/nofollow`` no es ``nofollow``
    (eso es ``robots_invalid_syntax``) y ``bingbot: nofollow`` tampoco.
    """
    from analysis.analyzer import _respuesta_valida
    from analysis.avisos_de_html import _Grupos, _plantilla
    from shared.indexabilidad import es_nofollow
    from shared.models import HtmlMeta, Url

    def contiene(col):
        return or_(func.lower(col).like("%nofollow%"), func.lower(col).like("%none%"))

    filas = az.session.execute(
        select(Url.id, Url.url, Url.host, HtmlMeta.meta_robots,
               HtmlMeta.meta_robots_googlebot, HtmlMeta.x_robots_tag)
        .join(HtmlMeta, HtmlMeta.url_id == Url.id)
        .where(Url.job_id == az.job_id, Url.is_internal.is_(True), Url.is_html.is_(True),
               Url.status_code == 200, Url.indexable.is_(True), _respuesta_valida(),
               or_(contiene(HtmlMeta.meta_robots), contiene(HtmlMeta.meta_robots_googlebot),
                   contiene(HtmlMeta.x_robots_tag)))
    ).all()
    grupos = _Grupos()
    fuentes: dict[tuple, dict[str, Any]] = {}
    # Por URL: la directiva que se ensena es la de la pagina de URL menor del
    # grupo, la misma en la que se ancla la fila (decision 88).
    for f in sorted(filas, key=lambda f: f.url):
        meta = {"robots": f.meta_robots, "googlebot": f.meta_robots_googlebot}
        if not es_nofollow(meta_robots=meta, x_robots=f.x_robots_tag):
            continue
        clave = (f.host or "", _plantilla(f.url))
        grupos.anotar(clave, f.url, f.id)
        # La fuente y el texto de la directiva de UNA pagina del grupo: para
        # la ficha, no para decidir nada.
        if clave not in fuentes:
            fuente = ("meta_robots" if es_nofollow(meta_robots={"robots": f.meta_robots})
                      else "meta_googlebot" if es_nofollow(meta_robots={"googlebot": f.meta_robots_googlebot})
                      else "x_robots_tag")
            texto = {"meta_robots": f.meta_robots, "meta_googlebot": f.meta_robots_googlebot,
                     "x_robots_tag": f.x_robots_tag}[fuente]
            fuentes[clave] = {"fuente": fuente, "directiva": (texto or "")[:200]}
    n = grupos.emitir(az, "pagina_nofollow", lambda k: "warning",
                      lambda k: {"host": k[0], "plantilla": k[1], **fuentes[k]})
    az._flush_issues()
    if n:
        logger.info("Job %s: %d plantillas de indexables con nofollow de pagina", az.job_id, n)
    return n
