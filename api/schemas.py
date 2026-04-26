"""Pydantic schemas for the SEO crawler API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.config import (
    DEFAULT_CONCURRENT_REQUESTS,
    DEFAULT_CONCURRENT_REQUESTS_PER_DOMAIN,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_URLS,
    DEFAULT_USER_AGENT,
)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------
class Recommendation(BaseModel):
    priority: str
    title: str
    description: str
    affected_count: int


class CategoryInsight(BaseModel):
    key: str
    name: str
    score: int
    icon: str
    metrics: dict[str, Any]
    recommendations: list[Recommendation]


class InsightsResponse(BaseModel):
    job_id: uuid.UUID
    overall_score: int
    categories: list[CategoryInsight]
    generated_at: datetime


# ---------------------------------------------------------------------------
# Job configuration
# ---------------------------------------------------------------------------
class JobConfig(BaseModel):
    """Crawl configuration that travels with every job."""

    max_depth: int = Field(default=DEFAULT_MAX_DEPTH, ge=1, le=50)
    max_urls: int = Field(default=DEFAULT_MAX_URLS, ge=1, le=5_000_000)
    follow_external: bool = False
    respect_robots: bool = True
    concurrent_requests: int = Field(default=DEFAULT_CONCURRENT_REQUESTS, ge=1, le=256)
    concurrent_requests_per_domain: int = Field(
        default=DEFAULT_CONCURRENT_REQUESTS_PER_DOMAIN, ge=1, le=64
    )
    user_agent: str = DEFAULT_USER_AGENT
    render_js: bool = False
    exclude_patterns: list[str] = Field(default_factory=list)
    include_patterns: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Job create / response
# ---------------------------------------------------------------------------
class JobCreate(BaseModel):
    """Payload to create a new crawl job."""

    name: str = Field(..., min_length=1, max_length=512)
    seeds: list[str] = Field(..., min_length=1)
    client_id: str | None = Field(default=None, max_length=128)
    config: JobConfig = Field(default_factory=JobConfig)

    @field_validator("seeds", mode="before")
    @classmethod
    def validate_seeds(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        for url in v:
            url = url.strip()
            if not url:
                continue
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"Seed URL must start with http:// or https://: {url}")
            cleaned.append(url)
        if not cleaned:
            raise ValueError("At least one valid seed URL is required")
        return cleaned


class JobResponse(BaseModel):
    """Full representation of a crawl job."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    client_id: str | None
    owner_id: str | None
    status: str
    seeds: list[str]
    config: dict[str, Any]
    total_urls_discovered: int
    total_urls_crawled: int
    total_urls_failed: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


# ---------------------------------------------------------------------------
# URL / HTML meta
# ---------------------------------------------------------------------------
class HtmlMetaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    url_id: int
    title: str | None = None
    title_len: int | None = None
    meta_description: str | None = None
    meta_description_len: int | None = None
    meta_keywords: str | None = None
    meta_robots: str | None = None
    x_robots_tag: str | None = None
    canonical_href: str | None = None
    canonical_header: str | None = None
    og_title: str | None = None
    og_description: str | None = None
    og_image: str | None = None
    og_url: str | None = None
    og_type: str | None = None
    twitter_card: str | None = None
    twitter_title: str | None = None
    twitter_description: str | None = None
    rel_next: str | None = None
    rel_prev: str | None = None
    # Screaming Frog parity
    title_pixel_width: int | None = None
    meta_description_pixel_width: int | None = None
    meta_refresh: str | None = None
    has_meta_outside_head: bool | None = None


class PageContentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    url_id: int
    content_text: str | None = None
    content_length: int | None = None
    content_markdown: str | None = None


class HeadingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tag: str
    position: int
    text: str | None = None


class ResourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    resource_url: str
    resource_type: str
    alt_text: str | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    is_mixed_content: bool | None = None


class HreflangResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lang: str
    href: str
    return_tag_ok: bool | None = None
    lang_valid: bool | None = None


class StructuredDataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    format: str
    schema_type: str | None = None
    validation_status: str | None = None
    validation_issues: dict[str, Any] | list | None = None
    raw: dict[str, Any] | list | None = None


class SecurityHeadersResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    is_https: bool | None = None
    has_mixed_content: bool | None = None
    has_hsts: bool | None = None
    has_csp: bool | None = None
    has_x_content_type_options: bool | None = None
    has_x_frame_options: bool | None = None
    referrer_policy: str | None = None
    has_unsafe_crossorigin: bool | None = None


class UrlResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: uuid.UUID
    url: str
    host: str | None = None
    path: str | None = None
    scheme: str | None = None
    is_internal: bool | None = None
    crawl_depth: int | None = None
    content_type: str | None = None
    content_length: int | None = None
    status_code: int | None = None
    status_group: str | None = None
    response_time_ms: float | None = None
    is_html: bool | None = None
    resource_type: str | None = None
    redirect_url: str | None = None
    indexable: bool | None = None
    first_seen_at: datetime | None = None
    last_crawled_at: datetime | None = None
    # Screaming Frog parity
    url_length: int | None = None
    folder_depth: int | None = None
    word_count: int | None = None
    text_ratio: float | None = None
    redirect_type: int | None = None
    status_text: str | None = None
    last_modified: str | None = None
    http_version: str | None = None
    transfer_size: int | None = None
    indexability_status: str | None = None
    inlinks_count: int | None = None
    outlinks_count: int | None = None
    external_outlinks_count: int | None = None
    unique_inlinks_count: int | None = None
    html_meta: HtmlMetaResponse | None = None
    page_content: PageContentResponse | None = None


class UrlDetailResponse(UrlResponse):
    """Full URL detail with all related data for the detail view."""

    headings: list[HeadingResponse] = []
    resources: list[ResourceResponse] = []
    hreflangs: list[HreflangResponse] = []
    structured_data: list[StructuredDataResponse] = []
    security: SecurityHeadersResponse | None = None
    issues: list[IssueResponse] = []
    inlinks: list[LinkResponse] = []
    outlinks: list[LinkResponse] = []


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------
class IssueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: uuid.UUID
    url_id: int
    url: str | None = None
    issue_type: str
    severity: str
    details: dict[str, Any] | None = None
    detected_at: datetime | None = None


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------
class LinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: uuid.UUID
    from_url_id: int
    from_url: str | None = None
    to_url: str
    anchor_text: str | None = None
    rel: str | None = None
    is_internal: bool | None = None
    link_position: str | None = None
    # Screaming Frog parity
    follow: bool | None = None
    target: str | None = None
    alt_text: str | None = None
    link_type: str | None = None


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
class StatusGroupCount(BaseModel):
    status_group: str
    count: int


class IssueTypeCount(BaseModel):
    issue_type: str
    severity: str
    count: int


class HostCount(BaseModel):
    host: str
    count: int


class JobStats(BaseModel):
    job_id: uuid.UUID
    total_urls: int
    total_urls_crawled: int
    total_urls_failed: int
    urls_by_status_group: list[StatusGroupCount]
    issues_by_type: list[IssueTypeCount]
    top_hosts: list[HostCount]


# ---------------------------------------------------------------------------
# Paginated wrapper
# ---------------------------------------------------------------------------
class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int
