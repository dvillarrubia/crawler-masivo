# Backlog de mejoras — tablero de trabajo

Repo de ideas y mejoras. **Tú añades líneas en "Bandeja de entrada"** y yo
las voy moviendo a "En curso" / "Hecho" a medida que las hago.

Estados: ✅ hecho · 🔨 en curso · 📋 pendiente (necesita decisión/spec) · 💡 idea

---

## Hecho

- ✅ **Validación de rich results (datos estructurados)** (2026-07-06)
  → `analysis/rich_results.py`: valida el JSON-LD/microdata ya extraído
  contra los requisitos de resultado enriquecido de Google por tipo de
  schema (Product, Article/News/Blog, Recipe, FAQPage, HowTo, Breadcrumb,
  Organization, LocalBusiness+subtipos, Event, JobPosting, Review, Video,
  Course, SoftwareApplication…). Campos obligatorios que faltan → `error`
  (no sale el rich result), recomendados → `warning`. `analyze_structured_data`
  ahora CALCULA y persiste `validation_status`/`validation_issues` (antes
  siempre NULL → los issues no saltaban nunca) y emite
  `structured_data_error`/`warning` con el detalle de qué campos faltan
  (frase legible en el catálogo del frontend). Conservador (presencia de
  campo, solo tipos con requisitos claros) para no dar falsos positivos.
  13 tests (suite 337). Retirado de "no existe aún" en CLAUDE.md. Commit: (este).

- ✅ **Semántica más clara: mapa, anillos y drift** (2026-07-06, bandeja)
  → Tres quejas de la bandeja resueltas. **Mapa (UMAP):** reescrito —
  color **por tema** (cluster) con centroides etiquetados, toggle a color
  por anillo, tamaño por peso **normalizado** (antes todos los puntos
  salían iguales), tooltip flotante y **clic en un punto = ficha de la
  URL**. **Anillos objetivo:** tarjeta explicativa («escribe el tema que
  QUIERES dominar; reforzar vs reenfocar») + URLs clicables. **Drift:**
  tarjeta que explica qué es (páginas potentes fuera del tema núcleo que
  diluyen la identidad) y **cómo explotarlo**, + URLs clicables. Textos de
  las pestañas reescritos. Commit: (este).

- ✅ **Incidencias → ficha de URL + "ver la web"** (2026-07-06, bandeja)
  → En Incidencias, cada URL afectada es clicable y abre la **ficha
  completa** (la misma del Explorador: resumen, on-page, contenido,
  enlaces, recursos, datos estructurados, seguridad e incidencias de esa
  URL). El drawer se extrajo a `frontend-v2/src/UrlDrawer.jsx` (compartido
  por Explorador e Incidencias, sin duplicar) y se le añadió un botón
  **"Ver la web ↗"** que abre la página real. Commit: (este).

- ✅ **Revisar/limpiar el catálogo generado con IA** (2026-07-06)
  → Un LLM revisa el catálogo (mantener/descartar/renombrar) para quitar
  el ruido del crawl; endpoints review + apply-review; UI en el catálogo.
  Commit: 68ab053.

- ✅ **Proponer las ENTRADAS del catálogo con IA** (2026-07-06)
  → La IA propone los valores concretos por tipo resoluble (marca los que
  ya existen); endpoints suggest + bulk; verificado con workoholics.
  Commit: dc2d47b.

