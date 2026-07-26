# Auditoría del crawler y guía de verificación

Este documento registra todos los bugs encontrados durante la auditoría de la
lógica de extracción y análisis del crawler, el arreglo aplicado, y **cómo
comprobar** que cada arreglo funciona. Está pensado para seguirlo de principio
a fin cuando montes el repo en tu ordenador: es autónomo, no hace falta volver
a la conversación.

> Rama de trabajo: `claude/crawler-export-issues-oi77bm` (PR #5).
> Todos los cambios son sólo de la lógica del crawler/análisis/export/UI; **no
> tocan el esquema de la base de datos**, así que no hay migraciones.

### Índice

- **0. Puesta en marcha desde cero** (setup, lanzar crawls, tests, DB)
- **1. Export CSV / Excel**
- **2. Resolución de URLs relativas** (canonical/hreflang/og) ⭐
- **3. Paridad Screaming Frog: enlaces y timing**
- **4. Fase de análisis** (hreflang, orphan, contenido, structured data, duplicados)
- **5. Orquestación / worker** (heartbeat, delete)
- **6. Análisis semántico / GSC**
- **7. Backup / Import**
- **8. Frontend / orquestación de resultados** (estado `analyzing`, timers)
- **9. Consultas SQL de verificación (Q1–Q12)**
- **10. Diagnóstico de impacto en crawls ANTERIORES** (¿cuánto me afectó? D1–D7)
- **11. Limitaciones conocidas / trabajo futuro**
- **12. Referencia de commits**

---

## 0. Puesta en marcha desde cero (en tu ordenador)

Requisitos: Docker + Docker Compose, y Git. (Para los tests unitarios también
Python 3.11+.)

```bash
# 1) Clonar el repo y situarse en la rama de la auditoría
git clone <URL_DEL_REPO> crawler-masivo
cd crawler-masivo
git fetch origin claude/crawler-export-issues-oi77bm
git checkout claude/crawler-export-issues-oi77bm

# 2) Variables de entorno (si no existe .env, copiar el ejemplo)
cp -n .env.example .env    # revisa credenciales si quieres

# 3) Construir y levantar el stack (IMPORTANTE: --build, hay cambios en el
#    worker y en el crawler que requieren reconstruir la imagen)
docker compose up -d --build

# 4) Crear las tablas (solo la primera vez)
docker compose exec api python scripts/init_db.py

# 5) Comprobar que todo está arriba
docker compose ps
curl -s http://localhost:8000/health
```

La interfaz queda en **http://localhost:8000**.

### Lanzar los dos crawls de prueba

Lo ideal para cubrir todas las comprobaciones es un sitio **multi-idioma
(hreflang)** con **datos estructurados** (Schema.org). Lanza dos jobs: uno
**sin JS** y otro **con `render_js=true`**.

Opción A — por la interfaz web (recomendado): botón "Nuevo rastreo", pon la
URL semilla, y en el segundo activa "Renderizar JS".

Opción B — por API (sustituye la URL):
```bash
# Crawl SIN JS
curl -s -X POST http://localhost:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"name":"Prueba sin JS","seeds":["https://ejemplo.com"],
       "config":{"max_depth":3,"max_urls":500,"render_js":false}}'

# Crawl CON JS
curl -s -X POST http://localhost:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"name":"Prueba con JS","seeds":["https://ejemplo.com"],
       "config":{"max_depth":2,"max_urls":200,"render_js":true}}'
```
La respuesta incluye el `id` del job (el **`<JOB_ID>`** que usarás abajo).
También puedes listarlos: `curl -s http://localhost:8000/api/jobs`.

Espera a que el badge pase de **En curso → Analizando → Completado**.

### Conectarse a la base de datos (para las consultas SQL)

```bash
docker exec -it crawlermasivo-postgres-1 psql -U crawler -d crawler_db
```
> Si el contenedor tiene otro nombre, míralo con `docker compose ps` y usa
> ese. En todas las consultas de la sección 9 sustituye `'<JOB_ID>'` por el
> UUID real del job.

### Correr los tests unitarios (sin Docker)

```bash
pip install -r tests/requirements.txt
pytest        # desde la raíz del repo — 56 casos, deben salir todos en verde
```
Cubren las funciones puras de extracción y la validación de datos
estructurados. La capa que toca base de datos (hreflang recíproco, inlinks,
orphan, pagerank) NO es unit-testeable sin una BD: se verifica con las
consultas SQL de la sección 9.

### Cómo usar el checklist

Cada sección (1–8) tiene una tabla con: el bug, el arreglo, **cómo
comprobarlo**, y una casilla de estado. Ve ejecutando cada comprobación y
cambia `☐` por `✅` (o `❌` si algo falla y me lo comentas). Las comprobaciones
por SQL apuntan a las consultas **Q1–Q12** de la sección 9.

---

## 1. Export CSV / Excel  (commit `9d86f11`)

| # | Bug | Arreglo | Verificación en producción | Estado |
|---|-----|---------|-----------------------------|--------|
| 1.1 | Columna `content_length` **duplicada** en el CSV de URLs → campos desalineados en Excel | Segunda columna renombrada a `content_char_count` | Exporta `Exportar CSV` de un job terminado; abre en Excel y confirma que las cabeceras son únicas y las columnas cuadran | ☐ |
| 1.2 | CSV sin BOM UTF-8 → acentos corruptos en Excel | BOM `﻿` + `charset=utf-8` en los 3 export | Abre el CSV en Excel (no LibreOffice) y confirma que "categoría", "página" salen bien | ☐ |
| 1.3 | No existía export de **enlaces** | Nuevo `GET /api/jobs/{id}/links/export` + botón "Enlaces CSV" | Pulsa "Enlaces CSV"; confirma columnas `from_url,to_url,anchor_text,link_type,link_position,rel,follow,target,is_internal,alt_text` | ☐ |
| 1.4 | No existía export de **contenido** | Nuevo `GET /api/jobs/{id}/content/export` + botón "Contenido CSV" | Pulsa "Contenido CSV"; confirma que `content_text`/`content_markdown` salen completos (no truncados) | ☐ |

---

## 2. Resolución de URLs relativas  (commit `8828ec8`)  ⭐ el más impactante

| # | Bug | Arreglo | Verificación en producción | Estado |
|---|-----|---------|-----------------------------|--------|
| 2.1 | **Canonical relativo** (`href="/ruta"`) no se resolvía a absoluto → páginas auto-canónicas marcadas falsamente como "Canonicalised" / **no indexables** | `extract_meta` resuelve canonical/og:url/og:image/rel next-prev a absoluto respetando `<base href>` | Ver consulta SQL **Q1**. En un sitio con canonicals root-relative, la mayoría de páginas 200 deben quedar `indexable = true` | ☐ |
| 2.2 | `<base href>` ignorado al resolver enlaces/recursos/hreflang | `extract_links`/`extract_resources`/`extract_hreflang` reciben el base efectivo | Crawl de una página con `<base href>`; confirma que las URLs de enlaces no están mal formadas (Q6) | ☐ |
| 2.3 | Clasificación de posición de enlace invertida: un wrapper externo (`<div class="site-header">`) etiquetaba **todos** los enlaces como `header` | `_detect_link_position` gana el ancestro más cercano | Ver **Q2**: la distribución de `link_position` debe repartirse (content/nav/footer), no ser casi todo header | ☐ |
| 2.4 | Analyzer comparaba canonical con igualdad exacta de strings | Normalización con w3lib (`_norm_url`) en `analyze_canonicals` e `analyze_indexability` | Q1 + revisar que `canonical_broken` no aparece para canonicals válidos con trailing slash | ☐ |

---

## 3. Paridad Screaming Frog: enlaces y timing  (commit `bec997c`)

| # | Bug | Arreglo | Verificación en producción | Estado |
|---|-----|---------|-----------------------------|--------|
| 3.1 | `extract_links` deduplicaba por página → `inlinks_count == unique_inlinks_count` siempre, y outlinks infravalorados | Se elimina el dedup; se conservan todas las instancias. El follow del spider deduplica su propio set | Ver **Q3**: deben existir páginas con `inlinks_count > unique_inlinks_count` | ☐ |
| 3.2 | `response_time_ms = 0` en páginas renderizadas con JS (Playwright no setea `download_latency`) | El `CompositeDownloadHandler` mide el tiempo y rellena `download_latency` | Ver **Q4** en un job con `render_js=true`: `response_time_ms` > 0 en páginas HTML | ☐ |
| 3.3 | `http_version` vacío en páginas JS | El handler preserva el protocolo en meta | **Q4**: revisar `http_version` en el job JS. ⚠️ Depende de lo que exponga scrapy-playwright; si sigue NULL, no es regresión (no se fabrica valor) | ☐ |
| 3.4 | **Integridad**: `pipelines._flush` borraba hijos por `from_url_id` en cada batch de 200 → páginas con >200 enlaces perdían los insertados en batches previos | Cada `url_id` se limpia una sola vez por run | **Q5**: en una página con muchos enlaces, `COUNT(links)` debe coincidir aprox. con los `<a href>` reales del HTML | ☐ |

---

## 4. Fase de análisis  (commits `804a04f`, `2f5c03f`)

| # | Bug | Arreglo | Verificación en producción | Estado |
|---|-----|---------|-----------------------------|--------|
| 4.1 | **hreflang recíproco muerto**: `return_tag_ok`/`lang_valid` no se escribían nunca → `hreflang_missing_return` jamás se emitía y los insights de i18n daban score ~0 | `analyze_hreflang` calcula y persiste ambos flags (validación recíproca real por URL normalizada) | Ver **Q7**: `hreflang.return_tag_ok` y `lang_valid` ya no son todo NULL; el score i18n en `/insights` deja de ser 0 con hreflang correcto | ☐ |
| 4.2 | **Orphan pages con falsos positivos**: home/semillas y 404/redirect marcados huérfanos | Sólo páginas internas 200 con `crawl_depth > 0` | **Q8**: el issue `orphan_page` no debe incluir la home ni URLs 404 | ☐ |
| 4.3 | Ruido de contenido: `low_word_count`/`text_ratio` en páginas 404/no-HTML | Restringido a internas 200 | **Q9**: `low_word_count` sólo sobre páginas 200 | ☐ |
| 4.4 | **`analyze_structured_data` muerto**: `validation_status`/`validation_issues` no se escribían nunca → 0 issues siempre | Validación conservadora desde el JSON crudo (`@type` ausente = error; propiedad requerida faltante = warning). Persiste el resultado | **Q10**: en un sitio con datos estructurados, deben aparecer filas con `validation_status` poblado; los issues `structured_data_error/warning` aparecen sólo si hay errores reales | ☐ |
| 4.5 | Duplicados de title/description incluían páginas no indexables/404 | Agrupación restringida a internas 200 | **Q11**: `title_duplicate` no debe incluir páginas 404 con título de plantilla | ☐ |
| 4.6 | Rotación de User-Agent muerta (intencional con TLS impersonation) | Documentado en el docstring; NO se cambia el comportamiento (rotar UA con fingerprint TLS fijo delataría al crawler) | N/A — decisión de diseño documentada | ✅ |

---

## 5. Orquestación / worker  (commits `bec997c`, este)

| # | Bug | Arreglo | Verificación en producción | Estado |
|---|-----|---------|-----------------------------|--------|
| 5.1 | **Doble crawl en multi-réplica**: `_recover_stale_jobs` re-encolaba cualquier job `running` con `started_at` > 30 min, pero `started_at` no se actualiza durante el crawl. Crawls largos (hasta 72h) se re-encolaban mientras seguían corriendo en otra réplica → dos subprocess escribiendo el mismo job | El spider escribe un **heartbeat** en Redis (`job:{id}:heartbeat`); la recuperación sólo re-encola si el heartbeat está ausente o es viejo | Ver **Sección 6 – Prueba de heartbeat** | ☐ |
| 5.2 | `delete_job` no detenía el crawl si el job estaba corriendo → inserts con FK a un job borrado | Se envía la señal de cancelación antes de borrar | Lanza un crawl, bórralo a mitad; confirma en logs del crawler que para y que no hay errores de FK en bucle | ☐ |

---

## 6. Análisis semántico / GSC  (commit `24c7376`)

| # | Bug | Arreglo | Verificación en producción | Estado |
|---|-----|---------|-----------------------------|--------|
| 6.1 | **CTR y posición medios mal ponderados**: `avg_ctr` era la media simple de los CTR por URL (una URL con 1 impresión pesaba igual que una con 100.000) y `avg_position` una media simple. GSC agrega distinto | `avg_ctr = total_clicks / total_impressions`; `avg_position` ponderada por impresiones (como GSC) | Ver **Q12**: compara `avg_ctr` de `/semantic/results` con `SUM(clicks)/SUM(impressions)`; deben coincidir | ☐ |

---

## 7. Backup / Import  (sin cambios de código — limitación documentada)

| # | Observación | Estado |
|---|-------------|--------|
| 7.1 | Pese a llamarse `stream_backup_zip`, construye el ZIP **entero en memoria** y acumula todas las filas en listas antes de escribir; el import también lee cada `.jsonl` completo en memoria. Correcto para jobs medianos, riesgo de OOM en jobs de millones de URLs. El remapeo de FKs (url_id/job_id) en el import es correcto | ⚠️ conocido |

Verificación práctica: exporta un backup de un job terminado, bórralo, e
impórtalo con `preserve_job_id=false`; confirma que `rows_imported` cuadra con
las filas originales y que `inlinks/outlinks` y hreflang siguen resolviendo
(los hashes se preservan).

---

## 8. Frontend / orquestación de resultados  (commit `2761ee2`)

| # | Bug | Arreglo | Verificación en producción | Estado |
|---|-----|---------|-----------------------------|--------|
| 8.1 | **Job "completado" con issues vacías**: el worker marcaba el job como `completed` ANTES de ejecutar el análisis (que corre síncrono justo después). El frontend veía `completed`, cargaba insights/issues y mostraba **0 issues / datos parciales** hasta que terminaba el análisis | Nuevo estado intermedio `analyzing`: el worker pasa a `analyzing` → corre análisis → `completed`. El frontend sigue haciendo polling durante `analyzing` y refresca los datos al completar. `analyzing` queda excluido de la recuperación de stale jobs (solo mira `running`) | Lanza un crawl mediano; al terminar el rastreo, el badge debe mostrar **"Analizando"** un momento y luego "Completado" con las issues ya pobladas. En ningún momento debe verse "Completado" con 0 issues si las hay | ☐ |
| 8.2 | **Fuga de timer**: `semanticPollTimer` no se limpiaba al volver a la lista de jobs; seguía disparando cada 2s contra `this.job.id` (ya null) indefinidamente | `_stopSemanticPoll()` en `backToJobs` y `openJob` | Abre un job con análisis semántico en curso, vuelve a la lista y confirma en la pestaña Red del navegador que dejan de salir peticiones a `/semantic/status` | ☐ |

Nota: el estado `analyzing` requiere reconstruir la imagen del worker
(`docker compose up -d --build`) para que el cambio tenga efecto.

---

## 9. Consultas SQL de verificación

```sql
-- Q1. Indexabilidad: en un sitio sano la mayoría de páginas 200 internas
-- deben ser indexables. Antes del fix 2.1, casi todas salían NO indexables
-- por canonicals relativos.
SELECT indexable, COUNT(*) FROM urls
WHERE job_id = '<JOB_ID>' AND is_internal AND status_code = 200 AND is_html
GROUP BY indexable;

-- Q1b. Ninguna página auto-canónica debería estar como 'Canonicalised'.
SELECT u.url, u.indexability_status, m.canonical_href
FROM urls u JOIN html_meta m ON m.url_id = u.id
WHERE u.job_id = '<JOB_ID>' AND u.indexability_status = 'Canonicalised'
LIMIT 20;   -- revisar que el canonical realmente apunta a OTRA url

-- Q2. Distribución de posiciones de enlace (fix 2.3): debe repartirse.
SELECT link_position, COUNT(*) FROM links
WHERE job_id = '<JOB_ID>' GROUP BY link_position ORDER BY 2 DESC;

-- Q3. Inlinks por instancia (fix 3.1): deben existir páginas con
-- inlinks_count > unique_inlinks_count.
SELECT COUNT(*) AS paginas_con_mas_instancias_que_unicos
FROM urls WHERE job_id = '<JOB_ID>' AND inlinks_count > unique_inlinks_count;

-- Q4. Timing / HTTP version en job con render_js=true (fix 3.2/3.3).
SELECT
  COUNT(*) FILTER (WHERE response_time_ms > 0)  AS con_timing,
  COUNT(*) FILTER (WHERE response_time_ms = 0)  AS sin_timing,
  COUNT(*) FILTER (WHERE http_version IS NOT NULL) AS con_http_version
FROM urls WHERE job_id = '<JOB_ID>' AND is_html AND status_code = 200;

-- Q5. Enlaces de una página concreta (fix 3.4): compara con los <a href>
-- reales del HTML de esa página.
SELECT COUNT(*) FROM links l JOIN urls u ON u.id = l.from_url_id
WHERE u.job_id = '<JOB_ID>' AND u.url = '<URL_CON_MUCHOS_ENLACES>';

-- Q6. Enlaces mal formados (deberían ser 0 salvo esquemas raros).
SELECT to_url FROM links
WHERE job_id = '<JOB_ID>' AND to_url NOT LIKE 'http%' LIMIT 20;

-- Q7. hreflang recíproco (fix 4.1): ya no todo NULL.
SELECT return_tag_ok, lang_valid, COUNT(*)
FROM hreflang h JOIN urls u ON u.id = h.url_id
WHERE u.job_id = '<JOB_ID>' GROUP BY return_tag_ok, lang_valid;

-- Q8. Orphan pages (fix 4.2): la home NO debe estar.
SELECT u.url, u.crawl_depth, u.status_code
FROM issues i JOIN urls u ON u.id = i.url_id
WHERE i.job_id = '<JOB_ID>' AND i.issue_type = 'orphan_page' LIMIT 20;

-- Q9. low_word_count sólo en páginas 200 (fix 4.3).
SELECT u.status_code, COUNT(*)
FROM issues i JOIN urls u ON u.id = i.url_id
WHERE i.job_id = '<JOB_ID>' AND i.issue_type = 'low_word_count'
GROUP BY u.status_code;   -- debería ser sólo 200

-- Q10. Validación de datos estructurados (fix 4.4): status poblado.
SELECT validation_status, COUNT(*)
FROM structured_data s JOIN urls u ON u.id = s.url_id
WHERE u.job_id = '<JOB_ID>' GROUP BY validation_status;

-- Q11. Duplicados de título (fix 4.5): no deben incluir 404.
SELECT u.status_code, COUNT(*)
FROM issues i JOIN urls u ON u.id = i.url_id
WHERE i.job_id = '<JOB_ID>' AND i.issue_type = 'title_duplicate'
GROUP BY u.status_code;   -- debería ser sólo 200

-- Q12. CTR/posición GSC bien ponderados (fix 6.1). El avg_ctr que devuelve
-- /semantic/results debe coincidir con este cálculo agregado:
SELECT
  ROUND(SUM(clicks)::numeric / NULLIF(SUM(impressions), 0), 4)  AS avg_ctr_real,
  ROUND(SUM(position * impressions)::numeric
        / NULLIF(SUM(impressions) FILTER (WHERE position IS NOT NULL), 0), 1) AS avg_pos_real
FROM gsc_job_data WHERE job_id = '<JOB_ID>';
```

### Prueba de heartbeat (fix 5.1)

```bash
# Con un crawl en marcha, confirma que el heartbeat se actualiza:
docker exec -it crawlermasivo-redis-1 redis-cli GET "job:<JOB_ID>:heartbeat"
# Espera ~1 min y vuelve a consultarlo: el timestamp debe haber aumentado.
# Reinicia un worker mientras el crawl sigue: NO debe re-encolarse
# (busca en logs "Recovering stale job"; no debe aparecer para este job).
docker compose logs crawler | grep "Recovering stale job"
```

---

## 10. Diagnóstico de impacto en crawls ANTERIORES (pre-fix)

Los crawls lanzados **antes** de estos arreglos pueden llevar dato incorrecto.
El alcance depende del bug: unos corrompen el dato crudo (necesitan re-crawl),
otros sólo el análisis (basta re-analizar), otros sólo export/UI (nada).

### Clasificación por recuperación

| Cubo | Bugs | Cómo recuperar crawls viejos |
|------|------|------------------------------|
| **A — dato crudo corrupto** | 2.1 canonical, 2.3 posición enlace, 3.1 inlinks, 3.2/3.3 timing JS, 3.4 >200 enlaces | **Re-crawl limpio** (job nuevo). `Reanudar` NO vale: salta las páginas ya rastreadas y no reescribe el dato |
| **B — sólo análisis** | 4.1 hreflang, 4.2 orphan, 4.3 contenido, 4.4 structured data, 4.5 duplicados | **Re-analizar**: `Reanudar` el job (re-ejecuta el análisis sobre el dato existente) |
| **C — sólo export/UI** | 1.x export, 6.1 GSC, 8.x UI | Nada: re-exportar el CSV / recargar la página |

### Consultas para MEDIR el daño en cada job existente

Ejecuta por cada `<JOB_ID>` sospechoso. Cuanto más alto el número "malo",
más te afectó.

```sql
-- D1. Canonicals relativos guardados (causa raíz del 2.1). Si > 0, la
-- indexabilidad de ese job es sospechosa y conviene re-crawl.
SELECT
  COUNT(*) FILTER (WHERE m.canonical_href IS NOT NULL
                   AND m.canonical_href NOT LIKE 'http%') AS canonicals_relativos,
  COUNT(*) FILTER (WHERE m.canonical_href IS NOT NULL)     AS canonicals_total
FROM html_meta m JOIN urls u ON u.id = m.url_id
WHERE u.job_id = '<JOB_ID>';

-- D2. Páginas 200 internas marcadas NO indexables. Si es una fracción alta
-- del total y D1 > 0, es el bug del canonical dando falsos "no indexable".
SELECT
  COUNT(*) FILTER (WHERE indexable = false) AS no_indexables,
  COUNT(*)                                   AS total_200_internas
FROM urls
WHERE job_id = '<JOB_ID>' AND is_internal AND status_code = 200 AND is_html;

-- D3. Inlinks deduplicados (3.1). En el código viejo inlinks_count siempre
-- era == unique_inlinks_count; si con_diferencia = 0 en un sitio con enlaces
-- repetidos (nav+footer), el bug estuvo activo.
SELECT
  COUNT(*) FILTER (WHERE inlinks_count <> unique_inlinks_count) AS con_diferencia,
  COUNT(*)                                                       AS total
FROM urls WHERE job_id = '<JOB_ID>' AND is_internal;

-- D4. Sesgo de posición de enlace (2.3). Si casi todo cae en 'header' con un
-- sitio normal, el bug estuvo activo.
SELECT link_position, COUNT(*) FROM links
WHERE job_id = '<JOB_ID>' GROUP BY 1 ORDER BY 2 DESC;

-- D5. hreflang sin validar (4.1). Si null_return = total, la validación
-- recíproca nunca corrió → re-analizar.
SELECT
  COUNT(*) FILTER (WHERE return_tag_ok IS NULL) AS null_return,
  COUNT(*)                                       AS total
FROM hreflang h JOIN urls u ON u.id = h.url_id WHERE u.job_id = '<JOB_ID>';

-- D6. structured data sin validar (4.4). Si null_status = total, nunca se
-- validó → re-analizar.
SELECT
  COUNT(*) FILTER (WHERE validation_status IS NULL) AS null_status,
  COUNT(*)                                           AS total
FROM structured_data s JOIN urls u ON u.id = s.url_id WHERE u.job_id = '<JOB_ID>';

-- D7. Orphan con falsos positivos (4.2): home (depth 0) o no-200 marcados
-- huérfanos. Si > 0, esos issues eran falsas alarmas → re-analizar.
SELECT COUNT(*) FROM issues i JOIN urls u ON u.id = i.url_id
WHERE i.job_id = '<JOB_ID>' AND i.issue_type = 'orphan_page'
  AND (u.crawl_depth = 0 OR u.status_code <> 200);
```

### Recomendación práctica

1. Corre **D1/D2** en tus jobs importantes. Si hay canonicals relativos y
   muchas "no indexables", **re-crawl** esos sitios y compara.
2. Para todo lo demás (hreflang, structured data, orphan, duplicados),
   **`Reanudar`** el job re-ejecuta el análisis con los fixes — sin re-fetch.
3. Los CSV entregados a clientes con el bug de columnas/acentos: **re-exporta**
   desde el mismo job, el dato subyacente estaba bien.

---

## 11. Limitaciones conocidas / trabajo futuro

- **Backup no es streaming real** (ver 7.1): construye el ZIP en memoria;
  riesgo de OOM en jobs enormes. Candidato a reescribir con escritura por
  chunks.
- **Matching GSC↔crawl** (`_normalize_url_for_match`) pasa la ruta a
  minúsculas; en el caso raro de dos URLs que sólo difieren en mayúsculas de
  la ruta (`/A` vs `/a`) podrían colisionar. Aceptable para la mayoría de
  sitios; a vigilar si un sitio usa rutas case-sensitive.
- **Validación de datos estructurados**: es conservadora (sólo `@type` y
  propiedades mínimas de tipos comunes). No valida todas las reglas de Google
  Rich Results. Ampliable por tipo si se necesita.
- **`http_version` en JS**: depende de que scrapy-playwright exponga el
  protocolo; puede quedar NULL en algunas versiones (no es regresión).
- **Near-duplicate content** (simhash) sigue sin existir; `analyze_duplicates`
  sólo detecta contenido byte-idéntico (`body_hash`).
- **Tests de integración del analyzer** (con BD): no existen aún; la capa de
  análisis se valida con las consultas SQL de arriba. Candidato a añadir con
  SQLite/Postgres de test.
- **`word_count`** cuenta texto de elementos ocultos (`display:none`); paridad
  aproximada con Screaming Frog.

---

## 12. Referencia de commits

Todos en la rama `claude/crawler-export-issues-oi77bm` (PR #5). Para ver el
detalle de un commit: `git show <hash>`.

| Commit | Contenido | Secciones |
|--------|-----------|-----------|
| `9d86f11` | Export CSV: columna duplicada, BOM UTF-8, export de enlaces y contenido | 1 |
| `8828ec8` | Resolución de URLs relativas (canonical/hreflang/og) + posición de enlace | 2 |
| `bec997c` | Inlinks reales, timing/HTTP en JS, borrado de hijos seguro, heartbeat | 3, 5 |
| `804a04f` | hreflang recíproco, orphan sin falsos positivos, ruido de contenido | 4 |
| `2f5c03f` | Validación de structured data + duplicados/UA | 4 |
| `cb43664` | Suite de tests unitarios (56 casos) + refactors de apoyo | tests |
| `922645a` | Heartbeat anti doble-crawl, parar crawl al borrar, este documento | 5 |
| `24c7376` | CTR/posición GSC bien ponderados + doc backup/semantic | 6, 7 |
| `2761ee2` | Estado `analyzing` (issues vacías), fuga de timer semántico | 8 |

> Nota: los hashes pueden variar si la rama se rebasa. Usa
> `git log --oneline origin/master..HEAD` para ver la lista actual.
