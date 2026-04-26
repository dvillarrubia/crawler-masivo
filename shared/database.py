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
        StructuredData, Resource, Issue,
    )
    Base.metadata.create_all(bind=engine)
