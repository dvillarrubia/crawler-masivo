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

import json
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

# Despues de arreglar sys.path, no antes.
from shared.cola import (  # noqa: E402
    COLA_JOBS, encolar, encolar_enriquecido, siguiente, siguiente_enriquecido,
)
from shared.version import VERSION as _VERSION

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
# Con que version del codigo se rastrea. La imagen la recibe como argumento de
# construccion y el despliegue le pasa el SHA del commit; en local, sin ese
# argumento, queda "dev".
#
# Por que hace falta: comparar dos censos del mismo sitio solo significa algo
# si se sabe si entre medias cambio el sitio o cambiamos nosotros. Sin esto me
# paso dos veces el mismo dia (decision 66): el grafo de Druni bajo de 37,5 a
# 32,1 millones de aristas entre dos medidas —era que `analyze_indexability`
# habia materializado 9.771 noindex, no un cambio del sitio—, y el censo de
# CST dio 88.838 "errores" de datos estructurados que eran basura guardada por
# un extractor de junio.
# La misma constante que usa el analizador para sellar su pasada:
# `shared/version.py`, para que no puedan decir cosas distintas.
CRAWLER_VERSION = _VERSION
# Cada cuanto se mira si toca lanzar algun rastreo programado. Un minuto: los
# rastreos se programan por horas o dias, asi que llegar hasta un minuto tarde
# no significa nada, y sondear mas a menudo solo gasta consultas.
PROGRAMACION_INTERVAL_SECONDS = int(
    os.getenv("PROGRAMACION_INTERVAL_SECONDS", "60"))
# Cada cuanto se vuelve a mirar si hay jobs huerfanos. Ver `_quizas_recuperar`.
RECOVERY_INTERVAL_SECONDS = int(os.getenv("RECOVERY_INTERVAL_SECONDS", "300"))
# Analisis enriquecido (#40, decision 85): cuantas ejecuciones a la vez. Van
# en su PROPIO pool y no en el de rastreos: una ejecucion larga (60.000 URLs
# a 600 por minuto es hora y media) no puede quitarle el hueco a un rastreo,
# y un rastreo de nueve horas no puede tener esperando a quien pulso el boton.
# Casi todo su tiempo es esperar a la red, asi que un hilo apenas pesa.
MAX_CONCURRENT_ENRIQUECIDOS = int(os.getenv("MAX_CONCURRENT_ENRIQUECIDOS", "1"))
# El nombre y el orden de la cola viven en shared/cola.py: estaban
# escritos a mano en cinco sitios y el orden salia al reves.
JOBS_QUEUE = COLA_JOBS

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


# Scrapy escribe su log en un fichero por job, EN VIVO (-s LOG_FILE), en vez
# de en el stderr del subproceso. Antes el log solo existia en memoria hasta que
# el proceso acababa: con un rastreo de horas no habia forma de ver que estaba
# pasando (una pagina que cae a status NULL, un navegador que empieza a fallar)
# hasta que terminaba o se mataba. Se abre en modo append para que las
# reanudaciones automaticas del mismo job queden en el mismo fichero.
LOG_DIR = os.getenv("SCRAPY_LOG_DIR", "/tmp/scrapy-logs")


def _ruta_log(job_id: str) -> str:
    return os.path.join(LOG_DIR, f"{job_id}.log")


def _leer_log(job_id: str, ultimos_bytes: int = 2_000_000) -> str:
    """Cola del log en fichero del job ('' si no existe)."""
    try:
        with open(_ruta_log(job_id), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            tam = fh.tell()
            fh.seek(max(0, tam - ultimos_bytes))
            return fh.read().decode("utf-8", "ignore")
    except OSError:
        return ""


def _guardar_stderr(job_id: str, error: str | None, motivo: str) -> None:
    """Deja rastro de POR QUE se ha matado un Scrapy (estancamiento o tope de
    horas): sin esto no hay forma de distinguir un navegador colgado de un
    sitio que dejo de responder.

    El log vive ya en fichero (ver LOG_DIR); si por lo que sea no esta, se
    guarda el stderr. En ambos casos se registra un resumen en WARNING.
    """
    ruta = _ruta_log(job_id)
    if not error:
        error = _leer_log(job_id)
    if not error:
        logger.warning("Job %s parado por %s: Scrapy no dejo log", job_id, motivo)
        return
    if not os.path.exists(ruta):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(ruta, "w", encoding="utf-8") as fh:
                fh.write(error)
        except OSError as exc:
            ruta = f"(no se pudo escribir: {exc})"
    # Las lineas de error del spider/Playwright son las que explican el
    # cuelgue; se sacan aparte porque el final del log suele ser el cierre.
    relevantes = [
        ln for ln in error.splitlines()
        if any(k in ln for k in ("ERROR", "Traceback", "Timeout", "crash", "closed", "disconnected"))
    ]
    logger.warning(
        "Job %s parado por %s. stderr completo en %s. %d linea(s) de error; ultimas:\n%s\n--- cola del log ---\n%s",
        job_id, motivo, ruta, len(relevantes),
        "\n".join(relevantes[-15:]), error[-2500:],
    )


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
            _, error = proc.communicate()
            _guardar_stderr(job_id, error, "tope de horas")
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
            _, error = proc.communicate()
            _guardar_stderr(job_id, error, "estancamiento")
            raise EstancadoError(f"{parado:.0f} min sin avanzar")


STALL_AUTO_RESUME = int(os.getenv("STALL_AUTO_RESUME", "3"))


def _reencolar_tras_estancamiento(job_id: str, motivo: str) -> bool:
    """Devuelve True si el job se ha vuelto a encolar para que continue.

    El contador vive en Redis (job:<id>:reanudaciones) y se borra al terminar
    el job por cualquier otra via. No se reencola si el job fue cancelado
    mientras tanto ni si se agotaron los intentos.
    """
    if STALL_AUTO_RESUME <= 0:
        return False
    try:
        rc = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=15)
        intentos = int(rc.incr(f"job:{job_id}:reanudaciones"))
    except Exception:
        return False
    if intentos > STALL_AUTO_RESUME:
        logger.warning(
            "Job %s: %d reanudaciones automaticas agotadas, se cierra como stalled",
            job_id, STALL_AUTO_RESUME,
        )
        return False
    from shared.database import SessionLocal
    from shared.models import Job

    session = SessionLocal()
    try:
        job = session.query(Job).filter(Job.id == job_id).one_or_none()
        if job is None or job.status == "cancelled":
            return False
        job.status = "pending"
        job.completed_at = None
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Job %s: no se pudo reencolar tras estancamiento", job_id)
        return False
    finally:
        session.close()
    try:
        encolar(rc, job_id)
    except Exception:
        logger.exception("Job %s: no se pudo encolar en Redis", job_id)
        return False
    logger.warning(
        "Job %s ESTANCADO (%s): reanudacion automatica %d/%d desde la frontera guardada",
        job_id, motivo, intentos, STALL_AUTO_RESUME,
    )
    return True


