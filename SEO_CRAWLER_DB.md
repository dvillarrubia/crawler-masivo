# SEO Crawler — Acceso a Base de Datos

## Conexion

PostgreSQL 16 expuesto en localhost via Docker.

```
Host:     localhost
Port:     5432
Database: crawler_db
User:     crawler
Password: crawler
```

```python
# Connection string para SQLAlchemy / psycopg2
DATABASE_URL = "postgresql+psycopg2://crawler:crawler@localhost:5432/crawler_db"

# Para pandas
import pandas as pd
from sqlalchemy import create_engine
engine = create_engine("postgresql+psycopg2://crawler:crawler@localhost:5432/crawler_db")
df = pd.read_sql("SELECT * FROM urls WHERE job_id = '...' LIMIT 10", engine)
```

```bash
# CLI directo
psql -h localhost -U crawler -d crawler_db
# o via docker
docker exec -it crawlermasivo-postgres-1 psql -U crawler -d crawler_db
```

---

## Tablas

### `jobs` — Crawl jobs
| Columna | Tipo | Descripcion |
|---|---|---|
| id | UUID (PK) | ID del job |
| name | varchar(512) | Nombre del proyecto |
| client_id | varchar(128) | ID del cliente (opcional) |
| status | varchar(20) | pending, running, completed, failed, cancelled |
| seeds | JSON | Lista de seed URLs |
| config | JSON | Configuracion completa del crawl |
| total_urls_crawled | int | URLs crawleadas |
| created_at / started_at / completed_at | timestamptz | Timestamps |

### `urls` — Todas las URLs crawleadas
| Columna | Tipo | Descripcion |
|---|---|---|
| id | bigint (PK) | ID interno |
| job_id | UUID (FK → jobs) | Job al que pertenece |
| url | text | URL completa |
| url_hash | varchar(64) | SHA-256 para dedup |
| host | varchar(512) | Hostname |
| path | text | Path de la URL |
| scheme | varchar(10) | http/https |
| is_internal | bool | Si pertenece al dominio semilla |
| crawl_depth | int | Profundidad BFS desde la seed |
| status_code | int | Codigo HTTP (200, 301, 404...) |
| status_group | varchar(10) | 2xx, 3xx, 4xx, 5xx, timeout, dns_error |
| content_type | varchar(256) | Content-Type header |
| content_length | bigint | Tamano en bytes |
| response_time_ms | float | Tiempo de respuesta |
| is_html | bool | Si es pagina HTML |
| resource_type | varchar(20) | html, image, css, js, pdf, redirect, other |
| redirect_url | text | Destino si es redireccion |
| body_hash | varchar(64) | SHA-256 del body (deteccion duplicados) |
| word_count | int | Palabras en texto visible |
| text_ratio | float | % texto visible vs HTML total |
| indexability_status | varchar(64) | Indexable, Noindex, Canonicalised, Redirect... |
| inlinks_count | int | Total inlinks recibidos |
| outlinks_count | int | Total outlinks internos |
| external_outlinks_count | int | Outlinks externos |
| unique_inlinks_count | int | Paginas unicas que enlazan aqui |
| pagerank | float | PageRank interno (0-10) |
| url_length | int | Longitud de la URL en caracteres |
| folder_depth | int | Segmentos en el path |

**Unique constraint:** `(job_id, url_hash)`

### `html_meta` — Metadatos SEO (1:1 con urls)
| Columna | Tipo | Descripcion |
|---|---|---|
| url_id | bigint (PK, FK → urls) | |
| title | text | Tag `<title>` |
| title_len | int | Longitud del title |
| title_pixel_width | int | Ancho en px estimado en SERP (Arial 20px) |
| meta_description | text | Meta description |
| meta_description_len | int | Longitud |
| meta_description_pixel_width | int | Ancho en px en SERP (Arial 14px) |
| meta_keywords | text | Meta keywords |
| meta_robots | varchar(256) | Contenido de meta robots |
| x_robots_tag | varchar(256) | Header X-Robots-Tag |
| canonical_href | text | `<link rel="canonical">` href |
| canonical_header | text | Canonical via Link header HTTP |
| og_title / og_description / og_image / og_url / og_type | text | Open Graph |
| twitter_card / twitter_title / twitter_description | text | Twitter Cards |
| rel_next / rel_prev | text | Paginacion |
| meta_refresh | text | Meta refresh si existe |
| has_meta_outside_head | bool | Meta tags fuera de `<head>` |

