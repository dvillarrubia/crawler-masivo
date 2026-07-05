# Plan de trabajo — tarde

Sobre las 5 ideas de `ideas_a_investigar.md`, calibrado contra el estado
real del código. Regla del reparto: **lo que es UX pura (sin backend
nuevo) se ejecuta hoy; lo que necesita fuente de datos o superficie
nueva se DISEÑA hoy y se implementa cuando apruebes la spec.** Así la
tarde deja cosas terminadas y decisiones tomadas, no tres cosas a medias.

---

## Bloque A — Ejecutable esta tarde (UX, cero backend nuevo)

### A1. Renombrar y reworkear la "Cola de firma"  · ~1,5 h · impacto alto
**Estado:** `frontend-v2/src/views/Firma.jsx` — 5 colas apiladas
(sugerencias de enlace, canibalización, cobertura consulta→pasaje,
anclas, entidades). El backend (review_status, endpoints de review) ya
está; esto es solo la vista.

**Problemas reales:**
- El nombre "Cola de firma" es jerga interna: "firmar" = aprobar un check
  de juicio. Nadie de fuera sabe qué es.
- Las 5 colas van apiladas en una página larga sin jerarquía ni contador
  global de "cuánto me queda por decidir".
- No hay contexto de *por qué* cada propuesta importa ni acción por lotes.

**Qué hago:**
1. Renombrar la vista. Recomendación: **"Acciones propuestas"** (o
   "Bandeja de revisión"). Se cambia el label del sidebar (`App.jsx` NAV),
   el título y la ruta se puede dejar `#/firma` por compatibilidad.
2. Cabecera con **resumen accionable**: total pendiente + desglose por
   tipo, y el nombre del revisor arriba (ya existe).
3. Pestañas por familia en vez de 5 cards apiladas (patrón de
   Inrank/Semántica), con un badge de "N pendientes" por pestaña.
4. Por fila: botón de **aceptar/rechazar** (ya está) + una frase de "qué
   ganas si lo aplicas" (reusar las descripciones de `issueCatalog.js`).
5. Filtro "solo pendientes / todo" y orden por prioridad donde exista.

**No toca backend.** Riesgo bajo. Entregable cerrado hoy.

---

### A2. Reorganizar Configuración en pestañas + onboarding  · ~1,5 h · impacto alto
**Estado:** `Config.jsx` — 8 paneles (cuentas, segmentos, watchlist,
umbrales, schema de entidades, catálogo, estado del pipeline, fuentes)
en 2 columnas planas, sin jerarquía. Para alguien que no sabe de SEO es
un muro.

**Qué hago:**
1. **Pestañas temáticas** (patrón de tabs ya existente):
   - **Cuentas y fuentes** — Gemini/GSC del proyecto + estado de fuentes.
   - **Estructura del sitio** — segmentos (con su preview) + watchlist.
   - **Entidades** — schema (formulario) + catálogo + estado del pipeline.
   - **Umbrales** — sugeridos + los de análisis.
2. **Onboarding para novatos:** cada pestaña abre con 1-2 frases de "qué
   es esto y por qué te importa" en lenguaje llano (no "segmento" a
   secas: "trozos del sitio por plantilla — blog, producto…").
3. Orden por dependencia: primero cuentas (sin ellas nada tira), luego
   estructura, luego entidades.

**No toca backend.** Riesgo bajo. Entregable cerrado hoy.

---

## Bloque B — Diseñar esta tarde, implementar tras tu OK

Estas tres necesitan una decisión tuya antes de invertir horas. El
entregable de hoy es una mini-spec de una página cada una, no código a
medias.

### B1. API para trabajar con Claude Code / cowork  · spec ~45 min · la más estratégica
**Estado:** hay una REST FastAPI completa (8 routers: jobs, results,
diff, semantic, segments, review, clients, simulate) pero **sin
autenticación** (CORS abierto, cero auth) y **sin capa pensada para un
agente**. Un agente hoy tendría que orquestar 8 routers de bajo nivel.