- ✅ **Proponer el esquema de entidades con IA** (2026-07-06)
  → El "formulario" de Entidades (Config) ya no se rellena a mano: un LLM
  propone los tipos a partir del cliente. `analysis/entities/schema_suggester.py`
  reúne señal real (host, títulos + H1 de las páginas más importantes del
  rastreo, secciones de path, top queries GSC) y le pide a Gemini Flash
  (JSON) los `resolubles`/`senal`/`tipo_pagina` con descripciones en
  lenguaje natural (las que GLiNER2 usa como definición). Endpoint
  `POST /api/clients/{id}/extraction-schema/suggest` (no guarda: pre-rellena
  el formulario, el usuario revisa y guarda con el PUT de siempre). UI:
  botón "✨ Proponer con IA" + campo opcional de descripción del negocio en
  el panel de extracción. Partes puras (contexto/prompt/parseo defensivo)
  testeadas sin Gemini; 9 tests (suite 317). **Verificado en vivo con
  workoholics real:** con una pista deliberadamente errónea ("coworking")
  el modelo leyó las páginas y se autocorrigió a agencia de branding en
  Bilbao, con entidades ancladas en clientes/casos reales del sitio.
  Commit: (este).

- ✅ **Descubrir propiedades GSC/GA4 al pegar el JSON** (2026-07-05)
  → Pegas la service account y la app consulta a Google qué propiedades ve
  de cada fuente (una misma credencial suele tener GSC y GA4). Endpoint
  `POST /api/sources/discover` (router `sources.py`) intenta ambas por
  separado → `{gsc:{ok,properties,error}, ga4:{...}}`. UI: componente
  `DiscoverProperties` en las dos formas de cuenta (botón "Descubrir
  propiedades"); en GA4 rellena el `property_id` al pulsar una propiedad
  (sin teclear el ID); en GSC muestra lo accesible. Además, en el panel de
  sincronización la propiedad GSC pasa a **desplegable** cargado de la
  cuenta elegida. Degrada con gracia (credencial mala → error por fuente,
  nunca 500). 2 tests (suite 308). Verificado en vivo. Commit: e014ed9.

- ✅ **Validación de return-tags de hreflang (reciprocidad)** (2026-07-05)
  → `analyze_hreflang` ya no deja `return_tag_ok`/`lang_valid` en NULL:
  resuelve cada href a absoluto (soporta relativos) contra la URL de
  origen, normaliza y comprueba la **reciprocidad** con tres estados
  honestos — True (el destino rastreado enlaza de vuelta o es
  autorreferencia), False (rastreado pero no devuelve → issue
  `hreflang_missing_return`), None (destino no rastreado: no se inventa
  veredicto). También valida BCP-47 (`lang_valid`) y arregla el check de
  destino roto (usaba el href sin resolver, no casaba nunca). Con esto
  **Insights → i18n deja de salir "sin validar" y puntúa**. Detalles de
  issue en castellano en el catálogo del frontend. 7 tests (suite 306).
  Fleco heredado del CLAUDE.md, ya retirado de "no existe aún". Commit: a1be35f.

- ✅ **Cron de sincronización diaria de métricas (GSC/GA4)** (2026-07-05)
  → Un cron no sirve sin saber QUÉ sincronizar, así que se añade el
  registro `metric_sync_configs` (cliente·fuente·propiedad, habilitable).
  Planificador APScheduler en el arranque de la API (`api/scheduler.py`),
  diario 05:00 UTC, con **lock Redis** para no duplicar entre réplicas y
  tolerante (si falta la lib, la API arranca sin cron). Refresca una
  **ventana móvil** de los últimos días (GSC arrastra 2-3 de lag) de forma
  idempotente y guarda `last_status`/`last_synced_at` por fuente. La lógica
  de sync se extrajo a `do_sync_gsc`/`do_sync_ga4` (endpoint manual + cron
  comparten camino). UI: en Cuentas y fuentes, casilla **"Sincronizar a
  diario"** al sincronizar + tabla de **programadas** (ejecutar ahora,
  pausar/reanudar, quitar, con el último resultado). Vars
  `METRICS_SYNC_ENABLED`/`METRICS_SYNC_HOUR`. 4 tests (suite 299).
  De paso: quitado el default débil `seontology` del `NEO4J_PASSWORD` en
  docker-compose (ahora compose falla si no se define). Commit: 6169674.

