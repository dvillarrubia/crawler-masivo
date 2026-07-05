# Backlog de mejoras — tablero de trabajo

Repo de ideas y mejoras. **Tú añades líneas en "Bandeja de entrada"** y yo
las voy moviendo a "En curso" / "Hecho" a medida que las hago.

Estados: ✅ hecho · 🔨 en curso · 📋 pendiente (necesita decisión/spec) · 💡 idea

---

## Hecho

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
  pendientes reales de workoholics. Commit: (este).

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

- 📋 **B3 · Dashboards de performance interanuales.** Existe diff entre
  crawls y GSC por run, pero no hay serie temporal ni year-over-year. Los
  datos YA están (varios runs por cliente con su GSC en fechas distintas).
  **Decisión:** ¿comparar dos runs separados ~12 meses (rápido con lo que
  hay) o construir la serie completa de todos los runs (más rico)?
  Métricas: clics/impresiones/posición GSC, issues por tipo, PageRank,
  cobertura de entidades. Solo comparar runs con la misma normalización
  (fingerprint). → `spec-interanual.md`.

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

---

## Bandeja de entrada — apunta aquí lo que se te ocurra

*(añade líneas debajo; yo las recojo)*

- 
