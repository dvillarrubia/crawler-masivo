"""Gestión de API keys por proyecto (B1 · auth).

Rutas bajo `/api/clients/{client_id}/api-keys`. Cuando la auth está activa
(`API_AUTH_ENABLED=1`) el middleware exige el ADMIN_TOKEN para tocarlas; con
la auth desactivada quedan abiertas (dev local, como el resto de cuentas).

La clave en claro se devuelve UNA sola vez, al crearla. Después solo queda
el hash + el prefijo.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.semantic_models import ApiKey

router = APIRouter(prefix="/api/clients/{client_id}/api-keys", tags=["api-keys"])


class ApiKeyCreate(BaseModel):
    name: str | None = Field(default=None, max_length=256)


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str | None
    prefix: str | None
    revoked: bool
    created_at: datetime | None
    last_used_at: datetime | None


@router.get("", response_model=list[ApiKeyResponse])
def list_api_keys(client_id: str, db: Session = Depends(get_session)):
    """Lista las keys del proyecto (sin la clave, solo prefijo y estado)."""
    return (db.query(ApiKey).filter(ApiKey.client_id == client_id)
            .order_by(ApiKey.created_at.desc()).all())


@router.post("")
def create_api_key(client_id: str, body: ApiKeyCreate,
                   db: Session = Depends(get_session)):
    """Crea una key para el proyecto. Devuelve la clave ENTERA una única vez:
    guárdala, no se puede recuperar después."""
    from api.auth import generate_api_key

    raw, key_hash, prefix = generate_api_key()
    row = ApiKey(client_id=client_id, name=body.name,
                 key_hash=key_hash, prefix=prefix)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": str(row.id), "name": row.name, "prefix": row.prefix,
        "api_key": raw,   # ← solo aquí, nunca más
        "aviso": "Guarda esta clave ahora: no se puede volver a mostrar.",
    }


@router.delete("/{key_id}")
def revoke_api_key(client_id: str, key_id: uuid.UUID,
                   db: Session = Depends(get_session)):
    """Revoca una key (soft: se conserva para auditoría, deja de valer)."""
    row = db.get(ApiKey, key_id)
    if row is None or row.client_id != client_id:
        raise HTTPException(status_code=404, detail="Key no encontrada.")
    row.revoked = True
    db.commit()
    return {"status": "ok", "revoked": True}
