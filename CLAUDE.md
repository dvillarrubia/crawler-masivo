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
| GET | `/api/jobs/{id}/export` | CSV export (streaming, 1000-row windows) — **82 columnas**: URL + metadatos + **h1/h2** (solo los que se pintan), og/twitter, hreflang, tipos de datos estructurados, imagenes sin alt y cabeceras de seguridad. Las de M1: `content_word_count`, `content_hash`, `pagerank_score` y `pagerank_raw` (la 0-100 es la que hay que leer y ordenar, decision 34), `pagerank_fiable` (decision 35), `blocked_by_robots` (decision 48, con `status_code` VACIO porque la URL no se pidio) y los dos `*_pixel_width` (decision 54), agregados por lote (4 consultas por ventana, no 4 por URL). `content_text_first_500` dice en el nombre que va recortado; el texto entero es `/content/export` |

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
| `contar_palabras(texto)` | int — tokens, y caracteres en las escrituras sin espacios (CJK, tailandes) |
| `extract_word_count(selector)` | int — palabras del texto visible del body |
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
| `analyze_content()` | low_word_count (sobre el contenido propio, solo en indexables) |
| `analyze_url_issues()` | url_too_long, url_non_ascii, url_uppercase, url_underscores, url_multiple_slashes, url_has_parameters, url_non_seo_friendly, url_cms_faceted |
| `analyze_links()` | orphan_page, **solo_enlazada_por_redireccion** (decision 63), high_outlink_count, y las metricas del grafo (inlinks, outlinks, pagerank) |
| `analyze_sitemap()` | sitemap_orphan, not_in_sitemap |

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
34. **El PageRank en tres columnas, porque son tres preguntas** — la escala
   0-10 lineal no distingue la pagina 250 de la 25.000: la mas fuerte vale 10
   y el resto se apelotona en 0,00xx (medido en blogs.uoc.edu: 29.483 de
   34.704 por debajo de 0,1, el 85% indistinguible). Ahora
   `pagerank_raw` es la probabilidad —por el numero de nodos da "veces la
   pagina media", comparable entre sitios de tamano distinto—, y
   `pagerank_score` es 0-100 logaritmica anclada al MAXIMO del sitio, donde
   cada 25 puntos son una decada. Es la que hay que leer y ordenar.
   `pagerank` se queda en 0-10 por compatibilidad con informes entregados.
   El 0-100 es lo que se ve en la tabla de URLs y en la ficha de una URL; el
   0-10 solo aparece en la ficha, en gris, rotulado «antiguo». Que la columna
   de la tabla siguiera siendo la de 0-10 hacia invisible el arreglo entero:
   las nueve primeras filas de blogs.uoc.edu se leian
   `0,4 0,0 0,0 0,2 0,0 0,3 0,0 3,2 7,1` y ahora `65 38 38 58 20 63 20 88 96`.
   La primera version la ancle con min-max sobre los logaritmos y el 78% de
   las paginas acabo en el decil mas alto: el minimo de la distribucion es una
   pagina aislada que estira la escala entera. Con el maximo como ancla, el
   mismo censo cubre los diez tramos. Las cuatro decadas no son arbitrarias:
   el maximo es 393 veces la mediana y 4.515 veces el percentil 25.

35. **El aviso de que el grafo no es fiable viaja CON el dato, tambien en
   pantalla** — si
   `jobs.js_check.grafo_fiable` es false hay plantillas que montan sus enlaces
   con JavaScript y el PageRank sale de un grafo incompleto. Antes eso solo
   existia como WARNING en el log del worker y la cifra se entregaba como si
   nada. Ahora esta en `jobs.pagerank_resumen` y en una columna del CSV
   (`pagerank_fiable`), porque quien ordena por PageRank en una hoja de
   calculo no abre el endpoint del job.
   Ojo al ORDEN, que durante un tiempo dejo esa columna vacia en todas las
   filas: el analisis escribe `pagerank_resumen` **antes** de que el worker
   lance la comprobacion de render, asi que en un rastreo recien terminado la
   clave existe con valor `None`. El CSV miraba si la clave estaba, no su
   valor, y devolvia ese None sin llegar a consultar el `js_check`. Medido en
   el censo de progym: 4.320 filas sin el aviso teniendo
   `js_check.grafo_fiable = true`. Un `None` es «aun no se sabe». Y con
   `render_js` la comprobacion no llega a correr —existe para los rastreos SIN
   render—, asi que ahi la respuesta es que si: un rastreo que renderiza ya ha
   visto los enlaces que monta el JavaScript, que es lo unico que mide esta
   columna. Y en el frontend, que es donde se mira
   un rastreo: un aviso en la ficha del job cuando `grafo_fiable` es false, con
   las plantillas afectadas, y otro distinto cuando es null (no se pudo
   comprobar porque Chromium no recibio contenido). Ademas la tabla pinta
   `pagerank_score` —la escala 0-100 de la decision 34— en vez de la 0-10 con
   cuatro decimales, que era la unica que habia: con esa, el 85% de las paginas
   de un censo queda en 0,00xx y no se distingue la 250 de la 25.000. Para eso
   hacia falta exponer `pagerank_score` y `pagerank_raw` en la respuesta de
   `/urls`, que solo llevaba `pagerank`.

