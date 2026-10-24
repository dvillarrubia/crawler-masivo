"""Analisis enriquecido bajo demanda (#40, decision 85).

La API NO llama a ningun modelo: estima (consultando solo caches), crea la
ejecucion y la encola en `enrichment:pending`. La ejecuta el worker. Es lo
contrario de `fetch-gsc`, que llama a Google dentro del request.

Seguridad: la API no tiene autenticacion y el CORS esta abierto, asi que
cualquiera que llegue al puerto podria gastar dinero. Crear una ejecucion
exige la cabecera `X-Enrichment-Token` igual a `ENRICHMENT_TOKEN`; sin la
variable, el lanzamiento esta DESACTIVADO, nunca abierto. Estimar, leer y
cancelar no gastan y no lo piden: cancelar es justo lo que hay que poder
hacer deprisa cuando algo se va de las manos.
"""

from __future__ import annotations

import hmac
import json
import os
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import exists, func
from sqlalchemy.orm import Session, aliased

from shared.cola import encolar_enriquecido
from shared.database import get_session
from shared.models import Decision, EnrichmentRun, Job, Url

from api.dependencies import get_redis
from api.schemas import EnriquecidoCrear, EnriquecidoEstimar, EnrichmentRunResponse

router = APIRouter(prefix="/api", tags=["enriquecido"])

# Un token corto se adivina: por debajo de esto se trata como no configurado
# y el lanzamiento queda desactivado, con el motivo en el 403.
LONGITUD_MINIMA_TOKEN = 16


def _token_configurado() -> str | None:
    t = (os.getenv("ENRICHMENT_TOKEN") or "").strip()
    return t if len(t) >= LONGITUD_MINIMA_TOKEN else None


def exigir_token(x_enrichment_token: str | None = Header(default=None)) -> None:
    """403 sin token configurado y 403 con uno incorrecto. Comparacion en
    tiempo constante: `==` corta en el primer caracter distinto y el tiempo
    de respuesta va diciendo cuantos se han acertado. Ningun mensaje repite
    el token recibido ni el esperado."""
    esperado = _token_configurado()
    if esperado is None:
        motivo = ("ENRICHMENT_TOKEN es demasiado corto (minimo "
                  f"{LONGITUD_MINIMA_TOKEN} caracteres)"
                  if (os.getenv("ENRICHMENT_TOKEN") or "").strip()
                  else "falta ENRICHMENT_TOKEN en el entorno de la API")
        raise HTTPException(
            status_code=403,
            detail=(f"El lanzamiento del analisis enriquecido esta desactivado: {motivo}. "
                    "Es una accion de pago y la API no tiene autenticacion; "
                    "configurala en el .env del servidor y reinicia la API."))
    recibido = (x_enrichment_token or "").strip()
    if not recibido or not hmac.compare_digest(recibido.encode("utf-8"), esperado.encode("utf-8")):
        raise HTTPException(status_code=403,
                            detail="Token de analisis enriquecido incorrecto o ausente.")


def _job_listo(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # `completed` implica analisis terminado: un analisis que falla deja el
    # job en `failed` (decision 28) y mientras corre esta en `analyzing`.
    if job.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=(f"El job esta '{job.status}': el analisis enriquecido solo se lanza "
                    "sobre un rastreo completado y con el analisis terminado."))
    return job


def _redis_o_none():
    try:
        return get_redis()
    except Exception:  # noqa: BLE001 - sin Redis la estimacion mira solo la BD
        return None


def _estimar(db, job, cuerpo):
    from shared.enriquecido import ModuloDesconocido, estimar

    try:
        return estimar(db, job, cuerpo.modulos, cuerpo.alcance.model_dump(), redis=_redis_o_none())
    except ModuloDesconocido as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _nivel(pregunta, respuesta: str | None) -> str | None:
    """Un score se agrupa por su nivel mas cercano («0»...«3»), no por el
    numero que devuelve Jev (2,62 y 2,9 son el mismo nivel): si no, la pantalla
    enseñaba una entrada por decimal (decision 177). Lo demas, tal cual."""
    if respuesta is None or pregunta is None or pregunta.tipo != "score":
        return respuesta
    from shared.evaluacion_decisiones import etiqueta_de_respuesta

    return etiqueta_de_respuesta("score", respuesta, len(pregunta.opciones))


def _etiquetas(pregunta) -> dict[str, str]:
    """El nombre corto de cada nivel de un score, para la pantalla (decision
    177): «2» no dice nada y cada score nombra sus niveles a su manera. Las
    respuestas de un choice o un sí/no salen del diccionario unico de la
    pantalla (`RESPUESTA_ENRIQ`, decision 176), no de aqui."""
    if pregunta is None or pregunta.tipo != "score":
        return {}
    nombres = list(pregunta.etiquetas) or [str(i) for i in range(len(pregunta.opciones))]
    return {str(i): n for i, n in enumerate(nombres)}


