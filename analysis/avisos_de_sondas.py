"""Avisos de las sondas de respuesta (decisiones 123 y 124).

Tres incidencias, de lo que el spider dejo en `respuestas_sondeadas`:

- ``enlace_externo_roto``: una URL de OTRO sitio que el sitio enlaza y responde
  404/410/4xx (o redirige a uno). **Una por destino, no por aparicion ni por
  pagina**: el mismo enlace roto en el pie de 3.000 paginas es un hallazgo, que
  se arregla una vez; la incidencia lleva `paginas_afectadas`, los `enlaces`
  (``SUM(veces)``, decision 94) y ejemplos de paginas que lo enlazan.
- ``dominio_externo_muerto``: un host al que el spider no llego (DNS que no
  existe, confirmado con el resolver, o dos fallos de conexion) y del que nunca
  hubo una respuesta HTTP. Una por host. Sus URLs NO llevan ademas
  ``enlace_externo_roto``: es el mismo hallazgo (36).
- ``broken_resource`` (#101): un recurso embebido (imagen, CSS, JS) que
  responde 4xx o cuyo host esta caido. Uno por recurso, con
  `paginas_afectadas` (53, 64).

Lo que NO se avisa, y sale como dato en la tabla: `no_verificable` (403, 429,
999, desafios de WAF), `error_servidor` (5xx), `tiempo_agotado`,
`tls_invalido` y `prohibido_por_robots`. Un enlace a un tercero que el WAF no
deja ver no esta roto, y afirmarlo es lo que la decision 67 prohibe.

Una URL que esta EN el censo (con `follow_external`, o un recurso enlazado con
un `<a>`) no se avisa aqui: tiene su codigo en `urls` y su `4xx_error` (36).

Las incidencias no tienen pagina propia: el destino no es del censo. Se anclan
en la pagina de URL MENOR que enlaza o carga (decision 88), con `group_key` del
destino (o del host), para que la comparacion entre censos identifique el
hallazgo aunque cambie el orden de rastreo.

Los enlaces salen de la temporal de aristas de ``compute_link_counts`` (columna
``externo_hash``), sin otra lectura de `links` (decisiones 37 y 41).
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import func, select, update

from shared import sondas as S

logger = logging.getLogger(__name__)

TIPOS = ("enlace_externo_roto", "dominio_externo_muerto", "broken_resource")

MUESTRA = 5
LOTE = 2000
# Si mas de la mitad de los hosts externos (y al menos estos) «no existen», lo
# que ha fallado es el resolver del rastreo, no Internet: no se afirma nada.
MIN_HOSTS_RESOLVER = 10


def _clave(valor: str | None) -> str | None:
    from analysis.analyzer import _clave_de_grupo

    return _clave_de_grupo(valor)


def hay_sondas(az) -> bool:
    from shared.models import RespuestaSondeada

    try:
        # En un SAVEPOINT: en una base sin la tabla, el fallo no puede llevarse
        # la transaccion del analisis entero.
        with az.session.begin_nested():
            return az.session.execute(
                select(RespuestaSondeada.id).where(RespuestaSondeada.job_id == az.job_id)
                .limit(1)
            ).first() is not None
    except Exception:  # noqa: BLE001 -- una base sin la tabla: no hay sondas
        return False


def _filas(az) -> list:
    from shared.models import RespuestaSondeada as R

    return az.session.execute(
        select(R.url_hash, R.url, R.host, R.uso, R.interno, R.status_code, R.final_url,
               R.final_status, R.veredicto, R.host_caido)
        .where(R.job_id == az.job_id)
    ).all()


def _en_el_censo(az, hashes: set[str]) -> set[str]:
    from shared.models import Url

    dentro: set[str] = set()
    lista = sorted(hashes)
    for i in range(0, len(lista), LOTE):
        dentro.update(h for (h,) in az.session.execute(
            select(Url.url_hash).where(Url.job_id == az.job_id,
                                       Url.url_hash.in_(lista[i:i + LOTE]))))
    return dentro


def hosts_muertos(filas) -> dict[str, list]:
    """Hosts externos dados por caidos y de los que nunca hubo respuesta HTTP.

    Hace falta las dos cosas: al menos una fila con `host_caido` y un veredicto
    de los de `MUERTOS`, y ninguna fila con otra cosa que un fallo de red (una
    sola respuesta, aunque sea un 404, dice que el dominio existe y contesta).
    """
    por_host: dict[str, list] = defaultdict(list)
    for f in filas:
        if not f.interno and f.host:
            por_host[f.host].append(f)
    sin_respuesta = S.MUERTOS | {S.TIEMPO_AGOTADO}
    muertos = {
        h: fs for h, fs in por_host.items()
        if any(f.host_caido and f.veredicto in S.MUERTOS for f in fs)
        and all(f.veredicto in sin_respuesta for f in fs)
    }
    inexistentes = [h for h, fs in muertos.items()
                    if any(f.veredicto == S.DOMINIO_INEXISTENTE for f in fs)]
    if len(inexistentes) >= MIN_HOSTS_RESOLVER and len(inexistentes) * 2 > len(por_host):
        logger.warning(
            "Sondas: %d de %d hosts externos «no existen» segun el DNS del rastreo: "
            "parece un fallo del resolver, no se avisa ningun dominio inexistente",
            len(inexistentes), len(por_host))
        for h in inexistentes:
            muertos.pop(h, None)
    return muertos


def _dominios_del_sitio(az) -> set[str]:
    from urllib.parse import urlsplit

    from shared.dominios import dominio_registrable
    from shared.models import Job

    job = az.session.query(Job).filter(Job.id == az.job_id).one_or_none()
    salida = set()
    for semilla in (getattr(job, "seeds", None) or []):
        host = (urlsplit(semilla).hostname or "").lower()
        if host:
            salida.add(dominio_registrable(host))
    return salida


def _ejemplos(origenes: dict[int, list]) -> tuple[int, list[str], int]:
    """(id de la pagina de URL menor, URLs de ejemplo, enlaces)."""
    ordenados = sorted(origenes.items(), key=lambda kv: kv[1][0] or "")
    ancla = ordenados[0][0]
    enlaces = sum(v[1] for v in origenes.values())
    return ancla, [v[0] for _, v in ordenados[:MUESTRA]], enlaces


# ---------------------------------------------------------------------------
# Enlaces externos
# ---------------------------------------------------------------------------
def avisar_enlaces_externos(az, aristas) -> None:
    """``enlace_externo_roto`` y ``dominio_externo_muerto``, y el resumen.

    ``aristas`` es la temporal de ``compute_link_counts``: ya solo tiene los
    enlaces de este job, y ``externo_hash`` es el ``to_url_hash`` de los
    externos. Se une con ``urls`` por la clave primaria del origen, que no es lo
    que prohibe la decision 37.
    """
    from shared.dominios import dominio_registrable
    from shared.models import RespuestaSondeada, Url

    resumen: dict[str, Any] = {}
    filas = _filas(az)
    externas = [f for f in filas if not f.interno]

    # Cuantos destinos externos distintos hay y cuantos se comprobaron: sin
    # esto, «ningun aviso» en un censo con el tope alcanzado se leeria como
    # «ningun enlace roto».
    distintos = az.session.execute(
        select(func.count(func.distinct(aristas.c.externo_hash)))
        .where(aristas.c.externo_hash.isnot(None))
    ).scalar() or 0
    comprobados = az.session.execute(
        select(func.count(func.distinct(aristas.c.externo_hash)))
        .select_from(aristas)
        .join(RespuestaSondeada, (RespuestaSondeada.url_hash == aristas.c.externo_hash)
              & (RespuestaSondeada.job_id == az.job_id))
    ).scalar() or 0

    muertos = hosts_muertos(externas)
    hash_a_host = {f.url_hash: h for h, fs in muertos.items() for f in fs}
    candidatos = {f.url_hash: f for f in externas
                  if f.veredicto in S.ROTOS and f.host not in muertos}
    en_censo = _en_el_censo(az, set(candidatos) | set(hash_a_host))
    rotos = {h: f for h, f in candidatos.items() if h not in en_censo}
    hash_a_host = {h: host for h, host in hash_a_host.items() if h not in en_censo}

    # (destino -> origen -> [url del origen, enlaces]). Origen: pagina HTML que
    # responde 200, como en los avisos de enlaces internos (57); un noindex SI
    # cuenta: el enlace lo pulsa igual una persona.
    por_destino: dict[str, dict[int, list]] = defaultdict(dict)
    objetivo = sorted(set(rotos) | set(hash_a_host))
    for i in range(0, len(objetivo), LOTE):
        for h, oid, ourl, veces in az.session.execute(
            select(aristas.c.externo_hash, Url.id, Url.url, func.sum(aristas.c.veces))
            .select_from(aristas)
            .join(Url, Url.id == aristas.c.origen_id)
            .where(aristas.c.externo_hash.in_(objetivo[i:i + LOTE]),
                   Url.status_code == 200, Url.is_html.is_(True))
            .group_by(aristas.c.externo_hash, Url.id, Url.url)
        ):
            por_destino[h][oid] = [ourl, int(veces or 0)]

    propios = _dominios_del_sitio(az)
    emitidos_rotos = 0
    for h, f in sorted(rotos.items(), key=lambda kv: kv[1].url):
        origenes = por_destino.get(h)
        if not origenes:
            continue
        ancla, ejemplos, enlaces = _ejemplos(origenes)
        az._add_issue(ancla, "enlace_externo_roto", "warning", {
            "destino": f.url, "veredicto": f.veredicto, "status_code": f.status_code,
            "final_url": f.final_url, "final_status": f.final_status,
            "paginas_afectadas": len(origenes), "enlaces": enlaces, "ejemplos": ejemplos,
            # Con crawl_subdomains=false, `blog.x.com` es externo para `x.com`:
            # un enlace roto al propio blog pesa mas.
            "misma_casa": dominio_registrable(f.host or "") in propios,
        }, group_key=_clave(f.url))
        emitidos_rotos += 1
        az.sondas_avisadas.add(h)

    emitidos_muertos = 0
    for host, fs in sorted(muertos.items()):
        origenes: dict[int, list] = {}
        urls_enlazadas = []
        for f in sorted(fs, key=lambda x: x.url):
            de_este = por_destino.get(f.url_hash) or {}
            if de_este:
                urls_enlazadas.append(f.url)
            for oid, (ourl, veces) in de_este.items():
                if oid in origenes:
                    origenes[oid][1] += veces
                else:
                    origenes[oid] = [ourl, veces]
        if not origenes:
            continue  # solo lo embebe una pagina: lo dice broken_resource
        ancla, ejemplos, enlaces = _ejemplos(origenes)
        veredicto = (S.DOMINIO_INEXISTENTE if any(f.veredicto == S.DOMINIO_INEXISTENTE for f in fs)
                     else S.SIN_CONEXION)
        az._add_issue(ancla, "dominio_externo_muerto", "warning", {
            "host": host, "veredicto": veredicto, "urls_distintas": len(urls_enlazadas),
            "ejemplos_destino": urls_enlazadas[:MUESTRA],
            "paginas_afectadas": len(origenes), "enlaces": enlaces, "ejemplos": ejemplos,
            "misma_casa": dominio_registrable(host) in propios,
        }, group_key=_clave(host))
        emitidos_muertos += 1
        az.sondas_avisadas.update(f.url_hash for f in fs)

    if filas or distintos:
        resumen = {
            "distintos": int(distintos), "comprobados": int(comprobados),
            "rotos": emitidos_rotos, "dominios_muertos": emitidos_muertos,
            "por_veredicto": dict(Counter(f.veredicto for f in externas if f.uso == "enlace")),
        }
    az.sondas_resumen["enlaces_externos"] = resumen
    az._flush_issues()


# ---------------------------------------------------------------------------
# Recursos embebidos (#101)
# ---------------------------------------------------------------------------
def avisar_recursos_rotos(az) -> None:
    """``broken_resource`` por recurso, y el peso de los recursos de paso."""
    from shared.models import Resource, Url

    filas = _filas(az) if hay_sondas(az) else []
    if filas:
        rellenar_tamanos(az)
    muertos = hosts_muertos(filas)
    candidatos = {
        f.url: f for f in filas
        if f.url_hash not in az.sondas_avisadas
        and (f.veredicto in S.ROTOS or (f.host in muertos and f.veredicto in S.MUERTOS))
    }
    if candidatos:
        en_censo = _en_el_censo(az, {f.url_hash for f in candidatos.values()})
        candidatos = {u: f for u, f in candidatos.items() if f.url_hash not in en_censo}

    por_recurso: dict[str, dict[int, list]] = defaultdict(dict)
    tipo: dict[str, str] = {}
    lista = sorted(candidatos)
    for i in range(0, len(lista), LOTE):
        for rurl, rtipo, uid, uurl in az.session.execute(
            select(Resource.resource_url, Resource.resource_type, Url.id, Url.url)
            .join(Url, Url.id == Resource.url_id)
            .where(Url.job_id == az.job_id, Resource.resource_url.in_(lista[i:i + LOTE]))
        ):
            por_recurso[rurl][uid] = [uurl, 1]
            tipo.setdefault(rurl, rtipo)

    emitidos = 0
    for rurl in sorted(por_recurso):
        f = candidatos[rurl]
        ancla, ejemplos, _ = _ejemplos(por_recurso[rurl])
        az._add_issue(ancla, "broken_resource", "warning", {
            "recurso": rurl, "resource_type": tipo.get(rurl), "veredicto": f.veredicto,
            "status_code": f.status_code, "final_url": f.final_url,
            "final_status": f.final_status, "interno": bool(f.interno),
            "paginas_afectadas": len(por_recurso[rurl]), "ejemplos": ejemplos,
        }, group_key=_clave(rurl))
        emitidos += 1

    recursos = [f for f in filas if f.uso == "recurso"]
    if recursos:
        az.sondas_resumen["recursos"] = {
            "comprobados": len(recursos), "rotos": emitidos,
            "por_veredicto": dict(Counter(f.veredicto for f in recursos)),
        }
    az._flush_issues()


def rellenar_tamanos(az) -> int:
    """`resources.size_bytes` con lo que dijo la sonda (`Content-Length` o el
    total de `Content-Range`). Los recursos que el censo SI rastreo como URL
    ya tienen su peso en `urls.transfer_size`; los sondeados no tenian ninguno.
    """
    from shared.models import Resource, RespuestaSondeada as R, Url

    try:
        with az.session.begin_nested():
            res = az.session.execute(
                update(Resource)
                .where(Resource.url_id == Url.id, Url.job_id == az.job_id,
                       R.job_id == az.job_id, R.url == Resource.resource_url,
                       R.content_length.isnot(None))
                .values(size_bytes=R.content_length)
                .execution_options(synchronize_session=False)
            )
        return int(res.rowcount or 0)
    except Exception:  # noqa: BLE001 -- el peso es un dato de paso, no el analisis
        logger.warning("No se pudo rellenar resources.size_bytes", exc_info=True)
        return 0