- ✅ **Informe de rendimiento AGNÓSTICO a los rastreos (por rangos de
  fecha) + GA4** (2026-07-05) → El eje deja de ser el crawl y pasa a ser la
  FECHA. Serie **diaria real** de Search Console (dimensión `date`, y
  `date+page` para el detalle por URL) y de **GA4** (sesiones, usuarios,
  conversiones, ingresos por canal), ingerida a nivel de propiedad/cliente
  e independiente de cuándo se rastreó. Tablas nuevas `gsc_daily`,
  `ga4_daily`, `ga4_accounts`. Router `api/routers/metrics.py`: cuentas GA4
  (CRUD), sincronización por rango (idempotente, reemplaza el rango),
  cobertura, e **informe por rangos** (agrupa día/semana/mes y compara con
  el periodo anterior de la misma duración). Vista **Rendimiento → Por
  fechas** (presets 7/28/90 días + rango personalizado, GSC/GA4, scope
  watchlist) y panel de **sincronización de histórico** en Cuentas y
  fuentes. Sin datos → `blocked` con motivo, nunca ceros falsos. 9 tests
  (suite 295). GA4 con import perezoso: la API arranca sin las libs; solo
  la sincronización GA4 las exige. Commit: 961c368.

- ✅ **Reworkear la "Cola de firma" + el nombre nefasto** (2026-07-05)
  → Renombrada a **"Acciones propuestas"**. Zona de trabajo real: bandeja
  unificada de las 5 familias, filtros (estado, búsqueda), selección
  múltiple + acciones por lotes, y **panel de detalle** al abrir cada
  propuesta (tabla compacta para escanear, drawer para decidir). En
  enlazado interno, filtros que piensan como un SEO: **desde / hacia** y
  vista **"URLs a potenciar"** (agrupada por destino, con nº de enlaces
  entrantes y PageRank actual). Commits: 166703b, ecf9466.

- ✅ **Reorganizar Configuración con más pestañas y mejor explicada**
  (2026-07-05) → 4 pestañas (Cuentas y fuentes · Estructura del sitio ·
  Entidades · Umbrales), cada una con intro en lenguaje llano. Commit: 390e342.

- ✅ **Sacar los datos "en duda" (propuestas pendientes) para trabajarlos
  fuera** (2026-07-05) → botón **Exportar CSV** en Acciones propuestas:
  descarga las propuestas con los filtros actuales (por defecto las
  pendientes = "en duda"), una fila por propuesta con familia, tipo, URL,
  origen, prioridad, estado y el detalle completo. Verificado: 120 KB de
  pendientes reales de workoholics. Commit: 6372dde.

---

## En curso

*(vacío)*

---

## Pendiente — proyectos grandes (necesitan una decisión tuya antes de picar código)

Backend nuevo. El siguiente paso de cada uno es una spec de una página; no
se tocan a medias. Contexto ampliado en `plan-tarde.md`.

- 📋 **B1 · API para trabajar con Claude Code / cowork** (la más
  estratégica). Analizar los datos y lanzar crawls desde un agente. Hoy
  hay REST FastAPI completa pero **sin auth** (CORS abierto) y **sin capa
  de agente** (8 routers de bajo nivel). Alcance mínimo del POC: lanzar un
  crawl, consultar su estado, devolver el resumen de issues y responder
  preguntas sobre los datos de un job.
  **Decisión que necesito:** ¿(a) servidor **MCP** con verbos de negocio
  —`lanzar_crawl`, `estado_job`, `top_issues`, `preguntar_a_los_datos`—
  (mi recomendación: es literalmente "trabajar con Claude Code"), (b) REST
  + API key por cliente, o (c) MCP encima de la REST? → `spec-api-agentes.md`.

- 📋 **B2 · Ingesta de logs de servidor.** La vista Logs está honestamente
  bloqueada-por-fuente (no se finge nada). Desbloquearía: hits de bots,
  presupuesto de rastreo real, detección de crawlers de IA, time-to-index.
  **Decisión:** ¿subida manual de fichero por run (multipart .log/.gz, lo
  más rápido para desbloquear) o conector (S3/rsync)? Formatos: Apache/
  Nginx combined + JSON, parser tolerante. Cruce por url_hash. → `spec-logs.md`.

