# Despliegue, entorno y umbrales

> Movido tal cual desde `CLAUDE.md` (2026-10-14).

## Deployment: Local vs VPS (Production)

The crawler uses **Playwright (headless Chromium)** for JS rendering, which is very resource-intensive. The project ships with two Docker Compose configs:

- **`docker-compose.yml`** — Conservative defaults for **local development** (your PC)
- **`docker-compose.prod.yml`** — Higher limits for **VPS / production servers**

### Local (your PC)

```bash
docker compose up -d
```

Default crawler limits: 1 replica, 2GB RAM, 2 CPUs, 8 concurrent JS requests, 4 max browser tabs.

### VPS / Production

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Production crawler limits: 2 replicas, 4GB RAM, 4 CPUs, 16 concurrent JS requests, 8 max browser tabs.

Adjust `docker-compose.prod.yml` values based on your VPS specs. Examples:

| VPS RAM | Replicas | Memory limit | `JS_CONCURRENT_REQUESTS` | `PLAYWRIGHT_MAX_PAGES` |
|---------|----------|--------------|--------------------------|------------------------|
| 4 GB    | 1        | 2g           | 8                        | 4                      |
| 8 GB    | 2        | 4g           | 16                       | 8                      |
| 16 GB   | 4        | 4g           | 16                       | 8                      |
| 32 GB+  | 4-8      | 8g           | 32                       | 16                     |

### JS Rendering Resource Variables

These env vars control Playwright/Chromium resource usage. Set them in `.env` or in `docker-compose.prod.yml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `JS_CONCURRENT_REQUESTS` | 8 | Max concurrent Scrapy requests when `render_js=true` (auto-cap, ignored if job sets `concurrent_requests`) |
| `JS_CONCURRENT_PER_DOMAIN` | 4 | Max concurrent requests per domain when `render_js=true` |
| `PLAYWRIGHT_MAX_PAGES` | 8 | Max browser tabs open simultaneously per context |

**Important:** These caps only apply to jobs with `render_js=true`. Jobs without JS rendering use the standard `CONCURRENT_REQUESTS=32` and are not resource-constrained by Playwright.

### Scaling crawlers at runtime

```bash
# Scale up (e.g., for a big crawl on a VPS)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --scale crawler=4

# Scale back down
docker compose up -d --scale crawler=1
```

## Environment Variables

See `.env.example`:

```
POSTGRES_USER=crawler
POSTGRES_PASSWORD=crawler
POSTGRES_DB=crawler_db
DATABASE_URL=postgresql+psycopg2://crawler:crawler@postgres:5432/crawler_db
REDIS_URL=redis://redis:6379/0
DEFAULT_MAX_DEPTH=3
DEFAULT_MAX_URLS=50000
DEFAULT_CONCURRENT_REQUESTS=32
DEFAULT_CONCURRENT_REQUESTS_PER_DOMAIN=8
DEFAULT_USER_AGENT=SEOCrawler/1.0
API_HOST=0.0.0.0
API_PORT=8000

# Playwright / JS rendering resource caps (only apply when render_js=true)
JS_CONCURRENT_REQUESTS=8
JS_CONCURRENT_PER_DOMAIN=4
PLAYWRIGHT_MAX_PAGES=8

# Render: espera y bloqueo de terceros
PLAYWRIGHT_BANNER_WAIT_MS=2000   # tope de la espera a que el DOM se calme
PLAYWRIGHT_DOM_QUIET_MS=400      # cuanto DOM quieto se considera "ha terminado"
PLAYWRIGHT_MIN_WAIT_MS=600       # piso: nunca se da por terminada antes de esto
PLAYWRIGHT_BLOCK_TRACKERS=1      # 0 = cargar analitica y publicidad

# Avisos fuera de la interfaz (solo alertas CRITICAS de la comparacion)
ALERTA_WEBHOOK_URL=              # vacio = no se avisa
ALERTA_TIMEOUT_SEGUNDOS=10
API_PUBLIC_URL=                  # para el enlace del aviso
REANALIZAR_LA_REFERENCIA=1       # re-analizar el censo anterior si lo analizo otro codigo (decision 93)

# Programaciones
PROGRAMACION_INTERVAL_SECONDS=60 # cada cuanto mira el worker si toca lanzar
PROGRAMACION_GRACIA_HORAS=12     # cuanto puede llegar tarde un disparo perdido
PROGRAMACION_ZONA=Europe/Madrid

