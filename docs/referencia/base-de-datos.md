# Base de datos

> Movido tal cual desde `CLAUDE.md` (2026-10-14).

## Database (PostgreSQL)

18 tables. Full schema documented in **`SEO_CRAWLER_DB.md`** (connection strings, all columns, example queries).

| Table | Relation | Key Fields |
|-------|----------|------------|
| `jobs` | root | id (UUID), name, status, seeds (JSON), config (JSON), segmentacion (JSON: reglas, huella, por_regla con `urls` y `sin_saber`, por_defecto, externas, recursos, segundos; decision 125) |
| `urls` | 1:N from jobs | url, status_code, is_html, pagerank, word_count, indexability_status, html_bytes_servidos (HTML servido descomprimido, antes del render; decision 102), segmento (decision 125: nombre de la regla de `config.segmentos`, la forma de la ruta, `(externa)` o `(recurso)`; NULL = sin segmentar; sin indice propio, va con `job_id`) |
| `html_meta` | 1:1 with urls | title, meta_description, canonical_href, og_*, twitter_*, head_cortado_por y fuera_del_head (decision 102; NULL en censos anteriores), canonical_distintos (decision 115; NULL en censos anteriores); viewport, html_lang, canonical_crudo, og_image_forma, redireccion_js, charset_* y texto_mal_codificado (decision 108; NULL = no medido) |
| `headings` | 1:N from urls | tag (h1-h6), position, text |
| `links` | N:N graph | from_url_id, to_url, anchor_text, rel, link_position, follow |
| `page_content` | 1:1 with urls | content_text, content_markdown |
| `hreflang` | 1:N from urls | lang, href, origen (`html`/`cabecera`, decision 116; NULL = del HTML) |
| `structured_data` | 1:N from urls | raw (JSON), format, schema_type |
| `resources` | 1:N from urls | resource_url, resource_type, alt_text |
| `security_headers` | 1:1 with urls | is_https, has_hsts, has_csp, has_mixed_content |
| `issues` | 1:N from urls | issue_type, severity, details (JSON) |
| `clients` | root | nombre, slug (unico), dominios, config_defecto (parcial), integraciones (sin secretos) |
| `crawl_profiles` | 1:N from clients | nombre, rol, seeds, config (parcial), last_job_id |
| `decisions` | 1:N from jobs | clave, pregunta, version_pregunta, estado, respuesta, coste_usd, run_id (decision 84) |
| `enrichment_runs` | 1:N from jobs | modulos, alcance, estimacion, presupuesto_usd, gastado_usd, estado, progreso (decision 85) |
| `custom_extraction` | 1:N from urls (con `job_id`) | nombre, clase (extractor/busqueda), estado, n, valor, valor_clave, valor_num, valores, render (decision 104). Una fila por (URL, regla) EVALUADA: sin fila = no evaluada, no «vacio». Migracion: `migrar_custom_extraction()` en `init_db.py` |
| `robots_txt` | 1:N from jobs | host, url, url_final, status_code, error, response_class, intentos, bytes, sha256, cuerpo (solo 2xx, recortado a 500 KiB), recortado, comprobacion (JSON: segunda peticion al cerrar), evaluacion (JSON del analisis, por bot). Una fila por (job, URL del robots.txt), solo hosts internos y como mucho 200 por job. Sin fila = no evaluado (decisiones 112 y 113). Migracion: `migrar_robots_txt()` |
| `enlaces_no_rastreables` | 1:N from urls (con `job_id`) | crawlability (javascript_href, onclick_sin_href, a_sin_href, href_fuera_de_a, onclick_en_otra_etiqueta, fragmento_como_ruta), etiqueta, href, destino, destino_interno, destino_hash, anchor_text, link_position, repeticiones (decision 106). Enlaces que Google no puede seguir: NO son aristas y no estan en `links`. Una pagina se reemplaza entera al re-rastrear. Migracion: `migrar_enlaces_no_rastreables()` |

`urls.response_class` dice si la respuesta era la pagina o un muro delante
(`contenido`, `challenge_waf`, `captcha`, `rate_limited`, `respuesta_binaria`,
`login_requerido`, `desconocido`; NULL en saltos, sin respuesta y censos
anteriores) y `jobs.health` los recuentos por clase y la causa si el rastreo se
dio por bloqueado (decision 103).

`urls.bloqueada_googlebot` (decision 113): si Googlebot puede pedir la URL segun
el robots.txt guardado de su host (`robots_txt`). La escribe el analisis; NULL =
no evaluado (censo anterior, host sin fila, robots.txt inaccesible o con un muro
delante). Si se conoce, es lo que usa la indexabilidad; `blocked_by_robots` sigue
siendo lo que hizo el rastreo. Migracion en `init_db.py`.

`jobs.soft404` (JSON, decision 105): `sondas` las escribe el spider (lo que el sitio responde a una URL que no existe) y `resumen` el analisis. Las sondas NO son URLs del censo. Migracion en `init_db.py`.

`jobs.cliente_id` / `jobs.profile_id` son las FK al cliente y al perfil; `jobs.client_id` sigue siendo el texto libre de siempre (decision 83).

URL dedup: SHA-256 hash per `(job_id, url_hash)` unique constraint.
