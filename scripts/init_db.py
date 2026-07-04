"""Initialize database tables. Run once before first use."""
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

    print("Creating database tables...")
    init_db()

    # Migrations for existing installations
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS pagerank FLOAT"))
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS blocked_by_robots BOOLEAN"))
        # T8: normalization fingerprint per job (NULL = default semantics)
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS normalization_fingerprint VARCHAR(64)"))
        # T1: sitemap ingestion flags on urls (NULL = no sitemap ingested)
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS in_sitemap BOOLEAN"))
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS sitemap_lastmod TIMESTAMPTZ"))
        conn.commit()
    print("Migrations applied.")

    print("Done.")
