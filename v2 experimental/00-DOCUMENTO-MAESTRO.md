# Documento maestro de implementación — crawler masivo v2

**Fecha:** 2026-07-04
**Fuentes consolidadas:**
1. `plan-mejoras-crawler-masivo.md` (T1–T23) — **verificado línea a línea contra el código real** (ver §1)
2. `modulo-arquitectura-enlazado.md` (spec ARQ, absorbida por T22/T23)
3. Handoff UI "Consola del crawler SEO" (prototipo funcional, 7 vistas)
4. Handoff UI "Crawler SEO empresarial" (prototipo funcional, 16 vistas)
5. Frontend actual (`frontend/`, Alpine.js, 5.5k líneas)

Este documento es el índice de trabajo. Cada fase se ejecuta contra él y se marca aquí.

---

## 1. Auditoría del plan de mejoras: veredicto

El plan es **fiel al código en la gran mayoría de sus afirmaciones** (verificadas una a una: el filtro `follow` del PageRank, el patrón de `init_db.py`, tenacity/batching de Gemini, `_add_issue`/`clear_existing_issues`, `_paginate`/`_stream_csv`, w3lib en `normalize_url`, ausencia de tests Python…). **Se implementa tal cual salvo las correcciones siguientes**, que son obligatorias:

### 1.1 Correcciones al plan (discrepancias reales detectadas)

| # | Tarea afectada | Corrección |
|---|---|---|
| C1 | **T4 (meta refresh)** | La extracción **ya existe**: `extract_meta_refresh` (`extractors.py:593`), columna `html_meta.meta_refresh` (`models.py:154`), expuesta en API y CSV. Lo que falta: parsear delay + URL destino **desde la columna existente** (en el analyzer, no re-extraer) y emitir el issue. Las columnas `meta_refresh_url`/`meta_refresh_delay` se derivan de `meta_refresh`. |
| C2 | **T9 (GSC)** | GSC **ya está productizado**: tablas `gsc_job_data`/`gsc_query_data` (`shared/semantic_models.py:136-171`) pobladas por `api/routers/semantic.py:332`, que ya reutiliza `POC_centro_semantico/src/gsc.py`. "Mover el POC a producción" ya está hecho. Lo que sí falta y justifica T9: esas tablas exigen `url_id NOT NULL` → **las URLs de GSC sin match en el crawl se descartan hoy**, imposibilitando `orphan_not_in_crawl` vía GSC. Decisión de diseño previa: extender las tablas existentes (url_id nullable + url_hash) **o** crear `gsc_metrics` y migrar consumidores. Evitar dos fuentes de verdad de clics por URL (el frontend semántico consume las viejas). |
| C3 | **T17.5 / T22 (link_position)** | `link_position` tiene **5 valores, no 4**: `content, nav, footer, header, sidebar` — `sidebar` NO es categoría nueva (`extractors.py:281,297`). Además `_detect_link_position` ya mira elementos semánticos como fallback; T17.5.a solo reordena prioridades. **Bug latente detectado:** `_POSITION_WEIGHT` (`analyzer.py:1084`) no cubre `nav` ni `sidebar` → caen al default 0.5, con lo que hoy un enlace de menú pesa más que header (0.3) y footer (0.2). **No corregirlo en v1** (cambiaría el PageRank de jobs comparables): corregirlo solo en la rama v2 de T3 y documentarlo. |
| C4 | **T8 / T9 (normalización)** | Existe un **segundo normalizador paralelo**: `_normalize_url_for_match` en `api/routers/semantic.py:67-84` (lowercase, strip trailing slash, quita utm/gclid/fbclid/mc_*/_ga) usado para el matching GSC↔crawl. T8 debe **unificarlo** con la `normalize_url` parametrizada o quedarán tres semánticas de normalización conviviendo. |

### 1.2 Riesgos de implementación confirmados (añadir a los criterios de cada tarea)

- **T2:** las filas `status_group='not_crawled'` afectan también a `SORT_COLUMNS`/filtros de `list_urls`, al explorador del frontend (filas casi todas NULL) y al backup/import NDJSON (`/backup`) — revisar los cuatro, no solo `get_stats` y CSV.
- **T3/T18:** `compute_pagerank` corre incondicionalmente en `run_all` (`analyzer.py:145`); la conmutación v1/v2 se lee de `job.config` en ese punto (el constructor ya lo carga).
- **T8:** `normalize_url` se llama desde ~10 sitios sin acceso a la config del job (extractors puros, pipeline, spider). Enhebrar la config vía spider/pipeline o módulo de config por proceso — más invasivo de lo que sugiere el plan; el default idéntico sigue siendo alcanzable.
- **T11:** los índices HNSW no los crea `create_all` — declararlos en el modelo o añadirlos al bloque de migraciones de `init_db.py`.
- **T16:** robots.txt se descarga (respect y audit vía `RobotsAuditMiddleware`, `middlewares.py:123`) pero **no se persiste en ningún sitio**; el snapshot requiere interceptar la respuesta en el middleware.
- **Menores:** `_representative_vector` vive en `gemini.py:195`, no en engine; `orphan_page` marca inlinks 0 **o NULL** (`analyzer.py:1201`) — la salvaguarda `is_html=False` de T2 es imprescindible, tal como el plan ya exige; `chunk_text` descarta chunks <10 palabras (tenerlo en cuenta en los tests de regresión de T11).

