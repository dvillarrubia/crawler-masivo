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
| client_id | varchar(128) | Texto libre de siempre (etiqueta). Con cliente, su slug. No es la FK |
| cliente_id | UUID (FK → clients, ON DELETE SET NULL) | Cliente del job. NULL = sin cliente |
| profile_id | UUID (FK → crawl_profiles, ON DELETE SET NULL) | Perfil del que salio. NULL = rastreo puntual |
| status | varchar(20) | pending, running, completed, failed, cancelled |
| seeds | JSON | Lista de seed URLs |
| config | JSON | Configuracion completa del crawl. Con cliente, ya resuelta (defaults ← cliente ← perfil ← job) en el momento de crearlo |
| total_urls_crawled | int | URLs crawleadas |
| decisiones_resumen | JSON | Capa de decisiones (#7): `llamadas`, `tokens_entrada`, `coste_usd` (real, de `usage.cost`), `aciertos_cache`, `n_429`, `latencia_p50_ms`/`p95`, `por_pregunta` con la distribucion de respuestas, `avisos` y `estado` (ok, degradado, desactivado, sin_credito, presupuesto_agotado). NULL = nunca se pregunto nada a un modelo |
| enlaces_archivados | JSON | Enlaces archivados en Parquet (decision 95): `filas`, `apariciones`, `internos`, `nofollow_internos`, `fichero`, `bytes`, `fecha`. Con valor, `links` NO tiene filas de este job: se leen de `$ARCHIVO_DIR/enlaces/<job_id>.parquet`. NULL = en `links` |
| pagerank_resumen | JSON | Reparto del PageRank por tipo de URL (`reparto`: indexable, no_indexable, redireccion, error, recurso, sin_respuesta; fracciones que suman 1), `desperdiciado` (lo que acaba en errores), modo de `peso` y aristas de redireccion/canonical. NULL = analizado antes de este modelo |
| created_at / started_at / completed_at | timestamptz | Timestamps |

### `clients` — Clientes (#36)
| Columna | Tipo | Descripcion |
|---|---|---|
| id | UUID (PK) | |
| nombre | varchar(256) | Nombre visible |
| slug | varchar(128), unico | Identificador estable. Con el se casa el cliente al importar un backup |
| dominios | JSON | Lista de hosts (`www.cliente.com`) |
| zona_horaria | varchar(64) | Def. `Europe/Madrid` |
| config_defecto | JSON | Configuracion PARCIAL de `JobConfig`: solo lo que el cliente fija. `null` en una clave = quitar lo heredado |
| integraciones | JSON | Lista de `{tipo, etiqueta, parametros, credencial_env, activa}`. **Sin secretos**: `credencial_env` es el NOMBRE de la variable de entorno con la clave (decision 83 de CLAUDE.md) |
| decisiones | JSON | `{"presupuesto_mensual_usd": 5.0}`: tope de gasto en llamadas a modelos por mes natural (UTC), sumando todas sus ejecuciones. NULL = sin tope propio (manda `ENRICHMENT_MAX_USD_MES`) |
| notas | text | |
| created_at / updated_at | timestamptz | |

### `crawl_profiles` — Perfiles de rastreo de un cliente
| Columna | Tipo | Descripcion |
|---|---|---|
| id | UUID (PK) | |
| cliente_id | UUID (FK → clients, ON DELETE CASCADE) | |
| nombre | varchar(256) | Unico por cliente (`uq_profile_cliente_nombre`) |
| rol | varchar(16) | `propio` o `competidor` |
| seeds | JSON | Semillas por defecto de los jobs del perfil |
| config | JSON | Configuracion PARCIAL, encima de la del cliente |
| notas | text | |
| activo | bool | |
| last_job_id | UUID (FK → jobs, ON DELETE SET NULL) | Ultimo job lanzado desde el perfil |
| created_at / updated_at | timestamptz | |

`programaciones` tiene tambien `cliente_id` y `profile_id` (FK, SET NULL): los jobs que dispara salen colgados de ese cliente y perfil.

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
| content_length | bigint | Tamano del recurso en bytes, ya descomprimido |
| response_time_ms | float | Tiempo de respuesta |
| is_html | bool | Si es pagina HTML |
| resource_type | varchar(20) | html, image, css, js, pdf, redirect, other |
| redirect_url | text | Destino si es redireccion |
| body_hash | varchar(64) | SHA-256 del body (deteccion duplicados) |
| word_count | int | Palabras en texto visible |
| text_ratio | float | % texto visible vs HTML total (metrica, ya no genera issue) |
| content_word_count | int | palabras del contenido principal, sin menu ni pie |
| content_hash | varchar(64) | SHA-256 del contenido principal normalizado (caja y espacios). Es el que detecta duplicados exactos; `body_hash` cambia con cualquier token de la pagina |
| indexability_status | varchar(64) | Indexable, Noindex, Canonicalised, Redirect... |
| bloqueada_googlebot | bool | Si Googlebot tiene prohibida la URL segun el robots.txt guardado (`robots_txt`). La escribe el analisis; NULL = no evaluado. Si se conoce, manda en la indexabilidad (decision 113) |
| inlinks_count | int | Total inlinks recibidos |
| outlinks_count | int | Total outlinks internos |
| external_outlinks_count | int | Outlinks externos |
| unique_inlinks_count | int | Paginas unicas que enlazan aqui |
| pagerank | float | PageRank interno, 0-10 relativo a la URL mas fuerte del job (no comparable entre jobs). Enlaces follow internos con peso por posicion y techo por repeticion, mas arista salto → destino en redirecciones y variante → canonical. El salto aleatorio solo va a paginas 200 indexables: un 404 recibe por sus enlaces entrantes pero no por azar. Modelo en `analysis/pagerank.py` |
| near_duplicate_count | int | Paginas con >= 90% del mismo contenido. NULL = no medido (sin texto o texto corto), 0 = medido y ninguna |
| closest_similarity | float | Similitud con la pagina mas parecida, 0-1 (1 = contenido identico) |
| url_length | int | Longitud de la URL en caracteres |
| folder_depth | int | Segmentos en el path |

**Unique constraint:** `(job_id, url_hash)`

### `html_meta` — Metadatos SEO (1:1 con urls)

Tambien hay fila para un documento **no HTML** 2xx (PDF, sobre todo) cuando trae
`X-Robots-Tag` o canonical en la cabecera `Link`: solo con esas dos columnas
rellenas. Es la unica forma de sacar del indice o canonicalizar un PDF, y sin la
fila el analisis lo devolvia a «Indexable». Para leer metadatos de paginas,
filtrar por `urls.is_html`.
| Columna | Tipo | Descripcion |
|---|---|---|
| url_id | bigint (PK, FK → urls) | |
| title | text | Tag `<title>` |
| title_len | int | Longitud del title |
| title_pixel_width | int | Ancho en px estimado en SERP (Arial 20px) |
| title_count | int | Cuantos `<title>` hay, vacios incluidos (>1 → `title_multiple`) |
| meta_description | text | Meta description |
| meta_description_len | int | Longitud |
| meta_description_pixel_width | int | Ancho en px en SERP (Arial 14px) |
| meta_description_count | int | Cuantas meta description hay, vacias incluidas (>1 → `description_multiple`) |
| meta_keywords | text | Meta keywords |
| meta_robots | varchar(256) | Contenido de meta robots |
| meta_robots_googlebot | varchar(256) | `<meta name="googlebot">` (manda sobre el `robots` generico para Google) |
| x_robots_tag | varchar(256) | Cabeceras X-Robots-Tag, **una por linea** (cada una puede ir a un bot; antes de oct-2026, unidas por coma) |
| canonical_href | text | `<link rel="canonical">` href |
| canonical_count | int | Cuantos `rel=canonical` lleva la pagina. Con mas de uno Google los ignora todos |
| canonical_in_body | bool | Hay algun canonical fuera del `<head>`: Google lo ignora siempre |
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
| oculto | bool | El titular esta en el HTML pero no se pinta (hidden, display:none, clase de utilidad, aria-hidden, template/noscript/svg). Los checks de h1 cuentan solo `oculto IS NOT TRUE` |

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
| repeticiones | int, def. 1 | Apariciones IDENTICAS del enlace en la pagina, agrupadas en esta fila (decision 94). Un conteo por aparicion es `SUM(repeticiones)`, no `COUNT(*)` |

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
| json_invalido | varchar(200) | Por que el JSON-LD no es JSON estricto (coma final, comentario): Google lo descarta. NULL = valido o no aplica |

### `hreflang` — Anotaciones hreflang
| Columna | Tipo | Descripcion |
|---|---|---|
| id | bigint (PK) | |
| url_id | bigint (FK → urls) | |
| lang | varchar(20) | Codigo de idioma (es, en-US, x-default) |
| href | text | URL alternativa |
| return_tag_ok | bool | La pagina destino devuelve el enlace (NULL = no se sabe: sin rastrear, autorreferencia, x-default, destino roto o en el body) |
| lang_valid | bool | Idioma ISO 639-1 y region existente |
| en_body | bool | Anotacion fuera del `<head>`: Google la ignora (`hreflang_in_body`). NULL = rastreo anterior a la columna |

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

### `custom_extraction` — Extraccion y busqueda a medida (#34, decision 104)
Una fila por (URL, regla) **evaluada**. Las definiciones viajan en `jobs.config`
(`custom_extraction`, `custom_search`). Sin fila = no evaluada (no es HTML 2xx,
cae fuera de `solo_urls` o el censo es anterior a la regla); «no contiene» es
exactamente `estado = 'sin_coincidencia'`.

| Columna | Tipo | Descripcion |
|---|---|---|
| id | bigint (PK) | |
| job_id | UUID (FK → jobs, CASCADE) | Para filtrar y borrar por censo sin unir con `urls` (decision 37) |
| url_id | bigint (FK → urls, CASCADE) | |
| nombre | varchar(40) | Nombre de la regla (`x.<nombre>` en el Explorador) |
| clase | varchar(10) | extractor, busqueda |
| estado | varchar(24) | ok, sin_coincidencia, incompleta, conversion_fallida, tiempo_agotado, presupuesto_pagina, desactivada, pagina_bloqueada, error |
| n | integer | Coincidencias antes de recortar; NULL salvo en ok, sin_coincidencia y conversion_fallida |
| valor | text | primero: el valor; todos: unidos con ` \| `; busqueda: ~120 caracteres de contexto |
| valor_clave | varchar(256) | `valor` con espacios colapsados y recortado: ordenar, agrupar, comparar |
| valor_num | float | valor=numero o recuento; en una busqueda, `n` |
| valores | JSON | Solo con `cuantos=todos` |
| truncado | boolean | Se recortaron valores o caracteres |
| render | boolean | Extraido del DOM renderizado (False: HTML de la respuesta) |
| detalle | varchar(200) | Motivo de un error, sin datos de la pagina |

Indices: `uq_cext_url_nombre (url_id, nombre)`, `ix_cext_job_nombre_estado`,
`ix_cext_job_nombre_num` y `ix_cext_job_nombre_clave`.

```sql
-- Paginas SIN el tag de GTM (solo las evaluadas: ni PDFs ni bloqueadas)
SELECT u.url FROM custom_extraction c JOIN urls u ON u.id = c.url_id
WHERE c.job_id = 'UUID-AQUI' AND c.nombre = 'gtm' AND c.estado = 'sin_coincidencia';
```

### `robots_txt` — robots.txt de cada host del censo (decisiones 112 y 113)
Lo que respondio el robots.txt de cada host durante el rastreo, con la respuesta
FINAL (tras reintentos y redirecciones). Una fila por (job, URL del robots.txt):
Google trata aparte cada esquema, host y puerto. Solo hosts internos y como
mucho 200 por job. Sin fila = no evaluado, nunca «sin robots.txt».

| Columna | Tipo | Descripcion |
|---|---|---|
| id | bigint (PK) | |
| job_id | UUID (FK → jobs, CASCADE) | |
| host | varchar(512) | Hostname |
| url | text | `https://host/robots.txt` pedido |
| url_final | text | Si redirigio, a donde |
| status_code | int | NULL = no hubo respuesta |
| error | varchar(200) | Excepcion de la descarga (timeout, DNS, conexion) |
| response_class | varchar(32) | `shared/respuestas.py`: un 429 es `rate_limited`, un desafio de Cloudflare `challenge_waf` (decision 103) |
| intentos | int | Con los reintentos de Scrapy (503 y 429 se reintentan dos veces; 500 no) |
| bytes | int | Tamano del cuerpo entero (solo 2xx) |
| sha256 | varchar(64) | Del cuerpo entero (solo 2xx): para ver si cambio entre censos (#36) |
| cuerpo | text | Solo de un 2xx, recortado a los 500 KiB que lee Google |
| recortado | bool | Pasaba de 500 KiB |
| comprobacion | JSON | `{status_code, error, response_class, fecha}` de la segunda peticion al cerrar, si la primera fallo |
| evaluacion | JSON | La escribe el analisis: `{"googlebot": {estado, evaluadas, bloqueadas}}` |
| fecha | timestamptz | |

Indices: `uq_robots_txt_job_url (job_id, url)` y `ix_robots_txt_job_host (job_id, host)`.

```sql
-- Hosts cuyo robots.txt fallo (5xx, 429 o sin respuesta) en un censo
SELECT host, status_code, error, intentos, comprobacion
FROM robots_txt WHERE job_id = 'UUID-AQUI'
  AND (status_code >= 500 OR status_code = 429 OR error IS NOT NULL);
```

### `decisions` — Respuestas de un modelo (#7, decision 84 de CLAUDE.md)
Registro de lo decidido y cache DURADERA: un recrawl busca aqui por `(clave, pregunta, version_pregunta, modelo)` antes de pagar otra vez.

| Columna | Tipo | Descripcion |
|---|---|---|
| id | bigint (PK) | |
| job_id | UUID (FK → jobs, CASCADE), nullable | NULL en el juego de evaluacion (#8) |
| url_id | bigint (FK → urls, CASCADE), nullable | |
| run_id | UUID (FK → enrichment_runs, SET NULL), nullable | Ejecucion de enriquecimiento de #40 que la produjo |
| ambito | varchar(10) | url, bloque, enlace, par, job |
| clave | varchar(64) | SHA-256 del estado normalizado. Estable entre jobs: no depende de `url_id` |
| pregunta / version_pregunta | varchar(64) / int | Del catalogo `shared/preguntas/` |
| estado | varchar(12) | decidido, incierto, sin_decidir |
| origen | varchar(10) | modelo, cache, arrastre, regla, humano |
| respuesta | text | La opcion de un choice, `true`/`false` de un noul, el valor esperado de un score |
| probabilidades | JSON | Por opcion / nivel |
| confianza | float | Normalizada: `max(p, 1-p)` en noul, `confidence` o la probabilidad maxima en choice/score |
| modelo / modelo_respuesta | varchar | El pedido (`typesafe/jev-1.13`, forma la clave) y el que respondio (`typesafe/jev-1.13-20260917`) |
| proveedor | varchar(64) | `TypeSafe` |
| tokens_entrada / coste_usd / coste_estimado | int / float / bool | Parte de la llamada que le toca a esta pregunta. 0 en lo servido de cache. `coste_estimado` = la respuesta no traia `usage.cost` |
| motivo | varchar(32) | Por que quedo sin decidir (presupuesto_ejecucion, presupuesto_cliente, sin_credito, servidor, desactivado...) |
| estado_enviado | JSON | Lo que VIO el modelo (estado normalizado) |
| created_at | timestamptz | |

Indices: `(job_id, pregunta)`, `url_id`, `(clave, pregunta, version_pregunta, modelo)`, `run_id`.

### `enrichment_runs` — Ejecuciones del analisis enriquecido (#40, decision 85 de CLAUDE.md)
Una pulsacion del boton «Analisis enriquecido». La crea la API (con token) y la ejecuta el worker desde la cola `enrichment:pending`.

| Columna | Tipo | Descripcion |
|---|---|---|
| id | UUID (PK) | |
| job_id | UUID (FK → jobs, CASCADE) | |
| modulos | JSON | Lista YA resuelta, con las dependencias anadidas, en orden de ejecucion |
| alcance | JSON | `{"carpeta", "max_urls", "muestra"}`; vacio = HTML internas, 200 e indexables |
| estimacion | JSON | Lo que enseño el dialogo al confirmar (llamadas, tokens, coste, cache, topes) |
| presupuesto_usd / gastado_usd | float | Tope de la ejecucion y suma de `usage.cost` de lo pagado |
| estado | varchar(16) | pendiente, en_curso, cancelando, completada, parcial (corte por presupuesto), cancelada, fallida |
| motivo | varchar(32) | Por que se paro: presupuesto_ejecucion, presupuesto_cliente, presupuesto_global, sin_credito, desactivado, cancelada... |
| progreso | JSON | `{modulo, procesadas, total, por_modulo: {...}}` |
| errores | JSON | Avisos de la capa (hasta 50) |
| created_at / started_at / finished_at / latido_at | timestamptz | `latido_at` lo actualiza el worker en cada lote; sin latido en `ENRICHMENT_STALE_MINUTES` se reencola |

Indices: `job_id`, `estado`.

```sql
-- Lo decidido en una ejecucion, por respuesta
SELECT pregunta, respuesta, estado, count(*), sum(coste_usd)
FROM decisions WHERE run_id = 'UUID-AQUI'
GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;
```

**Issue types:** missing_title, title_too_short, title_too_long, duplicate_title, missing_description, description_too_short, description_too_long, duplicate_description, missing_h1, multiple_h1, status_4xx, status_5xx, redirect_chain, image_missing_alt, http_url, mixed_content, missing_hsts, missing_csp, low_word_count, url_too_long, url_non_ascii, url_uppercase, url_underscores, url_multiple_slashes, url_has_parameters, url_non_seo_friendly, url_cms_faceted, orphan_page, high_outlink_count

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
clients (1) ──── (*) crawl_profiles
   │ (1)               │ (1)
   └──── (*) jobs (*) ─┘

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
