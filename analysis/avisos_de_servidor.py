"""Avisos del servidor: tiempo de respuesta, compresion y certificado TLS.

Cinco incidencias, todas AGRUPADAS (decisiones 36, 53 y 64): son
configuracion del servidor o de la plantilla, la misma en cientos de paginas,
y por pagina tapaban los hallazgos reales.

- ``respuesta_lenta`` (decision 109), por **(host, plantilla)**: la mediana
  del TTFB (`urls.ttfb_ms`, el tiempo del servidor sin DNS, TCP ni TLS) por
  encima de `respuesta_lenta_ms` (`info`) o `respuesta_lenta_grave_ms`
  (`warning`), solo en plantillas con `respuesta_lenta_min_paginas` paginas o
  mas. Un censo sin `ttfb_ms` (los anteriores al handler) no da nada: el
  `response_time_ms` no es TTFB —es la peticion entera con conexion nueva, y
  con render la espera del navegador— y caer a el seria afirmar lo que no se
  midio (decision 67).
- ``html_sin_comprimir`` (109), por **host**: HTML servido con
  `Content-Encoding` ausente (`identity`). Solo se afirma con la cabecera
  medida: con lo de antes, `transfer_size == content_length` no demuestra
  nada. Si el job pisa `Accept-Encoding` en sus cabeceras, no se dice nada.
- ``tls_certificado_invalido``, ``tls_caduca_pronto`` y
  ``tls_version_antigua`` (decision 110), por **host**, con lo que dejo la
  sonda en `jobs.tls`. Los dias son los del dia del rastreo: re-analizar un
  censo viejo no los recalcula contra hoy.

Ambito comun: HTML interno 200 que es la respuesta del sitio (decision 103).
La plantilla es la de `config.templates` del job y si no casa ninguna, la
forma de la ruta (`shared.plantillas.plantilla_de`).

Anclaje (decision 88): lo que es del host va en su raiz si se rastreo —o en
la HTML 200 a la que redirige—; lo que es de una plantilla, en la URL menor
del grupo. Nunca en el primer id que aparece, que cambia entre analisis.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)

MUESTRA = 5

# Por debajo de esto un servidor no comprime aunque sepa (nginx
# `gzip_min_length`, CDNs con umbrales de 1 KB): no es un hallazgo.
COMPRESION_MIN_BYTES = 1024

TIPOS = (
    "respuesta_lenta",
    "html_sin_comprimir",
    "tls_certificado_invalido",
    "tls_caduca_pronto",
    "tls_version_antigua",
)


def avisar(an) -> None:
    """Los cinco avisos, sobre un `SEOAnalyzer` (`an`)."""
    an._flush_issues()
    avisar_respuesta_lenta(an)
    avisar_html_sin_comprimir(an)
    avisar_tls(an)
    an._flush_issues()


# -- Helpers -------------------------------------------------------------

def _ambito_html(an):
    from analysis.analyzer import _respuesta_valida
    from shared.models import Url

    return (Url.job_id == an.job_id, Url.is_internal.is_(True), Url.is_html.is_(True),
            Url.status_code == 200, _respuesta_valida())


def ancla_de_host(an, host: str, respaldo: list[tuple[str, int]]) -> int | None:
    """La URL donde se ancla un hallazgo del host (decision 88).

    La raiz del host si se rastreo (`https` antes que `http`) siguiendo sus
    redirecciones dentro del censo hasta una HTML 200; si no hay, la raiz tal
    cual (con un certificado roto no hay ninguna 200); si tampoco, la URL menor
    de `respaldo` (pares `(url, id)`).
    """
    from shared.models import Url

    filas = an.session.execute(
        select(Url.id, Url.url, Url.status_code, Url.is_html, Url.redirect_url)
        .where(Url.job_id == an.job_id, Url.host == host,
               Url.url.in_([f"https://{host}/", f"http://{host}/"]))
    ).all()
    raiz = min(filas, key=lambda f: (not f.url.startswith("https"), f.url), default=None)
    if raiz is not None:
        actual, vistas = raiz, {raiz.url}
        for _ in range(5):
            if actual.status_code == 200 and actual.is_html:
                return actual.id
            if not actual.redirect_url or actual.redirect_url in vistas:
                break
            vistas.add(actual.redirect_url)
            siguiente = an.session.execute(
                select(Url.id, Url.url, Url.status_code, Url.is_html, Url.redirect_url)
                .where(Url.job_id == an.job_id, Url.url == actual.redirect_url)
            ).first()
            if siguiente is None:
                break
            actual = siguiente
        return raiz.id
    if respaldo:
        return min(respaldo)[1]
    return None


def _p90(valores: list[float]) -> float:
    orden = sorted(valores)
    return orden[min(len(orden) - 1, int(round(0.9 * (len(orden) - 1))))]


# -- respuesta_lenta -----------------------------------------------------

def avisar_respuesta_lenta(an) -> None:
    from analysis.analyzer import _clave_de_grupo
    from shared.models import Url
    from shared.plantillas import plantilla_de, reglas_de_plantilla

    config = an._job_config
    reglas = reglas_de_plantilla(config.get("templates"))
    grupos: dict[tuple[str, str], list[tuple[float, str, int]]] = defaultdict(list)
    for uid, url, host, path, ttfb in an._iter_urls(
            *_ambito_html(an)[1:], Url.ttfb_ms.isnot(None),
            columns=(Url.id, Url.url, Url.host, Url.path, Url.ttfb_ms)):
        grupos[(host or "", plantilla_de(path or "/", reglas))].append((float(ttfb), url, uid))
    if not grupos:
        return
    umbral, grave = an.respuesta_lenta_ms, an.respuesta_lenta_grave_ms
    minimo = an.respuesta_lenta_min_paginas
    cb = config.get("crawl_behavior") or {}
    for (host, plantilla), filas in sorted(grupos.items()):
        if len(filas) < minimo:
            continue
        tiempos = [t for t, _, _ in filas]
        mediana = statistics.median(tiempos)
        if mediana <= umbral:
            continue
        lentas = sorted(filas, key=lambda f: (-f[0], f[1]))
        an._add_issue(
            min((u, i) for _, u, i in filas)[1],
            "respuesta_lenta", "warning" if mediana > grave else "info",
            {"host": host, "plantilla": plantilla, "paginas": len(filas),
             "mediana_ms": round(mediana, 1), "p90_ms": round(_p90(tiempos), 1),
             "umbral_ms": grave if mediana > grave else umbral,
             # Con render es Navigation Timing; sin el, curl (decision 109).
             "fuente": "navegador" if config.get("render_js") else "curl",
             # Mas concurrencia, mas cola en el servidor: la cifra depende de
             # como se rastreo, y quien la lea tiene que saberlo.
             "concurrencia_por_dominio": config.get("concurrent_requests_per_domain"),
             "autothrottle": cb.get("autothrottle_enabled", True),
             "paginas_ejemplo": [u for _, u, _ in lentas[:MUESTRA]]},
            group_key=_clave_de_grupo(f"{host}|{plantilla}"),
        )


# -- html_sin_comprimir --------------------------------------------------

def _pisa_accept_encoding(config: dict) -> bool:
    cabeceras = ((config.get("http") or {}).get("custom_headers") or {})
    return any(str(k).strip().lower() == "accept-encoding" for k in cabeceras)


def avisar_html_sin_comprimir(an) -> None:
    from analysis.analyzer import _clave_de_grupo
    from shared.models import Url
    from shared.plantillas import plantilla_de, reglas_de_plantilla

    config = an._job_config
    if _pisa_accept_encoding(config):
        # Lo que se pidio no es lo que pide un navegador ni Googlebot: lo que
        # sirvio el servidor no dice nada de su configuracion.
        logger.info("Job %s: Accept-Encoding del job, no se juzga la compresion", an.job_id)
        return
    reglas = reglas_de_plantilla(config.get("templates"))
    por_host: dict[str, dict[str, Any]] = {}
    for uid, url, host, path, encoding, tam in an._iter_urls(
            *_ambito_html(an)[1:], Url.content_encoding.isnot(None),
            Url.content_length >= COMPRESION_MIN_BYTES,
            columns=(Url.id, Url.url, Url.host, Url.path, Url.content_encoding,
                     Url.content_length)):
        g = por_host.setdefault(host or "", {"paginas": 0, "afectadas": [],
                                             "plantillas": defaultdict(int)})
        g["paginas"] += 1
        if (encoding or "").lower() == "identity":
            g["afectadas"].append((url, uid, tam))
            g["plantillas"][plantilla_de(path or "/", reglas)] += 1
    for host, g in sorted(por_host.items()):
        afectadas = g["afectadas"]
        if not afectadas:
            continue
        detalle: dict[str, Any] = {
            "host": host, "paginas_afectadas": len(afectadas),
            "paginas_del_host": g["paginas"],
            "bytes_medianos": int(statistics.median(t for _, _, t in afectadas)),
            "paginas_ejemplo": [u for u, _, _ in sorted(afectadas)[:MUESTRA]],
        }
        if len(afectadas) < g["paginas"]:
            # El host mezcla: casi siempre una plantilla que sale por otro
            # camino (otra aplicacion, un proxy distinto). Por donde mirar.
            detalle["por_plantilla"] = dict(sorted(
                g["plantillas"].items(), key=lambda kv: (-kv[1], kv[0]))[:MUESTRA])
        an._add_issue(
            ancla_de_host(an, host, [(u, i) for u, i, _ in afectadas]),
            "html_sin_comprimir", "info", detalle,
            group_key=_clave_de_grupo(host),
        )


# -- TLS -------------------------------------------------------------------

MOTIVOS_INVALIDO = frozenset({"caducado", "aun_no_valido", "nombre", "autofirmado",
                              "cadena_incompleta", "revocado", "otro"})


def _fichas_tls(an) -> dict[str, dict]:
    tls = an._job_tls if isinstance(an._job_tls, dict) else {}
    hosts = tls.get("hosts") if isinstance(tls.get("hosts"), dict) else {}
    return {h: f for h, f in hosts.items() if isinstance(f, dict)}


def _hosts_que_respondieron(an) -> set[str]:
    """Hosts con alguna respuesta 2xx por https: alli el TLS si funciono."""
    from shared.models import Url

    filas = an.session.execute(
        select(Url.host).where(Url.job_id == an.job_id, Url.scheme == "https",
                               Url.status_code >= 200, Url.status_code < 300)
        .group_by(Url.host)
    ).all()
    return {f[0] for f in filas if f[0]}


def hosts_con_certificado_invalido(an) -> dict[str, dict]:
    """`{host: ficha}` de los hosts cuyo certificado no vale.

    Lo usa tambien `analyze_status_codes`: una pagina `tls_error` de uno de
    estos hosts no lleva ademas su `connection_error` (36). Si el host
    respondio 2xx por https en el rastreo, curl o Chromium SI verificaron el
    certificado: el fallo de la sonda es del almacen de CA de la maquina o de
    un WAF que corta lo que no parece un navegador, y no se afirma.
    """
    fichas = _fichas_tls(an)
    if not fichas:
        return {}
    candidatos = {}
    for host, f in fichas.items():
        caducado = isinstance(f.get("dias_restantes"), (int, float)) and f["dias_restantes"] < 0
        if (f.get("verificado") is False and f.get("motivo") in MOTIVOS_INVALIDO) or caducado:
            candidatos[host] = dict(f, motivo=f.get("motivo") or "caducado")
    if not candidatos:
        return {}
    respondieron = _hosts_que_respondieron(an)
    for host in list(candidatos):
        if host in respondieron:
            logger.info("Sonda TLS de %s: %s, pero el host respondio 2xx; no se afirma",
                        host, candidatos[host].get("motivo"))
            del candidatos[host]
    return candidatos


def _detalle_tls(host: str, f: dict) -> dict[str, Any]:
    return {k: v for k, v in {
        "host": host, "motivo": f.get("motivo"), "detalle": f.get("detalle"),
        "not_after": f.get("not_after"), "dias_restantes": f.get("dias_restantes"),
        "version": f.get("version"), "emisor": f.get("emisor"),
        "fuente": f.get("fuente"), "fecha_sonda": f.get("fecha"),
    }.items() if v is not None}


def avisar_tls(an) -> None:
    from analysis.analyzer import _clave_de_grupo
    from shared.models import Url

    fichas = _fichas_tls(an)
    if not fichas:
        return
    invalidos = hosts_con_certificado_invalido(an)
    errores_tls: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for uid, url, host in an.session.execute(
            select(Url.id, Url.url, Url.host)
            .where(Url.job_id == an.job_id, Url.status_group == "tls_error")).all():
        errores_tls[host or ""].append((url, uid))
    for host, f in sorted(fichas.items()):
        clave = _clave_de_grupo(host)
        avisos: list[tuple[str, str, dict]] = []
        dias = f.get("dias_restantes")
        if host in invalidos:
            detalle = _detalle_tls(host, invalidos[host])
            if errores_tls.get(host):
                detalle["paginas_con_error_tls"] = len(errores_tls[host])
                detalle["paginas_ejemplo"] = [u for u, _ in sorted(errores_tls[host])[:MUESTRA]]
            avisos.append(("tls_certificado_invalido", "error", detalle))
        elif isinstance(dias, (int, float)) and 0 <= dias <= an.tls_dias_aviso:
            avisos.append(("tls_caduca_pronto", "warning",
                           dict(_detalle_tls(host, f), umbral_dias=an.tls_dias_aviso)))
        if f.get("version_antigua"):
            avisos.append(("tls_version_antigua", "error", _detalle_tls(host, f)))
        if not avisos:
            continue
        respaldo = errores_tls.get(host) or [
            (u, i) for i, u in an.session.execute(
                select(Url.id, Url.url).where(Url.job_id == an.job_id, Url.host == host)
                .order_by(Url.url).limit(1)).all()]
        ancla = ancla_de_host(an, host, respaldo)
        if ancla is None:
            # Sin ninguna URL del host en el censo no hay donde anclarlo
            # (`issues.url_id` es NOT NULL); la ficha sigue en `jobs.tls`.
            continue
        for tipo, severidad, detalle in avisos:
            an._add_issue(ancla, tipo, severidad, detalle, group_key=clave)


def ids_tls_de_hosts_invalidos(an) -> set[int]:
    """Las paginas `tls_error` que ya cuenta `tls_certificado_invalido`."""
    from shared.models import Url

    invalidos = hosts_con_certificado_invalido(an)
    if not invalidos:
        return set()
    return {i for (i,) in an.session.execute(
        select(Url.id).where(Url.job_id == an.job_id, Url.status_group == "tls_error",
                             Url.host.in_(sorted(invalidos)))).all()}


__all__ = ["TIPOS", "avisar", "ancla_de_host", "hosts_con_certificado_invalido",
           "ids_tls_de_hosts_invalidos", "COMPRESION_MIN_BYTES"]