def _reencolar_si_pedido(job_id: str) -> bool:
    """True si alguien pidio reanudar el job mientras corria (ver el guard de
    duplicados en el bucle principal): se deja en "pending" y se vuelve a
    encolar sin analizar, porque el rastreo continua."""
    try:
        rc = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=15)
        if not rc.get(f"job:{job_id}:reencolar"):
            return False
        rc.delete(f"job:{job_id}:reencolar")
    except Exception:
        return False
    from shared.database import SessionLocal
    from shared.models import Job

    session = SessionLocal()
    try:
        job = session.query(Job).filter(Job.id == job_id).one_or_none()
        if job is None:
            return False
        job.status = "pending"
        job.completed_at = None
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Job %s: no se pudo dejar en pending para reencolar", job_id)
        return False
    finally:
        session.close()
    try:
        encolar(rc, job_id)
    except Exception:
        logger.exception("Job %s: no se pudo encolar en Redis", job_id)
        return False
    logger.warning("Job %s: reanudacion pedida durante la ejecucion, reencolado", job_id)
    return True


def _ajustes_de_cookies(job_config: dict) -> list[str]:
    """`-s` del jar de cookies: apagado en settings.py, como Googlebot, salvo
    con `http.conservar_cookies` (decision 114)."""
    if (job_config.get("http") or {}).get("conservar_cookies"):
        return ["-s", "COOKIES_ENABLED=True"]
    return []


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
        job.crawler_version = CRAWLER_VERSION
        job_config = job.config if job.config else {}  # save before closing session
        session.commit()
        logger.info("Job %s marked as running", job_id)
    except Exception:
        session.rollback()
        logger.exception("Failed to update job %s status", job_id)
        return
    finally:
        session.close()

    # La marca de bloqueo es de UNA ejecucion (decision 103). Si la anterior
    # paro por muro y no llego a la finalizacion —reanudacion pedida, un
    # despliegue que recreo el contenedor, un estancamiento con la salud sin
    # calcular—, la marca seguiria viva y esta ejecucion acabaria `failed` con
    # `bloqueado_por_waf` aunque el sitio respondiera bien de principio a fin.
    try:
        redis_lib.Redis.from_url(REDIS_URL, decode_responses=True,
                                 socket_timeout=15).delete(f"job:{job_id}:bloqueo")
    except Exception:
        logger.warning("Job %s: no se pudo borrar la marca de bloqueo anterior", job_id)

    # -- Run Scrapy as subprocess (avoids Twisted reactor restart issues) --
    final_status = "completed"
    timed_out = False
    stalled = False
    sin_guardar = False
    # Motivo de un rastreo con CERO URLs ("sin_urls" / "robots_bloquea_todo").
    razon_cero = ""
    # Salud del rastreo y, si estaba detras de un muro, la causa (decision 103).
    salud: dict | None = None
    bloqueo: str | None = None
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

        # Espera de render por job. El spider la lee de PLAYWRIGHT_BANNER_WAIT_MS
        # al importarse, asi que va por entorno del subproceso. Los listados
        # que se montan por XHR/GraphQL (AEM, headless CMS) pintan sus enlaces
        # 2-3 s despues de domcontentloaded: con el tope de 2 s se guardaban
        # paginas de categoria con 0 enlaces a items y hubs con menos de los
        # que hay, y los huerfanos salian inflados sin que nada lo avisara.
        render_wait = (job_config.get("crawl_behavior") or {}).get("render_wait_ms")
        if render_wait:
            env["PLAYWRIGHT_BANNER_WAIT_MS"] = str(int(render_wait))

        # Build Scrapy command with per-job overrides
        cmd = [
            sys.executable, "-m", "scrapy", "crawl", "seo",
            "-a", f"job_id={job_id}",
        ]
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            cmd += ["-s", f"LOG_FILE={_ruta_log(job_id)}", "-s", "LOG_FILE_APPEND=True"]
            logger.info("Job %s: log de Scrapy en %s", job_id, _ruta_log(job_id))
        except OSError as exc:
            logger.warning("Sin directorio de logs (%s): Scrapy escribe en stderr", exc)

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
        cmd += _ajustes_de_cookies(job_config)
        if job_config.get("user_agent"):
            cmd += ["-s", f"USER_AGENT={job_config['user_agent']}"]
        if job_config.get("impersonate"):
            cmd += ["-s", f"IMPERSONATE={job_config['impersonate']}"]

        # Advanced crawl behavior settings
        crawl_behavior = job_config.get("crawl_behavior", {})
        if crawl_behavior.get("download_timeout", 30) != 30:
            cmd += ["-s", f"DOWNLOAD_TIMEOUT={crawl_behavior['download_timeout']}"]
            # El timeout de navegacion de Playwright debe ir alineado con el de
            # Scrapy (ver settings.py): si el job sube uno y no el otro, las
            # paginas lentas siguen perdiendose con status NULL por el que quede
            # corto. Solo se toca si el entorno no lo fijo a mano.
            if not os.getenv("PLAYWRIGHT_NAV_TIMEOUT"):
                cmd += ["-s", f"PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT={int(crawl_behavior['download_timeout']) * 1000}"]
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

        # Con LOG_FILE el stderr viene vacio: el log esta en el fichero. Se
        # usa como si fuera el stderr para que el resumen de avisos siga igual.
        if not result.stderr:
            texto_log = _leer_log(job_id)
            if texto_log:
                result = subprocess.CompletedProcess(
                    result.args, result.returncode, result.stdout, texto_log
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
            rastreadas, guardadas, recuperadas = _rastreadas_y_guardadas(job_id)
            # Reglas a medida que no compilan (decision 104): va ANTES que
            # `recuperadas`, porque en una reanudacion ya hay filas y el job
            # saldria `completed` con la frontera sin explorar.
            reglas_invalidas = _config_invalida(job_id)
            if reglas_invalidas:
                logger.error(
                    "Job %s: las reglas a medida no compilan y no se ha rastreado "
                    "nada; se marca como fallido (config_invalida): %s",
                    job_id, reglas_invalidas,
                )
                final_status = "failed"
                sin_guardar = True
                razon_cero = "config_invalida"
            elif rastreadas and not guardadas:
                logger.error(
                    "Job %s: se rastrearon %d URLs y no se guardo NINGUNA. El "
                    "pipeline fallo al escribir (ver errores arriba); el job se "
                    "marca como fallido",
                    job_id, rastreadas,
                )
                final_status = "failed"
                sin_guardar = True
            elif not recuperadas:
                # Cero rastreadas y cero guardadas: el rastreo no llego a
                # empezar. Scrapy termina con codigo 0 —no ha "fallado"— y el
                # job quedaba `completed` con 0 URLs, que es exactamente como
                # se lee un sitio vacio y limpio. El caso tipico es un
                # robots.txt con `Disallow: /` y `robots_mode=respect`: la
                # semilla se descarta con IgnoreRequest. Ahora la fila SI queda
                # (marcada "Blocked by robots.txt"), pero sin respuesta: por eso
                # la condicion mira `recuperadas` y no las filas a secas.
                # Con la comparacion entre censos (#32) esto declararia
                # desaparecido el sitio entero.
                final_status = "failed"
                sin_guardar = True
                razon_cero = _por_que_cero_urls(job_id)
                logger.error(
                    "Job %s: termino con CERO URLs (%s). Se marca como fallido: "
                    "un rastreo vacio no es un sitio limpio",
                    job_id, razon_cero,
                )

    except EstancadoError as exc:
        # Un estancamiento casi siempre es el navegador (Playwright) que se ha
        # quedado colgado, no el sitio: el spider retoma desde la frontera que
        # dejo en la BD, asi que lo barato es volver a encolar el job y seguir.
        # Se reintenta STALL_AUTO_RESUME veces (def. 3); solo despues se cierra
        # como "stalled" y se analiza lo obtenido, sin fingir que termino bien.
        #
        # SALVO que este bloqueado: reanudar un rastreo detras de un muro es
        # pedir otras tres veces lo mismo y que el WAF lo apunte (decision 103).
        salud = _salud_del_job(job_id, job_config)
        bloqueo = (salud or {}).get("causa")
        if not bloqueo and _reencolar_tras_estancamiento(job_id, str(exc)):
            return
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
    if _reencolar_si_pedido(job_id):
        return

    # Un rastreo detras de un muro no es un sitio limpio (decisiones 28 y 32):
    # si las respuestas no validas llegan al umbral, `failed` con la causa y
    # sin analizar —el analisis sacaria incidencias de un censo que no se ha
    # visto— ni comparar con el anterior.
    if salud is None:
        salud = _salud_del_job(job_id, job_config)
    if not bloqueo:
        bloqueo = (salud or {}).get("causa")
    if bloqueo and not razon_cero and not sin_guardar:
        logger.error(
            "Job %s BLOQUEADO (%s): %s. Se marca fallido; lo rastreado se "
            "conserva", job_id, bloqueo, (salud or {}).get("por_clase"))
        final_status = "failed"

    cancelled = False
    session = SessionLocal()
    try:
        job = session.query(Job).filter(Job.id == job_id).one_or_none()
        if job and job.status == "cancelled":
            cancelled = True
        elif job and final_status == "completed":
            job.status = "analyzing"
            # El analisis tiene que saber ya si el censo esta truncado: lo que
            # decide sobre ausencias (que un destino no este en el censo) no
            # puede afirmarse en un censo cortado por el tope (decision 67), y
            # antes `finish_reason` solo se escribia DESPUES de analizar —en el
            # analisis automatico de cada rastreo valia NULL—. Se escribe el
            # mismo motivo que al cerrar; la marca de Redis se consume alli.
            job.finish_reason = _motivo_de_fin(
                job_id, razon_cero=razon_cero, sin_guardar=sin_guardar,
                stalled=stalled, timed_out=timed_out, consumir=False,
                bloqueo=bloqueo)
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
            motivo = _motivo_de_fin(
                job_id, razon_cero=razon_cero, sin_guardar=sin_guardar,
                stalled=stalled, timed_out=timed_out, consumir=True,
                bloqueo=bloqueo)
            job.finish_reason = motivo
            if salud is not None:
                job.health = salud
            session.commit()
            try:
                redis_lib.Redis.from_url(REDIS_URL, decode_responses=True).delete(
                    f"job:{job_id}:reanudaciones"
                )
            except Exception:
                pass
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

    # La comparacion con el censo anterior va DESPUES de escribir el estado
    # final, y no junto a la comprobacion de render. En ese punto la fila del
    # job todavia dice `analyzing`, y para la comparacion cualquier estado que
    # no sea `completed` es un censo truncado (decision 67): el censo de ahora
    # se daria por incompleto SIEMPRE, de modo que nunca se podria afirmar que
    # una pagina ha desaparecido — justo el aviso que mas cuesta reconstruir
    # luego.
    if final_status == "completed":
        _comparar_con_el_censo_anterior(job_id)
        # Despues de comparar, y el orden importa: la comparacion puede
        # RE-ANALIZAR la referencia (decision 93), y la referencia es justo un
        # censo que este rastreo acaba de dejar superado. Archivandola antes,
        # el re-analisis no tendria enlaces. Contra el cierre de OTRO rastreo
        # del mismo sitio a la vez protegen el candado del job, que comparten
        # el analisis y el archivado, y que la comparacion no re-analiza un
        # censo ya archivado.
        _archivar_enlaces_superados(job_id)


def _archivar_enlaces_superados(job_id: str) -> None:
    """Pasa a Parquet los enlaces de los censos que ya han quedado superados.

    Es lo que mantiene `links` acotada a los censos vivos en vez de crecer con
    cada rastreo semanal (decision 95). Best-effort, con el `try` cubriendo la
    funcion ENTERA (decision 74): un fallo deja los enlaces donde estaban, que
    es un estado valido, y no puede tocar el job que acaba de terminar.
    `ARCHIVAR_ENLACES=0` lo apaga.
    """
    if os.getenv("ARCHIVAR_ENLACES", "1") not in ("1", "true", "True"):
        return
    session = None
    try:
        from shared import archivo_enlaces
        from shared.database import SessionLocal

        session = SessionLocal()
        for job in archivo_enlaces.candidatos(session):
            try:
                r = archivo_enlaces.archivar(session, job.id)
                logger.info("Job %s: enlaces del censo superado %s archivados "
                            "(%d filas, %d bytes)", job_id, job.id, r["filas"], r["bytes"])
            except archivo_enlaces.SeEstaAnalizando:
                # Lo esta analizando otro proceso (p. ej. el re-analisis de la
                # referencia de otro rastreo, decision 93): sigue en `links` y
                # el cierre del siguiente rastreo lo volvera a intentar.
                session.rollback()
                logger.info("Job %s: %s se esta analizando; se archivara en otra "
                            "pasada", job_id, job.id)
            except Exception:
                session.rollback()
                logger.exception("Job %s: no se pudieron archivar los enlaces de %s",
                                 job_id, job.id)
    except Exception:
        logger.exception("Job %s: fallo al buscar enlaces que archivar", job_id)
    finally:
        if session is not None:
            session.close()


def _comparar_con_el_censo_anterior(job_id: str) -> None:
    """Compara el rastreo recien cerrado con el anterior del mismo sitio.

    Es lo que convierte la comparacion de #32 en una ALERTA: nadie va a entrar
    a elegir dos censos en un desplegable para enterarse de que media seccion
    se ha ido del indice. En Lopesan paso exactamente eso —2.367 paginas
    canonicalizadas a un servidor de pruebas— y se entrego como un aviso
    `info` entre 315.119 incidencias.

    Best-effort: si revienta, el job NO se marca fallido. Lo rastreado y lo
    analizado valen igual, y la comparacion se puede pedir despues por el
    endpoint. Lo contrario seria que un censo correcto quedase en `failed` por
    no poder compararse con otro.
    """
    # `SessionLocal` y `Job` se importan DENTRO, como en el resto del fichero:
    # no estan en el ambito del modulo y la primera version de esto reventaba
    # con `NameError: name 'SessionLocal' is not defined` en produccion, en el
    # primer rastreo que la ejecuto.
    from shared.avisos import avisar_de_alertas
    from shared.comparacion import comparar_con_el_anterior
    from shared.database import SessionLocal
    from shared.models import Job

    # El montaje tambien va dentro del try: estaba fuera, asi que el
    # "best-effort" no cubria la linea que fallo.
    session = None
    try:
        session = SessionLocal()
        job = session.query(Job).filter(Job.id == job_id).one_or_none()
        if job is None:
            return
        # El analizador se pasa desde aqui y no lo importa `shared/`: la API
        # carga ese modulo y su imagen no lleva `analysis/` (decision 93). Si
        # faltara, se compara igual, sin poner al dia la referencia.
        try:
            from analysis.analyzer import run_analysis
        except ImportError:
            logger.exception("Job %s: sin el analizador no se re-analiza la "
                             "referencia", job_id)
            run_analysis = None

        resultado = comparar_con_el_anterior(session, job, reanalizar=run_analysis)
        if resultado is None:
            logger.info("Job %s: no hay censo anterior del mismo sitio", job_id)
            return
        job.comparacion = resultado
        session.commit()
        criticas = [a for a in resultado.get("alertas", [])
                    if a.get("severidad") == "critical"]
        if criticas:
            logger.warning(
                "Job %s: %d ALERTA(S) CRITICA(S) respecto al censo anterior (%s): %s",
                job_id, len(criticas), resultado.get("nombre_anterior"),
                "; ".join(f"{a['regla']} {a['paginas']}/{a['de']} ({a['pct']}%)"
                          for a in criticas),
            )
            # Y fuera, si hay a donde. Una alerta que espera en la interfaz
            # solo sirve si alguien entra: el fallo de Lopesan lo pillamos
            # porque re-rastreamos ese dia por otro motivo.
            #
            # SOLO las criticas. Un aviso que llega cada semana con cosas que
            # no hay que mirar se deja de leer, y entonces no avisa de nada.
            avisar_de_alertas(job_id, job.name, criticas)
        else:
            logger.info("Job %s: comparado con %s, %d alerta(s)", job_id,
                        resultado.get("comparado_con"),
                        len(resultado.get("alertas", [])))
    except Exception:
        if session is not None:
            session.rollback()
        logger.exception("Job %s: fallo al comparar con el censo anterior", job_id)
    finally:
        if session is not None:
            session.close()


# Un enlace escondido no invalida un grafo: lo que lo invalida es que
# una plantilla monte con JavaScript una parte que PESA. Con el umbral
# en "mayor que cero", el enlace que inyecta cualquier widget —un
# gestor de consentimiento, un chat, un "volver arriba"— marcaba el
# grafo como no fiable y ponia el PageRank bajo sospecha en sitios donde
# estaba bien. Se pide una fraccion del grafo de la plantilla (10%) o el
# caso que de verdad importa: una plantilla con CERO enlaces en crudo que
# al renderizar aparecen (el listado montado por XHR de la decision 18).
def _esconde_enlaces(r: dict) -> bool:
    solo_js = r.get("enlaces_solo_js") or 0
    if not solo_js:
        return False
    crudo = r.get("enlaces_crudo")
    if crudo is None:
        return solo_js > 0  # resultados de antes de medir el crudo
    if crudo == 0:
        return True
    return solo_js >= max(3, crudo * 0.1)


def _resumen_js_check(resultados: list[dict]) -> dict:
    """Veredicto de `jobs.js_check` a partir de las plantillas comprobadas."""
    con_enlaces_ocultos = [r for r in resultados if _esconde_enlaces(r)]
    # Plantillas cuyo render no fue tal (WAF, desafio, error): su "0 enlaces
    # solo-JS" no cuenta. Si TODAS estan asi, el veredicto queda en None
    # ("no se pudo comprobar"), nunca en "fiable".
    sospechosas = [r for r in resultados if r.get("render_sospechoso")]
    # Y las que se quedaron sin una sola muestra valida (un 403, el muro de un
    # WAF, un fallo de descarga): su "0 enlaces solo-JS" es que no se miro.
    # Sin esto, un sitio que bloquea al comprobador en todas las plantillas
    # salia con el grafo "fiable" (decision 62).
    sin_muestra = [r for r in resultados if not r.get("muestras", 1)]
    concluyentes = [
        r for r in resultados if not r.get("render_sospechoso") and r.get("muestras", 1)
    ]
    resumen = {
        "plantillas": resultados,
        "enlaces_ocultos": bool(con_enlaces_ocultos),
        "grafo_fiable": (not con_enlaces_ocultos) if concluyentes else None,
        "render_bloqueado": len(sospechosas),
        "sin_comprobar": len(sin_muestra),
        "concluyentes": len(concluyentes),
    }
    return resumen


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

        resumen = _resumen_js_check(resultados)
        con_enlaces_ocultos = [r for r in resultados if _esconde_enlaces(r)]
        sospechosas = [r for r in resultados if r.get("render_sospechoso")]
        sin_muestra = [r for r in resultados if not r.get("muestras", 1)]
        concluyentes = resumen["concluyentes"] > 0

        session = SessionLocal()
        try:
            job = session.query(Job).filter(Job.id == job_id).one_or_none()
            if job:
                job.js_check = resumen
                session.commit()
        finally:
            session.close()

        if sin_muestra:
            logger.warning(
                "Job %s: %d de %d plantilla(s) sin ninguna muestra valida (codigo "
                "distinto de 200, muro de WAF o fallo de descarga/render): no "
                "cuentan para el veredicto del grafo (%s).",
                job_id, len(sin_muestra), len(resultados),
                ", ".join(r["plantilla"].split("  (")[0] for r in sin_muestra),
            )
        if sospechosas:
            logger.warning(
                "Job %s: en %d de %d plantilla(s) Chromium recibio una pagina sin "
                "contenido (WAF/desafio/error): la comprobacion de render JS NO es "
                "concluyente para ellas. Medir a mano antes de afirmar nada.",
                job_id, len(sospechosas), len(resultados),
            )
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
        elif concluyentes:
            logger.info(
                "Job %s: ninguna plantilla esconde enlaces tras JS; el grafo y "
                "el PageRank son fiables sin render.",
                job_id,
            )
    except Exception:
        # Nunca debe tumbar el cierre del job: es un diagnostico, no el rastreo.
        logger.exception("Comprobacion de render JS fallida para el job %s", job_id)


def _motivo_de_fin(job_id: str, *, razon_cero: str, sin_guardar: bool, stalled: bool,
                   timed_out: bool, consumir: bool, bloqueo: str | None = None) -> str:
    """Motivo real de finalizacion para `finish_reason`.

    El spider marca en Redis cuando corta por el tope de URLs; sin esto, un
    crawl truncado quedaba indistinguible de uno completo y el PageRank se
    presentaba como bueno estando calculado sobre una parte del sitio.
    OJO con el orden: el timeout manda. Antes se ponia "finished" por defecto,
    asi que un rastreo cortado por tiempo afirmaba haber agotado la frontera
    teniendo 1.051 URLs pendientes. `consumir` borra la marca de Redis: solo al
    cerrar, porque antes de analizar se calcula lo mismo una primera vez.
    """
    motivo = (razon_cero if razon_cero
              else "persistence_failed" if sin_guardar
              else bloqueo if bloqueo
              else "stalled" if stalled
              else "max_runtime_reached" if timed_out
              else "finished")
    try:
        rc = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
        # Si no se guardo nada, eso manda sobre como acabo el rastreo; y un
        # bloqueo (decision 103) tambien: el tope no es lo que lo corto.
        if (rc.get(f"job:{job_id}:finish_reason") == "max_urls_reached"
                and not sin_guardar and not bloqueo):
            motivo = "max_urls_reached"
        if consumir:
            rc.delete(f"job:{job_id}:finish_reason")
            rc.delete(f"job:{job_id}:bloqueo")
    except Exception:
        pass
    return motivo


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


def _por_que_cero_urls(job_id: str) -> str:
    """Motivo para `finish_reason` cuando un rastreo acaba sin una sola URL.

    Distinguir "robots.txt lo bloquea todo" de "no se sabe" importa: lo
    primero es un hallazgo que se cuenta al cliente en una frase, y lo segundo
    hay que mirarlo.
    """
    try:
        rc = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=15)
        if rc.get(f"job:{job_id}:robots_bloquea_semillas"):
            return "robots_bloquea_todo"
    except Exception:
        pass
    return "sin_urls"


