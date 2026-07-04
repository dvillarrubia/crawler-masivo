"""
Shared pytest fixtures for the SEO crawler test suite.

Design notes (Fase 0 del documento maestro v2):

* ``DATABASE_URL`` is forced to a throwaway SQLite file *before* any
  ``shared`` import so that ``shared.database`` can build its module-level
  engine without a running Postgres (the engine is never connected here --
  every test gets its own in-memory engine from the ``db_session`` fixture).
* ``BigInteger`` primary keys are compiled as ``INTEGER`` on SQLite,
  otherwise SQLite does not autoincrement them.
* Pure checks (extractors, normalization, analyzer logic) run on SQLite.
  Checks that genuinely need Postgres semantics should be marked with
  ``@pytest.mark.postgres`` and read ``TEST_POSTGRES_URL`` from the env.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# --- import path + stub DATABASE_URL (must run before `shared` imports) -----
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "crawler"))
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:///" + os.path.join(tempfile.gettempdir(), "seo_crawler_test_stub.db"),
)

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@compiles(BigInteger, "sqlite")
def _bigint_as_integer_on_sqlite(type_, compiler, **kw):
    # SQLite only autoincrements INTEGER PRIMARY KEY (not BIGINT).
    return "INTEGER"


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _reset_url_normalization_config():
    """T8: never leak a per-test active config into other tests."""
    yield
    from shared.url_normalization import DEFAULT_CONFIG, set_active_config

    set_active_config(DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_engine():
    """A fresh in-memory SQLite engine with all crawler tables created."""
    from shared.database import Base
    import shared.models  # noqa: F401 -- register table metadata

    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """A session bound to the per-test in-memory database."""
    factory = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def make_job(db_session):
    """Factory that inserts a Job row and returns it."""
    from shared.models import Job

    def _make(name: str = "test-job", config: dict | None = None,
              seeds: list[str] | None = None):
        job = Job(
            name=name,
            seeds=seeds or ["https://toy.local/"],
            config=config or {},
            status="completed",
        )
        db_session.add(job)
        db_session.flush()
        return job

    return _make


# ---------------------------------------------------------------------------
# Golden HTML fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def golden_html() -> str:
    """Representative HTML page exercising every extractor code path."""
    return (FIXTURES_DIR / "golden.html").read_text(encoding="utf-8")


@pytest.fixture()
def golden_selector(golden_html):
    """The golden page wrapped in a parsel Selector (what extractors expect)."""
    from parsel import Selector

    return Selector(text=golden_html)
