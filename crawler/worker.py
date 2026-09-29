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
import re
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
class EstancadoError(Exception):
    """El rastreo dejo de avanzar: latido sin moverse durante el margen dado."""


def _ejecutar_con_vigilancia(
    cmd: list[str],
    *,
    cwd: str,
    env: dict,
    job_id: str,
    stall_minutes: int,
    max_seconds: float,
) -> subprocess.CompletedProcess:
    """Lanza Scrapy y lo mata si deja de avanzar (o si revienta el tope duro).

    Devuelve lo mismo que subprocess.run. Lanza EstancadoError si el latido se
    queda congelado, y subprocess.TimeoutExpired si se agota el tope de horas.
    """
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )

    rc = None
    try:
        rc = redis_lib.Redis.from_url(
            REDIS_URL, decode_responses=True, socket_timeout=15
        )
    except Exception:
        logger.warning("Sin Redis: el rastreo corre sin vigilancia de estancamiento")

    inicio = time.monotonic()
    ultimo_latido = None
    visto_en = inicio
    intervalo = 30  # cada cuanto se comprueba

    while True:
        try:
            salida, error = proc.communicate(timeout=intervalo)
            return subprocess.CompletedProcess(cmd, proc.returncode, salida, error)
        except subprocess.TimeoutExpired:
            pass  # sigue vivo: toca comprobar

        ahora = time.monotonic()

        if max_seconds and (ahora - inicio) > max_seconds:
            proc.kill()
            proc.communicate()
            raise subprocess.TimeoutExpired(cmd, max_seconds)

        if rc is None or stall_minutes <= 0:
            continue

        try:
            latido = rc.get(f"job:{job_id}:heartbeat")
        except Exception:
            continue  # un fallo de Redis no debe matar un rastreo sano

        if latido != ultimo_latido:
            ultimo_latido, visto_en = latido, ahora
            continue

        parado = (ahora - visto_en) / 60
        if parado >= stall_minutes:
            logger.error(
                "Job %s ESTANCADO: %.0f min sin avanzar (margen %d min). "
                "Se detiene; lo rastreado se conserva y se analiza.",
                job_id, parado, stall_minutes,
            )
            proc.kill()
            proc.communicate()
            raise EstancadoError(f"{parado:.0f} min sin avanzar")


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
    timed_out = False
    stalled = False
    sin_guardar = False
    analisis_ok = True
    max_runtime_hours = max(1, min(int(job_config.get("crawl_behavior", {}).get("max_runtime_hours", 72)), 720))
    # 0 = sin vigilancia. Por defecto 30 min: el latido se estampa cada 50
    # URLs y en cada lote de siembra, asi que media hora sin moverse es un
    # rastreo muerto incluso yendo despacio.
    stall_minutes = max(0, int(job_config.get("crawl_behavior", {}).get("stall_timeout_minutes", 30)))
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

        # Vigilancia por ESTANCAMIENTO, no por reloj.
        #
        # Un tope de horas no protege de nada util: hay que adivinarlo antes de
        # conocer el trabajo, y el mismo sitio rinde distinto segun el momento
        # (medido en un caso real: 613 ms sin render y 4.097 ms con el, siete
        # veces mas). El resultado fue cortar un rastreo al 97,5%, con 1.051
        # URLs de 42.000 pendientes. Y al reves: un rastreo colgado seguia vivo
        # hasta agotar el plazo, tres dias en el peor caso.
        #
        # Lo que importa es si AVANZA. El spider estampa un latido en Redis al
        # actualizar progreso y al sembrar la frontera; si ese latido deja de
        # moverse, el rastreo esta muerto aunque le sobren horas. Un rastreo
        # lento pero sano sigue, tarde lo que tarde.
        #
        # max_runtime_hours se conserva como red de seguridad dura, no como
        # mecanismo principal.
        result = _ejecutar_con_vigilancia(
            cmd,
            cwd=_CRAWLER_DIR,
            env=env,
            job_id=job_id,
            stall_minutes=stall_minutes,
            max_seconds=3600 * max_runtime_hours,
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
            avisos, errores = resumir_stderr(result.stderr)
            for clave, msgs in avisos.items():
                logger.warning(
                    "Job %s: %d aviso(s) de '%s'. Ejemplo: %s",
                    job_id,
                    len(msgs),
                    clave,
                    msgs[0][:180],
                )
            for clave, msgs in errores.items():
                logger.error(
                    "Job %s: %d error(es) de '%s'. Ejemplo: %s",
                    job_id,
                    len(msgs),
                    clave,
                    msgs[0][:400],
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
            # Rastrear no es guardar. Si el pipeline falla en cada escritura
            # (una columna que falta, la BD caida a mitad), Scrapy termina con
            # codigo 0 y el job quedaba `completed` con cero filas. Medido: un
            # censo de 289 URLs rastreadas y 0 guardadas, sin un solo aviso.
            rastreadas, guardadas = _rastreadas_y_guardadas(job_id)
            if rastreadas and not guardadas:
                logger.error(
                    "Job %s: se rastrearon %d URLs y no se guardo NINGUNA. El "
                    "pipeline fallo al escribir (ver errores arriba); el job se "
                    "marca como fallido",
                    job_id, rastreadas,
                )
                final_status = "failed"
                sin_guardar = True

    except EstancadoError as exc:
        # Igual que el tope de tiempo: lo rastreado es valido y se analiza. Lo
        # que NO se hace es fingir que el rastreo termino bien — finish_reason
        # queda en "stalled" para que el dato no se confunda con uno completo.
        logger.warning(
            "Job %s detenido por estancamiento (%s): se analiza lo obtenido",
            job_id, exc,
        )
        stalled = True
    except subprocess.TimeoutExpired:
        # Agotar el tiempo NO es un fallo: lo rastreado hasta ahi es valido y
        # normalmente casi completo. Marcarlo "failed" impedia que corriese el
        # analisis —que solo se lanza con estado "completed"— y tiraba a la
        # basura el trabajo. Caso real: 24 h de rastreo, 41.287 URLs con solo
        # 1.051 pendientes, y cero incidencias y cero PageRank calculados.
        #
        # Se trata como el tope de URLs: dato PARCIAL pero utilizable, se
        # analiza, y finish_reason lo deja claro para que nadie lo confunda con
        # un rastreo completo.
        logger.warning(
            "Job %s TRUNCADO por el limite de %d hora(s): el rastreo esta "
            "incompleto pero lo obtenido se analiza igualmente",
            job_id,
            max_runtime_hours,
        )
        timed_out = True
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
        analisis_ok = _trigger_analysis(job_id)
        if not job_config.get("render_js", False):
            _comprobar_render_js(job_id)

    # -- Finalise status --
    session = SessionLocal()
    try:
        job = session.query(Job).filter(Job.id == job_id).one_or_none()
        if job:
            # A cancel that arrived during analysis still wins.
            if job.status == "cancelled":
                final_status = "cancelled"
            elif not analisis_ok:
                # Un job `completed` sin issues ni PageRank se lee como "sitio
                # limpio". Si el analisis revienta, el job no ha terminado: se
                # marca fallido. Lo rastreado es valido (finish_reason sigue
                # diciendo como acabo el rastreo) y el analisis se puede
                # relanzar con `python -m analysis.analyzer <job_id>`.
                final_status = "failed"
            job.status = final_status
            job.completed_at = datetime.now(timezone.utc)

            # Motivo real de finalizacion. El spider marca en Redis cuando
            # corta por el tope de URLs; sin esto, un crawl truncado quedaba
            # indistinguible de uno completo y el PageRank se presentaba como
            # bueno estando calculado sobre una parte del sitio.
            # OJO con el orden: el timeout manda. Antes se ponia "finished"
            # por defecto, asi que un rastreo cortado por tiempo afirmaba haber
            # agotado la frontera teniendo 1.051 URLs pendientes.
            motivo = ("persistence_failed" if sin_guardar
                      else "stalled" if stalled
                      else "max_runtime_reached" if timed_out
                      else "finished")
            try:
                rc = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
                # Si no se guardo nada, eso manda sobre como acabo el rastreo
                if (rc.get(f"job:{job_id}:finish_reason") == "max_urls_reached"
                        and not sin_guardar):
                    motivo = "max_urls_reached"
                rc.delete(f"job:{job_id}:finish_reason")
            except Exception:
                pass
            job.finish_reason = motivo
            session.commit()
            if motivo in ("max_urls_reached", "max_runtime_reached", "stalled"):
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


def _comprobar_render_js(job_id: str) -> None:
    """Comprueba, plantilla a plantilla, que se pierde por no renderizar JS.

    Se ejecuta SIEMPRE al cerrar un rastreo sin render_js, porque la respuesta
    condiciona si el resultado es fiable: si una plantilla monta sus enlaces con
    JavaScript, el grafo de enlaces esta incompleto y el PageRank calculado sobre
    el es falso, sin que nada lo delate. Antes habia que sospecharlo y lanzar la
    comprobacion a mano.

    Se limita a las plantillas mayores y a una muestra por plantilla para que
    cueste un par de minutos sobre un rastreo de horas. Todo configurable por
    entorno; JS_CHECK_ENABLED=0 lo desactiva.
    """
    if os.getenv("JS_CHECK_ENABLED", "1") not in ("1", "true", "True"):
        return
    try:
        sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))
        from check_js_templates import comprobar
        from shared.database import SessionLocal
        from shared.models import Job

        plantillas = int(os.getenv("JS_CHECK_TEMPLATES", "5"))
        muestras = int(os.getenv("JS_CHECK_SAMPLES", "1"))
        logger.info(
            "Comprobando render JS del job %s (%d plantillas x %d muestra)",
            job_id, plantillas, muestras,
        )
        resultados = comprobar(
            str(job_id), muestras=muestras, plantillas_max=plantillas
        )
        if not resultados:
            return

        con_enlaces_ocultos = [r for r in resultados if r["enlaces_solo_js"] > 0]
        resumen = {
            "plantillas": resultados,
            "enlaces_ocultos": bool(con_enlaces_ocultos),
            "grafo_fiable": not con_enlaces_ocultos,
        }

        session = SessionLocal()
        try:
            job = session.query(Job).filter(Job.id == job_id).one_or_none()
            if job:
                job.js_check = resumen
                session.commit()
        finally:
            session.close()

        if con_enlaces_ocultos:
            # WARNING: invalida el PageRank del rastreo, no es un detalle.
            logger.warning(
                "Job %s: %d plantilla(s) esconden enlaces tras JavaScript (%s). "
                "El grafo de enlaces esta INCOMPLETO y el PageRank no es fiable: "
                "relanzar con render_js=true si se va a usar para analisis de "
                "enlazado.",
                job_id,
                len(con_enlaces_ocultos),
                ", ".join(r["plantilla"].split("  (")[0] for r in con_enlaces_ocultos),
            )
        else:
            logger.info(
                "Job %s: ninguna plantilla esconde enlaces tras JS; el grafo y "
                "el PageRank son fiables sin render.",
                job_id,
            )
    except Exception:
        # Nunca debe tumbar el cierre del job: es un diagnostico, no el rastreo.
        logger.exception("Comprobacion de render JS fallida para el job %s", job_id)


