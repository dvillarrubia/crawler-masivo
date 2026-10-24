# Base de datos

> Movido tal cual desde `CLAUDE.md` (2026-10-14).

## Database (PostgreSQL)

19 tables. Full schema documented in **`SEO_CRAWLER_DB.md`** (connection strings, all columns, example queries).

| Table | Relation | Key Fields |
|-------|----------|------------|
| `jobs` | root | id (UUID), name, status, seeds (JSON), config (JSON), segmentacion (JSON: reglas, huella, por_regla con `urls` y `sin_saber`, por_defecto, externas, recursos, segundos; decision 125) |
| `urls` | 1:N from jobs | url, status_code, is_html, pagerank, word_count, indexability_status, html_bytes_servidos (HTML servido descomprimido, antes del render; decision 102), segmento (decision 125: nombre de la regla de `config.segmentos`, la forma de la ruta, `(externa)` o `(recurso)`; NULL = sin segmentar; sin indice propio, va con `job_id`) |
| `html_meta` | 1:1 with urls | title, meta_description, canonical_href, og_*, twitter_*, head_cortado_por y fuera_del_head (decision 102; NULL en censos anteriores), canonical_distintos (decision 115; NULL en censos anteriores); viewport, html_lang, canonical_crudo, og_image_forma, redireccion_js, charset_* y texto_mal_codificado (decision 108; NULL = no medido); videos (JSON), amphtml y alternate_movil (decisiones 155 y 156; `videos` NULL = no medido) |
| `headings` | 1:N from urls | tag (h1-h6), position, text |
| `links` | N:N graph | from_url_id, to_url, anchor_text, rel, link_position, follow |
| `page_content` | 1:1 with urls | content_text, content_markdown |
| `hreflang` | 1:N from urls | lang, href, origen (`html`/`cabecera`, decision 116; NULL = del HTML) |
| `structured_data` | 1:N from urls | raw (JSON), format, schema_type |
| `resources` | 1:N from urls | resource_url, resource_type, alt_text, size_bytes (lo rellena el analisis con la sonda de recursos, decision 124; NULL = no se sabe) |
| `security_headers` | 1:1 with urls | is_https, has_hsts, has_csp, has_mixed_content |
| `issues` | 1:N from urls | issue_type, severity, details (JSON) |
| `clients` | root | nombre, slug (unico), dominios, config_defecto (parcial), integraciones (sin secretos) |
| `crawl_profiles` | 1:N from clients | nombre, rol, seeds, config (parcial), last_job_id |
| `decisions` | 1:N from jobs | clave, pregunta, version_pregunta, estado, respuesta, coste_usd, run_id (decision 84) |
| `enrichment_runs` | 1:N from jobs | modulos, alcance, estimacion, presupuesto_usd, gastado_usd, estado, progreso (decision 85) |
| `custom_extraction` | 1:N from urls (con `job_id`) | nombre, clase (extractor/busqueda), estado, n, valor, valor_clave, valor_num, valores, render (decision 104). Una fila por (URL, regla) EVALUADA: sin fila = no evaluada, no «vacio». Migracion: `migrar_custom_extraction()` en `init_db.py` |
| `robots_txt` | 1:N from jobs | host, url, url_final, status_code, error, response_class, intentos, bytes, sha256, cuerpo (solo 2xx, recortado a 500 KiB), recortado, comprobacion (JSON: segunda peticion al cerrar), evaluacion (JSON del analisis, por bot). Una fila por (job, URL del robots.txt), solo hosts internos y como mucho 200 por job. Sin fila = no evaluado (decisiones 112 y 113). Migracion: `migrar_robots_txt()` |
| `enlaces_no_rastreables` | 1:N from urls (con `job_id`) | crawlability (javascript_href, onclick_sin_href, a_sin_href, href_fuera_de_a, onclick_en_otra_etiqueta, fragmento_como_ruta), etiqueta, href, destino, destino_interno, destino_hash, anchor_text, link_position, repeticiones (decision 106). Enlaces que Google no puede seguir: NO son aristas y no estan en `links`. Una pagina se reemplaza entera al re-rastrear. Migracion: `migrar_enlaces_no_rastreables()` |
| `capturas_alternativas` | 1:N from urls (con `job_id`) | perfil (movil, escritorio; mas adelante crudo para la Ola 7.1), modo (render/sin_render), estado (comparada, redirige, respuesta_distinta, no_evaluada), plantilla, user_agent, status_code, response_class, redirige_a, vary_base, vary, medidas, diferencias, volatiles, detalle (decision 119). Una fila por URL muestreada y perfil aunque no difiera: sin fila = no evaluada. Migracion: `migrar_capturas_alternativas()` |
| `respuestas_sondeadas` | 1:N from jobs (SIN url FK) | url_hash (el de `links.to_url_hash`), url, host, uso (enlace/recurso), interno, metodo (HEAD/GET; NULL = no pedida), status_code, final_url, final_status, saltos, veredicto (`shared/sondas.py`), response_class, host_caido, content_type, content_length, error (decisiones 123 y 124); `uso='og_image'` con ancho y alto (decision 154). Lo que respondio una URL que el rastreo pidio SIN que sea del censo: enlaces externos y recursos embebidos. Unica por (job_id, url_hash); sin fila = no comprobada. En `TABLAS_POR_JOB`, `borrar_job` y el backup. Migracion: `migrar_respuestas_sondeadas()` |
| `sitemap_entries` | 1:N from jobs (SIN url FK) | url_hash (el de `urls.url_hash`), url, fichero (la URL del fichero de sitemap), lastmod (crudo, recortado a 64), videos (cuantos `<video:video>` declara; NULL = leida antes de la decision 155), hreflang (JSON `[[idioma, href]]` de sus `<xhtml:link>`, el href del mismo origen sin el; `[]` = ninguno, NULL = no se sabe: fila anterior o pasado `SITEMAP_HREFLANG_MAX`; decision 167). Una fila por (fichero, URL interna): la misma URL en tres ficheros son tres filas; una URL del sitemap sin rastrear tiene fila aqui y no en `urls`. Indice (job_id, url_hash). Tope `SITEMAP_ENTRIES_MAX` (250.000) por proceso de rastreo. En `TABLAS_POR_JOB`, `borrar_job` y el backup. Migracion: `migrar_sitemap_entries()` (decision 126) |
| `adapter_entities` | 1:N from jobs (SIN url FK) | adaptador (`wordpress`), tipo (`post`, `page`, un tipo propio, `attachment`, `category`, `post_tag`... y `sonda_paginacion`/`sonda_adjunto`), id_externo, url, url_hash (el de `urls.url_hash`), url_hash_alt (otro esquema), en_alcance (pasaba los filtros del job), estado (`publish`, `inherit`), status_code (sondas), cuenta (entradas de un termino), modificado, datos (JSON). Lo que la REST API de WordPress dice que hay: que no tenga fila en `urls` es el hallazgo, por eso no hay FK. Indices (job_id, tipo) y (job_id, url_hash). En `TABLAS_POR_JOB`, `borrar_job` y el backup. Migracion: `migrar_adapter_entities()` (decision 161) |
| `profile_runs` | 1:1 con jobs | job_id (unico), criterio, analisis_version, crawler_version, status/finish_reason (foto), urls y urls_2xx..5xx/bloqueadas (internas), paginas_html, indexables, click_depth_medio, indexables_sin_camino, incidencias, errores, avisos, info, urls_con_error, top_tipos (JSON), pagerank_desperdiciado, pagerank_indexable, segundos, calculado_at. La evolucion de cada ejecucion (decision 130): la escriben `run_analysis` y el cierre del worker, sin leer `links`. Solo cifras: cliente, perfil y tipo se leen de `jobs`. En `TABLAS_DEL_CENSO`, `borrar_job` y el backup. Migracion: `migrar_profile_runs()`; relleno: `scripts/rellenar_evolucion.py [--simular]` |
| `gsc_consultas` | 1:N from clients (SIN job ni url FK) | propiedad, fecha, url_clave (la de `gsc_paginas`, que guarda la URL en texto), consulta, clics, impresiones, posicion. Search Console por URL, busqueda y dia (decision 157): la canibalizacion por busqueda, la curva de CTR y las oportunidades. Sin indice unico: un dia se reescribe entero. Indice (cliente_id, fecha). Retencion `SEARCH_CONSOLE_DETALLE_DIAS` (180, minimo 90). Las busquedas anonimizadas no las da Google: la suma por busqueda no cuadra con el total de la pagina. La crea `create_all` |
| `gsc_sitemaps` | 1:N from clients (SIN job FK) | propiedad, path, enviado (lastSubmitted), descargado (lastDownloaded), pendiente (isPending), es_indice, avisos, errores, enviadas (JSON: `[{type, submitted}]`), visto_en. Un sitemap segun la Sitemaps API (decision 157); unica por (cliente_id, propiedad, path); los que Google ya no lista se borran. `contents[].indexed` esta obsoleto y no se guarda. Solo lista los ENVIADOS: que un sitemap del sitio no tenga fila no dice que Google no lo lea. La crea `create_all` |

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