def _config_invalida(job_id: str) -> str | None:
    """El motivo si el spider dejo la marca de reglas a medida que no compilan
    (decision 104), o None. La marca se consume: el motivo queda en el log del
    worker y el estado en `finish_reason`."""
    from shared.extraccion_a_medida import CLAVE_CONFIG_INVALIDA

    try:
        rc = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=15)
        clave = CLAVE_CONFIG_INVALIDA.format(job_id=job_id)
        motivo = rc.get(clave)
        if motivo:
            rc.delete(clave)
        return motivo or None
    except Exception:
        return None


def _salud_del_job(job_id: str, job_config: dict | None) -> dict | None:
    """Respuestas del job por clase y, si el rastreo esta bloqueado, la causa.

    Dos fuentes, por este orden: la marca que deja el spider cuando para por
    la ventana (`job:<id>:bloqueo`) y, si no la hay, el TOTAL del job en la
    base de datos. El total es lo que cubre el rastreo corto que acaba entero
    detras de un muro sin llenar la ventana: el de una URL de la decision 11,
    o el de progym, cuya home responde 200 desde la cache de Cloudflare y todo
    lo demas 403 con el desafio (decision 103). Y como la BD sobrevive a las
    reanudaciones, tambien es lo que decide si un job estancado se reencola.

    Best-effort entero (decision 74): si algo falla devuelve None y el job se
    juzga como antes.
    """
    try:
        from sqlalchemy import func

        from shared.database import SessionLocal
        from shared.models import Url
        from shared.respuestas import (
            UMBRAL_POR_DEFECTO, causa_de_bloqueo, resumen_de_salud,
        )

        cb = (job_config or {}).get("crawl_behavior") or {}
        umbral = float(cb.get("salud_umbral") or UMBRAL_POR_DEFECTO)
        session = SessionLocal()
        try:
            recuentos = dict(
                session.query(Url.response_class, func.count(Url.id))
                # Solo las internas, como la ventana del spider: un muro de un
                # host externo (follow_external) no dice nada del sitio.
                .filter(Url.job_id == job_id, Url.response_class.isnot(None),
                        Url.is_internal.is_(True))
                .group_by(Url.response_class)
                .all()
            )
        finally:
            session.close()
        marca = None
        try:
            rc = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=15)
            crudo = rc.get(f"job:{job_id}:bloqueo")
            marca = json.loads(crudo) if crudo else None
        except Exception:
            marca = None
        causa = (marca or {}).get("causa") or causa_de_bloqueo(recuentos, umbral)
        salud = resumen_de_salud(recuentos, umbral, causa)
        if marca:
            # Lo que vio el spider al parar: la ventana, no el total.
            salud["ventana"] = marca
        return salud
    except Exception:
        logger.exception("Job %s: no se pudo calcular la salud del rastreo", job_id)
        return None