# Worker
STALL_AUTO_RESUME=3              # reanudaciones automaticas tras estancamiento (0 = ninguna)
# Por job (crawl_behavior): render_wait_ms sobreescribe PLAYWRIGHT_BANNER_WAIT_MS;
# recrawl_patterns (regex) hace que un resume repita esas URLs; retry_failed_on_resume.
SCRAPY_LOG_DIR=/tmp/scrapy-logs  # log de Scrapy por job, en vivo

# Sitemaps
MAX_SITEMAP_FILES=500            # cuantos ficheros de sitemap se siguen
MAX_URLS_PER_SITEMAP=50000       # URLs por fichero
MAX_SITEMAP_BYTES=52428800       # tope del XML YA DESCOMPRIMIDO (bomba gzip)

# robots.txt
ROBOTS_CRAWL_DELAY_MAX=10        # tope del Crawl-delay que se respeta (decision 81)

# Capa de decisiones: Jev por OpenRouter (decision 84). Sin clave o con
# DECISIONS_ENABLED=0 no se llama a nadie; con las dos, tampoco solo (#40).
DECISIONS_ENABLED=0
OPENROUTER_API_KEY=              # en el entorno, NUNCA en la BD
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_APP_URL=              # HTTP-Referer
OPENROUTER_APP_TITLE=SEO Crawler # X-OpenRouter-Title
LLM_RPM=600                      # el limite de Jev via OpenRouter no esta publicado
LLM_MAX_CONCURRENCIA=16
LLM_TIMEOUT_S=15
LLM_CACHE_TTL_D=30               # Redis; la cache de BD no caduca
LLM_ZDR=0                        # 1 = exigir retencion cero
JEV_PRECIO_USD_MTOK=0.042        # solo reserva y respaldo si falta usage.cost
ENRICHMENT_MAX_USD_MES=          # tope global mensual; vacio = sin tope

# Analisis enriquecido (decision 85)
ENRICHMENT_TOKEN=                # API: sin el (o <16 caracteres) lanzar esta DESACTIVADO
MAX_CONCURRENT_ENRIQUECIDOS=1    # worker: pool propio, no quita huecos a los rastreos
ENRICHMENT_LOTE=200              # URLs por lote (entre lotes: cancelar, progreso, latido)
ENRICHMENT_STALE_MINUTES=15      # sin latido -> se reencola

# Enlaces archivados en Parquet (decision 95)
ARCHIVO_DIR=/data/archivo        # volumen `archivo`, compartido por worker y API
ARCHIVAR_ENLACES=1               # 0 = el worker no archiva tras cada rastreo
ARCHIVAR_DIAS_MINIMOS=14         # no se archiva nada que termino hace menos
```

## SEO Config Thresholds (`shared/config.py`)

| Constant | Value |
|----------|-------|
| `TITLE_MIN_LEN` | 10 |
| `TITLE_MAX_LEN` | 60 (solo si no hay ancho en pixeles medido) |
| `TITLE_MAX_PIXELS` | 580 |
| `DESCRIPTION_MAX_PIXELS` | 985 |
| `DESCRIPTION_MIN_LEN` | 50 |
| `DESCRIPTION_MAX_LEN` | 160 |

Umbral de casi duplicados: `job.config.analysis_thresholds.near_duplicate_similarity`
(por defecto 0.9; ver decisiones 16-18).

Additional thresholds in `analyzer.py`: `LOW_WORD_COUNT_THRESHOLD=200`, `URL_MAX_LENGTH=115`, `HIGH_OUTLINK_THRESHOLD=100`.

## CI y despliegue

`.github/workflows/tests.yml` corre la suite en cada PR y en cada push a
`master`, en dos trabajos porque las dependencias no son las mismas: el del
crawler instala `crawler/` + `analysis/` (los tests del spider lanzan el
`SeoSpider` real contra un sitio local, necesitan scrapy de verdad; Playwright
se instala pero no se baja el navegador) y el de la API solo FastAPI,
SQLAlchemy y Pydantic (`api/requirements.txt` entero compila umap-learn y
hdbscan: minutos por nada). El trabajo del crawler levanta un Postgres de
servicio, que destapa los tests marcados que sin `PAGERANK_TEST_DATABASE_URL`
se saltaban SIEMPRE.

`deploy.yml` **depende** de ese workflow (`needs: tests`): si la suite falla,
el VPS no se toca. Antes no era asi — el 5-oct-2026 un merge desplego a
produccion sin ejecutar un solo test.

Lo que dispara despliegue es el filtro de `paths` de `deploy.yml`:
`crawler/**`, `api/**`, `analysis/**`, `shared/**`, `frontend/**`,
`scripts/**`, los `docker-compose*.yml` y el propio workflow. `docs/**` y los
`.md` de la raiz **no** despliegan.
