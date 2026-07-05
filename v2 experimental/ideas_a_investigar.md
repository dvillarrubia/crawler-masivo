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

## Pendiente — necesitan una decisión tuya antes de implementar

Proyectos con backend nuevo. El siguiente paso de cada una es una spec de
una página; no se tocan a medias. Detalle en `plan-tarde.md`.

- 📋 **API para trabajar con Claude Code / cowork** (analizar datos +
  lanzar crawls). Hoy hay REST FastAPI pero SIN auth y sin capa de agente.
  **Decisión:** ¿MCP (verbos de negocio, mi recomendación), REST + API
  key, o ambas? → `spec-api-agentes.md`.

- 📋 **¿Cómo se suben los logs a la app?** Vista Logs bloqueada por
  fuente. **Decisión:** subida manual por run (rápido) vs conector.
  → `spec-logs.md`.

- 📋 **Dashboards de performance interanuales.** Los datos ya están
  (varios runs por cliente con su GSC). **Decisión:** comparar dos runs a
  ~12 meses (rápido) vs serie completa. → `spec-interanual.md`.

---

## Bandeja de entrada — apunta aquí lo que se te ocurra

*(añade líneas debajo; yo las recojo)*

- 
