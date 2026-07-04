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
        # T4: client-side redirects (meta refresh parsed + JS redirect)
        conn.execute(text("ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS meta_refresh_url TEXT"))
        conn.execute(text("ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS meta_refresh_delay INT"))
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS js_redirect_url TEXT"))
        # T17.5.b: DOM context per link (prerequisite for T22)
        conn.execute(text("ALTER TABLE links ADD COLUMN IF NOT EXISTS dom_ancestor VARCHAR(16)"))
        conn.execute(text("ALTER TABLE links ADD COLUMN IF NOT EXISTS dom_container TEXT"))
        # T14: SimHash para near-duplicates
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS simhash BIGINT"))
        # T9/D2: conservar URLs de GSC sin match en el crawl
        conn.execute(text("ALTER TABLE gsc_job_data ADD COLUMN IF NOT EXISTS url TEXT"))
        conn.execute(text("ALTER TABLE gsc_job_data ADD COLUMN IF NOT EXISTS url_hash VARCHAR(64)"))
        conn.execute(text("ALTER TABLE gsc_job_data ALTER COLUMN url_id DROP NOT NULL"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_gsc_job_data_hash "
            "ON gsc_job_data (job_id, url_hash)"
        ))
        # T10: issues firmables (NULL = issue determinista, nada cambia)
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS review_status VARCHAR(16)"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS reviewed_by TEXT"))
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ"))
        # T15: GEO crudo vs renderizado
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS raw_word_count INT"))
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS raw_schema_types JSON"))
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS js_content_ratio FLOAT"))
        conn.execute(text("ALTER TABLE structured_data ADD COLUMN IF NOT EXISTS visible_without_js BOOLEAN"))
        # T11: índice HNSW para chunks semánticos (create_all no lo crea)
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_semantic_chunks_embedding "
            "ON semantic_chunks USING hnsw (embedding vector_cosine_ops)"
        ))
        conn.commit()
    print("Migrations applied.")

    print("Done.")
