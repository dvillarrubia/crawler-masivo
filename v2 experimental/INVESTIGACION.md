# INVESTIGACION.md — Fase 0 del brief GLiNER2 (POC cruce entidad-query)

Fecha: 2026-07-05 · Rama: `v2-experimental` · Solo lectura: no se ha escrito
código de implementación. Todas las rutas, tablas y líneas citadas se han
verificado contra este repositorio y contra la base de datos de desarrollo.

**Aviso previo importante.** El brief asume un ecosistema (Neo4j por cliente,
`clients.yaml`, consola chasis-seo con 47 checks, Semantic Sentinel) que **no
existe en este repositorio**. Este repo es `crawler masivo - v2 experimental`:
FastAPI + Scrapy + PostgreSQL(pgvector) + Redis + consola React propia
(`frontend-v2/`). Donde el brief pregunta por esas piezas, esta investigación
documenta el equivalente real de aquí o su ausencia, y adapta la
recomendación. Si el POC debe integrarse en OTRO repo (chasis-seo), este
informe sigue siendo válido como mapa del lado crawler, pero hay que decirlo
explícitamente en las decisiones.

---

## 1. Punto de integración en el crawler

### 1.1 Dónde vive el contenido extraído

- Tabla **`page_content`** (modelo `PageContent`, `shared/models.py`), 1:1 con
  `urls`. Columnas: `url_id`, `content_text` (texto plano del contenido
  principal, sin menús ni plantilla), `content_markdown` (mismo contenido como
  Markdown con jerarquía de encabezados), `content_length`.
- Se extrae en el spider con `extract_main_content` /
  `extract_main_content_markdown` (`crawler/seo_crawler/extractors.py`,
  funciones puras sin Scrapy) y lo persiste el pipeline
  (`crawler/seo_crawler/pipelines.py`). Gate: `JobConfig.extraction.extract_page_content`
  (default `true`).
- **Además existen pasajes ya troceados**: tabla **`semantic_chunks`**
  (`shared/semantic_models.py`, T11): `analysis_id`, `url_id`, `position`,
  `heading_path`, `text`, `word_count`, `embedding vector(1024)`, `strategy`
  (`fixed`|`semantic`). Se escriben al correr el análisis semántico
  (`api/routers/semantic.py::_run_analysis_thread`). Para GLiNER2 esto importa:
  el chunking ~384 tokens que pide el brief puede APOYARSE en estos pasajes
  (estrategia `semantic` corta por encabezados/temas) en vez de re-trocear,
  siempre que el job haya corrido el análisis; si no, se trocea
  `content_text` al vuelo.
- El HTML bruto NO se guarda por defecto (`store_raw_html` default `false`).
  El input natural de GLiNER2 aquí es `content_text` (o los chunks).

### 1.2 Identificador canónico de URL

- **`url_hash`**: SHA-256 hex (64 chars) de la URL **normalizada**. Función:
  `compute_url_hash` en **`shared/url_normalization.py`** (única fuente de
  verdad; el spider, los extractores, la ingesta de sitemaps y la de GSC la
  usan). La normalización es configurable por job
  (`JobConfig.url_normalization`) y cada job guarda
  `jobs.normalization_fingerprint`; el diff entre crawls se bloquea (HTTP 409)
  si los fingerprints difieren. **Consecuencia para el POC**: cualquier tabla
  `gliner_*` que joinee por `url_hash` hereda esa semántica — los joins
  entre jobs solo son válidos con el mismo fingerprint. Unicidad real en DB:
  `(job_id, url_hash)`, no `url_hash` a secas; las tablas del POC deben llevar
  `job_id` además de `url_hash` (o `url_id` FK, como hacen todas las tablas
  hijas de aquí).

### 1.3 Punto de enganche recomendado

Patrones existentes en el repo:

| Patrón | Ejemplo real | Pros/contras para GLiNER2 |
|---|---|---|
| (a) Pipeline item Scrapy | `pipelines.py` (upserts + batch 200) | Descartado: el crawl corre en subproceso con presupuesto ajustado; un modelo torch en CPU dentro del spider hundiría el throughput y acopla ciclos de vida. |
| (b) Post-análisis del worker | `crawler/worker.py::_trigger_analysis` → `analysis/analyzer.py::run_analysis` | Corre en el contenedor crawler al completar el job. Añadir GLiNER2 aquí meterías torch+pesos en la imagen del crawler y alargarías el "completed". |
| (c) Hilo background lanzado por la API | análisis semántico: `POST /semantic/analyze` → `threading.Thread` en el contenedor api, progreso vía Redis, estado en tabla (`semantic_analyses.status`) | Es el patrón de la casa para enriquecimientos caros y opcionales por run. |
| (d) Batch independiente que lee Postgres | scripts sueltos (`scripts/init_db.py`) — no hay precedente de servicio batch | Hipótesis del brief. |

