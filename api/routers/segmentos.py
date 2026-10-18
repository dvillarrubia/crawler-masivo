"""Segmentos de negocio de un censo (Ola 3.3, decision 125).

- `GET /api/jobs/{id}/segmentos`: los segmentos materializados con sus cifras
  (URLs, indexables, paginas con error o aviso), las reglas y si `urls.segmento`
  esta al dia con ellas.
- `POST /api/jobs/{id}/segmentos`: re-segmentar SIN el analisis entero, con
  reglas nuevas, las del cliente/perfil de hoy (`desde_cliente`) o las que ya
  tiene el censo. Es lo que hace retroactivo un cambio de reglas: un censo de
  hace meses se segmenta sin re-rastrear ni re-analizar. Las reglas usadas
  quedan en `jobs.config.segmentos`, para que un re-analisis posterior las
  respete y para que el censo diga con que se segmento.

La logica vive en `shared/segmentos.py` (la usa tambien el analizador).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from api.schemas import ReglaSegmento
from shared import segmentos as sg
from shared.database import get_session
from shared.models import Issue, Job, Url

router = APIRouter(prefix="/api/jobs/{job_id}", tags=["segmentos"])

# Un censo que alguien esta escribiendo no se re-segmenta: el analisis lo hara
# al terminar con las reglas de su config.
EN_MARCHA = {"pending", "queued", "running", "analyzing"}


class Segmentar(BaseModel):
    # None = las del propio censo (o las del cliente con `desde_cliente`).
    segmentos: list[ReglaSegmento] | None = None
    desde_cliente: bool = False


def _job(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/segmentos")
def listar_segmentos(job_id: uuid.UUID, db: Session = Depends(get_session)) -> dict[str, Any]:
    from api.routers.results import incidencia_por_pagina

    job = _job(db, job_id)
    reglas = sg.reglas_de_config(job.config)
    resumen = job.segmentacion or None
    filas = (
        db.query(
            Url.segmento, func.count(Url.id),
            func.sum(case((Url.indexable.is_(True), 1), else_=0)),
            func.sum(case((Url.status_group == "2xx", 1), else_=0)),
        )
        .filter(Url.job_id == job_id).group_by(Url.segmento).all()
    )
    # Paginas con alguna incidencia de su pagina (no las de una por hallazgo).
    con: dict[tuple[str | None, str], int] = {}
    for seg, sev, n in (
        db.query(Url.segmento, Issue.severity, func.count(func.distinct(Issue.url_id)))
        .join(Url, Url.id == Issue.url_id)
        .filter(Issue.job_id == job_id, incidencia_por_pagina())
        .group_by(Url.segmento, Issue.severity)
    ):
        con[(seg, sev)] = n
    nombres_regla = {r.get("nombre", "").strip() for r in reglas}
    lista = []
    sin_segmentar = 0
    for seg, n, idx, ok in filas:
        if seg is None:
            sin_segmentar = n
            continue
        lista.append({
            "segmento": seg,
            "de_regla": seg in nombres_regla,
            "urls": n, "indexables": int(idx or 0), "respuestas_2xx": int(ok or 0),
            "paginas_con_error": con.get((seg, "error"), 0),
            "paginas_con_aviso": con.get((seg, "warning"), 0),
        })
    # Primero los de regla en el orden de la precedencia, luego el resto.
    orden = {r.get("nombre", "").strip(): i for i, r in reversed(list(enumerate(reglas)))}
    lista.sort(key=lambda d: (0, orden[d["segmento"]], "") if d["de_regla"]
               else (1, -d["urls"], d["segmento"]))
    return {
        "reglas": reglas,
        "segmentacion": resumen,
        # False = las reglas del censo cambiaron despues de materializar (o no
        # se ha materializado nunca): las cifras son de otras reglas.
        "al_dia": bool(resumen) and resumen.get("huella") == sg.huella(reglas),
        "sin_segmentar": sin_segmentar,
        "segmentos": lista,
    }


@router.post("/segmentos")
def resegmentar(job_id: uuid.UUID, cuerpo: Segmentar | None = None,
                db: Session = Depends(get_session)) -> dict[str, Any]:
    cuerpo = cuerpo or Segmentar()
    job = _job(db, job_id)
    if job.status in EN_MARCHA:
        raise HTTPException(status_code=409, detail=(
            f"el censo esta {job.status}: se segmentara al terminar el analisis"))
    config = dict(job.config or {})
    if cuerpo.segmentos is not None:
        reglas = [r.model_dump() for r in cuerpo.segmentos]
    elif cuerpo.desde_cliente:
        reglas = _reglas_del_cliente(db, job)
    else:
        reglas = sg.reglas_de_config(config)
    # Contra las reglas a medida de ESTE censo: una condicion `x` sobre una
    # regla que el censo no extrajo no casaria nunca y se leeria como «no hay».
    errores = sg.validar(reglas, sg.nombres_a_medida(config))
    if errores:
        raise HTTPException(status_code=422, detail="; ".join(errores))
    _candado(db, job_id)
    config["segmentos"] = reglas
    job.config = config
    db.flush()
    resumen = sg.segmentar(db, job_id, reglas)
    db.commit()
    return resumen


def _reglas_del_cliente(db: Session, job: Job) -> list[dict]:
    from api.routers.clientes import resolver_cliente_y_perfil, resolver_config

    if job.cliente_id is None and job.profile_id is None:
        raise HTTPException(status_code=422, detail="el censo no tiene cliente: manda las reglas")
    cliente, perfil = resolver_cliente_y_perfil(db, cliente_id=job.cliente_id,
                                                profile_id=job.profile_id)
    return sg.reglas_de_config(resolver_config(cliente, perfil, None))


def _candado(db: Session, job_id: uuid.UUID) -> None:
    """El mismo candado que el analisis (`shared.candados`), de transaccion: si
    alguien analiza el censo ahora, el re-segmentado espera a otro momento en
    vez de escribir encima."""
    from sqlalchemy import text

    from shared.candados import clave_de_job

    if not db.get_bind().dialect.name.startswith("postgres"):
        return
    if not db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"),
                      {"k": clave_de_job(job_id)}).scalar():
        raise HTTPException(status_code=409, detail="el censo se esta analizando ahora")
