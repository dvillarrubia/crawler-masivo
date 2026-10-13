"""Pydantic schemas for the SEO crawler API."""

from __future__ import annotations

import re
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
    # Red de seguridad dura, no el mecanismo principal: la parada real la
    # decide stall_timeout_minutes. Adivinar cuanto tarda un rastreo es
    # imposible —el mismo sitio rinde 7x distinto con y sin render— y cortar
    # por reloj mato un rastreo al 97,5%.
    max_runtime_hours: int = Field(default=72, ge=1, le=720)
    # Minutos sin avanzar tras los que se da el rastreo por muerto. 0 lo
    # desactiva. El spider estampa un latido al actualizar progreso y en cada
    # lote de siembra de la frontera, asi que un rastreo lento pero vivo no
    # se ve afectado.
    stall_timeout_minutes: int = Field(default=30, ge=0, le=1440)
    # Al reanudar, repetir las paginas perdidas (status NULL), 5xx y las que
    # Chromium dejo en pagina de error, en vez de darlas por rastreadas.
    retry_failed_on_resume: bool = True
    # Al reanudar, volver a rastrear tambien las URLs que casen (regex).
    recrawl_patterns: list[str] = Field(default_factory=list)
    # Al reanudar, repetir las HTML 200 internas guardadas sin contenido
    # extraido (tras corregir el stripper o los selectores del cliente).
    recrawl_empty_content: bool = False
    # Tope (ms) de la espera a que el DOM se calme tras domcontentloaded con
    # render_js; 0/None = el del entorno (PLAYWRIGHT_BANNER_WAIT_MS, def. 2000).
    render_wait_ms: int | None = Field(default=None, ge=0, le=60000)


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
    strip_promo_blocks: bool = True
    custom_boilerplate_selectors: list[str] = Field(default_factory=list)
    store_raw_html: bool = False


class HttpConfig(BaseModel):
    """Custom HTTP settings sent with every request."""

    custom_headers: dict[str, str] = Field(default_factory=dict)
    accept_language: str = ""
    cookies: dict[str, str] = Field(default_factory=dict)
    basic_auth_user: str = ""
    basic_auth_password: str = ""


class AnalysisThresholdsConfig(BaseModel):
    """Per-job thresholds for SEO analysis."""

    title_min_length: int = Field(default=10, ge=0, le=200)
    title_max_length: int = Field(default=60, ge=1, le=500)
    description_min_length: int = Field(default=50, ge=0, le=500)
    description_max_length: int = Field(default=160, ge=1, le=1000)
    min_word_count: int = Field(default=200, ge=0, le=10000)
    # Saltos de redireccion que se consideran aceptables. Uno es una
    # redireccion normal; dos encadenadas (A->B->C) ya son algo que arreglar y
    # es lo que Google pide evitar. Estaba en 2 —o sea, hacian falta tres— y
    # eso dejaba sin reportar 372 cadenas de dos saltos en un solo censo.
    max_redirect_chain_length: int = Field(default=1, ge=1, le=20)
    max_outlinks: int = Field(default=100, ge=1, le=10000)


# ---------------------------------------------------------------------------
# Job configuration
# ---------------------------------------------------------------------------
class TemplateRule(BaseModel):
    """Regla de plantilla: la primera cuyo ``patron`` (regex sobre el path)
    casa da nombre a la plantilla. Las usan check_js_templates.py y
    check_content_quality.py para muestrear por plantilla."""

    nombre: str = Field(..., min_length=1, max_length=80)
    patron: str = Field(..., min_length=1, max_length=500)