`urls.ttfb_ms` y `urls.content_encoding` (decision 109): el tiempo del servidor hasta el primer byte y la compresion servida, medidos en el handler; `response_time_ms` sigue siendo la peticion entera. `jobs.tls` (JSON, decision 110): la sonda del certificado por host. `jobs.health["retry_after"]` (decision 111): lo que se respeto de los `Retry-After`. Las tres columnas, nullables y sin DEFAULT, con su `ALTER ... IF NOT EXISTS` en `init_db.py`.

`jobs.sitemaps` (JSON, decision 126): lo escribe el spider al cerrar. `ficheros` (por fichero: url, status, formato, declaradas —contadas enteras, tambien las que pasan del tope—, devueltas, hijos, internas, bytes descomprimidos, cortado, entradas_guardadas, leido_at), `pedidos`, `leidos`, `parcial`, `tope_entradas`, `entradas_guardadas`, `entradas_recortadas`. NULL = rastreo anterior o sin sitemaps. Migracion en `init_db.py`.

`jobs.adapters` (JSON, decision 161): `{"wordpress": {...}}` lo escribe el spider (detectado, rest abierta/cerrada/bloqueada_por_robots/no_detectado, codigo, raiz, plugin_seo, namespaces, evidencia, tipos con total/paginas/leidas/recortado, peticiones, cortada, sondas) y el analisis le anade `analisis`. NULL = rastreo anterior o adaptador apagado. Migracion en `init_db.py`.

