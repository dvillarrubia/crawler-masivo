# SEO Crawler - CLAUDE.md

## Project Overview
A distributed SEO crawler (similar to Screaming Frog) built with FastAPI + Scrapy + PostgreSQL + Redis. Designed for large-scale website audits with real-time progress tracking and comprehensive SEO analysis.

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
- **`api/`** — FastAPI REST API. Job CRUD, results, CSV export, real-time progress via Redis.
- **`crawler/`** — Scrapy spider + Redis queue worker. Each crawl runs as a **subprocess** (avoids Twisted reactor restart). Worker consumes from Redis `jobs:pending` queue.
- **`analysis/`** — Post-crawl SEO analysis (14 check types). Triggered automatically by worker after crawl completes.
- **`shared/`** — SQLAlchemy models, DB config, constants. Shared across all components.
- **`frontend/`** — Lightweight static SPA (vanilla JS + Alpine.js). Served by FastAPI.

## Key Commands

```bash
# Start all services
docker-compose up -d

# Rebuild after code changes
docker-compose up -d --build

# Init database tables
docker-compose exec api python scripts/init_db.py

# View logs
docker-compose logs -f api
docker-compose logs -f crawler

# Scale crawlers
docker-compose up -d --scale crawler=4
```

## Database (PostgreSQL)
9 tables: `jobs`, `urls`, `html_meta`, `headings`, `links`, `hreflang`, `structured_data`, `resources`, `issues`, `security_headers`.

- URL dedup: SHA-256 hash per `(job_id, url_hash)` unique constraint
- All IDs are BigInteger except `jobs.id` which is UUID

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/jobs` | Create crawl job |
| GET | `/api/jobs` | List jobs (`?status=`, `?client_id=`, `?page=`) |
| GET | `/api/jobs/{id}` | Get job details |
| PATCH | `/api/jobs/{id}/cancel` | Cancel job |
| DELETE | `/api/jobs/{id}` | Delete job + cascade |
| GET | `/api/jobs/{id}/progress` | Real-time progress (Redis) |
| GET | `/api/jobs/{id}/urls` | Crawled URLs (`?status_group=`, `?is_internal=`, `?resource_type=`) |
| GET | `/api/jobs/{id}/issues` | SEO issues (`?severity=`, `?issue_type=`) |
| GET | `/api/jobs/{id}/links` | Link graph |
| GET | `/api/jobs/{id}/stats` | Aggregated stats |
| GET | `/api/jobs/{id}/export` | CSV export (streaming) |

## Critical Design Decisions
1. **Subprocess per crawl** — Worker runs `python -m scrapy crawl seo` as subprocess. Do NOT try to run Scrapy in-process; Twisted reactor cannot restart.
2. **Settings via CLI flags** — Per-job Scrapy settings passed via `-s` flags. `custom_settings` on spider class is NOT used (too late for Scrapy).
3. **Cancel via Redis** — `job:{id}:cancel` key checked every response. Progress via `job:{id}:crawled_count`.
4. **Batched pipeline** — Parent items (page, html_meta) upserted individually. Child items (links, headings) buffered and bulk-inserted every 200 items.
5. **Streaming CSV** — New DB session per 1000-row window to avoid long transactions.

## Code Conventions
- All files use `from __future__ import annotations`
- Pydantic v2 with `model_config = ConfigDict(from_attributes=True)`
- SQLAlchemy 2.0 `select()` style in analyzer, ORM query style in API
- Extractors (`crawler/seo_crawler/extractors.py`) are pure functions — no Scrapy imports
- No authentication yet — CORS is wide open

## Environment Variables
See `.env.example`. Key vars: `DATABASE_URL`, `REDIS_URL`, `DEFAULT_MAX_DEPTH`, `DEFAULT_MAX_URLS`, `DEFAULT_CONCURRENT_REQUESTS`.

## Testing
No tests exist yet. Extractors are pure functions and should be the first to get unit tests.

## What Does NOT Exist Yet
- Authentication/authorization
- CI/CD pipeline
- Monitoring/metrics (Prometheus, Grafana)
- Sitemap ingestion
- PageSpeed integration
