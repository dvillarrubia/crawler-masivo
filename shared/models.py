import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, BigInteger, Boolean, Float, Text, DateTime,
    ForeignKey, Index, JSON, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from shared.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
class Job(Base):
    __tablename__ = "jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name = Column(String(512), nullable=False)
    client_id = Column(String(128), nullable=True, index=True)
    owner_id = Column(String(128), nullable=True)

    # pending | running | completed | failed | cancelled
    status = Column(String(20), nullable=False, default="pending", index=True)

    seeds = Column(JSON, nullable=False)  # list of seed URLs
    config = Column(JSON, nullable=False, default=dict)

    total_urls_discovered = Column(Integer, default=0)
    total_urls_crawled = Column(Integer, default=0)
    total_urls_failed = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    urls = relationship("Url", back_populates="job", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
class Url(Base):
    __tablename__ = "urls"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    url = Column(Text, nullable=False)
    url_hash = Column(String(64), nullable=False)  # sha256 for dedup
    host = Column(String(512))
    path = Column(Text)
    scheme = Column(String(10))

    is_internal = Column(Boolean, default=True)
    crawl_depth = Column(Integer, nullable=True)

    content_type = Column(String(256))
    content_length = Column(BigInteger, nullable=True)
    status_code = Column(Integer, nullable=True)
    status_group = Column(String(10))  # 2xx, 3xx, 4xx, 5xx, timeout, dns_error
    response_time_ms = Column(Float, nullable=True)

    is_html = Column(Boolean, default=False)
    resource_type = Column(String(20))  # html, image, css, js, pdf, other

    redirect_url = Column(Text, nullable=True)

    indexable = Column(Boolean, nullable=True)
    body_hash = Column(String(64), nullable=True)  # for duplicate content detection

    first_seen_at = Column(DateTime(timezone=True), default=_utcnow)
    last_crawled_at = Column(DateTime(timezone=True), default=_utcnow)

    # --- Screaming Frog extended fields ---
    url_length = Column(Integer)                             # character count of URL
    folder_depth = Column(Integer)                           # number of path segments
    word_count = Column(Integer, nullable=True)              # words in body text
    text_ratio = Column(Float, nullable=True)                # text/HTML ratio percentage
    redirect_type = Column(Integer, nullable=True)           # HTTP redirect code (301, 302, 307, 308)
    status_text = Column(String(64), nullable=True)          # "OK", "Not Found", "Moved Permanently"
    last_modified = Column(String(128), nullable=True)       # Last-Modified header
    http_version = Column(String(16), nullable=True)         # "HTTP/1.1", "HTTP/2"
    transfer_size = Column(BigInteger, nullable=True)        # compressed transfer size
    indexability_status = Column(String(64), nullable=True)  # reason: "Canonicalised", "Noindex", etc.
    inlinks_count = Column(Integer, default=0)               # total inlinks to this URL
    outlinks_count = Column(Integer, default=0)              # total outlinks from this URL
    external_outlinks_count = Column(Integer, default=0)     # external outlinks count
    unique_inlinks_count = Column(Integer, default=0)        # unique source pages linking in

    job = relationship("Job", back_populates="urls")
    html_meta = relationship("HtmlMeta", back_populates="url_rel", uselist=False, cascade="all, delete-orphan")
    headings = relationship("Heading", back_populates="url_rel", cascade="all, delete-orphan")
    hreflangs = relationship("Hreflang", back_populates="url_rel", cascade="all, delete-orphan")
    structured_data = relationship("StructuredData", back_populates="url_rel", cascade="all, delete-orphan")
    resources = relationship("Resource", back_populates="url_rel", cascade="all, delete-orphan")
    issues = relationship("Issue", back_populates="url_rel", cascade="all, delete-orphan")
    security = relationship("SecurityHeaders", back_populates="url_rel", uselist=False, cascade="all, delete-orphan")
    page_content = relationship("PageContent", back_populates="url_rel", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("job_id", "url_hash", name="uq_job_url"),
        Index("ix_urls_job_status", "job_id", "status_code"),
        Index("ix_urls_job_host", "job_id", "host"),
    )


# ---------------------------------------------------------------------------
# HTML Metadata
# ---------------------------------------------------------------------------
class HtmlMeta(Base):
    __tablename__ = "html_meta"

    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), primary_key=True)

    title = Column(Text, nullable=True)
    title_len = Column(Integer, nullable=True)
    meta_description = Column(Text, nullable=True)
    meta_description_len = Column(Integer, nullable=True)
    meta_keywords = Column(Text, nullable=True)
    meta_robots = Column(String(256), nullable=True)
    x_robots_tag = Column(String(256), nullable=True)

    canonical_href = Column(Text, nullable=True)
    canonical_header = Column(Text, nullable=True)

    og_title = Column(Text, nullable=True)
    og_description = Column(Text, nullable=True)
    og_image = Column(Text, nullable=True)
    og_url = Column(Text, nullable=True)
    og_type = Column(String(64), nullable=True)

    twitter_card = Column(String(64), nullable=True)
    twitter_title = Column(Text, nullable=True)
    twitter_description = Column(Text, nullable=True)

    rel_next = Column(Text, nullable=True)
    rel_prev = Column(Text, nullable=True)

    # --- Screaming Frog extended fields ---
    title_pixel_width = Column(Integer, nullable=True)             # SERP pixel width at Arial 20px
    meta_description_pixel_width = Column(Integer, nullable=True)  # SERP pixel width at Arial 14px
    meta_refresh = Column(Text, nullable=True)                     # meta refresh content if present
    has_meta_outside_head = Column(Boolean, nullable=True)         # meta tags found outside head

    url_rel = relationship("Url", back_populates="html_meta")


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------
class Heading(Base):
    __tablename__ = "headings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)
    tag = Column(String(4), nullable=False)  # h1, h2, h3 …
    position = Column(Integer, nullable=False)
    text = Column(Text, nullable=True)

    url_rel = relationship("Url", back_populates="headings")


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------
class Link(Base):
    __tablename__ = "links"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    from_url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    to_url = Column(Text, nullable=False)
    to_url_hash = Column(String(64), nullable=False)
    anchor_text = Column(Text, nullable=True)
    rel = Column(String(128), nullable=True)
    is_internal = Column(Boolean, default=True)
    link_position = Column(String(20), nullable=True)  # nav, footer, content

    # --- Screaming Frog extended fields ---
    follow = Column(Boolean, default=True)                # whether link passes equity (derived from rel)
    target = Column(String(20), nullable=True)            # _blank, _self, etc.
    alt_text = Column(Text, nullable=True)                # for image links
    link_type = Column(String(20), default="hyperlink")   # hyperlink, image, redirect, canonical

    from_url_rel = relationship("Url", foreign_keys=[from_url_id])

    __table_args__ = (
        Index("ix_links_from", "from_url_id"),
        Index("ix_links_to_hash", "job_id", "to_url_hash"),
    )


