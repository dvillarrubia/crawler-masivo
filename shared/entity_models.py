"""SQLAlchemy models for the GLiNER2 entity layer (POC entidad-query).

Diseño conforme a `v2 experimental/INVESTIGACION.md` y al contrato
Seontology v1.0 (WebKnograph):

- Espacio vectorial del catálogo: **768d** (`gemini-embedding-001@768`,
  MRL + L2), SEPARADO de los 1024d del resto del repo. Toda tabla con
  vectores lleva `model_version`; jamás se comparan espacios distintos.
- `entity_id` sigue la convención del contrato: `wikidata_qid` cuando
  exista, si no `local:{slug}` con `is_linked = false`.
- Las menciones usan los nombres de propiedad del contrato
  (`confidence`, `source`) para que la migración futura a Neo4j
  (`MENTIONS`) sea un volcado, no una reescritura.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from shared.database import Base

EMBEDDING_MODEL_VERSION = "gemini-embedding-001@768"
EMBEDDING_DIM = 768


def _utcnow():
    return datetime.now(timezone.utc)


class ClientExtractionSchema(Base):
    """El `schema.yaml` por cliente (convención de la casa: config de
    cliente en DB, editable desde la consola)."""

    __tablename__ = "client_extraction_schemas"

    client_id = Column(String(128), primary_key=True)
    yaml_text = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class EntityCatalog(Base):
    """Catálogo resoluble por cliente. PK compuesta (client_id, entity_id)."""

    __tablename__ = "entity_catalog"

    client_id = Column(String(128), primary_key=True)
    entity_id = Column(String(256), primary_key=True)  # wikidata_qid | local:{slug}
    name = Column(Text, nullable=False)
    entity_type = Column(String(64), nullable=False)
    source = Column(String(16), nullable=False, default="generado")  # feed|crawl|generado
    is_linked = Column(Boolean, nullable=False, default=False)  # true = QID real
    embedding = Column(Vector(EMBEDDING_DIM), nullable=True)
    model_version = Column(String(64), nullable=False, default=EMBEDDING_MODEL_VERSION)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


class GlinerPageEntity(Base):
    """Una mención (span agregado) de entidad en una URL del job."""

    __tablename__ = "gliner_page_entities"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    url_hash = Column(String(64), nullable=False)
    entity_text = Column(Text, nullable=False)          # texto normalizado del span
    entity_type = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False, default="resoluble")  # resoluble|senal
    source_field = Column(String(16), nullable=False, default="body")  # title|h1|body
    frequency = Column(Integer, nullable=False, default=1)  # menciones agregadas
    span_start = Column(Integer, nullable=True)  # primer span visto
    span_end = Column(Integer, nullable=True)
    confidence = Column(Float, nullable=True)    # máxima del grupo
    entity_id = Column(String(256), nullable=True)      # NULL = sin resolver
    resolved_by = Column(String(16), nullable=True)     # cosine|llm|NULL
    resolution_score = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_gliner_page_entities_job", "job_id"),
        Index("ix_gliner_page_entities_job_url", "job_id", "url_id"),
        Index("ix_gliner_page_entities_job_entity", "job_id", "entity_id"),
    )


class GlinerPageLabel(Base):
    """Clasificación multi-label por URL: funnel y tipo de página."""

    __tablename__ = "gliner_page_labels"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    label_type = Column(String(32), nullable=False)   # funnel | tipo_pagina
    label = Column(String(64), nullable=False)        # TOFU | ficha | ...
    confidence = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_gliner_page_labels_job", "job_id"),
        Index("ix_gliner_page_labels_job_url_type", "job_id", "url_id", "label_type"),
    )


class GlinerQueryLabel(Base):
    """Clasificación por query única del job (p. ej. banda funnel)."""

    __tablename__ = "gliner_query_labels"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    query = Column(String(500), nullable=False)
    label_type = Column(String(32), nullable=False)   # funnel
    label = Column(String(64), nullable=False)
    confidence = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_gliner_query_labels_job", "job_id"),
        Index("ix_gliner_query_labels_job_query", "job_id", "query", "label_type"),
    )


class GlinerQueryEntity(Base):
    """Entidades extraídas de las queries GSC del job (query única)."""

    __tablename__ = "gliner_query_entities"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    query = Column(String(500), nullable=False)
    entity_text = Column(Text, nullable=False)
    entity_type = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False, default="resoluble")
    confidence = Column(Float, nullable=True)
    entity_id = Column(String(256), nullable=True)
    resolved_by = Column(String(16), nullable=True)
    resolution_score = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_gliner_query_entities_job", "job_id"),
        Index("ix_gliner_query_entities_job_query", "job_id", "query"),
    )
