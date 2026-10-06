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
| **Analysis** | `analysis/` | SQLAlchemy 2.0 | Post-crawl SEO analysis (17 check types). Triggered automatically by worker |
| **Shared** | `shared/` | SQLAlchemy | Models, DB config, constants. Shared across all components |
| **Frontend** | `frontend/` | Alpine.js | Lightweight static SPA (vanilla JS). Served by FastAPI. Light theme (`style.css`) is the default; `theme-terminal.css` (green-phosphor console) is opt-in; toggle in the top bar, stored in `localStorage['seo-crawler-theme']` |
| **Scripts** | `scripts/` | Python | DB init (`init_db.py`), verificación (`check_content_quality.py`, `check_js_templates.py`) y reparaciones sobre censos ya hechos (`fix_h1_en_contenido.py`, `near_duplicates.py`) |

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
| `PLAYWRIGHT_MAX_PAGES` | 8 | Max browser tabs open simultaneously per context |

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
| `analyzer.py` | `SEOAnalyzer` class with 17 check methods + `run_analysis()` entry point |
| `near_duplicates.py` | Contenido casi duplicado — MinHash + LSH. Funciones puras sobre texto |
| `sd_validation.py` | Validación conservadora de datos estructurados |

### Shared (`shared/`)
| File | Description |
|------|-------------|
| `models.py` | SQLAlchemy models: Job, Url (40+ fields), HtmlMeta, Heading, Link, Hreflang, StructuredData, Resource, PageContent, SecurityHeaders, Issue |
| `database.py` | Engine + SessionLocal factory |
| `config.py` | Env vars + SEO thresholds (title/description min/max lengths) |
| `robots.py` | Lectura de directivas robots — tokenizado y detección de separadores inválidos. En `shared/` porque el analyzer y el extractor deben leerlas igual |

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
| GET | `/api/jobs/{id}/urls/{url_id}/raw-html` | HTML guardado de una URL (solo con `extraction.store_raw_html`). Endpoint aparte y no un campo del detalle: 170 kB de media por pagina |
| GET | `/api/jobs/{id}/export` | CSV export (streaming, 1000-row windows) — 75 columnas: URL + metadatos + **h1/h2**, og/twitter, hreflang, tipos de datos estructurados, imagenes sin alt y cabeceras de seguridad, agregados por lote (4 consultas por ventana, no 4 por URL). `content_text_first_500` dice en el nombre que va recortado; el texto entero es `/content/export` |

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

The `SEOAnalyzer` class runs 17 check methods and populates the `issues` table:

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
| `analyze_robots_syntax()` | robots_invalid_syntax (directivas pegadas con `/`, `\|` o `;`) |
| `analyze_duplicates()` | content duplicates (byte-idénticos) |
| `analyze_near_duplicates()` | near_duplicate_content (MinHash, umbral 0.9 configurable) |
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
| `PLAYWRIGHT_MAX_PAGES_PER_CONTEXT` | 8 | Env-overridable (`PLAYWRIGHT_MAX_PAGES`) |
| `PLAYWRIGHT_MAX_CONTEXTS` | 3 | Safety cap on browser contexts (env-overridable) |
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
9. **Landmark-aware content extraction** — `_strip_boilerplate_html` removes
   `<header>`/`<footer>` only when they are the page banner/contentinfo (not
   nested in `main`/`article`/`section`, or explicit ARIA role). A hero
   `<header>` inside `<main>` (Astro/Next/Nuxt pattern) is content; stripping
   it blindly reduced whole pages to two words. Node removal keeps tail text.
   The trafilatura-vs-fallback decision compares against the words in the
   stripped main container (not body `word_count`), threshold 0.7; the
   fallback flattens block by block and collapses marquee/animation repeats.
10. **Hero outside `<main>` is still content** — some landings put the `<h1>`
   and the main claim in a `<section>` that is a *sibling* of `<main>` (often
   wrapped in `<figure><figcaption>`). trafilatura and the fallback both stay
   inside the chosen container, so that block used to vanish from
   `page_content` — the page's headline and commercial promise, on exactly the
   most important pages. `_hero_outside_container` recovers it when the `<h1>`
   is outside the container, prepending only the lines not already present. A
   `<figcaption>` containing headings is unwrapped to a `<div>`: it is a hero,
   not a photo caption.