- ✅ **B3 · Dashboards de rendimiento (evolución en el tiempo)** (2026-07-05)
  → Vista **Rendimiento** (nivel proyecto): serie de todos los rastreos
  del cliente con clics/impresiones/posición GSC, URLs, incidencias y
  PageRank medio; gráfico SVG por métrica, tarjetas de comparación
  actual-vs-referencia (delta interanual) y tabla de todos los runs.
  Marca los cortes de normalización (no finge continuidad).
  **+ Seguir grupos concretos** (petición del usuario): scope por
  **segmento** (una sección: servicios, cursos, categorías) o por
  **watchlist** (URLs vigiladas), y sección de **evolución por URL
  vigilada una a una** (clics/impresiones/posición con su delta).
  Router `api/routers/performance.py`, 7 tests. Commit: 88293d9.
  **Ampliado luego** con el informe por FECHAS (agnóstico a rastreos) +
  GA4 + cron diario — ver arriba en Hecho.

---

## Pendiente — flecos y cierres (sin decisión, se hacen cuando toque)

- 📋 **Cerrar el POC de entidades (GLiNER2): validación de calidad.** El
  pipeline corre entero y genera propuestas reales, pero faltan los
  criterios de éxito del brief: **anotar el gold set** a mano
  (`informes/gold_workoholics.csv`, 50 URLs + 200 queries), calcular F1 y
  pasar el gate de 0,75; **calibrar los umbrales** de resolución por
  barrido; y validar a mano la precisión de 30 mismatches. Es trabajo
  humano + un script de evaluación (ya existe `--gold-eval`).

- 💡 **Grafo Neo4j — las 2 clases de nodo que faltan.** `Cluster` +
  `BELONGS_TO` solo se crean con el paso GDS/Leiden (`--gds`), que necesita
  activar el plugin GDS en el compose. `Competitor` + `COMPETES_WITH`
  necesitan ingesta de competencia/SERP (no existe). Ambas quedan para
  cuando haya esas fuentes.

- 💡 **Retirar el frontend Alpine legacy (`/legacy`).** Sigue servido por
  compatibilidad pero la consola nueva ya no lo referencia. Quitarlo
  cuando confirmes que no lo usa nadie.

- 💡 **Anchor propuesto en más sitios / abrir la URL en el Explorador
  desde el detalle de una propuesta** (mejoras menores de UX de la bandeja
  de Acciones propuestas).

- ✅ ~~Validación de rich results de datos estructurados~~ → hecho
  (2026-07-06, ver arriba en Hecho).

- 💡 **Features de plataforma (no de análisis), del `CLAUDE.md`:** CI/CD,
  monitoring (Prometheus/Grafana), integración PageSpeed/CrUX. Fuera del
  alcance actual; anotadas por si algún día. La **autenticación** también
  falta, pero la cubre B1 (la API para agentes la necesita sí o sí).

---

## Bandeja de entrada — apunta aquí lo que se te ocurra

*(añade líneas debajo; yo las recojo)*

~~En la pestaña de incidencias necesitamos poder pinchar en el detalle de las urls para ver todo lo relacionado con la url como si fuera la parte del explorador, y poner un enlace para ver la web estaria bien.~~ ✅ hecho (arriba)

~~Mapa semántico (UMAP) · color por anillo, tamaño por peso esto esta fatal, no esta bien traido del original el mapa semantico.~~ ✅ hecho (arriba)

~~lo de anillos objetivo no se entiende ni cascorro, no se muy bien para que sirve~~ ✅ hecho (arriba)

~~lo del drift igual no se para que sirve y como lo puedo explotar, esto hay que mejorarlo~~ ✅ hecho (arriba)

- 
