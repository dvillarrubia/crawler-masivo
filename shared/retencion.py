"""Retencion de censos por perfil (decision 223).

Con `config.retencion.activo`, al cerrar un rastreo de un perfil se vacian sus
terminados mas antiguos que los `conservar` censos completados mas recientes.
Vaciar es borrar lo que pesa (URLs y todo lo que cuelga de ellas, enlaces,
incidencias, el Parquet y las capturas) y dejar la fila de `jobs` con
`status='vaciado'` y su fila de `profile_runs`: la evolucion del perfil
conserva el punto.

`vaciado` no es `completed`: la comparacion, la base del incremental, el
archivado, los enlaces compartidos y los exports buscan `completed` y no lo
eligen nunca. Solo la evolucion lo acepta (`shared/evolucion.py`).
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFECTO: dict[str, Any] = {"activo": False, "conservar": 8}
# Lo que se puede vaciar: lo que ha terminado. Un `vaciado` vuelve a entrar
# si se quedo a medias (se agoto el tiempo): se termina de vaciar.
_VACIABLES = ("completed", "failed", "stalled", "cancelled", "vaciado")
TROZO = 200_000


def config_de(config: dict | None) -> dict[str, Any]:
    propio = (config or {}).get("retencion") or {}
    cfg = {**DEFECTO, **{k: v for k, v in propio.items() if v is not None}}
    cfg["conservar"] = max(2, int(cfg["conservar"]))
    return cfg


def a_vaciar(db, profile_id, conservar: int) -> list:
    """Los jobs del perfil que sobran: los terminados creados antes del
    `conservar`-esimo completado mas reciente. Los mas nuevos, aunque hayan
    fallado, se quedan; los vaciados que ya no tienen URLs, tambien."""
    from shared.models import Job, Url

    jobs = (db.query(Job).filter(Job.profile_id == profile_id, Job.status.in_(_VACIABLES))
            .order_by(Job.created_at.desc()).all())
    completos = [j for j in jobs if j.status == "completed"]
    if len(completos) < conservar:
        return []
    corte = completos[conservar - 1].created_at
    referencias = _referencias_de_programaciones(db, profile_id)
    salida = []
    for j in jobs:
        if j.created_at is None or j.created_at >= corte or j.id in referencias:
            continue
        if j.status == "vaciado" and db.query(Url.id).filter(Url.job_id == j.id).first() is None:
            continue
        salida.append(j)
    return salida


def _referencias_de_programaciones(db, profile_id) -> set:
    """El ultimo completado de cada programacion activa del perfil: el
    siguiente rastreo de esa serie se compara con el (decisiones 78 y 86), y
    no se vacia aunque haya `conservar` mas nuevos lanzados a mano."""
    from sqlalchemy import func

    from shared.models import Job, Programacion

    ultimos = (db.query(Job.programacion_id, func.max(Job.created_at))
               .join(Programacion, Programacion.id == Job.programacion_id)
               .filter(Job.profile_id == profile_id, Job.status == "completed",
                       Programacion.activa.is_(True))
               .group_by(Job.programacion_id).all())
    ids = set()
    for programacion_id, creado in ultimos:
        fila = (db.query(Job.id).filter(Job.programacion_id == programacion_id,
                                        Job.status == "completed", Job.created_at == creado)
                .first())
        if fila is not None:
            ids.add(fila[0])
    return ids


def vaciar(db, job, *, hasta: float | None = None) -> dict[str, Any]:
    """Vacia un job (ver el docstring del modulo). Por trozos con un commit
    por trozo; si se agota `hasta` (`time.monotonic`) lo deja `vaciado` a
    medias y la siguiente pasada sigue. Antes de borrar nada se asegura de que
    su punto de evolucion existe: sin el, no se vacia."""
    from shared import archivo_enlaces, capturas_pantalla
    from shared.borrado import borrar_filas_del_job
    from shared.evolucion import registrar
    from shared.models import ProfileRun, UrlState

    if job.status != "vaciado":
        if job.status == "completed":
            registrar(db, job.id, solo_si_falta=True)
            db.commit()
        if db.query(ProfileRun.job_id).filter(ProfileRun.job_id == job.id).first() is None \
                and job.status == "completed":
            return {"vaciado": False, "motivo": "sin_evolucion"}
        job.status = "vaciado"
        db.query(UrlState).filter(UrlState.last_job_id == job.id).update(
            {UrlState.last_job_id: None}, synchronize_session=False)
        db.commit()
    filas: dict[str, int] = {}
    completo = borrar_filas_del_job(db, job.id, filas, trozo=TROZO, hasta=hasta,
                                    conservar_evolucion=True)
    db.commit()
    if completo:
        archivo_enlaces.borrar(job.id)
        capturas_pantalla.borrar(job.id)
    return {"vaciado": completo, "filas": filas}


def aplicar(db, job_id, *, segundos: float = 300) -> dict[str, Any] | None:
    """La retencion del perfil del job que acaba de cerrar. None si no aplica
    (sin perfil o apagada). Cada job a vaciar, bajo el candado del censo
    (decisiones 93 y 95): si alguien lo esta analizando o archivando, se salta
    y lo coge la siguiente pasada."""
    from analysis.analyzer import candado_de_job
    from shared.models import CrawlProfile, Job

    job = db.get(Job, job_id)
    if job is None or job.profile_id is None:
        return None
    perfil = db.get(CrawlProfile, job.profile_id)
    cfg = config_de(perfil.config if perfil is not None else None)
    if not cfg["activo"]:
        return None
    hasta = time.monotonic() + segundos
    resumen: dict[str, Any] = {"conservar": cfg["conservar"], "vaciados": [], "saltados": []}
    for viejo in a_vaciar(db, job.profile_id, cfg["conservar"]):
        if time.monotonic() >= hasta:
            resumen["saltados"].append({"job_id": str(viejo.id), "motivo": "sin_tiempo"})
            continue
        with candado_de_job(str(viejo.id)) as puedo:
            if not puedo:
                resumen["saltados"].append({"job_id": str(viejo.id), "motivo": "candado"})
                continue
            r = vaciar(db, viejo, hasta=hasta)
        (resumen["vaciados"] if r["vaciado"] else resumen["saltados"]).append(
            {"job_id": str(viejo.id), **({} if r["vaciado"] else {"motivo": r.get("motivo", "sin_tiempo")})})
    return resumen
