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

logging.basicConfig(
    level=logging.INFO,
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

        # Apply job-level Scrapy settings overrides
        if "concurrent_requests" in job_config:
            cmd += ["-s", f"CONCURRENT_REQUESTS={job_config['concurrent_requests']}"]
        if "concurrent_requests_per_domain" in job_config:
            cmd += ["-s", f"CONCURRENT_REQUESTS_PER_DOMAIN={job_config['concurrent_requests_per_domain']}"]
        if job_config.get("respect_robots") is False:
            cmd += ["-s", "ROBOTSTXT_OBEY=False"]
        if job_config.get("user_agent"):
            cmd += ["-s", f"USER_AGENT={job_config['user_agent']}"]

        result = subprocess.run(
            cmd,
            cwd=_CRAWLER_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=3600 * 6,  # 6 hour max per job
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
        logger.error("Crawl timed out for job %s", job_id)
        final_status = "failed"
    except Exception:
        logger.exception("Crawl failed for job %s", job_id)
        final_status = "failed"

    # -- Post-crawl: update status --
    session = SessionLocal()
    try:
        job = session.query(Job).filter(Job.id == job_id).one_or_none()
        if job:
            if job.status == "cancelled":
                final_status = "cancelled"
            job.status = final_status
            job.completed_at = datetime.now(timezone.utc)
            session.commit()
            logger.info("Job %s finished with status: %s", job_id, final_status)
    except Exception:
        session.rollback()
        logger.exception("Failed to finalise job %s", job_id)
    finally:
        session.close()

    # -- Trigger analysis (best-effort) --
    if final_status == "completed":
        _trigger_analysis(job_id)


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

    session = SessionLocal()
    try:
        stale = (
            session.query(Job)
            .filter(Job.status == "running", Job.started_at < cutoff)
            .all()
        )
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

    rconn = redis_lib.Redis.from_url(REDIS_URL, decode_responses=True)
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

            # Poll for a new job
            result = rconn.brpop(JOBS_QUEUE, timeout=BRPOP_TIMEOUT)
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
