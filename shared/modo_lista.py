"""Modo lista y veredicto pasa/falla para QA de despliegues (decision 128).

Un rastreo con `max_depth=0` es una LISTA: solo las semillas, sin seguir un
enlace y sin sitemap salvo que se pida (`api/schemas.JobConfig`). Sirve para la
pregunta que se hace despues de cada despliegue: «estas 50 URLs clave siguen
respondiendo, siguen indexables y conservan su canonical?».

Tres piezas, las tres aqui para tocar `shared/comparacion.py` lo minimo:

- `umbral_absoluto(config)`: con cuantas paginas salta una alerta en una lista.
  El piso de 20 de la decision 68 existe para no alertar por la variacion
  normal de un sitio de miles de paginas (decision 13); en una lista cada URL
  la eligio alguien, y con ese piso 10 de 50 URLs clave fuera del indice no
  alertaban.
- `acotar_a_la_lista(...)`: la referencia de una lista puede ser un censo
  completo del sitio; solo cuentan sus URLs que estan en la lista.
- `veredicto(db, job)`: `pasa`, `falla` o `no_concluyente`, con los motivos.
  Lo no concluyente NO es `pasa`: un CI que lee «pasa» despliega.

Vive en `shared/` porque lo usan el worker (la comparacion automatica) y la API
(el endpoint), y la imagen de la API no lleva `analysis/` ni `crawler/`.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from shared.comparacion import (
    MOTIVOS_TRUNCADO,
    MUESTRA,
    _canonical_que_cuenta,
    _era_indexable,
    _norm,
)
from shared.respuestas import MOTIVOS_BLOQUEO, NO_VALIDAS

UMBRAL_LISTA_POR_DEFECTO = 1

PASA, FALLA, NO_CONCLUYENTE = "pasa", "falla", "no_concluyente"

# Por que un rastreo fallido SI es un veredicto: robots.txt prohibiendo las
# semillas es el despliegue roto mas clasico (`Disallow: /` de preproduccion
# llegando a produccion), no un rastreo que no se pudo hacer.
FALLOS_DEL_SITIO = frozenset({"robots_bloquea_todo"})


def es_lista(config: dict | None) -> bool:
    """Un censo es una lista si no sigue enlaces: `max_depth == 0`."""
    return (config or {}).get("max_depth") == 0


def umbral_absoluto(config: dict | None) -> int | None:
    """Paginas con las que salta una alerta en una lista; None si no es lista."""
    if not es_lista(config):
        return None
    umbrales = (config or {}).get("analysis_thresholds") or {}
    valor = umbrales.get("alerta_lista_min_paginas")
    try:
        valor = int(valor) if valor is not None else UMBRAL_LISTA_POR_DEFECTO
    except (TypeError, ValueError):
        valor = UMBRAL_LISTA_POR_DEFECTO
    return max(1, valor)


def acotar_a_la_lista(paginas_ref, resumen_ref, job):
    """La referencia recortada a las URLs de la lista, si `job` es una lista.

    Sin esto, comparar una lista de 50 con el censo completo del sitio daba
    «desaparecen» todas las demas paginas del sitio: no estan porque no se
    pidieron. Y las semillas de la referencia se recortan igual, para que el
    guardia de alcance (decision 70) no marque no concluyente una lista que ha
    quitado una URL que ya no le interesa.
    """
    if not es_lista(getattr(job, "config", None)):
        return paginas_ref, resumen_ref
    mias = {_norm(u) for u in (job.seeds or [])}
    paginas = [p for p in paginas_ref if _norm(p.get("url")) in mias]
    resumen = dict(resumen_ref)
    if resumen.get("semillas") is not None:
        resumen["semillas"] = [u for u in resumen["semillas"] if _norm(u) in mias]
    return paginas, resumen


def _paginas_de(db, job_id, semillas) -> dict[str, dict[str, Any]]:
    """Las filas de `semillas` en un censo, por URL normalizada.

    Sin filtrar por `is_html`, al reves que `paginas_de_censo`: una URL clave
    que ahora responde 500 en texto plano o es un salto no es HTML, y es justo
    la que hay que ver. Se busca por la URL y por el hash de su forma
    normalizada, que es lo que guarda el spider.
    """
    from shared.models import HtmlMeta, Url

    if job_id is None or not semillas:
        return {}
    if not isinstance(job_id, uuid.UUID):
        # `jobs.comparacion.comparado_con` es texto (JSON); la columna, UUID.
        try:
            job_id = uuid.UUID(str(job_id))
        except ValueError:
            return {}
    claves = {_norm(u) for u in semillas if u}
    textos = set(semillas) | claves
    hashes = {hashlib.sha256(c.encode("utf-8")).hexdigest() for c in claves}
    salida: dict[str, dict[str, Any]] = {}

    def _cargar(columna, valores):
        valores = list(valores)
        for i in range(0, len(valores), 500):
            filas = (
                db.query(Url.url, Url.status_code, Url.indexability_status,
                         Url.response_class, Url.redirect_url,
                         HtmlMeta.canonical_href, HtmlMeta.canonical_distintos)
                .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
                .filter(Url.job_id == job_id, columna.in_(valores[i:i + 500]))
                .all()
            )
            for f in filas:
                clave = _norm(f.url)
                if clave in claves:
                    salida[clave] = {
                        "url": f.url, "status_code": f.status_code,
                        "indexability_status": f.indexability_status,
                        "response_class": f.response_class,
                        "redirect_url": f.redirect_url,
                        "canonical_href": f.canonical_href,
                        "canonical_distintos": f.canonical_distintos,
                    }

    _cargar(Url.url, textos)
    if len(salida) < len(claves):
        _cargar(Url.url_hash, hashes)
    return salida


def _destino_canonico(pagina: dict[str, Any]) -> str:
    """A donde consolida Google la pagina: su canonical, o ella misma."""
    return _norm(_canonical_que_cuenta(pagina) or pagina.get("url"))


def _valida(p: dict[str, Any] | None) -> bool:
    return (p is not None and p.get("status_code") == 200
            and p.get("response_class") not in NO_VALIDAS)


def _cambio(r: dict[str, Any], p: dict[str, Any]) -> str | None:
    """Que cambio de `r` a `p` (las dos 200) es un fallo; una causa (36)."""
    if _destino_canonico(r) != _destino_canonico(p):
        return "cambia_el_canonical"
    if _era_indexable(r) and not _era_indexable(p):
        return "deja_de_ser_indexable"
    return None


# Cuantos censos hacia atras se sigue la cadena de `comparado_con`.
MAX_CADENA = 10


def _referencias(db, comparado_con, semillas, maximo=MAX_CADENA) -> dict[str, dict[str, Any]]:
    """Para cada URL de la lista, su ULTIMO ESTADO QUE PASO, no la del censo anterior.

    Comparar solo con el anterior pone en verde lo que sigue roto: el CI falla
    porque 10 URLs clave pasan a noindex, alguien relanza la lista, el censo
    nuevo se compara con el roto, no hay cambio y sale `pasa`. Se sigue la
    cadena de `jobs.comparacion.comparado_con` (hasta `MAX_CADENA` censos) y se
    recorre del mas antiguo al mas reciente: el estado de una URL solo pasa a
    ser su referencia si ese censo no fallaba en ella. Un cambio intencionado
    (una URL que se quiere en noindex) falla hasta que se quita de la lista,
    que es lo que dice una lista de URLs clave (o durante `MAX_CADENA` censos:
    pasado eso, el estado roto mas antiguo de la ventana se toma por bueno).
    Fuera de una lista (`maximo`
    1) solo el anterior: las semillas de un censo completo pueden ser 87.000.
    """
    from shared.models import Job

    cadena: list[str] = []
    actual = comparado_con
    while actual and len(cadena) < maximo and str(actual) not in cadena:
        cadena.append(str(actual))
        try:
            j = db.get(Job, uuid.UUID(str(actual)))
        except ValueError:
            j = None
        comp = getattr(j, "comparacion", None) if j is not None else None
        actual = comp.get("comparado_con") if isinstance(comp, dict) and comp.get("comparable") else None
    referencia: dict[str, dict[str, Any]] = {}
    for censo in reversed(cadena):
        for clave, p in _paginas_de(db, censo, semillas).items():
            if not _valida(p):
                continue  # un fallo (o un muro) no es referencia
            p = {**p, "_censo": censo}
            r = referencia.get(clave)
            if r is None or _cambio(r, p) is None:
                referencia[clave] = p
    return referencia


MENSAJES = {
    "alerta": "alerta critica de la comparacion con el censo anterior",
    "bloqueada_por_robots": "robots.txt prohibe rastrear estas URLs de la lista",
    "no_responde_200": "estas URLs de la lista no responden 200",
    "deja_de_ser_indexable": "estas URLs eran indexables y han dejado de serlo",
    "cambia_el_canonical": "estas URLs han cambiado de canonical",
    "rastreo_bloqueado_por_robots": "robots.txt prohibe rastrear las semillas",
    # no concluyente
    "sin_terminar": "el rastreo no ha terminado",
    "rastreo_fallido": "el rastreo ha fallado",
    "bloqueado": "el rastreo se hizo detras de un muro (WAF, captcha o limite)",
    "truncado": "el rastreo no termino entero",
    "sin_referencia": "no hay censo anterior con el que comparar",
    "comparacion_no_concluyente": "la comparacion con el censo anterior no es concluyente",
    "no_rastreadas": "estas URLs de la lista no se llegaron a rastrear",
    "muro": "en estas URLs se recibio un muro, no la pagina",
    "sin_referencia_url": "estas URLs no estaban (o no respondian 200) en el censo anterior",
}


# La regla de la comparacion (`shared.comparacion.REGLAS`) y el grupo URL a URL
# del veredicto que dicen lo mismo de una pagina.
EQUIVALENTE = {
    "rotas": "no_responde_200",
    "salen_del_indice": "deja_de_ser_indexable",
    "canonical_a_otra_url": "cambia_el_canonical",
    "canonical_a_otro_host": "cambia_el_canonical",
}


def _grupo(tipo, filas=None, mensaje=None, **extra):
    filas = filas or []
    g = {"tipo": tipo, "mensaje": mensaje or MENSAJES[tipo], "paginas": len(filas),
         "ejemplos": filas[:MUESTRA]}
    g.update(extra)
    return g


def veredicto(db, job) -> dict[str, Any]:
    """`pasa`, `falla` o `no_concluyente` para un rastreo, con los motivos.

    `falla` si hay cualquier cosa AFIRMABLE: una alerta critica de la
    comparacion automatica (`jobs.comparacion`), o una URL de la lista (las
    semillas) que no responde 200, que robots.txt prohibe, que era indexable y
    ya no lo es, o que cambia de canonical. `no_concluyente` si no hay nada de
    eso pero tampoco se puede afirmar que este bien (sin referencia, truncado,
    URLs sin rastrear o sin referencia). Un censo bloqueado es SIEMPRE
    `no_concluyente`: sus codigos son los del muro, no los del sitio (103).
    """
    config = job.config or {}
    semillas = list(job.seeds or [])
    fallos: list[dict[str, Any]] = []
    dudas: list[dict[str, Any]] = []
    alertas: list[dict[str, Any]] = []
    comp = job.comparacion if isinstance(getattr(job, "comparacion", None), dict) else None
    comparado_con = (comp or {}).get("comparado_con")

    def _salida(estado):
        return {
            "veredicto": estado,
            "job_id": str(job.id),
            "nombre": job.name,
            "estado_job": job.status,
            "finish_reason": job.finish_reason,
            "es_lista": es_lista(config),
            "urls_de_la_lista": len(semillas),
            "comparado_con": comparado_con,
            "motivos": fallos,
            "no_concluyente_por": dudas,
        }

    motivo = job.finish_reason or ""
    if job.status in ("pending", "queued", "running", "analyzing"):
        dudas.append(_grupo("sin_terminar"))
        return _salida(NO_CONCLUYENTE)
    if motivo in FALLOS_DEL_SITIO:
        fallos.append(_grupo("rastreo_bloqueado_por_robots",
                             [{"url": u} for u in semillas]))
        return _salida(FALLA)
    bloqueo = (motivo if motivo in MOTIVOS_BLOQUEO
               else (getattr(job, "health", None) or {}).get("causa"))
    if bloqueo:
        dudas.append(_grupo("bloqueado", causa=bloqueo))
        return _salida(NO_CONCLUYENTE)
    if job.status != "completed":
        dudas.append(_grupo("rastreo_fallido", causa=motivo or job.status))
        return _salida(NO_CONCLUYENTE)
    if motivo in MOTIVOS_TRUNCADO:
        dudas.append(_grupo("truncado", causa=motivo))

    # La comparacion automatica: sus alertas criticas son fallos; que no la
    # haya, o que no sea concluyente, es una duda.
    if comp is None:
        dudas.append(_grupo("sin_referencia"))
    elif not comp.get("comparable"):
        dudas.append(_grupo("sin_referencia", mensaje=comp.get("motivo")
                            or MENSAJES["sin_referencia"]))
        comparado_con = None
    else:
        if comp.get("bloqueado"):
            dudas.append(_grupo("bloqueado", causa=comp.get("bloqueado")))
        elif comp.get("concluyente") is False and not es_lista(config):
            # En una lista no: lo que esa marca protege —afirmar que falta una
            # URL que el anterior, truncado o con otro alcance, quiza no vio—
            # se mira aqui URL a URL (`no_rastreadas`, `sin_referencia_url`).
            # Medido con dos censos de blogs.uoc.edu y www.uoc.edu: un canario
            # truncado como referencia daba `no_concluyente` con las 50 URLs
            # de la lista presentes y sin cambios en los dos censos.
            dudas.append(_grupo("comparacion_no_concluyente",
                                avisos=comp.get("avisos") or []))
        for a in comp.get("alertas") or []:
            if a.get("severidad") == "critical":
                alertas.append(a)

    # URL a URL, sobre la lista. Una causa por URL (decision 36).
    ahora = _paginas_de(db, job.id, semillas)
    antes = (_referencias(db, comparado_con, semillas,
                          MAX_CADENA if es_lista(config) else 1)
             if comparado_con else {})
    por_tipo: dict[str, list[dict[str, Any]]] = {}
    vistas: set[str] = set()
    for semilla in semillas:
        clave = _norm(semilla)
        if not clave or clave in vistas:
            continue
        vistas.add(clave)
        p = ahora.get(clave)
        if p is None:
            por_tipo.setdefault("no_rastreadas", []).append({"url": semilla})
            continue
        if p.get("response_class") in NO_VALIDAS:
            por_tipo.setdefault("muro", []).append(
                {"url": p["url"], "ahora": p.get("indexability_status")})
            continue
        codigo = p.get("status_code")
        if codigo is None and "robots" in (p.get("indexability_status") or "").lower():
            por_tipo.setdefault("bloqueada_por_robots", []).append({"url": p["url"]})
            continue
        if codigo != 200:
            fila = {"url": p["url"], "ahora": codigo}
            if p.get("redirect_url"):
                fila["destino"] = p["redirect_url"]
            r = antes.get(clave)
            if r is not None:
                fila["antes"] = r.get("status_code")
            por_tipo.setdefault("no_responde_200", []).append(fila)
            continue
        if not comparado_con:
            continue  # sin referencia ya es una duda del censo entero
        r = antes.get(clave)
        if r is None or r.get("status_code") != 200 or r.get("response_class") in NO_VALIDAS:
            por_tipo.setdefault("sin_referencia_url", []).append({"url": p["url"]})
            continue
        tipo = _cambio(r, p)
        if tipo == "cambia_el_canonical":
            fila = {"url": p["url"], "antes": _canonical_que_cuenta(r),
                    "ahora": _canonical_que_cuenta(p)}
        elif tipo:
            fila = {"url": p["url"], "antes": r.get("indexability_status"),
                    "ahora": p.get("indexability_status")}
        if tipo:
            if r.get("_censo") and r["_censo"] != str(comparado_con):
                fila["referencia"] = r["_censo"]  # el ultimo estado que paso
            por_tipo.setdefault(tipo, []).append(fila)

    # Un aviso por hallazgo (decision 36): la alerta de la comparacion y la
    # comprobacion URL a URL pueden nombrar las MISMAS paginas (en una lista,
    # siempre: la comparacion se acota a ella). Si todas las de la alerta ya
    # estan en el grupo URL a URL que dice lo mismo, la alerta no es otro
    # motivo: su regla y lo que decide Google viajan con ese grupo.
    en_grupo = {t: {_norm(f["url"]) for f in filas} for t, filas in por_tipo.items()}
    contexto: dict[str, dict[str, Any]] = {}
    for a in alertas:
        tipo = EQUIVALENTE.get(a.get("regla"))
        # Los ejemplos de la alerta son una muestra (`MUESTRA`): cubierta si la
        # muestra esta en el grupo y el grupo tiene al menos tantas paginas.
        suyas = {_norm(e.get("url")) for e in a.get("ejemplos") or []}
        grupo = en_grupo.get(tipo, set())
        if tipo and suyas <= grupo and len(grupo) >= (a.get("paginas") or 0):
            contexto.setdefault(tipo, {
                "regla": a.get("regla"), "que_decide_google": a.get("que_decide_google"),
                "de": a.get("de"), "pct": a.get("pct")})
            continue
        fallos.append(_grupo(
            "alerta", a.get("ejemplos") or [],
            mensaje=a.get("que_decide_google") or MENSAJES["alerta"],
            regla=a.get("regla"), de=a.get("de"), pct=a.get("pct"))
            | {"paginas": a.get("paginas")})

    for tipo in ("bloqueada_por_robots", "no_responde_200",
                 "deja_de_ser_indexable", "cambia_el_canonical"):
        if por_tipo.get(tipo):
            fallos.append(_grupo(tipo, por_tipo[tipo], **contexto.get(tipo, {})))
    for tipo in ("no_rastreadas", "muro", "sin_referencia_url"):
        if por_tipo.get(tipo):
            dudas.append(_grupo(tipo, por_tipo[tipo]))

    if fallos:
        return _salida(FALLA)
    if dudas:
        return _salida(NO_CONCLUYENTE)
    return _salida(PASA)