10b. **El hero tambien se pierde estando DENTRO del contenedor** — el caso
   contrario al anterior y mas comun: `<main><article><div class=hero><h1>`.
   trafilatura lo descarta por su pinta de cabecera mientras conserva el resto
   del articulo, asi que la comprobacion de share no salta (0.75 > 0.7) y el
   titular desaparece sin aviso. `_hero_dentro_perdido` sube desde el `<h1>`
   por los ancestros mientras el bloque siga siendo una fraccion pequena del
   contenedor (30% / 60 palabras), para arrastrar la categoria o el subtitulo
   del hero pero nunca el articulo. El titular se da por presente solo si
   aparece como LINEA propia: como subcadena da falsos positivos (la marca
   reaparece a media frase). Medido: 3/20 paginas de control de otros clientes
   cambian, y en las tres el cambio es recuperar el h1 que faltaba.

12. **La espera de render va por el DOM, no por reloj** — antes eran 2 s
   fijos por pagina, y eran la mayor parte del tiempo de render: en el censo
   del 2026-09-10, 44 de los 62 minutos se fueron esperando a paginas que ya
   habian terminado. Ahora `_JS_ESPERAR_DOM_QUIETO` espera a que el DOM lleve
   400 ms sin mutar, con tope en los mismos 2000 ms (el peor caso es lo que
   costaba antes). 30% menos de tiempo con el mismo texto y los mismos
   enlaces en 8 plantillas de 5 sitios. Va como `evaluate` y no como
   `wait_for_load_state("networkidle")` porque un PageMethod no puede capturar
   excepciones: un sitio cuya red no calla nunca se caeria por timeout. La
   promesa siempre resuelve.

12b. **Mirar solo el DOM tiene un punto ciego: la peticion EN VUELO** —
   mientras un XHR viaja no hay mutaciones, asi que el DOM parecia "quieto" y
   la espera resolvia antes de que llegase nada. No se ve en los datos: la
   pagina se guarda con 200, titulo y texto, solo que sin el modulo que monta
   ese XHR. Medido en www.uoc.edu: de 9.895 noticias, 6.555 quedaron con 5-6
   bloques de datos estructurados y solo 1.965 con los 7 de la pagina montada
   — faltaba el de relacionadas y con el ~20 enlaces y ~20% del texto. Que
   unas si y otras no, en el MISMO rastreo, es la firma de una carrera, no de
   un cambio del sitio. Ahora `seo_crawler/render.py` instala un contador de
   fetch/XHR en vuelo con `add_init_script` (antes de navegar, para ver
   tambien la primera peticion) y la espera solo resuelve con ese contador a
   cero, ademas de reiniciarse con cada recurso que termina
   (`PerformanceObserver`) y de un piso de `PLAYWRIGHT_MIN_WAIT_MS`. El tope
   sigue acotandolo todo. Comprobable: `scripts/prueba_espera_render.py`
   (laboratorio con XHR retardado: con 700 ms la espera vieja se dejaba los 10
   enlaces, la nueva los coge). Coste medido en 3 paginas reales: +500/900 ms
   por pagina. Si una plantilla necesita mas que el tope, `render_wait_ms`.

13. **Los sitios varian solos: medir una vez no es medir** — al bajar la
   espera, cst.gov.sa parecia perder enlaces; repitiendo 5 veces por modo se
   vio que varia solo (90/99/90/90/90 con la espera de SIEMPRE). Y una pagina
   del canario cayo de 156 a 52 enlaces: con espera fija y con 5 s daba
   tambien 52 — habia cambiado el sitio. Antes de atribuir una diferencia a un
   cambio del crawler, repetirla contra el estado ACTUAL del sitio con la
   configuracion vieja.

11. **Browser TLS impersonation must be requested per-request** —
   `scrapy-impersonate` only routes through curl_cffi when the request carries
   `meta["impersonate"]`; the composite handler sets it for every non-Playwright
   request. Without it everything silently falls back to Twisted's TLS and a
   WAF like F5 BIG-IP ASM answers with binary garbage, ending the job with 1
   URL and no error that looks like a block.

14. **Los métodos de página de Playwright toleran una navegación** —
   `PageMethod("evaluate", js)` reventaba con "Execution context was destroyed"
   cuando la página redirigía por JS tras `domcontentloaded` (portales legado,
   `index.html` que saltan a la sección). Tras unos cuantos cierres de pestaña
   así, el navegador dejaba de servir páginas y el rastreo seguía "vivo" a 0
   páginas/min. `_evaluar_tolerante` (callable como PageMethod) espera al nuevo
   documento y reintenta. Medido en la UOC: 19 fallos y cuelgue total en 40 min.