**Recomendación: (d) para el POC, con camino trazado a (c) para producto.**
La hipótesis del brief se valida con matices: un proceso batch independiente
(script/contenedor propio con torch+GLiNER2) que lee `page_content`/`semantic_chunks`
y `gsc_query_data` de Postgres y escribe las tablas `gliner_*` es lo correcto
para el POC porque (1) no toca ninguna imagen existente (la de la api ya pesa
por Playwright-less pero no tiene torch), (2) es re-lanzable e iterable sin
redesplegar, y (3) el gate del gold set (F1 ≥ 0,75) puede abortar el proyecto
sin haber acoplado nada. Para producto, la integración natural es (c): un
`POST /api/jobs/{id}/entities/extract` que encole el batch (el POC ya habrá
definido tablas y contratos), igual que hoy funciona el análisis semántico.
Rebato solo una parte de la hipótesis: el batch NO debe ser "a posteriori
desconectado del run" sino **por job_id**, porque todo el modelo de datos de
este repo es job-céntrico (re-crawls, diffs, comparabilidad por fingerprint).

### 1.4 Configuración por cliente hoy y encaje del `schema.yaml`

**No existe `clients.yaml`, ni schemas Postgres por tenant, ni contenedor
Neo4j por cliente.** El multi-tenant real de este repo es:

- `jobs.client_id` (string) — agrupa runs de un cliente.
- Configuración a nivel cliente **en tablas Postgres**: `segments`
  (reglas de plantilla, `api/routers/segments.py`), `watchlist_entries`
  (`api/routers/clients.py`), `client_selectors` (selectores DOM por CMS para
  el clasificador de aristas, `shared/models.py`).
- Credenciales por cliente en tablas: `gsc_accounts` (service account JSON) y
  `gemini_accounts` (API key; cada cliente paga sus embeddings).

**Encaje recomendado del `schema.yaml`**: seguir la convención de la casa =
**tabla Postgres a nivel cliente** (p. ej. `client_extraction_schemas`
(client_id, yaml_text o JSON, updated_at)) editable desde la vista
Configuración de la consola, igual que segmentos y watchlist. Alternativa
file-based (`config/gliner/<client_id>.schema.yaml`) solo si se quiere
versionado git del schema; rompe la convención "todo lo de cliente vive en DB
y se edita en la consola". Decisión abierta (§5).

---

## 2. Estado real de Seontology / Neo4j

- `contrato_seontology_neo4j_postgres.md` **no existe en este repositorio**
  (búsqueda global por nombre y por contenido "seontology"/"neo4j": cero
  resultados en código; solo menciones en HTML de mockups de diseño).
- No hay driver Neo4j en requirements, ni servicio en `docker-compose.yml`
  (postgres, redis, api, crawler — nada más), ni nodos/relaciones/constraints
  que auditar. No existe nodo entidad ni relación MENTIONS porque no existe
  grafo alguno fuera del grafo de enlaces relacional (`links`, `arch_edges`).
- **Conclusión: la recomendación por defecto del brief queda VALIDADA con la
  forma más fuerte posible — el POC es 100 % Postgres por ausencia total de la
  alternativa.** El "anexo de migración futura" debe escribirse contra el
  contrato Seontology del OTRO repo si existe allí; desde este lado, las
  garantías exportables al grafo futuro son: `url_hash` determinista como
  clave de join, cero texto fuera de Postgres, y `entity_id` estable del
  catálogo como identificador de nodo entidad.

---

## 3. Solapes con lo ya construido

### 3.1 Canibalización

- **SEM-01 (chasis-seo) no está en este repo.** El equivalente aquí:
  `analysis/link_suggester.py::emit_cannibalization_issues` emite
  `semantic_cannibalization` como **issue firmable** (warning,
  `review_status='pending'`; las decisiones firmadas sobreviven a re-runs).
  Método: coseno entre **embeddings de página completa**
  (`semantic_pages.embedding`, Gemini 1024d) ≥ umbral
  (`cannibal_threshold`, default **0,92** — exactamente el histórico que el
  brief prohíbe heredar para el POC). Además el endpoint
  `GET /semantic/cannibalization` valida pares contra queries GSC compartidas
  no-marca cuando hay `gsc_query_data`.
