# Base de datos

> Movido tal cual desde `CLAUDE.md` (2026-10-14).

## Database (PostgreSQL)

16 tables. Full schema documented in **`SEO_CRAWLER_DB.md`** (connection strings, all columns, example queries).

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
| `resources` | 1:N from urls | resource_url, resource_type, alt_text |
| `security_headers` | 1:1 with urls | is_https, has_hsts, has_csp, has_mixed_content |
| `issues` | 1:N from urls | issue_type, severity, details (JSON) |
| `clients` | root | nombre, slug (unico), dominios, config_defecto (parcial), integraciones (sin secretos) |
| `crawl_profiles` | 1:N from clients | nombre, rol, seeds, config (parcial), last_job_id |
| `decisions` | 1:N from jobs | clave, pregunta, version_pregunta, estado, respuesta, coste_usd, run_id (decision 84) |
| `enrichment_runs` | 1:N from jobs | modulos, alcance, estimacion, presupuesto_usd, gastado_usd, estado, progreso (decision 85) |
| `custom_extraction` | 1:N from urls (con `job_id`) | nombre, clase (extractor/busqueda), estado, n, valor, valor_clave, valor_num, valores, render (decision 104). Una fila por (URL, regla) EVALUADA: sin fila = no evaluada, no «vacio». Migracion: `migrar_custom_extraction()` en `init_db.py` |

`jobs.cliente_id` / `jobs.profile_id` son las FK al cliente y al perfil; `jobs.client_id` sigue siendo el texto libre de siempre (decision 83).

URL dedup: SHA-256 hash per `(job_id, url_hash)` unique constraint.