class JobConfig(BaseModel):
    """Crawl configuration that travels with every job."""

    # Tope 1000, no 50: en WordPress el paginador solo enlaza a la pagina
    # siguiente, asi que /page/120/ esta a profundidad 120 de la home. Con 50
    # se cortaba la cadena y los posts de las paginas profundas salian como
    # huerfanos aunque estuvieran enlazados.
    max_depth: int = Field(default=DEFAULT_MAX_DEPTH, ge=1, le=1000)
    # None = rastrear hasta agotar la frontera (comportamiento de Screaming
    # Frog). Antes era obligatorio con default 50.000, asi que habia que
    # adivinar el tamano del sitio ANTES de rastrearlo: quedarse corto truncaba
    # el crawl en silencio y el PageRank salia calculado sobre un grafo parcial.
    max_urls: int | None = Field(default=None, ge=1, le=5_000_000)
    follow_external: bool = False
    robots_mode: Literal["respect", "ignore", "audit"] = "respect"
    concurrent_requests: int = Field(default=DEFAULT_CONCURRENT_REQUESTS, ge=1, le=256)
    concurrent_requests_per_domain: int = Field(
        default=DEFAULT_CONCURRENT_REQUESTS_PER_DOMAIN, ge=1, le=64
    )
    user_agent: str = DEFAULT_USER_AGENT
    render_js: bool = False
    impersonate: str = "chrome124"
    exclude_patterns: list[str] = Field(default_factory=list)
    include_patterns: list[str] = Field(default_factory=list)
    use_sitemap: bool = True
    sitemap_urls: list[str] = Field(default_factory=list)
    # Reglas de plantilla por cliente (ver projects/README.md)
    templates: list[TemplateRule] = Field(default_factory=list)

    # Advanced configuration sub-models
    resource_types: ResourceTypeConfig = Field(default_factory=ResourceTypeConfig)
    crawl_behavior: CrawlBehaviorConfig = Field(default_factory=CrawlBehaviorConfig)
    url_filters: UrlFilterConfig = Field(default_factory=UrlFilterConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    analysis_thresholds: AnalysisThresholdsConfig = Field(default_factory=AnalysisThresholdsConfig)


# ---------------------------------------------------------------------------
# Job create / response
# ---------------------------------------------------------------------------
def _validar_expresion_cron(v: str) -> str:
    """Se valida al ESCRIBIRLA, no al dispararla.

    Una expresion que no se puede interpretar, guardada, es una programacion
    que no se dispara nunca — y eso se lee igual que un sitio que no cambia.
    El error tiene que salir cuando alguien la escribe.
    """
    from shared.programaciones import cron_valido

    if not cron_valido(v):
        raise ValueError(
            f"'{v}' no es una expresion cron valida de cinco campos "
            f"(minuto hora dia-del-mes mes dia-de-semana). "
            f"Por ejemplo: '0 3 * * 1' es todos los lunes a las 3:00"
        )
    return v.strip()


def _validar_zona(v: str | None) -> str | None:
    if v is None:
        return v
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(v)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"zona horaria desconocida: {v!r}") from exc
    return v


class ProgramacionCreate(BaseModel):
    """Un rastreo que se repite. Mismo cuerpo que un job, mas el cuando."""

    nombre: str = Field(..., min_length=1, max_length=512)
    seeds: list[str] = Field(..., min_length=1)
    client_id: str | None = Field(default=None, max_length=128)
    # De quien es (#36). Los jobs que dispare salen colgados de este cliente
    # y perfil; la configuracion sigue siendo la suya propia.
    cliente_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    config: JobConfig = Field(default_factory=JobConfig)
    # Expresion de cinco campos: `minuto hora dia-del-mes mes dia-de-semana`.
    cron: str = Field(..., min_length=1, max_length=128)
    zona_horaria: str | None = Field(default=None, max_length=64)
    activa: bool = True

    @field_validator("cron")
    @classmethod
    def validar_cron(cls, v: str) -> str:
        return _validar_expresion_cron(v)

    @field_validator("zona_horaria")
    @classmethod
    def validar_zona_horaria(cls, v: str | None) -> str | None:
        return _validar_zona(v)

    @field_validator("seeds", mode="before")
    @classmethod
    def validar_semillas(cls, v: list[str]) -> list[str]:
        return JobCreate.validate_seeds(v)


