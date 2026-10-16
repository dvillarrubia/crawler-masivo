# API, mapa de ficheros y extraccion

> Movido tal cual desde `CLAUDE.md` (2026-10-14).

## File Map

### API (`api/`)
| File | Description |
|------|-------------|
| `main.py` | FastAPI app, lifespan, CORS, static files, SPA fallback |
| `schemas.py` | Pydantic v2 request/response models |
| `dependencies.py` | Shared Redis client + DB session dependency |
| `routers/jobs.py` | Job CRUD endpoints (POST, GET, PATCH cancel, DELETE) |
| `routers/results.py` | Results endpoints (urls, issues, links, stats, export CSV) |
| `routers/clientes.py` | Clientes, perfiles e integraciones (#36, decision 83). Resuelve la herencia de configuracion al crear un job (`resolver_config`) |
| `routers/almacenamiento.py` | Lo que ocupa la base, cada tabla y los Parquet de enlaces archivados (decision 96). Router propio: en el de jobs la ruta caeria debajo de `GET /{job_id}` |
| `routers/extraccion.py` | Extraccion y busqueda a medida (#34, decision 104): probar reglas sin red (`POST /api/extraccion/probar`), resumen y distribucion de valores de un censo |
| `routers/enriquecido.py` | Analisis enriquecido (#40, decision 85): estimar, crear (con `X-Enrichment-Token`), leer y cancelar ejecuciones. No llama a ningun modelo: encola en `enrichment:pending` |

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
| `clientes.py` | Slug, fusion profunda de configuracion (`fusionar_config`), cliente a partir del `client_id` de texto y la migracion de esos textos (decision 83) |
| `credenciales.py` | `credencial(integracion)`: el secreto de una integracion, leido del ENTORNO (nunca de la BD) y solo dentro del prefijo de su tipo (decision 83) |
| `llm.py` | Transporte UNICO hacia OpenRouter (hoy Jev por `/systemone`): asincrono (httpx), ritmo por minuto con AIMD ante 429, reintentos, corte ante 402/401/404, circuito abierto y `usage.cost` (decision 84) |
| `decisiones.py` | `CapaDecisiones.preguntar(estados, preguntas)`: cache (memoria, Redis, tabla `decisions`), agrupacion por estado, presupuesto (ejecucion, mensual del cliente, global), degradacion a `sin_decidir` y observabilidad por job (decision 84). Solo la dispara `enriquecido.py` |
| `enriquecido.py` | Analisis enriquecido bajo demanda (#40, decision 85): registro de modulos con dependencias, alcance, `estimar()` sin llamar a nadie y `ejecutar()` por lotes en el worker (corte por presupuesto, cancelar, reanudar) |
| `preguntas/` | Catalogo versionado de preguntas (`Pregunta`: nombre, tipo, instrucciones, opciones, version, modelo fijado). Solo una de ejemplo: el catalogo es #9 y la evaluacion #8 |
| `almacenamiento.py` | Cuanto ocupa un censo y cuanto ocupa la base (decision 96). Estimacion por filas x bytes/fila; los enlaces se cuentan por censo y con presupuesto, lo que no se puede medir viene a `None`, y un censo archivado se mide por su Parquet (`archivo_bytes`, aparte) |
| `extraccion_a_medida.py` | Extraccion y busqueda a medida (decision 104): `validar`, `compilar`, `aplicar`, `firma`, `a_numero` y el `Presupuesto` con cortacircuitos. La usan el spider y la API |
| `texto_visible.py` | Texto que el navegador pinta, linea a linea (`_lineas_visibles`, `_nodo_oculto`...). Mudado desde `extractors.py`, que lo reexporta, para que la API lo comparta (decision 104) |
| `robots.py` | Lectura de directivas robots — tokenizado y detección de separadores inválidos. En `shared/` porque el analyzer y el extractor deben leerlas igual |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/jobs` | Create crawl job |
| GET | `/api/jobs` | List jobs (`?status=`, `?client_id=` —UUID, slug o el texto viejo—, `?profile_id=`, `?page=`) |
| GET/POST | `/api/clients` | Listar / crear clientes |
| GET/PATCH/DELETE | `/api/clients/{id}` | Ficha, edicion y borrado (`?confirmar=<slug>`, `?con_jobs=`) |
| GET/POST | `/api/clients/{id}/profiles` | Perfiles de rastreo del cliente |
| GET/PATCH/DELETE | `/api/clients/{id}/profiles/{pid}` | Un perfil |
| GET | `/api/clients/{id}/config-resuelta` | Lo que heredaria un job (`?profile_id=`), para precargar el modal |
| GET | `/api/enrichment/modulos` | Modulos del analisis enriquecido y si lanzar esta activado (decision 85) |
| POST | `/api/jobs/{id}/enrichment/estimate` | Llamadas tras la cache, tokens, coste, tiempo y gasto del mes del cliente. No gasta nada |
| POST | `/api/jobs/{id}/enrichment` | Crea y encola una ejecucion. **Exige `X-Enrichment-Token`**; sin `ENRICHMENT_TOKEN` en el entorno, 403 |
| GET | `/api/jobs/{id}/enrichment[/{run_id}]` | Ejecuciones del job / una con su progreso, coste y resultados por pregunta |
| GET | `/api/jobs/{id}/enrichment/{run_id}/decisions` | Respuestas URL a URL (`?pregunta=`, `?respuesta=`, `?page=`) |
| PATCH | `/api/jobs/{id}/enrichment/{run_id}/cancel` | Cancela (pendiente: en el acto; en curso: entre lotes, conservando lo pagado) |
| GET | `/api/jobs/{id}` | Get job details |
| PATCH | `/api/jobs/{id}/cancel` | Cancel job |
| DELETE | `/api/jobs/{id}` | Delete job + cascade, y el Parquet de sus enlaces si estaban archivados (decision 95) |
| POST | `/api/jobs/eliminar` | Borrado en masa (decision 96). `{ids, simular, forzar, segundos}` (`forzar` salta solo el guardia de la serie programada, nunca el de un rastreo en marcha): `simular` es la vista previa con tamanos, guardias y `archivo_bytes`; sin el, borra (Parquet incluido) y devuelve `pendientes` con lo que no cupo en su presupuesto de tiempo; lo que queda a medias pasa a `status='deleting'` |
| GET | `/api/almacenamiento` | Lo que ocupa la base y cada tabla, mas los Parquet archivados (`archivo_bytes`, `archivo_ficheros`), para decidir que censos borrar |
| GET | `/api/jobs/{id}/progress` | Real-time progress from Redis |
| GET | `/api/jobs/{id}/urls` | Crawled URLs (`?status_group=`, `?is_internal=`, `?resource_type=`, `?issue_type=` —uno o varios, por comas—, `?severity=`, `?in_sitemap=`, `?vacio=`/`?con_valor=` campos, `<col>_gte/_lte`, `<campo>_contains`, `?ligero=true` sin `page_content`; columnas calculadas con `?origenes=`, `?h1=` y `?cambios=` —frente al censo de la comparación automática o `?con=`—). Los «contiene» admiten `a|b` y `!a`; `status_code` admite `301,302`. El filtrado vive en `_urls_filtradas`, compartido con el CSV de la vista. Reglas a medida (decision 104): `x.<n>_gte/_lte`, `x.<n>_contains` (`!a` solo sobre lo evaluado), `x.<n>=si\|no`, `x.<n>_estado=`, `vacio=x.<n>`, `sort_by=x.<n>` y `?a_medida=n1,n2\|todas` para añadir los valores; una regla que el censo no tiene es 422 |
| GET | `/api/jobs/{id}/urls/plantillas` | La vista del Explorador agrupada por forma de ruta (`shared.plantillas.firma_de_ruta`): URLs, indexables, estados, con errores, medianas y los últimos tramos que se repiten (`null` × 6 = un fallo de plantilla). `/urls?plantilla=` entra en una |
| GET | `/api/jobs/{id}/urls/enlaces-entrantes` | CSV con todos los enlaces que apuntan a las URLs de la vista (destino, origen, ancla, posición, follow). `/urls?origenes=true` añade a cada fila `enlazada_desde` y `origenes` |
| GET/POST/DELETE | `/api/clients/{id}/vistas[/{vista_id}]` | Vistas guardadas del Explorador (`clients.vistas_explorador`): la consulta de la URL con nombre, válida en cualquier censo del cliente |
| GET | `/api/uso/explorador` | Qué pestañas, filtros, órdenes, problemas y columnas se piden en el Explorador (`?dias=`). Solo nombres, nunca valores (`api/uso.py`) |
| GET | `/api/jobs/{id}/urls/export` | CSV de la vista del Explorador: mismos filtros y orden que `/urls`, solo las columnas de `?columnas=` (claves de `COLUMNAS_VISTA` y `x.<nombre>`: una columna por posicion con `cuantos=todos`, formulas escapadas) |
| POST | `/api/extraccion/probar` | Prueba reglas a medida (decision 104) contra `html` pegado (5 MB como mucho, 413) o el `raw_html` de `job_id` + `url_id` (409 si no se guardo). Hereda con `cliente_id`/`profile_id`. Sin red. Dos a la vez como mucho (429; `CUSTOM_EXTRACTION_PRUEBAS_A_LA_VEZ`). Devuelve por regla estado, n, valor(es), valor_num, ms y avisos de coste |
| GET | `/api/jobs/{id}/extraccion` | Por regla: definicion, firma, evaluadas, por estado, renderizadas y los 5 valores mas frecuentes; avisa si un extractor no encontro nada |
| GET | `/api/jobs/{id}/extraccion/{nombre}/valores` | Distribucion paginada de `valor_clave` |
| GET | `/api/jobs/{id}/issues` | SEO issues (`?severity=`, `?issue_type=`), por gravedad y tipo |
| GET | `/api/jobs/{id}/issues/resumen` | Una fila por tipo: incidencias, páginas distintas y ejemplos (vista agrupada de Problemas) |
| GET | `/api/jobs/{id}/links` | Link graph |
| GET | `/api/jobs/{id}/stats` | Aggregated stats |
| GET | `/api/jobs/{id}/urls/{url_id}/raw-html` | HTML guardado de una URL (solo con `extraction.store_raw_html`). Endpoint aparte y no un campo del detalle: 170 kB de media por pagina |
| GET | `/api/jobs/{id}/export` | CSV export (streaming, 1000-row windows) — **83 columnas**: URL + metadatos + `click_depth` (clics desde la home, decision 90) + **h1/h2** (solo los que se pintan), og/twitter, hreflang, tipos de datos estructurados, imagenes sin alt y cabeceras de seguridad. Las de M1: `content_word_count`, `content_hash`, `pagerank_score` y `pagerank_raw` (la 0-100 es la que hay que leer y ordenar, decision 34), `pagerank_fiable` (decision 35), `blocked_by_robots` (decision 48, con `status_code` VACIO porque la URL no se pidio) y los dos `*_pixel_width` (decision 54), agregados por lote (4 consultas por ventana, no 4 por URL). `content_text_first_500` dice en el nombre que va recortado; el texto entero es `/content/export`. Con reglas a medida, columnas `x_<nombre>` AL FINAL (decision 104) |

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
| `extract_custom(selector, html, comp, **kw)` | list[dict] — filas de las reglas a medida (envuelve `shared.extraccion_a_medida.aplicar`, decision 104) |

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
| `analyze_links()` | orphan_page, **solo_enlazada_por_redireccion** (decision 63), **not_reachable_from_home** y `click_depth` (decision 90, en `compute_link_counts`), high_outlink_count, y las metricas del grafo (inlinks, outlinks, pagerank) |
| `analyze_sitemap()` | sitemap_orphan, not_in_sitemap |

Configurable thresholds via `job.config.analysis_thresholds` JSON or module-level constants.

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
| `RobotsCrawlDelayMiddleware` | 110 | `Crawl-delay` de robots.txt como retraso del slot, solo en `respect` (decision 81) |

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