15. **Un estancamiento reencola el job, no lo cierra** — el vigilante del worker
   mata el Scrapy que lleva `stall_timeout_minutes` sin latido y lo vuelve a
   encolar hasta `STALL_AUTO_RESUME` veces (def. 3); el spider retoma desde la
   frontera guardada en BD. Solo al agotar los intentos se cierra como `stalled`.
31. **La cola de rastreos es una cola, no una pila** — `rpush` + `brpop`
   trabajan sobre el MISMO extremo de la lista, asi que el ultimo job creado
   adelantaba a todos y lo recuperado tras un reinicio se ponia delante de lo
   que llevaba horas esperando. No se habia notado porque en el uso normal hay
   un job a la vez. El nombre de la cola estaba escrito a mano en cinco
   sitios; ahora el orden y el nombre viven en `shared/cola.py` (`encolar` /
   `siguiente`, FIFO con `rpush` + `blpop`) y el FIFO vale para TODO, tambien
   para reanudaciones y recuperaciones: adelantarlas significaria que un job
   que se atasca y se reencola hasta `STALL_AUTO_RESUME` veces pisa
   indefinidamente a los que esperan.

32. **Un rastreo con cero URLs no es un sitio limpio** — con `Disallow: /` y
   `robots_mode=respect`, Scrapy descarta la semilla con `IgnoreRequest`,
   `handle_error` no registraba nada y Scrapy terminaba con codigo 0: el job
   quedaba `completed` con 0 URLs, que se lee igual que un sitio vacio y en
   orden (y con la comparacion entre censos de #32 declararia desaparecido el
   sitio entero). Ahora es `failed`, y el motivo distingue
   `robots_bloquea_todo` —un hallazgo que se cuenta en una frase— de
   `sin_urls`, que hay que mirar. El spider deja la marca en Redis cuando lo
   prohibido es una SEMILLA.

33. **Pydantic v2 tira en silencio lo que no esta declarado** — el formulario
   enviaba `use_sitemap`, `JobConfig` no lo declaraba y el spider leia
   `job_config.get("use_sitemap", True)`: desmarcar la casilla no tenia NINGUN
   efecto y nadie se enteraba. Le pasa a cualquier clave nueva. Hay un test
   que se mantiene solo (`test_jobconfig_claves.py`): saca por regex las
   claves que lee el spider —hoy 14— y falla si alguna no esta en el schema.

16. **El log de Scrapy va a fichero, en vivo** — `-s LOG_FILE=$SCRAPY_LOG_DIR/<job>.log`
   (def. `/tmp/scrapy-logs`, dentro del contenedor, modo append). Antes solo
   existía en memoria hasta que el proceso acababa. Al matar un rastreo se
   registra un resumen de sus líneas de error en WARNING.

19. **robots.txt también en el destino de una redirección con render** — Chromium
   sigue los 3xx por su cuenta, así que el host de destino nunca pasaba por el
   middleware de robots: se guardaron 22 páginas de un SSO con `Disallow: /`.
   Si el render acaba en otro host no interno, la petición se repite sin
   render (misma ruta que `_sin_render`); y si robots corta una cadena de
   redirecciones, `handle_error` registra los saltos con su código en vez de
   dejar que la URL original desaparezca del informe.

18. **La espera de render se ajusta por cliente** — los listados montados por
   XHR/GraphQL (AEM) pintan sus enlaces 2-3 s después de `domcontentloaded`;
   con el tope de 2 s se guardaban categorías con 0 enlaces a items y los
   huérfanos salían inflados sin aviso. `crawl_behavior.render_wait_ms` fija
   el tope por job y `crawl_behavior.recrawl_patterns` permite repetir solo
   esa plantilla al reanudar. Medir con Playwright a 0/2/3/5/8 s antes de
   afirmar que una página no enlaza algo.

17. **Página de error de Chromium = repetir sin render** — cuando Playwright
   acaba en `chrome-error://`, la respuesta llegaba como un 307 con destino
   `chrome-error://chromewebdata/` y la URL real quedaba sin estado. Pasa tras
   una redirección que Chromium no sigue (http → `https://host:443/...` con el
   puerto explícito). El spider repite la petición sin render (`_sin_render`),
   que deja el código y la cadena de redirecciones reales.

