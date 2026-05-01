# SEO Crawler - CLAUDE.md

## Project Overview

A distributed SEO crawler (similar to Screaming Frog) built with **FastAPI + Scrapy + PostgreSQL + Redis**. Designed for large-scale website audits with real-time progress tracking, comprehensive SEO analysis, and streaming CSV export.

## Architecture

```
┌─────────┐    ┌───────┐    ┌──────────┐    ┌──────────┐
│ Frontend │───▶│  API  │───▶│  Redis   │◀───│  Worker  │
│ (static) │    │FastAPI│    │ (queue)  │    │ (Scrapy) │
└─────────┘    └───┬───┘    └──────────┘    └────┬─────┘
                   │                              │
                   └──────────┐  ┌────────────────┘
                              ▼  ▼
                         ┌──────────┐
                         │PostgreSQL│
                         └──────────┘
```

### Components

| Component | Path | Technology | Description |
|-----------|------|------------|-------------|
| **API** | `api/` | FastAPI | Job CRUD, results, CSV export, real-time progress via Redis |
| **Crawler** | `crawler/` | Scrapy + Playwright | SEO spider + Redis queue worker. Each crawl runs as **subprocess** |
| **Analysis** | `analysis/` | SQLAlchemy 2.0 | Post-crawl SEO analysis (15 check types). Triggered automatically by worker |
| **Shared** | `shared/` | SQLAlchemy | Models, DB config, constants. Shared across all components |
| **Frontend** | `frontend/` | Alpine.js | Lightweight static SPA (vanilla JS). Served by FastAPI |
| **Scripts** | `scripts/` | Python | DB initialization (`init_db.py`) |

### Docker Services (docker-compose.yml)
- **postgres** — PostgreSQL 16 Alpine, port 5432, healthcheck via `pg_isready`
- **redis** — Redis 7 Alpine, port 6379, healthcheck via `redis-cli ping`
- **api** — FastAPI app, port 8000, depends on postgres + redis
- **crawler** — Scrapy worker, 1 replica by default (local), resource-limited (2GB RAM, 2 CPUs)

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
| `PLAYWRIGHT_MAX_PAGES` | 4 | Max browser tabs open simultaneously per context |

**Important:** These caps only apply to jobs with `render_js=true`. Jobs without JS rendering use the standard `CONCURRENT_REQUESTS=32` and are not resource-constrained by Playwright.

### Scaling crawlers at runtime

```bash
# Scale up (e.g., for a big crawl on a VPS)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --scale crawler=4

# Scale back down
docker compose up -d --scale crawler=1
```

## Key Commands

```bash
# Start all services (local)
docker compose up -d

# Start all services (production VPS)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Rebuild after code changes
docker compose up -d --build

# Init database tables
docker compose exec api python scripts/init_db.py

# View logs
docker compose logs -f api
docker compose logs -f crawler

# Scale crawlers
docker compose up -d --scale crawler=4

# Direct DB access
docker exec -it crawlermasivo-postgres-1 psql -U crawler -d crawler_db
```

## File Map

### API (`api/`)
| File | Description |
|------|-------------|
| `main.py` | FastAPI app, lifespan, CORS, static files, SPA fallback |
| `schemas.py` | Pydantic v2 request/response models |
| `dependencies.py` | Shared Redis client + DB session dependency |
| `routers/jobs.py` | Job CRUD endpoints (POST, GET, PATCH cancel, DELETE) |
| `routers/results.py` | Results endpoints (urls, issues, links, stats, export CSV) |

### Crawler (`crawler/`)
| File | Description |
|------|-------------|
| `worker.py` | Redis queue consumer. Runs Scrapy as **subprocess** per job |
| `seo_crawler/spiders/seo_spider.py` | Main SEO spider — response handling, link following, all extraction |
| `seo_crawler/extractors.py` | **Pure functions** (no Scrapy imports) — all HTML extraction logic |
| `seo_crawler/pipelines.py` | PostgreSQL pipeline — upsert pages, batch-insert child items |
| `seo_crawler/items.py` | Scrapy Item definitions (PageItem, HtmlMetaItem, LinkItem, etc.) |
| `seo_crawler/middlewares.py` | UA rotation, proxy, job config, HTTP config middlewares |
| `seo_crawler/settings.py` | Scrapy settings (BFS, autothrottle, Playwright, pipeline) |

