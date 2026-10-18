"""Initialize database tables. Run once before first use."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.database import init_db, engine, limitar_espera_de_bloqueos
from sqlalchemy import text

# Force registration of semantic models so their tables are created
import shared.semantic_models  # noqa: F401


def main() -> None:
    limitar_espera_de_bloqueos()
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
        # Clics desde la home, no saltos desde las semillas (decision 90).
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS click_depth INTEGER"))
        # Titular que no se pinta: se guarda marcado y los checks de h1 lo ignoran.
        conn.execute(text("ALTER TABLE headings ADD COLUMN IF NOT EXISTS oculto BOOLEAN"))
        # Hash del contenido normalizado: el de los bytes no detecta duplicados
        # en ningun CMS moderno (un token CSRF cambia el hash).
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)"))
        # HTML que Google trunca o ignora (decision 102).
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS html_bytes_servidos INTEGER"))
        conn.execute(text("ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS head_cortado_por VARCHAR(64)"))
        conn.execute(text("ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS fuera_del_head VARCHAR(128)"))
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
        # De donde sale la anotacion: `html` o `cabecera` (Link). NULL = rastreo
        # anterior, del HTML (decision 116).
        conn.execute(text("ALTER TABLE hreflang ADD COLUMN IF NOT EXISTS origen VARCHAR(16)"))
        # Canonicals del head con DESTINO distinto: con dos, Google los ignora
        # todos. NULL = rastreo anterior; se mira `canonical_count` (decision 115).
        conn.execute(text("ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS canonical_distintos INTEGER"))
        # Lo que el HTML declara de si mismo (decision 108). Nullable y sin
        # DEFAULT: solo catalogo.
        for columna, tipo in (
            ("viewport", "TEXT"), ("viewport_count", "INTEGER"),
            ("html_lang", "VARCHAR(35)"), ("content_language", "VARCHAR(64)"),
            ("canonical_crudo", "TEXT"), ("og_image_forma", "VARCHAR(24)"),
            ("twitter_image", "TEXT"), ("redireccion_js", "TEXT"),
            ("charset_meta", "VARCHAR(40)"), ("charset_bom", "VARCHAR(40)"),
            ("cuerpo_solo_ascii", "BOOLEAN"), ("texto_mal_codificado", "BOOLEAN"),
        ):
            conn.execute(text(f"ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS {columna} {tipo}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_urls_content_hash ON urls (content_hash)"))
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS closest_similarity FLOAT"))
        # La respuesta era la pagina o un muro delante, y la salud del job
        # (decision 103). Sin DEFAULT: solo catalogo, instantaneo.
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS response_class VARCHAR(32)"))
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS health JSON"))
        # Segmentos de negocio (decision 125). Sin DEFAULT: solo catalogo. Sin
        # indice a proposito: todo filtro por segmento va con `job_id`, que ya
        # tiene los suyos, y crear uno sobre `urls` bloquearia las escrituras
        # de un rastreo en marcha mientras se construye.
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS segmento TEXT"))
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS segmentacion JSON"))
        # Si Googlebot puede pedir la URL segun el robots.txt guardado
        # (decision 113). Sin DEFAULT: NULL = no evaluado, solo catalogo.
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS bloqueada_googlebot BOOLEAN"))
        # Motivo de finalizacion: distingue un crawl completo de uno truncado
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS finish_reason VARCHAR(32)"))
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS crawler_version VARCHAR(64)"))
        # Comprobacion automatica de render JS por plantilla
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS js_check JSON"))
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS comparacion JSON"))
        # Sondas y resumen de soft 404 (decision 105)
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS soft404 JSON"))
        # TTFB y compresion servida (decision 109) y la sonda TLS (110).
        # Nullables y sin DEFAULT: solo catalogo.
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS ttfb_ms FLOAT"))
        conn.execute(text("ALTER TABLE urls ADD COLUMN IF NOT EXISTS content_encoding VARCHAR(32)"))
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS tls JSON"))
        # Resumen de las sondas de respuesta (decisiones 123 y 124)
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS sondas_resumen JSON"))
        # Ficheros de sitemap leidos (decision 126)
        conn.execute(text("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS sitemaps JSON"))
        # La clave que distingue dos hallazgos del mismo tipo en la misma
        # URL: el codigo de hreflang, el grupo de un duplicado, la imagen sin
        # alt. Sin ella no se puede decir "esto se arreglo" entre censos (#32).
        conn.execute(text("ALTER TABLE issues ADD COLUMN IF NOT EXISTS group_key TEXT"))
        conn.execute(text(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS programacion_id UUID"))
        conn.execute(text(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS analisis_version VARCHAR(64)"))
        # Enlaces de los censos superados, archivados en Parquet (decision 95)
        conn.execute(text(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS enlaces_archivados JSON"))
        # Capa de decisiones (#7, decision 84). La tabla `decisions` la crea
        # create_all (con sus indices, por ser nueva); aqui va lo que
        # create_all no anade a tablas que ya existen.
        conn.execute(text(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS decisiones_resumen JSON"))
        conn.execute(text(
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS decisiones JSON"))
        # Reglas de alerta por cliente (decision 135). La tabla
        # `resumenes_semanales` la crea create_all.
        conn.execute(text(
            "ALTER TABLE clients ADD COLUMN IF NOT EXISTS reglas_alerta JSON"))
        conn.execute(text(
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS alertas_cliente JSON"))
        # Enlaces identicos de una pagina agrupados en una fila (decision 94).
        # Con DEFAULT constante, Postgres 11+ no reescribe la tabla: es solo
        # catalogo, instantaneo aunque `links` tenga 180 M filas.
        conn.execute(text(
            "ALTER TABLE links ADD COLUMN IF NOT EXISTS repeticiones INTEGER "
            "NOT NULL DEFAULT 1"))

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
    migrar_enriquecido()
    migrar_custom_extraction()
    migrar_enlaces_no_rastreables()
    migrar_robots_txt()
    migrar_capturas_alternativas()
    migrar_respuestas_sondeadas()
    migrar_sitemap_entries()
    migrar_profile_runs()

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


def migrar_enriquecido() -> None:
    """Analisis enriquecido (#40, decision 85). Idempotente.

    `enrichment_runs` la crea create_all (con sus indices, por ser nueva). Lo
    que create_all no hace en una instalacion existente es la FK de
    `decisions.run_id`, que #7 dejo sin ella porque la tabla aun no existia.
    SET NULL: borrar una ejecucion no tiene por que llevarse lo decidido, que
    sigue sirviendo de cache.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_enrichment_runs_job_id ON enrichment_runs (job_id)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_enrichment_runs_estado ON enrichment_runs (estado)"))
        existe = conn.execute(
            text("SELECT 1 FROM pg_constraint WHERE conname = :n"),
            {"n": "decisions_run_id_fkey"}).first()
        if not existe:
            # Una fila con un run_id que no existe (no deberia haberla: hasta
            # ahora nadie creaba ejecuciones) haria fallar la FK entera.
            conn.execute(text(
                "UPDATE decisions SET run_id = NULL WHERE run_id IS NOT NULL AND run_id "
                "NOT IN (SELECT id FROM enrichment_runs)"))
            conn.execute(text(
                "ALTER TABLE decisions ADD CONSTRAINT decisions_run_id_fkey FOREIGN KEY "
                "(run_id) REFERENCES enrichment_runs(id) ON DELETE SET NULL"))
    print("Enriquecido: tabla enrichment_runs y FK de decisions.run_id al dia.")


def migrar_custom_extraction() -> None:
    """Extraccion a medida (#34, decision 104). Idempotente.

    La tabla la crea create_all (por ser nueva, con sus indices). Aqui solo se
    asegura que los indices existen en una instalacion donde la tabla se creo
    antes que alguno de ellos. NO hay ningun ALTER sobre `jobs` ni `urls`: las
    definiciones de las reglas viajan en `jobs.config`. Va bajo el
    `lock_timeout` de `main()` (`limitar_espera_de_bloqueos`).
    """
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_cext_url_nombre "
            "ON custom_extraction (url_id, nombre)"))
        for nombre, cols in (("ix_cext_job_nombre_estado", "job_id, nombre, estado"),
                             ("ix_cext_job_nombre_num", "job_id, nombre, valor_num"),
                             ("ix_cext_job_nombre_clave", "job_id, nombre, valor_clave")):
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {nombre} ON custom_extraction ({cols})"))
    print("Extraccion a medida: tabla custom_extraction e indices al dia.")