def _rastreadas_y_guardadas(job_id: str) -> tuple[int, int, int]:
    """Rastreadas segun el spider, filas en `urls`, y filas con respuesta.

    `recuperadas` cuenta solo las filas con `status_code`: las que robots.txt
    bloquea se guardan (es un hallazgo, no un hueco) pero no se piden, asi que
    un sitio con `Disallow: /` deja filas y ninguna respuesta. Sin esa
    distincion, guardar la semilla bloqueada hacia pasar por "rastreo correcto"
    justo el caso que #37 queria no confundir con un sitio limpio.
    """
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
        recuperadas = (
            session.query(Url.id)
            .filter(Url.job_id == job_id, Url.status_code.isnot(None))
            .limit(1)
            .count()
        )
    except Exception:
        # Si ni siquiera se puede contar, no se afirma nada: se deja pasar.
        guardadas = recuperadas = 1
    finally:
        session.close()
    return rastreadas, guardadas, recuperadas


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
# Rastreos programados
# ---------------------------------------------------------------------------
def _quizas_programar(rconn, ultima_vez: float, ahora: float) -> float:
    """Pasa el planificador si toca, y devuelve cuando fue la ultima pasada.

    Va en el bucle y no en un hilo de APScheduler a proposito: este proceso
    lanza Scrapy como SUBPROCESO y maneja senales, y meter ahi un hilo que
    dispara cosas por su cuenta es buscarse una carrera entre el fork y el
    disparo. De APScheduler se usa solo el motor de tiempos (`CronTrigger`),
    que es la parte que no quiero escribir a mano: el dia 31 en meses de 30 y
    el domingo en que las 2:30 no existe o existe dos veces.
    """
    if ahora - ultima_vez < PROGRAMACION_INTERVAL_SECONDS:
        return ultima_vez
    try:
        _lanzar_programados(rconn)
    except Exception:
        # Que el planificador reviente no puede tumbar el worker: dejaria de
        # atender la cola, que es lo que de verdad hace.
        logger.exception("Fallo la pasada del planificador")
    return ahora


