# Modelo de datos y cálculos tipo Screaming Frog sobre Scrapy

Este documento define los campos que debe recoger el crawler y los cálculos necesarios para replicar (y ampliar) lo que ofrece Screaming Frog en sus pestañas principales.[web:43][web:49][web:48][web:36]

## 1. Esquema de tablas principales

Propuesta mínima de modelo relacional (adaptable a columnas/NoSQL):

### 1.1. Tabla `urls`

Una fila por URL única (normalizada).

Campos básicos:

- `id` (PK)
- `url` (text)
- `host` (text)
- `path` (text)
- `scheme` (text)
- `is_internal` (bool)
- `crawl_depth` (int, nullable)
- `content_type` (text)
- `content_length` (int)
- `status_code` (int)
- `status_group` (text, ej: `2xx`, `3xx`, `4xx`, `5xx`, `timeout`, `dns_error`)
- `first_seen_at` (timestamp)
- `last_crawled_at` (timestamp)
- `is_html` (bool)
- `resource_type` (enum: `html`, `image`, `css`, `js`, `pdf`, `other`)

Derivados típicos (para informes tipo Screaming Frog):

- `status_group` calculado desde `status_code`.[web:55]
- `resource_type` desde `content_type` + extensión.

### 1.2. Tabla `html_meta`

Solo para URLs HTML (`is_html = true`).

- `url_id` (FK → urls.id)
- `title` (text)
- `title_len` (int)
- `meta_description` (text)
- `meta_description_len` (int)
- `meta_keywords` (text)
- `meta_robots` (text)
- `x_robots_tag` (text, desde cabecera)
- `canonical_href` (text)
- `canonical_header` (text)
- `og_title` (text)
- `og_description` (text)
- `og_image` (text)
- `twitter_card` (text)

Cálculos:

- `title_len` y `meta_description_len` basados en longitud de caracteres.
- Flags para informes:
  - `title_missing` (bool)
  - `title_too_short` (bool)
  - `title_too_long` (bool)
  - `description_missing` (bool)
  - `description_too_short` (bool)
  - `description_too_long` (bool)[web:36][web:49]

### 1.3. Tabla `headings`

Opcionalmente normalizada.

- `id`
- `url_id`
- `tag` (enum: `h1`, `h2`, …)
- `position` (int, orden de aparición)
- `text` (text)

Cálculos:

- `h1_count`, `h2_count` por URL (via query agregada).
- Flags:
  - `multiple_h1` (bool)
  - `missing_h1` (bool)
  - `duplicate_h1` (bool, si mismo texto H1 aparece en múltiples URLs).[web:36]

### 1.4. Tabla `links`

Grafo de enlaces (origen → destino).

- `id`
- `from_url_id`
- `to_url_id`
- `anchor_text` (text)
- `rel` (text, ej: `nofollow`, `ugc`, `sponsored`)
- `is_internal` (bool)
- `link_position` (opcional: `nav`, `footer`, `content`, etc.)

Cálculos:

- `inlinks` por URL: `COUNT(*) WHERE to_url_id = X`.
- `outlinks` por URL: `COUNT(*) WHERE from_url_id = X`.
- Detección de cadenas de redirección con queries sobre `urls` + `links` donde hay 3xx en cascada.[web:49]

### 1.5. Tabla `hreflang`

- `id`
- `url_id`
- `lang` (ej: `es`, `es-es`, `en-gb`)
- `href` (URL destino)
- `return_tag_ok` (bool, derivado)
- `lang_valid` (bool, derivado)

Análisis tipo Screaming Frog:

- Por conjunto de hreflang (por canonical o por cluster lógico):
  - Missing return tag (A → B pero B no apunta a A).[web:43]
  - Lang inválido/no estándar.
  - Destinos no 2xx (`JOIN` con `urls`).

### 1.6. Tabla `structured_data`

Almacena JSON‑LD, Microdata, RDFa.

- `id`
- `url_id`
- `raw` (json/text)
- `format` (`jsonld`, `microdata`, `rdfa`)
- `schema_type` (text, ej: `Product`, `Article`, `Organization`)
- `validation_status` (enum: `ok`, `warning`, `error`)
- `validation_issues` (json/text)

Cálculos:

- Validación según Schema.org + rich results:
  - Campos obligatorios faltantes → `validation_status = error`.
  - Campos recomendados faltantes → `warning`.[web:48]

### 1.7. Tabla `resources` (imágenes, CSS, JS, PDFs)

Puede coexistir con `urls` o ser vista derivada.