### 1.3 Reglas de oro (heredadas del plan, vigentes)

Compatibilidad total hacia atrás · migraciones aditivas vía patrón `init_db.py` · flags en `JobConfig` con default = comportamiento actual · issue types nuevos, nunca reciclados · `normalize_url` sagrada (T8 la extiende con default bit a bit) · tests pytest bajo `tests/python/` con cada tarea · sin Alembic · GSC/semántica nunca bloquean el pipeline.

---

## 2. Las dos UIs: qué es cada una

| | **Consola** (7 vistas) | **Empresarial** (16 vistas) | **Frontend actual** |
|---|---|---|---|
| Modelo mental | Proyecto → runs comparables → segmento como filtro global | Capas de análisis (00–10) por vista | Job → tabs → explorador |
| Fortaleza clave | Diff/flapping, cola de firma, watchlist, honestidad de datos (proxy, procedencia, bloqueado≠vacío≠error), segmentos con preview, umbrales sugeridos | Explorador con sort + chips con conteos, ficha de URL riquísima, modal "nuevo rastreo" que mapea al `JobConfig` real, progreso en vivo con ETA, overview con distribución de status y profundidad | Explorador tipo Screaming Frog **con filtros por columna**, prefs de columnas, navegación por teclado; gestión real de jobs; semántica/GSC ya cableados |
| Debilidad clave | Explorador débil (9 columnas fijas, sin sort ni filtros por columna); sin formulario de crawl; sin gestión de jobs | Sin filtros por columna ni paginación; ~60% asume backend inexistente (logs, GA4, CWV, a11y, CI/CD, MCP) | Visualmente pobre; sin diff, sin segmentos, sin firma |
| Design system | LIN3S editorial: Besley display + Inter, claro, radius suave, severidad con paleta chart | LIN3S denso: Inter + mono, radius 0, hairlines, sidebar oscura #101010, tabular-nums | CSS artesanal |

**Ambos prototipos son funcionales** (runtime React de Claude Design): sirven como spec visual y de interacción, **no** como código a copiar.

---

## 3. Fusión UI — "lo mejor de los dos mundos"

### 3.1 Decisiones de fusión

**Esqueleto y modelo mental → de la CONSOLA:**
- Jerarquía proyecto (client_id) → run (job) → segmento como **filtro global** que re-corta todas las vistas.
- Context bar: selector de proyecto + selector de run + chips de estado de fuentes (`crawl ✓ · GSC ✓ · semántica ✓ · logs ✗`).
- Patrón de tres estados: **dato real / bloqueado por fuente (con CTA y modo degradado) / vacío legítimo** ("limpio de verdad, no sin datos"). Es la solución elegante al vaporware: cada módulo sin backend se muestra como "bloqueado", no se finge.
- Etiquetado de procedencia y proxys (`clics/día *proxy GSC 28d`).
- Vistas: **Salud del proyecto** (titular editorial del diff + KPIs + alertas + watchlist), **Diff entre crawls** (con flapping), **Cola de firma**, **Configuración** (fuentes, segmentos con preview, política de parámetros, umbrales sugeridos, watchlist).

**Densidad de datos y componentes → del EMPRESARIAL:**
- **Explorador**: cabecera sticky, sort por columna, chips de filtro con conteos, coloreado semántico por celda, selector de columnas — **fusionado con los filtros por columna y prefs del explorador actual** (que es superior en eso y ya funciona contra la API real).
- **Ficha de URL** (drawer): status + facts técnicos + on-page + inlinks con anchor/rel/posición + incidencias + historial entre runs (cuando exista T7).
- **Modal "Nuevo rastreo"**: mapea casi 1:1 al `JobConfig` actual (depth, max_urls, render_js, robots_mode, include/exclude) + toggles de los flags nuevos del plan (T1 sitemaps, T6 soft-404, T13 trampas, T15 GEO…).
- **Vista de motor/progreso en vivo** (barra, cola, ETA) sobre el `job:{id}:progress` de Redis que ya existe.
- **Overview del run**: KPIs con delta vs run anterior, distribución por status, profundidad de clic, incidencias prioritarias.
- Organización del análisis en vistas: **Técnico / On-page / Enlazado·Inrank / Semántica / GEO** (las capas del Empresarial que SÍ tienen backend real o planificado).