def _lanzar_programados(rconn) -> None:
    """Encola los rastreos cuya hora ha llegado."""
    from datetime import datetime, timezone

    from shared.cola import encolar
    from shared.database import SessionLocal
    from shared.models import Job, Programacion
    from shared.programaciones import (
        hay_rastreo_vivo, job_desde_programacion, proxima_desde, toca_lanzar,
    )

    session = None
    try:
        session = SessionLocal()
        ahora = datetime.now(timezone.utc)
        pendientes = (
            session.query(Programacion)
            .filter(Programacion.activa.is_(True),
                    Programacion.proxima_ejecucion.isnot(None),
                    Programacion.proxima_ejecucion <= ahora)
            .all()
        )
        for prog in pendientes:
            proxima = prog.proxima_ejecucion
            if proxima is not None and proxima.tzinfo is None:
                # Postgres devuelve timestamptz con zona, pero SQLite no: sin
                # esto la comparacion revienta en los tests y, peor, en
                # cualquier motor que no guarde la zona.
                proxima = proxima.replace(tzinfo=timezone.utc)

            lanzar, motivo = toca_lanzar(proxima, ahora)
            if lanzar and hay_rastreo_vivo(session, prog):
                lanzar, motivo = False, (
                    "el rastreo anterior de esta programacion sigue sin "
                    "terminar; no se encola otro encima")

            if lanzar:
                datos = job_desde_programacion(prog)
                # El cliente y el perfil se copian tal cual de la programacion
                # (#36). El worker NO crea clientes a partir del texto: si la
                # programacion es anterior a la migracion, el job sale solo
                # con su `client_id` y la siguiente pasada de `init_db.py` lo
                # cuelga de su cliente. Crear filas desde aqui haria que un
                # fallo de esa tabla impidiera lanzar el rastreo.
                job = Job(name=datos["name"], client_id=datos["client_id"],
                          cliente_id=datos.get("cliente_id"),
                          profile_id=datos.get("profile_id"),
                          seeds=datos["seeds"], config=datos["config"],
                          status="pending", programacion_id=prog.id)
                session.add(job)
                session.flush()
                if job.profile_id is not None:
                    from shared.models import CrawlProfile

                    session.query(CrawlProfile).filter(
                        CrawlProfile.id == job.profile_id
                    ).update({CrawlProfile.last_job_id: job.id},
                             synchronize_session=False)
                encolar(rconn, job.id)
                prog.ultima_ejecucion = ahora
                prog.ultimo_job_id = job.id
                logger.info(
                    "Programacion %s (%s): encolado %s — %s",
                    prog.id, prog.nombre, job.id, motivo)
            else:
                logger.info("Programacion %s (%s): no se lanza — %s",
                            prog.id, prog.nombre, motivo)

            prog.ultimo_motivo = motivo
            # La proxima se recalcula SIEMPRE, se haya lanzado o no. Si no, una
            # programacion que se salta un disparo se queda con la hora pasada
            # y vuelve a intentarlo en cada vuelta del bucle, cada minuto.
            prog.proxima_ejecucion = proxima_desde(
                prog.cron, prog.zona_horaria, ahora)
        if pendientes:
            session.commit()
    except Exception:
        if session is not None:
            session.rollback()
        raise
    finally:
        if session is not None:
            session.close()


