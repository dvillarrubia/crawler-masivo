from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from shared.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=30,
    pool_pre_ping=True,
    pool_recycle=3600,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# Additive, idempotent column/index migrations (the repo does not use
# Alembic — regla de oro 2 del plan). Run by init_db() so both the API
# lifespan and the worker startup apply them: the worker must never race
# ahead of the schema (seen in prod: startup crash-recovery queried
# jobs.normalization_fingerprint before scripts/init_db.py had run).
_MIGRATIONS = [
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS pagerank FLOAT",
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS blocked_by_robots BOOLEAN",
    # T8: normalization fingerprint per job (NULL = default semantics)
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS normalization_fingerprint VARCHAR(64)",
    # T1: sitemap ingestion flags on urls (NULL = no sitemap ingested)
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS in_sitemap BOOLEAN",
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS sitemap_lastmod TIMESTAMPTZ",
    # T4: client-side redirects (meta refresh parsed + JS redirect)
    "ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS meta_refresh_url TEXT",
    "ALTER TABLE html_meta ADD COLUMN IF NOT EXISTS meta_refresh_delay INT",
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS js_redirect_url TEXT",
    # T17.5.b: DOM context per link (prerequisite for T22)
    "ALTER TABLE links ADD COLUMN IF NOT EXISTS dom_ancestor VARCHAR(16)",
    "ALTER TABLE links ADD COLUMN IF NOT EXISTS dom_container TEXT",
    # T14: SimHash para near-duplicates
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS simhash BIGINT",
    # T9/D2: conservar URLs de GSC sin match en el crawl
    "ALTER TABLE gsc_job_data ADD COLUMN IF NOT EXISTS url TEXT",
    "ALTER TABLE gsc_job_data ADD COLUMN IF NOT EXISTS url_hash VARCHAR(64)",
    "ALTER TABLE gsc_job_data ALTER COLUMN url_id DROP NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_gsc_job_data_hash ON gsc_job_data (job_id, url_hash)",
    # T10: issues firmables (NULL = issue determinista, nada cambia)
    "ALTER TABLE issues ADD COLUMN IF NOT EXISTS review_status VARCHAR(16)",
    "ALTER TABLE issues ADD COLUMN IF NOT EXISTS reviewed_by TEXT",
    "ALTER TABLE issues ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
    # T22/T23: arquitectura
    "ALTER TABLE links ADD COLUMN IF NOT EXISTS edge_class VARCHAR(16)",
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS click_depth INT",
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS in_contextual INT",
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS out_contextual INT",
    "ALTER TABLE segments ADD COLUMN IF NOT EXISTS is_business BOOLEAN DEFAULT FALSE",
    # T15: GEO crudo vs renderizado
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS raw_word_count INT",
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS raw_schema_types JSON",
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS js_content_ratio FLOAT",
    "ALTER TABLE structured_data ADD COLUMN IF NOT EXISTS visible_without_js BOOLEAN",
    # T20: contenido único / T18: PageRank semántico
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS unique_word_count INT",
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS boilerplate_ratio FLOAT",
    "ALTER TABLE urls ADD COLUMN IF NOT EXISTS pagerank_semantic FLOAT",
    # T11: índice HNSW para chunks semánticos (create_all no lo crea)
    "CREATE INDEX IF NOT EXISTS ix_semantic_chunks_embedding "
    "ON semantic_chunks USING hnsw (embedding vector_cosine_ops)",
    # T18 (cierre): anchor propuesto en las sugerencias T10
    "ALTER TABLE link_suggestions ADD COLUMN IF NOT EXISTS proposed_anchor TEXT",
    # GLiNER2: conservar queries GSC de URLs sin match (patrón T9/D2)
    "ALTER TABLE gsc_query_data ADD COLUMN IF NOT EXISTS url TEXT",
    "ALTER TABLE gsc_query_data ADD COLUMN IF NOT EXISTS url_hash VARCHAR(64)",
    "ALTER TABLE gsc_query_data ALTER COLUMN url_id DROP NOT NULL",
    # GLiNER2: índice HNSW del catálogo de entidades (768d, espacio propio)
    "CREATE INDEX IF NOT EXISTS ix_entity_catalog_embedding "
    "ON entity_catalog USING hnsw (embedding vector_cosine_ops)",
    # Hostil: 'not_crawled' (11) no cabía en VARCHAR(10) y tumbaba el
    # análisis de cualquier sitio con huérfanas de sitemap
    "ALTER TABLE urls ALTER COLUMN status_group TYPE VARCHAR(20)",
    # extraction.store_raw_html: el flag existía pero nunca persistía nada
    "ALTER TABLE page_content ADD COLUMN IF NOT EXISTS raw_html TEXT",
]


def run_migrations() -> None:
    """Apply the additive migration block. Postgres-only statements: no-op
    (with a log) on other engines like the SQLite test harness.

    lock_timeout: los ALTER idempotentes se re-ejecutan en cada arranque;
    si otra sesión (p. ej. el batch de entidades) tiene un lock largo, el
    arranque NO debe colgarse — se avisa y se sigue (la migración ya
    estará aplicada de un arranque anterior o se aplicará en el próximo).
    """
    import logging

    from sqlalchemy import text

    if not engine.dialect.name.startswith("postgres"):
        return
    log = logging.getLogger(__name__)
    with engine.connect() as conn:
        conn.execute(text("SET lock_timeout = '5000ms'"))
        for stmt in _MIGRATIONS:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as exc:  # lock ocupado o tipo ya migrado
                conn.rollback()
                log.warning("Migración pospuesta (%s…): %s", stmt[:60], exc)


def init_db():
    from shared.models import (  # noqa: F401 – force table registration
        Job, Url, HtmlMeta, Heading, Link, Hreflang,
        StructuredData, Resource, Issue, SitemapUrl,
        Segment, UrlSegment, RobotsSnapshot, WatchlistEntry,
        CrawlTrapEvent, LinkSuggestion, ArchEdge, ClientSelector,
        SectionFlow,
    )
    from shared.semantic_models import (  # noqa: F401 – force semantic table registration
        GscAccount, SemanticAnalysis, SemanticPage,
        SemanticCannibalization, GscJobData, SemanticChunk,
        QueryEmbedding, GscDaily, Ga4Account, Ga4Daily, MetricSyncConfig,
        ApiKey,
    )
    from shared.entity_models import (  # noqa: F401 – capa de entidades GLiNER2
        ClientExtractionSchema, ClientSettings, EntityCatalog,
        GlinerPageEntity, GlinerPageLabel, GlinerQueryEntity,
        GlinerQueryLabel,
    )
    if engine.dialect.name.startswith("postgres"):
        # Vector columns need the extension before create_all on a fresh DB
        from sqlalchemy import text

        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
    Base.metadata.create_all(bind=engine)
    run_migrations()