20. **La indexabilidad la decide el analizador, y repara lo ya rastreado** —
   se calculaba dos veces con dos criterios y las dos columnas acababan
   contradiciendose en el mismo CSV. El spider comparaba el canonical contra
   la URL de PARTIDA de la cadena de redirecciones mientras guardaba la de
   destino: toda pagina alcanzada por un 301 salia "Canonicalised" con un
   canonical identico a su propia URL (1.254 de 34.704 en blogs.uoc.edu, 1.329
   en www.uoc.edu; el 100% llegadas por redireccion). Y `indexable` solo
   existia para HTML con metadatos, asi que 404, 3xx y PDFs quedaban en NULL
   en vez de en `false` — el filtro `?indexable=false` los perdia. Ahora el
   spider compara contra la URL final, y `analyze_indexability` escribe
   `indexable` Y `indexability_status` para TODAS las URLs del job,
   respetando los motivos con codigo exacto del rastreo ("Redirect (301)",
   "Client Error (404)"). Re-analizar un job viejo lo corrige sin re-rastrear.

21. **El contador del job contaba solo el ultimo tramo** — cada reanudacion
   abre un proceso de Scrapy nuevo, con su tuberia y su cuenta desde cero, y
   al cerrar la escribia en `jobs.total_urls_crawled`: un rastreo con 34.704
   filas se reportaba como 802 y `/stats` devolvia las dos cifras a la vez. La
   tuberia arranca ahora leyendo lo que ya hay guardado del job y solo suma
   URLs nuevas.

22. **La barra en `meta robots` se avisa, no se interpreta** — hay plantillas
   que escriben `index/follow` y `noindex/nofollow` (69 páginas en el censo de
   Saunier Duval). La sintaxis oficial separa por comas, y Google ignora lo que
   no reconoce, así que ese valor llega como un token desconocido: la página se
   indexa y sus enlaces se siguen. Tokenizar por barra "arreglaría" el parser y
   rompería el informe — marcaríamos como noindex una página que Google sí
   indexa, y el cliente se quedaría creyendo que está fuera del índice. Lo que
   se emite es `robots_invalid_syntax`, y su severidad depende de qué se
   pierde: warning si la directiva ignorada era restrictiva (hay una intención
   que no se cumple), info si era `index/follow` (el comportamiento por
   defecto, no se pierde nada). Encaja con la decisión 7: lo roto se reporta,
   no se filtra.

23. **Las reglas robots viven en `shared/robots.py`, no en el extractor** — el
   analyzer y el extractor las necesitan los dos y la imagen de `analysis/`
   solo copia `shared/` y `analysis/`. Tenerlas por duplicado costó un
   desacuerdo silencioso: el extractor tokenizaba (correcto) mientras el
   analyzer hacía `"noindex" in valor` por subcadena, así que con
   `noindex/nofollow` la misma página salía indexable en `indexability_status`
   y no indexable en `urls.indexable`. Arreglado al unificar: gana el
   tokenizado.

24. **Casi duplicados con MinHash, no con simhash** — la nota antigua decía
   "near-duplicates via simhash", y simhash es efectivamente más barato (un
   entero por página). El problema es que su distancia no se le puede enseñar
   a un cliente: medido sobre un texto de 300 palabras, cambiar 3 da 0,92 de
   "similitud" y cambiar 10 ya da 0,81 — un informe que dice "estas dos se
   parecen un 81%" cuando comparten el 97% del texto no se sostiene en una
   reunión. La firma MinHash estima la Jaccard de los trigramas, que sí
   significa lo que parece; contrastado contra la Jaccard exacta, el error se
   queda en 1-3 puntos. Se mide sobre `page_content.content_text` (contenido
   sin plantilla): con el body entero, cabecera y pie hacen que todo el sitio
   salga duplicado de todo.

25. **El reparto de bandas del LSH sigue al umbral** — con bandas fijas
   pensadas para el 90%, bajar el umbral a 0,6 no encuentra ni una pareja más:
   esas parejas no llegan siquiera a medirse, porque el filtro previo no las
   propone. `reparto_bandas` elige entre 8×32, 16×16, 32×8 y 64×4 el de filas
   más anchas que aún proponga el 95% de las parejas que están justo en el
   umbral. Por debajo de 0,6 (`UMBRAL_MINIMO_FIABLE`) el recall cae y se
   registra un WARNING: se mide igual, pero el recuento ya no es de fiar.

