"""Rastreos programados: crear, listar, editar y borrar.

El disparo NO vive aquí: lo hace el worker, en la misma pasada del bucle que
el vigilante de estancamientos. Aquí solo se guarda qué hay que rastrear y
cuándo, y se calcula la próxima vez cada vez que eso cambia.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.incidentes import SERIE_BORRADA, SERIE_PAUSADA, cerrar_serie
from shared.models import Programacion
from shared.programaciones import proxima_desde

from api.schemas import (
    ProgramacionCreate,
    ProgramacionResponse,
    ProgramacionUpdate,
)

router = APIRouter(prefix="/api/programaciones", tags=["programaciones"])


def _recalcular(prog: Programacion) -> None:
    """Deja `proxima_ejecucion` al día, o a None si está desactivada.

    Se llama al crear y en CADA edición. Si no, cambiar la hora o la zona no
    tendría efecto hasta el disparo siguiente —o sea, se dispararía una vez
    más a la hora vieja— y eso se lee como que el cambio no se guardó.
    """
    if not prog.activa:
        prog.proxima_ejecucion = None
        return
    prog.proxima_ejecucion = proxima_desde(
        prog.cron, prog.zona_horaria, datetime.now(timezone.utc))


def _respuestas(db: Session, progs: list[Programacion], *, resumen: bool = False) -> list[dict]:
    """Cada programacion con lo que hace falta para leerla sin abrir nada mas
    (revision de UX del 25-oct, 2.2): el nombre del cliente y del perfil, las
    semillas que lanzaria HOY y las otras activas que repiten su trabajo.

    Las repetidas se miran contra TODAS las activas, no solo las filtradas:
    filtrar por cliente no puede esconder que hay dos iguales.
    """
    from shared.models import Client, CrawlProfile
    from shared.programaciones import repetidas, semillas_efectivas

    activas = db.query(Programacion).filter(Programacion.activa.is_(True)).all()
    todas = {p.id: p for p in list(progs) + activas}
    perfil_ids = {p.profile_id for p in todas.values() if p.profile_id}
    perfiles = ({p.id: p for p in db.query(CrawlProfile).filter(CrawlProfile.id.in_(perfil_ids))}
                if perfil_ids else {})
    cliente_ids = {p.cliente_id for p in progs if p.cliente_id}
    clientes = ({c.id: c.nombre for c in db.query(Client).filter(Client.id.in_(cliente_ids))}
                if cliente_ids else {})
    rep = repetidas(list(todas.values()), perfiles)

    salida = []
    for p in progs:
        semillas = semillas_efectivas(p, perfiles)
        d = ProgramacionResponse.model_validate(p).model_dump()
        perfil = perfiles.get(p.profile_id)
        d.update(
            seeds=semillas[:3] if resumen else semillas,
            n_semillas=len(semillas),
            cliente_nombre=clientes.get(p.cliente_id),
            perfil_nombre=perfil.nombre if perfil is not None else None,
            repetida_con=rep.get(p.id, []) if p.activa else [],
        )
        salida.append(d)
    return salida


def _validar_contra_perfil(db: Session, perfil, propia: dict | None) -> None:
    """La config de lo propio sobre el cliente y el perfil de hoy tiene que ser
    valida: mejor el 422 al guardar que una programacion que no se lanza."""
    from shared.models import Client

    from api.routers.clientes import resolver_config

    resolver_config(db.get(Client, perfil.cliente_id), perfil, propia or {})


@router.post("", response_model=ProgramacionResponse, status_code=201)
def crear_programacion(
    cuerpo: ProgramacionCreate, db: Session = Depends(get_session)
):
    from api.routers.clientes import resolver_cliente_y_perfil

    cliente, perfil = resolver_cliente_y_perfil(
        db, cliente_id=cuerpo.cliente_id, profile_id=cuerpo.profile_id,
        client_id_texto=cuerpo.client_id)
    if cuerpo.hereda_perfil:
        # Colgada del perfil (decision 193): ni semillas ni config copiadas;
        # solo lo propio. El worker lee el perfil en cada disparo.
        _validar_contra_perfil(db, perfil, cuerpo.config_propia)
        prog = Programacion(
            nombre=(cuerpo.nombre or perfil.nombre).strip(),
            client_id=cliente.slug if cliente else cuerpo.client_id,
            cliente_id=perfil.cliente_id,
            profile_id=perfil.id,
            hereda_perfil=True,
            seeds=[],
            config=cuerpo.config_propia or {},
            cron=cuerpo.cron,
            zona_horaria=cuerpo.zona_horaria,
            activa=cuerpo.activa,
        )
    else:
        prog = Programacion(
            nombre=cuerpo.nombre,
            client_id=cliente.slug if cliente else cuerpo.client_id,
            cliente_id=cliente.id if cliente else None,
            profile_id=perfil.id if perfil else None,
            hereda_perfil=False,
            seeds=cuerpo.seeds,
            config=cuerpo.config.model_dump(exclude_none=True),
            cron=cuerpo.cron,
            zona_horaria=cuerpo.zona_horaria,
            activa=cuerpo.activa,
        )
    _recalcular(prog)
    db.add(prog)
    db.commit()
    db.refresh(prog)
    return _respuestas(db, [prog])[0]


@router.get("", response_model=list[ProgramacionResponse])
def listar_programaciones(
    activa: bool | None = Query(default=None),
    client_id: str | None = Query(default=None),
    cliente_id: uuid.UUID | None = Query(default=None),
    profile_id: uuid.UUID | None = Query(default=None),
    resumen: bool = Query(default=False, description="Solo las tres primeras semillas"),
    db: Session = Depends(get_session),
):
    consulta = db.query(Programacion)
    if activa is not None:
        consulta = consulta.filter(Programacion.activa.is_(activa))
    if client_id:
        consulta = consulta.filter(Programacion.client_id == client_id)
    if cliente_id:
        consulta = consulta.filter(Programacion.cliente_id == cliente_id)
    if profile_id:
        consulta = consulta.filter(Programacion.profile_id == profile_id)
    # Las que van a dispararse antes, primero; las desactivadas al final.
    progs = consulta.order_by(
        Programacion.proxima_ejecucion.is_(None),
        Programacion.proxima_ejecucion.asc(),
    ).all()
    return _respuestas(db, progs, resumen=resumen)


def _buscar(prog_id: uuid.UUID, db: Session) -> Programacion:
    prog = db.query(Programacion).filter(Programacion.id == prog_id).one_or_none()
    if prog is None:
        raise HTTPException(status_code=404, detail="Programacion no encontrada")
    return prog


@router.get("/{prog_id}", response_model=ProgramacionResponse)
def obtener_programacion(prog_id: uuid.UUID, db: Session = Depends(get_session)):
    return _respuestas(db, [_buscar(prog_id, db)])[0]


@router.patch("/{prog_id}", response_model=ProgramacionResponse)
def editar_programacion(
    prog_id: uuid.UUID,
    cuerpo: ProgramacionUpdate,
    db: Session = Depends(get_session),
):
    prog = _buscar(prog_id, db)
    cambios = cuerpo.model_dump(exclude_unset=True)
    if "config_propia" in cambios:
        propia = cambios.pop("config_propia")
        if not prog.hereda_perfil:
            raise HTTPException(
                status_code=422,
                detail="Esta programacion es una copia fija: no hereda del perfil "
                       "y no tiene «lo propio» que cambiar")
        from shared.models import CrawlProfile

        perfil = db.get(CrawlProfile, prog.profile_id) if prog.profile_id else None
        if perfil is None:
            raise HTTPException(status_code=409, detail="Su perfil ya no existe")
        _validar_contra_perfil(db, perfil, propia)
        prog.config = propia or {}
    for campo in ("nombre", "cron", "activa"):
        if campo in cambios and cambios[campo] is None:
            cambios.pop(campo)
    pausada = prog.activa and cambios.get("activa") is False
    for campo, valor in cambios.items():
        setattr(prog, campo, valor)
    if {"cron", "zona_horaria", "activa"} & set(cambios):
        _recalcular(prog)
    if pausada:
        _cerrar_su_serie(db, prog, SERIE_PAUSADA)
    db.commit()
    db.refresh(prog)
    return _respuestas(db, [prog])[0]


@router.post("/{prog_id}/ejecutar", status_code=201)
def ejecutar_ahora(prog_id: uuid.UUID, db: Session = Depends(get_session)):
    """Lanza ya el rastreo de la programación, sin esperar a su hora (#36, decisión 209).

    El MISMO job que crearía el planificador (`crear_job_de_disparo`), con la
    misma regla de no lanzar encima de un rastreo vivo: un 409 dice por qué.
    Va a la cola normal y no a la de programados: quien lo pide está
    esperando. No toca la próxima hora; si el rastreo sigue vivo cuando
    llegue, el planificador se salta ese disparo y lo dice, como siempre.
    Funciona también con la programación en pausa: pausar es «que no se lance
    sola», no «que no se pueda lanzar».
    """
    from shared.cola import encolar
    from shared.models import Job
    from shared.programaciones import (LANZADA_A_MANO, bloquear_perfil,
                                       crear_job_de_disparo, motivo_rastreo_vivo,
                                       resolver_disparo)

    from api.dependencies import get_redis

    prog = (db.query(Programacion).filter(Programacion.id == prog_id)
            .with_for_update().one_or_none())
    if prog is None:
        raise HTTPException(status_code=404, detail="Programacion no encontrada")
    bloquear_perfil(db, prog)
    vivo = motivo_rastreo_vivo(db, prog)
    if vivo:
        raise HTTPException(status_code=409, detail=vivo)
    datos, motivo = resolver_disparo(db, prog)
    if datos is None:
        raise HTTPException(status_code=409, detail=motivo)
    job = crear_job_de_disparo(db, prog, datos, datetime.now(timezone.utc))
    prog.ultimo_motivo = LANZADA_A_MANO
    db.commit()
    try:
        encolar(get_redis(), job.id)
    except Exception:
        # Como en el worker: un job «pending» fuera de la cola no se rastrea
        # nunca y bloquearia esta programacion («sigue sin terminar»).
        db.query(Job).filter(Job.id == job.id).update({Job.status: "failed"},
                                                      synchronize_session=False)
        prog.ultimo_motivo = "no se pudo encolar: la cola no responde"
        db.commit()
        raise HTTPException(status_code=503,
                            detail="La cola de rastreos no responde: no se ha lanzado")
    return {"job_id": str(job.id), "nombre": job.name}


@router.delete("/{prog_id}", status_code=204)
def borrar_programacion(prog_id: uuid.UUID, db: Session = Depends(get_session)):
    """Borra la programación. Los rastreos que ya lanzó NO se tocan.

    Son censos entregados o comparables: borrarlos al quitar el calendario
    sería tirar el histórico del cliente por cambiar de idea sobre la hora.
    """
    prog = _buscar(prog_id, db)
    _cerrar_su_serie(db, prog, SERIE_BORRADA)
    db.delete(prog)
    db.commit()


def _cerrar_su_serie(db: Session, prog, motivo: str) -> None:
    """Decision 188: sus incidentes no los iba a cerrar ningun rastreo. Solo si
    la serie es la de la programacion: una que lanza un perfil es la serie del
    perfil, que se puede seguir lanzando a mano (`serie_de_job`)."""
    if prog.profile_id is None:
        cerrar_serie(db, f"programacion:{prog.id}", motivo)