def _opciones(pregunta) -> list[str] | None:
    """El orden en que la pantalla pinta las respuestas (decision 176): las
    opciones de un choice del catalogo y los niveles «0»...«n-1» de un score,
    que es como llega agrupado (177). Las opciones del cliente (178), sin orden."""
    if pregunta is None or not pregunta.opciones or pregunta.opciones_del_cliente:
        return None
    if pregunta.tipo == "score":
        return [str(i) for i in range(len(pregunta.opciones))]
    if pregunta.tipo == "choice":
        return list(pregunta.opciones)
    return None


def _resultados(db: Session, run_id: uuid.UUID) -> dict:
    filas = (db.query(Decision.pregunta, Decision.estado, Decision.respuesta,
                      func.count(Decision.id), func.coalesce(func.sum(Decision.coste_usd), 0.0),
                      Decision.ambito)
             .filter(Decision.run_id == run_id)
             .group_by(Decision.pregunta, Decision.estado, Decision.respuesta, Decision.ambito).all())
    from shared.preguntas import CATALOGO

    salida: dict[str, dict] = {}
    for pregunta, estado, respuesta, n, coste, ambito in filas:
        # El umbral de HOY (decision 169): sin el, la interfaz avisa de que todo
        # es orientativo aunque alguna fila vieja diga otra cosa; con el, separa
        # por respuesta lo decidido de lo incierto, que no tiene el mismo aplomo.
        actual = CATALOGO.get(pregunta)
        p = salida.setdefault(pregunta, {"por_estado": {}, "por_respuesta": {},
                                         "decididas_por_respuesta": {}, "total": 0, "coste_usd": 0.0,
                                         "umbral": actual.umbral if actual else None,
                                         "tipo": actual.tipo if actual else None,
                                         "etiquetas": _etiquetas(actual)})
        # Decision 176: un choice se pinta con sus opciones en orden y una fila
        # de `intencion_busqueda` es una busqueda de una pagina, no una URL. Las
        # de una pregunta con las opciones del cliente (178) no son las del
        # catalogo: sin orden que dar. Un score se agrupa por nivel (177), asi
        # que sus opciones son los niveles «0»...«n-1».
        p["opciones"] = _opciones(actual)
        p["ambito"] = ambito or "url"
        respuesta = _nivel(actual, respuesta)
        p["total"] += n
        p["coste_usd"] = round(p["coste_usd"] + float(coste), 8)
        p["por_estado"][estado] = p["por_estado"].get(estado, 0) + n
        if respuesta is not None:
            p["por_respuesta"][respuesta] = p["por_respuesta"].get(respuesta, 0) + n
            if estado == "decidido":
                p["decididas_por_respuesta"][respuesta] = p["decididas_por_respuesta"].get(respuesta, 0) + n
    for pregunta, p in salida.items():
        _solo_lo_que_aplica(db, run_id, pregunta, p, CATALOGO.get(pregunta))
    return salida


def _condicion(pregunta):
    """La fila de la pregunta de la que depende esta (`solo_si`, decision 175):
    la del MISMO estado (clave y URL) y la misma ejecucion."""
    otra, valor = pregunta.solo_si
    c = aliased(Decision)
    return exists().where(c.run_id == Decision.run_id, c.clave == Decision.clave,
                          c.url_id == Decision.url_id, c.pregunta == otra, c.respuesta == valor)


def _solo_lo_que_aplica(db, run_id, nombre: str, p: dict, actual) -> None:
    """Una pregunta con `solo_si` (la recomendacion de una canibalizacion) se
    cuenta solo donde la otra respondio lo que pide: su respuesta sobre un
    reparto legitimo no significa nada. Lo demas va en `no_aplica`; el coste
    sigue siendo el de todas, que se pagaron."""
    if actual is None or actual.solo_si is None:
        return
    filas = (db.query(Decision.estado, Decision.respuesta, func.count(Decision.id))
             .filter(Decision.run_id == run_id, Decision.pregunta == nombre, _condicion(actual))
             .group_by(Decision.estado, Decision.respuesta).all())
    total = p["total"]
    p.update({"por_estado": {}, "por_respuesta": {}, "decididas_por_respuesta": {}, "total": 0,
              "solo_si": list(actual.solo_si)})
    for estado, respuesta, n in filas:
        respuesta = _nivel(actual, respuesta)
        p["total"] += n
        p["por_estado"][estado] = p["por_estado"].get(estado, 0) + n
        if respuesta is not None:
            p["por_respuesta"][respuesta] = p["por_respuesta"].get(respuesta, 0) + n
            if estado == "decidido":
                p["decididas_por_respuesta"][respuesta] = p["decididas_por_respuesta"].get(respuesta, 0) + n
    p["no_aplica"] = total - p["total"]


