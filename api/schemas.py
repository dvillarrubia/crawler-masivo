"""Pydantic schemas for the SEO crawler API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    issue_types: list[str] = []
    url_filter: dict[str, Any] = {}


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
# Job configuration — sub-models
# ---------------------------------------------------------------------------
class ResourceTypeConfig(BaseModel):
    """Which resource types to crawl."""

    crawl_images: bool = True
    crawl_css: bool = True
    crawl_js: bool = True
    crawl_pdfs: bool = True
    crawl_fonts: bool = False
    crawl_svg: bool = True
    crawl_other: bool = True
    check_external_resources: bool = False


class CrawlBehaviorConfig(BaseModel):
    """Speed and behavior tuning."""

    download_timeout: int = Field(default=30, ge=5, le=120)
    retry_count: int = Field(default=2, ge=0, le=10)
    request_delay: float = Field(default=0.0, ge=0.0, le=30.0)
    autothrottle_enabled: bool = True
    autothrottle_target_concurrency: float = Field(default=8.0, ge=1.0, le=100.0)
    follow_nofollow: bool = False
    crawl_subdomains: bool = False
    max_runtime_hours: int = Field(default=6, ge=1, le=72)


class UrlFilterConfig(BaseModel):
    """URL-level filtering rules."""

    max_url_length: int = Field(default=0, ge=0, le=10000)
    max_folder_depth: int = Field(default=0, ge=0, le=100)


class ExtractionConfig(BaseModel):
    """Toggle extraction of optional data."""

    extract_structured_data: bool = True
    extract_hreflang: bool = True
    extract_security_headers: bool = True
    extract_page_content: bool = True
    store_raw_html: bool = False


class HttpConfig(BaseModel):
    """Custom HTTP settings sent with every request."""

    custom_headers: dict[str, str] = Field(default_factory=dict)
    accept_language: str = ""
    cookies: dict[str, str] = Field(default_factory=dict)
    basic_auth_user: str = ""
    basic_auth_password: str = ""


class TrapDetectionConfig(BaseModel):
    """T13: crawl-trap protection (off by default = current behaviour)."""

    enabled: bool = False
    max_urls_per_pattern: int = Field(default=500, ge=10, le=100000)
    max_param_combinations: int = Field(default=3, ge=1, le=20)


class UrlNormalizationConfig(BaseModel):
    """URL normalization policy (T8). Defaults reproduce current behaviour."""

    strip_params: list[str] = Field(default_factory=list)
    strip_common_tracking: bool = False


class AnalysisThresholdsConfig(BaseModel):
    """Per-job thresholds for SEO analysis."""

    title_min_length: int = Field(default=10, ge=0, le=200)
    title_max_length: int = Field(default=60, ge=1, le=500)
    description_min_length: int = Field(default=50, ge=0, le=500)
    description_max_length: int = Field(default=160, ge=1, le=1000)
    min_word_count: int = Field(default=200, ge=0, le=10000)
    max_redirect_chain_length: int = Field(default=2, ge=1, le=20)
    max_outlinks: int = Field(default=100, ge=1, le=10000)
    # T3: 1 = algoritmo histórico bit a bit; 2 = nofollow diluyente +
    # colapso de redirecciones + solo-indexables + equity_leak
    pagerank_version: int = Field(default=1, ge=1, le=2)
    equity_leak_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    # T17.3: umbral de página lenta en milisegundos
    slow_page_ms: int = Field(default=3000, ge=100, le=120000)
    # T14: near-duplicates (off = comportamiento actual)
    near_duplicate_detection: Literal["off", "simhash", "embeddings"] = "off"
    near_duplicate_hamming: int = Field(default=3, ge=1, le=16)
    # T6: similitud de tokens con la plantilla de error del probe
    soft404_similarity: float = Field(default=0.85, ge=0.5, le=1.0)
    # T20: contenido único (descuento de boilerplate por segmento)
    unique_content_analysis: bool = False
    boilerplate_shingle_share: float = Field(default=0.3, ge=0.05, le=1.0)
    min_unique_word_count: int = Field(default=100, ge=0, le=10000)


# ---------------------------------------------------------------------------
# Job configuration
# ---------------------------------------------------------------------------
class JobConfig(BaseModel):
    """Crawl configuration that travels with every job."""

    max_depth: int = Field(default=DEFAULT_MAX_DEPTH, ge=1, le=50)
    max_urls: int = Field(default=DEFAULT_MAX_URLS, ge=1, le=5_000_000)
    follow_external: bool = False
    robots_mode: Literal["respect", "ignore", "audit"] = "respect"
    concurrent_requests: int = Field(default=DEFAULT_CONCURRENT_REQUESTS, ge=1, le=256)
    concurrent_requests_per_domain: int = Field(
        default=DEFAULT_CONCURRENT_REQUESTS_PER_DOMAIN, ge=1, le=64
    )
    user_agent: str = DEFAULT_USER_AGENT
    render_js: bool = False
    impersonate: str = "chrome124"
    # T1: ingest sitemap.xml at job start (in_sitemap flags + orphan basis)
    ingest_sitemaps: bool = False
    # T6: probe de soft 404 por host al inicio del job
    detect_soft_404: bool = False
    # T5: job anterior contra el que evaluar frescura (stale_lastmod)
    compare_to_job_id: str | None = None
    # T15: comparar HTML crudo vs renderizado (exige render_js)
    geo_analysis: bool = False
    # T22/T23: clasificador de aristas + capa de arquitectura
    edge_classification: bool = False
    exclude_patterns: list[str] = Field(default_factory=list)
    include_patterns: list[str] = Field(default_factory=list)

    # Advanced configuration sub-models
    @model_validator(mode="after")
    def _geo_requires_render_js(self):
        # T15: sin render no hay DOM renderizado que comparar
        if self.geo_analysis and not self.render_js:
            raise ValueError(
                "geo_analysis requiere render_js=true: compara el HTML "
                "crudo con el DOM renderizado por el navegador"
            )
        return self

    resource_types: ResourceTypeConfig = Field(default_factory=ResourceTypeConfig)
    crawl_behavior: CrawlBehaviorConfig = Field(default_factory=CrawlBehaviorConfig)
    url_filters: UrlFilterConfig = Field(default_factory=UrlFilterConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    analysis_thresholds: AnalysisThresholdsConfig = Field(default_factory=AnalysisThresholdsConfig)
    url_normalization: UrlNormalizationConfig = Field(default_factory=UrlNormalizationConfig)
    trap_detection: TrapDetectionConfig = Field(default_factory=TrapDetectionConfig)


class ReanalyzeRequest(BaseModel):
    """T17.2: optional threshold overrides for a reanalysis run."""

    analysis_thresholds: AnalysisThresholdsConfig | None = None


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
    # T4: derived from meta_refresh by the analyzer
    meta_refresh_url: str | None = None
    meta_refresh_delay: int | None = None


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
    blocked_by_robots: bool | None = None
    inlinks_count: int | None = None
    outlinks_count: int | None = None
    external_outlinks_count: int | None = None
    unique_inlinks_count: int | None = None
    pagerank: float | None = None
    # T1/T4
    in_sitemap: bool | None = None
    sitemap_lastmod: datetime | None = None
    js_redirect_url: str | None = None
    # T23 arquitectura / T20 contenido único / T18 semántico / T15 GEO
    click_depth: int | None = None
    in_contextual: int | None = None
    out_contextual: int | None = None
    unique_word_count: int | None = None
    boilerplate_ratio: float | None = None
    pagerank_semantic: float | None = None
    js_content_ratio: float | None = None
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
    # T10: NULL para issues deterministas; pending/signed/rejected para
    # los checks de juicio (canibalización semántica)
    review_status: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


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
    # T17.5.b: DOM context
    dom_ancestor: str | None = None
    dom_container: str | None = None
    # T22: fine edge taxonomy (NULL unless edge_classification ran)
    edge_class: str | None = None


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


class ResourceTypeCount(BaseModel):
    resource_type: str
    count: int


class LatencyPercentiles(BaseModel):
    """T17.3: nearest-rank latency percentiles in milliseconds."""

    p50: float
    p90: float
    p99: float


class LatencyStats(LatencyPercentiles):
    by_status_group: dict[str, LatencyPercentiles] = {}


class JobStats(BaseModel):
    job_id: uuid.UUID
    total_urls: int
    total_urls_crawled: int
    total_urls_failed: int
    urls_by_status_group: list[StatusGroupCount]
    issues_by_type: list[IssueTypeCount]
    top_hosts: list[HostCount]
    urls_by_resource_type: list[ResourceTypeCount] = []
    internal_count: int = 0
    external_count: int = 0
    # T17.3 — None when the job has no timed responses
    latency: LatencyStats | None = None
    # T15 — present only for geo_analysis jobs (never fake zeros)
    geo: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Paginated wrapper
# ---------------------------------------------------------------------------
class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


# ---------------------------------------------------------------------------
# Backup / Import
# ---------------------------------------------------------------------------
class BackupManifest(BaseModel):
    format_version: str
    export_timestamp: datetime
    job_id: uuid.UUID
    job_name: str
    job_status: str
    row_counts: dict[str, int]
    has_page_content: bool


class ImportResponse(BaseModel):
    new_job_id: uuid.UUID
    original_job_id: uuid.UUID
    rows_imported: dict[str, int]
    rows_skipped: dict[str, int]
    warnings: list[str]