# ---------------------------------------------------------------------------
# Hreflang
# ---------------------------------------------------------------------------
class Hreflang(Base):
    __tablename__ = "hreflang"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)
    lang = Column(String(20), nullable=False)
    href = Column(Text, nullable=False)
    return_tag_ok = Column(Boolean, nullable=True)
    lang_valid = Column(Boolean, nullable=True)

    url_rel = relationship("Url", back_populates="hreflangs")


# ---------------------------------------------------------------------------
# Structured Data
# ---------------------------------------------------------------------------
class StructuredData(Base):
    __tablename__ = "structured_data"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)
    raw = Column(JSON, nullable=True)
    format = Column(String(20), nullable=False)  # jsonld, microdata, rdfa
    schema_type = Column(String(128), nullable=True)
    validation_status = Column(String(10), nullable=True)  # ok, warning, error
    validation_issues = Column(JSON, nullable=True)

    url_rel = relationship("Url", back_populates="structured_data")


# ---------------------------------------------------------------------------
# Resources (images, CSS, JS, PDFs)
# ---------------------------------------------------------------------------
class Resource(Base):
    __tablename__ = "resources"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)
    resource_url = Column(Text, nullable=False)
    resource_type = Column(String(20), nullable=False)  # image, css, js, pdf, font, other
    alt_text = Column(Text, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)

    # --- Screaming Frog extended fields ---
    width = Column(Integer, nullable=True)              # width attribute from HTML
    height = Column(Integer, nullable=True)             # height attribute from HTML
    is_mixed_content = Column(Boolean, nullable=True)   # HTTP resource on HTTPS page

    url_rel = relationship("Url", back_populates="resources")


# ---------------------------------------------------------------------------
# Security Headers (1:1 with Url)
# ---------------------------------------------------------------------------
class PageContent(Base):
    __tablename__ = "page_content"

    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), primary_key=True)
    content_text = Column(Text, nullable=True)
    content_length = Column(Integer, nullable=True)
    content_markdown = Column(Text, nullable=True)

    url_rel = relationship("Url", back_populates="page_content")


class SecurityHeaders(Base):
    __tablename__ = "security_headers"

    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), primary_key=True)

    is_https = Column(Boolean)
    has_mixed_content = Column(Boolean)
    has_hsts = Column(Boolean)                                  # Strict-Transport-Security header present
    has_csp = Column(Boolean)                                   # Content-Security-Policy header present
    has_x_content_type_options = Column(Boolean)                # X-Content-Type-Options present
    has_x_frame_options = Column(Boolean)                       # X-Frame-Options present
    referrer_policy = Column(String(64), nullable=True)         # Referrer-Policy header value
    has_unsafe_crossorigin = Column(Boolean)                    # target=_blank without rel=noopener

    url_rel = relationship("Url", back_populates="security")


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------
class Issue(Base):
    __tablename__ = "issues"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    url_id = Column(BigInteger, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False)
    issue_type = Column(String(64), nullable=False)
    severity = Column(String(10), nullable=False)  # error, warning, info
    details = Column(JSON, nullable=True)
    detected_at = Column(DateTime(timezone=True), default=_utcnow)

    url_rel = relationship("Url", back_populates="issues")

    __table_args__ = (
        Index("ix_issues_job", "job_id"),
        Index("ix_issues_job_type", "job_id", "issue_type"),
    )