`jobs.sondas_resumen` (JSON, decisiones 123 y 124): lo escribe el analisis. `enlaces_externos` (distintos, comprobados, rotos, dominios_muertos, por_veredicto) y `recursos` (comprobados, rotos, por_veredicto). NULL = sin sondas. Migracion en `init_db.py`.

`jobs.soft404` (JSON, decision 105): `sondas` las escribe el spider (lo que el sitio responde a una URL que no existe) y `resumen` el analisis. Las sondas NO son URLs del censo. Migracion en `init_db.py`.

`jobs.cliente_id` / `jobs.profile_id` son las FK al cliente y al perfil; `jobs.client_id` sigue siendo el texto libre de siempre (decision 83).

URL dedup: SHA-256 hash per `(job_id, url_hash)` unique constraint.

`jobs.comparacion.trafico` (JSON, decisiones 158 y 165): el trafico de Google entre ese censo y `comparado_con`, de `shared/trafico_entre_censos.py`. Con `pendiente` espera a los datos y lo completa el worker tras la descarga de Search Console; al completarse lleva `aviso` = `{estado: por_enviar | enviado | no_enviado, en}`, la marca de que el aviso de Slack ya se intento (una sola vez). `contenido_por_hash` dice si el contenido se comparo por `content_hash` (misma `crawler_version` y config de extraccion en los dos censos) o por palabras. Un perfil competidor no tiene trafico.