def _respuesta(db: Session, run: EnrichmentRun, con_resultados: bool = True) -> EnrichmentRunResponse:
    r = EnrichmentRunResponse.model_validate(run)
    if con_resultados:
        r.resultados = _resultados(db, run.id)
    return r


@router.get("/enrichment/modulos")
def listar_modulos():
    """Lo que el dialogo ofrece, y si lanzar esta activado en esta instancia
    (sin decir nada del token)."""
    from shared.enriquecido import describir_modulos

    return {"modulos": describir_modulos(), "lanzamiento_activado": _token_configurado() is not None}


@router.post("/jobs/{job_id}/enrichment/estimate")
def estimar_enriquecido(job_id: uuid.UUID, cuerpo: EnriquecidoEstimar,
                        db: Session = Depends(get_session)):
    """No gasta nada: la capa se monta sin cliente y solo mira caches."""
    job = _job_listo(db, job_id)
    return _estimar(db, job, cuerpo)


@router.post("/jobs/{job_id}/enrichment", response_model=EnrichmentRunResponse, status_code=201,
             dependencies=[Depends(exigir_token)])
def crear_enriquecido(job_id: uuid.UUID, cuerpo: EnriquecidoCrear,
                      db: Session = Depends(get_session)):
    from shared.enriquecido import ACTIVOS, PENDIENTE

    job = _job_listo(db, job_id)
    activa = (db.query(EnrichmentRun.id)
              .filter(EnrichmentRun.job_id == job.id, EnrichmentRun.estado.in_(ACTIVOS)).first())
    if activa:
        raise HTTPException(status_code=409, detail=(
            f"Ya hay una ejecucion en marcha sobre este job ({activa[0]}). "
            "Espera a que termine o cancelala."))
    estimacion = _estimar(db, job, cuerpo)
    # Si el cliente ya agoto su mes, se dice ahora y no con una ejecucion que
    # se queda `parcial` sin haber hecho nada.
    if estimacion["llamadas"] > 0:
        for nivel in ("cliente", "global"):
            t = estimacion.get(nivel)
            if t and t.get("restante_usd") is not None and t["restante_usd"] <= 0:
                raise HTTPException(status_code=409, detail=(
                    f"Tope mensual {'del cliente' if nivel == 'cliente' else 'de la instancia'} "
                    f"agotado: {t['gastado_mes_usd']} $ de {t['tope_mensual_usd']} $."))
    run = EnrichmentRun(job_id=job.id, modulos=estimacion["modulos"],
                        alcance=estimacion["alcance"], estimacion=estimacion,
                        presupuesto_usd=cuerpo.presupuesto_usd, gastado_usd=0.0,
                        estado=PENDIENTE)
    db.add(run)
    db.commit()
    try:
        encolar_enriquecido(get_redis(), run.id)
    except Exception as exc:  # noqa: BLE001
        run.estado = "fallida"
        run.errores = [f"no se pudo encolar: {type(exc).__name__}"]
        db.commit()
        raise HTTPException(status_code=503, detail="No se pudo encolar la ejecucion") from None
    db.refresh(run)
    return _respuesta(db, run, con_resultados=False)


@router.get("/jobs/{job_id}/enrichment", response_model=list[EnrichmentRunResponse])
def listar_enriquecidos(job_id: uuid.UUID, db: Session = Depends(get_session)):
    runs = (db.query(EnrichmentRun).filter(EnrichmentRun.job_id == job_id)
            .order_by(EnrichmentRun.created_at.desc()).limit(50).all())
    return [_respuesta(db, r, con_resultados=False) for r in runs]


def _run(db, job_id, run_id) -> EnrichmentRun:
    run = db.get(EnrichmentRun, run_id)
    if run is None or run.job_id != job_id:
        raise HTTPException(status_code=404, detail="Ejecucion no encontrada")
    return run


@router.get("/jobs/{job_id}/enrichment/{run_id}", response_model=EnrichmentRunResponse)
def ver_enriquecido(job_id: uuid.UUID, run_id: uuid.UUID, db: Session = Depends(get_session)):
    return _respuesta(db, _run(db, job_id, run_id))