**Decisiones que necesito de ti (van en la spec):**
- **Forma:** (a) **servidor MCP** — tools tipadas que Claude Code invoca
  directamente (`lanzar_crawl`, `estado_job`, `top_issues`,
  `analizar_semantica`, `preguntar_a_los_datos`)  ·  (b) REST + API key
  bien documentada  ·  (c) las dos (MCP encima de la REST).
  **Mi recomendación:** MCP, porque es literalmente "trabajar con Claude
  Code" — el agente ve verbos de negocio, no CRUD. Se puede montar como
  un servicio fino que llama a la REST interna.
- **Auth:** API key por cliente (tabla + cabecera `X-API-Key`),
  imprescindible antes de exponer nada fuera de localhost.
- **Alcance mínimo del POC:** lanzar un crawl, consultar su estado,
  devolver el resumen de issues y responder preguntas sobre los datos de
  un job (para el "análisis de los datos" que pides).

**Entregable hoy:** `spec-api-agentes.md` con la forma elegida, el
catálogo de tools/endpoints y el modelo de auth. Implementación: 1-2 días.

### B2. Ingesta de logs de servidor  · spec ~30 min
**Estado:** la vista `Logs.jsx` está honestamente bloqueada-por-fuente
(no se finge nada). No hay ni subida ni parser ni tabla.

**Decisiones para la spec:**
- **Cómo se suben:** subida de fichero en la consola (multipart, .log/.gz)
  vs. conector (S3/rsync). Para empezar: **subida manual por run**, es lo
  que desbloquea la vista con menos fontanería.
- **Formatos:** Apache/Nginx combined + JSON. Parser tolerante (una línea
  basura no tumba el lote — lección de la auditoría hostil).
- **Qué se extrae:** hits por URL, user-agent → clasificación de bots
  (Googlebot, bots de IA), presupuesto de rastreo real, time-to-index.
- **Cruce:** por `url_hash` contra las URLs del job (misma clave de join
  de todo el sistema).

**Entregable hoy:** `spec-logs.md` (formato de subida, tabla `log_hits`,
qué issues/vistas alimenta). Implementación: 1-2 días.

### B3. Dashboards de performance interanuales  · spec ~30 min
**Estado:** existe diff entre crawls (`diff.py`) y GSC por run (ventana
`days` configurable), pero **no hay serie temporal ni comparación
year-over-year**. Los datos SÍ están: varios jobs del mismo `client_id`
en fechas distintas, cada uno con su GSC.

**Decisiones para la spec:**
- **Eje temporal:** ¿comparar dos runs separados ~12 meses (simple,
  inmediato con lo que hay) o construir una serie de todos los runs del
  cliente (más rico, más trabajo)?
- **Métricas:** clics/impresiones/posición GSC agregadas, nº de issues
  por tipo, PageRank medio, cobertura de entidades — todas ya existen por
  run; el trabajo es agregarlas y pintar la evolución.
- **Aviso honesto (regla de la casa):** solo comparar runs con la misma
  normalización (fingerprint) y marcar los huecos; nada de inventar
  continuidad donde no la hay.

**Entregable hoy:** `spec-interanual.md` (qué métricas, de qué tablas,
qué comparación). Implementación: ~1 día.

---

## Orden sugerido para la tarde

1. **A1** (renombrar + rework de "Cola de firma") — arranca con la más
   visible y molesta.
2. **A2** (reorganizar Configuración) — el otro quick win de UX.
3. Con lo que quede de tarde, **B1** (spec de la API para agentes,
   la más estratégica) y, si hay tiempo, B2/B3.

Al cierre: A1 y A2 entregados y desplegados; B1-B3 como specs listas para
que decidas y arranquemos mañana. Todo en la rama `v2-experimental`, sin
merge (regla vigente).

## Lo que NO entra hoy (y por qué)
- Implementar logs / API de agentes / interanual **a la vez**: son tres
  proyectos con backend nuevo; hacerlos en paralelo en una tarde deja
  tres medias cosas rotas. Primero specs, luego uno a uno.
