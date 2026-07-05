"""Cola Redis del pipeline de entidades (GLiNER2).

El pipeline deja de ser un paso manual de CLI: cada vez que entra un dato
nuevo se encola el job y el worker residente del contenedor ``gliner``
(`analysis.entities.worker`) lo procesa. Disparadores:

- ``crawl``   — el worker del crawler, al completarse un rastreo.
- ``gsc``     — la API, tras importar datos de Search Console.
- ``schema``  — la API, al guardar el schema de extracción del cliente.
- ``catalog`` — la API, al tocar el catálogo de entidades a mano.
- ``manual``  — reserva para encolados explícitos.

Este módulo es deliberadamente ligero (sin torch ni imports del modelo):
lo importan la API y el worker del crawler solo para encolar y consultar
estado. La deduplicación es por job: si el job ya está en cola solo se
acumula el motivo; si está corriendo, el re-encolado provoca otra pasada
al terminar (el pipeline es re-ejecutable por diseño: reemplazo por job).
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid as uuid_mod

logger = logging.getLogger(__name__)

QUEUE_KEY = "entities:pending"
QUEUED_SET_KEY = "entities:queued"      # dedup de job_ids pendientes
STATUS_TTL = 30 * 24 * 3600             # 30 días

VALID_REASONS = ("crawl", "gsc", "schema", "catalog", "manual")


def status_key(job_id) -> str:
    return f"entities:{job_id}:status"


def reasons_key(job_id) -> str:
    return f"entities:{job_id}:reasons"


def connect(redis_url: str | None = None):
    """Conexión Redis propia (para procesos sin cliente compartido)."""
    import redis as redis_lib

    return redis_lib.Redis.from_url(
        redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def set_status(rconn, job_id, state: str, **extra) -> None:
    payload = {"state": state, "ts": time.time(), **extra}
    rconn.set(status_key(job_id), json.dumps(payload), ex=STATUS_TTL)


def get_status(rconn, job_id) -> dict | None:
    raw = rconn.get(status_key(job_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def maybe_enqueue(rconn, session, job_id, *, reason: str) -> dict:
    """Encola el pipeline de entidades del job si cumple los requisitos.

    Requisitos: job existente, rastreo ``completed``, ``client_id`` y
    schema de extracción definido para ese cliente. Devuelve siempre un
    dict con ``enqueued`` y ``why`` (nunca lanza por reglas de negocio;
    los errores de Redis/DB sí suben — usa :func:`enqueue_safe` en los
    puntos best-effort).
    """
    from shared.entity_models import ClientExtractionSchema
    from shared.models import Job

    if reason not in VALID_REASONS:
        reason = "manual"
    try:
        job_uuid = uuid_mod.UUID(str(job_id))
    except ValueError:
        return {"enqueued": False, "why": "job_id_invalido"}

    job = session.get(Job, job_uuid)
    if job is None:
        return {"enqueued": False, "why": "job_no_existe"}
    if job.status != "completed":
        return {"enqueued": False, "why": f"job_{job.status}"}
    if not job.client_id:
        return {"enqueued": False, "why": "sin_client_id"}
    if session.get(ClientExtractionSchema, job.client_id) is None:
        return {"enqueued": False, "why": "sin_schema_extraccion"}

    jid = str(job_uuid)
    rconn.sadd(reasons_key(jid), reason)
    rconn.expire(reasons_key(jid), STATUS_TTL)
    added = rconn.sadd(QUEUED_SET_KEY, jid)
    if added:
        rconn.lpush(QUEUE_KEY, jid)
        set_status(rconn, jid, "queued", reason=reason)
        return {"enqueued": True, "why": "queued"}
    return {"enqueued": False, "why": "ya_en_cola"}


def enqueue_safe(rconn, session, job_id, *, reason: str) -> dict:
    """Como :func:`maybe_enqueue` pero nunca propaga excepciones: los
    disparadores viven dentro de endpoints y del worker del crawler, y un
    Redis caído no debe tumbar la petición ni el rastreo."""
    try:
        out = maybe_enqueue(rconn, session, job_id, reason=reason)
    except Exception as exc:  # noqa: BLE001 — best-effort por contrato
        logger.warning("No se pudo encolar entidades para %s (%s): %s",
                       job_id, reason, exc)
        return {"enqueued": False, "why": "error", "error": str(exc)}
    if out.get("enqueued"):
        logger.info("Pipeline de entidades encolado para %s (motivo: %s)",
                    job_id, reason)
    return out
