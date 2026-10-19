"""Enlaces de solo lectura para el cliente (decision 143, Ola 6.1).

`/c/<token>` es la aplicacion en «modo compartido»: el informe de cada censo
del cliente (decision 138) y nada mas. Lo autoriza el token de la ruta, no la
sesion de la agencia (`api/auth.py` deja pasar `/api/publico`), y cada
endpoint publico comprueba que lo pedido es de ESE cliente.

- El token son 32 caracteres aleatorios (`secrets.token_urlsafe(24)`, 192
  bits); se guarda su SHA-256 y se ensena una vez, al crearlo.
- Un enlace revocado o caducado responde 404, igual que uno que no existe: no
  se le dice a quien prueba tokens cual existio.
- Solo censos completados (no canarios ni pruebas, `tipo_de_rastreo`), y solo
  del cliente del enlace. Un id de otro cliente es otro 404.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.models import Client, EnlaceCliente, Job

router = APIRouter(tags=["compartido"])


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _a_dict(e: EnlaceCliente) -> dict:
    return {"id": str(e.id), "nombre": e.nombre, "prefijo": e.prefijo, "creado_en": e.creado_en,
            "caduca_en": e.caduca_en, "revocado_en": e.revocado_en, "ultimo_uso": e.ultimo_uso,
            "usos": e.usos or 0}


class EnlaceNuevo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str | None = Field(default=None, max_length=256)
    # Dias hasta que caduca; None = no caduca (se revoca a mano).
    dias: int | None = Field(default=365, ge=1, le=3650)


# -- Lo que gestiona la agencia (protegido por la sesion) ---------------------

@router.get("/api/clients/{client_id}/enlaces")
def listar_enlaces(client_id: uuid.UUID, db: Session = Depends(get_session)):
    if db.get(Client, client_id) is None:
        raise HTTPException(404, "Cliente no encontrado")
    filas = (db.query(EnlaceCliente).filter(EnlaceCliente.cliente_id == client_id)
             .order_by(EnlaceCliente.creado_en.desc()).all())
    return [_a_dict(e) for e in filas]


@router.post("/api/clients/{client_id}/enlaces", status_code=201)
def crear_enlace(client_id: uuid.UUID, cuerpo: EnlaceNuevo, db: Session = Depends(get_session)):
    if db.get(Client, client_id) is None:
        raise HTTPException(404, "Cliente no encontrado")
    token = secrets.token_urlsafe(24)
    ahora = datetime.now(timezone.utc)
    e = EnlaceCliente(cliente_id=client_id, token_hash=_hash(token), prefijo=token[:6],
                      nombre=cuerpo.nombre, creado_en=ahora,
                      caduca_en=ahora + timedelta(days=cuerpo.dias) if cuerpo.dias else None)
    db.add(e)
    db.commit()
    # La unica vez que sale el token.
    return _a_dict(e) | {"token": token, "ruta": f"/c/{token}"}


@router.delete("/api/clients/{client_id}/enlaces/{enlace_id}", status_code=204)
def revocar_enlace(client_id: uuid.UUID, enlace_id: uuid.UUID, db: Session = Depends(get_session)):
    e = (db.query(EnlaceCliente)
         .filter(EnlaceCliente.id == enlace_id, EnlaceCliente.cliente_id == client_id).one_or_none())
    if e is None:
        raise HTTPException(404, "Enlace no encontrado")
    if e.revocado_en is None:
        e.revocado_en = datetime.now(timezone.utc)
        db.commit()


# -- Lo que ve el cliente (publico, autorizado por el token) -----------------

def _enlace(db: Session, token: str) -> EnlaceCliente:
    e = (db.query(EnlaceCliente).filter(EnlaceCliente.token_hash == _hash(token)).one_or_none()
         if 20 <= len(token) <= 64 else None)
    ahora = datetime.now(timezone.utc)
    caduca = e.caduca_en if e is not None else None
    if caduca is not None and caduca.tzinfo is None:
        caduca = caduca.replace(tzinfo=timezone.utc)
    if e is None or e.revocado_en is not None or (caduca is not None and caduca < ahora):
        raise HTTPException(404, "Este enlace no existe o ya no es válido")
    e.ultimo_uso = ahora
    e.usos = (e.usos or 0) + 1
    db.commit()
    return e


def _censos(db: Session, cliente_id) -> list[Job]:
    from shared.clientes import tipo_de_rastreo

    jobs = (db.query(Job).filter(Job.cliente_id == cliente_id, Job.status == "completed")
            .order_by(Job.created_at.desc()).limit(100).all())
    return [j for j in jobs if tipo_de_rastreo(j.name, j.tipo) == "censo"][:24]


@router.get("/api/publico/{token}")
def portada_publica(token: str, db: Session = Depends(get_session)):
    e = _enlace(db, token)
    cliente = db.get(Client, e.cliente_id)
    return {"cliente": {"nombre": cliente.nombre},
            "censos": [{"id": str(j.id), "nombre": j.name,
                        "fecha": j.completed_at or j.started_at or j.created_at,
                        "sitio": (j.seeds or [None])[0]} for j in _censos(db, e.cliente_id)]}


@router.get("/api/publico/{token}/informe/{job_id}")
def informe_publico(token: str, job_id: uuid.UUID, prioridades: int = Query(10, ge=1, le=50),
                    db: Session = Depends(get_session)):
    from api.routers.results import get_informe

    e = _enlace(db, token)
    if job_id not in {j.id for j in _censos(db, e.cliente_id)}:
        raise HTTPException(404, "Este informe no existe o no es de este cliente")
    return get_informe(job_id, prioridades=prioridades, db=db)