def migrar_enlaces_no_rastreables() -> None:
    """Enlaces que Google no puede seguir (decision 106). Idempotente.

    Tabla NUEVA, que crea create_all con sus indices: ningun ALTER sobre
    `links` (decenas de GB) ni sobre `urls`. Aqui solo se aseguran los indices
    por si la tabla se creo antes que ellos. Bajo el `lock_timeout` de `main()`.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_enr_job ON enlaces_no_rastreables (job_id)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_enr_from ON enlaces_no_rastreables (from_url_id)"))
    print("Enlaces no rastreables: tabla enlaces_no_rastreables e indices al dia.")


def migrar_robots_txt() -> None:
    """robots.txt de cada host del censo (decisiones 112 y 113). Idempotente.

    Tabla NUEVA, que crea create_all con sus indices; unas pocas filas por
    censo. Aqui solo se aseguran los indices por si la tabla se creo antes que
    ellos. Bajo el `lock_timeout` de `main()`.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_robots_txt_job_url ON robots_txt (job_id, url)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_robots_txt_job_host ON robots_txt (job_id, host)"))
    print("robots.txt: tabla robots_txt e indices al dia.")


def migrar_capturas_alternativas() -> None:
    """Capturas de una URL con otro perfil (decision 119). Idempotente.

    Tabla NUEVA: la crea create_all con sus indices, sin tocar `urls` ni
    `links`. Aqui solo se aseguran los indices por si la tabla se creo antes
    que ellos. Bajo el `lock_timeout` de `main()`.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_capalt_job_perfil "
            "ON capturas_alternativas (job_id, perfil)"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_capalt_url ON capturas_alternativas (url_id)"))
    print("Capturas alternativas: tabla capturas_alternativas e indices al dia.")


def migrar_respuestas_sondeadas() -> None:
    """Enlaces externos y recursos embebidos sondeados (decisiones 123 y 124).

    Tabla NUEVA, que crea create_all con su indice unico (job_id, url_hash):
    ningun ALTER sobre `links`, `urls` ni `resources`. Aqui solo se asegura el
    indice por si la tabla se creo antes que el. Bajo el `lock_timeout` de
    `main()`.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_rsond_job_hash "
            "ON respuestas_sondeadas (job_id, url_hash)"))
    print("Respuestas sondeadas: tabla respuestas_sondeadas e indice al dia.")