def _trigger_analysis(job_id: str) -> bool:
    """Import and invoke the analyzer. False si el analisis ha fallado."""
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
        logger.exception(
            "Analysis failed for job %s: el job se marca como fallido. Lo "
            "rastreado es valido; relanzar con `python -m analysis.analyzer %s`",
            job_id, job_id,
        )
        return False
    return True


def _rastreadas_y_guardadas(job_id: str) -> tuple[int, int]:
    """URLs que el spider dice haber rastreado y filas que hay en `urls`."""
    from shared.database import SessionLocal
    from shared.models import Url

    rastreadas = 0
    try:
        rc = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
        rastreadas = int(rc.get(f"job:{job_id}:crawled_count") or 0)
    except Exception:
        pass
    session = SessionLocal()
    try:
        guardadas = session.query(Url.id).filter(Url.job_id == job_id).limit(1).count()
    except Exception:
        # Si ni siquiera se puede contar, no se afirma nada: se deja pasar.
        guardadas = 1
    finally:
        session.close()
    return rastreadas, guardadas


_LINEA_DE_LOG = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
# `modulo.Clase: mensaje`, `ValueError: ...`. No vale "la ultima linea sin
# sangria": SQLAlchemy cierra con "(Background on this error at: <url>)".
_LINEA_DE_EXCEPCION = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt)\b")


