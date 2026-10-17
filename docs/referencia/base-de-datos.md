# Base de datos

> Movido tal cual desde `CLAUDE.md` (2026-10-14).

## Database (PostgreSQL)

17 tables. Full schema documented in **`SEO_CRAWLER_DB.md`** (connection strings, all columns, example queries).

| Table | Relation | Key Fields |
|-------|----------|------------|
| `jobs` | root | id (UUID), name, status, seeds (JSON), config (JSON) |
| `urls` | 1:N from jobs | url, status_code, is_html, pagerank, word_count, indexability_status, html_bytes_servidos (HTML servido descomprimido, antes del render; decision 102) |
| `html_meta` | 1:1 with urls | title, meta_description, canonical_href, og_*, twitter_*, head_cortado_por y fuera_del_head (decision 102; NULL en censos anteriores) |
| `headings` | 1:N from urls | tag (h1-h6), position, text |
| `links` | N:N graph | from_url_id, to_url, anchor_text, rel, link_position, follow |
| `page_content` | 1:1 with urls | content_text, content_markdown |
| `hreflang` | 1:N from urls | lang, href |
| `structured_data` | 1:N from urls | raw (JSON), format, schema_type |
| `resources` | 1:N from urls | resource_url, resource_type, alt_text, size_bytes (lo rellena el analisis con la sonda de recursos, decision 124; NULL = no se sabe) |
| `security_headers` | 1:1 with urls | is_https, has_hsts, has_csp, has_mixed_content |
| `issues` | 1:N from urls | issue_type, severity, details (JSON) |
| `clients` | root | nombre, slug (unico), dominios, config_defecto (parcial), integraciones (sin secretos) |
| `crawl_profiles` | 1:N from clients | nombre, rol, seeds, config (parcial), last_job_id |
| `decisions` | 1:N from jobs | clave, pregunta, version_pregunta, estado, respuesta, coste_usd, run_id (decision 84) |
| `enrichment_runs` | 1:N from jobs | modulos, alcance, estimacion, presupuesto_usd, gastado_usd, estado, progreso (decision 85) |
| `custom_extraction` | 1:N from urls (con `job_id`) | nombre, clase (extractor/busqueda), estado, n, valor, valor_clave, valor_num, valores, render (decision 104). Una fila por (URL, regla) EVALUADA: sin fila = no evaluada, no «vacio». Migracion: `migrar_custom_extraction()` en `init_db.py` |
| `enlaces_no_rastreables` | 1:N from urls (con `job_id`) | crawlability (javascript_href, onclick_sin_href, a_sin_href, href_fuera_de_a, onclick_en_otra_etiqueta, fragmento_como_ruta), etiqueta, href, destino, destino_interno, destino_hash, anchor_text, link_position, repeticiones (decision 106). Enlaces que Google no puede seguir: NO son aristas y no estan en `links`. Una pagina se reemplaza entera al re-rastrear. Migracion: `migrar_enlaces_no_rastreables()` |
| `respuestas_sondeadas` | 1:N from jobs (SIN url FK) | url_hash (el de `links.to_url_hash`), url, host, uso (enlace/recurso), interno, metodo (HEAD/GET; NULL = no pedida), status_code, final_url, final_status, saltos, veredicto (`shared/sondas.py`), response_class, host_caido, content_type, content_length, error (decisiones 123 y 124). Lo que respondio una URL que el rastreo pidio SIN que sea del censo: enlaces externos y recursos embebidos. Unica por (job_id, url_hash); sin fila = no comprobada. En `TABLAS_POR_JOB`, `borrar_job` y el backup. Migracion: `migrar_respuestas_sondeadas()` |

`urls.response_class` dice si la respuesta era la pagina o un muro delante
(`contenido`, `challenge_waf`, `captcha`, `rate_limited`, `respuesta_binaria`,
`login_requerido`, `desconocido`; NULL en saltos, sin respuesta y censos
anteriores) y `jobs.health` los recuentos por clase y la causa si el rastreo se
dio por bloqueado (decision 103).

`jobs.sondas_resumen` (JSON, decisiones 123 y 124): lo escribe el analisis. `enlaces_externos` (distintos, comprobados, rotos, dominios_muertos, por_veredicto) y `recursos` (comprobados, rotos, por_veredicto). NULL = sin sondas. Migracion en `init_db.py`.

`jobs.soft404` (JSON, decision 105): `sondas` las escribe el spider (lo que el sitio responde a una URL que no existe) y `resumen` el analisis. Las sondas NO son URLs del censo. Migracion en `init_db.py`.

`jobs.cliente_id` / `jobs.profile_id` son las FK al cliente y al perfil; `jobs.client_id` sigue siendo el texto libre de siempre (decision 83).

URL dedup: SHA-256 hash per `(job_id, url_hash)` unique constraint.