- `url_id`
- `resource_type` (`image`, `css`, `js`, `pdf`, …)
- `size_bytes`
- `width`, `height` (para imágenes si se calculan)
- `alt_text` (para imágenes via parsing HTML, no del recurso en sí)

### 1.8. Tabla `issues`

Tabla genérica de issues tipo Screaming Frog.

- `id`
- `url_id`
- `issue_type` (ej: `title_missing`, `title_too_long`, `4xx`, `canonical_broken`, `hreflang_missing_return_tag`)
- `severity` (`error`, `warning`, `info`)
- `details` (json/text)
- `detected_at` (timestamp)

Esta tabla se llena vía procesos de análisis (no en el crawler), replicando los “Issues” que muestra SF.[web:48][web:56]

---

## 2. Qué debe extraer el spider (Scrapy)

En cada `response` HTML:

- Datos básicos:
  - URL final, status, headers (`Content-Type`, `Content-Length`).
- Metadatos:
  - `<title>`, `<meta name="description">`, `<meta name="robots">`.
  - `<link rel="canonical">`, `rel="next"/"prev"`.
  - `<meta property="og:*">`, `<meta name="twitter:*">`.
- Headings:
  - Todos los `h1` y `h2` (texto, orden).
- Enlaces:
  - Todos los `<a href>` normalizados, con texto de anchor, `rel`.[web:39][web:45]
- Hreflang:
  - `<link rel="alternate" hreflang="...">` (lang, href).
- Structured data:
  - Todo `<script type="application/ld+json">` (contenido crudo).
  - Microdata/RDFa (via librería externa).
- Recursos:
  - `<img src>`, `<link href>` (CSS, iconos), `<script src>`, etc.

Los campos de robots.txt y cabeceras (`X‑Robots‑Tag`) se pueden gestionar con middlewares o una fase previa de resolución por host.[web:48]

---

## 3. Cálculos clave (post-proceso)

### 3.1. Profundidad de crawl

Opción simple:

- Usar `meta["depth"]` de Scrapy y persistirlo en cada URL en el momento del crawl.

Opción grafo:

- BFS desde seeds en la tabla `links`, calculando `crawl_depth` mínimo para cada URL.

### 3.2. Indexability

Campo derivado `indexable`:

- `indexable = (status_code in (200, 304))`
- `AND NOT noindex` (desde `meta_robots` o `x_robots_tag`)
- `AND NOT blocked_by_robots_txt`
- `AND NOT canonical_to_non_200`
- Etc. (las mismas reglas que use tu “policy” SEO).[web:48][web:43]

### 3.3. Duplicidades

Sobre `urls` + `html_meta`:

- Duplicidad de títulos: hash de `title` → agrupar por hash con count > 1.
- Duplicidad de `title + meta_description`.
- Duplicidad de contenido si guardas un hash del cuerpo renderizado o text‑only.

### 3.4. Issues típicos

Ejemplos de lógica que llena la tabla `issues`:

- `status_code` en 4xx → `issue_type = '4xx'`, `severity = 'error'`.
- `title_missing`, `title_too_long`, `description_missing`, etc.
- `canonical_href` apuntando a URL con `status_code` no 2xx.
- `hreflang` con `return_tag_ok = false`.
- `structured_data.validation_status != 'ok'`.

---

## 4. Equivalencia con pestañas de Screaming Frog

Tabla de mapeo simplificada:

| Pestaña SF         | Datos base       | Tablas       | Cálculos                       |
|--------------------|------------------|--------------|--------------------------------|
| Internal           | URLs HTML        | `urls`       | depth, status_group, inlinks   |
| External           | URLs externas    | `urls`, `links` | is_internal=false            |
| Response Codes     | Status y errores | `urls`       | status_group, issues 4xx/5xx   |
| Page Titles        | Titles           | `html_meta`  | len, duplicados, issues        |
| Meta Description   | Descriptions     | `html_meta`  | len, duplicados, issues        |
| H1/H2              | Headings         | `headings`   | counts, duplicados, issues     |
| Canonicals         | Canonical rel    | `html_meta`  | canonicals rotos, cadenas      |
| Directives         | Robots           | `html_meta`  | indexability, noindex, etc.    |
| Hreflang           | Hreflang         | `hreflang`   | return tags, válidos           |
| Structured Data    | Schema           | `structured_data` | validación, issues         |
| Images/CSS/JS/PDFs | Recursos         | `resources`  | peso, status, alt, etc.        |
| Issues             | Problemas        | `issues`     | reglas sobre el resto          |

Con este modelo tienes una base de datos que reproduce prácticamente todo lo que ves en Screaming Frog, pero lista para escalar, versionar y combinar con otros datos (GSC, logs, KG, etc.).[web:43][web:49][web:48]