def resumir_stderr(stderr: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Avisos y errores del crawler en la salida de Scrapy, agrupados por tipo.

    Solo los loggers `seo_crawler.*`: se deja fuera el ruido de Scrapy y de
    las librerias. Los errores llevan pegada la ultima linea de su traceback
    (`psycopg2.errors.UndefinedColumn: ...`), que es la que dice que ha pasado.

    Antes solo se miraban los WARNING: los fallos del pipeline van por
    `logger.exception` (nivel ERROR) y se perdian enteros, asi que un job que
    no guardaba ni una fila terminaba sin rastro en el log.
    """
    avisos: dict[str, list[str]] = {}
    errores: dict[str, list[str]] = {}
    lineas = stderr.splitlines()
    for i, ln in enumerate(lineas):
        # "[seo_crawler." con corchete: es el nombre del logger. Sin el
        # corchete tambien casaria la ruta del fichero que imprime
        # py.warnings en las deprecaciones de Scrapy.
        if "[seo_crawler." not in ln:
            continue
        if "WARNING:" in ln:
            destino, nivel = avisos, "WARNING:"
        elif "ERROR:" in ln:
            destino, nivel = errores, "ERROR:"
        elif "CRITICAL:" in ln:
            destino, nivel = errores, "CRITICAL:"
        else:
            continue
        msg = ln.split(nivel, 1)[-1].strip()
        clave = msg.split(":", 1)[0][:60]
        if destino is errores:
            # Ultima excepcion del traceback que sigue al mensaje
            causa = None
            for siguiente in lineas[i + 1:]:
                if _LINEA_DE_LOG.match(siguiente):
                    break
                if _LINEA_DE_EXCEPCION.match(siguiente):
                    causa = siguiente.strip()
            if causa:
                msg = f"{msg} -> {causa}"
        destino.setdefault(clave, []).append(msg)
    return avisos, errores


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
