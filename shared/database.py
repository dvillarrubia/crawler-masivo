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


def init_db():
    from shared.models import (  # noqa: F401 – force table registration
        Job, Url, HtmlMeta, Heading, Link, Hreflang,
        StructuredData, Resource, Issue, CustomExtraction, EnlaceNoRastreable,
    )
    from shared.semantic_models import (  # noqa: F401 – force semantic table registration
        GscAccount, SemanticAnalysis, SemanticPage,
        SemanticCannibalization, GscJobData,
    )
    Base.metadata.create_all(bind=engine)


def limitar_espera_de_bloqueos(segundos: int = 15) -> None:
    """Las migraciones del despliegue no esperan a un candado indefinidamente.

    Un `ALTER TABLE jobs` pide un candado exclusivo y, mientras espera, Postgres
    pone en cola DETRAS de el todas las lecturas de la tabla: con un analisis en
    marcha (que tiene `jobs` con `FOR UPDATE` toda la pasada) la API dejo de
    responder a `/api/jobs` el 2026-10-13 mientras la migracion esperaba. Con
    `lock_timeout` la migracion falla en segundos, `set -e` para el despliegue
    antes de cambiar contenedores y produccion sigue con el codigo anterior.
    Se llama solo desde los scripts de migracion, antes de abrir conexion.
    """
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _lock_timeout(dbapi_conn, _record):
        # En autocommit: dentro de la transaccion implicita de psycopg2, el
        # rollback con que el pool devuelve la conexion deshace el SET.
        autocommit = dbapi_conn.autocommit
        dbapi_conn.autocommit = True
        cur = dbapi_conn.cursor()
        cur.execute(f"SET lock_timeout = '{int(segundos)}s'")
        cur.close()
        dbapi_conn.autocommit = autocommit
