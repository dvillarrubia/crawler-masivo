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
versionado git del schema. Nota: el `clients.yaml` por el que pregunta el
brief pertenece al ecosistema WebKnograph (contrato §6, conexiones
PG-schema/Neo4j por cliente); si el POC vive aquí, su equivalente es
`client_id` + tablas de cliente. Decisión abierta (§5).

---

## 2. Estado real de Seontology

El contrato existe: `v2 experimental/contrato_seontology_neo4j_postgres.md`
(**propuesta v1.0 del proyecto WebKnograph**, 2026-06-10). Es un diseño
objetivo, no un sistema desplegado: en ESTE repo no hay driver Neo4j en
requirements, ni servicio en `docker-compose.yml` (postgres, redis, api,
crawler), ni constraint/índice alguno que auditar.

### 2.1 Invariantes del contrato (lista)

1. **Un solo espacio vectorial**: `gemini-embedding-001` a **768d** fijadas
   (MRL), normalización L2 obligatoria a esa dimensión, `task_type` parte del
   contrato (`RETRIEVAL_DOCUMENT` para lo almacenado, `RETRIEVAL_QUERY` en
   runtime, `SEMANTIC_SIMILARITY` para comparaciones simétricas como
   canibalización). Cambio de modelo/dimensión ⇒ re-embeber todo + `model_version`.
2. **Cero duplicación**: cada dato tiene un dueño; el otro motor guarda solo
   la referencia.
3. **Unión lógica, no física**: sin FKs entre motores; la clave es `page_id`
   = `sha1(normalize_url(url))[:16]`, resuelta en Python. `chunk_id =
   {page_id}:{index:04d}`; `entity_id = wikidata_qid` o `local:{slug}` con
   `is_linked=false`.
4. **Python decide umbrales; el LLM solo escribe lenguaje.**
5. **El agente no toca las bases**: solo tools de firma fija (§7 del contrato).
6. **Orden de ingesta estricto**: Postgres primero, Neo4j siempre derivado;
   upserts `MERGE` idempotentes; skip por `html_hash` (control de coste de
   embeddings) y hash por chunk; Batch API en ingesta.
7. **Nada de texto ni embeddings de chunk en el grafo** (única excepción: el
   centroide 768d por página como propiedad derivada del nodo `Page`).
8. Anti-patrones explícitos: segundo modelo/dimensión, texto en el grafo,
   joins cross-DB en runtime, escribir en Neo4j antes que en PG, borrar
   comunidades antiguas (se archivan).

### 2.2 Auditoría de implementación real

**Nada de la ontología (6 nodos, 12 relaciones) está implementado hoy**: no
existe instancia Neo4j, ni nodo `Entity`, ni relación `MENTIONS`, ni
constraints. Todo es especificación.

Lo relevante para el POC es que **el contrato ya anticipa TODO lo que este
pipeline produce, sin necesidad de extenderlo**:

- Nodo `Entity` (`entity_id`, `name`, `wikidata_qid`, `entity_type`,
  `is_linked`) y relaciones `MENTIONS` (Page→Entity: `frequency`,
  `confidence`, `source` — con **`gliner` ya contemplado como source**),
  `SAME_AS`, `SUBCLASS_OF/PART_OF`.
- Nodo `Query` + `COVERS` (Page→Query) para el cruce entidad-query.
- `funnel_stage` es propiedad del nodo `Page` en el contrato: la
  clasificación funnel del POC tiene destino declarado.

### 2.3 Conflictos contrato ↔ este repo (hay que decidirlos, §5)