### Analysis (`analysis/`)
| File | Description |
|------|-------------|
| `analyzer.py` | `SEOAnalyzer` class with 15 check methods + `run_analysis()` entry point |

### Shared (`shared/`)
| File | Description |
|------|-------------|
| `models.py` | SQLAlchemy models: Job, Url (40+ fields), HtmlMeta, Heading, Link, Hreflang, StructuredData, Resource, PageContent, SecurityHeaders, Issue |
| `database.py` | Engine + SessionLocal factory |
| `config.py` | Env vars + SEO thresholds (title/description min/max lengths) |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/jobs` | Create crawl job |
| GET | `/api/jobs` | List jobs (`?status=`, `?client_id=`, `?page=`) |
| GET | `/api/jobs/{id}` | Get job details |
| PATCH | `/api/jobs/{id}/cancel` | Cancel job |
| DELETE | `/api/jobs/{id}` | Delete job + cascade |
| GET | `/api/jobs/{id}/progress` | Real-time progress from Redis |
| GET | `/api/jobs/{id}/urls` | Crawled URLs (`?status_group=`, `?is_internal=`, `?resource_type=`) |
| GET | `/api/jobs/{id}/issues` | SEO issues (`?severity=`, `?issue_type=`) |
| GET | `/api/jobs/{id}/links` | Link graph |
| GET | `/api/jobs/{id}/stats` | Aggregated stats |
| GET | `/api/jobs/{id}/export` | CSV export (streaming, 1000-row windows) |

## Database (PostgreSQL)

11 tables. Full schema documented in **`SEO_CRAWLER_DB.md`** (connection strings, all columns, example queries).

| Table | Relation | Key Fields |
|-------|----------|------------|
| `jobs` | root | id (UUID), name, status, seeds (JSON), config (JSON) |
| `urls` | 1:N from jobs | url, status_code, is_html, pagerank, word_count, indexability_status |
| `html_meta` | 1:1 with urls | title, meta_description, canonical_href, og_*, twitter_* |
| `headings` | 1:N from urls | tag (h1-h6), position, text |
| `links` | N:N graph | from_url_id, to_url, anchor_text, rel, link_position, follow |
| `page_content` | 1:1 with urls | content_text, content_markdown |
| `hreflang` | 1:N from urls | lang, href |
| `structured_data` | 1:N from urls | raw (JSON), format, schema_type |
| `resources` | 1:N from urls | resource_url, resource_type, alt_text |
| `security_headers` | 1:1 with urls | is_https, has_hsts, has_csp, has_mixed_content |
| `issues` | 1:N from urls | issue_type, severity, details (JSON) |

URL dedup: SHA-256 hash per `(job_id, url_hash)` unique constraint.

## Extraction Functions (`extractors.py`)

All pure functions — no Scrapy imports. First candidates for unit tests.

| Function | Returns |
|----------|---------|
| `extract_meta(selector)` | dict — title, description, canonical, OG, Twitter, robots |
| `extract_headings(selector)` | list[dict] — tag, position, text (excludes template/noscript/svg) |
| `extract_links(selector, base_url, hosts)` | list[dict] — to_url, anchor, rel, position, follow, type |
| `extract_hreflang(selector)` | list[dict] — lang, href |
| `extract_structured_data(html, url)` | list[dict] — raw, format (jsonld/microdata/rdfa), schema_type |
| `extract_resources(selector, base_url)` | list[dict] — url, type, alt, width, height, mixed_content |
| `extract_word_count(selector)` | int |
| `extract_visible_text(selector)` | str |
| `extract_main_content(selector)` | str or None — boilerplate-free text |
| `extract_main_content_markdown(selector)` | str or None — content as Markdown |
| `extract_meta_refresh(selector)` | str or None |
| `extract_security_headers(headers)` | dict — HTTPS, HSTS, CSP, X-Frame, etc. |

## SEO Analysis Checks (`analyzer.py`)

The `SEOAnalyzer` class runs 15 check methods and populates the `issues` table:

| Method | Issue Types Detected |
|--------|---------------------|
| `analyze_status_codes()` | status_4xx, status_5xx |
| `analyze_titles()` | missing_title, title_too_short, title_too_long, duplicate_title |
| `analyze_descriptions()` | missing_description, description_too_short, description_too_long, duplicate_description |
| `analyze_headings()` | missing_h1, multiple_h1 |
| `analyze_canonicals()` | canonical issues |
| `analyze_hreflang()` | hreflang return tags, invalid langs |
| `analyze_structured_data()` | structured data validation |
| `analyze_indexability()` | indexability status |
| `analyze_duplicates()` | content duplicates |
| `analyze_redirect_chains()` | redirect_chain |
| `analyze_images()` | image_missing_alt |
| `analyze_security()` | http_url, mixed_content, missing_hsts, missing_csp |
| `analyze_content()` | low_word_count, low_text_ratio |
| `analyze_url_issues()` | url_too_long, url_non_ascii, url_uppercase, url_underscores, url_multiple_slashes, url_has_parameters, url_non_seo_friendly, url_cms_faceted, orphan_page, high_outlink_count |
| `analyze_links()` | link graph metrics (inlinks, outlinks, pagerank) |

Configurable thresholds via `job.config.analysis_thresholds` JSON or module-level constants.

## Scrapy Settings

| Setting | Value | Notes |
|---------|-------|-------|
| `CONCURRENT_REQUESTS` | 32 | Env-overridable |
| `CONCURRENT_REQUESTS_PER_DOMAIN` | 8 | Env-overridable |
| `DOWNLOAD_TIMEOUT` | 30s | |
| `RETRY_TIMES` | 2 | Only for 502, 503, 504, 408, 429 |
| `ROBOTSTXT_OBEY` | True | Overridable per-job |
| `AUTOTHROTTLE_ENABLED` | True | Target concurrency: 8.0 |
| `HTTPERROR_ALLOW_ALL` | True | All status codes reach spider (Screaming Frog parity) |
| `DEPTH_PRIORITY` | 1 | BFS scheduling |
| `PIPELINE_BATCH_SIZE` | 200 | Child items buffered then bulk-inserted |
| Playwright | chromium, headless | JS rendering via `scrapy-playwright` |
| `PLAYWRIGHT_MAX_PAGES_PER_CONTEXT` | 4 | Env-overridable (`PLAYWRIGHT_MAX_PAGES`) |
| JS auto-cap | 8 / 4 | When `render_js=true`, worker auto-caps concurrency (env-overridable) |

## Critical Design Decisions

1. **Subprocess per crawl** — Worker runs `python -m scrapy crawl seo` as subprocess. Do NOT try to run Scrapy in-process; Twisted reactor cannot restart.
2. **Settings via CLI flags** — Per-job Scrapy settings passed via `-s` flags. `custom_settings` on spider class is NOT used (too late for Scrapy to read).
3. **Cancel via Redis** — `job:{id}:cancel` key checked every response. Progress via `job:{id}:crawled_count`.
4. **Batched pipeline** — Parent items (page, html_meta) upserted individually. Child items (links, headings) buffered and bulk-inserted every 200 items. DELETE-before-INSERT prevents duplicates on re-crawl.
5. **Streaming CSV** — New DB session per 1000-row window to avoid long transactions.
6. **Headings dedup** — `extract_headings` skips headings inside `<template>`, `<noscript>`, `<svg>` to avoid SSR/framework duplicates.
7. **URL issues as SEO problems** — Junk/malformed URLs are crawled and reported as SEO issues (not filtered), because if a crawler finds them, Google can too.
8. **JS rendering auto-cap** — When a job has `render_js=true`, the worker automatically caps `CONCURRENT_REQUESTS` and `CONCURRENT_REQUESTS_PER_DOMAIN` to lower values (env-configurable) to prevent Chromium memory exhaustion. Jobs without JS are unaffected. See "Deployment: Local vs VPS" section.

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
PLAYWRIGHT_MAX_PAGES=4
```

