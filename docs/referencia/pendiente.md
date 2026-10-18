# Trabajo pendiente y lo que no existe

> Movido tal cual desde `CLAUDE.md` (2026-10-14).

## PENDING WORK — read before adding features

**`docs/AUDITORIA_Y_VERIFICACION.md` is the canonical TODO.** It contains, in
order of priority:

1. **Verification checklist (sections 0–8c)**: ~30 fixes from the 2026-07
   audit branch (`claude/crawler-export-issues-oi77bm`, PR #5). **Ya está
   pasado**: el 2026-09-11 contra dos censos reales de produccion (Saunier
   Duval con `render_js` y Lopesan sin JS) en vez de crawls de prueba, y el
   ultimo punto que quedaba —8b.6, la propagacion del `nofollow` de pagina a
   sus enlaces— se cerro el 2026-10-07 con el censo de progym: 1.157 paginas
   con `noindex,nofollow` en sintaxis valida y sus 267.063 enlaces en
   `follow=false`, sin tocar los del resto del sitio. Lo unico abierto de esa
   seccion era `http_version`, que solo se rellenaba con `render_js`: desde la
   decision 109 lo da tambien curl. No hay que volver a pasarlo.
2. **Impact diagnosis for pre-fix crawls (section 10, D1–D7)**: tambien
   **ejecutado** (2026-09-11, sobre todos los jobs de mas de 50 URLs), con el
   resultado ya escrito ahi. Lo que hay que saber sin abrir el documento: los
   canonicals relativos —el bug que parecia el peor— **no contaminaron ningun
   censo** (0 en todos), asi que las indexabilidades viejas se pueden mirar sin
   sospecha. El que si mordio es el dedup de inlinks: **todo lo anterior al
   2026-09-08** (CST, Salle, penguin y los tres Lopesan de julio) tiene
   `inlinks_count == unique_inlinks_count` en todas sus paginas, con los
   enlaces infravalorados y **el PageRank calculado sobre un grafo
   incompleto**. De esos censos, titulos, metas, contenido y codigos de estado
   valen tal cual; cualquier metrica de enlazado interno o PageRank exige
   re-rastrear. Las consultas D1-D7 estan ahi para medir un job concreto.
3. **Screaming Frog parity roadmap (section 10b)**: gaps left, prioritized —
   custom extraction ya hecha en lo basico (decision 104; lo que falta, abajo),
   JavaScript raw-vs-rendered tab ⭐⭐, pagination analysis ⭐⭐,
   PageSpeed/CWV API ⭐⭐, minor ones after.
4. **Known limitations (section 11)**: also, with `render_js` an `http://` URL of an
   https site is recorded as **307** (Chromium's internal upgrade redirect), not the
   server's real 301. Treat http→https 307s in JS crawls as 301.: backup is not truly streaming (OOM risk
   on huge jobs), word_count includes hidden text, SD validation is basic.
   Una columna mas con relleno parcial, a proposito (`urls.http_version` ya
   se rellena sin render desde la decision 109, que reimplementa el handler
   de scrapy-impersonate): `resources.size_bytes` solo se rellena con la sonda
   de recursos (`crawl_behavior.comprobar_recursos`, decision 124); sin ella
   sigue NULL, y el de los recursos rastreados como URL esta en
   `urls.transfer_size`.

## What Does NOT Exist Yet

- Authentication/authorization
- Monitoring/metrics (Prometheus, Grafana)
- PageSpeed/CrUX integration
- Custom extraction / custom search: hecho lo basico (decision 104, #34). Falta:
  - **PR 2**: pestaña «Extraccion» en el formulario (tablas de reglas, marca de
    heredada, `null` al quitar una heredada) y prueba con URL en vivo desde el
    worker (cola `extraccion:pruebas`, subproceso, `shared/red.py` contra SSRF (ya existe con
    la comprobacion por la forma del host, decision 123; falta fijar la IP con
    `CURLOPT_RESOLVE` y comprobarla en cada salto), `comparar_render`), mudando
    `_BOILERPLATE_REMOVAL_JS` y `_JS_ESPERAR_DOM_QUIETO` a `render.py`;
  - **PR 3**: `fuente=html_crudo` en jobs con render (`page.on("response")`) e
    incidencia agrupada opcional `busqueda_a_medida` (una fila por regla con
    `paginas_afectadas`, `group_key = sha16(firma)`, ficha en `incidencias.js`);
  - **PR 4 (3.4)**: comparar valores extraidos entre censos en
    `shared/comparacion.py` (misma firma, sin mezclar `render`, umbral de la 68);
  - `scripts/reextraer_a_medida.py` sobre `raw_html`, con el candado del job.
- JavaScript comparison tab (raw HTML vs rendered)
- Structured data rich-result validation (only basic @type/required-prop checks)
- Any automatic call to a model: the decisions layer (decision 84) exists but nothing triggers it until #40

## Reference Documents

These markdown files are available in the project root for consultation:

| File | Description |
|------|-------------|
| **`ROADMAP.md`** | Funcionalidades hechas y en cola, en lenguaje no técnico, con enlace a la issue de cada punto. Actualizarlo al cerrar o abrir una funcionalidad. |
| **`SEO_CRAWLER_DB.md`** | Complete database schema, connection strings, example SQL queries, relationship diagram. Use this for DB access from external tools. |
| **`modelo-de-datos.md`** | Original data model design — tables, calculations, Screaming Frog tab mapping. Design spec that guided implementation. |
| **`stack-de-ingenieria.md`** | Engineering stack design doc — architecture decisions, component breakdown, scaling strategy, monitoring plan. |
| **`millones-de-URL.md`** | Guide for scaling to millions of URLs — distributed crawling patterns (scrapy-redis, Scrapy Cluster), memory management, BFS tuning. |
| **`screaming_frog_complete_fields_reference.md`** | Complete Screaming Frog fields reference (900+ lines) — all tabs, columns, filters, bulk exports. Feature parity checklist. |