| Tema | Contrato WebKnograph | Este repo (crawler-masivo v2) |
|---|---|---|
| Clave de página | `page_id` = sha1(url normalizada)[:16]; normalización fija (lowercase, sin trailing slash, sin utm, sin fragmento) | `url_hash` = sha256 completo (64), normalización CONFIGURABLE por job + `normalization_fingerprint` que gatea comparaciones |
| Espacio vectorial | 768d único en todo el ecosistema | 1024d en todas las tablas (`semantic_pages/chunks`, `query_embeddings`, centroides) — el repo ya incumple el contrato si se considera parte del ecosistema |
| `model_version` en tablas con vectores | Obligatorio (`gemini-embedding-001@768`) | No existe en ninguna tabla actual |
| GSC | `gsc_metrics` serie diaria (page_id, query, date) | Agregado por run (`gsc_job_data`, `gsc_query_data`), sin serie temporal |
| Multi-tenancy | Schema PG por cliente + contenedor Neo4j por cliente + `clients.yaml` | Un schema único, `client_id` por job, credenciales en tablas |
| Control de coste re-embedding | Skip por `html_hash` + hash por chunk | `urls.body_hash` ya existe (equivalente directo al html_hash del contrato); no hay hash por chunk |

Ambas claves de página son deterministas desde la URL ⇒ **`page_id` es
derivable desde nuestros datos** (una función de mapeo, sin tabla de mapeo,
como quiere el contrato). Pero solo si la normalización del job coincide con
la del contrato: con `strip_common_tracking=false` (nuestro default) las URLs
con utm producirían page_id distintos. La migración exige fijar una política.

### 2.4 Conclusión

La recomendación por defecto del brief queda **validada**: el POC se hace
**100 % en Postgres** (no hay grafo que poblar) y la migración futura se
documenta como anexo (al final de este informe) contra el contrato v1.0. El
contrato NO necesita extensión formal para las aristas página-entidad — ya
las define. Lo que sí conviene adoptar DESDE YA en el POC para que la
migración sea un volcado y no una reescritura: (a) convención de `entity_id`
del contrato (`wikidata_qid` | `local:{slug}` + `is_linked`), (b) columna
`model_version` en toda tabla nueva con vectores, (c) `source='gliner'` y
`confidence` en las menciones con los nombres del contrato.

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
- El brief propone 768d para el catálogo y **el contrato Seontology (§2) fija
  768d como espacio único del ecosistema WebKnograph** — lo que convierte los
  1024d de este repo en una divergencia preexistente. Las dos opciones
  honestas:
  - **(A) 768d conforme al contrato** (mi recomendación tras leerlo): las
    tablas `gliner_*`/`entity_catalog` nacen ya en el espacio del ecosistema
    destino, con `model_version='gemini-embedding-001@768'`, L2 explícita y
    task_type del contrato (`SEMANTIC_SIMILARITY` para la comparación
    simétrica span↔catálogo). Coste: parámetro `output_dimensionality`
    distinto en el backend (una línea) y NO poder comparar directamente con
    los vectores 1024d existentes (T11/T19) — que el contrato prohibiría
    igualmente como "segundo espacio".
  - **(B) 1024d reutilizando el backend tal cual**: menos fricción local y
    comparabilidad con `semantic_chunks`, pero consolida la divergencia con
    el contrato y obligaría a re-embeber el catálogo al migrar.
  En ambos casos: tablas separadas + columna `model_version`, y jamás una
  comparación entre espacios. Decisión abierta (§5).

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
3. **Espacio vectorial del catálogo**: ¿768d conforme al contrato Seontology
   (mi recomendación tras leerlo, opción A de §3.3) o 1024d como el resto de
   este repo (opción B)? Pregunta ligada: ¿el 1024d preexistente de este repo
   se considera divergencia a corregir algún día en WebKnograph, o los dos
   ecosistemas se declaran espacios separados de forma permanente?
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

---

## Anexo: migración futura a Neo4j (contra el contrato v1.0 de WebKnograph)

El POC es 100 % Postgres, pero sus tablas se diseñan para que la migración
sea un volcado idempotente (pasos 5-8 del pipeline del contrato), no una
reescritura:

