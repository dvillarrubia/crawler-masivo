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


@router.post("", response_model=ProgramacionResponse, status_code=201)
def crear_programacion(
    cuerpo: ProgramacionCreate, db: Session = Depends(get_session)
):
    from api.routers.clientes import resolver_cliente_y_perfil

    cliente, perfil = resolver_cliente_y_perfil(
        db, cliente_id=cuerpo.cliente_id, profile_id=cuerpo.profile_id,
        client_id_texto=cuerpo.client_id)
    prog = Programacion(
        nombre=cuerpo.nombre,
        client_id=cliente.slug if cliente else cuerpo.client_id,
        cliente_id=cliente.id if cliente else None,
        profile_id=perfil.id if perfil else None,
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
    return prog


@router.get("", response_model=list[ProgramacionResponse])
def listar_programaciones(
    activa: bool | None = Query(default=None),
    client_id: str | None = Query(default=None),
    db: Session = Depends(get_session),
):
    consulta = db.query(Programacion)
    if activa is not None:
        consulta = consulta.filter(Programacion.activa.is_(activa))
    if client_id:
        consulta = consulta.filter(Programacion.client_id == client_id)
    # Las que van a dispararse antes, primero; las desactivadas al final.
    return consulta.order_by(
        Programacion.proxima_ejecucion.is_(None),
        Programacion.proxima_ejecucion.asc(),
    ).all()


def _buscar(prog_id: uuid.UUID, db: Session) -> Programacion:
    prog = db.query(Programacion).filter(Programacion.id == prog_id).one_or_none()
    if prog is None:
        raise HTTPException(status_code=404, detail="Programacion no encontrada")
    return prog


@router.get("/{prog_id}", response_model=ProgramacionResponse)
def obtener_programacion(prog_id: uuid.UUID, db: Session = Depends(get_session)):
    return _buscar(prog_id, db)


@router.patch("/{prog_id}", response_model=ProgramacionResponse)
def editar_programacion(
    prog_id: uuid.UUID,
    cuerpo: ProgramacionUpdate,
    db: Session = Depends(get_session),
):
    prog = _buscar(prog_id, db)
    cambios = cuerpo.model_dump(exclude_unset=True)
    pausada = prog.activa and cambios.get("activa") is False
    for campo, valor in cambios.items():
        setattr(prog, campo, valor)
    if {"cron", "zona_horaria", "activa"} & set(cambios):
        _recalcular(prog)
    if pausada:
        _cerrar_su_serie(db, prog, SERIE_PAUSADA)
    db.commit()
    db.refresh(prog)
    return prog


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