## SEO Config Thresholds (`shared/config.py`)

| Constant | Value |
|----------|-------|
| `TITLE_MIN_LEN` | 10 |
| `TITLE_MAX_LEN` | 60 |
| `DESCRIPTION_MIN_LEN` | 50 |
| `DESCRIPTION_MAX_LEN` | 160 |

Additional thresholds in `analyzer.py`: `LOW_WORD_COUNT_THRESHOLD=200`, `LOW_TEXT_RATIO_THRESHOLD=10.0`, `URL_MAX_LENGTH=115`, `HIGH_OUTLINK_THRESHOLD=100`.

## Code Conventions

- All files use `from __future__ import annotations`
- Pydantic v2 with `model_config = ConfigDict(from_attributes=True)`
- SQLAlchemy 2.0 `select()` style in analyzer, ORM query style in API
- Extractors (`extractors.py`) are pure functions — no Scrapy imports
- Scrapy Items map 1:1 to SQLAlchemy models
- No authentication — CORS wide open (`allow_origins=["*"]`)

## Scrapy Item Types

| Item Class | DB Table | Relation |
|------------|----------|----------|
| `PageItem` | `urls` | One per response |
| `HtmlMetaItem` | `html_meta` | 1:1 with urls |
| `HeadingItem` | `headings` | N per url |
| `LinkItem` | `links` | N per url |
| `HreflangItem` | `hreflang` | N per url |
| `StructuredDataItem` | `structured_data` | N per url |
| `ResourceItem` | `resources` | N per url |
| `ContentItem` | `page_content` | 1:1 with urls |
| `SecurityItem` | `security_headers` | 1:1 with urls |

