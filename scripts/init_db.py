"""Initialize database tables. Run once before first use.

The additive migration block lives in ``shared.database.run_migrations``
and is also applied automatically by ``init_db()`` — which both the API
lifespan and the crawler worker call at startup — so a fresh deploy can
never race a worker ahead of the schema. This script remains as the
explicit entrypoint for CI/deploy pipelines.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.database import init_db, engine
from sqlalchemy import text

# Force registration of semantic models so their tables are created
import shared.semantic_models  # noqa: F401

if __name__ == "__main__":
    # Enable pgvector extension before creating tables
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    print("pgvector extension enabled.")

    print("Creating database tables + migrations...")
    init_db()

    print("Done.")
