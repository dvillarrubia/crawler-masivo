"""Revision humana de lo que respondio Jev (#8, decision 211).

La pantalla «Revisar a Jev»: lo que falta por etiquetar (a ciegas: sin la
respuesta de Jev), guardar la etiqueta y, despues, ensenar si Jev coincidia; y
el juego y sus respuestas en JSONL para `scripts/evaluar_decisiones.py
evaluar`. No llama a ningun modelo. La logica vive en
`shared/revision_decisiones.py`, que usa tambien el script.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from shared import revision_decisiones as R
from shared.database import get_session

router = APIRouter(prefix="/api/decisiones", tags=["revision"])


class EtiquetaCrear(BaseModel):
    # str, bool o el indice de un nivel: lo normaliza `normalizar_etiqueta`.
    etiqueta: str | bool | int
    nota: str | None = Field(default=None, max_length=2000)
    autor: str | None = Field(default=None, max_length=128)


@router.get("/revision/resumen")
def resumen(db: Session = Depends(get_session)):
    """Por pregunta: cuantas inciertas faltan, cuantas hay etiquetadas y en
    cuantas coincide Jev."""
    return R.resumen(db)


@router.get("/revision")
def pendientes(pregunta: str | None = None, job_id: uuid.UUID | None = None,
               estado: str | None = Query("incierto", pattern="^(incierto|decidido|todas)$"),
               limite: int = Query(20, ge=1, le=100), db: Session = Depends(get_session)):
    """Lo que falta por revisar, una fila por estado, sin la respuesta de Jev.
    `estado=todas` para etiquetar tambien lo decidido (medir un umbral ya
    puesto)."""
    return R.pendientes(db, pregunta=pregunta, job_id=job_id,
                        estado=None if estado == "todas" else estado, limite=limite)


@router.put("/{decision_id}/etiqueta")
def etiquetar(decision_id: int, cuerpo: EtiquetaCrear, db: Session = Depends(get_session)):
    """Guarda (o corrige) la etiqueta humana y devuelve lo que dijo Jev."""
    try:
        r = R.etiquetar(db, decision_id, cuerpo.etiqueta, nota=cuerpo.nota, autor=cuerpo.autor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except R.EtiquetaInvalida as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return r


@router.get("/etiquetas/{pregunta}/{que}", response_class=PlainTextResponse)
def exportar(pregunta: str, que: str, db: Session = Depends(get_session)):
    """`juego.jsonl` o `respuestas.jsonl` de lo etiquetado, para `evaluar`."""
    if que not in ("juego.jsonl", "respuestas.jsonl"):
        raise HTTPException(status_code=404, detail="juego.jsonl o respuestas.jsonl")
    try:
        juego, respuestas = R.exportar(db, pregunta)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(R.jsonl(juego if que == "juego.jsonl" else respuestas),
                             media_type="application/x-ndjson",
                             headers={"Content-Disposition": f'attachment; filename="{pregunta}-{que}"'})
