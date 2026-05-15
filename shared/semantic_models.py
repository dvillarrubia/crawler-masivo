"""SQLAlchemy models for semantic analysis (pgvector-backed)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from shared.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# GSC Accounts
# ---------------------------------------------------------------------------
class GscAccount(Base):
    __tablename__ = "gsc_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name = Column(String(256), nullable=False)
    credentials_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# Gemini Accounts (one per user/client; each pays their own embeddings)
# ---------------------------------------------------------------------------
class GeminiAccount(Base):
    """Stores Gemini API keys so each client pays their own embeddings.

    The api_key column is stored as-is (matching the GscAccount pattern).
    This is not production-grade for shared deployments — for that, wrap
    with Fernet/AES at-rest encryption keyed off an env-var master key.
    """

    __tablename__ = "gemini_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name = Column(String(256), nullable=False)
    api_key = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


# ---------------------------------------------------------------------------
# Semantic Analysis (one per job run)
# ---------------------------------------------------------------------------
class SemanticAnalysis(Base):
    __tablename__ = "semantic_analyses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending")  # pending/running/completed/failed
    config = Column(JSON, nullable=True)  # {alpha, beta, threshold, model_name}
    site_metrics = Column(JSON, nullable=True)  # {focus_score, semantic_radius, ...}
    centroid = Column(Vector(1024), nullable=True)
    error_message = Column(Text, nullable=True)
    total_pages = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    pages = relationship("SemanticPage", back_populates="analysis", cascade="all, delete-orphan")
    cannibalization = relationship("SemanticCannibalization", back_populates="analysis", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Semantic Page (one per analyzed URL)
# ---------------------------------------------------------------------------
class SemanticPage(Base):
    __tablename__ = "semantic_pages"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    analysis_id = Column(UUID(as_uuid=True), ForeignKey("semantic_analyses.id", ondelete="CASCADE"), nullable=False)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    embedding = Column(Vector(1024), nullable=True)
    cluster_id = Column(Integer, nullable=True)
    ring = Column(String(20), nullable=True)  # Core/Focus/Expansion/Peripheral
    semantic_role = Column(String(20), nullable=True)  # core/peripheral/outlier
    distance_to_centroid = Column(Float, nullable=True)
    weight = Column(Float, nullable=True)
    pr_norm = Column(Float, nullable=True)
    clicks_norm = Column(Float, nullable=True)
    x = Column(Float, nullable=True)  # UMAP 2D x
    y = Column(Float, nullable=True)  # UMAP 2D y

    analysis = relationship("SemanticAnalysis", back_populates="pages")

    __table_args__ = (
        Index("ix_semantic_pages_analysis", "analysis_id"),
        Index("ix_semantic_pages_url", "url_id"),
    )


# ---------------------------------------------------------------------------
# Semantic Cannibalization (pairs)
# ---------------------------------------------------------------------------
class SemanticCannibalization(Base):
    __tablename__ = "semantic_cannibalization"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    analysis_id = Column(UUID(as_uuid=True), ForeignKey("semantic_analyses.id", ondelete="CASCADE"), nullable=False)
    url_dominant_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    url_weak_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    cosine_similarity = Column(Float, nullable=False)

    analysis = relationship("SemanticAnalysis", back_populates="cannibalization")

    __table_args__ = (
        Index("ix_semantic_cannibal_analysis", "analysis_id"),
    )


# ---------------------------------------------------------------------------
# GSC Job Data (GSC metrics linked to a crawl job)
# ---------------------------------------------------------------------------
class GscJobData(Base):
    __tablename__ = "gsc_job_data"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    clicks = Column(Integer, default=0)
    impressions = Column(Integer, default=0)
    ctr = Column(Float, nullable=True)
    position = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_gsc_job_data_job", "job_id"),
        Index("ix_gsc_job_data_url", "url_id"),
    )


# ---------------------------------------------------------------------------
# GSC Query-Page Data (per-query per-page GSC metrics)
# ---------------------------------------------------------------------------
class GscQueryData(Base):
    __tablename__ = "gsc_query_data"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    query = Column(String(500), nullable=False)
    clicks = Column(Integer, default=0)
    impressions = Column(Integer, default=0)
    ctr = Column(Float, nullable=True)
    position = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_gsc_query_data_job", "job_id"),
        Index("ix_gsc_query_data_url", "url_id"),
    )