class ProgramacionUpdate(BaseModel):
    """Lo que se puede cambiar de una programacion ya creada."""

    nombre: str | None = Field(default=None, min_length=1, max_length=512)
    cron: str | None = Field(default=None, min_length=1, max_length=128)
    zona_horaria: str | None = Field(default=None, max_length=64)
    activa: bool | None = None

    @field_validator("cron")
    @classmethod
    def validar_cron(cls, v: str | None) -> str | None:
        return None if v is None else _validar_expresion_cron(v)

    @field_validator("zona_horaria")
    @classmethod
    def validar_zona_horaria(cls, v: str | None) -> str | None:
        return _validar_zona(v)


class ProgramacionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre: str
    client_id: str | None = None
    cliente_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    seeds: list[str]
    config: dict[str, Any] | None = None
    cron: str
    zona_horaria: str | None = None
    activa: bool
    proxima_ejecucion: datetime | None = None
    ultima_ejecucion: datetime | None = None
    ultimo_job_id: uuid.UUID | None = None
    ultimo_motivo: str | None = None
    created_at: datetime | None = None


def _limpiar_semillas(v: list[str] | None, *, obligatorias: bool = True) -> list[str]:
    cleaned: list[str] = []
    for url in v or []:
        url = url.strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Seed URL must start with http:// or https://: {url}")
        cleaned.append(url)
    if obligatorias and not cleaned:
        raise ValueError("At least one valid seed URL is required")
    return cleaned


class JobCreate(BaseModel):
    """Payload to create a new crawl job.

    Con cliente (#36): `cliente_id` y/o `profile_id`. La configuracion que
    llega aqui es solo lo que el job CAMBIA: se fusiona encima de la del
    cliente y la del perfil (`shared/clientes.fusionar_config`) y lo resuelto
    se copia en `jobs.config`. Por eso se lee con `exclude_unset`: si se
    leyera el `JobConfig` entero, sus valores por defecto pisarian todo lo
    heredado. Sin perfil, las semillas son obligatorias; con perfil, si no se
    mandan se usan las del perfil.

    `client_id` (texto) se mantiene por compatibilidad —`lanzar_job.py` y los
    backups viejos lo mandan—: se resuelve por slug y, si no existe, se crea el
    cliente.
    """

    name: str = Field(..., min_length=1, max_length=512)
    seeds: list[str] = Field(default_factory=list)
    client_id: str | None = Field(default=None, max_length=128)
    cliente_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    config: JobConfig = Field(default_factory=JobConfig)

    @field_validator("seeds", mode="before")
    @classmethod
    def _semillas(cls, v: list[str] | None) -> list[str]:
        # Vacias se permiten AQUI: con perfil salen del perfil. El endpoint
        # comprueba que al final haya alguna.
        return _limpiar_semillas(v, obligatorias=False)

    @classmethod
    def validate_seeds(cls, v: list[str]) -> list[str]:
        """Semillas obligatorias. Lo reutilizan las programaciones."""
        return _limpiar_semillas(v, obligatorias=True)