**Design system:** tokens LIN3S compartidos por ambos. Propuesta: base del **Empresarial** (Inter + mono, radius 0, hairlines, sidebar oscura, tabular-nums — mejor para producto denso de datos) + del de la Consola los **números display Besley** en KPIs/titulares editoriales de Salud y los dots de severidad con paleta chart. Un solo tema claro.

### 3.2 Qué se PODA (no entra en v1 de la UI — se muestra "bloqueado por fuente" o no se muestra)

- **Logs de servidor / bots de IA / crawl budget por hits / time-to-index / latencia Googlebot** → vista única "Logs" en estado bloqueado con el modo degradado GSC Crawl Stats como opción futura. No hay ingesta de logs en el backend ni en el plan T1–T23.
- **GA4, backlinks (Majestic), CWV/CrUX, accesibilidad axe-core, workflow/tareas/CI-CD, conectores BI, API pública GraphQL/OQL, MCP, citas GEO por plataforma, extracción personalizada** → fuera de alcance. Ni pantalla bloqueada salvo que cueste cero.
- Vistas "Modelo de datos" y "Roadmap" del Empresarial → eran slides de pitch, no producto.
- **Neo4j**: los dos prototipos lo mencionan; el backend real calcula PageRank en Python/Postgres (y T17.1 lo hace sparse). No se introduce Neo4j.

### 3.3 Stack frontend (recomendación)

El frontend actual (Alpine.js, 3 ficheros, 5.5k líneas) no aguanta este rediseño. Recomendación: **SPA nueva con Vite + React** (los prototipos ya piensan en componentes React), build a `frontend/dist` servido por FastAPI igual que hoy, **sin tocar la API**. El frontend actual se conserva accesible en `/legacy` durante la transición. Migración por vistas: primero las que solo necesitan la API existente (jobs, explorador, ficha, progreso, overview), después las que dependen de backend nuevo.

*Alternativa si se prefiere no introducir build tooling:* seguir en Alpine.js reorganizado en módulos — viable pero el coste de los componentes densos (drawer, diff, firma) será mayor. **Decisión abierta D1 (§5).**

### 3.4 Mapa vista ↔ backend necesario

| Vista de la UI fusionada | Backend que necesita | Estado |
|---|---|---|
| Jobs / Nuevo rastreo / Progreso en vivo | API actual | ✅ existe |
| Overview del run (KPIs, status, profundidad, top issues) | `get_stats` actual (+ deltas de T7) | ✅ parcial |
| Explorador fusionado + ficha de URL | `list_urls` + filtros actuales | ✅ existe |
| Técnico / On-page (grupos de checks) | `list_issues` actual + issues nuevos | ✅ parcial |
| Salud del proyecto (titular diff, alertas, watchlist, cobertura) | **T7 + T16 + T12** | ❌ |
| Diff entre crawls + flapping | **T7** | ❌ |
| Segmento como filtro global | **T12** | ❌ |
| Enlazado · Inrank (percentiles por segmento, underlinked, sugerencias) | T3 + T12 + **T10** (+T9 para underlinked) | ❌ |
| Cola de firma | **T10** (review_status) | ❌ |
| Semántica (canibalización, gaps, drift) | ✅ ya existe (router semantic) — re-skin |
| GEO | **T15** | ❌ |
| Configuración: segmentos con preview | **T12** | ❌ |
| Configuración: política de parámetros | **T8** | ❌ |
| Configuración: umbrales sugeridos | **T16** | ❌ |
| Configuración: watchlist | **T16** | ❌ |
| Configuración: cuentas GSC/Gemini | ✅ ya existe — re-skin |

---

## 4. Roadmap por fases

Orden = apéndice D del plan, ajustado con las correcciones §1.1 e intercalando la UI para que cada bloque de backend se haga visible cuanto antes.

### Fase 0 — Cimientos (antes de tocar features)
- [x] **0.1** Infraestructura de tests: `tests/python/` con pytest, fixtures de sesión BD (SQLite para checks puros, Postgres opcional), fixture de HTML dorado. *(regla 6 del plan)* — `conftest.py` (SQLite in-memory + `BigInteger→INTEGER`, `DATABASE_URL` stub), `fixtures/golden.html`, `test_extractors_golden.py` (2026-07-04)
- [x] **0.2** **Lista dorada de normalización**: 30 URLs representativas + sus hashes actuales, como test de regresión permanente. *(pieza clave de T8; hacerla ANTES de tocar nada)* — 33 URLs en `fixtures/normalization_golden.json` + `test_normalization_golden.py` + `regenerate_normalization_golden.py`. Hechos congelados: w3lib NO quita puertos por defecto, NO quita utm/fbclid, NO normaliza trailing slash, NO resuelve dot-segments (2026-07-04)
- [x] **0.3** Test de regresión snapshot del PageRank v1 sobre grafo de juguete. *(prerrequisito de T3)* — `toygraph.py` compartido + `test_pagerank_v1_snapshot.py` (valores exactos + ranking + exclusión de externas) (2026-07-04)
- [x] **0.4** Documentar el bug latente de `_POSITION_WEIGHT` (nav/sidebar → 0.5) en el código; se corrige solo en v2 (C3). — comentario en `analyzer.py:1082` (2026-07-04)