26. **La firma son 256 muestras porque 64 cambiaban el veredicto** — el error
   típico del estimador es sqrt(s(1-s)/n). Con 64 muestras, una pareja al 93%
   real se reportaba al 87,5% y se caía del umbral del 90%: el mismo censo
   marcaba o no marcaba una página según el ruido del muestreo. Con 256 el
   desvío baja a ~2 puntos. Cuesta lineal y se midió: firmar 2.000 páginas de
   400 palabras pasa de 1 s a 3,8 s (≈40 s en un censo de 20.000).

27. **Las redirecciones las sigue el spider, no Scrapy** — toda peticion de
   pagina lleva `dont_redirect`. Si las sigue el `RedirectMiddleware`, la
   peticion al destino pasa por el dupefilter y, si el destino ya se habia
   visto, se descarta con la 301 dentro: el salto no llegaba a `parse`, no se
   guardaba, y los enlaces que apuntaban a el desaparecian del grafo (faltaban
   la mayoria de las 301 internas: http→https, barra final, www). Ahora cada
   salto es su propia fila con `redirect_url`, el destino se pide con la
   MISMA profundidad (una redireccion no es un clic: sumar uno le quitaba un
   nivel entero al rastreo de una semilla `http://x.com`), y la meta refresh
   con URL se trata igual. El PageRank anade la arista salto → destino con
   peso 1 y el destino de una redireccion interna ya no sale huerfano. Con
   render JS la redireccion la sigue el navegador: se registra el salto sin
   codigo (`Redirect (JS)`). La profundidad la respeta
   `middlewares.DepthMiddleware`, que sustituye a la de Scrapy porque esa la
   pisaba (las URLs del sitemap entraban a profundidad 3 en vez de 1).

28. **Un job solo es `completed` si su dato esta completo** — antes el worker
   daba por bueno cualquier rastreo con codigo de salida 0 y un analisis que
   reventaba se tragaba en el log: un censo de 289 URLs rastreadas y 0
   guardadas (faltaba una columna) y todos los analisis fallando en
   `analyze_near_duplicates` terminaron `completed`, sin issues ni PageRank,
   que se lee como "sitio limpio". Ahora: si se rastrea y no se guarda nada, el
   job es `failed` con `finish_reason=persistence_failed`; si falla el
   analisis, `failed` conservando el `finish_reason` del rastreo (lo rastreado
   vale; se relanza con `python -m analysis.analyzer <job_id>`). Los ERROR de
   `seo_crawler.*` suben al log del worker agrupados, con la ultima excepcion
   del traceback (`resumir_stderr`); antes solo subian los WARNING.