| Dato del POC (Postgres) | Destino en el grafo (contrato §3) | Notas |
|---|---|---|
| `entity_catalog.entity_id` | Nodo `Entity` (`entity_id`, `name`, `entity_type`, `wikidata_qid`, `is_linked`) | El POC adopta desde el día 1 la convención `wikidata_qid` \| `local:{slug}` + `is_linked=false`; el entity linking a QIDs es un paso posterior que solo cambia el flag. |
| `gliner_page_entities` (spans agregados por URL) | Relación `MENTIONS` (Page→Entity: `frequency`, `confidence`, `source='gliner'`) | Los nombres de propiedades del contrato se usan tal cual en las columnas del POC. Los spans (texto, offsets) NO migran: texto = Postgres. |
| `gliner_query_entities` + `gsc_query_data` | Nodo `Query` + relación `COVERS` (Page→Query: `position`, `clicks_ref`) | `clicks_ref` es referencia temporal; la métrica vive en PG como exige el contrato. |
| Label funnel de `gliner_page_labels` | Propiedad `funnel_stage` del nodo `Page` | Ya prevista en el contrato; vocabulario TOFU/MOFU/BOFU a mapear con el suyo (`transaccional`… — fijar equivalencia al migrar). |
| Label tipo_pagina | Sin destino declarado en el contrato | Único punto que requeriría extensión formal (propiedad `page_type` en `Page`) — o quedarse en PG. |
| Clave de join | `page_id` = sha1(normalized)[:16] | Derivable desde nuestra URL con una función de mapeo (sin tablas de mapeo). Requiere congelar la política de normalización (§2.3): recomendación = migrar solo jobs cuyo fingerprint coincida con la normalización del contrato (lowercase, sin trailing slash, sin tracking, sin fragmento) o recalcular page_id desde la URL cruda con esa política fija. |
| Embeddings del catálogo | NO migran (anti-patrón §8.1 del contrato) | Solo el centroide por página (768d) sería propiedad del nodo `Page`, derivada de PG. |

Garantías que el POC ya respeta del contrato: PG primero y grafo derivado,
cero texto fuera de PG, umbrales en código versionado (calibrados contra gold
set, no en prompts), LLM solo para juicio lingüístico (zona gris y naming de
clusters), `model_version` en tablas con vectores.

---

---

## Decisiones tomadas (aprobación 2026-07-05: "todo en este repo, recomienda tú")

1. Repo destino: **este** (crawler-masivo v2), integrado en su consola.
2. Enganche: **batch por job_id** en contenedor propio (`Dockerfile.gliner`,
   perfil compose `gliner` — torch fuera de las imágenes api/crawler).
3. Espacio vectorial: **768d conforme al contrato** (`gemini-embedding-001@768`,
   L2, SEMANTIC_SIMILARITY, `model_version` en tabla).
4. schema.yaml: **tabla `client_extraction_schemas`** editable desde la consola
   (Configuración → Extracción de entidades) + plantillas en `config/entities/`.
5. Zona gris: **google-genai directo** (Gemini Flash, cuenta por cliente de
   `gemini_accounts`); OpenRouter queda como failover futuro documentado.
6. `gsc_query_data` **extendida** (url_id nullable + url/url_hash): las queries
   de URLs sin match ya se conservan.
7. Códigos de check: los 4 propuestos, con el mapeo determinista/firmable de §4.
   Refinamiento surgido en tests: el gap tiene precedencia sobre el mismatch
   (si NADIE cubre la entidad, la acción es crear contenido, no on-page).
8. Piloto: **workoholics** (schema leads ya cargado en su tabla).
9. Gold set: plantilla CSV generada por `--gold-out`, anotación humana, F1 con
   `--gold-eval`; gate 0,75 intacto.

Implementación de fase 1: módulo `analysis/entities/` (schema_config,
extraction, gliner_adapter, pipeline, resolve, report, gold_set, run CLI),
tablas `gliner_*` + `entity_catalog` + HNSW, endpoints del schema, checks en
consola y cola de firma. 11 tests con fakes (suite completa en verde).
Pendiente de ejecución real: build del contenedor gliner + gold set anotado +
calibración de umbrales + criterios de éxito 1-3 del brief.