41. **Los conteos de enlaces leen `links` UNA vez, no cuatro** (#52) — la
   tabla guarda los enlaces de TODOS los rastreos (46 GB y 181.695.835 filas en
   produccion) y el planificador la recorre entera para sacar los de un job:
   cuatro agregados eran cuatro recorridos. Ahora las aristas del censo se
   materializan una vez en una temporal —lo mismo que ya hacia
   `compute_pagerank`— con el id del DESTINO ya resuelto, asi que los conteos
   no vuelven a `urls` ni filtran por job. Medido en penguin (10,3 M enlaces
   del job): **400,8 s -> 57,0 s, siete veces**, y las tres cifras de control
   (6.227.726 inlinks, 19.495 paginas sin entrantes, 9.600.373 outlinks) salen
   IDENTICAS: es una optimizacion, no un cambio de criterio disfrazado.

   La temporal se construye con tipos de SQLAlchemy y no con SQL a mano: el
   `job_id` es un UUID y comparado como cadena no liga igual en todos los
   motores — la primera version no actualizaba ni una fila en los tests contra
   SQLite. Los indices de la temporal van con nombre, porque
   `CREATE INDEX ON tabla (col)` es sintaxis solo de Postgres.

   Cronometrado tambien en Druni (60.399 URLs, **37,5 millones de enlaces**,
   3,6 veces penguin): `compute_link_counts` **299,3 s** y el `run_all` entero
   **2.585,8 s (43 min)**. O sea que el agregado de enlaces escala razonable
   (3,6x las aristas -> 5,2x el tiempo) y en un censo de ese tamano **el coste
   dominante ya no es este sino `compute_pagerank`**: unos 35 de esos 43
   minutos se van en montar SU propia materializacion de aristas
   (`pr_lk_tmp` -> `pr_rep_tmp` -> `pr_edges_tmp`). Las dos materializan casi
   la misma tabla dos veces en el mismo analisis, **pero compartirla ahorraria
   poco**: cronometrando cada sentencia de `_aristas_de_enlaces` en Druni, el
   recorrido de `links` (`pr_lk_tmp`) son **114,9 s de 1.770,9 — el 6,5%**,
   mientras que las ventanas de repeticion (`pr_rep_tmp`) son 781,6 s (44%) y
   la agregacion final por (origen, destino) con el peso (`pr_edges_tmp`) otros
   862,3 s (49%). El coste esta en las dos agregaciones sobre 37,5 millones de
   filas, no en leer la tabla. La direccion que si tendria efecto —anotada en
   #52— es que `pr_lk_tmp` guarda una fila por INSTANCIA de enlace y la tabla
   final es por (origen, destino), que son muchisimas menos: agregar antes del
   join atacaria los dos pasos caros a la vez, pero toca el criterio y necesita
   su propia comprobacion de que las cifras no se mueven.

42. **Los avisos sobre la forma de la URL casaban mal, y uno no salto nunca**
   (#29). Cuatro cosas, medidas en blogs.uoc.edu (34.704 URLs):

   - `url_non_ascii` comprobaba la URL **escapada**, que es ASCII puro, asi que
     no salto en ningun censo: 0 -> **337 paginas**. Hay que decodificar antes.
   - La regex de escapes de JavaScript pedia `%5Cu\d{4}` —solo digitos— asi que
     `%5Cu002F`, el ejemplo de su propio comentario, NO casaba. Son
     hexadecimales.
   - `/-/categories/123`, la faceta de verdad de Liferay, no la cazaba ninguna
     alternativa; y `/elem_entry_list/` si la cazaba por `/ELEM_ENTRY` con
     IGNORECASE. Un listado de contenidos no es una faceta, y marcarlo manda al
     cliente a bloquear algo que quiere indexar.
   - Los checks se aplicaban a CUALQUIER fila: saltos de redireccion, imagenes,
     CSS. En un salto no hay nada que arreglar —la URL se esta yendo— y en una
     imagen la forma de la URL no es una decision editorial. Ahora solo
     documentos internos (HTML y PDF, que Google indexa). Efecto: `url_too_long`
     4.984 -> 4.623, `url_underscores` 1.950 -> 1.545, `url_uppercase` 693 ->
     563, `url_multiple_slashes` 8 -> 0.

43. **Una cadena de redirecciones empieza en DOS saltos** — el umbral por
   defecto era 2 con la comparacion `hops > 2`, asi que hacian falta TRES y
   A->B->C, la cadena mas comun y la que Google pide evitar, no se reportaba
   nunca. Medido en el mismo censo: habia 2.731 redirecciones de un salto
   (normales), **372 de dos** y 3 de tres o mas; los avisos pasan de 3 a 375. El
   valor manda desde `api/schemas.py`, no desde la config del cliente: ahi
   estaba el 2.

39. **Una sola funcion de indexabilidad, en `shared/`** (#26) — el spider y el
   analyzer repetian las reglas y discrepaban: 1.254 paginas de un censo eran
   "Canonicalised" para uno e "Indexable" para el otro. `shared/indexabilidad.py`
   tiene `estado_indexabilidad()` y una tabla de 32 casos en
   `tests/test_indexabilidad.py`. Lo que arregla, cada cosa con su criterio:
   robots dirigido a un bot (`<meta name="googlebot" content="noindex">` saca
   la pagina del indice aunque el generico diga `index`), TODAS las cabeceras
   `X-Robots-Tag` y no solo la ultima (`noindex` + `noarchive` salia
   indexable), el canonical de la cabecera HTTP `Link` (se extraia y nadie lo
   miraba), y el 204 —una respuesta sin contenido no se indexa— que el spider
   daba por indexable y el analyzer no.

   Comparar URLs tiene su propia regla, y es SEO, no fontaneria: el puerto por
   defecto, la caja del host y el fragmento NO cuentan; `www` frente a sin
   `www` y `http` frente a `https` SI, porque ahi hay una canonicalizacion de
   verdad. Y el escapado por ciento tampoco cuenta: `intel%C2%B7ligencia` y
   `intel·ligencia` son la misma URL, y no normalizarlo marcaba 83 paginas
   autocanonicas de un censo como "Canonicalised" (pasa en catalan y en
   cualquier idioma con acentos en la URL). Cambio real al unificar: 0,2% de
   las filas en blogs.uoc.edu y 0,7% en www.uoc.edu.

40. **Un candado no puede tragarse los errores de lo que protege** — la primera
   version de `candado_de_job` tenia el `yield` dentro de un `try/except`
   amplio: cuando el analisis de dentro reventaba, la excepcion entraba por el
   yield, la cazaba ese except y se cedia por segunda vez, asi que Python
   lanzaba `generator didn't stop after throw()` y el error ORIGINAL
   desaparecia. Medido: el analisis de un censo de 60.399 URLs fallo y el log
   solo decia eso. Un solo `yield`, y el `except` del montaje no lo envuelve.

38. **Una pagina `noindex` no enlaza: ni como entrante ni en el PageRank**
   (C3 de #24, criterio decidido el 7-oct-2026). Google acaba tratando los enlaces de
   una noindex como nofollow, asi que una pagina cuyos unicos enlaces vienen de
   ahi no esta enlazada a efectos de buscador: cuelga de paginas que el
   buscador va a dejar de rastrear. El `noindex` se materializa en
   `urls.noindex`, separado de `indexable` —que tambien es False por canonical
   o por codigo— y no se puede deducir en SQL: buscar la subcadena marcaria
   `noindex/nofollow`, que Google NO interpreta (decision 23). Si el dato no se
   conoce (NULL) el enlace cuenta: no se descarta por desconocimiento.
   **Ojo al re-analizar un censo anterior a la columna**: `urls.noindex` la
   materializa `analyze_indexability`, asi que en un rastreo viejo estaba a
   NULL —y `NULL IS NOT TRUE` es cierto, o sea que esas paginas SI repartian—.
   Al re-analizarlo se rellena y salen del grafo: en Druni, un censo de hace
   dos meses, aparecieron **9.771 paginas noindex** y con ellas se fueron sus
   aristas. El PageRank de un censo viejo CAMBIA al re-analizarlo, y no es un
   fallo: antes se calculaba con todas repartiendo. Si se vuelve a entregar un
   informe de enlazado de un censo anterior, las cifras no cuadraran con las de
   la primera entrega y la explicacion es esta.
   Medido en blogs.uoc.edu: 1.813 paginas noindex, y 992 URLs cuyos unicos
   enlaces venian de ellas — 824 con parametros (busquedas internas, trampas de
   rastreo) y 168 sin ellos, que son el hallazgo: categorias con 400-800
   palabras, fuera del sitemap, colgando solo de su paginacion noindex.

   El mismo criterio vale en el PageRank: una noindex **recibe pero no
   reparte**. Lo que ya tenia acumulado no se pierde —queda colgante y su masa
   va al teletransporte, o sea al conjunto de indexables— pero no va a los
   destinos concretos que ella enlazaba. Que SI reciba es deliberado: un enlace
   a una noindex es autoridad que se tira, y eso es justo lo que mide
   `reparto["no_indexable"]`. Cuantas paginas son fuente de verdad del grafo
   cambia muchisimo por sitio y no se puede estimar con `outlinks_count` (que no
   filtra por `follow`): medido con los enlaces del grafo, blogs.uoc.edu tiene
   **1.758 paginas noindex que enlazan 203.380 veces** a 7.400 destinos,
   www.uoc.edu solo 44 (10.775 enlaces) y progym 3 (519). Por eso el efecto
   tambien cambia: en www.uoc.edu quita 7.496 aristas de 2.013.337 y mueve el
   reparto 7 centesimas (84,02% -> 84,09% indexable), sin que entre ni salga
   nadie del top 50 y con 2 puestos de movimiento maximo; en progym, 474 aristas
   de 582.205 y el reparto no se mueve; pero en blogs.uoc.edu quita **127.360
   aristas de 1.517.272** (el 8,4% del grafo) y pasa **seis puntos** de PageRank
   de las noindex a las indexables (no_indexable 8,46% -> 2,29%, indexable
   87,22% -> 93,12%). Son sus 1.758 paginas de paginacion de categoria
   enlazandose entre ellas: la autoridad circulaba en un circuito cerrado que
   Google va a dejar de rastrear. El ORDEN casi no se mueve en ninguno de los
   tres —del top 50 no entra ni sale nadie— pero los valores absolutos suben (el
   1.º de blogs pasa de 108,3x a 119,9x la pagina media).

37. **El job se filtra en `links`, nunca uniendo con `urls`** — la tabla de
   enlaces guarda los de TODOS los rastreos: 46 GB y 181 millones de filas en
   la instalacion de produccion. Poner el `job_id` en el lado de `urls` deja
   el filtro fuera del alcance del indice y Postgres recorre la tabla entera.
   Medido con EXPLAIN en la consulta nueva de `high_outlink`: coste 6.189.456
   uniendo, 122.247 filtrando por `links.job_id`. Cincuenta veces, y en tiempo
   real `analyze_links` paso de minutos a 1,7 s en blogs.uoc.edu y 11,2 s en
   www.uoc.edu. Lo pille porque un re-analisis que antes tardaba 219 s llevaba
   doce minutos sin terminar.

63. **Lo que solo se alcanza por un salto tampoco puede quedarse sin aviso**
   — excluir los destinos de redireccion de `orphan_page` (decision 36) evita
   llenar el informe de huerfanas falsas tras una migracion, pero abrio un
   agujero: una pagina **indexable, con 200 y cero enlaces internos**, a la que
   solo se llega a traves de un 301, no recibia NINGUN aviso. Medido sobre los
   tres censos: **522 en www.uoc.edu (363 declaradas en el sitemap)**, 109 en
   blogs.uoc.edu y 1 en progym; ninguna con un solo issue. La de progym es
   ademas la pagina **mas fuerte del sitio por PageRank**: hereda el de la URL
   vieja, que es la que todo el mundo sigue enlazando, y en el CSV se leia como
   «PageRank 100, enlaces entrantes 0».
   No es `orphan_page` —la pagina es alcanzable y por el salto le llega casi
   toda la senal—: lo que hay que arreglar es concreto y distinto, que los
   enlaces internos apunten a la URL final, porque Google acaba dejando de
   rastrear la vieja. Si ademas esta en el sitemap, se declara para indexar algo
   que el propio sitio no enlaza: `warning`; sin sitemap, `info`.

36. **Un aviso por hallazgo, no dos** — las URLs del sitemap sin inlinks
   recibian `orphan_page` y `sitemap_orphan` a la vez. Dicen lo mismo y el
   segundo es mas fuerte (esta declarada para indexar y aun asi nadie la
   enlaza), asi que el informe parecia tener el doble de problemas. Medido:
   en www.uoc.edu, 8.045 + 7.475 filas para unas 8.000 paginas pasan a 15 +
   6.498. `orphan_page` se reserva para las que NO estan en el sitemap, y de
   esas se siguen excluyendo los destinos de redireccion (555 de 570 en ese
   censo) para no llenar el informe de huerfanas falsas tras una migracion.

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
   Ese cubre lo que lee el CODIGO; lo que escribe una PERSONA lo cubren
   `tests/test_lanzador.py` (las claves de los `projects/*/config.json` contra
   las que `JobConfig` declara, leidas del AST) y `scripts/lanzar_job.py`, que
   tras crear el job compara lo enviado con lo guardado y sale con **3** si la
   API ha tirado algo, nombrando la clave. Con los cinco configs de hoy no
   salta: es un cable trampa, no un lobo que grita.

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

44. **El texto que no se pinta no es contenido, y el menú no es contenido
   de la página** — tres medidas que decidían cosas distintas estaban mal:
   `word_count` contaba con `split()`, así que una página japonesa entera daba
   **1 palabra** y todo sitio CJK salía como thin content; `text_ratio` medía
   sobre `extract_visible_text`, que incluía los nodos de solo espacios, así
   que medía la indentación de la plantilla (31% medido en la home de un
   cliente, 6,7% real); y el recuento era del `<body>` entero, de modo que el
   megamenú y el pie —boilerplate para Google— tapaban las páginas escasas:
   997 avisos de 29.808 páginas midiendo el body contra **10.433** midiendo el
   contenido propio, 4.736 de ellas indexables. Ahora un solo recorrido del
   árbol (`_lineas_visibles`) alimenta `word_count`, `text_ratio` y
   `page_content`: fuera lo que el navegador no pinta (`hidden`,
   `display:none` en línea, `title` de un svg, respaldo de un `iframe`),
   `hidden="until-found"` sí es contenido, y las fronteras de bloque evitan que
   `Zapa<span>tillas` cuente como dos palabras. Nueva columna
   `urls.content_word_count`. Medido en 51 páginas de control de tres clientes:
   10 cambian su contenido guardado y en las 10 lo único que sale es interfaz
   oculta (modal de login, menú de cuenta, promo de la app). `low_text_ratio` y
   `very_low_text_ratio` dejan de emitirse: el ratio texto/HTML no es una señal
   de Google y, bien medido, salta en 21 de 30 páginas de control.

45. **El limpiador de plantilla no puede borrar la página, y deduplicar no
   puede desalinear una tabla** — tres fallos del extractor de contenido, los
   tres reproducidos: (a) las listas de nombres de plantilla casaban por
   **subcadena sobre cualquier elemento, `body` incluido**, así que
   `<body class="cookie-bar-active">` dejaba la página en **0 palabras**, y
   `cookie-policy` o `privacy-notice-content` borraban el cuerpo de las propias
   páginas legales —las que tienen que estar indexadas tal cual—; (b) `<form>`
   se quitaba siempre, y en ASP.NET WebForms la página entera va dentro de
   `<form id="aspnetForm">`: 0 palabras otra vez; (c) `_dedupe_lines` miraba una
   ventana de 4 líneas sin saber de dónde venían, así que de 8 celdas `Sí` de
   una tabla comparativa quedaba 1 y **las filas salían desplazadas**: el
   informe decía lo contrario que la página. Ahora: `_TAGS_INTOCABLES`
   (`html`, `body`, `main`, `article`) no se borran nunca; un bloque cuyo nombre
   casa pero que se lleva más del 40% de las palabras de la página es la página,
   no plantilla (`_MAX_SHARE_PLANTILLA`); un `<aside>` o un
   `role="complementary"` DENTRO de `main`/`article`/`section` es contenido —la
   misma regla de landmark de la decisión 9— y se desenvuelve a `<div>` porque
   trafilatura tira todo `<aside>` por su cuenta; y una repetición solo se
   colapsa si no viene de una celda o un item (`_TAGS_DATO`) y si entre las dos
   apariciones hay 2 líneas distintas como mucho, que es la forma de una
   marquesina (`A A A`) o de una animación (`A B A B`), no la de una tabla.
   Además lo que no se pinta se quita también del HTML que lee trafilatura, no
   solo al contar: `d-none` (Bootstrap), `hidden` (Tailwind), `is-hidden`,
   `hide` e `invisible` se casan por token completo, de modo que `d-md-none`
   —que oculta solo a partir de cierto ancho— sigue siendo contenido. Medido en
   las mismas 58 páginas de control: cambia el contenido de 41, se recuperan
   hasta **+1.414 palabras** en los listados de blog (las fichas repetidas que
   el deduplicador borraba) y se van hasta **−7.117** de megamenú oculto en un
   artículo de comercio electrónico, con **0 líneas de prosa perdidas**.

46. **Un titular oculto no es un titular, y un icono con `aria-label` no es un
   enlace sin ancla** — cuatro cosas que el extractor leía mal y que acababan en
   el informe: (a) los clones móvil/escritorio del mismo `<h1>` (uno con
   `d-none`, otro con `aria-hidden`) contaban como tres h1 y producían un
   `h1_multiple` falso, mientras que los de `template`/`noscript` se tiraban en
   silencio —al revés de lo que se hacía con sus enlaces e imágenes—; ahora se
   guardan TODOS con una marca `headings.oculto` y los checks de h1 y el CSV
   cuentan solo los que se pintan; (b) el texto del titular se armaba uniendo
   nodos con espacio, así que `Zapa<span>tillas` daba `"Zapa tillas"` y un
   `<script>` dentro del `<h1>` acababa en el texto; un `<h1>` que solo lleva el
   logo ahora usa el `alt` de la imagen, que es de donde lo lee Google;
   (c) `role="heading"` con `aria-level` cuenta como titular de ese nivel (2 por
   defecto, según ARIA); (d) un enlace de icono sin texto usa su `aria-label`,
   su `title` o la etiqueta de su `<svg>` — es lo que anuncia un lector de
   pantalla y lo que Google toma como ancla: **3.438 → 1.276 anclas vacías** en
   58 páginas de control (2.915 → 1.056 contando solo los enlaces internos). El
   ancla pesa en el agrupamiento por repetición del PageRank (decisión 29) y en
   todo análisis de anchor text. *(Esas cifras corrigen las de la primera
   versión de esta nota —«1.783 → 155»—, que no se reproducen con ninguna de
   las dos definiciones; estas salen de volver a pasar cada versión del
   extractor sobre las mismas 58 páginas. Ver la decisión 61, que baja las
   1.276 a 208.)* Además, lo que vive
   en `<template>` o `<noscript>` ya no aporta enlaces ni imágenes: no está en el
   DOM, y el `<noscript><img>` de la carga diferida duplicaba la imagen real
   (583 de 4.409 imágenes en las mismas 58 páginas). El hero fuera del
   contenedor (decisión 10) rechaza el bloque cuando más de la mitad de sus
   palabras están dentro de enlaces: con `<div class="top-bar"><h1 class=logo>`
   se antepone el megamenú entero al contenido.

47. **Un duplicado solo importa entre páginas que Google puede posicionar, y
   se mide por contenido, no por bytes** — los grupos de título, description y
   h1 duplicados incluían las variantes `?utm`, las ordenaciones de un listado y
   los `/page/2`, que comparten título a propósito y están canonicalizadas o en
   noindex: no compiten con nadie y enterraban las que sí. Medido en tres censos,
   las páginas avisadas por título duplicado pasan de 5.936 a 2.788, de 14.580 a
   5.299 y de 5.557 a 1.417; las de description de 2.807 a 659; las de h1 de
   15.235 a 10.838. Por eso `analyze_indexability` corre ahora ANTES de títulos y
   descripciones en `run_all`: estaba después, así que `Url.indexable` era NULL
   cuando se agrupaba. Y el duplicado exacto se compara con `urls.content_hash`
   —SHA-256 del contenido principal con caja y espacios normalizados— en vez de
   `body_hash`, que es el de los bytes de la respuesta: con un token CSRF o un
   nonce de CSP dentro, dos páginas idénticas nunca coincidían. En blogs.uoc.edu
   el hash de bytes encontraba **0** duplicados y el del contenido encuentra
   **745 páginas en 304 grupos**, entre ellos la misma política de privacidad de
   652 palabras publicada e indexable en decenas de blogs del multisite.
   `body_hash` se conserva: responde a otra pregunta (si la página cambió entre
   dos rastreos). Los rastreos anteriores a la columna caen a `body_hash` con un
   aviso en el log.

48. **Lo que robots.txt prohíbe existe, y el modo auditoría estaba muerto** —
   las URLs que el sitio enlaza y robots.txt bloquea no llegaban a la base de
   datos: el `IgnoreRequest` se descartaba y la URL no aparecía en ningún
   informe, ni bloqueada ni enlazada. Google las lista en Search Console y
   Screaming Frog también: o el bloqueo es un error sobre contenido que debería
   posicionar, o es intencionado y entonces son enlaces internos gastando
   presupuesto de rastreo. Ahora se guardan con `status_code` NULL (no se
   pidieron; un 0 en el CSV se lee como «respondió 0»), `status_group="blocked"`
   e `indexability_status="Blocked by robots.txt"`, y el worker distingue
   «filas» de «filas con respuesta» para que un `Disallow: /` siga sin pasar por
   un rastreo correcto (#37). Y el modo `audit` no funcionaba en absoluto:
   `RobotsAuditMiddleware` replicaba a ojo el `__init__` del middleware de
   Scrapy y se dejaba `self._stats`, así que la **primera** petición moría con
   `AttributeError`, el rastreo se quedaba a cero páginas y el vigilante lo
   mataba por estancamiento media hora después. Hay un test que compara los dos
   conjuntos de atributos, de modo que si una versión de Scrapy añade otro lo
   canta el test y no un rastreo de 40 minutos.

49. **El alcance del rastreo va por la Public Suffix List, y una `<base href>`
   no navegable se ignora** — `crawl_subdomains` tomaba como raíz las dos
   últimas etiquetas del host, así que para una semilla `.co.uk` la raíz era
   `co.uk` y **cualquier** sitio `.co.uk` entraba en el rastreo y en el informe
   del cliente. Ahora `dominio_registrable()` usa la PSL con `tldextract`, que
   ya viene con Scrapy, sin red (`suffix_list_urls=()`) y con los sufijos
   privados activados, de modo que `usuario.github.io` y `cliente.myshopify.com`
   son sitios distintos y `localhost`/`127.0.0.1` se devuelven tal cual.
   Y una `<base href="javascript:void(0)">` —común en portales antiguos y en
   SPA— dejaba TODOS los enlaces relativos de esa página sin resolver, marcados
   como externos y sin rastrear: el grafo interno de esas páginas desaparecía.
   Comprobado en Chromium: el navegador ignora una base `javascript:` o `data:`
   y mantiene la URL del documento, así que es exactamente lo que se hace ahora
   (solo se acepta una base que resuelva a http/https).

50. **Un sitemap de texto, un RSS o un Atom daban cero URLs, y los recursos
   filtrados gastaban presupuesto** — el protocolo de sitemaps.org incluye el
   fichero de texto plano (una URL por línea) y Google acepta además RSS 2.0 y
   Atom como sitemap. `parse_sitemap` solo entendía `<urlset>` y
   `<sitemapindex>`, así que con cualquiera de los otros tres la lista salía
   **vacía** — y con ella `in_sitemap` en falso para TODAS las URLs del sitio,
   de donde salen los huérfanos inflados y una lista de «URLs del sitemap sin
   rastrear» vacía. El de texto exige que la línea empiece por http(s) y no
   tenga espacios ni `<`, para que un HTML de error no pase por sitemap.
   Aparte: el contador de páginas se incrementaba ANTES del filtro por tipo de
   recurso, así que un PDF o una fuente que el job excluye gastaba `max_urls`
   sin dejar una sola fila (medido contra el código anterior: el contador subía
   a 1 con un PDF filtrado). Y `crawl_svg` no hacía nada, porque el SVG se
   clasificaba como `image` tanto por `Content-Type` como por extensión: ahora
   es su propio tipo. Y los dos tamaños estaban **cambiados**: Scrapy
   descomprime el cuerpo pero no toca la cabecera, así que `Content-Length` son
   los bytes del cable y `len(response.body)` el recurso descomprimido —medido:
   143 contra 28.055 en una respuesta gzip—. Se guardaba el comprimido como
   `content_length` y el descomprimido como `transfer_size`. En el censo de
   www.uoc.edu la media de `content_length` era **45 kB** para páginas de
   **760 kB**, con 15.330 filas donde el «transferido» superaba al «tamaño» más
   del doble.

51. **El `@graph` de Yoast dejaba ciego a todo WordPress** — un bloque con
   `@graph` se guardaba como UNA fila con `schema_type` NULL, así que las 2-6
   entidades de dentro no existían: ni filtro por tipo, ni informe por tipo, ni
   validación por entidad. Medido en blogs.uoc.edu: **30.701 bloques en 29.803
   páginas** —el 100% del censo— con el tipo a NULL. Ahora el `@graph` se abre
   en una entidad por nodo. Con él, cuatro arreglos más del mismo sitio: el
   `@type` se normaliza a nombre corto (RDFa lo escribía como IRI completo y
   JSON-LD/microdatos como nombre corto, así que el mismo tipo salía con dos
   nombres y los filtros no casaban); el JSON-LD envuelto en `//<![CDATA[`
   —Drupal, portales antiguos— se lee en vez de desaparecer sin aviso; un nodo
   que solo es una referencia (`{"@id": …}`) no es un bloque roto; y una
   propiedad obligatoria presente pero **vacía** cuenta como ausente
   (`itemListElement: []` daba «ok» a una miga de pan sin eslabones). Las
   entidades anidadas también se validan: un Product dentro de
   `WebPage.mainEntity` es el producto de la página. Y `Article` deja de exigir
   `headline`: Google no documenta ninguna propiedad obligatoria para Article,
   así que es un aviso, no un error.

52. **Las imágenes que se auditaban no eran las de la página** — cuatro
   fallos que se tapaban entre sí: un `<source>` de `<picture>` se registraba
   como imagen sin alt (no existe el atributo en `<source>`: el alt que cuenta
   es el del `<img>`), el placeholder `data:` de la carga diferida se guardaba
   en lugar de la imagen real —medido en 58 páginas de control: **142 `data:`
   URIs guardadas como imágenes y 140 imágenes reales que nadie veía**—, un
   `srcset` con un `data:` se partía en su coma interna, y la deduplicación se
   quedaba con la primera aparición aunque fuera la que no lleva alt. Además el
   contenido mixto no se detectaba con `HTTP://` en mayúsculas ni en `<video>`,
   `<audio>`, `link[rel=preload|icon]` o un `url()` en línea (0 → 3 en las
   mismas páginas), `width="100%"` se leía como 100 px, y un pixel de 1×1 —un
   contador, nunca contenido— salía como imagen sin alt.

53. **Las cabeceras de seguridad son del servidor, no de la página** — HSTS,
   CSP, X-Frame-Options y X-Content-Type-Options son la misma respuesta en las
   29.808 páginas del sitio y no afectan al posicionamiento, pero se emitían una
   vez por página: en los censos guardados, **131.096 avisos de
   `unsafe_crossorigin`, 93.545 de `missing_csp` y 41.863 de
   `missing_x_frame_options`**, tapando los hallazgos reales. Ahora va un aviso
   por HOST con el número de páginas en los detalles, y `unsafe_crossorigin`
   desaparece: desde 2021 todos los navegadores aplican `noopener` por defecto a
   `target="_blank"`, así que no había nada que arreglar. `http_url` y
   `mixed_content` siguen siendo por página, porque sí lo son. Y el extractor
   daba por bueno un HSTS con `max-age=0` (que le dice al navegador que OLVIDE la
   política) y un `X-Frame-Options` vacío, y pedía X-Frame-Options aunque la CSP
   ya llevara `frame-ancestors`.

54. **El título se corta por píxeles, no por caracteres** — Google trunca el
   título del resultado hacia los 580 px y el fragmento hacia los 985: 65 letras
   estrechas caben y 55 en mayúsculas no. Medido en tres censos, **8.801 títulos
   pasaban de 60 caracteres sin pasar del ancho en píxeles** —o sea, sin
   truncarse—, el 29% de los avisos de título; al contrario solo ocurría en 3
   páginas de más de 80.000. `title_too_long` y `description_too_long` van ahora
   por `*_pixel_width`, cayendo a caracteres en los rastreos que no lo midieron.
   Y la estimación de ancho estaba ciega fuera del latino: 9,6 px por carácter
   daba 202 px para un título japonés de 21 caracteres que mide unos 420, así que
   **ningún título CJK salía como truncado**. Hay tabla por rango Unicode
   (ideogramas y kana de ancho completo, hangul, tailandés, árabe, hebreo,
   emoji) y un emoji compuesto cuenta como un glifo.

55. **Los scripts de control mentían, y uno de ellos escribe en la base de
   datos** — `fix_h1_en_contenido.py` es el único script que **modifica**
   `page_content`, así que un fallo suyo no da un dato raro: corrompe el
   contenido guardado. Tenía tres: devolvía `0` donde el llamador hace `len(...)`,
   de modo que con `--todos` **un solo job sin h1 abortaba la pasada entera**;
   `_falta_titular` exigía una línea idéntica, así que «Hola mundo.» frente al h1
   «Hola mundo» —o el titular partido por un `<br>`— se daba por ausente y se
   anteponía **duplicado**; y al revertir se quitaba la primera línea de
   `content_text_original` aunque no se hubiera parcheado, **borrando un titular
   legítimo** (ahora el diario anota qué columnas se tocaron). 6 de los 8 tests
   nuevos fallan contra el código anterior.
   `check_js_templates.py`, que es quien decide `grafo_fiable` y por tanto si el
   PageRank es de fiar: los hosts no incluían la variante sin `www`, así que en
   un rastreo de `www.x.com` un enlace a `x.com` salía **externo** y no contaba;
   no se limpiaban los banners antes de comparar, así que el enlace que inyecta
   OneTrust o Cookiebot contaba como enlace escondido tras JavaScript y
   **cualquier sitio con gestor de consentimiento salía con el grafo no fiable**;
   el umbral era «mayor que cero», ahora pide el 10% del grafo de la plantilla o
   el caso que importa (cero enlaces en crudo y alguno al renderizar); `/tv/`
   pasaba por home y `/es/productos/galletas-cookies/` por página legal; y las
   reglas de producto estaban escritas para un cliente de hoteles, así que en
   cualquier otro sitio todo caía en «otras · N niveles». El repuesto ahora es la
   forma de la ruta (`shared/plantillas.py`, compartida con
   `check_content_quality.py`).
   `check_content_quality.py` comparaba **cifras incomparables**: filas de
   `links` con repeticiones contra un conjunto de URLs sin repetir, de modo que
   lo guardado salía siempre mayor y la alarma «se han perdido enlaces» no
   saltaba nunca — que es justo para lo que existe el script.
   `export_markdown.py` comparaba cada ruta con todas las demás (O(n²): 2.500
   millones de comparaciones con 50.000 URLs, 0,45 s medidos ya con 2.789) y
   exportaba 404 y noindex como si fueran contenido publicado.

56. **`rel` es una lista de tokens, y lo que Google ignora hay que decirlo** —
   el canonical, la paginación y el hreflang se buscaban comparando el atributo
   `rel` entero y en minúsculas, así que `rel="Canonical"`, `rel="canonical "` y
   `rel="alternate canonical"` —los tres válidos y los tres escritos por CMS
   reales— daban **None**: la página salía sin canonical y, con ella, sin
   `canonical_missing` tampoco donde debía. Ahora se compara por token entero
   (`canonicalize` no cuenta), `rel="Next"` y `rel="previous"` valen, y
   `http-equiv="REFresh"` también: ese se probaba con tres variantes escritas a
   mano, de modo que `extract_meta_refresh` decía que no había meta refresh
   mientras `extract_meta_refresh_target` sí veía el destino — las dos funciones
   contradiciéndose sobre la misma página.
   Lo que Google ignora ahora se reporta en vez de taparse: un canonical en el
   `<body>` (`canonical_in_body`) y **varios** canonicals (`canonical_multiple`,
   con los que Google los descarta todos y elige por su cuenta) se quedaban en
   «el primero manda» en silencio; hacen falta `html_meta.canonical_count` y
   `canonical_in_body` para poder verlo. Un canonical a `http` desde una página
   `https` es ahora `canonical_a_http`. Y al revés: de `www.x.com` a `x.com` ya
   no es «canonical a otro dominio» —es el mismo sitio resolviendo su variante,
   que es para lo que existe el canonical—, aunque en los tres censos medidos no
   había ni un caso, así que es corrección de criterio y no una cifra.
   El canonical de la cabecera `Link` se parseaba cogiendo el **primer** enlace
   de la cabecera fuera el que fuera: con `</style.css>; rel=preload, <…>;
   rel="canonical"` se guardaba la hoja de estilos como canonical de la página.
   Es la única forma de declarar canonical en un PDF.
   En hreflang: `X-Default` se marcaba como idioma inválido por la caja (lo
   escriben media docena de plugins de WordPress), `en-UK` pasaba por válido
   cuando el código de Reino Unido es `GB` —Google ignora la anotación entera y
   ese idioma se queda sin hreflang—, y un destino que no responde 200 generaba
   **dos** avisos del mismo hallazgo (`hreflang_broken_target` y
   `hreflang_missing_return`): medido, 282 + 38 + 44 páginas con los dos a la
   vez. Si el destino está roto, el retorno no se sabe; lo que hay que arreglar
   es el destino. `uk` como IDIOMA sigue siendo válido: es ucraniano.
   También: `name=" robots"` con un espacio delante no se detectaba, y un
   `<meta property="description">` —que es RDFa, no una meta description—
   contaba como la description de la página.

57. **Un enlace desde una página rota no enlaza nada** — un 404 o un 500 sale
   del índice y se lleva sus enlaces con él, así que lo que solo cuelga de ahí no
   está enlazado a efectos de buscador. Es el mismo criterio que la decisión 38
   aplicó a las `noindex`, y faltaba para los códigos de error. Medido en
   www.uoc.edu: **503 páginas que devuelven HTTP 500**, cada una con su menú
   completo, aportaban **264.991 enlaces** a 2.775 destinos, y **137 URLs tenían
   TODOS sus entrantes internos ahí** — huérfanas de hecho que salían enlazadas y
   por tanto no aparecían en el informe de huérfanas. En progym, 0 casos, que es
   lo que tiene que pasar en un sitio sano. Se aplica en `compute_link_counts`
   (el origen tiene que ser 200) y en `compute_pagerank` (una página que no
   responde 200 no entra como fuente, así que no reparte autoridad que no tiene).
   El modelo de nofollow (C2) y el de `noindex,follow` en el PageRank siguen
   siendo decisión abierta. Re-analizado el censo con el arreglo: **240.543
   enlaces entrantes menos** (el 6,7% del total) y **19 páginas más** que
   aparecen con cero entrantes; `orphan_page` se queda en 370 porque esas 19
   están declaradas en el sitemap y por eso van a `sitemap_orphan` (decisión 36:
   un aviso por hallazgo, y ese es el más fuerte de los dos).
   Lo mismo con `rel="ugc"` y `rel="sponsored"`, que Google documenta como la
   misma pista que `nofollow`: se leía solo `nofollow`, así que un enlace
   patrocinado sumaba autoridad. Medido en cuatro censos (28,4 M enlaces): 224
   con `ugc`, ninguno con `sponsored` y solo 12 internos — corrección de
   criterio, no de volumen.

58. **Un menú en un `<div class="main-menu">` no es la cabecera** — la
   auditoría midió que `link_position` fallaba en **13 de 24** plantillas
   realistas. Al escribir esos 24 casos como test salían ya **20/24** (los
   arreglos de octubre —tokens por palabra entera, camelCase, landmarks— habían
   resuelto la mayoría) y los cuatro que quedaban se arreglaron: el token `menu`
   y sus variantes entran en la lista de navegación —`main-menu`,
   `primary-menu`, `mobile-menu` y `menu-item` salían como **contenido**, y un
   menú contado como editorial es justo lo que hacía saltar
   `high_outlink_count` en todas las páginas—; `id="secondary"` es la barra
   lateral de los temas de WordPress, pero solo como **id**, porque como clase
   el `btn-secondary` de Bootstrap marcaría de barra lateral los enlaces de
   cualquier botón; y un `<aside>` dentro de `article`/`main` es contenido, la
   misma regla de landmark de las decisiones 9 y 45.
   Medido en las 58 páginas de control: `nav` 3.467 → 7.113 y `header` 3.772 →
   325, casi todo el movimiento siendo **header → nav** (3.447 enlaces del menú
   que vive dentro del `<header>`). Para el PageRank eso no cambia nada —`nav` y
   `header` pesan 0,25 los dos— y `content` se mueve 2 enlaces, así que
   `high_outlink_count` tampoco; lo que se arregla es la posición que se
   **exporta** y con la que se analiza el anchor text: un enlace de menú ahora
   dice que es de menú. Lo que sí cambia peso son los 199 enlaces que pasan de
   footer (0,15), sidebar (0,4) o content (1,0) a nav.
   Riesgo conocido y asumido, escrito en el test: el `class="menu"` de una carta
   de restaurante se clasifica como navegación. Equivocarse al otro lado afecta a
   todas las páginas del sitio; este caso, a una.

59. **Lo que la extensión ya dice no hace falta descargarlo** (R12 de #25) —
   el filtro por tipo de recurso necesita el `Content-Type`, así que se aplica
   **después** de la descarga: se pedía la imagen, se descargaba y se tiraba.
   Visto en vivo en el re-rastreo de progym: el sitio responde a **2,7 s de
   media** y, con 2 peticiones por dominio, **28 de cada 60 respuestas** eran
   recursos que se descargaban para descartarlos — el ritmo cayó de 50 a 10
   páginas por minuto y el contador de items de Scrapy se quedó clavado mientras
   el de páginas seguía subiendo, que es la firma de esto. Ahora, cuando la
   extensión identifica el tipo sin dudas y el job lo excluye, la petición no se
   hace.
   La mitad importante del arreglo es `tipo_por_extension()` devolviendo
   **None** cuando no sabe: la clasificación por extensión respondía `"other"`
   por defecto, así que cualquier URL con un punto en el último tramo
   —`producto-2.5-kg`, `v1.2-guia`— habría pasado por recurso y se habría dejado
   de pedir. Perder páginas de producto en silencio es mucho peor que descargar
   una imagen.

60. **Un `nofollow` no diluye, y está medido que da igual** (C2 de #24) — hoy
   los enlaces `nofollow` no entran en el grafo ni en el denominador, así que los
   `follow` de esa página se reparten el 100%. Es el *PageRank sculpting* que
   Google desactivó en 2009; su criterio sería que el `nofollow` consume su parte
   y se tira. Probados los dos modelos sobre los tres censos
   (`docs/experimentos/c2_nofollow.py`), incluido un e-commerce donde el **28,7%**
   de los enlaces internos son `nofollow`: el peso que se movería es del 0,9% al
   2,0%, el top 50 coincide en 49 de 50 y `pagerank_score` cambia **2-3 puntos
   sobre 100** como máximo, sin una sola página moviéndose 5. No se toca. Si
   alguna vez se toca, la masa perdida va al teletransporte, igual que la de las
   `noindex` (decisión 29 y C3).

61. **El alt de la imagen es el ancla del enlace de imagen** — Google lo
   documenta («if the link is an image, the alt text acts as the anchor text») y
   es el caso de la ficha de un listado, donde el enlace envuelve la foto y el
   nombre del producto está en el `alt`, y del logo que enlaza a la home. El
   respaldo del ancla llegaba hasta `aria-label`, `title` y la etiqueta del
   `<svg>` (decisión 46) y ahí se paraba. Medido sobre las mismas 58 páginas de
   control: **1.276 → 208 enlaces sin ancla**, y los 208 que quedan son iconos
   de redes sociales sin `alt`, sin `aria-label` y sin `title` — no los rotula
   nada. Contando solo los internos, **1.056 → 39**. Ninguna ancla que ya tenía
   texto cambia, y el `link_type` tampoco: un `<a><img></a>` sigue siendo
   `image` aunque su ancla salga del `alt`, porque el tipo describe el marcado.
   El `alt` va en último lugar a propósito: `aria-label` y `title` los escribe
   alguien para anunciar el ENLACE, el `alt` describe la IMAGEN.
   De paso, el mismo `alt` repetido deja de duplicarse: un enlace con el clon
   de móvil y el de escritorio de la misma imagen daba
   `"voto femenino voto femenino"` — 7 de las 58 páginas, y 504 valores de
   `links.alt_text` corregidos.

62. **El comprobador de contenido no puede dar la alarma cuando el bloqueado
   es él** (#30) — `check_content_quality.py` es la herramienta con la que se
   caza la pérdida de contenido después de un rastreo, y descargaba la página
   con `r.text` **sin mirar el código**. Un 403, o un muro de WAF que responde
   **200** con 5-6 kB de HTML y cero palabras de la página, se comparaba como si
   fuera la página: el informe decía «aquí se ha perdido todo el contenido»
   sobre un extractor que funciona. Lo tengo reproducido con progym, que desde
   mi IP contesta `Just a moment...` al mismo camino (curl_cffi + chrome124) que
   usa el script. Ahora un código distinto de 200, o un `<title>` de los nueve
   muros conocidos, es `NoEsLaPagina` y la muestra se descarta.
   Y lo que se descarta **se cuenta y se imprime**: antes, una plantilla cuyas
   muestras fallaban todas desaparecía de la tabla, y una tabla corta se lee
   como «aquí no hay problemas». Si no se pudo comprobar ni una, lo dice con
   todas las letras en vez de imprimir una tabla vacía. El filtro casa el
   título por subcadena pero solo contra muros reales: «Checklist de seguridad
   web» es un artículo y pasa, y una página sin `<title>` no se tira por eso.

64. **La severidad es la consecuencia, y un aviso que sale en todas las
   páginas deja de ser un aviso** — las dos salieron del mismo sitio: un
   informe entregado de 315.119 incidencias donde el hallazgo más grave del
   sitio estaba archivado como `info`.
   (a) **`canonical_cross_domain` se decide por dominio REGISTRABLE, no por
   host.** A otro subdominio de la misma casa (`comein.uoc.edu` desde
   `www.uoc.edu`) es consolidación de contenido: `info`. A otro dominio
   registrable la página se saca del índice en favor de un sitio que no es el
   suyo: **`error`**. Medido: en www.uoc.edu hay 3.551 canonicals a otro host y
   **3.550 se quedan dentro de uoc.edu** —con el criterio de host eran 3.411
   líneas de ruido—, mientras que en Lopesan los 2.367 iban a
   `*.lfr.cloud`, el servidor de origen de Liferay, y eran el **99,5% de su
   sección de hoteles**. Para eso `dominio_registrable()` se muda a
   `shared/dominios.py`: lo necesitan el spider (alcance del rastreo) y el
   analyzer (si un canonical se va de casa), y la imagen del análisis solo
   copia `shared/` y `analysis/` — el mismo motivo que la decisión 23.
   (b) **`image_missing_alt` va por IMAGEN, no por aparición.** La unidad de
   trabajo es la imagen: ponerle el alt al logo del pie se hace una vez, no
   10.990 —que es las veces que salía, repartido en cinco variantes responsive
   del MISMO logo—. Medido en cinco censos: **287.175 → 19.287 (−93%)** en el
   peor y entre −30% y −75% en los otros. No se pierde nada: la fila lleva
   `paginas_afectadas` y `resources` conserva todas las apariciones. Misma cura
   que la decisión 53 con las cabeceras.
   Efecto conjunto sobre el informe entregado de Lopesan: **315.119 → 38.922
   incidencias**, y los errores pasan a ser los cinco que hay que leer
   (4.716 de datos estructurados, 2.367 de canonical, 32 4xx, 12 hreflang
   rotos, 4 canonical rotos). **Las notas de `/insights` no se mueven** (88 y
   77 antes y después): esto no cambia la valoración, cambia qué se ve.

65. **El backup decía «stream» y montaba el censo entero en memoria, y los
   duplicados se guardaban al cuadrado** — las dos salieron de lo mismo:
   intentar traerme un censo de producción para compararlo y que no cupiera.
   (a) `stream_backup_zip` acumulaba **cada tabla entera** en una lista de
   cadenas, la unía en otra cadena y lo metía todo en un `BytesIO` que solo se
   soltaba al final, con un único `yield`. Ahora cada fila se escribe
   directamente en la entrada del ZIP (`zf.open(name, "w")`) sobre un destino
   **sin `seek`** —así `zipfile` escribe descriptores de datos y el ZIP se
   puede servir mientras se genera— y se suelta un trozo cada MB.
   (b) Lo que de verdad pesaba no era el ZIP: cada incidencia de duplicado
   guardaba `duplicate_urls` con **la lista de TODAS las demás del grupo**, o
   sea N×N identificadores. Medido en el censo de CST, donde **7.511 páginas
   comparten la misma description**: esa sola clase ocupaba **431 MB** de la
   columna `details` (9 kB por fila, la mayor de 59 kB), frente a 8, 7 y 0,8 MB
   en censos sin un grupo gigante. Y **no lo leía nadie**: cuatro sitios lo
   escribían y ninguno lo consumía. Ahora va la cuenta real
   (`duplicate_count`), una muestra de 20 y una marca si está recortada; el
   grupo entero se saca con una consulta por el valor compartido.
   Medido, pico de memoria del proceso que exporta:

   | censo | antes | ahora |
   |---|---|---|
   | Saunier Duval (2.876 URLs) | 264 MB | **97 MB** |
   | CST (12.112 URLs) | 1.450 MB | **92 MB** |

   Y la columna `details` de CST: **434 MB → 4,3 MB** con las mismas 49.207
   incidencias. Lo importante del segundo par de cifras es que **ya no crece
   con el censo**: 97 MB con 2.876 URLs y 92 MB con 12.112. El ZIP sale **byte
   a byte idéntico** en los dos censos (comprobado entrada por entrada), así
   que es la misma copia hecha de otra manera.

66. **Cada rastreo queda sellado con la versión que lo hizo** — comparar dos
   censos del mismo sitio solo significa algo si se sabe si entre medias cambió
   **el sitio** o cambiamos **nosotros**, y con lo que se guardaba no había
   forma de contestarlo. Me pasó dos veces el mismo día:
   - el grafo de Druni bajó de 37,5 a 32,1 millones de aristas entre dos
     medidas, y parecía efecto de una optimización mía; era que
     `analyze_indexability` había materializado **9.771 páginas noindex** que
     antes repartían autoridad. Costó repetir una medición de 30 minutos
     descartarlo;
   - el censo de CST dio **88.838 «errores» de datos estructurados** que eran
     basura guardada por el extractor de junio, no un sitio roto.
   Ahora `jobs.crawler_version` guarda el SHA del commit: el `ARG` del
   Dockerfile lo recibe al construir (`--build-arg`, que el despliegue rellena
   con `git rev-parse --short HEAD`), queda en el entorno de la imagen y el
   worker lo escribe al marcar el job como `running`. En local, sin argumento,
   queda **`dev`** y no una cadena vacía que parezca una versión. Comprobado de
   extremo a extremo: imagen construida con un valor de prueba → el código lo
   reporta.
   Es la decisión 13 llevada a su conclusión («antes de atribuir una diferencia
   a un cambio del crawler, repetirla con la configuración vieja»): para eso
   hay que saber cuál era la configuración vieja **y cuál era el código viejo**.
   Y hay un caso que el sello no cubre y conviene recordar: **un re-análisis
   arregla lo que decide el análisis, no lo que el extractor guardó mal**, así
   que una comparación debe distinguir «re-analizado» de «re-rastreado».

67. **Comparar dos censos: lo que no se puede afirmar pesa más que lo que
   sí** (#32) — `shared/comparacion.py` responde «qué cambió entre estos dos
   rastreos del mismo sitio», y vive en `shared/` porque lo necesitan la API
   (que no lleva `analysis/` en su imagen) y el worker. Lo interesante son las
   cuatro cosas que se **niega** a decir:
   - **Un censo truncado no permite afirmar ausencias.** Si uno paró por
     `max_urls`, por tiempo o estancado, «han desaparecido 4.000 URLs» es
     falso: no se llegó a ellas. El resultado se marca no concluyente y las
     ausencias se cuentan aparte, sin afirmarlas.
   - **Dos orígenes distintos no se comparan**, y la comprobación es por HOST
     y no por dominio registrable: `pre.x.com` y `x.com` comparten dominio
     pero no casa ni una URL, así que el resultado sería «ha desaparecido el
     sitio entero». Con `map_host=pre.x.com=x.com` sí, porque entonces es
     explícito. Comprobado contra datos reales: se niega a comparar el censo
     de Saunier con el de Lopesan.
   - **Si no se sabe con qué versión se hizo alguno de los dos, se dice.**
     Desconocido no es igual: callarse equivale a afirmar que se hicieron con
     el mismo código. Medido con dos censos reales de progym anteriores al
     sello (decisión 66): **439 páginas cambian su recuento de palabras y el
     cambio es NUESTRO** —la extracción de contenido de M1—, no del sitio.
   - **La barra final SÍ hace dos URLs.** `/a` y `/a/` pueden servir cosas
     distintas y normalmente una redirige a la otra; emparejarlas taparía que
     un sitio ha cambiado de convención. El orden de los parámetros no, que
     eso sí es la misma URL.
   Compara `status_code`, `indexability_status`, `canonical_href`, `title`,
   el primer h1 que se pinta y `word_count` (con un umbral del 20%, que por
   debajo es ruido de plantilla). Solo páginas HTML: el `title` de un PDF no
   dice nada. Endpoint `GET /api/jobs/{id}/diff/{otro_id}` y pestaña
   **Comparar** en la ficha del rastreo, que solo ofrece rastreos completados
   del mismo host.
   El umbral de palabras se mide sobre el **mayor** de los dos valores, no
   sobre el «antes»: si no, comparar A con B y B con A da resultados
   distintos. Lo vi usando la vista con dos censos reales de progym —1.639
   páginas en un sentido y 439 en el otro, con los mismos datos—, y pasar de
   100 a 130 palabras es el mismo cambio que de 130 a 100.
   «Antes» y «ahora» los pone la **fecha**, no el orden de los parámetros: una
   página que sale del índice y una que entra son hallazgos opuestos, y el
   endpoint los rotulaba por el orden de los argumentos, así que elegir el
   censo viejo en el desplegable daba la vuelta al informe entero.

68. **Un cambio que afecta a una parte grande de las indexables no es trabajo
   editorial** — es una plantilla, una configuración o un despliegue, y tiene
   que verse como una alerta, no como una fila más del diff. En Lopesan, 2.367
   páginas canonicalizadas a `webserver-lopesan-prd.lfr.cloud` se entregaron
   como un aviso `info` entre 315.119 incidencias, el 91% de ellas
   `image_missing_alt`. `shared/comparacion.py` emite nueve reglas, cada una
   con **qué decide Google** (regla 0) y con su denominador: «2.367 páginas» no
   dice nada sin «de 2.379», y el conjunto no es el mismo para todas (una
   página que ENTRA en el índice se cuenta sobre las que no estaban).
   Umbral: 5% de las indexables con un piso de 20 páginas —en un censo de 30
   URLs «el 10%» son tres— **o** el 50% de una forma de ruta, porque una
   plantilla entera rota se diluye en el total: las 2.367 de Lopesan son el
   99,5% de su plantilla y el 11% del sitio. Un aviso por hallazgo (decisión
   36): la página que pierde el índice *porque* le pusieron un canonical fuera
   no se cuenta además en `salen_del_indice`.
   Medido contra seis parejas de censos reales. El control —Lopesan 8-sep
   contra 9-sep, un día de diferencia— **no dispara nada**, y Saunier con un
   mes de diferencia tampoco; el fallo real dispara `canonical_a_otro_host`
   con **2.319 de 4.193 (55,3%)** y plantillas enteras al 100%.
   `canonical_a_otro_host` es la única que NO exige que la página fuera
   indexable: lo que abandona el sitio es el **destino** de la consolidación.
   De las 2.367 de Lopesan, **1.832 ya estaban canonicalizadas** a una URL
   legítima, así que la regla de «era indexable y deja de serlo» las descartaba
   y la alerta decía 476 de 2.271 (21%) en vez de 55,3%.

69. **Una dependencia que falta no puede reaparecer como otro criterio de SEO**
   — `_norm` tenía un `except` que caía a `url.strip().rstrip("/")` y la imagen
   de la API **no llevaba `w3lib`**: el endpoint de comparación emparejaba las
   URLs con el criterio CONTRARIO al documentado en los dos casos que importan
   —`?a=1&b=2` dejaba de casar con `?b=2&a=1`, y `/a/` sí casaba con `/a`— sin
   que nada lo dijera. Lo mismo en `dominio_registrable`, cuyo `except` volvía
   a las dos últimas etiquetas, que es exactamente la regla que la decisión 49
   descartó; y **ninguna de las tres imágenes declaraba `tldextract`**: el
   crawler lo heredaba de Scrapy, `analysis/` no lo tenía y la API tampoco. Los
   dos respaldos fuera, las dos dependencias declaradas donde se usan. CI
   tampoco lo habría cazado, porque `test_comparacion.py` solo corría en el
   trabajo del crawler, que sí lleva w3lib: ahora corre también en el de la
   API, que es donde vive el endpoint.

70. **Dos rastreos del mismo host pueden ser dos censos distintos** — el
   guardia de #32 mira el host (decisión 3) y los dos censos de cst.gov.sa del
   mismo día lo pasaban: uno sembrado en el árbol castellano y otro en el
   inglés, **0 semillas en común de 4.780**. La comparación afirmaba que
   **habían desaparecido 834 páginas** que nunca estuvieron en el alcance.
   Las ausencias solo se afirman si **cada semilla del censo anterior está
   también en el posterior**. No es un umbral, es una contención, y separa los
   casos sin ajustar nada: Lopesan 2.935/2.935, el canario de penguin
   1.950/1.950, CST 0/4.780. La Jaccard no sirve aquí — el canario de penguin
   da 0,022 contra su censo completo y es un subconjunto limpio. Las semillas
   viajan al comparador pero no a la respuesta (87.429 en penguin): queda
   `n_semillas`. Y si los dos censos difieren en `render_js`, un aviso: el que
   renderiza ve enlaces que el otro no, que es lo que mide `js_check`
   (decisión 35). Eso último no está medido —ninguna pareja de censos difiere
   en el render— y sale de ese mecanismo, no de una cifra.

71. **Una alerta a la que hay que ir no es una alerta** (#36) — la comparación
   de #32 solo existía si alguien entraba en la pestaña y elegía dos censos en
   un desplegable. Nadie hace eso para enterarse de que media sección se ha ido
   del índice: en Lopesan pasó y se entregó como un aviso `info` entre 315.119
   incidencias. Ahora, al cerrar un rastreo, el worker lo compara con el censo
   anterior del mismo sitio (`_comparar_con_el_censo_anterior`, junto a
   `_comprobar_render_js`), guarda el veredicto en `jobs.comparacion` y lo pinta
   **arriba del todo** en la ficha del rastreo, antes de las cifras. Las alertas
   críticas suben además al log del worker.
   Es best-effort a propósito: si falla, el job NO se marca fallido. Lo
   rastreado y lo analizado valen igual, y la comparación se puede pedir
   después por el endpoint; lo contrario sería dejar en `failed` un censo
   correcto por no poder compararse con otro.
   El censo de referencia se busca **en lotes, de más reciente a más antiguo**,
   no con un `limit`: con un tope fijo, en una instalación con varios clientes
   los rastreos de los demás se comen la ventana y el censo anterior de ESE
   sitio queda fuera — y la ausencia de alerta se lee igual que «no ha cambiado
   nada». El host no se puede filtrar en SQL sin atarse a Postgres, porque
   `seeds` es JSON y la primera semilla no es una columna.
   Y las dos consultas que leen un censo (`paginas_de_censo`, `resumen_de_job`)
   se mudan del router a `shared/comparacion.py`: las necesitan el endpoint y
   el worker, y tenerlas por duplicado es como se llega a dos cifras que dicen
   medir lo mismo y no coinciden (decisiones 23 y 39).

72. **Con qué se rastreó y con qué se ANALIZÓ son dos preguntas** — la
   decisión 66 selló el rastreo y dejó abierto el otro lado, que es el que más
   muerde: **un re-análisis cambia las cifras de un censo sin que cambie el
   sitio ni el rastreo**. Medido en penguin, un censo de julio con **959.633
   incidencias** entregadas, re-analizado con el código de octubre. Comparar
   dos censos analizados con código distinto sin decirlo atribuye al cliente un
   cambio que es nuestro. `jobs.analisis_version` lo sella al terminar
   `run_all`, y la comparación avisa cuando difieren.
   La constante se muda a `shared/version.py` porque ahora la escriben los dos
   —el worker al marcar `running`, el analizador al cerrar su pasada— y dos
   copias de «qué versión soy» pueden decir cosas distintas, que es justo lo
   que estas columnas existen para evitar. Solo se avisa de la diferencia
   cuando se CONOCEN las dos: el desconocido ya lo cubre el aviso del rastreo.

73. **Los datos estructurados también son un aviso por hallazgo** — el mismo
   criterio de la decisión 64b con las imágenes y la 53 con las cabeceras, que
   faltaba aquí. Un bloque de plantilla —el `Organization` del pie, la miga de
   pan— es el MISMO en todo el sitio y se arregla una vez, y se emitía una
   incidencia por bloque. Medido en tres censos, corriendo el código nuevo
   sobre los datos guardados en producción y deshaciendo la escritura:

   | censo | bloques | incidencias antes | después |
   |---|---|---|---|
   | penguin (87.531 páginas) | 377.171 | **226.651** | **4** |
   | Lopesan | 45.774 | 11.186 | **13** |
   | Saunier Duval | 1.685 | 288 | **7** |

   En penguin esas 226.651 filas eran el **39% de las incidencias del censo
   entero** diciendo cuatro cosas: 84.305 páginas con un `Organization` sin
   `sameAs`, 83.764 con uno sin `name`, 58.552 `Product` sin `brand` y 30
   `Review` sin `itemReviewed`. No se pierde nada: cada fila lleva
   `paginas_afectadas` y cinco URLs de ejemplo, y `structured_data` conserva
   todos los bloques con su validación.
   De paso, la validación se escribía con **un UPDATE por bloque**: 226.651
   sentencias para grabar cuatro valores distintos. Ahora va por lotes
   agrupados por veredicto, y el chequeo entero tarda **33,9 s** en ese censo.
   Lo mismo en `analyze_hreflang`, que lo hacía igual y encima escribía DOS
   veces la misma fila cuando el destino estaba roto (la segunda poniendo
   `return_tag_ok` a NULL, que es lo que ya decide la decisión 56). Penguin
   tiene 438.957 anotaciones y de ahí salen seis veredictos. Medido sobre los
   mismos datos: **284,9 s → 20,3 s, catorce veces**, con los veredictos
   IDÉNTICOS (42 / 5.743 / 433.172) y las mismas incidencias — que es lo que
   lo convierte en una optimización y no en un cambio de criterio disfrazado.

74. **Un test que lee el fuente comprueba que escribiste algo, no que
   funcione** — me pasó DOS VECES la misma tarde, con el mismo final: el
   analizador escribía `analisis_version` con `Job` sin importar
   (`NameError`), y el worker montaba su sesión con `SessionLocal` sin
   importar (`NameError`). Los dos métodos importan sus dependencias **dentro**
   de la función, como el resto de esos ficheros, y a los dos se me olvidó. Los
   dos tenían test. Los dos pasaban: uno comprobaba que la cadena
   `analisis_version` aparecía en el fuente del método, el otro el ORDEN de las
   llamadas con `inspect`. El segundo llegó a producción y lo cazó el primer
   rastreo que lo ejecutó.
   La regla no es «nunca mires el fuente»: los tres `inspect.getsource` que
   quedan comprueban **forma** que no se puede observar ejecutando —que el SQL
   de `analyze_links` no una con `urls` (decisión 37, y en SQLite no hay plan
   que medir), que no haya vuelto el respaldo silencioso de
   `dominio_registrable` (69), que la comparación vaya después del estado
   final (71)—. La regla es que **mirar el fuente no puede sustituir a
   ejecutar**: esas tres van ahora acompañadas de un test que llama a la
   función de verdad contra SQLite y lee lo que quedó escrito.
   Y el `try` tiene que cubrir la función ENTERA. El fallo de producción
   ocurrió en la línea que montaba la sesión, que estaba fuera, así que el
   «best-effort» no cubría precisamente la línea que falló.

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

## Al operar: reconstruir el contenedor mata el rastreo en marcha

`docker compose up -d --build crawler` recrea el contenedor, y con el se va el
subproceso de Scrapy del rastreo que estuviera corriendo **y el directorio de
logs** (`/tmp/scrapy-logs`, que vive dentro). El job se queda en `running` sin
nadie detras hasta que el vigilante lo recupera por latido viejo
(`STALE_JOB_MINUTES`, 30 por defecto).

Y ojo, que eso **no era verdad hasta el 2026-10-08**: `_recover_stale_jobs`
corria UNA sola vez, al arrancar el worker, asi que no cubria el caso que mas
lo necesita. Si el contenedor se reinicia DENTRO de los primeros 30 minutos de
un rastreo —justo cuando lo pilla un despliegue—, el job recien empezado no
llega al umbral, no es candidato, y como nadie vuelve a mirar se queda
huerfano **para siempre**. Medido en carne propia: un rastreo lanzado a las
22:22:01 y un despliegue que recreo el contenedor a las 22:22:39 dejaron el
job colgado **7 h 40 min con 39 URLs**, sin proceso de Scrapy y sin que el
vigilante lo tocara. Ahora pasa cada `RECOVERY_INTERVAL_SECONDS` (300) dentro
del bucle.

**Y antes de lanzar un rastreo, mirar que no haya un despliegue en vuelo**, no
solo antes de bajar un censo: `gh run list --workflow=deploy.yml --limit 1`.
Mergear una PR y lanzar un rastreo en el mismo minuto es matarlo.

Pasa con cualquier cambio de codigo que obligue a reconstruir —una migracion de
esquema, por ejemplo— y es facil confundirlo con un bloqueo del sitio: un
canario que se para en seco a las 199 URLs parece un WAF y era esto. Antes de
reconstruir, mirar si hay algo rastreando:

```bash
curl -s "$API/api/jobs?status=running" | python -c "import json,sys;print([j['name'] for j in json.load(sys.stdin)['items']])"
```

Y lo mismo vale para cualquier cosa larga contra la API: **mergear una PR
dispara un despliegue, y el despliegue recrea el contenedor de la API**, asi
que se lleva por delante la descarga que estuviera en curso. Medido el
2026-10-07: el backup del censo de CST murio con `curl 92` (error de framing
HTTP/2) **once segundos antes** de que terminara el despliegue de un merge
hecho mientras tanto. No fue memoria ni un fallo del endpoint; fue el
despliegue. Antes de bajar un censo grande, mirar que no haya un merge en
vuelo (`gh run list --workflow=deploy.yml --limit 1`).

Y **nunca** copiar ficheros al contenedor que esta rastreando (`docker cp`): si
el spider y los extractores quedan descasados, la siguiente reanudacion del job
muere con un ImportError. Para probar codigo nuevo contra el sitio de pruebas o
contra un script, levantar un contenedor efimero con el repo montado, que no
toca al que trabaja:

```bash
docker run --rm --network host -v "$PWD":/repo -w /repo \
  -e PYTHONPATH=/repo:/repo/crawler:/repo/tests \
  crawler-masivo-crawler bash -lc "pip install -q pytest; python -m pytest /repo/tests -q"
```

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
| `TITLE_MAX_LEN` | 60 (solo si no hay ancho en pixeles medido) |
| `TITLE_MAX_PIXELS` | 580 |
| `DESCRIPTION_MAX_PIXELS` | 985 |
| `DESCRIPTION_MIN_LEN` | 50 |
| `DESCRIPTION_MAX_LEN` | 160 |

Umbral de casi duplicados: `job.config.analysis_thresholds.near_duplicate_similarity`
(por defecto 0.9; ver decisiones 16-18).

Additional thresholds in `analyzer.py`: `LOW_WORD_COUNT_THRESHOLD=200`, `URL_MAX_LENGTH=115`, `HIGH_OUTLINK_THRESHOLD=100`.

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
| **`ROADMAP.md`** | Funcionalidades hechas y en cola, en lenguaje no técnico, con enlace a la issue de cada punto. Actualizarlo al cerrar o abrir una funcionalidad. |
| **`SEO_CRAWLER_DB.md`** | Complete database schema, connection strings, example SQL queries, relationship diagram. Use this for DB access from external tools. |
| **`modelo-de-datos.md`** | Original data model design — tables, calculations, Screaming Frog tab mapping. Design spec that guided implementation. |
| **`stack-de-ingenieria.md`** | Engineering stack design doc — architecture decisions, component breakdown, scaling strategy, monitoring plan. |
| **`millones-de-URL.md`** | Guide for scaling to millions of URLs — distributed crawling patterns (scrapy-redis, Scrapy Cluster), memory management, BFS tuning. |
| **`screaming_frog_complete_fields_reference.md`** | Complete Screaming Frog fields reference (900+ lines) — all tabs, columns, filters, bulk exports. Feature parity checklist. |

## Testing

Unit test suite at `tests/` (**480 casos**: 466 en la imagen del crawler y 14
mas —`test_insights.py`, `test_export_csv.py` y `test_raw_html.py`— que
necesitan FastAPI y se corren en la de la API). Los 466 incluyen 32 que SOLO
corren con servicios de verdad: `test_pagerank_db.py` (27, Postgres) y el de la
cola contra Redis. Para destaparlos en local:

```bash
docker compose exec -T postgres createdb -U crawler crawler_test
docker run --rm --network crawler-masivo_default -v "$PWD":/repo -w /repo \
  -e PYTHONPATH=/repo:/repo/crawler:/repo/tests \
  -e PAGERANK_TEST_DATABASE_URL=postgresql+psycopg2://crawler:crawler@postgres:5432/crawler_test \
  -e REDIS_TEST_URL=redis://redis:6379/9 \
  crawler-masivo-crawler bash -lc "pip install -q pytest; python -m pytest /repo/tests -q"
```

Sin la red de compose y los nombres de servicio no valen: apuntar
`REDIS_TEST_URL` a `127.0.0.1` desde otro contenedor hace fallar el test de la
cola, y parece un fallo del codigo. Lo que cubren: pure extractors
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
   audit branch (`claude/crawler-export-issues-oi77bm`, PR #5). **Ya está
   pasado**: el 2026-09-11 contra dos censos reales de produccion (Saunier
   Duval con `render_js` y Lopesan sin JS) en vez de crawls de prueba, y el
   ultimo punto que quedaba —8b.6, la propagacion del `nofollow` de pagina a
   sus enlaces— se cerro el 2026-10-07 con el censo de progym: 1.157 paginas
   con `noindex,nofollow` en sintaxis valida y sus 267.063 enlaces en
   `follow=false`, sin tocar los del resto del sitio. Lo unico abierto de esa
   seccion es `http_version`, que solo se rellena con `render_js` y esta en las
   limitaciones conocidas. No hay que volver a pasarlo.
2. **Impact diagnosis for pre-fix crawls (section 10, D1–D7)**: tambien
   **ejecutado** (2026-09-11, sobre todos los jobs de mas de 50 URLs), con el
   resultado ya escrito ahi. Lo que hay que saber sin abrir el documento: los
   canonicals relativos —el bug que parecia el peor— **no contaminaron ningun
   censo** (0 en todos), asi que las indexabilidades viejas se pueden mirar sin
   sospecha. El que si mordio es el dedup de inlinks: **todo lo anterior al
   2026-09-08** (CST, Salle, penguin y los tres Lopesan de julio) tiene
   `inlinks_count == unique_inlinks_count` en todas sus paginas, con los
   enlaces infravalorados y **el PageRank calculado sobre un grafo
   incompleto**. De esos censos, titulos, metas, contenido y codigos de estado
   valen tal cual; cualquier metrica de enlazado interno o PageRank exige
   re-rastrear. Las consultas D1-D7 estan ahi para medir un job concreto.
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