- Relación con el enfoque entidad primaria + banda funnel: **complementario y
  alimentador, no sustituto.** El método actual detecta "hablan de lo mismo"
  (similitud global); el del brief detecta "compiten por la misma entidad en la
  misma banda de funnel" (explicable y accionable: consolidar/diferenciar/
  desoptimizar por entidad). Propuesta: el check nuevo emite issues propios
  (`entity_cannibalization`) y en `details` referencia si el par también salta
  por el método de embeddings — dos señales convergentes suben prioridad.

### 3.2 Extracción de entidades previa

- **No hay Semantic Cluster Analyzer ni Semantic Sentinel** en este repo (cero
  referencias). Lo reutilizable como semilla de catálogo `generado`:
  - `semantic_pages.cluster_id` — clusters **HDBSCAN** reales por análisis
    (`POC_centro_semantico/src/engine.py:213-258`, sobre espacio PCA).
  - `semantic_chunks` con `heading_path` — los H1>H2>H3 son candidatos baratos
    a nombres de entidad/categoría.
  - No existe ningún catálogo resoluble ni gold set previo: hay que crearlos
    (el `00_gold_set.py` del brief es necesario, no hay atajo).

### 3.3 Embeddings existentes

- Modelo único en todo el repo: **`gemini-embedding-001` a 1024 dimensiones**
  (MRL `output_dimensionality=1024`), en
  `POC_centro_semantico/src/embedding_backends/gemini.py` (`DEFAULT_OUTPUT_DIM
  = 1024`). Columnas `vector(1024)`: `semantic_analyses.centroid`,
  `semantic_pages.embedding`, `semantic_chunks.embedding`,
  `query_embeddings.embedding`. Índice HNSW coseno ya operativo sobre
  `semantic_chunks` (migración en `shared/database.py`).