# ---------------------------------------------------------------------------
# Stale job recovery
# ---------------------------------------------------------------------------
def _quizas_recuperar(rconn, ultima_vez: float, ahora: float) -> float:
    """Pasa el vigilante si toca, y devuelve cuando fue la ultima pasada.

    El vigilante corria **una sola vez, al arrancar el worker**, y por eso no
    servia para el caso que mas lo necesita: si el contenedor se reinicia
    DENTRO de los primeros `STALE_JOB_MINUTES` de un rastreo —que es justo
    cuando lo pilla un despliegue—, el job recien empezado no llega al umbral,
    no es candidato, y como nadie vuelve a mirar se queda en `running` sin
    nadie detras **para siempre**.

    Medido: un rastreo lanzado a las 22:22:01 y un despliegue que recreo el
    contenedor a las 22:22:39 dejaron el job colgado 7 h 40 min con 39 URLs,
    sin proceso de Scrapy y sin que el vigilante lo tocara.
    """
    if ahora - ultima_vez < RECOVERY_INTERVAL_SECONDS:
        return ultima_vez
    _recover_stale_jobs(rconn)
    _recuperar_enriquecidos(rconn)
    return ahora


# ---------------------------------------------------------------------------
# Analisis enriquecido (#40, decision 85)
# ---------------------------------------------------------------------------
def _ejecutar_enriquecido(run_id: str) -> None:
    """Unico sitio del worker que llega a la capa de decisiones, y solo con
    un id sacado de `enrichment:pending`, que solo llena
    `POST /api/jobs/{id}/enrichment` (con token). Ni `_run_job` ni el analisis
    ni las programaciones pasan por aqui. El import va DENTRO a proposito: el
    worker no carga la capa hasta que alguien ha pulsado el boton."""
    try:
        from shared.database import SessionLocal
        from shared.enriquecido import ejecutar

        rconn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
        final = ejecutar(run_id, sesion_factory=SessionLocal, redis=rconn)
        if final is None:
            logger.info("Enriquecido %s: no estaba pendiente; se omite", run_id)
        else:
            logger.info("Enriquecido %s: %s", run_id, final)
    except Exception:
        # Best-effort con el try cubriendo la funcion ENTERA (decision 74):
        # un fallo aqui no puede tumbar el bucle de rastreos.
        logger.exception("Enriquecido %s: fallo al ejecutar", run_id)