@router.get("/jobs/{job_id}/enrichment/{run_id}/decisions")
def decisiones_de_ejecucion(job_id: uuid.UUID, run_id: uuid.UUID,
                            pregunta: str | None = None, respuesta: str | None = None,
                            page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                            db: Session = Depends(get_session)):
    """Las respuestas URL a URL. `estado_enviado` no sale: es contenido de la
    pagina y para revisarlo esta #8. De el solo salen la busqueda y las rutas
    de un grupo (decision 175: en un modulo por grupo la URL es solo donde
    cuelga, y sin ellas la fila no dice que se juzgo). Filtrando por una pregunta con
    `solo_si`, solo las filas donde aplica."""
    from shared.preguntas import CATALOGO

    _run(db, job_id, run_id)
    q = (db.query(Decision.id, Url.url, Decision.pregunta, Decision.estado, Decision.origen,
                  Decision.respuesta, Decision.confianza, Decision.coste_usd, Decision.motivo,
                  Decision.ambito, Decision.estado_enviado["busqueda"].as_string(),
                  Decision.estado_enviado["paginas"])
         .join(Url, Url.id == Decision.url_id).filter(Decision.run_id == run_id))
    actual = CATALOGO.get(pregunta) if pregunta else None
    if pregunta:
        q = q.filter(Decision.pregunta == pregunta)
        if actual is not None and actual.solo_si is not None:
            q = q.filter(_condicion(actual))
    if respuesta and actual is not None and actual.tipo == "score" and respuesta.isdigit():
        # El nivel de un score (decision 177): [nivel - 0,5, nivel + 0,5), con
        # los extremos abiertos, como lo redondea `etiqueta_de_respuesta`.
        from sqlalchemy import Float, cast

        nivel, valor = int(respuesta), cast(Decision.respuesta, Float)
        if nivel > 0:
            q = q.filter(valor >= nivel - 0.5)
        if nivel < len(actual.opciones) - 1:
            q = q.filter(valor < nivel + 0.5)
    elif respuesta:
        q = q.filter(Decision.respuesta == respuesta)
    total = q.count()
    filas = q.order_by(Decision.id).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [{"id": i, "url": u, "pregunta": p, "estado": e, "origen": o,
                       "respuesta": r, "nivel": _nivel(CATALOGO.get(p), r) if r is not None else None,
                       "confianza": c, "coste_usd": cu, "motivo": m,
                       "busqueda": b if amb != "url" else None,
                       "rutas": _rutas(pag) if amb != "url" else None}
                      for i, u, p, e, o, r, c, cu, m, amb, b, pag in filas]}


def _rutas(paginas) -> list[str] | None:
    """Las rutas del par juzgado (decision 175): la URL de la fila es solo
    donde cuelga, y sin las dos rutas no se sabe que dos paginas se juzgaron."""
    if isinstance(paginas, str):
        try:
            paginas = json.loads(paginas)
        except ValueError:
            return None
    if not isinstance(paginas, list):
        return None
    return [str(p.get("ruta")) for p in paginas if isinstance(p, dict) and p.get("ruta")] or None


@router.get("/jobs/{job_id}/enrichment/{run_id}/plantillas")
def propuesta_por_plantilla(job_id: uuid.UUID, run_id: uuid.UUID, pregunta: str = "tipo_pagina",
                            db: Session = Depends(get_session)):
    """Lo que las muestras de cada plantilla responden y a cuantas URLs se
    propondria (decision 178). Solo lee `decisions`: no propaga nada, y sin
    umbral calibrado ninguna plantilla sale `propagable`."""
    from shared.tipo_pagina import propuesta_por_plantilla as propuesta

    _run(db, job_id, run_id)
    return propuesta(db, run_id, pregunta)


@router.get("/jobs/{job_id}/enrichment/{run_id}/intencion")
def intencion_de_ejecucion(job_id: uuid.UUID, run_id: uuid.UUID,
                           limite: int = Query(100, ge=1, le=1000),
                           db: Session = Depends(get_session)):
    """Intencion de cada pagina frente a la de sus busquedas, ponderado por
    clics, y el embudo con el trafico real (#39, #169; decision 176). Todo
    orientativo mientras las preguntas no tengan umbral (#8)."""
    from shared.enriquecido import intencion_frente_a_busquedas

    _run(db, job_id, run_id)
    return intencion_frente_a_busquedas(db, run_id, limite)


@router.patch("/jobs/{job_id}/enrichment/{run_id}/cancel", response_model=EnrichmentRunResponse)
def cancelar_enriquecido(job_id: uuid.UUID, run_id: uuid.UUID, db: Session = Depends(get_session)):
    """Pendiente -> cancelada en el acto (el worker la salta al sacarla).
    En curso -> `cancelando`: el worker la ve entre lotes y cierra conservando
    todo lo pagado. Terminada -> 409."""
    from datetime import datetime, timezone

    from shared.enriquecido import CANCELADA, CANCELANDO, EN_CURSO, PENDIENTE

    run = _run(db, job_id, run_id)
    if run.estado == PENDIENTE:
        run.estado = CANCELADA
        run.motivo = "cancelada"
        run.finished_at = datetime.now(timezone.utc)
    elif run.estado == EN_CURSO:
        run.estado = CANCELANDO
    elif run.estado != CANCELANDO:
        raise HTTPException(status_code=409, detail=f"La ejecucion ya esta '{run.estado}'")
    db.commit()
    db.refresh(run)
    return _respuesta(db, run)