### Fase 1 — Fundamentos backend
- [x] **1.1 T8** — normalización configurable + fingerprint + **unificación con `_normalize_url_for_match` de semantic.py (C4)**. Riesgo de enhebrado de config: ver §1.2. — `shared/url_normalization.py` (fuente única: config por dataclass, tracking list unificada, `normalize_for_match`); enhebrado vía config activa por proceso (`set_active_config` en `spider_opened`, válido porque cada crawl es un subproceso); `extractors.normalize_url` delega; `jobs.normalization_fingerprint` (modelo+migración+create_job); `UrlNormalizationConfig` en `JobConfig`; w3lib añadido a requirements de api/analysis; tests en `test_url_normalization.py` incl. regresión literal del normalizador legacy de semantic.py (2026-07-04)
- [x] **1.2 T1** — ingesta de sitemaps (flag off por defecto). — `sitemap_ingest.py` (robots.txt + /sitemap.xml, índices anidados, gzip, fetch inyectable, caps con log); tabla `sitemap_urls` + columnas `urls.in_sitemap`/`sitemap_lastmod` (+migración); flag `ingest_sitemaps` en JobConfig; hook en `spider_opened` (tras activar config T8, fallo no aborta); `analyze_sitemaps()` en analyzer (no-op sin filas; issues `in_sitemap_not_crawled` solo para filas rastreadas no-2xx — las no rastreadas son territorio de T2 — y `crawled_not_in_sitemap` solo indexables) (2026-07-04)
- [x] **1.3 T2** — huérfanas reales (`orphan_not_in_crawl`), con la salvaguarda `is_html=False` y revisión de `list_urls`/backup/frontend (§1.2). — `analyze_real_orphans()` inserta filas mínimas `not_crawled` (is_html=False, contadores NULL forzados por UPDATE — el default=0 de SQLAlchemy pisa el None del constructor), issue idempotente en re-análisis; `compute_pagerank` excluye `not_crawled` (v1 bit a bit para jobs sin esas filas — verificado por snapshot); `get_stats` excluye not_crawled de totales pero lo muestra en el desglose; backup import ahora round-tripea in_sitemap/sitemap_lastmod (+fix de `blocked_by_robots` que ya faltaba); docstring de `list_issues` aclara orphan_page vs orphan_not_in_crawl; test explícito de no-coexistencia (2026-07-04)
- [x] **1.4 T3** — PageRank v2 (nofollow diluyente, decay 301, solo-indexables, `equity_leak`); incluye corrección de pesos nav/sidebar SOLO en v2 (C3). — `analysis_thresholds.pagerank_version` (1=v1 bit a bit, dispatcher en `compute_pagerank`, versión usada registrada en `jobs.config._pagerank_version_used`); `_POSITION_WEIGHT_V2` con nav 0.2/sidebar 0.25; colapso 3xx vía `redirect_url` (10 saltos máx, decay 0.9, bucles cortados, pass-through con pagerank NULL); dedup (src,dst,follow) antes del denominador; nofollow cuenta en denominador y se destruye; `equity_leak` (warning) sobre `equity_leak_threshold` (default 0.3) con leaked/total/ratio/nofollow en details; 9 tests con los casos a mano del plan (2026-07-04)
- [x] **1.5 T4 (corregido C1)** — parsear `html_meta.meta_refresh` existente → issue `meta_refresh_redirect`; `js_redirect` en el handler Playwright. — `parse_meta_refresh()` pura en analyzer + `analyze_meta_refresh()` (deriva `meta_refresh_url`/`meta_refresh_delay` de la columna existente, destino relativo resuelto, warning ≤5s / info >5s, self-refresh no es issue) + `analyze_js_redirects()`; detección JS vía sonda `performance.getEntriesByType('navigation')` como PageMethod extra (redirectCount==0 distingue JS de HTTP en el navegador, conservador en ambigüedad); `urls.js_redirect_url` (item+pipeline+modelo+migración+backup+API) (2026-07-04)
- [x] **1.6 T17 quick wins** (PRs independientes, en huecos): 17.7 export issues/links · 17.3 percentiles latencia + `slow_page` · 17.2 re-análisis sin re-crawl · 17.4 cadenas de canonicals · 17.6 splitter español · 17.5 contexto DOM en links (**pronto: cada crawl sin `dom_ancestor` es un crawl a repetir para T22**). — 17.7: `GET /export?entity=urls|issues|links` (streaming con ventanas); 17.3: bloque `latency` (p50/p90/p99 global y por status_group, nearest-rank) en stats + issue `slow_page` (`slow_page_ms` default 3000); 17.2: `POST /jobs/{id}/reanalyze` (202, merge de thresholds persistido, BackgroundTasks; `analysis/` añadido a la imagen del API); 17.4: `analyze_canonical_chains` (5 saltos, `canonical_chain` warning / `canonical_loop` error); 17.6: abreviaturas españolas protegidas en `_split_sentences` (frases doradas); 17.5: `_detect_link_position` semántico-primero/más-cercano-gana + `links.dom_ancestor`/`dom_container` extraídos y persistidos end-to-end. NOTA: 17.1 (PageRank sparse) queda pendiente para antes de T18/T21 (2026-07-04)