def _quizas_enriquecer(rconn, executor, activos: dict) -> None:
    """Saca una ejecucion de su cola si hay hueco. Sin bloquear: el bucle ya
    bloquea en la de rastreos."""
    for rid in [r for r, f in activos.items() if f.done()]:
        activos.pop(rid)
    if len(activos) >= MAX_CONCURRENT_ENRIQUECIDOS:
        return
    try:
        run_id = siguiente_enriquecido(rconn)
    except Exception as exc:
        logger.warning("Redis: no se pudo leer la cola de enriquecido (%s)", exc)
        return
    run_id = (run_id or "").strip()
    if not run_id or run_id in activos:
        return
    logger.info("Dequeued enriquecido %s", run_id)
    activos[run_id] = executor.submit(_ejecutar_enriquecido, run_id)


def _recuperar_enriquecidos(rconn) -> None:
    """Las ejecuciones que un despliegue dejo en `en_curso` sin nadie detras
    vuelven a la cola. Reanudar es idempotente y no repaga: se saltan las URLs
    con fila de esta ejecucion y lo demas sale de la cache."""
    try:
        from shared.database import SessionLocal
        from shared.enriquecido import minutos_de_latido, recuperar

        s = SessionLocal()
        try:
            ids = recuperar(s, minutos_de_latido())
        finally:
            s.close()
        for rid in ids:
            logger.warning("Recuperando enriquecido %s huerfano: se reencola", rid)
            encolar_enriquecido(rconn, rid)
    except Exception:
        logger.exception("No se pudieron recuperar las ejecuciones de enriquecido")


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
            encolar(rconn, job.id)

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
    _recuperar_enriquecidos(rconn)
    ultima_recuperacion = time.time()
    # A cero para que la primera vuelta ya mire las programaciones: si el
    # worker arranca justo despues de un despliegue, el disparo perdido de ese
    # rato se recupera en el acto y no al minuto siguiente.
    ultima_programacion = 0.0

    executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS)
    active_futures: dict[str, Future] = {}
    executor_enr = ThreadPoolExecutor(max_workers=max(1, MAX_CONCURRENT_ENRIQUECIDOS))
    enriquecidos: dict[str, Future] = {}

    try:
        while not _shutdown_event.is_set():
            ultima_recuperacion = _quizas_recuperar(
                rconn, ultima_recuperacion, time.time()
            )
            ultima_programacion = _quizas_programar(
                rconn, ultima_programacion, time.time()
            )

            # Antes de mirar el cupo de rastreos: con los huecos de rastreo
            # llenos el bucle hace `continue` y la cola de enriquecido no se
            # leeria nunca.
            _quizas_enriquecer(rconn, executor_enr, enriquecidos)

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
                job_id = siguiente(rconn, timeout=BRPOP_TIMEOUT)
            except (redis_lib.exceptions.TimeoutError, redis_lib.exceptions.ConnectionError) as exc:
                logger.warning("Redis poll error (%s); retrying", exc)
                time.sleep(1)
                continue
            if job_id is None:
                continue

            job_id = job_id.strip()
            if not job_id:
                continue

            if job_id in active_futures:
                # Llega un resume (o una reanudacion automatica) mientras el
                # Scrapy anterior todavia se esta cerrando. Tirar la entrada
                # perdia la reanudacion: el proceso viejo acababa, el cierre
                # ponia "completed" sobre el "pending" del resume y el job se
                # quedaba parado con la cola llena. Se apunta y se reencola
                # cuando el proceso actual termine.
                logger.warning(
                    "Job %s ya esta en marcha: se reencolara cuando termine el proceso actual",
                    job_id,
                )
                try:
                    rconn.set(f"job:{job_id}:reencolar", "1", ex=6 * 3600)
                except Exception:
                    logger.exception("Job %s: no se pudo anotar el reencolado", job_id)
                continue

            logger.info("Dequeued job %s", job_id)
            future = executor.submit(_run_job, job_id)
            active_futures[job_id] = future

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received, shutting down")
    finally:
        logger.info("Waiting for %d active job(s) to finish ...", len(active_futures))
        executor.shutdown(wait=True)
        executor_enr.shutdown(wait=True)
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
