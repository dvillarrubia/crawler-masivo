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
    prog = Programacion(
        nombre=cuerpo.nombre,
        client_id=cuerpo.client_id,
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
    for campo, valor in cambios.items():
        setattr(prog, campo, valor)
    if {"cron", "zona_horaria", "activa"} & set(cambios):
        _recalcular(prog)
    db.commit()
    db.refresh(prog)
    return prog


@router.delete("/{prog_id}", status_code=204)
def borrar_programacion(prog_id: uuid.UUID, db: Session = Depends(get_session)):
    """Borra la programación. Los rastreos que ya lanzó NO se tocan.

    Son censos entregados o comparables: borrarlos al quitar el calendario
    sería tirar el histórico del cliente por cambiar de idea sobre la hora.
    """
    db.delete(_buscar(prog_id, db))
    db.commit()