### Fase 2 — Multiplicadores
- [x] **2.1 T12** — motor de segmentación (tablas cliente-level, `assign_segments()`, preview, filtro `segment_id` en endpoints). — tablas `segments` (client-level, prefix|regex sobre el path, priority asc) y `url_segments` (PK job+url, idempotente); `assign_segments()` al inicio de `run_all` (solo HTML interno, first-match-wins, regex inválida se salta con warning); router `segments.py` (CRUD + `POST /preview` contra el último job completado con conteos/muestras/unmatched); filtro `segment_id` opcional en `list_urls`, `list_issues` y `get_stats` (todos los agregados) (2026-07-04)
- [x] **2.2 T7** — diff entre crawls + flapping (router `diff.py`). **La tarea de más valor del plan.** — `GET /api/diff` (resumen new/gone + cambios por campo con umbral de delta de pagerank), `GET /api/diff/urls?change=...` (old/new paginado), `GET /api/diff/flapping?client_id=&last_n=` (A→B→A por compresión de duplicados consecutivos sobre status_group/indexable); validaciones mismo client (422), ambos completed (422), mismo `normalization_fingerprint` (409 — T8); filas `not_crawled` de T2 excluidas de ambos lados; `segment_id` (T12) re-corta el diff; on-the-fly como pide el plan v1 (2026-07-04)
- [x] **2.3 T16** — robots versionado (interceptar en middleware, §1.2) + watchlist + umbrales sugeridos. — `robots_snapshots` poblada por `robots_snapshot.py` desde `spider_opened` (siempre on, fetch propio inyectable — más robusto que interceptar los dos middlewares; fallo no aborta); diff T7 devuelve `robots_changes` con diff unificado legible por host; tabla `watchlist` + CRUD en router `clients.py` + `analyze_watchlist()` (status 200, indexable, canonical self; URL no rastreada → fila not_crawled patrón T2) con issue `watchlist_check_failed` (error) y reasons en details; `GET /api/clients/{id}/suggested-thresholds` (min_word_count=P10, slow_page_ms=P90 redondeado, max_outlinks=P95 — solo sugiere) (2026-07-04)

### Fase 3 — UI v1 (en paralelo con fase 2–3 de backend)
- [x] **3.1** Decisión D1 (stack) + scaffold + design tokens fusionados (§3.1). — D1 = Vite+React en `frontend-v2/` (hash routing propio, sin deps de router); tokens D3 en `src/theme.css` (base Empresarial + Besley display + severidad chart) (2026-07-04)
- [x] **3.2** Shell: sidebar + context bar (proyecto/run/fuentes) + patrón bloqueado/vacío/degradado. — `App.jsx` (contexto proyecto→run→segmento persistido en localStorage, chips de fuentes honestos) + `ui.jsx` (`Blocked`/`EmptyClean`) (2026-07-04)
- [x] **3.3** Vistas sobre API existente: jobs + nuevo rastreo (modal Empresarial) + progreso en vivo + overview + **explorador fusionado** + ficha de URL (drawer) + técnico/on-page + re-skin semántica y cuentas. — COMPLETADO con paridad total nativa (segunda pasada, sin depender de /legacy): Semántica completa (GSC import, análisis con estrategia de chunking, mapa SVG, canibalización, gap, drift), Cuentas GSC/Gemini, Enlaces, Insights, backup/import/resume en Jobs, explorador con prefs de columnas + filtros de servidor por columna + teclado, ficha de URL con 6 pestañas. Mejoras nuevas expuestas: Cola de firma (T10), Inrank (striking T9, delta T18, simulador T21, profundidad y flujos T23 con endpoint /section-flows nuevo), Frescura (T5), GEO en Overview (T15), is_business en segmentos. Fixes E2E: click_depth atraviesa redirects (falsos link_orphan 31→7 verificado contra el crawl real; los 7 restantes son hallazgos legítimos) y migraciones aplicadas por init_db() desde API y worker (arranque sin carrera de esquema, verificado) (2026-07-04)
- [x] **3.4** Cuando T7/T12/T16 estén: Salud del proyecto + Diff/flapping + segmento global + configuración completa. — Salud (titular editorial, robots diff, flapping, watchlist), DiffView (resumen clicable + old/new), segmento como filtro global (context bar → stats/urls/issues/diff), Config (segmentos+preview, watchlist, umbrales sugeridos, fuentes), Logs bloqueada (D4) (2026-07-04)
- [x] **3.5** Frontend actual queda en `/legacy` hasta paridad; se retira al final. — `main.py` sirve `frontend-v2/dist` en raíz si existe y `/legacy` → frontend Alpine; `api/Dockerfile` con stage node (2026-07-04)