29. **PageRank: la repeticion pone techo, no sustituye a la posicion** — el
   peso por posicion se adivina por etiquetas y clases y falla con menus en
   `div`, Tailwind o facetas (salian `content`, peso 1). Ahora se mide la
   repeticion de cada enlace (destino + anchor) en el sitio y en su seccion
   (host + primer segmento, desde 10 paginas), y lo repetido no pesa mas que
   un enlace de menu: techo `0,1 / repeticion`, nunca por debajo de 0,25. #24
   proponia `1 - sqrt(rep)`; se descarto porque lo que recibe un destino
   (rep x peso) CAE a partir del 44%: estar enlazado desde todo el sitio
   restaba. Con el techo el total nunca baja (hay test). Ademas: variante →
   canonical (si la canonica se rastreo con 200) y salto → destino con peso 1;
   el teletransporte y la masa colgante van solo a paginas 200 indexables, y
   `jobs.pagerank_resumen` guarda cuanto acaba en cada tipo de URL. Medido en
   seobide, workoholics, Lopesan y tucanaldesalud: las paginas legales siguen
   arriba cuando todo el sitio las enlaza desde el pie; es un dato del sitio,
   separarlas es trabajo del tipo de pagina (#12). Si la home baja, mirar de
   donde le llegaba: en re-magazine era 1.a solo por el logo de un aviso de
   navegador antiguo (`body > div.deprecation-notice`, fuera de header/nav/
   footer, clasificado `content`) presente en 181 de 183 paginas; con techo
   es 13.a y arriba quedan los tags, que ademas del menu reciben enlaces
   dentro de los articulos. Es B1 funcionando. Coste: 4-5 s con 150-180 k
   aristas, 400 s en Quironsalud (7,7 M enlaces). Materializar los enlaces con el
   anchor ya calculado es obligatorio: unir por `lower(btrim(anchor))` dejaba
   al planificador sin estimacion y tardaba entre 30 y 80 veces mas.

30. **Los porcentajes de /insights van sobre paginas HTML 2xx y cuentan
   paginas** — dividian por todas las URLs internas (saltos, PDFs, 404: donde
   `indexable` es NULL) y sumaban incidencias, asi que una pagina con dos
   problemas de title contaba doble y `pct_thin` pasaba del 100%. En i18n,
   `return_tag_ok`/`lang_valid` NULL es "sin verificar", no fallo: un censo sin
   verificar (Lopesan) salia con nota 0 y dos recomendaciones falsas de
   prioridad alta. `h1_missing` ya no se emite en 4xx/5xx.

## Configuración por cliente (`projects/`)

Todo lo específico de un cliente (filtros, selectores de plantilla, `templates`
para el muestreo por plantilla, idioma, ritmo, semillas) vive en
`projects/<cliente>/config.json`, que es el campo `config` de `POST /api/jobs`.
Se lanza con `python scripts/lanzar_job.py <cliente> [--canary] [--set k=v]`.
Solo `projects/_ejemplo/` se versiona; ver `projects/README.md`. **Nunca**
reglas de un cliente en código.

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

# Worker
STALL_AUTO_RESUME=3              # reanudaciones automaticas tras estancamiento (0 = ninguna)
# Por job (crawl_behavior): render_wait_ms sobreescribe PLAYWRIGHT_BANNER_WAIT_MS;
# recrawl_patterns (regex) hace que un resume repita esas URLs; retry_failed_on_resume.
SCRAPY_LOG_DIR=/tmp/scrapy-logs  # log de Scrapy por job, en vivo

# Sitemaps
MAX_SITEMAP_FILES=500            # cuantos ficheros de sitemap se siguen
MAX_URLS_PER_SITEMAP=50000       # URLs por fichero
MAX_SITEMAP_BYTES=52428800       # tope del XML YA DESCOMPRIMIDO (bomba gzip)
```

## SEO Config Thresholds (`shared/config.py`)

| Constant | Value |
|----------|-------|
| `TITLE_MIN_LEN` | 10 |
| `TITLE_MAX_LEN` | 60 |
| `DESCRIPTION_MIN_LEN` | 50 |
| `DESCRIPTION_MAX_LEN` | 160 |

Umbral de casi duplicados: `job.config.analysis_thresholds.near_duplicate_similarity`
(por defecto 0.9; ver decisiones 16-18).

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

Unit test suite at `tests/` (268 casos: 258 en la imagen del crawler y 10 mas —`test_insights.py` y `test_export_csv.py`— que necesitan FastAPI y se corren en la de la API): pure extractors
(`test_extractors.py`), main-content extraction / boilerplate stripping
(`test_content_extraction.py`), structured-data validation
(`test_sd_validation.py`), sitemap parsing (`test_sitemaps.py`) and
casi-duplicados (`test_near_duplicates.py`, que contrasta la similitud
estimada contra la Jaccard exacta) y un test de integracion del spider
(`test_spider_rastreo.py`: lanza el `SeoSpider` real contra un sitio servido en
local por `spider_harness.py`, con BD y Redis simulados — redirecciones, meta
refresh, sitemaps, patrones y hrefs malformados, tambien en el modo en que la
redireccion la sigue el navegador) y `test_analyzer_near_duplicates_db.py`, que
pasa `analyze_near_duplicates` contra SQLite en memoria, y `test_worker.py`
(que errores del spider suben al log del worker). PageRank: `test_pagerank.py`
(modelo sobre grafos pequenos) y `test_pagerank_db.py`, que necesita Postgres
de verdad y se salta sin `PAGERANK_TEST_DATABASE_URL` (instrucciones en el
fichero; nunca apuntarlo a `crawler_db`). Del contrafactual por columna salen
tres mas: `test_export_csv.py` (cada columna declarada del CSV tiene su valor
en la fila; sin esto, anadir una y olvidar el dato desplaza en silencio todas
las de la derecha), y `test_render_espera.py` (contrato de la espera de render:
no resolver con peticiones en vuelo).
Run with
`pip install -r tests/requirements.txt && pytest`.
`scripts/prueba_espera_render.py` mide si la espera de render pierde el
contenido que llega por XHR (laboratorio con retardo, o URLs reales).
`scripts/check_content_quality.py <job_id>` compares, per URL template,
what the crawl stored against what the extractor, `extract_main_content` and a
Chromium render see right now — it is how content loss is caught after a crawl.
De la capa de analisis contra BD ya hay tres tests
(`test_analyzer_headings_db.py`, `test_analyzer_near_duplicates_db.py` y
`test_pagerank_db.py`); el resto se sigue verificando con las consultas SQL de
`docs/AUDITORIA_Y_VERIFICACION.md`.

## PENDING WORK — read before adding features

**`docs/AUDITORIA_Y_VERIFICACION.md` is the canonical TODO.** It contains, in
order of priority:

1. **Verification checklist (sections 0–8c)**: ~30 fixes from the 2026-07
   audit branch (`claude/crawler-export-issues-oi77bm`, PR #5) that have NOT
   yet been verified against a real crawl. Run it top-to-bottom on first
   deployment (two test crawls: with and without `render_js`). ⚠️ Requires
   `docker compose up -d --build` (worker image changed: `analyzing` status,
   heartbeat) and re-running `scripts/init_db.py` (new `urls.in_sitemap`
   column).
2. **Impact diagnosis for pre-fix crawls (section 10, D1–D7)**: old crawls may
   carry corrupted data (relative canonicals → false non-indexable). Measure
   before trusting/re-delivering old reports; re-crawl bucket-A jobs.
3. **Screaming Frog parity roadmap (section 10b)**: gaps left, prioritized —
   custom extraction (XPath/regex per job) ⭐⭐⭐, JavaScript raw-vs-rendered tab ⭐⭐, pagination analysis ⭐⭐,
   PageSpeed/CWV API ⭐⭐, minor ones after.
4. **Known limitations (section 11)**: also, with `render_js` an `http://` URL of an
   https site is recorded as **307** (Chromium's internal upgrade redirect), not the
   server's real 301. Treat http→https 307s in JS crawls as 301.: backup is not truly streaming (OOM risk
   on huge jobs), word_count includes hidden text, SD validation is basic.
   Dos columnas mas con relleno parcial, a proposito: `urls.http_version` solo
   se rellena con `render_js` (Navigation Timing; por el camino de curl_cffi
   habria que reimplementar el handler de scrapy-impersonate, que la
   descarta), y `resources.size_bytes` no se recoge — el rastreo no descarga
   el cuerpo de los recursos; el dato saldria de unir `resource_url` con
   `urls.content_length` de los recursos que si se rastrearon.

## Automatic JS-render check

Every crawl that finishes **without** `render_js` triggers
`scripts/check_js_templates.py` automatically (worker → `_comprobar_render_js`).
It groups the crawled URLs by template shape, samples them, and compares raw HTML
against Chromium-rendered HTML.

It answers the question that otherwise stays invisible: **if a template builds its
links with JavaScript, the link graph is incomplete and the PageRank computed from
it is wrong, with nothing to flag it.** A WARNING is logged when that happens.

Result is stored in `jobs.js_check` and exposed on the job endpoint:

| Field | Meaning |
|---|---|
| `grafo_fiable` | false → some template hides links; re-crawl with `render_js=true` before trusting link metrics |
| `enlaces_ocultos` | whether any JS-only internal links were found |
| `plantillas[]` | per template: samples, JS-only links, words raw vs rendered |

Costs ~1 min on top of a multi-hour crawl (5 templates × 1 sample by default).
Env: `JS_CHECK_ENABLED=0` disables it, `JS_CHECK_TEMPLATES`, `JS_CHECK_SAMPLES`.
Classification rules live at the top of the script and are per-project — CMS URLs
(Liferay portlets) are isolated first so they don't pollute the sample.

Run it by hand against any finished job:

```bash
docker compose exec -T crawler python /app/scripts/check_js_templates.py <job_id> --muestras 2
```

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

## What Does NOT Exist Yet

- Authentication/authorization
- Monitoring/metrics (Prometheus, Grafana)
- PageSpeed/CrUX integration
- Custom extraction / custom search (XPath/CSS/regex per job)
- JavaScript comparison tab (raw HTML vs rendered)
- Pagination (rel next/prev) analysis
- Structured data rich-result validation (only basic @type/required-prop checks)
