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
        # Sitemap ingestion: NULL = no sitemap data for the job
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS in_sitemap BOOLEAN"))
        # Motivo de finalizacion: distingue un crawl completo de uno truncado
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS finish_reason VARCHAR(32)"))
        # Comprobacion automatica de render JS por plantilla
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS js_check JSON"))

        # Indices de claves foraneas que faltaban. create_all() solo crea
        # indices al crear la tabla, asi que en instalaciones ya existentes hay
        # que anadirlos a mano. Sin ellos, borrar un rastreo obliga a Postgres a
        # recorrer entera la tabla hija por CADA fila de `urls`: medido en
        # produccion con 1,76 M de incidencias, borrar 16 rastreos pasaba de mas
        # de 14 minutos sin terminar a 15 segundos.
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_issues_url_id ON issues (url_id)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_semantic_cannibal_dominant "
            "ON semantic_cannibalization (url_dominant_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_semantic_cannibal_weak "
            "ON semantic_cannibalization (url_weak_id)"
        ))
        # Nombres provisionales creados a mano durante una incidencia; se
        # sustituyen por los del modelo para no dejar indices duplicados.
        conn.execute(text("DROP INDEX IF EXISTS ix_semcanib_dominant"))
        conn.execute(text("DROP INDEX IF EXISTS ix_semcanib_weak"))
        # Post-crawl cleaning: original-content backup + cleaned marker
        conn.execute(text("ALTER TABLE page_content ADD COLUMN IF NOT EXISTS content_text_original TEXT"))
        conn.execute(text("ALTER TABLE page_content ADD COLUMN IF NOT EXISTS content_markdown_original TEXT"))
        conn.execute(text("ALTER TABLE page_content ADD COLUMN IF NOT EXISTS cleaned_at TIMESTAMPTZ"))
        conn.commit()
    print("Migrations applied.")

    print("Done.")
