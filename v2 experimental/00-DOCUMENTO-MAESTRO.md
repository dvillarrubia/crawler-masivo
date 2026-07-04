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
- [ ] **1.3 T2** — huérfanas reales (`orphan_not_in_crawl`), con la salvaguarda `is_html=False` y revisión de `list_urls`/backup/frontend (§1.2).
- [ ] **1.4 T3** — PageRank v2 (nofollow diluyente, decay 301, solo-indexables, `equity_leak`); incluye corrección de pesos nav/sidebar SOLO en v2 (C3).
- [ ] **1.5 T4 (corregido C1)** — parsear `html_meta.meta_refresh` existente → issue `meta_refresh_redirect`; `js_redirect` en el handler Playwright.
- [ ] **1.6 T17 quick wins** (PRs independientes, en huecos): 17.7 export issues/links · 17.3 percentiles latencia + `slow_page` · 17.2 re-análisis sin re-crawl · 17.4 cadenas de canonicals · 17.6 splitter español · 17.5 contexto DOM en links (**pronto: cada crawl sin `dom_ancestor` es un crawl a repetir para T22**).

### Fase 2 — Multiplicadores
- [ ] **2.1 T12** — motor de segmentación (tablas cliente-level, `assign_segments()`, preview, filtro `segment_id` en endpoints).
- [ ] **2.2 T7** — diff entre crawls + flapping (router `diff.py`). **La tarea de más valor del plan.**
- [ ] **2.3 T16** — robots versionado (interceptar en middleware, §1.2) + watchlist + umbrales sugeridos.

### Fase 3 — UI v1 (en paralelo con fase 2–3 de backend)
- [ ] **3.1** Decisión D1 (stack) + scaffold + design tokens fusionados (§3.1).
- [ ] **3.2** Shell: sidebar + context bar (proyecto/run/fuentes) + patrón bloqueado/vacío/degradado.
- [ ] **3.3** Vistas sobre API existente: jobs + nuevo rastreo (modal Empresarial) + progreso en vivo + overview + **explorador fusionado** (sort/chips del Empresarial + filtros por columna/prefs/teclado del actual) + ficha de URL (drawer) + técnico/on-page + re-skin semántica y cuentas.
- [ ] **3.4** Cuando T7/T12/T16 estén: Salud del proyecto + Diff/flapping + segmento global + configuración completa.
- [ ] **3.5** Frontend actual queda en `/legacy` hasta paridad; se retira al final.

### Fase 4 — Calidad del crawl
- [ ] **4.1 T13** — trampas de rastreo. · **4.2 T5** — frescura entre jobs. · **4.3 T6** — soft 404. · **4.4 T14** — near-duplicates (simhash).

### Fase 5 — Capa de negocio
- [ ] **5.1 T9 (corregido C2)** — decidir esquema GSC (extender `gsc_job_data` vs `gsc_metrics` nueva) ANTES de codificar; conservar URLs sin match; striking distance; estados `blocked` explícitos.
- [ ] **5.2 T11** — chunking semántico persistido (HNSW vía migración, §1.2).
- [ ] **5.3 T10** — sugerencias de enlazado + canibalización como issues firmables (`review_status`) → desbloquea la **Cola de firma** de la UI.
- [ ] **5.4 T15** — GEO crudo vs renderizado → desbloquea vista GEO.

### Fase 6 — Arquitectura
- [ ] **6.1 T22** — clasificador de aristas + `arch_edges` (necesita 17.5.b). · **6.2 T23** — click depth real, flujos entre secciones, checks ARQ. UI: vista de flujos segmento→segmento (la "vista estrella").

### Fase 7 — Game changers
- [ ] **7.1 T20** contenido único (T12+T14) · **7.2 T18** PageRank semántico + anchors (T3+17.1) · **7.3 T21** simulador what-if (17.1) · **7.4 T19** cobertura consulta→pasaje (T9+T11).

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