### `headings` — Encabezados H1-H6
| Columna | Tipo | Descripcion |
|---|---|---|
| id | bigint (PK) | |
| url_id | bigint (FK → urls) | |
| tag | varchar(4) | h1, h2, h3... |
| position | int | Orden en el documento (0-based) |
| text | text | Texto del heading |

### `links` — Grafo de enlaces
| Columna | Tipo | Descripcion |
|---|---|---|
| id | bigint (PK) | |
| job_id | UUID (FK → jobs) | |
| from_url_id | bigint (FK → urls) | Pagina origen |
| to_url | text | URL destino |
| to_url_hash | varchar(64) | Hash de la URL destino |
| anchor_text | text | Texto ancla |
| rel | varchar(128) | Atributo rel (nofollow, etc.) |
| is_internal | bool | Si es enlace interno |
| link_position | varchar(20) | nav, footer, content, header, sidebar |
| follow | bool | Si pasa equity (no tiene nofollow) |
| target | varchar(20) | _blank, _self... |
| alt_text | text | Alt de imagen si es image link |
| link_type | varchar(20) | hyperlink, image, image_text |

### `page_content` — Contenido extraido (1:1 con urls)
| Columna | Tipo | Descripcion |
|---|---|---|
| url_id | bigint (PK, FK → urls) | |
| content_text | text | Texto limpio (sin boilerplate) |
| content_length | int | Longitud del texto |
| content_markdown | text | Contenido en Markdown |

### `structured_data` — Datos estructurados
| Columna | Tipo | Descripcion |
|---|---|---|
| id | bigint (PK) | |
| url_id | bigint (FK → urls) | |
| raw | JSON | Dato estructurado completo |
| format | varchar(20) | jsonld, microdata, rdfa |
| schema_type | varchar(128) | @type del schema (Article, FAQPage...) |

### `hreflang` — Anotaciones hreflang
| Columna | Tipo | Descripcion |
|---|---|---|
| id | bigint (PK) | |
| url_id | bigint (FK → urls) | |
| lang | varchar(20) | Codigo de idioma (es, en-US, x-default) |
| href | text | URL alternativa |

### `resources` — Recursos referenciados (img, css, js)
| Columna | Tipo | Descripcion |
|---|---|---|
| id | bigint (PK) | |
| url_id | bigint (FK → urls) | |
| resource_url | text | URL del recurso |
| resource_type | varchar(20) | image, css, js, pdf, font |
| alt_text | text | Texto alt (imagenes) |
| width / height | int | Dimensiones del atributo HTML |
| is_mixed_content | bool | Recurso HTTP en pagina HTTPS |

### `security_headers` — Cabeceras de seguridad (1:1 con urls)
| Columna | Tipo | Descripcion |
|---|---|---|
| url_id | bigint (PK, FK → urls) | |
| is_https | bool | |
| has_mixed_content | bool | |
| has_hsts / has_csp / has_x_content_type_options / has_x_frame_options | bool | |
| referrer_policy | varchar(64) | Valor del header |
| has_unsafe_crossorigin | bool | target=_blank sin rel=noopener |

### `issues` — Problemas SEO detectados
| Columna | Tipo | Descripcion |
|---|---|---|
| id | bigint (PK) | |
| job_id | UUID (FK → jobs) | |
| url_id | bigint (FK → urls) | |
| issue_type | varchar(64) | Tipo de issue (ver lista abajo) |
| severity | varchar(10) | error, warning, info |
| details | JSON | Detalles adicionales |
| detected_at | timestamptz | |