### Fase 4 — Calidad del crawl
- [x] **4.1 T13** — trampas de rastreo. · **4.2 T5** — frescura entre jobs. · **4.3 T6** — soft 404. · **4.4 T14** — near-duplicates (simhash). — T13: `trap_detection.py` (firma de patrón con comodines numéricos/params, cap por patrón + explosión de params, gate en el follow del spider, `crawl_trap_events` persistidos en spider_closed, issue `crawl_trap_detected`); T5: `GET /jobs/{id}/freshness?compare_to=` (mismo client 422, fingerprint 409, only_changed) + `analyze_freshness` con `stale_lastmod` (dos razones) si `compare_to_job_id`; T6: probe por host en start_requests → firma en `job.config._soft404_signature`, `analyze_soft_404` con 3 señales (hash de plantilla, similitud de tokens, título de error + low words; probe 200 → solo (c)); T14: `shared/simhash.py` propio (blake2b, 64 bits, signed BIGINT), cálculo en pipeline solo con `near_duplicate_detection=simhash`, clustering por bandas 4×16 + union-find, issue `near_duplicate_content` con cluster; modo embeddings degradado con log hasta T11 (2026-07-04)

### Fase 5 — Capa de negocio
- [x] **5.1 T9 (corregido C2)** — D2 aplicada: `gsc_job_data` extendida (url_id nullable + url/url_hash con la normalización del job, índice job+hash, migración con DROP NOT NULL); fetch-gsc conserva las no-matcheadas (url_id NULL) y devuelve `unmatched`; `analyze_real_orphans` suma fuente gsc (`seen_in: ["gsc"]`, import de semantic_models con degradación si falta pgvector); `analyze_gsc_signals` (`no_inlinks_with_traffic`, `underlinked_high_performer` con P25 PR × P75 clicks); `GET /jobs/{id}/striking-distance` (posición 5–15, impresiones desc, pagerank asc, `blocked` explícito sin GSC) (2026-07-04)
- [x] **5.2 T11** — `semantic_chunk_text` en text_utils (fronteras H1-H3 con heading_path, ventanas de 3 frases, corte por percentil de distancia coseno, min/max words, offsets sobre cuerpo normalizado, modos aggregate/reembed con embed_fn inyectable — testeado sin Gemini); `GeminiBackend.embed_documents_with_chunks` (fixed = pipeline histórico bit a bit con meta gratis; semantic = ventanas + representative vector); engine con `chunking_strategy`/`chunk_embedding_mode` registrados en config; tabla `semantic_chunks` + índice HNSW en migración; persistencia en el hilo semántico (2026-07-04)
- [x] **5.3 T10** — tabla `link_suggestions` + columnas `issues.review_status/reviewed_by/reviewed_at` (NULL = determinista, nada cambia); `analysis/link_suggester.py` (núcleo puro vector-agnóstico testeable + wrapper que lee SemanticPage y anti-join contra links; pending nunca pisa decisiones); canibalización → issues `semantic_cannibalization` pending tras el análisis; API `GET /jobs/{id}/link-suggestions` (blocked sin análisis), `POST /link-suggestions/{id}/decision`, `POST /issues/{id}/review` (422 en deterministas). Cola de firma de la UI desbloqueada a nivel API (2026-07-04)
- [x] **5.4 T15** — flag `geo_analysis` (validator: exige render_js, 422); fetch crudo extra por página renderizada (prioridad baja, callback persiste `raw_word_count`/`raw_schema_types`); `analyze_geo` deriva `js_content_ratio`, marca `structured_data.visible_without_js` y emite `content_only_after_js` (error, umbral `geo_js_content_threshold` 0.5) y `schema_only_after_js` (warning); bloque `geo` en stats solo con flag (2026-07-04)