class JobResponse(BaseModel):
    """Full representation of a crawl job."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    client_id: str | None
    cliente_id: uuid.UUID | None = None
    # Lo rellena el endpoint con una consulta aparte: una relacion cargada en
    # cada `query(Job)` seria un JOIN en el worker y el analizador para nada.
    cliente_nombre: str | None = None
    profile_id: uuid.UUID | None = None
    owner_id: str | None
    status: str
    # "finished" = frontera agotada; "max_urls_reached" = truncado
    finish_reason: str | None = None
    crawler_version: str | None = None
    # Comprobacion de render JS por plantilla (solo en rastreos sin render_js)
    js_check: dict[str, Any] | None = None
    comparacion: dict[str, Any] | None = None
    analisis_version: str | None = None
    # Reparto del PageRank por tipo de URL y fraccion desperdiciada en errores
    pagerank_resumen: dict[str, Any] | None = None
    # Capa de decisiones (#7): llamadas, tokens, coste real, aciertos de
    # cache, 429, latencia y distribucion de respuestas. NULL = nunca se le
    # pregunto nada a un modelo sobre este job.
    decisiones_resumen: dict[str, Any] | None = None
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
    oculto: bool | None = None


class ResourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    resource_url: str
    resource_type: str
    alt_text: str | None = None
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
    json_invalido: str | None = None


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
    content_word_count: int | None = None
    content_hash: str | None = None
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
    # La escala que hay que leer y ordenar (decision 34). Sin esto en la
    # respuesta, la tabla solo podia pintar la 0-10 con cuatro decimales, donde
    # el 85% de las paginas de un censo queda en 0,00xx.
    pagerank_score: int | None = None
    pagerank_raw: float | None = None
    in_sitemap: bool | None = None
    near_duplicate_count: int | None = None
    closest_similarity: float | None = None
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


class ResourceTypeCount(BaseModel):
    resource_type: str
    count: int


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
# Clientes, perfiles e integraciones (#36, F1)
# ---------------------------------------------------------------------------
# Dicts que son DATOS del usuario: sus claves no las declara ningun schema.
_DICTS_DE_DATOS = {"http.custom_headers", "http.cookies"}


def _claves(d: Any, prefijo: str = "") -> set[str]:
    """Rutas de las claves de un dict anidado: `{"a": {"b": 1}}` -> {a, a.b}."""
    if not isinstance(d, dict):
        return set()
    rutas: set[str] = set()
    for k, v in d.items():
        ruta = f"{prefijo}{k}"
        rutas.add(ruta)
        if ruta not in _DICTS_DE_DATOS:
            rutas |= _claves(v, ruta + ".")
    return rutas


def _separar_nones(d: dict, prefijo: str = "") -> tuple[dict, list[str]]:
    """`{"a": None, "b": {"c": None, "d": 1}}` -> `({"b": {"d": 1}}, ["a", "b.c"])`."""
    valores: dict = {}
    borrados: list[str] = []
    for k, v in d.items():
        ruta = f"{prefijo}{k}"
        if v is None:
            borrados.append(ruta)
        elif isinstance(v, dict) and ruta not in _DICTS_DE_DATOS:
            sub, sub_borrados = _separar_nones(v, ruta + ".")
            valores[k] = sub
            borrados += sub_borrados
        else:
            valores[k] = v
    return valores, borrados


def validar_config_parcial(d: dict[str, Any] | None) -> dict[str, Any]:
    """Valida una configuracion PARCIAL (la de un cliente o un perfil) y
    devuelve solo lo que fija, ya normalizado.

    Dos cosas que `JobConfig.model_validate(d).model_dump()` haria mal:

    - rellenaria todos los valores por defecto, asi que un cliente que solo
      fija `render_js` «fijaria» tambien `max_depth=3` y pisaria al perfil;
    - **tiraria en silencio lo que no esta declarado** (decision 33). En un job
      eso ya costo `use_sitemap`; en la configuracion de un cliente, que se
      escribe una vez y se hereda en cada rastreo, seria peor. Aqui una clave
      desconocida es un error que se nombra.

    Ademas, la contrasena de autenticacion basica no se acepta en un cliente
    ni en un perfil: se guardaria en la base de datos para siempre y saldria
    por `GET /api/clients` a cualquiera (decision 83). En un job puntual sigue
    pudiendo ir, como hasta ahora.
    """
    d = d or {}
    if not isinstance(d, dict):
        raise ValueError("la configuracion tiene que ser un objeto")
    # Un `None` explicito significa «quitar lo heredado» (un perfil que deja
    # sin tope de URLs a un cliente que lo fija). No es un valor, asi que no
    # se valida contra JobConfig —`render_js: None` no lo aceptaria—, pero se
    # GUARDA: es la unica forma de expresar esa intencion.
    valores, borrados = _separar_nones(d)
    modelo = JobConfig.model_validate(valores)
    parcial = modelo.model_dump(exclude_unset=True, mode="json")
    tiradas = sorted((_claves(valores) - _claves(parcial))
                     | (set(borrados) - _claves(JobConfig().model_dump())))
    if tiradas:
        raise ValueError(
            "claves de configuracion desconocidas (no tendrian ningun efecto): "
            + ", ".join(tiradas))
    for ruta in borrados:
        destino = parcial
        *padres, hoja = ruta.split(".")
        for p in padres:
            destino = destino.setdefault(p, {})
        destino[hoja] = None
    if (parcial.get("http") or {}).get("basic_auth_password"):
        raise ValueError(
            "http.basic_auth_password no se guarda en un cliente ni en un "
            "perfil: la API no tiene autenticacion y se podria leer. Ponla en "
            "el rastreo concreto")
    return parcial


class Integracion(BaseModel):
    """Una API externa del cliente. **Sin secretos** (decision 83).

    `credencial_env` es el NOMBRE de la variable de entorno del servidor que
    tiene la clave (`DATAFORSEO_LIN3S`), no la clave. Tiene que empezar por el
    prefijo de su tipo (`shared/credenciales.PREFIJOS`).
    """

    model_config = ConfigDict(extra="forbid")

    tipo: Literal["gsc", "dataforseo", "ahrefs", "semrush", "majestic", "pagespeed"]
    etiqueta: str | None = Field(default=None, max_length=128)
    # Parametros publicos: propiedad de GSC, ubicacion e idioma de DataForSEO...
    parametros: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    credencial_env: str | None = Field(default=None, max_length=128)
    activa: bool = True

    @field_validator("credencial_env", mode="before")
    @classmethod
    def _vacio_es_none(cls, v):
        if isinstance(v, str):
            v = v.strip()
        return v or None

    @field_validator("parametros")
    @classmethod
    def _sin_secretos(cls, v: dict) -> dict:
        from shared.credenciales import parametro_sospechoso

        clave = parametro_sospechoso(v)
        if clave:
            raise ValueError(
                f"el parametro {clave!r} parece un secreto. Los secretos no se "
                "guardan en la base de datos: van en una variable de entorno "
                "del servidor y aqui se pone su nombre en credencial_env")
        return v

    @model_validator(mode="after")
    def _variable_valida(self):
        from shared.credenciales import error_en_variable

        error = error_en_variable(self.tipo, self.credencial_env)
        if error:
            raise ValueError(error)
        return self


class IntegracionResponse(BaseModel):
    """Lo que se devuelve de una integracion: nunca el valor del secreto."""

    tipo: str
    etiqueta: str | None = None
    parametros: dict[str, Any] = Field(default_factory=dict)
    credencial_env: str | None = None
    activa: bool = True
    # Si la variable existe y no esta vacia en el entorno de la API.
    configurada: bool = False


_PATRON_DOMINIO = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")


def _validar_dominios(v: list[str] | None) -> list[str]:
    """`https://WWW.X.com/blog` -> `www.x.com`. Sin duplicados, en orden."""
    vistos: list[str] = []
    for d in v or []:
        d = (d or "").strip().lower()
        if not d:
            continue
        d = re.sub(r"^[a-z][a-z0-9+.-]*://", "", d).split("/", 1)[0].split(":", 1)[0]
        if d != "localhost" and not _PATRON_DOMINIO.match(d):
            raise ValueError(f"{d!r} no es un dominio")
        if d not in vistos:
            vistos.append(d)
    return vistos


class DecisionesCliente(BaseModel):
    """Lo del cliente en la capa de decisiones (#7). Un presupuesto, no un
    secreto: la clave de OpenRouter va en el entorno (decision 84)."""

    model_config = ConfigDict(extra="forbid")

    # Tope de gasto en llamadas a modelos por mes natural (UTC), sumando todas
    # las ejecuciones del cliente. None = sin tope propio (manda el global,
    # `ENRICHMENT_MAX_USD_MES`). Con rastreos semanales, un tope por ejecucion
    # se multiplica sin control; este no.
    presupuesto_mensual_usd: float | None = Field(default=None, ge=0, le=100_000)


class _ClienteCampos(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("slug", check_fields=False)
    @classmethod
    def _slug(cls, v: str | None) -> str | None:
        from shared.clientes import slug_valido

        if v is None:
            return v
        v = v.strip()
        if not slug_valido(v):
            raise ValueError(
                f"{v!r} no es un slug valido: minusculas, digitos y guiones")
        return v

    @field_validator("dominios", check_fields=False)
    @classmethod
    def _dominios(cls, v):
        return None if v is None else _validar_dominios(v)

    @field_validator("zona_horaria", check_fields=False)
    @classmethod
    def _zona(cls, v):
        return _validar_zona(v)

    @field_validator("config_defecto", check_fields=False)
    @classmethod
    def _config(cls, v):
        return None if v is None else validar_config_parcial(v)


class ClientCreate(_ClienteCampos):
    nombre: str = Field(..., min_length=1, max_length=256)
    # Si no se manda, sale del nombre.
    slug: str | None = Field(default=None, max_length=128)
    dominios: list[str] = Field(default_factory=list)
    zona_horaria: str | None = Field(default="Europe/Madrid", max_length=64)
    config_defecto: dict[str, Any] = Field(default_factory=dict)
    integraciones: list[Integracion] = Field(default_factory=list)
    decisiones: DecisionesCliente | None = None
    notas: str | None = None


class ClientUpdate(_ClienteCampos):
    nombre: str | None = Field(default=None, min_length=1, max_length=256)
    slug: str | None = Field(default=None, max_length=128)
    dominios: list[str] | None = None
    zona_horaria: str | None = Field(default=None, max_length=64)
    config_defecto: dict[str, Any] | None = None
    # Se sustituye la lista entera: es como la edita la interfaz.
    integraciones: list[Integracion] | None = None
    decisiones: DecisionesCliente | None = None
    notas: str | None = None


class ClientResponse(BaseModel):
    id: uuid.UUID
    nombre: str
    slug: str
    dominios: list[str] = Field(default_factory=list)
    zona_horaria: str | None = None
    config_defecto: dict[str, Any] = Field(default_factory=dict)
    integraciones: list[IntegracionResponse] = Field(default_factory=list)
    decisiones: dict[str, Any] | None = None
    notas: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    n_perfiles: int = 0
    n_jobs: int = 0
    ultimo_job_at: datetime | None = None


class _PerfilCampos(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("seeds", check_fields=False, mode="before")
    @classmethod
    def _semillas(cls, v):
        return None if v is None else _limpiar_semillas(v, obligatorias=True)

    @field_validator("config", check_fields=False)
    @classmethod
    def _config(cls, v):
        return None if v is None else validar_config_parcial(v)


class ProfileCreate(_PerfilCampos):
    nombre: str = Field(..., min_length=1, max_length=256)
    rol: Literal["propio", "competidor"] = "propio"
    seeds: list[str] = Field(..., min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    notas: str | None = None
    activo: bool = True


class ProfileUpdate(_PerfilCampos):
    nombre: str | None = Field(default=None, min_length=1, max_length=256)
    rol: Literal["propio", "competidor"] | None = None
    seeds: list[str] | None = None
    config: dict[str, Any] | None = None
    notas: str | None = None
    activo: bool | None = None


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cliente_id: uuid.UUID
    nombre: str
    rol: str
    seeds: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    notas: str | None = None
    activo: bool = True
    last_job_id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConfigResueltaResponse(BaseModel):
    """La configuracion que tendria un job lanzado ahora desde este cliente
    (y perfil): para precargar el modal con lo heredado."""

    cliente_id: uuid.UUID
    profile_id: uuid.UUID | None = None
    seeds: list[str] = Field(default_factory=list)
    # Completa: lo que tendria el job (defaults incluidos).
    config: dict[str, Any]
    # Solo lo que fijan cliente y perfil, sin los defaults. Es lo que precarga
    # el modal: con la completa, un default de la API (el UA `SEOCrawler/1.0`)
    # pisaria el del formulario sin que nadie lo hubiera elegido.
    config_heredada: dict[str, Any] = Field(default_factory=dict)


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


# ---------------------------------------------------------------------------
# Post-crawl cleaning
# ---------------------------------------------------------------------------
class CleaningRule(BaseModel):
    """One cleaning rule. See api/cleaning_engine.py for semantics."""

    type: Literal[
        "line_exact", "line_prefix", "line_contains",
        "cut_from_line", "remove_substring", "regex",
    ]
    value: str = Field(..., min_length=1, max_length=2000)


class CleaningGroupInfo(BaseModel):
    group_key: str
    pages: int
    cleaned_pages: int
    avg_content_length: int
    sample_urls: list[str]


class CleaningPreviewRequest(BaseModel):
    group_key: str | None = None
    url_regex: str | None = Field(default=None, max_length=500)
    rules: list[CleaningRule] = Field(..., min_length=1, max_length=50)
    targets: Literal["text", "markdown", "both"] = "both"
    sample_size: int = Field(default=5, ge=1, le=20)


class CleaningPreviewSample(BaseModel):
    url: str
    before_excerpt: str
    after_excerpt: str
    chars_removed: int
    removed_pct: float


class CleaningPreviewResponse(BaseModel):
    samples: list[CleaningPreviewSample]
    total_chars_removed: int
    pages_skipped_safety: int


class CleaningApplyRequest(BaseModel):
    group_key: str | None = None
    url_regex: str | None = Field(default=None, max_length=500)
    rules: list[CleaningRule] = Field(..., min_length=1, max_length=50)
    targets: Literal["text", "markdown", "both"] = "both"


class CleaningRevertRequest(BaseModel):
    group_key: str | None = None
    url_regex: str | None = Field(default=None, max_length=500)


class CleaningApplyResponse(BaseModel):
    ruleset_id: int | None
    pages_updated: int
    pages_skipped_safety: int
    total_chars_removed: int


class CleaningRulesetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: uuid.UUID
    group_key: str | None
    url_regex: str | None
    rules: list
    targets: str
    pages_updated: int
    pages_skipped: int
    chars_removed: int
    created_at: datetime


# ---------------------------------------------------------------------------
# Analisis enriquecido (#40, decision 85)
# ---------------------------------------------------------------------------
class AlcanceEnriquecido(BaseModel):
    """Sobre que URLs. Vacio = todas las HTML internas, 200 e indexables.

    `extra="forbid"`: una clave mal escrita (`max_url`) seria un alcance que
    se ignora en silencio, y aqui eso es pagar por todo el sitio en vez de
    por la muestra que se pidio (decision 33)."""

    model_config = ConfigDict(extra="forbid")

    carpeta: str | None = Field(default=None, max_length=512)
    max_urls: int | None = Field(default=None, ge=1, le=1_000_000)
    muestra: bool = False


class EnriquecidoEstimar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modulos: list[str] = Field(..., min_length=1, max_length=50)
    alcance: AlcanceEnriquecido = Field(default_factory=AlcanceEnriquecido)


class EnriquecidoCrear(EnriquecidoEstimar):
    # Obligatorio: lanzar sin tope de ejecucion seria dejar todo en manos del
    # mensual, y el dialogo siempre propone uno.
    presupuesto_usd: float = Field(..., ge=0, le=10_000)


class EnrichmentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    modulos: list[str]
    alcance: dict[str, Any]
    estimacion: dict[str, Any] | None = None
    presupuesto_usd: float | None = None
    gastado_usd: float = 0.0
    estado: str
    motivo: str | None = None
    progreso: dict[str, Any] | None = None
    errores: list[str] | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latido_at: datetime | None = None
    # Distribucion de respuestas por pregunta, para la pestaña.
    resultados: dict[str, Any] | None = None