**Issue types:** missing_title, title_too_short, title_too_long, duplicate_title, missing_description, description_too_short, description_too_long, duplicate_description, missing_h1, multiple_h1, status_4xx, status_5xx, redirect_chain, image_missing_alt, http_url, mixed_content, missing_hsts, missing_csp, low_word_count, low_text_ratio, url_too_long, url_non_ascii, url_uppercase, url_underscores, url_multiple_slashes, url_has_parameters, url_non_seo_friendly, url_cms_faceted, orphan_page, high_outlink_count

---

## Queries utiles

### Listar jobs
```sql
SELECT id, name, status, total_urls_crawled, created_at
FROM jobs ORDER BY created_at DESC;
```

### URLs de un job con sus metas
```sql
SELECT u.url, u.status_code, u.word_count, u.pagerank,
       m.title, m.meta_description, m.canonical_href
FROM urls u
LEFT JOIN html_meta m ON m.url_id = u.id
WHERE u.job_id = 'UUID-AQUI'
  AND u.is_html = true AND u.status_code = 200
ORDER BY u.pagerank DESC NULLS LAST;
```

### Contenido de paginas (texto + markdown)
```sql
SELECT u.url, pc.content_text, pc.content_markdown, pc.content_length
FROM urls u
JOIN page_content pc ON pc.url_id = u.id
WHERE u.job_id = 'UUID-AQUI'
  AND u.status_code = 200;
```

### Headings de una URL
```sql
SELECT h.tag, h.position, h.text
FROM headings h
JOIN urls u ON u.id = h.url_id
WHERE u.job_id = 'UUID-AQUI'
  AND u.url = 'https://example.com/page'
ORDER BY h.position;
```

### Datos estructurados
```sql
SELECT u.url, sd.format, sd.schema_type, sd.raw
FROM structured_data sd
JOIN urls u ON u.id = sd.url_id
WHERE u.job_id = 'UUID-AQUI';
```

### Issues por severidad
```sql
SELECT issue_type, severity, COUNT(*) as count
FROM issues
WHERE job_id = 'UUID-AQUI'
GROUP BY issue_type, severity
ORDER BY severity, count DESC;
```

### Grafo de enlaces internos
```sql
SELECT src.url AS from_url, l.anchor_text, l.to_url, l.link_position, l.follow
FROM links l
JOIN urls src ON src.id = l.from_url_id
WHERE l.job_id = 'UUID-AQUI' AND l.is_internal = true;
```

### Paginas con mejor PageRank
```sql
SELECT url, pagerank, inlinks_count, unique_inlinks_count, word_count
FROM urls
WHERE job_id = 'UUID-AQUI' AND is_html = true AND status_code = 200
ORDER BY pagerank DESC NULLS LAST
LIMIT 50;
```

### Contenido completo de una URL (todo junto)
```sql
SELECT u.url, u.status_code, u.word_count, u.text_ratio, u.pagerank,
       m.title, m.meta_description, m.canonical_href, m.og_title,
       pc.content_text, pc.content_markdown
FROM urls u
LEFT JOIN html_meta m ON m.url_id = u.id
LEFT JOIN page_content pc ON pc.url_id = u.id
WHERE u.job_id = 'UUID-AQUI'
  AND u.url = 'https://example.com/page';
```

---

## Diagrama de relaciones

```
jobs (1) ──── (*) urls (1) ──── (1) html_meta
                    │ (1) ──── (1) page_content
                    │ (1) ──── (1) security_headers
                    │ (1) ──── (*) headings
                    │ (1) ──── (*) hreflang
                    │ (1) ──── (*) structured_data
                    │ (1) ──── (*) resources
                    │ (1) ──── (*) issues
                    │
              links (*) ──── from_url_id (FK → urls)
                         ──── to_url_hash (match → urls.url_hash)
```
