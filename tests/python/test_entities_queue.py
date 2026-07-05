"""Tests del disparo automático del pipeline de entidades (cola Redis).

Cubre las puertas de `shared.entities_queue.maybe_enqueue` (solo se
encola con rastreo completado + client_id + schema de extracción), la
deduplicación con acumulación de motivos, y la selección de pasos del
worker (`select_steps`): un import de GSC no debe re-pasar el modelo por
las páginas y un cambio de catálogo no debe cargar el modelo siquiera.
"""

from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------

class FakeRedis:
    """Lo mínimo que usa entities_queue: sets, listas y strings."""

    def __init__(self):
        self.kv = {}
        self.sets = {}
        self.lists = {}

    def sadd(self, key, *vals):
        s = self.sets.setdefault(key, set())
        added = sum(1 for v in vals if v not in s)
        s.update(vals)
        return added

    def srem(self, key, *vals):
        s = self.sets.get(key, set())
        for v in vals:
            s.discard(v)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def delete(self, key):
        self.sets.pop(key, None)
        self.kv.pop(key, None)
        self.lists.pop(key, None)

    def expire(self, key, ttl):
        return True

    def lpush(self, key, val):
        self.lists.setdefault(key, []).insert(0, val)

    def set(self, key, val, ex=None):
        self.kv[key] = val

    def get(self, key):
        return self.kv.get(key)


@pytest.fixture()
def schema_table(db_engine):
    pytest.importorskip("pgvector")
    from pgvector.sqlalchemy import Vector
    from sqlalchemy.ext.compiler import compiles

    @compiles(Vector, "sqlite")
    def _vector_sqlite(type_, compiler, **kw):
        return "TEXT"

    from shared.entity_models import ClientExtractionSchema

    ClientExtractionSchema.__table__.create(db_engine, checkfirst=True)
    return True


def _job_with_client(db_session, make_job, client_id="acme", status="completed"):
    job = make_job()
    job.client_id = client_id
    job.status = status
    db_session.flush()
    return job


def _add_schema(db_session, client_id="acme"):
    from shared.entity_models import ClientExtractionSchema

    db_session.add(ClientExtractionSchema(client_id=client_id, yaml_text="x: 1"))
    db_session.flush()


# ---------------------------------------------------------------------------
# maybe_enqueue: puertas
# ---------------------------------------------------------------------------

def test_no_enqueue_without_schema(db_session, make_job, schema_table):
    from shared.entities_queue import QUEUE_KEY, maybe_enqueue

    job = _job_with_client(db_session, make_job)
    r = FakeRedis()
    out = maybe_enqueue(r, db_session, job.id, reason="crawl")
    assert out == {"enqueued": False, "why": "sin_schema_extraccion"}
    assert QUEUE_KEY not in r.lists


def test_no_enqueue_without_client_or_completion(db_session, make_job, schema_table):
    from shared.entities_queue import maybe_enqueue

    r = FakeRedis()
    job = make_job()  # sin client_id
    assert maybe_enqueue(r, db_session, job.id, reason="crawl")["why"] == "sin_client_id"

    running = _job_with_client(db_session, make_job, status="running")
    _add_schema(db_session)
    assert maybe_enqueue(r, db_session, running.id, reason="crawl")["why"] == "job_running"

    assert maybe_enqueue(
        r, db_session, "00000000-0000-0000-0000-000000000000", reason="crawl",
    )["why"] == "job_no_existe"
    assert maybe_enqueue(r, db_session, "no-uuid", reason="crawl")["why"] == "job_id_invalido"


def test_enqueue_dedups_and_accumulates_reasons(db_session, make_job, schema_table):
    from shared.entities_queue import (
        QUEUE_KEY, get_status, maybe_enqueue, reasons_key,
    )

    job = _job_with_client(db_session, make_job)
    _add_schema(db_session)
    r = FakeRedis()

    first = maybe_enqueue(r, db_session, job.id, reason="crawl")
    assert first["enqueued"] is True
    assert r.lists[QUEUE_KEY] == [str(job.id)]
    assert get_status(r, str(job.id))["state"] == "queued"

    # Segundo disparo con el job aún en cola: no duplica, acumula motivo.
    second = maybe_enqueue(r, db_session, job.id, reason="gsc")
    assert second == {"enqueued": False, "why": "ya_en_cola"}
    assert r.lists[QUEUE_KEY] == [str(job.id)]
    assert r.smembers(reasons_key(str(job.id))) == {"crawl", "gsc"}


def test_enqueue_safe_never_raises(db_session, make_job, schema_table):
    from shared.entities_queue import enqueue_safe

    class BrokenRedis:
        def sadd(self, *a):
            raise ConnectionError("redis caído")

    job = _job_with_client(db_session, make_job)
    _add_schema(db_session)
    out = enqueue_safe(BrokenRedis(), db_session, job.id, reason="crawl")
    assert out["enqueued"] is False
    assert out["why"] == "error"


def test_status_roundtrip():
    from shared.entities_queue import get_status, set_status

    r = FakeRedis()
    set_status(r, "j1", "running", steps=["pages"])
    st = get_status(r, "j1")
    assert st["state"] == "running" and st["steps"] == ["pages"]
    assert isinstance(json.dumps(st), str)
    assert get_status(r, "otro") is None


# ---------------------------------------------------------------------------
# select_steps: qué corre según qué dato entró
# ---------------------------------------------------------------------------

def test_select_steps_full_on_crawl_or_schema_or_fresh_job():
    from analysis.entities.worker import select_steps

    full = ["pages", "queries", "catalog", "resolve", "report"]
    assert select_steps({"crawl"}, has_page_entities=False) == full
    assert select_steps({"schema"}, has_page_entities=True) == full
    # Job nunca extraído: da igual el motivo, hay que extraer páginas.
    assert select_steps({"gsc"}, has_page_entities=False) == full


def test_select_steps_gsc_skips_pages():
    from analysis.entities.worker import select_steps

    assert select_steps({"gsc"}, has_page_entities=True) == [
        "queries", "catalog", "resolve", "report",
    ]


def test_select_steps_catalog_skips_model():
    from analysis.entities.worker import select_steps

    steps = select_steps({"catalog"}, has_page_entities=True)
    assert steps == ["catalog", "resolve", "report"]
    assert "pages" not in steps and "queries" not in steps
