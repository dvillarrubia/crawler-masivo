"""Initialize database tables. Run once before first use."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.database import init_db, engine
from sqlalchemy import text

# Force registration of semantic models so their tables are created
import shared.semantic_models  # noqa: F401


def main() -> None:
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
        # Casi duplicados (MinHash): NULL = no medido, 0 = medido y sin ninguna
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS near_duplicate_count INTEGER"))
        # Palabras del contenido principal: con las del body entero el
        # megamenu tapaba las paginas escasas (997 vs 10.433 en un censo).
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS content_word_count INTEGER"))
        # Titular que no se pinta: se guarda marcado y los checks de h1 lo ignoran.
        conn.execute(text("ALTER TABLE headings ADD COLUMN IF NOT EXISTS oculto BOOLEAN"))
        # Hash del contenido normalizado: el de los bytes no detecta duplicados
        # en ningun CMS moderno (un token CSRF cambia el hash).
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)"))
        # Varios canonicals, o uno en el body: Google los ignora.
        conn.execute(text("ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS canonical_count INTEGER"))
        conn.execute(text("ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS canonical_in_body BOOLEAN"))
        # Robots dirigido a Google: lo necesita el analyzer al recalcular (#26).
        conn.execute(text("ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS meta_robots_googlebot VARCHAR(256)"))
        # Varios <title> / meta description (vacios incluidos) y hreflang en el
        # body: Google ignora el del body y con dos titulos no se sabe cual usa (#26).
        conn.execute(text("ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS title_count INTEGER"))
        conn.execute(text("ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS meta_description_count INTEGER"))
        conn.execute(text("ALTER TABLE hreflang ADD COLUMN IF NOT EXISTS en_body BOOLEAN"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_urls_content_hash ON urls (content_hash)"))
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS closest_similarity FLOAT"))
        # Motivo de finalizacion: distingue un crawl completo de uno truncado
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS finish_reason VARCHAR(32)"))
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS crawler_version VARCHAR(64)"))
        # Comprobacion automatica de render JS por plantilla
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS js_check JSON"))
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS comparacion JSON"))
        conn.execute(text(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS programacion_id UUID"))
        conn.execute(text(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS analisis_version VARCHAR(64)"))
        # Capa de decisiones (#7, decision 84). La tabla `decisions` la crea
        # create_all (con sus indices, por ser nueva); aqui va lo que
        # create_all no anade a tablas que ya existen.
        conn.execute(text(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS decisiones_resumen JSON"))
        conn.execute(text(
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS decisiones JSON"))

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

    migrar_clientes()

    print("Done.")


def migrar_clientes() -> None:
    """Clientes como entidad (#36, F1). Idempotente.

    Las tablas `clients` y `crawl_profiles` las crea `init_db()` (create_all).
    Aqui va lo que create_all NO hace en una instalacion existente: las
    columnas nuevas de `jobs` y `programaciones`, sus indices y sus claves
    foraneas, y convertir los `client_id` de texto en filas de `clients`.

    `jobs.client_id` (texto) NO se convierte en FK: se anade `cliente_id` al
    lado. Asi los jobs viejos se siguen abriendo igual, los scripts que mandan
    texto siguen funcionando, y si la conversion sale mal se repite sin haber
    perdido el dato original.
    """
    from shared.clientes import migrar_client_id_texto
    from shared.database import SessionLocal

    ddl = [
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS cliente_id UUID",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS profile_id UUID",
        "ALTER TABLE programaciones ADD COLUMN IF NOT EXISTS cliente_id UUID",
        "ALTER TABLE programaciones ADD COLUMN IF NOT EXISTS profile_id UUID",
        # Mismos nombres que generaria create_all en una instalacion nueva, para
        # no acabar con el indice dos veces.
        "CREATE INDEX IF NOT EXISTS ix_jobs_cliente_id ON jobs (cliente_id)",
        "CREATE INDEX IF NOT EXISTS ix_jobs_profile_id ON jobs (profile_id)",
        "CREATE INDEX IF NOT EXISTS ix_programaciones_cliente_id ON programaciones (cliente_id)",
        "CREATE INDEX IF NOT EXISTS ix_programaciones_profile_id ON programaciones (profile_id)",
    ]
    # Claves foraneas: Postgres no tiene ADD CONSTRAINT IF NOT EXISTS, asi que
    # se mira el catalogo. Los nombres son los que pone create_all por
    # convencion, para que una instalacion nueva y una migrada acaben iguales.
    fks = [
        ("jobs", "jobs_cliente_id_fkey", "cliente_id", "clients", "SET NULL"),
        ("jobs", "jobs_profile_id_fkey", "profile_id", "crawl_profiles", "SET NULL"),
        ("programaciones", "programaciones_cliente_id_fkey", "cliente_id", "clients", "SET NULL"),
        ("programaciones", "programaciones_profile_id_fkey", "profile_id",
         "crawl_profiles", "SET NULL"),
        ("crawl_profiles", "fk_profiles_last_job", "last_job_id", "jobs", "SET NULL"),
    ]
    with engine.begin() as conn:
        for sentencia in ddl:
            conn.execute(text(sentencia))
        for tabla, nombre, columna, destino, al_borrar in fks:
            existe = conn.execute(
                text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": nombre}
            ).first()
            if not existe:
                conn.execute(text(
                    f"ALTER TABLE {tabla} ADD CONSTRAINT {nombre} FOREIGN KEY "
                    f"({columna}) REFERENCES {destino}(id) ON DELETE {al_borrar}"))

    session = SessionLocal()
    try:
        resultado = migrar_client_id_texto(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    print(
        f"Clientes: {resultado['creados']} creados; colgados de su cliente "
        f"{resultado['jobs']} jobs y {resultado['programaciones']} programaciones.")
    # Se imprimen para revisarlos a mano: no se fusionan erratas en silencio.
    for slug, textos in sorted(resultado["colisiones"].items()):
        print(f"  ATENCION: textos distintos colapsan en el cliente '{slug}': {textos}")


if __name__ == "__main__":
    main()