### Fase 6 — Arquitectura
- [x] **6.1 T22** — clasificador de aristas + `arch_edges` (necesita 17.5.b). · **6.2 T23** — click depth real, flujos entre secciones, checks ARQ. — `analysis/architecture.py`: clasificador de 3 pasadas (DOM con selectores por cliente en `client_selectors`; sitewide >80% de indexables → reclasificación a menu; plantilla >90% del segmento → listado) sobre `links.edge_class` (taxonomía fina, `link_position` intacta); `arch_edges` con colapso de sitewide a source='*'; `compute_click_depth` (BFS desde las semillas sobre TODAS las aristas → `urls.click_depth` ≠ crawl_depth); contadores `in_contextual`/`out_contextual`; `section_flows` (flujo = d×PR×peso/Σpesos con `EDGE_CLASS_WEIGHT`, invariante de conservación testeado); checks ARQ en `analyze_architecture`: `link_orphan` (tercer concepto de huérfana, documentados los tres juntos), `excessive_click_depth` (4 negocio / 5 resto), `no_contextual_inlinks` (solo `segments.is_business`), `authority_sink` (PR>p50 × out_contextual=0, con corrección de plantilla en details), `deep_pagination` (cadenas >3), `hierarchy_imbalance` (a nivel job con distribución completa). Flag `edge_classification` off → todo NULL y cero cambios. UI: vista de flujos segmento→segmento en Inrank (pestaña Flujos) + matriz agregada `GET /jobs/{id}/arch-edges` (paginada, filtro por clase, sitewide como origen `*`) con pestaña Aristas del grafo (2026-07-04)

### Fase 7 — Game changers
- [x] **7.1 T20** contenido único (T12+T14) — `analyze_unique_content`: shingles de 5 palabras compartidos por >30% del segmento = boilerplate; `urls.unique_word_count`/`boilerplate_ratio` + issue `low_unique_content` (umbral 100 únicas); gated por `analysis_thresholds.unique_content_analysis`; `low_word_count` intacto (2026-07-04)
- [x] **7.2 T18** PageRank semántico (NÚCLEO) — `compute_semantic_pagerank` en link_suggester (peso arista = pos × (0.3 + 0.7×cos), misma power iteration T17.1, `urls.pagerank_semantic`), post-paso del análisis semántico; `GET /jobs/{id}/pagerank-delta` ordenado por |delta| con blocked explícito (2026-07-04). CIERRE T18 (2026-07-04): relevancia de anchors en `analysis/anchor_relevance.py` — `generic_anchor` lexical (stoplist ES/EN normalizada, agregado por URL destino) y `anchor_target_mismatch` (anchor embebido como query vía `embed_queries` vs vector de página del destino, umbral 0.35 por defecto), solo anchors contextuales (edge_class si existe, si no link_position=content), issues firmables patrón T10; endpoint `POST /semantic/anchor-relevance`; anchor propuesto en sugerencias T10 (`link_suggestions.proposed_anchor` = primer H1 del destino, fallback title, determinista sin coste); UI pestaña Anclas en Semántica + cola T18 en Firma + columna en sugerencias
- [x] **7.3 T21** simulador what-if — `POST /jobs/{id}/pagerank-simulate` (add/remove hasta 500 mutaciones, grafo v2 en memoria, baseline y mutado con la misma power iteration compartida, cero escrituras — pureza verificada por test); nota: la escala 0-10 normalizada hace que la página máxima tenga delta 0 por construcción (2026-07-04). CIERRE (2026-07-04): caché de grafo por job para simulaciones interactivas — `_load_graph_cached` (TTL 600s, máx 4 jobs, thread-safe, solo memoiza LECTURAS: la pureza se mantiene), flag `fresh=true` en el body para forzar recarga tras re-análisis; verificado por test (2ª simulación sin tocar DB, TTL expira, fresh recarga)
- [x] **7.4 T19** cobertura consulta→pasaje (T9+T11) — `analysis/query_coverage.py` (núcleo puro `compute_coverage` + wrapper `run_query_coverage`): agrega `gsc_query_data` por query (top por impresiones, cap `max_queries`), embebe en runtime vía `backend.embed_queries` (RETRIEVAL_QUERY batched, cuenta Gemini del análisis) y cruza con los chunks T11; tabla `query_embeddings` cachea vector + best_similarity/covered/buried para que el GET sirva sin re-embeber. Issues firmables (patrón T10, nacen pending, decisiones sobreviven al re-run): `passage_gap` (query con demanda sin chunk ≥ umbral, sobre la URL que rankea), `buried_passage` (chunk cubridor en posición ≥5) y `orphan_chunk` (agregado por URL, chunks sin demanda). Camino exacto = matmul por bloques; >20M pares query×chunk en Postgres conmuta a top-K por query vía el índice HNSW de `semantic_chunks` (huérfanos aproximados, marcado en details). Endpoints `POST/GET /jobs/{id}/semantic/query-coverage`; UI pestaña Semántica → Consultas→Pasajes + cola propia en Firma. 7 tests (2026-07-04)

