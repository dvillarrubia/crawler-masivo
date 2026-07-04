# Plan de mejoras aditivas — crawler-masivo

Documentación de trabajo para Claude Code. El repo funciona bien en producción: **todo lo de este plan suma, nada rompe**. Ninguna tarea cambia el comportamiento por defecto de un job existente; lo nuevo entra detrás de flags de configuración o como tablas/columnas/endpoints nuevos.

---

## 0. Reglas de oro (leer antes de tocar nada)

1. **Compatibilidad total hacia atrás.** Los jobs existentes, sus resultados, el export CSV y el frontend actual siguen funcionando sin cambios. Prohibido renombrar columnas, issue types o campos de API existentes; prohibido cambiar la semántica de un issue existente sin flag.
2. **Migraciones aditivas e idempotentes.** El repo no usa Alembic: `scripts/init_db.py` hace `create_all` + bloque de `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Seguir ese patrón exacto: tablas nuevas vía modelos SQLAlchemy (las crea `create_all`), columnas nuevas sobre tablas existentes vía `ADD COLUMN IF NOT EXISTS` en el bloque de migraciones de `init_db.py`. Columnas nuevas siempre `nullable` o con default.
3. **Flags en `JobConfig`** (`api/schemas.py`). Toda feature nueva que altere el crawl o el análisis se activa con un campo nuevo de `JobConfig` cuyo **default reproduce el comportamiento actual**.
4. **Issue types nuevos, nunca reciclados.** Los issues nuevos se añaden a la tabla `issues` con `issue_type` nuevo. Un frontend que no los conozca simplemente los lista; no hay que tocarlo.
5. **La normalización de URL es sagrada.** `normalize_url` en `crawler/seo_crawler/extractors.py` alimenta `url_hash`, que es la clave de deduplicación y de todos los joins. Cualquier cambio de normalización invalida la comparación con jobs antiguos. Por eso T8 la parametriza por job y guarda la huella de la configuración, sin cambiar el default.
6. **Tests con cada tarea.** El repo tiene poco test de Python (la carpeta `tests/` es de inspección del frontend). Cada tarea crea sus tests bajo `tests/python/` con pytest; los checks del analyzer se testean con una sesión SQLite/Postgres de fixtures. Si un test necesita red, se mockea.
7. **Convenciones del repo.** SQLAlchemy 2.0, sesiones vía `shared/database.py`, issues con el helper `_add_issue` del analyzer, logging con `logger` del módulo, docstrings en inglés como el resto del código. El análisis se dispara automáticamente al acabar el worker: los análisis nuevos se registran al final de `run_all()`.
8. **Orden de ejecución: ver apéndice D.** Las tareas son independientes salvo dependencias explícitas; dentro de cada tarea: modelo de datos → crawler/analyzer → API → tests.

---

## T1 — ingesta de sitemaps

**Objetivo:** conocer las URLs declaradas en `sitemap.xml` para marcar `in_sitemap`, capturar `lastmod` y habilitar la detección real de huérfanas (T2).

**Flag:** `JobConfig.ingest_sitemaps: bool = False` (default off = comportamiento actual intacto).

**Modelo de datos (aditivo):**
- Columnas nuevas en `urls`: `in_sitemap BOOLEAN NULL`, `sitemap_lastmod TIMESTAMPTZ NULL`.
- Tabla nueva `sitemap_urls` (por si el sitemap declara URLs que el crawl nunca alcanza — imprescindible para T2): `id, job_id FK, url TEXT, url_hash VARCHAR(64), lastmod TIMESTAMPTZ NULL, sitemap_source TEXT`. Índice `(job_id, url_hash)`.

**Implementación:**
- Módulo nuevo `crawler/seo_crawler/sitemap_ingest.py`: descubre sitemaps desde `robots.txt` (línea `Sitemap:`) y `/sitemap.xml`; soporta índices de sitemaps anidados y `.xml.gz`; normaliza cada URL con la **misma** `normalize_url` + `compute_url_hash` del repo; inserta en `sitemap_urls` en lotes.
- Se ejecuta al inicio del job (desde `worker.py` o `seo_spider.py` en `start_requests`, antes del crawl) solo si el flag está activo. Los fallos de sitemap no abortan el crawl: log warning y continuar.
- Al cerrar el job, un paso en el analyzer cruza `sitemap_urls` con `urls` por `(job_id, url_hash)` y rellena `in_sitemap` / `sitemap_lastmod`.

**Issues nuevos:** `in_sitemap_not_crawled` (warning; URL en sitemap sin fila 2xx en `urls` — usar `details` con el motivo si se conoce: excluida por patrón, profundidad, error) y `crawled_not_in_sitemap` (info; HTML indexable no declarado).

**Criterios de aceptación:** job con flag off → cero cambios en BD y resultados. Job con flag on contra un sitio de fixtures con índice + gzip → `sitemap_urls` pobladas, flags correctos, ambos issues emitidos donde toca. Sitemap 404 → el crawl termina igual.

---

## T2 — huérfanas de verdad (sin romper `orphan_page`)

**Contexto:** hoy `analyzer.analyze_links` emite `orphan_page` para HTML con 0 inlinks. Conceptualmente eso es "página sin enlaces entrantes"; una huérfana real es una URL **conocida por fuentes externas (sitemap, GSC) pero inalcanzable por el crawl**, y por tanto hoy es indetectable.

**Diseño aditivo:**
- `orphan_page` **no se toca**: mismo trigger, misma semántica, mismos consumidores contentos.
- Issue nuevo `orphan_not_in_crawl` (warning): URLs de `sitemap_urls` (T1) — y de `gsc_metrics` cuando exista T9 — cuyo `url_hash` no tiene fila en `urls` con contenido rastreado. `details = {"seen_in": ["sitemap"|"gsc"], "lastmod": ...}`.
- Issue nuevo `no_inlinks_with_traffic` queda reservado para T9 (0 inlinks × clics GSC > 0).
- En la documentación del API (`docstring` de `list_issues`), aclarar la diferencia entre ambos tipos.

**Nota de implementación:** las URLs de `orphan_not_in_crawl` no tienen `url_id` en `urls`. Opciones: (a) insertar en `urls` una fila mínima con `status_group='not_crawled'` — preferida, porque mantiene la FK de `issues` y aparece en el explorador; documentar el nuevo `status_group`; (b) tabla aparte. Elegir (a) salvo que rompa alguna query existente (verificar `get_stats` y el export CSV: filtrar `status_group='not_crawled'` de los totales de rastreo). **Crítico:** estas filas se insertan con `is_html = False` e `inlinks_count = NULL`; si entraran como HTML con 0 inlinks dispararían también el `orphan_page` antiguo y cada huérfana real generaría dos issues contradictorios. Test explícito: una URL con `orphan_not_in_crawl` jamás tiene además `orphan_page`.

**Criterios de aceptación:** con T1 activo y un sitemap que declara 3 URLs no enlazadas desde ninguna parte → 3 issues `orphan_not_in_crawl`; `orphan_page` sigue comportándose exactamente igual que antes en el mismo job.

---

## T3 — PageRank v2: nofollow diluyente y decay en redirecciones

**Contexto:** `compute_pagerank` filtra `Link.follow.is_(True)`, con lo que los nofollow desaparecen del cálculo y su equity se redistribuye entre los follow (sculpting pre-2009). Además un enlace hacia una URL 301 transmite como si el destino fuera final.

**Flag:** `AnalysisThresholdsConfig.pagerank_version: int = 1` (o campo propio en `JobConfig`); `1` = algoritmo actual bit a bit, `2` = el nuevo. El resultado se escribe en la misma columna `urls.pagerank` sea cual sea la versión (el consumidor no distingue), y la versión usada se guarda en `jobs.config`.

**Cambios en `compute_pagerank` (solo rama v2):**
1. Traer **todos** los enlaces internos, sin filtrar por `follow`.
2. `out_total_weight[src]` suma el peso de todos los salientes (follow + nofollow): el nofollow cuenta en el denominador.
3. En el bucle de transmisión, solo los follow reparten: la fracción de los nofollow se destruye (no se redistribuye ni entre los follow de la página ni como dangling).
4. **Colapso de redirecciones:** antes de construir aristas, resolver destinos 3xx hasta el destino final usando `urls.redirect_url` (limitado a N=10 saltos; los bucles se cortan y se ignora la arista). Cada salto aplica un factor `redirect_decay = 0.9` al peso de la arista. Las URLs 3xx dejan de acumular PageRank propio en v2 (son pass-through).
5. Mantener: exclusión de self-links (`src != dst`), deduplicación src→dst por peso máximo, pesos por posición, dangling nodes, normalización 0–10.
6. **Mapa de pesos por tipo de arista** (sustituye al mapa por posición cuando T22 esté disponible; configurable por cliente): contextual 1.0, listado 0.6, paginación 0.4, breadcrumb 0.3, sidebar 0.25, menú 0.2, footer 0.1. Sin T22 se usa el mapa por posición actual — el clasificador fino mejora el cálculo, no lo bloquea.
7. **Alcance solo-indexables (definitorio de v2):** el grafo del cálculo incluye únicamente nodos indexables; las aristas hacia no indexables, redirecciones y 4xx/5xx se excluyen del reparto y su peso se destruye (dilución, coherente con el tratamiento del nofollow). La suma de peso destruido por página y por segmento se reporta como **fuga de equity** — issue `equity_leak` (warning) por encima de umbral configurable. Es la versión sin logs del desperdicio de presupuesto: cuánta autoridad interna se tira hacia destinos sin valor.

**Tests:** grafo de juguete de 6 nodos con casos calculados a mano: (a) página con 1 follow + 1 nofollow transmite la mitad que con 2 follow; (b) A→B(301)→C entrega a C con decay 0,9 y B queda pass-through; (c) `pagerank_version=1` produce exactamente los valores del algoritmo actual (test de regresión con snapshot).

---

## T4 — meta refresh y redirecciones JS

**Objetivo:** detectar redirecciones invisibles al fetch estático.

- **Meta refresh (siempre activo, es solo extracción):** en `extractors.py`, extraer `<meta http-equiv="refresh">`, parsear delay y URL destino. Columnas nuevas en `html_meta`: `meta_refresh_url TEXT NULL`, `meta_refresh_delay INT NULL`. Issue nuevo `meta_refresh_redirect` (warning si delay ≤ 5, info si mayor).
- **Redirección JS (solo con `render_js=true`):** en el handler de Playwright, comparar la URL final del navegador con la solicitada (tras descontar redirecciones HTTP ya conocidas). Si difieren, columna nueva `urls.js_redirect_url TEXT NULL` + issue `js_redirect` (warning). No requiere flag nuevo: si no hay render, simplemente no se evalúa.

**Criterios de aceptación:** fixture HTML con meta refresh de 0 s → issue emitido con destino en `details`. Página que hace `location.replace` en JS con render activo → `js_redirect` con la URL final. Jobs sin render: cero cambios.

---

## T5 — frescura y cambio de contenido entre jobs

**Objetivo:** saber qué URLs han cambiado de contenido entre dos crawls del mismo cliente, cruzando `body_hash` (ya existe) por `url_hash`.

**Sin flag** (es un endpoint de lectura, no toca el crawl).

**API nueva:** `GET /api/jobs/{job_id}/freshness?compare_to={job_id_anterior}` → por URL: `body_changed: bool`, `last_modified` (cabecera, ya capturada), `sitemap_lastmod` (T1 si existe), `first_seen_at`. Validar que ambos jobs comparten `client_id`.

**Issue opcional** (emitido solo si el job se lanza con `compare_to_job_id` en config): `stale_lastmod` — el `sitemap_lastmod` declara cambio pero `body_hash` es idéntico al del job anterior, o al revés (contenido cambiado con lastmod antiguo). Es señal de sitemaps que mienten, muy útil para diagnóstico de rastreo.

**Criterios de aceptación:** dos jobs de fixtures con 1 URL cambiada → el endpoint la señala y solo a ella; jobs de clientes distintos → 422.

---

## T6 — soft 404

**Objetivo:** detectar páginas 200 que en realidad son "no encontrado".

**Flag:** `JobConfig.detect_soft_404: bool = False`.

**Implementación:**
- Al inicio del job (con el flag activo), el spider solicita una URL aleatoria inexistente por host (`/__soft404_probe_<uuid>`). Si el host responde 404 real, se guarda el `body_hash` y el texto de la plantilla de error en el job (p. ej. `jobs.config["_soft404_signature"]` o tabla auxiliar `soft404_signatures (job_id, host, body_hash, sample_text)`), y esa fila NO entra en `urls`.
- En el analyzer, paso nuevo `analyze_soft_404`: marca `soft_404` (error) en páginas 200 HTML que cumplan cualquiera de: (a) `body_hash` igual a la firma del probe; (b) similitud alta con el texto del probe (umbral configurable, comparación barata tipo ratio de tokens compartidos — sin embeddings en esta tarea); (c) `word_count` < umbral **y** el title contiene patrones de error (lista configurable: "404", "no encontrado", "not found", "página no existe").
- Si el probe devuelve 200 (el host ya sirve soft 404 en todo), emitir un issue a nivel de job en `details` del primer hallazgo y aplicar solo la heurística (c).

**Criterios de aceptación:** fixture con plantilla de error servida en 200 → marcada; página corta legítima (una landing mínima con title normal) → no marcada; flag off → paso no ejecutado.

---

## T7 — diff entre crawls y flapping

**Objetivo:** convertir el crawler de foto en película: comparar dos jobs del mismo cliente por `url_hash`.

**Sin flag** (lectura). **Es la tarea de más valor del plan.**

**Modelo de datos:** ninguno obligatorio en v1 del diff (se calcula on-the-fly con una query por campo); si el rendimiento lo pide en sitios grandes, materializar en tabla `job_diffs (job_a, job_b, url_hash, field, old_value, new_value)` con índice `(job_a, job_b, field)`. Empezar on-the-fly, materializar solo si hace falta.

**API nueva** (router nuevo `api/routers/diff.py`):
- `GET /api/diff?job_a=&job_b=` → resumen: nº URLs nuevas, desaparecidas, y cambios por campo (`status_group`, `indexable`, `canonical_href`, `title`, `crawl_depth`, `pagerank` con umbral de delta configurable, `body_hash`).
- `GET /api/diff/urls?job_a=&job_b=&change=status|indexable|canonical|title|depth|pagerank|content|new|gone` → paginado con old/new por URL, reutilizando el helper `_paginate` de `results.py`.
- Validaciones: mismos `client_id`, ambos `completed`.
- **Flapping:** `GET /api/diff/flapping?client_id=&last_n=4` → URLs cuyo `status_group` o `indexable` alterna (A→B→A) a lo largo de los últimos N jobs del cliente. Devolver la secuencia por URL.

**Notas:** comparar solo URLs con el mismo esquema de normalización — añadir a `jobs` la columna `normalization_fingerprint VARCHAR(64) NULL` (T8 la rellena; NULL = normalización por defecto, comparable entre sí). Si difieren, el endpoint responde 409 con explicación.

**Criterios de aceptación:** dos jobs de fixtures con 1 alta, 1 baja, 1 cambio de status y 1 cambio de title → el resumen cuadra exactamente; flapping detecta 200→404→200 en 3 jobs.

---

## T8 — política de parámetros de URL (normalización configurable)

**Objetivo:** eliminar parámetros de tracking y aplicar reglas por proyecto **sin cambiar el default**.

**Flag:** `JobConfig.url_normalization: UrlNormalizationConfig` con `strip_params: list[str] = []` (default vacío = comportamiento actual bit a bit), `strip_common_tracking: bool = False` (si true, aplica lista mantenida en `shared/constants`: utm_*, gclid, fbclid, msclkid, mc_cid, mc_eid…).

**Implementación:**
- `normalize_url` pasa a aceptar la config (con default que reproduce la firma actual). El spider la lee del job y la propaga; `sitemap_ingest` (T1) y cualquier consumidor de `compute_url_hash` usan la misma config del job — **un solo punto de verdad**.
- `normalization_fingerprint` = sha256 del JSON canónico de la config de normalización, guardado en `jobs` (columna de T7). Dos jobs son comparables si comparten fingerprint (o ambos NULL).
- **Tests de propiedad** (pieza clave de la tarea): la misma URL escrita de N formas (mayúsculas en host, params desordenados, con y sin utm, fragmento, trailing slash según w3lib) produce el mismo hash bajo la misma config; y la config default produce exactamente los hashes de hoy (test de regresión con lista dorada de 30 URLs y sus hashes actuales).

**Criterios de aceptación:** job sin config → hashes idénticos a los actuales (verificado por la lista dorada). Job con `strip_common_tracking` → `?utm_source=x` y la URL limpia colapsan en la misma fila de `urls`.

---

## T9 — GSC en el pipeline principal

**Objetivo:** sacar el conector de `POC_centro_semantico/src/gsc.py` a producción y unirlo al crawl por `url_hash`, desbloqueando huérfanas vía GSC, underlinked high-performers y striking distance.

**Modelo de datos:** tabla nueva `gsc_metrics (id, job_id FK, url TEXT, url_hash VARCHAR(64), query TEXT NULL, clicks INT, impressions INT, ctr FLOAT, position FLOAT, date_from DATE, date_to DATE)`. Dos granularidades en la misma tabla: filas con `query NULL` = agregado por página (para joins baratos), filas con query = detalle. Índices `(job_id, url_hash)` y `(job_id, query)`.

**Implementación:**
- Módulo `analysis/gsc_sync.py` reutilizando `_build_service`/`fetch_gsc_data` del POC (moverlos a `shared/` o importarlos; no duplicar código). Credenciales de service account por cliente en config del job o variable de entorno — nunca en el repo.
- **Capa de normalización de fuente:** GSC reporta sobre la URL canónica; antes de hashear, aplicar la misma `normalize_url` con la config del job (T8). Las URLs de GSC que no casan con ninguna fila de `urls` se conservan igualmente (son candidatas a huérfanas) y alimentan `orphan_not_in_crawl` con `seen_in: ["gsc"]` (T2).
- Se ejecuta como paso opcional post-análisis si el job trae `gsc_property` en config; su fallo no invalida el análisis del crawl (log + issue informativo a nivel de job).
- **Límites de la API:** Search Analytics devuelve máx. 25.000 filas por petición con paginación por `startRow`, tiene cuota diaria por propiedad y una ventana de 16 meses de histórico. El sync pagina hasta agotar o hasta un tope configurable de filas, y registra en `details` si el volcado quedó truncado — nunca truncar en silencio.

**Issues/derivados nuevos:**
- `no_inlinks_with_traffic` (warning): `inlinks_count = 0` × clics > 0 (reservado en T2).
- `underlinked_high_performer` (info): `pagerank` bajo (< percentil 25 del job) × clics altos (> percentil 75).
- Endpoint `GET /api/jobs/{id}/striking-distance`: URLs con posición media 5–15, ordenadas por impresiones desc y pagerank asc — la cola de trabajo de enlazado interno.

**Regla de fuente bloqueada:** si el job no trae `gsc_property`, estos issues y el endpoint devuelven estado `blocked` explícito (HTTP 200 con `{"status": "blocked", "reason": "gsc_not_configured"}`), nunca lista vacía silenciosa.

**Criterios de aceptación:** con fixtures de respuesta GSC mockeada → joins correctos incluyendo una URL GSC ausente del crawl que emite huérfana; sin `gsc_property` → estado bloqueado; el análisis del crawl termina aunque GSC falle.

---

## T10 — sugerencias de enlazado semántico y canibalización como issues

**Objetivo:** producir acción a partir del POC semántico: desde qué páginas enlazar cada objetivo, y la canibalización como issue firmable.

**Prerrequisito:** embeddings del job calculados con el backend Gemini existente (`gemini-embedding-001`, dim 1024, pgvector; es el único backend de runtime — respetar la factory). Si T11 está activo, las sugerencias pueden operar a nivel de chunk además de página.

**Modelo de datos:**
- `link_suggestions (id, job_id, target_url_hash, source_url_hash, cosine_similarity FLOAT, source_pagerank FLOAT, status VARCHAR(16) DEFAULT 'pending', decided_by TEXT NULL, decided_at TIMESTAMPTZ NULL)`. Estados: `pending | accepted | rejected`.
- Columnas en `issues`: `review_status VARCHAR(16) NULL` (`pending | signed | rejected`), `reviewed_by TEXT NULL`, `reviewed_at TIMESTAMPTZ NULL` — aditivas, NULL para todos los issues deterministas actuales (nada cambia para ellos).

**Implementación:**
- `analysis/link_suggester.py`: para cada página objetivo (configurable: por defecto las de pagerank < mediana con word_count > umbral), candidatas = páginas con `cosine_similarity ≥ umbral` (default 0,75, configurable) que **no** la enlazan ya (anti-join contra `links` por hashes), top-K (default 5) ordenadas por `similarity × pagerank_normalizado`. Excluir candidatas noindex o no-2xx.
- Canibalización: volcar los pares de `detect_cannibalization` del POC (umbral 0,92) a `issues` como `semantic_cannibalization` (warning) con `review_status='pending'` y las dos URLs + similitud en `details`.
- **API:** `GET /api/jobs/{id}/link-suggestions` (paginado, filtro por status) y `POST /api/link-suggestions/{id}/decision {status, decided_by}`. `POST /api/issues/{id}/review {review_status, reviewed_by}` para la firma de canibalizaciones.
- Regla dura: nada auto-acepta. Los checks de juicio nacen `pending` y solo un humano los pasa a `accepted/signed`.

**Criterios de aceptación:** fixture con 3 páginas semánticamente próximas donde A no enlaza a B → sugerencia B→A generada con score correcto; el par canibalizado aparece como issue `pending`; la decisión persiste con autor y fecha; los issues antiguos siguen con `review_status NULL` y el API los devuelve igual que siempre.

---

## T11 — chunking semántico persistido (embeddings Gemini)

**Contexto:** el chunking actual (`POC_centro_semantico/src/text_utils.py:chunk_text`) corta por tamaño (~500 palabras, solape 50) con conciencia de párrafo/frase, y los chunks son efímeros: solo se persiste un vector representativo por página (`SemanticPage.embedding`). Se quiere: cortes por **frontera semántica** (donde cambia el tema), chunks **persistidos** con su embedding, y todo sobre el backend Gemini existente. Es la base del análisis GEO a nivel de pasaje (los LLM citan pasajes, no páginas).

**Flag:** `chunking_strategy: Literal["fixed", "semantic"] = "fixed"` en la config del análisis semántico. Default `fixed` = comportamiento actual bit a bit; el vector representativo por página se sigue calculando igual con ambas estrategias (nada aguas abajo cambia).

**Algoritmo del corte semántico (`text_utils.py`, función nueva `semantic_chunk_text`):**
1. **Fronteras duras primero, del DOM:** los encabezados H2/H3 de la página (tabla `headings`, ya capturada con posición) son cortes obligatorios. Es la señal semántica gratuita más fiable en HTML y produce chunks que coinciden con lo que un LLM citaría (pasaje bajo su encabezado). Guardar el `heading_path` (H1 > H2 > H3) de cada chunk.
2. **Dentro de cada sección**, corte por embedding: dividir en frases (mejorar `_split_sentences` para abreviaturas del español: "Sr.", "núm.", "pág.", "etc."), formar ventanas deslizantes de 3 frases, embeber las ventanas con Gemini (RETRIEVAL_DOCUMENT, mismo batching), calcular la distancia coseno entre ventanas consecutivas y cortar donde la distancia supere el percentil configurable (default P90 de la propia página) — con mínimo y máximo de tamaño de chunk (defaults: 80 y 500 palabras) para evitar fragmentos y chunks desbocados.
3. **Embedding del chunk final:** dos modos vía `chunk_embedding_mode: Literal["reembed", "aggregate"] = "aggregate"`. `aggregate` = media L2-normalizada de las ventanas del chunk (cero llamadas extra: reutiliza los embeddings del paso 2); `reembed` = segunda pasada embebiendo cada chunk completo (mejor fidelidad, duplica coste de API). Documentar el trade-off en el docstring.
4. El **vector de página** mantiene la estrategia actual de chunk representativo contra el centroide (`_representative_vector`), alimentada por los chunks de la estrategia activa.

**Modelo de datos (aditivo):** tabla nueva `semantic_chunks (id, analysis_id FK, url_id FK, position INT, heading_path TEXT NULL, text TEXT, word_count INT, char_start INT, char_end INT, embedding Vector(1024), strategy VARCHAR(16))`. Índice `(analysis_id, url_id, position)`; índice HNSW sobre `embedding` (pgvector ya está habilitado en `init_db.py`).

**Coste y límites:** con `aggregate` el número de llamadas es ~el mismo que hoy (las ventanas sustituyen a los chunks fijos); con `reembed`, aproximadamente el doble. Mantener el batching (100) y el backoff con tenacity ya implementados en `GeminiBackend._embed_batch`. Registrar en `SemanticAnalysis.config` la estrategia, el percentil y el modo usados (reproducibilidad).

**Nota de espacios vectoriales:** los embeddings de Gemini (1024d) no son comparables con vectores de otros modelos. Cualquier cruce con sistemas externos que usen otro modelo se hace por `url_hash`/entidad, nunca por similitud entre espacios distintos. Un solo modelo de embeddings por instalación.

**Criterios de aceptación:** con `fixed` → resultados idénticos a los actuales (test de regresión sobre una página dorada). Con `semantic` sobre un fixture con tres temas claramente separados por H2 → tres chunks como mínimo, cada uno con su `heading_path`; sobre un texto plano sin encabezados con dos temas → el corte cae en la frontera temática (verificable con fixture sintético: N frases de cocina + N de fiscalidad → 2 chunks). Los chunks persisten con offsets correctos (`text == body[char_start:char_end]` tras normalización de espacios documentada). `aggregate` no aumenta las llamadas a la API respecto al conteo de ventanas (verificable con mock del cliente).

---

## T12 — motor de segmentación

**Contexto:** no existe (solo `folder_depth`). Es la pieza central de las plataformas de referencia: todo informe se corta por segmento (plantilla, categoría, idioma, funnel). Sin ella, un sitio de 500.000 URLs es una lista plana.

**Modelo de datos:** tablas nuevas `segments (id, client_id, name, rule_type VARCHAR(16), rule TEXT, priority INT, created_at)` — `rule_type` ∈ `prefix | regex`; y `url_segments (job_id, url_id, segment_id)` con índice `(job_id, segment_id)`. Los segmentos viven a nivel de **cliente**, no de job: se definen una vez y se aplican a cada crawl nuevo.

**Implementación:**
- Paso nuevo del analyzer `assign_segments()` (al principio de `run_all`, antes de los checks): para cada URL HTML, evaluar las reglas del cliente por prioridad; primera coincidencia gana; sin coincidencia → segmento implícito `(sin segmento)`. Regex precompiladas; una sola pasada.
- **API:** CRUD de segmentos (`GET/POST/PUT/DELETE /api/clients/{client_id}/segments`) con **vista previa** obligatoria: `POST /api/clients/{id}/segments/preview` devuelve cuántas URLs del último job captura cada regla antes de guardarla (evita reglas que capturan todo o nada).
- Parámetro `segment_id` en los endpoints existentes de lectura (`list_urls`, `list_issues`, `get_stats`) como filtro **opcional**: sin él, comportamiento idéntico al actual.
- El diff de T7 acepta `segment_id` y devuelve el resumen por segmento.

**Criterios de aceptación:** reglas `^/blog/` y `^/producto/` sobre fixtures → asignación correcta con prioridad respetada; `get_stats?segment_id=` cuadra con el recuento manual; endpoints sin el parámetro devuelven byte a byte lo de antes; re-crawl del mismo cliente reasigna sin duplicar filas.

---

## T13 — detección de trampas de rastreo

**Contexto:** la única defensa actual son `max_depth`, `max_urls` y los patrones include/exclude manuales. En el primer e-commerce con navegación facetada o calendario infinito, el presupuesto del job se evapora en URLs basura. Esta tarea protege el propio crawler, no solo informa.

**Flag:** `JobConfig.trap_detection: TrapDetectionConfig` con `enabled: bool = False`, `max_urls_per_pattern: int = 500`, `max_param_combinations: int = 3`.

**Implementación (en el spider, `seo_spider.py`):**
- **Firma de patrón** por URL: plantilla derivada sustituyendo segmentos numéricos y valores de parámetros por comodines (`/producto/123?color=rojo&talla=m` → `/producto/*?color=*&talla=*`). Contador en memoria por firma.
- Cuando una firma supera `max_urls_per_pattern`, las URLs nuevas de esa firma dejan de encolarse y se registran en tabla nueva `crawl_trap_events (job_id, pattern, urls_seen, urls_skipped, first_url_sample)`.
- Heurísticas adicionales: nº de parámetros distintos combinados > `max_param_combinations` en la misma ruta; profundidad de una misma firma creciendo sin aportar firmas nuevas (calendario infinito).
- **Nada se pierde en silencio:** issue a nivel de job `crawl_trap_detected` (warning) con el patrón y la muestra, para que el analista decida si añadir un exclude o subir el límite y relanzar.

**Criterios de aceptación:** fixture con facetas combinatorias (3 parámetros × 10 valores) → el job termina, encola como máximo el límite por patrón y registra el evento; con el flag off el comportamiento es el actual (y el test lo demuestra encolando todo).

---

## T14 — near-duplicates

**Contexto:** `body_hash` solo detecta duplicados exactos. El caso real de e-commerce —fichas idénticas salvo una talla— pasa de largo.

**Flag:** `AnalysisThresholdsConfig.near_duplicate_detection: Literal["off", "simhash", "embeddings"] = "off"`.

**Implementación:**
- **`simhash` (barato, sin API):** simhash de 64 bits sobre shingles de palabras del texto del cuerpo (dependencia ligera `simhash` o implementación propia de ~40 líneas, preferida para no añadir dependencia). Columna nueva `urls.simhash BIGINT NULL`, calculada en pipeline. En el analyzer, agrupación por distancia de Hamming ≤ umbral (default 3) usando bucketing por bandas (no O(n²)).
- **`embeddings` (preciso, requiere análisis semántico):** pares con similitud coseno ≥ 0,92 desde los vectores Gemini ya calculados — reutiliza la matriz del POC, sin llamadas extra. Solo disponible si el job tiene análisis semántico.
- Issue nuevo `near_duplicate_content` (warning) con `details = {cluster_id, method, score, urls}`. El `duplicate_content` exacto existente no se toca.

**Criterios de aceptación:** dos fichas que difieren en una palabra → detectadas por simhash y no por `body_hash`; dos páginas distintas de temática similar → no marcadas con umbral default; `off` → cero cambios.

---

## T15 — GEO readiness: crudo vs. renderizado

**Contexto:** el repo captura structured data y puede renderizar JS, pero nunca compara ambos mundos. El hallazgo clave del trabajo GEO previo: **lo que solo existe tras ejecutar JS es invisible para los crawlers de IA** (y para el primer pase de Google). Esta tarea mejora features existentes (`StructuredData`, render) en vez de añadir módulos.

**Flag:** `JobConfig.geo_analysis: bool = False`. Requiere `render_js=true` (validar en el schema: si `geo_analysis` sin render → 422 con mensaje claro).

**Implementación:**
- Con el flag activo, el handler conserva **ambos** HTML (el crudo ya viaja por Scrapy; el renderizado sale de Playwright) y el pipeline calcula por página: `content_requires_js` (ratio de texto del cuerpo presente solo en el DOM renderizado, umbral configurable), y `schema_requires_js` (bloques JSON-LD presentes en renderizado pero ausentes en crudo). Columnas nuevas en `urls`: `raw_word_count INT NULL`, `js_content_ratio FLOAT NULL`; columna nueva en `structured_data`: `visible_without_js BOOLEAN NULL`.
- Issues nuevos: `content_only_after_js` (error si el ratio supera el umbral — el contenido principal no existe para un fetcher sin JS) y `schema_only_after_js` (warning — el marcado existe pero los crawlers de IA no lo ven).
- En `get_stats`, bloque nuevo opcional `geo` con los agregados (solo si el job tiene el flag; si no, ausente — nada de ceros falsos).

**Criterios de aceptación:** fixture SPA cuyo cuerpo solo existe tras JS → `content_only_after_js` con ratio ≈ 1; página con JSON-LD inyectado por GTM → `schema_only_after_js`; página server-side rendered → ningún issue; jobs sin el flag → columnas NULL y stats sin bloque `geo`.

---

## T16 — robots.txt versionado, watchlist y umbrales adaptativos

Tres mejoras pequeñas de prevención que comparten tarea:

- **Snapshot de robots.txt:** tabla nueva `robots_snapshots (job_id, host, content TEXT, content_hash, fetched_at)`. El spider ya descarga robots.txt (modo `respect`/`audit`): persistirlo. En el diff de T7, si el hash cambia entre jobs → entrada destacada `robots_txt_changed` con el diff textual en `details`. Los desastres silenciosos de indexación suelen empezar aquí.
- **Watchlist:** tabla `watchlist (client_id, url, url_hash, label)` + CRUD mínimo. En cada job, paso del analyzer que verifica cada URL de la lista (status 200, indexable, canonical self) y emite `watchlist_check_failed` (error) al primer incumplimiento. Es la «sanity check» de páginas de negocio.
- **Umbrales adaptativos (sugerencia, no imposición):** endpoint `GET /api/clients/{id}/suggested-thresholds` que calcula, a partir del último job completado, valores sugeridos para `analysis_thresholds` (p. ej. `min_word_count` = P10 del word count de páginas indexables). Solo sugiere: el default de los jobs no cambia jamás.

**Criterios de aceptación:** cambio de una línea en robots.txt entre dos jobs → detectado con diff legible; URL de watchlist que pasa a 404 → issue error; el endpoint de sugerencias devuelve valores coherentes con los percentiles del fixture.

---

## T17 — endurecimiento de lo que ya existe

Mejoras sobre código actual, sin features nuevas de cara al usuario. Cada punto es un PR independiente y pequeño.

1. **PageRank a escala.** `compute_pagerank` itera con dicts de Python: a 1M de nodos (el objetivo declarado en `millones-de-URL.md`) es inviable. Añadir camino vectorizado con `scipy.sparse` (matriz CSR + power iteration), con conmutación automática por tamaño (`n > 50.000` → sparse) y test de equivalencia numérica contra la implementación actual (tolerancia 1e-6) en un grafo mediano. La implementación actual se conserva como referencia y para grafos pequeños.
2. **Re-análisis sin re-crawl.** No existe: si cambias un umbral hay que rastrear de nuevo. Endpoint `POST /api/jobs/{id}/reanalyze` (acepta `analysis_thresholds` opcionales que se fusionan sobre los del job) → borra issues del job (el analyzer ya lo hace con `clear_existing_issues`) y relanza `run_all` en background. Los datos de crawl son inmutables; solo se recalcula el análisis. Barato de construir y elimina la fricción diaria más tonta.
3. **Percentiles de latencia en stats.** `response_time_ms` ya existe por URL pero `get_stats` no lo agrega. Añadir p50/p90/p99 global y por `status_group` al response de stats (campo nuevo opcional, aditivo), e issue `slow_page` (warning) por encima de umbral configurable (default 3000 ms). Es la semilla de la «salud de rastreo» del esquema.
4. **Cadenas de canonicals.** `analyze_canonicals` valida el destino pero no sigue la cadena: A canonical→ B canonical→ C queda sin detectar. Añadir resolución transitiva (límite 5 saltos) e issues `canonical_chain` (warning) y `canonical_loop` (error), espejo de lo que ya hace `analyze_redirect_chains`.
5. **Posición de enlace más fina y contexto DOM.** `_detect_link_position` mira clases CSS de ancestros; ampliar en dos pasos. (a) Elementos semánticos reales primero (`<nav>`, `<header>`, `<footer>`, `<aside>`, `<main>`, `<article>`), más fiables que las clases, y categoría nueva `sidebar`. (b) **Capturar el contexto DOM completo de cada enlace** en el extractor: primer ancestro semántico y clases/id del contenedor inmediato, en dos columnas nuevas de `links` (`dom_ancestor VARCHAR(16) NULL`, `dom_container TEXT NULL`). Es el único cambio de crawler que necesita T22; todo lo demás de la capa de arquitectura es post-proceso. Añadir peso `sidebar: 0.25` al mapa de `_POSITION_WEIGHT` (solo rama v2 de T3, para no alterar la v1).
6. **Splitter de frases para español.** `_split_sentences` corta en `Sr.`, `núm.`, `pág.`, `EE. UU.`. Lista de abreviaturas protegidas antes del split (afecta a chunking fijo y semántico; test con frases doradas). Ya apuntado en T11; extraerlo aquí como PR propio porque también mejora el chunking actual.
7. **Export de issues y links.** El CSV streaming solo exporta URLs. Reutilizar `_stream_csv` para `GET /api/jobs/{id}/export?entity=issues|links`. Parámetro nuevo con default `urls` = comportamiento actual.

**Criterios de aceptación:** cada punto lleva su test; el 1 exige equivalencia numérica entre implementaciones; el 2 demuestra que los datos de crawl no cambian tras re-analizar; el resto, regresión de que el default no varía.

---

## T22 — clasificador de aristas y grafo agregado

**Contexto (spec de arquitectura §1–2):** el repo guarda cada enlace como ocurrencia individual en `links` con una posición gruesa. Para análisis de arquitectura hace falta una taxonomía fina de tipos de enlace y una vista agregada del grafo que no se ahogue con los sitewide (un menú de 15 ítems en 100.000 páginas son 1,5 M de filas idénticas).

**Prerrequisito:** T17.5.b (columnas `dom_ancestor` y `dom_container` en `links`).

**Flag:** `JobConfig.edge_classification: bool = False`.

**Modelo de datos (aditivo):**
- Columna nueva `links.edge_class VARCHAR(16) NULL` — valores: `contextual | listado | breadcrumb | paginacion | menu | footer | sidebar | desconocido`. **No** se toca `link_position` (sus 4 valores actuales tienen consumidores); `edge_class` es la taxonomía fina que convive con la gruesa.
- Tabla agregada `arch_edges (job_id, source_hash, target_hash, edge_class, n_pages INT, sitewide BOOL, anchor_sample TEXT)` con PK `(job_id, source_hash, target_hash, edge_class)`: deduplica el par por tipo y acumula en `n_pages`. Se materializa en el analyzer desde `links`; el detalle por ocurrencia sigue en `links` intacto.

**Clasificador (paso nuevo del analyzer, tres pasadas):**
1. **Regla DOM:** ancestro `nav`/`header` → `menu`; `footer` → `footer`; `aside` o container con `sidebar` → `sidebar`; container con `breadcrumb` o marcado BreadcrumbList (tabla `structured_data`) → `breadcrumb`; container con `pagination|page-numbers` o `rel=prev/next` → `paginacion`; container con `related|listing|grid|card|archive` → `listado`; ancestro `main`/`article` sin match anterior → `contextual`; sin señal → `desconocido`. Los selectores son **configurables por cliente** (tabla `client_selectors (client_id, edge_class, selector)`) porque cada CMS tiene sus clases.
2. **Regla estadística de sitewide:** destino que recibe el mismo enlace desde > 80 % de las páginas indexables (umbral configurable) → `sitewide = TRUE` y reclasificación a `menu` salvo que ya sea `menu`/`footer`. Corrige los `desconocido` y los falsos contextuales (banners de plantilla en el body).
3. **Regla de plantilla:** par (container, destino) idéntico en > 90 % de las páginas del mismo segmento (T12) → `listado` aunque viva en `main`. Distingue el enlace editorial real del módulo automático de relacionados — la distinción que importa para el diagnóstico.

**Criterios de aceptación:** fixture con menú, footer, migas, paginación, grid de relacionados y un enlace editorial en el cuerpo → las seis clases correctas; banner sitewide dentro de `main` → reclasificado por la pasada 2; con el flag off → `edge_class` NULL en todo y cero cambios; `links` conserva exactamente las mismas filas con y sin flag.

---

## T23 — arquitectura: click depth real, flujo entre secciones y checks ARQ

**Contexto (spec §3–4):** `crawl_depth` es profundidad de **descubrimiento** (depende del orden del crawl y de las semillas), no de clics desde la home. Y no existe ninguna vista de cómo fluye la autoridad entre secciones del sitio.

**Prerrequisitos:** T3 (PageRank v2), T12 (segmentación — hace de `tipo_pagina`/sección: **no se crea una segunda taxonomía**), T22 (clases de arista). Columna nueva en `segments`: `is_business BOOLEAN DEFAULT FALSE` (marca las secciones de negocio para los checks).

**Cálculos (post-proceso en el analyzer):**
- **Click depth:** BFS desde la home sobre todas las aristas (incluidas sitewide) → columna nueva `urls.click_depth SMALLINT NULL`, distinta de `crawl_depth` y documentada como tal. Las indexables sin profundidad asignada (alcanzadas solo por sitemap) son **huérfanas de enlazado** — tercer concepto de huérfana, distinto de `orphan_page` (0 inlinks) y de `orphan_not_in_crawl` (fuera del crawl); issue nuevo `link_orphan` (warning). Documentar los tres en el mismo sitio para que nadie los confunda.
- **Contadores por clase:** `in_contextual`, `out_contextual` por URL (desde `arch_edges`), columnas nuevas en `urls`.
- **Flujo entre secciones:** para cada arista agregada, `flujo = d × PR(origen) × peso / Σ pesos salientes(origen)`; agregación segmento→segmento en tabla `section_flows (job_id, segment_from, segment_to, flow FLOAT)`. Es la matriz que alimenta el mapa de bloques y flechas del frontend (a partir de ~2.000 URLs, la vista por defecto es esta, no el grafo nodo a nodo). Los sitewide se reparten analíticamente en memoria durante el PageRank, sin materializar aristas físicas.

**Checks nuevos (deterministas, sin firma):**

| issue | definición |
|---|---|
| `link_orphan` | indexable sin click depth (solo alcanzable por sitemap) |
| `excessive_click_depth` | indexable a ≥ N clics (default: 4 en segmentos `is_business`, 5 en el resto; en `analysis_thresholds`) |
| `no_contextual_inlinks` | página de segmento de negocio con `in_contextual = 0` |
| `authority_sink` | página con `pagerank > p50` y `out_contextual = 0` — acumula autoridad y no la reparte |
| `deep_pagination` | cadena de aristas `paginacion` de longitud > K (default 3) |
| `hierarchy_imbalance` | a nivel de job: el reparto de PageRank entre segmentos de negocio y soporte incumple el umbral declarado (`details` con la distribución completa) |

`equity_leak` ya queda definido en T3.7. En `details` de los checks masivos, incluir cuando sea posible la **corrección de plantilla** que resuelve el grupo entero (p. ej. `authority_sink` masivo en posts → un bloque de relacionados en la plantilla mueve miles de enlaces con un solo cambio): el analista prescribe a nivel de plantilla, no URL a URL.

**Criterios de aceptación:** fixture con home → categoría → ficha a 3 clics pero descubierta a depth 1 por sitemap → `click_depth = 3 ≠ crawl_depth`; página en sitemap sin ningún camino de clics → `link_orphan` y ni rastro de los otros dos tipos de huérfana; la matriz de flujos suma ≈ la masa de PageRank repartida (invariante de conservación con tolerancia); umbrales de negocio solo aplican a segmentos `is_business`.



---

# Aportaciones game changer (T18–T21)

Cuatro capacidades que ni Lumar ni Oncrawl tienen y que salen casi gratis de combinar piezas que el plan ya construye (PageRank propio, embeddings Gemini con task types asimétricos, chunks persistidos, GSC, segmentación). Son el motivo por el que merece la pena tener crawler propio en vez de pagar licencia.

## T18 — PageRank semántico y relevancia de anchors

**La idea:** el PageRank estructural trata igual un enlace contextual perfecto y un enlace de menú repetido en 500.000 páginas. Google no: su modelo de reasonable surfer pondera por probabilidad de clic y contexto. Con los vectores Gemini ya calculados por página, se puede ponderar cada arista por la **similitud semántica origen→destino** y obtener un segundo score.

**Implementación (extiende T3, solo rama v2):**
- Peso de arista v3 = `peso_posición × (α + (1−α) × cos(origen, destino))` con `α = 0.3` configurable. Requiere análisis semántico del job; si no existe, el score no se calcula (bloqueado explícito, no cero).
- Columna nueva `urls.pagerank_semantic FLOAT NULL`. Se calcula junto al estructural en la misma pasada sparse (T17.1): dos vectores de pesos, un solo grafo.
- **La métrica accionable es el delta:** `pagerank − pagerank_semantic`. Delta positivo grande = página sostenida por enlaces boilerplate sin respaldo contextual (frágil ante cambios de plantilla); delta negativo = página con enlazado contextual fuerte pero poco volumen (candidata a más enlaces de plantilla). Endpoint `GET /api/jobs/{id}/pagerank-delta` ordenado por |delta|, filtrable por segmento.
- **Relevancia de anchors:** los anchors únicos internos se embeben con `embed_query` (ya existe, task type RETRIEVAL_QUERY — el lado correcto de la asimetría: un anchor funciona como una consulta hacia el destino). Los anchors únicos de un sitio suelen ser pocos miles: coste marginal. Columna `links.anchor_relevance FLOAT NULL` = cos(anchor, página destino). Issues nuevos: `generic_anchor` (info; anchor en lista de genéricos: "ver más", "aquí", "click", configurable) y `anchor_target_mismatch` (warning; relevancia < umbral **solo en enlaces con `link_position='content'`** — el boilerplate se excluye a propósito).
- Cierre del círculo con T10: cada sugerencia de enlace incluye el **anchor propuesto** (la frase del chunk origen más similar a la página destino).

**Por qué es game changer:** convierte "esta página está infra-enlazada" en "esta página está enlazada desde los sitios equivocados con los textos equivocados, y aquí están los correctos". Es la diferencia entre auditar y prescribir.

## T19 — cobertura consulta→pasaje (fan-out a nivel de chunk)

**La idea:** la unidad de recuperación de Google (passage ranking) y de los LLM (citas) es el pasaje, no la página. Con los chunks semánticos de T11 persistidos y las queries de GSC de T9, se puede responder la pregunta que ninguna plataforma responde: **¿qué consultas con demanda real no tienen ningún pasaje que las cubra?**

**Implementación:**
- Embeber las queries de GSC con impresiones > umbral usando `embed_query` (asimetría correcta de serie). Tabla nueva `query_embeddings (job_id, query, impressions, clicks, position, embedding Vector(1024))`.
- Matriz de cobertura query×chunk vía pgvector (HNSW ya indexado en T11): para cada query, el mejor chunk del sitio y su similitud.
- Tres salidas, todas cortables por segmento:
  1. **Gap de pasaje:** queries con impresiones y posición > 10 cuyo mejor chunk queda por debajo del umbral → no existe contenido que las cubra a nivel de pasaje. Es la cola de briefs de contenido con demanda demostrada.
  2. **Pasaje enterrado:** queries cuyo mejor chunk vive en una página distinta de la que GSC dice que posiciona → el contenido correcto existe pero Google sirve la página equivocada (hermana de la canibalización, invisible a nivel de página).
  3. **Chunk huérfano de demanda:** chunks sin ninguna query cercana → contenido sin demanda aparente, candidato a consolidación.
- Endpoint `GET /api/jobs/{id}/query-coverage` con las tres vistas. Las salidas 1 y 2 entran como issues de juicio (`review_status='pending'`, patrón de T10): decidir si falta contenido o falta enlazado es criterio humano.

**Por qué es game changer:** es la mecánica de la era GEO (query fan-out, citación por pasaje) aplicada con datos propios de demanda en vez de con scoring ciego. La "Content Lens" de Oncrawl puntúa contenido; esto casa demanda real con pasajes reales.

## T20 — contenido único real (descuento de boilerplate)

**La idea:** el `word_count` actual cuenta menú, footer y bloques repetidos de plantilla. El thin content de un e-commerce se mide mal: una ficha con 400 palabras de las que 320 son plantilla es thin, y hoy pasa el umbral.

**Implementación (usa T12 y la infraestructura de shingles de T14):**
- Por segmento, calcular los shingles de palabras (5-gramas) presentes en más del X % de las páginas del segmento (default 30 %) → conjunto de boilerplate del segmento.
- Por página: `unique_word_count` = palabras del cuerpo tras descontar shingles de boilerplate, y `boilerplate_ratio`. Columnas nuevas en `urls`, calculadas en el analyzer. Necesita el texto del cuerpo: reutilizar el que ya se extrae para `word_count`/`body_hash`; si no se persiste, guardar los hashes de shingles por página en tabla auxiliar y decidir según memoria disponible.
- Issue nuevo `low_unique_content` (warning) con umbral propio en `analysis_thresholds` (default 100 palabras únicas). El `low_word_count` existente no se toca.
- Derivada valiosa: el thin content deja de ser absoluto y pasa a ser **relativo a la plantilla**, que es como lo evalúa Google de facto.

**Por qué es game changer:** la métrica de thin content pasa de mentir sistemáticamente en e-commerce a ser fiable. Cambia decisiones de noindexado y consolidación que hoy se toman con datos malos.

## T21 — simulador what-if de enlazado

**La idea:** hoy el flujo es auditar → proponer enlaces → implementar → esperar al siguiente crawl → ver si el PageRank se movió. Con la implementación sparse de T17.1, recalcular el PageRank de un grafo de cientos de miles de nodos tarda segundos: se puede **simular antes de implementar**.

**Implementación:**
- Endpoint `POST /api/jobs/{id}/pagerank-simulate` con body `{add: [{from_hash, to_hash, position}], remove: [link_ids]}` (límite configurable de mutaciones, default 500).
- Carga el grafo del job en memoria (sparse), aplica las mutaciones, recalcula (v2 y semántico si existe) y devuelve el delta de PageRank de las N páginas más afectadas — **sin escribir nada en BD**: simulación pura, idempotente, sin efectos.
- Integración natural con T10: "simular impacto" sobre un conjunto de sugerencias aceptadas antes de pasarlas al cliente. El resultado se adjunta a la propuesta ("estos 40 enlaces suben el PageRank de las 12 fichas objetivo una media del 18 %").
- Cachear la matriz del grafo por job (invalidada al re-crawlear) para que simulaciones sucesivas sean interactivas.

**Por qué es game changer:** ninguna plataforma del mercado simula. Convierte la propuesta de enlazado interno de "confía en mí" a "aquí está el impacto calculado" — para una agencia, argumento de venta directo. Y cierra el bucle con el patrón keeper/revert: la simulación es la métrica verificable previa a la implementación.

---

## Apéndice A — resumen de flags nuevos en JobConfig

| Flag | Default | Tarea |
|---|---|---|
| `ingest_sitemaps` | `False` | T1 |
| `pagerank_version` | `1` | T3 |
| `detect_soft_404` | `False` | T6 |
| `url_normalization.strip_params` | `[]` | T8 |
| `url_normalization.strip_common_tracking` | `False` | T8 |
| `compare_to_job_id` | `None` | T5 |
| `gsc_property` | `None` | T9 |
| `chunking_strategy` (config semántica) | `"fixed"` | T11 |
| `chunk_embedding_mode` (config semántica) | `"aggregate"` | T11 |
| `trap_detection.enabled` | `False` | T13 |
| `near_duplicate_detection` | `"off"` | T14 |
| `geo_analysis` | `False` | T15 |
| `edge_classification` | `False` | T22 |
| `export?entity=` | `"urls"` | T17.7 |

Todos los defaults reproducen el comportamiento actual. Un job creado con el payload de ayer se comporta igual mañana. La segmentación (T12), la watchlist y los snapshots de robots (T16) no llevan flag: son datos nuevos a nivel de cliente que no alteran ningún job.

## Apéndice B — issue types nuevos

`in_sitemap_not_crawled`, `crawled_not_in_sitemap`, `orphan_not_in_crawl`, `meta_refresh_redirect`, `js_redirect`, `stale_lastmod`, `soft_404`, `no_inlinks_with_traffic`, `underlinked_high_performer`, `semantic_cannibalization`, `crawl_trap_detected`, `near_duplicate_content`, `content_only_after_js`, `schema_only_after_js`, `robots_txt_changed`, `watchlist_check_failed`, `slow_page`, `canonical_chain`, `canonical_loop`, `generic_anchor`, `anchor_target_mismatch`, `low_unique_content`, `equity_leak`, `link_orphan`, `excessive_click_depth`, `no_contextual_inlinks`, `authority_sink`, `deep_pagination`, `hierarchy_imbalance`.

**Los tres conceptos de huérfana** (documentarlos juntos, se confunden): `orphan_page` = 0 enlaces entrantes dentro del crawl (semántica histórica, intacta); `orphan_not_in_crawl` = conocida por sitemap/GSC pero fuera del crawl (T2); `link_orphan` = dentro del crawl pero sin camino de clics desde la home, alcanzada solo vía sitemap (T23).

Ninguno reutiliza un `issue_type` existente. `orphan_page` y `duplicate_content` mantienen su semántica actual intacta.

## Apéndice D — orden recomendado de ejecución

Las tareas están especificadas para ser independientes, pero el orden con menos fricción es:

1. **Fundamentos:** T8 (normalización con lista dorada) → T1 (sitemap) → T2 (huérfanas) → T3 (PageRank v2).
2. **Multiplicadores:** T12 (segmentación) → T7 (diff + flapping, que ya la aprovecha) → T16 (robots/watchlist/umbrales, que cuelga del diff).
3. **Calidad del crawl:** T13 (trampas) → T4 → T5 → T6 → T14 (near-duplicates en modo simhash).
4. **Capa de negocio:** T9 (GSC) → T11 (chunking semántico) → T10 (sugerencias + canibalización) → T15 (GEO, cuando haya jobs con render).
5. **T17 en paralelo:** cada punto es un PR pequeño e independiente; ideales para huecos entre tareas grandes. El 17.1 (PageRank sparse) conviene antes del primer cliente de cientos de miles de URLs; el 17.5.b (contexto DOM) conviene pronto porque cada crawl sin él es un crawl que habrá que repetir para tener `edge_class`.
6. **Arquitectura:** T22 (clasificador de aristas) → T23 (click depth, flujos y checks ARQ). Necesitan T12 y se benefician de T3; el flujo entre secciones es la vista estrella del informe de arquitectura.
7. **Game changers (cuando la capa de negocio esté viva):** T20 (contenido único; solo necesita T12+T14) → T18 (PageRank semántico + anchors; necesita T3+T17.1 y semántica, y con T22 el peso semántico se aplica sobre la clase de arista fina) → T21 (simulador; necesita T17.1, ideal junto a T18 para simular ambos scores) → T19 (cobertura consulta→pasaje; necesita T9+T11, es el más ambicioso y el de mayor valor GEO).

## Apéndice C — qué NO hacer

- No tocar la firma ni el comportamiento por defecto de `normalize_url` (T8 la extiende con default idéntico).
- No cambiar el algoritmo del PageRank v1: la v2 es una rama nueva con test de regresión sobre la v1.
- No convertir el análisis GSC/semántico en bloqueante del pipeline: si fallan, el job se completa y se registra el fallo.
- No introducir Alembic ni reestructurar `init_db.py`: seguir el patrón existente.
- No añadir dependencias pesadas sin justificarlo en el PR (el módulo de similitud de T6 es comparación de tokens, no embeddings).