- El modelo está **deliberadamente no configurable** (docstring de
  `AnalyzeRequest` en `api/routers/semantic.py`: cambiarlo invalidaría los
  vectores almacenados). **Ninguna tabla actual lleva columna de
  modelo/dimensión** — la invariante del brief ("columna de versión de modelo
  en toda tabla con vectores") es NUEVA y solo la cumpliría `entity_catalog`.
- El brief propone 768d para el catálogo. Conviven sin peligro si jamás se
  comparan (tablas separadas + columna `embedding_model`), **pero recomiendo
  1024d reutilizando el backend existente** (`embed_queries`/`embed_documents`
  ya implementados, batching + backoff + L2-norm resueltos, misma cuenta
  Gemini por cliente vía `gemini_accounts`): menos código nuevo, un solo
  espacio vectorial en el sistema, y abre la puerta a comparar entidad↔chunk
  con los vectores T11 ya persistidos. El ahorro de 768 vs 1024 es marginal a
  escala de catálogo. Decisión abierta (§5).

### 3.4 Clasificación funnel

- **No existe TOFU/MOFU/BOFU en ninguna tabla ni cálculo ad hoc** (búsqueda
  global: cero resultados en código). Lo más cercano es `segments`
  (plantillas por regla de ruta, con flag `is_business`) — es tipo-de-página
  rule-based por URL, no funnel. El bloque `clasificacion.tipo_pagina` del
  schema.yaml SOLAPA conceptualmente con segments: hay que decidir si el label
  GLiNER2 de tipo de página es una señal más (recomendado: sí, y contrastarla
  contra el segmento asignado — discrepancia = check de calidad) o si
  sustituye a segments (no recomendado: segments alimenta flujos, diff y
  filtros de toda la consola).

### 3.5 Ingesta de GSC

- **Existe conector completo por API** (service account): cuentas en
  `gsc_accounts`, import por run en `POST /jobs/{id}/semantic/fetch-gsc`
  (`api/routers/semantic.py`), ventana configurable `days` (7–480; el
  agregado 3-6 meses del brief cabe sin tocar nada).
- Tablas: **`gsc_job_data`** (por URL: clicks, impressions, ctr, position;
  conserva filas sin match con `url_id NULL` + `url_hash` — T9/D2) y
  **`gsc_query_data`** (por query×URL: query, clicks, impressions, ctr,
  position). No hace falta BigQuery.
- **Limitación real detectada**: `gsc_query_data.url_id` es `NOT NULL` y la
  ingesta descarta las filas query×URL cuyа URL no matchea el crawl
  (`semantic.py`: solo `if url_id`). Para el análisis entidad↔query esto
  sesga: las queries que rankean hacia URLs no rastreadas desaparecen del
  nivel query. Extensión mínima propuesta (fase 1, un ALTER + 3 líneas):
  replicar el patrón T9/D2 en `gsc_query_data` (url_id nullable + url_hash).
- Ya existe además **`query_embeddings`** (T19): queries GSC embebidas en
  runtime con caché de cobertura query→pasaje. `02_extract_queries.py` debe
  COMPARTIR la agregación/filtros de `analysis/query_coverage.py`
  (agregación por query, `min_impressions`, cap por impresiones) para no
  duplicar lógica con criterios distintos.

### 3.6 Tabla de solapes (entregable)

| Área | Qué existe (real) | Qué se reutiliza | Qué se descarta para no duplicar |
|---|---|---|---|
| Contenido por URL | `page_content.content_text/markdown`; pasajes `semantic_chunks` (heading_path, 1024d, HNSW) | Input de `01_extract_pages.py`; chunks `semantic` como troceado preferente | Re-trocear cuando ya hay chunks del run |
| Identidad de URL | `compute_url_hash` + normalización por job + fingerprint | Clave de join de todas las tablas `gliner_*` (+ `job_id`) | Cualquier hash propio del POC |
| GSC | Conector API + `gsc_job_data` + `gsc_query_data` + `query_embeddings` (T19) | Ingesta tal cual; agregación de queries de `query_coverage.py` | Conector BigQuery; tabla nueva de queries |
| Embeddings | Gemini 1024d único, backend con batching, HNSW, cuenta por cliente (`gemini_accounts`) | `embed_documents`/`embed_queries` para catálogo y spans (si se aprueba 1024d) | Cliente de embeddings nuevo; OpenRouter para embeddings (no los sirve, como dice el brief) |
| Canibalización | `semantic_cannibalization` firmable (coseno 0,92 sobre páginas) + validación por queries GSC | Patrón de firma (issues + `review_status` + vista Firma); convergencia de señales en `details` | Sustituir el check existente |
| Cobertura/gaps | T19 `query_coverage` (gap de pasaje, enterrado, huérfano) + `gap-analysis` por tema | Sus agregaciones y su patrón blocked/caché | Un segundo "gap" embedding-only: el gap del POC debe ser POR ENTIDAD (explica el porqué), o no aporta sobre T19 |
| Funnel / tipo página | Nada de funnel; `segments` rule-based por cliente | `segments` como contraste del label tipo_pagina | Sustituir segments por el clasificador |
| Clustering previo | HDBSCAN en `semantic_pages.cluster_id` | Semilla del catálogo `generado` (+ naming por LLM) | Clustering nuevo para el catálogo |
| Consola/checks | Consola propia (`frontend-v2`): tabla `issues`, capas en `Issues.jsx`, catálogo en `issueCatalog.js`, cola de firma (`Firma.jsx`), endpoints `review.py` | Formato de ingesta = filas `issues` (§4); firmables nacen `pending` | Formato JSON de chasis-seo (no hay chasis-seo aquí) |
| Grafo | Nada (sin Neo4j) | — | Todo lo de nodos/relaciones hasta que exista grafo |

---

## 4. Encaje en la interfaz (la consola REAL de este repo)

No hay chasis-seo ni sus 47 checks. La consola es `frontend-v2/` (React) y su
sistema de checks es la tabla **`issues`** + estos contratos ya operativos:

- **Formato de ingesta de un check** (lo que `04_report.py` debe emitir además
  del Excel): filas en `issues` con
  `{job_id, url_id, issue_type, severity('error'|'warning'|'info'),
  details(JSON), review_status(NULL=determinista | 'pending'=firmable)}`.
  Regla dura T10 vigente: nada auto-acepta; al re-ejecutar se reemplazan solo
  los `pending` y las decisiones firmadas sobreviven (patrón exacto en
  `analysis/query_coverage.py` y `analysis/anchor_relevance.py`).
- La consola renderiza tipos nuevos SIN tocar código de vistas (fallback
  humanizado), pero lo correcto es añadir 4 entradas a
  `frontend-v2/src/issueCatalog.js` (nombre + explicación + frase de details)
  y los tipos a su capa en `Issues.jsx`. La cola de firma (`Firma.jsx`) añade
  una sección por familia de tipos firmables (patrón CoverageQueue/AnchorQueue).

### Checks propuestos

| Código (`issue_type`) | Capa (consola actual) | Tipo | Detalle |
|---|---|---|---|
| `entity_query_mismatch` | "Semántica y cobertura (se firman a mano)" | **Determinista con evidencia** (`review_status=NULL`), severidad warning | details: query, impresiones, entidad de la query, entidades presentes en la URL que rankea, confianza. Equivale al "verified" del brief: la evidencia va en details; en esta consola "verified" ≡ determinista no firmable. |
| `entity_coverage_gap` | Semántica y cobertura | Determinista con evidencia, warning | Demanda (queries agregadas por entidad, impresiones) sin ninguna URL con esa entidad primaria. Complementa a `passage_gap` (T19): este explica el QUÉ falta (entidad), T19 el DÓNDE no se responde (pasaje). |
| `entity_cannibalization` | Semántica y cobertura | **Firmable siempre** (`pending`), warning | ≥2 URLs con misma entidad primaria y misma banda funnel. details incluye si el par también salta en `semantic_cannibalization` (convergencia). Acciones del vocabulario cerrado en details.accion_sugerida. |
| `funnel_mismatch` | Semántica y cobertura (no GEO: la capa GEO de esta consola es crudo-vs-renderizado, otra cosa) | Firmable (`pending`), info | URL con label BOFU capturando queries clasificadas TOFU o viceversa. Firmable porque el remedio (reenfocar vs crear página) es juicio editorial. |

`prioridad` (impresiones × posición × confianza) viaja en `details.prioridad`
y el Excel; la consola ya ordena por severidad/recuento y la vista Firma puede
ordenar por ese campo sin cambios de backend.

---

## 5. Decisiones abiertas (necesito tu respuesta antes de fase 1)

1. **¿Repo destino?** Esta investigación asume que el POC vive AQUÍ
   (crawler-masivo v2) y se integra en SU consola. Si la intención era
   integrarlo en chasis-seo/Seontology (otro repo), dímelo y la fase 0 se
   completa con la auditoría de aquel lado.
2. **Punto de enganche**: ¿apruebas batch independiente por `job_id` (opción
   d) para el POC, con evolución a endpoint+background (opción c) en producto?
3. **Espacio vectorial del catálogo**: ¿768d como dice el brief, o 1024d
   reutilizando el backend Gemini existente (mi recomendación, §3.3)? En ambos
   casos `entity_catalog` lleva `embedding_model` + dimensión.
4. **`schema.yaml` por cliente**: ¿tabla Postgres editable desde la consola
   (convención de la casa) o fichero YAML en el repo (versionado git)?
5. **Zona gris con LLM**: ¿OpenRouter como pide el brief (dependencia nueva,
   failover fácil) o `google-genai` ya presente en la imagen (una dependencia
   menos, sin failover multi-proveedor)? Las API keys por cliente ya existen
   (`gemini_accounts`) solo para Gemini directo.
6. **Extensión de `gsc_query_data`** para conservar queries de URLs sin match
   (patrón T9/D2): ¿la incluyo en fase 1? Sin ella, el mismatch entidad-query
   solo ve queries de URLs rastreadas.
7. **Códigos de check definitivos**: ¿valen `entity_query_mismatch`,
   `entity_coverage_gap`, `entity_cannibalization`, `funnel_mismatch` y su
   mapeo determinista/firmable de §4?
8. **Cliente piloto**: el único con datos reales completos hoy es
   `workoholics` (GSC importado: 154 URLs con métricas, 573+ queries en
   striking; cuenta GSC y crawl JS reales). ¿Es el piloto? ¿Vertical leads?
9. **Gold set**: las 50 URLs + 200 queries las anota un humano (tú u otro).
   ¿Quién y con qué herramienta (Excel simple vs Label Studio)? El gate
   F1 ≥ 0,75 en resolubles queda como está salvo que digas otra cosa.

**STOP.** Conforme a la regla de oro del brief, no se escribe código de fase 1
hasta tu aprobación explícita de este informe y respuesta a las decisiones.
