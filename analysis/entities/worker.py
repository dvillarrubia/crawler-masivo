"""Worker residente del pipeline de entidades (GLiNER2).

Consume la cola Redis ``entities:pending`` (ver ``shared/entities_queue.py``)
y ejecuta el pipeline por job según los datos que hayan entrado: el flujo
se dispara solo (crawl completado, import de GSC, schema guardado,
catálogo tocado), sin CLI. La ejecución manual puntual sigue disponible
en ``analysis.entities.run``.

Uso (servicio ``gliner`` de docker-compose)::

    python -m analysis.entities.worker

Variables de entorno: REDIS_URL, DATABASE_URL, ENTITIES_OUTPUT_DIR
(default ``informes``), ENTITIES_BRPOP_TIMEOUT (default 5).

Los imports pesados (torch/GLiNER2) se hacen dentro de las funciones y
solo cuando la pasada necesita el modelo: un disparo por catálogo no
carga torch.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
import uuid as uuid_mod

# Raíz del proyecto en sys.path (mismo patrón que crawler/worker.py)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("entities.worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
BRPOP_TIMEOUT = int(os.getenv("ENTITIES_BRPOP_TIMEOUT", "5"))
OUTPUT_DIR = os.getenv("ENTITIES_OUTPUT_DIR", "informes")
MIN_IMPRESSIONS = int(os.getenv("ENTITIES_MIN_IMPRESSIONS", "10"))

_shutdown_event = threading.Event()


def _signal_handler(signum, frame):
    logger.info("Señal %s recibida — parada ordenada", signum)
    _shutdown_event.set()


# ---------------------------------------------------------------------------
# Selección de pasos (pura, testeada en tests/python/test_entities_queue.py)
# ---------------------------------------------------------------------------
def select_steps(reasons: set[str], has_page_entities: bool) -> list[str]:
    """Qué pasos correr según qué dato disparó la pasada.

    - ``crawl``/``schema``/``manual`` (o un job aún sin extraer) → todo.
    - ``gsc`` → re-extraer queries y re-cruzar, sin re-pasar el modelo
      por las páginas si ya están extraídas.
    - ``catalog`` → solo re-resolver y re-cruzar (no carga el modelo).
    """
    full = bool(reasons & {"crawl", "schema", "manual"}) or not has_page_entities
    steps: list[str] = []
    if full:
        steps.append("pages")
    if full or "gsc" in reasons:
        steps.append("queries")
    steps += ["catalog", "resolve", "report"]
    return steps


# ---------------------------------------------------------------------------
# Adapter cacheado: recargar torch + pesos por cada pasada del mismo
# cliente sería lo caro; se conserva el último (un solo modelo en RAM).
# ---------------------------------------------------------------------------
_adapter_cache: dict = {"yaml": None, "adapter": None}


def _get_adapter(schema, yaml_text: str):
    if _adapter_cache["yaml"] == yaml_text and _adapter_cache["adapter"] is not None:
        return _adapter_cache["adapter"]
    from analysis.entities.gliner_adapter import DEFAULT_MODEL, Gliner2Adapter

    adapter = Gliner2Adapter(schema, model_name=os.getenv("GLINER_MODEL") or DEFAULT_MODEL)
    _adapter_cache.update(yaml=yaml_text, adapter=adapter)
    return adapter


def _gemini_key(session, client_id: str) -> str | None:
    """API key de Gemini: la cuenta del cliente si está configurada
    (Configuración → Cuentas), si no la primera de la tabla."""
    from shared.entity_models import ClientSettings
    from shared.semantic_models import GeminiAccount

    settings = session.get(ClientSettings, client_id)
    if settings is not None and settings.gemini_account_id:
        acc = session.get(GeminiAccount, settings.gemini_account_id)
        if acc is not None:
            return acc.api_key
    acc = session.query(GeminiAccount).first()
    return acc.api_key if acc is not None else None


# ---------------------------------------------------------------------------
# Pasada completa de un job
# ---------------------------------------------------------------------------
def process_job(session, rconn, job_id_str: str) -> None:
    from shared.entities_queue import reasons_key, set_status
    from shared.entity_models import GlinerPageEntity
    from shared.models import Job

    job_id = uuid_mod.UUID(job_id_str)
    reasons = set(rconn.smembers(reasons_key(job_id_str)) or ()) or {"manual"}
    rconn.delete(reasons_key(job_id_str))

    job = session.get(Job, job_id)
    if job is None or not job.client_id:
        set_status(rconn, job_id_str, "failed", error="job sin client_id o inexistente")
        return
    client_id = job.client_id

    from analysis.entities.schema_config import SchemaError, load_client_schema
    from shared.entity_models import ClientExtractionSchema

    try:
        schema = load_client_schema(session, client_id)
    except SchemaError as exc:
        set_status(rconn, job_id_str, "failed", error=str(exc))
        return
    yaml_text = session.get(ClientExtractionSchema, client_id).yaml_text

    has_pages = session.query(
        session.query(GlinerPageEntity)
        .filter(GlinerPageEntity.job_id == job_id).exists()
    ).scalar()
    steps = select_steps(reasons, bool(has_pages))
    set_status(rconn, job_id_str, "running",
               reasons=sorted(reasons), steps=steps)
    logger.info("Job %s: pasada por %s (motivos: %s)",
                job_id, ",".join(steps), ",".join(sorted(reasons)))

    stats: dict = {}
    notes: list[str] = []
    t0 = time.monotonic()

    adapter = None
    if {"pages", "queries"} & set(steps):
        adapter = _get_adapter(schema, yaml_text)

    if "pages" in steps:
        from analysis.entities.pipeline import extract_pages

        stats["pages"] = extract_pages(session, job_id, schema, adapter)
        session.commit()

    if "queries" in steps:
        from analysis.entities.pipeline import extract_queries

        stats["queries"] = extract_queries(
            session, job_id, schema, adapter, min_impressions=MIN_IMPRESSIONS)
        session.commit()

    key = _gemini_key(session, client_id)
    if key is None:
        notes.append("sin cuenta Gemini: catálogo y resolución omitidos "
                     "(créala en la consola → Cuentas)")
        steps = [s for s in steps if s not in ("catalog", "resolve")]

    if "catalog" in steps:
        from analysis.entities.resolve import (
            GeminiEntityEmbedder, embed_catalog, seed_catalog_from_crawl,
        )

        if schema.catalogo_fuente == "generado":
            stats["catalog_seeded"] = seed_catalog_from_crawl(
                session, client_id, job_id, schema)
        stats["catalog_embedded"] = embed_catalog(
            session, client_id, GeminiEntityEmbedder(key))
        session.commit()

    if "resolve" in steps:
        from analysis.entities.resolve import (
            GeminiEntityEmbedder, GeminiFlashJudge, resolve_job,
        )

        stats["resolve"] = resolve_job(
            session, job_id, client_id, schema,
            GeminiEntityEmbedder(key), GeminiFlashJudge(key))
        session.commit()

    if "report" in steps:
        from analysis.entities.report import build_report, write_outputs

        report = build_report(session, job_id, client_id)
        stats["report"] = write_outputs(session, job_id, report,
                                        output_dir=OUTPUT_DIR)
        session.commit()

    elapsed = round(time.monotonic() - t0, 1)
    state = "partial" if notes else "done"
    set_status(rconn, job_id_str, state, reasons=sorted(reasons),
               steps=steps, stats=_jsonable(stats), notes=notes,
               seconds=elapsed)
    logger.info("Job %s: pipeline de entidades %s en %.1fs — %s",
                job_id, state, elapsed, stats)


def _jsonable(value):
    """Los stats de los pasos son dicts de números/strings; cualquier cosa
    exótica se degrada a str para no romper el status JSON."""
    import json

    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# Bucle principal
# ---------------------------------------------------------------------------
def main() -> None:
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    import redis as redis_lib

    from shared.entities_queue import (
        QUEUE_KEY, QUEUED_SET_KEY, connect, set_status,
    )

    logger.info("Worker de entidades arrancando (queue=%s, redis=%s)",
                QUEUE_KEY, REDIS_URL)
    rconn = connect(REDIS_URL)
    try:
        rconn.ping()
    except redis_lib.ConnectionError:
        logger.critical("Sin conexión a Redis en %s", REDIS_URL)
        sys.exit(1)

    from shared.database import SessionLocal, init_db

    try:
        init_db()
    except Exception:
        logger.exception("init_db falló; sigo (la API suele ser la dueña)")

    while not _shutdown_event.is_set():
        try:
            result = rconn.brpop(QUEUE_KEY, timeout=BRPOP_TIMEOUT)
        except (redis_lib.exceptions.TimeoutError, redis_lib.exceptions.ConnectionError) as exc:
            logger.warning("Error de poll en Redis (%s); reintento", exc)
            time.sleep(1)
            continue
        if result is None:
            continue
        _, job_id = result
        job_id = job_id.strip()
        if not job_id:
            continue
        # Fuera del set de dedup ANTES de procesar: un dato nuevo que
        # entre durante la pasada debe re-encolar otra pasada.
        rconn.srem(QUEUED_SET_KEY, job_id)

        session = SessionLocal()
        try:
            process_job(session, rconn, job_id)
        except Exception as exc:
            session.rollback()
            logger.exception("Pipeline de entidades falló para %s", job_id)
            try:
                set_status(rconn, job_id, "failed", error=str(exc)[:500])
            except Exception:
                pass
        finally:
            session.close()

    logger.info("Worker de entidades parado")


if __name__ == "__main__":
    main()