def migrar_sitemap_entries() -> None:
    """Entradas de cada fichero de sitemap (decision 126).

    Tabla NUEVA, que crea create_all con su indice (job_id, url_hash): ningun
    ALTER sobre `urls`. Aqui solo se asegura el indice por si la tabla se creo
    antes que el. Bajo el `lock_timeout` de `main()`.
    """
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_sment_job_hash "
            "ON sitemap_entries (job_id, url_hash)"))
    print("Sitemaps: tabla sitemap_entries e indice al dia.")



def migrar_profile_runs() -> None:
    """La evolucion de cada job (decision 130).

    Tabla NUEVA, que crea create_all con su restriccion unica sobre `job_id`:
    ningun ALTER sobre `jobs`, `urls` ni `issues`, asi que no espera el candado
    de un analisis en marcha (que tiene `jobs` con FOR UPDATE). Aqui solo se
    asegura que existe, bajo el `lock_timeout` de `main()`. Las filas de los
    censos ya hechos las pone `scripts/rellenar_evolucion.py`, que no va en el
    despliegue: se lanza a mano y de uno en uno.
    """
    from shared.models import ProfileRun

    ProfileRun.__table__.create(bind=engine, checkfirst=True)
    print("Evolucion: tabla profile_runs al dia.")


if __name__ == "__main__":
    main()
