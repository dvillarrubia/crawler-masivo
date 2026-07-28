"""
Job runner process.

Polls Redis for pending job IDs and executes Scrapy crawls via subprocess
(each crawl needs its own Twisted reactor, which can't be restarted).

Usage::

    python -m worker          # or:  python worker.py
    MAX_CONCURRENT_JOBS=2 python worker.py

Environment variables
---------------------
REDIS_URL              Redis connection string (default: redis://localhost:6379/0)
DATABASE_URL           PostgreSQL connection string
MAX_CONCURRENT_JOBS    How many crawls to run in parallel (default: 2)
BRPOP_TIMEOUT          Seconds to block on Redis pop (default: 5)
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime, timedelta, timezone

import redis as redis_lib

# Ensure the project root is on sys.path so ``shared`` imports resolve.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Nivel configurable por entorno. La salida de Scrapy se registra en DEBUG, y
# como el subproceso no escribe en el stdout del worker, con INFO no habia
# forma de diagnosticar un crawl raro sin tocar codigo: LOG_LEVEL=DEBUG lo
# expone sin reconstruir la imagen.
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("worker")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "2"))
BRPOP_TIMEOUT = int(os.getenv("BRPOP_TIMEOUT", "5"))
STALE_JOB_MINUTES = int(os.getenv("STALE_JOB_MINUTES", "30"))
JOBS_QUEUE = "jobs:pending"

# Path to the crawler directory (where scrapy.cfg lives)
_CRAWLER_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_shutdown_event = threading.Event()


def _signal_handler(signum, frame):
    logger.info("Received signal %s -- shutting down gracefully", signum)
    _shutdown_event.set()


# ---------------------------------------------------------------------------
# Single-job execution
# ---------------------------------------------------------------------------
def _run_job(job_id: str) -> None:
    """Execute a single Scrapy crawl for *job_id* via subprocess."""
    from shared.database import SessionLocal
    from shared.models import Job

    session = SessionLocal()
    try:
        job = session.query(Job).filter(Job.id == job_id).one_or_none()
        if job is None:
            logger.error("Job %s not found in database, skipping", job_id)
            return
        if job.status == "cancelled":
            logger.info("Job %s already cancelled, skipping", job_id)
            return

        # Mark running
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        job_config = job.config if job.config else {}  # save before closing session
        session.commit()
        logger.info("Job %s marked as running", job_id)
    except Exception:
        session.rollback()
        logger.exception("Failed to update job %s status", job_id)
        return
    finally:
        session.close()

    # -- Run Scrapy as subprocess (avoids Twisted reactor restart issues) --
    final_status = "completed"
    max_runtime_hours = max(1, min(int(job_config.get("crawl_behavior", {}).get("max_runtime_hours", 6)), 72))
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            _CRAWLER_DIR + os.pathsep +
            _PROJECT_ROOT + os.pathsep +
            env.get("PYTHONPATH", "")
        )

        # Build Scrapy command with per-job overrides
        cmd = [
            sys.executable, "-m", "scrapy", "crawl", "seo",
            "-a", f"job_id={job_id}",
        ]

        # -- Concurrencia efectiva ---------------------------------------
        # Con render_js el tope se aplica como min() sobre lo que pida el job,
        # no solo cuando el campo falta.
        #
        # Antes se saltaba por dos motivos a la vez: (1) la API rellena SIEMPRE
        # concurrent_requests con su default (32), asi que la condicion
        # "not in job_config" no se cumplia nunca; y (2) aunque se hubiera
        # cumplido, el bloque de overrides volvia a emitir el flag despues y
        # ganaba por ser el ultimo. El resultado es que el tope documentado
        # para JS no llego a aplicarse jamas.
        #
        # El valor importa: medido sobre un sitio real en el perfil local
        # (2 CPU / 2 GB), con 8 fallaba el 59,6% de las URLs por "Page.goto:
        # Timeout exceeded" y se guardaban con status_code NULL. Con 4 el fallo
        # baja al 0% y ademas termina antes que con 2 (360s frente a 440s). Un
        # VPS con mas CPU puede subirlo por entorno.
        js_rendering = job_config.get("render_js", False)
        concurrent = job_config.get("concurrent_requests")
        concurrent_per_domain = job_config.get("concurrent_requests_per_domain")
        if js_rendering:
            js_concurrent = int(os.getenv("JS_CONCURRENT_REQUESTS", "4"))
            js_per_domain = int(os.getenv("JS_CONCURRENT_PER_DOMAIN", "4"))
            concurrent = min(concurrent, js_concurrent) if concurrent else js_concurrent
            concurrent_per_domain = (
                min(concurrent_per_domain, js_per_domain)
                if concurrent_per_domain
                else js_per_domain
            )
            logger.info(
                "JS rendering: concurrencia limitada a %s (%s por dominio)",
                concurrent,
                concurrent_per_domain,
            )

        # Apply job-level Scrapy settings overrides
        if concurrent is not None:
            cmd += ["-s", f"CONCURRENT_REQUESTS={concurrent}"]
        if concurrent_per_domain is not None:
            cmd += ["-s", f"CONCURRENT_REQUESTS_PER_DOMAIN={concurrent_per_domain}"]
        robots_mode = job_config.get("robots_mode", "respect")
        if robots_mode == "ignore":
            cmd += ["-s", "ROBOTSTXT_OBEY=False"]
        elif robots_mode == "audit":
            # Disable built-in blocking middleware; enable our audit one.
            cmd += ["-s", "ROBOTSTXT_OBEY=False", "-s", "ROBOTS_MODE=audit"]
        if job_config.get("user_agent"):
            cmd += ["-s", f"USER_AGENT={job_config['user_agent']}"]
        if job_config.get("impersonate"):
            cmd += ["-s", f"IMPERSONATE={job_config['impersonate']}"]

        # Advanced crawl behavior settings
        crawl_behavior = job_config.get("crawl_behavior", {})
        if crawl_behavior.get("download_timeout", 30) != 30:
            cmd += ["-s", f"DOWNLOAD_TIMEOUT={crawl_behavior['download_timeout']}"]
        if crawl_behavior.get("retry_count", 2) != 2:
            cmd += ["-s", f"RETRY_TIMES={crawl_behavior['retry_count']}"]
        if crawl_behavior.get("request_delay", 0) > 0:
            cmd += ["-s", f"DOWNLOAD_DELAY={crawl_behavior['request_delay']}"]
        if not crawl_behavior.get("autothrottle_enabled", True):
            cmd += ["-s", "AUTOTHROTTLE_ENABLED=False"]
        elif crawl_behavior.get("autothrottle_target_concurrency", 8.0) != 8.0:
            cmd += ["-s", f"AUTOTHROTTLE_TARGET_CONCURRENCY={crawl_behavior['autothrottle_target_concurrency']}"]

        result = subprocess.run(
            cmd,
            cwd=_CRAWLER_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=3600 * max_runtime_hours,
        )

        # Avisos del spider. Su salida es la de un subproceso que aqui se
        # vuelca en DEBUG, asi que sin esto no aparecen en ningun sitio: una
        # pagina perdida se guarda con status_code NULL y un sitemap leido a
        # medias deja datos incompletos, ambos sin rastro operativo.
        #
        # Se filtra por el logger del spider a proposito, para no arrastrar las
        # deprecaciones de Scrapy ni el ruido de librerias. Se agrupan por tipo
        # de mensaje para que un crawl con cientos de fallos no inunde el log.
        if result.stderr:
            avisos: dict[str, list[str]] = {}
            for ln in result.stderr.splitlines():
                # "[seo_crawler." con corchete: es el nombre del logger. Sin el
                # corchete tambien casaria la ruta del fichero que imprime
                # py.warnings en las deprecaciones de Scrapy.
                if "WARNING" not in ln or "[seo_crawler." not in ln:
                    continue
                msg = ln.split("WARNING:", 1)[-1].strip()
                clave = msg.split(":", 1)[0][:60]
                avisos.setdefault(clave, []).append(msg)
            for clave, msgs in avisos.items():
                logger.warning(
                    "Job %s: %d aviso(s) de '%s'. Ejemplo: %s",
                    job_id,
                    len(msgs),
                    clave,
                    msgs[0][:180],
                )

        # Always log last portion of stderr for debugging
        if result.stderr:
            logger.debug(
                "Scrapy stderr for job %s:\n%s",
                job_id,
                result.stderr[-3000:],
            )

        if result.returncode != 0:
            logger.error(
                "Scrapy exited with code %d for job %s:\nSTDERR: %s",
                result.returncode,
                job_id,
                result.stderr[-2000:] if result.stderr else "(empty)",
            )
            final_status = "failed"
        else:
            logger.info("Scrapy crawl finished successfully for job %s", job_id)

    except subprocess.TimeoutExpired:
        logger.error(
            "Crawl timed out for job %s after %d hour(s)",
            job_id,
            max_runtime_hours,
        )
        final_status = "failed"
    except Exception:
        logger.exception("Crawl failed for job %s", job_id)
        final_status = "failed"

    # -- Post-crawl: move to 'analyzing' before running analysis --
    # The job must NOT be marked 'completed' until analysis has populated the
    # issues/indexability/pagerank data, or the UI shows a completed job with
    # an empty issues table. The intermediate 'analyzing' status also keeps
    # stale-job recovery (which only targets 'running') from re-queuing the
    # job while analysis runs with the spider — and its heartbeat — stopped.
    cancelled = False
    session = SessionLocal()
    try:
        job = session.query(Job).filter(Job.id == job_id).one_or_none()
        if job and job.status == "cancelled":
            cancelled = True
        elif job and final_status == "completed":
            job.status = "analyzing"
            session.commit()
    except Exception:
        session.rollback()
        logger.exception("Failed to update job %s status", job_id)
    finally:
        session.close()

    if cancelled:
        final_status = "cancelled"

    # -- Trigger analysis (best-effort) while status is 'analyzing' --
    if final_status == "completed" and not cancelled:
        _trigger_analysis(job_id)

    # -- Finalise status --
    session = SessionLocal()
    try:
        job = session.query(Job).filter(Job.id == job_id).one_or_none()
        if job:
            # A cancel that arrived during analysis still wins.
            if job.status == "cancelled":
                final_status = "cancelled"
            job.status = final_status
            job.completed_at = datetime.now(timezone.utc)

            # Motivo real de finalizacion. El spider marca en Redis cuando
            # corta por el tope de URLs; sin esto, un crawl truncado quedaba
            # indistinguible de uno completo y el PageRank se presentaba como
            # bueno estando calculado sobre una parte del sitio.
            motivo = "finished"
            try:
                rc = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
                if rc.get(f"job:{job_id}:finish_reason") == "max_urls_reached":
                    motivo = "max_urls_reached"
                rc.delete(f"job:{job_id}:finish_reason")
            except Exception:
                pass
            job.finish_reason = motivo
            session.commit()
            if motivo == "max_urls_reached":
                logger.warning(
                    "Job %s TRUNCADO por el tope de URLs: el rastreo esta "
                    "incompleto y el PageRank se ha calculado sobre un grafo "
                    "parcial",
                    job_id,
                )
            logger.info("Job %s finished with status: %s", job_id, final_status)
    except Exception:
        session.rollback()
        logger.exception("Failed to finalise job %s", job_id)
    finally:
        session.close()


def _trigger_analysis(job_id: str) -> None:
    """Import and invoke the analyzer."""
    try:
        from analysis.analyzer import run_analysis

        logger.info("Triggering analysis for job %s", job_id)
        run_analysis(str(job_id))
        logger.info("Analysis completed for job %s", job_id)
    except ImportError:
        logger.info(
            "Analyzer module not available; skipping analysis for job %s",
            job_id,
        )
    except Exception:
        logger.exception("Analysis failed for job %s", job_id)


# ---------------------------------------------------------------------------
# Stale job recovery
# ---------------------------------------------------------------------------
def _recover_stale_jobs(rconn: redis_lib.Redis) -> None:
    """Re-queue jobs stuck in 'running' with no recent activity.

    This handles the case where a worker crashed or was restarted while a job
    was in progress.  Jobs whose ``started_at`` is older than
    ``STALE_JOB_MINUTES`` are reset to ``pending`` and pushed back onto the
    queue so another worker picks them up.
    """
    from shared.database import SessionLocal
    from shared.models import Job

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_JOB_MINUTES)
    cutoff_ts = cutoff.timestamp()

    session = SessionLocal()
    try:
        candidates = (
            session.query(Job)
            .filter(Job.status == "running", Job.started_at < cutoff)
            .all()
        )

        # A job started long ago is only *stale* if it is not still making
        # progress. The spider writes a Redis heartbeat as it crawls; if that
        # heartbeat is recent the crawl is alive (long crawls can run for
        # hours) and must NOT be re-queued, or two workers would crawl the
        # same job and double-write its data.
        stale = []
        for job in candidates:
            try:
                hb = rconn.get(f"job:{job.id}:heartbeat")
            except Exception:
                hb = None
            if hb is not None:
                try:
                    if float(hb) >= cutoff_ts:
                        continue  # alive — skip
                except (TypeError, ValueError):
                    pass
            stale.append(job)

        for job in stale:
            job.status = "pending"
            job.started_at = None
            logger.warning(
                "Recovering stale job %s (%s) — re-queuing", job.id, job.name,
            )
        session.commit()

        for job in stale:
            rconn.rpush(JOBS_QUEUE, str(job.id))

        if stale:
            logger.info("Recovered %d stale job(s)", len(stale))
    except Exception:
        session.rollback()
        logger.exception("Failed to recover stale jobs")
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> None:
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info(
        "Worker starting (max_concurrent=%d, queue=%s, redis=%s)",
        MAX_CONCURRENT_JOBS,
        JOBS_QUEUE,
        REDIS_URL,
    )

    # socket_timeout con holgura sobre BRPOP_TIMEOUT. redis-py >=8 fija el
    # plazo de lectura del socket al timeout del propio comando bloqueante, asi
    # que el socket expiraba en el mismo instante en que el servidor mandaba la
    # respuesta vacia: BRPOP lanzaba TimeoutError en CADA sondeo en vez de
    # devolver None. El except de abajo lo absorbia, pero dejaba un warning cada
    # pocos segundos y sumaba la espera de reintento a cada vuelta.
    rconn = redis_lib.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_timeout=BRPOP_TIMEOUT + 10,
    )
    try:
        rconn.ping()
    except redis_lib.ConnectionError:
        logger.critical("Cannot connect to Redis at %s", REDIS_URL)
        sys.exit(1)

    _recover_stale_jobs(rconn)

    executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS)
    active_futures: dict[str, Future] = {}

    try:
        while not _shutdown_event.is_set():
            # Clean up finished futures
            done_ids = [
                jid for jid, fut in active_futures.items() if fut.done()
            ]
            for jid in done_ids:
                fut = active_futures.pop(jid)
                exc = fut.exception()
                if exc:
                    logger.error("Job %s raised: %s", jid, exc)

            # Wait if at capacity
            if len(active_futures) >= MAX_CONCURRENT_JOBS:
                time.sleep(1)
                continue

            # Poll for a new job. Blocking pops can raise transient
            # TimeoutError/ConnectionError (e.g. the socket read timing out
            # around the BRPOP window, or Redis briefly unavailable during a
            # deploy). These must NOT kill the worker — otherwise the process
            # exits, the container restarts, and any in-flight crawl is killed
            # in a crash loop. Swallow them and keep polling.
            try:
                result = rconn.brpop(JOBS_QUEUE, timeout=BRPOP_TIMEOUT)
            except (redis_lib.exceptions.TimeoutError, redis_lib.exceptions.ConnectionError) as exc:
                logger.warning("Redis poll error (%s); retrying", exc)
                time.sleep(1)
                continue
            if result is None:
                continue

            _, job_id = result
            job_id = job_id.strip()
            if not job_id:
                continue

            if job_id in active_futures:
                logger.warning("Job %s is already running, skipping duplicate", job_id)
                continue

            logger.info("Dequeued job %s", job_id)
            future = executor.submit(_run_job, job_id)
            active_futures[job_id] = future

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down")
    finally:
        logger.info("Waiting for %d active job(s) to finish ...", len(active_futures))
        executor.shutdown(wait=True)
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