## Middleware Stack

| Middleware | Priority | Purpose |
|-----------|----------|---------|
| `JobConfigMiddleware` | 100 | Injects per-job settings from Redis/DB |
| `UserAgentMiddleware` | 400 | UA rotation (replaces Scrapy default) |
| `ProxyMiddleware` | 410 | Optional proxy rotation from `PROXY_LIST` |
| `HttpConfigMiddleware` | 420 | Per-job HTTP overrides |

## Reference Documents

These markdown files are available in the project root for consultation:

| File | Description |
|------|-------------|
| **`SEO_CRAWLER_DB.md`** | Complete database schema, connection strings, example SQL queries, relationship diagram. Use this for DB access from external tools. |
| **`modelo-de-datos.md`** | Original data model design — tables, calculations, Screaming Frog tab mapping. Design spec that guided implementation. |
| **`stack-de-ingenieria.md`** | Engineering stack design doc — architecture decisions, component breakdown, scaling strategy, monitoring plan. |
| **`millones-de-URL.md`** | Guide for scaling to millions of URLs — distributed crawling patterns (scrapy-redis, Scrapy Cluster), memory management, BFS tuning. |
| **`screaming_frog_complete_fields_reference.md`** | Complete Screaming Frog fields reference (900+ lines) — all tabs, columns, filters, bulk exports. Feature parity checklist. |

## Testing

No tests exist yet. Extractors are pure functions and should be the first to get unit tests. Test directory exists at `tests/`.

## What Does NOT Exist Yet

- Authentication/authorization
- CI/CD pipeline
- Monitoring/metrics (Prometheus, Grafana)
- Sitemap ingestion
- PageSpeed/CrUX integration
- Near-duplicate content detection (simhash)
- Hreflang return-tag validation
- Structured data rich-result validation