### GLiNER2 — capa de entidades (brief aparte, añadido 2026-07-05)
- [x] **Fase 0** — `INVESTIGACION.md` (integración, contrato Seontology, solapes, encaje en consola, 9 decisiones; aprobado "todo en este repo")
- [x] **Fase 1 (código)** — `analysis/entities/` completo: schema.yaml por cliente (tabla + editor en Config + plantillas ecommerce/leads), pipeline batch por job (contenedor `gliner`, torch aparte), extracción GLiNER2 (adaptador + chunking ~384 tokens + agregación con peso title/H1×3), gate de resolución 3 zonas (coseno 768d contrato / gris → Gemini Flash / señal), catálogo `generado` sembrado del crawl (entity_id = local:slug del contrato), informe determinista → 4 checks en consola (`entity_query_mismatch`, `entity_coverage_gap` deterministas; `entity_cannibalization`, `funnel_mismatch` firmables, gap con precedencia sobre mismatch) + Excel/JSON; gold set CSV + F1 + gate 0,75; `gsc_query_data` extendida (conserva sin-match). 11 tests con fakes
- [ ] **Fase 1 (ejecución real)** — PENDIENTE: run del modelo GLiNER2 sobre workoholics (imagen construida), gold set anotado a mano (CSV generado: 50 URLs + 200 queries), calibración de umbrales por barrido, criterios de éxito 1-3 (F1, precisión de mismatch sobre 30, throughput CPU)
- [x] **Lado Neo4j (contrato Seontology, añadido 2026-07-05)** — servicio `neo4j:5-community` (perfil compose `graph`, healthcheck, volumen); `shared/graph_identity.py` (page_id sha1[:16] con la política FIJA del contrato, independiente del url_hash del crawler; chunk_id/query_id); `analysis/graph/`: `contract_schema` (constraints + índice vectorial 768d idempotentes), `collect` (colectores puros PG→filas con SOLO las propiedades permitidas — ni texto, ni hashes de contenido, ni 1024d; skip de páginas sin cambios comparando body_hash EN Postgres contra el run anterior), `sync` (CLI por job: MERGE batched de Site/Page/LINKS_TO/Entity/MENTIONS/Query/COVERS, --prune con DETACH DELETE conservando Entity huérfanas, --gds opcional Leiden→Cluster versionado con BELONGS_TO archivado no borrado), `tools` (las 7 firmas del §7: canibalización PG→grafo→Python, internal_links, required_entities, architecture, metrics_decay por-run; content_gaps y graph_ingest bloqueadas honestas). p.embedding (centroide 768d) deliberadamente sin poblar hasta tener origen 768d — los 1024d del repo jamás entran al grafo (anti-patrón 4). 6 tests de identidad y colectores

### T17.1 — PageRank a escala (añadido en el cierre)
- [x] **17.1** — power iteration extraída a `run_power_iteration` compartida por v1/v2/simulador/semántico, con camino `scipy.sparse` (CSR + vector dangling) conmutado automáticamente en n>50.000 (`SPARSE_PAGERANK_THRESHOLD`, forzable); equivalencia numérica Python↔sparse verificada a 1e-6 sobre grafo aleatorio de 400 nodos; snapshot v1 intacto tras la extracción; scipy en requirements de api/analysis con fallback logueado si falta (2026-07-04)

---

## 5. Decisiones abiertas (para el propietario del proyecto)

| # | Decisión | Recomendación |
|---|---|---|
| D1 | Stack del frontend nuevo | Vite + React servido por FastAPI, `/legacy` durante transición (§3.3) |
| D2 | Esquema GSC: extender tablas existentes vs `gsc_metrics` nueva (C2) | Extender `gsc_job_data` (url_id nullable + url_hash indexado) para no duplicar fuente de verdad; decidir al iniciar 5.1 |
| D3 | Tema visual: base Empresarial (denso, radius 0) con display Besley de la Consola | Sí (§3.1) — validar con una pantalla piloto (overview) antes de extender |
| D4 | ¿Vista "Logs" bloqueada como placeholder de futuro? | Sí: coste ~0 con el patrón bloqueado-por-fuente y deja el hueco del "moat" visible |

## 6. Qué NO se hace (consolidado)

Del apéndice C del plan: no tocar `normalize_url` por defecto, no cambiar PageRank v1, no bloquear pipeline con GSC/semántica, no Alembic, no dependencias pesadas. Añadidos de esta consolidación: **no Neo4j** (los prototipos lo mencionan; el cálculo real es Python/Postgres sparse), **no ingesta de logs** (fuera de T1–T23; la UI lo refleja como fuente no conectada), **no GA4/backlinks/CWV/a11y/CI-CD/MCP** en este ciclo.
