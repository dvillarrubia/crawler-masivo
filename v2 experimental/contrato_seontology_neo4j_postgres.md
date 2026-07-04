# Contrato de datos — Seontology sobre Neo4j + Postgres

> **Estado:** propuesta v1.0
> **Proyecto:** WebKnograph
> **Tags:** #webknograph #seontology #neo4j #pgvector #arquitectura #contrato
> **Fecha:** 2026-06-10
> **Principio rector:** el grafo es la única fuente de verdad semántica; Postgres es la única fuente de verdad de contenido y métricas. Nada vive en los dos sitios.

---

## 1. principios no negociables

1. **Un solo espacio vectorial.** Todo embedding del ecosistema sale de `gemini-embedding-001` con `output_dimensionality = 768` fijado en configuración, no por defecto. Ningún componente introduce un segundo modelo ni una segunda dimensionalidad. Si un día se cambia cualquiera de las dos cosas, se re-embebe todo y se versiona (`model_version`).
   - **Por qué 768 y no 3072:** el índice HNSW de pgvector no soporta más de 2000 dimensiones con el tipo `vector`; a 3072 te quedas sin índice (scan secuencial) o te obliga a `halfvec`. Gemini está entrenado con Matryoshka (MRL), así que truncar a 768 pierde muy poca calidad y mantiene compatible todo el stack actual (pgvector HNSW + índice vectorial de Neo4j) sin tocar DDL.
   - **Normalización obligatoria:** a dimensionalidades distintas de 3072 el vector de Gemini no viene normalizado. Se normaliza (L2) en el paso de ingesta, antes del `UPDATE`, o la distancia coseno deja de ser fiable.
   - **`task_type` forma parte del contrato:** `RETRIEVAL_DOCUMENT` para chunks y centroides, `RETRIEVAL_QUERY` para queries de búsqueda en runtime, `SEMANTIC_SIMILARITY` para comparaciones simétricas (canibalización). Mezclar task types entre indexación y consulta degrada resultados de forma silenciosa.
2. **Cero duplicación.** Cada dato tiene un único dueño. El otro motor solo guarda la referencia (`page_id`, `chunk_id`), nunca una copia.
3. **La unión es lógica, no física.** No hay foreign keys entre motores. La clave de unión es `page_id` (hash determinista de la URL normalizada) y se resuelve en la capa Python, nunca en runtime de query.
4. **Python decide umbrales; el LLM solo escribe lenguaje.** Los thresholds de similitud, los parámetros de Leiden/HDBSCAN y los cortes de canibalización viven en código versionado, no en prompts.
5. **El bicho no toca las bases directamente.** Consume tools con firma fija (sección 7). Cambiar el motor por debajo no rompe al agente.

---

## 2. la clave de unión

```python
import hashlib

def page_id(url: str) -> str:
    """Hash determinista de URL normalizada. Idéntico en ambas bases."""
    normalized = normalize_url(url)  # lowercase, sin trailing slash, sin utm, sin fragmento
    return hashlib.sha1(normalized.encode()).hexdigest()[:16]
```

- `page_id` es la PK lógica de la página en **ambos** motores.
- `chunk_id = f"{page_id}:{chunk_index:04d}"` — derivable, ordenable, estable entre recrawls si el chunking es determinista.
- `entity_id = wikidata_qid` cuando existe; si no, `f"local:{slug}"` con flag `is_linked = false` pendiente de entity linking.

Esto elimina la necesidad de tablas de mapeo: cualquier componente puede reconstruir la referencia cruzada sin consultar nada.

---

## 3. reparto de responsabilidades

### neo4j — estructura y semántica (qué se relaciona con qué)

| capa Seontology | nodo | propiedades mínimas |
|---|---|---|
| Documento | `Page` | `page_id`, `url`, `title`, `status_code`, `depth`, `funnel_stage`, `pagerank`, `community_id`, `last_crawled` |
| Concepto | `Entity` | `entity_id`, `name`, `wikidata_qid`, `entity_type`, `is_linked` |
| Demanda | `Query` | `query_id`, `text`, `intent`, `volume`, `serp_cluster_id` |
| Agrupación | `Cluster` | `cluster_id`, `method` (leiden/hdbscan/serp), `label`, `computed_at` |
| Sitio | `Site` | `domain`, `client`, `cms` |
| Competencia | `Competitor` | `domain` |

| relación | de → a | propiedades |
|---|---|---|
| `LINKS_TO` | Page → Page | `anchor`, `is_nav`, `position` |
| `MENTIONS` | Page → Entity | `frequency`, `confidence`, `source` (gliner/manual) |
| `COVERS` | Page → Query | `position`, `clicks_ref` (solo referencia temporal, el dato vive en PG) |
| `BELONGS_TO` | Page → Cluster | `score` |
| `SAME_AS` | Entity → Entity | `confidence` (resolución de entidades) |
| `SUBCLASS_OF` / `PART_OF` | Entity → Entity | importadas de Wikidata (P279/P361/P527) |
| `COMPETES_WITH` | Page → Competitor | `query_overlap` |
| `PART_OF_SITE` | Page → Site | — |

**Lo que Neo4j NO guarda:**
- Texto completo ni chunks (solo `title` y `h1` como propiedades de navegación)
- Embeddings de chunks (masa vectorial → pgvector)
- Series temporales de métricas (GSC/GA4 → Postgres)
- HTML, JSON-LD crudo, logs de crawl

**Excepción permitida:** embedding *a nivel de página* (centroide de sus chunks, 768d) como propiedad del nodo `Page` con índice vectorial nativo de Neo4j 5. Justificación: habilita queries híbridas en un solo motor ("páginas semánticamente cercanas dentro de la misma comunidad Leiden") sin hop a Postgres. Es un dato *derivado*, no fuente: se recalcula desde pgvector, nunca al revés.

```cypher
// constraints e índices — ejecutar una vez por cliente
CREATE CONSTRAINT page_pk IF NOT EXISTS FOR (p:Page) REQUIRE p.page_id IS UNIQUE;
CREATE CONSTRAINT entity_pk IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE;
CREATE CONSTRAINT query_pk IF NOT EXISTS FOR (q:Query) REQUIRE q.query_id IS UNIQUE;
CREATE CONSTRAINT cluster_pk IF NOT EXISTS FOR (c:Cluster) REQUIRE c.cluster_id IS UNIQUE;
CREATE INDEX page_community IF NOT EXISTS FOR (p:Page) ON (p.community_id);
CREATE INDEX entity_qid IF NOT EXISTS FOR (e:Entity) ON (e.wikidata_qid);

// índice vectorial nativo para el centroide de página
CREATE VECTOR INDEX page_centroid IF NOT EXISTS
FOR (p:Page) ON (p.embedding)
OPTIONS { indexConfig: {
  `vector.dimensions`: 768,
  `vector.similarity_function`: 'cosine'
}};
```

### postgres — contenido, vectores y métricas (qué dice cada cosa y cómo rinde)

```sql
-- un schema por cliente: multi-tenancy gratis
CREATE SCHEMA IF NOT EXISTS ilerna;
SET search_path TO ilerna;

CREATE TABLE pages_raw (
    page_id      TEXT PRIMARY KEY,
    url          TEXT NOT NULL,
    html_hash    TEXT,                 -- detectar cambios entre crawls
    text_content TEXT,                 -- texto extraído completo
    json_ld      JSONB,
    crawled_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE chunks (
    chunk_id     TEXT PRIMARY KEY,     -- {page_id}:{index}
    page_id      TEXT NOT NULL REFERENCES pages_raw(page_id) ON DELETE CASCADE,
    chunk_index  INT NOT NULL,
    content      TEXT NOT NULL,
    token_count  INT,
    embedding    VECTOR(768) NOT NULL,
    model_version TEXT NOT NULL DEFAULT 'gemini-embedding-001@768',
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX chunks_page ON chunks(page_id);
CREATE INDEX chunks_hnsw ON chunks
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 200);

CREATE TABLE gsc_metrics (
    page_id     TEXT NOT NULL,
    query       TEXT NOT NULL,
    date        DATE NOT NULL,
    clicks      INT, impressions INT, position NUMERIC(5,2),
    PRIMARY KEY (page_id, query, date)
);

CREATE TABLE ga4_metrics (
    page_id     TEXT NOT NULL,
    date        DATE NOT NULL,
    channel     TEXT NOT NULL,         -- incluye referrals de IA
    sessions    INT, conversions INT, revenue NUMERIC(12,2),
    PRIMARY KEY (page_id, date, channel)
);

CREATE TABLE serp_snapshots (
    query_id    TEXT NOT NULL,
    date        DATE NOT NULL,
    position    INT NOT NULL,
    result_url  TEXT,
    domain      TEXT,
    features    JSONB,                 -- AIO, PAA, etc.
    PRIMARY KEY (query_id, date, position)
);
```

**Lo que Postgres NO guarda:** relaciones entre páginas, entidades enlazadas, comunidades, nada que implique traversal. Si una query SQL empieza a necesitar self-joins recursivos, ese dato pertenece al grafo.

---

## 4. pipeline de ingesta (orden estricto)

El orden importa: Postgres siempre primero, Neo4j siempre derivado. Así un fallo a mitad deja el grafo desactualizado pero nunca inconsistente con un contenido que no existe.

```
1. crawl            → pages_raw (PG)                  [Python, determinista]
2. chunking         → chunks sin embedding (PG)        [determinista: mismo input, mismos chunk_id]
3. embeddings       → UPDATE chunks (PG, batch)        [Gemini Batch API, task RETRIEVAL_DOCUMENT,
                                                         768d + normalización L2, reintentos con backoff]
4. centroides       → AVG(embedding) por page_id (PG)  [SQL puro]
5. NER              → entidades candidatas             [GLiNER afinado]
6. entity linking   → QIDs                             [pipeline 2 capas: gate semántico → scoring]
7. upsert grafo     → Page, Entity, MENTIONS, LINKS_TO (Neo4j, MERGE idempotente)
8. centroide a nodo → p.embedding desde paso 4
9. algoritmos       → GDS: PageRank, Leiden            [escribe pagerank, community_id, nodos Cluster]
10. métricas        → gsc_metrics, ga4_metrics (PG)    [job diario independiente, no bloquea 1-9]
```

Reglas de idempotencia:
- Todo upsert a Neo4j usa `MERGE` sobre la PK, nunca `CREATE`.
- Si `html_hash` no cambia entre crawls, los pasos 2–8 se saltan para esa página. Esto es el grueso del ahorro: en un recrawl típico el 90 % de las páginas no cambian. **Con Gemini esto pasa de ser optimización a ser control de coste:** cada embedding es una llamada de pago, así que el `html_hash` es la única barrera entre un recrawl y refacturar el sitio entero. Refuerzo adicional: hash a nivel de chunk (`sha1(content)`) para que un cambio en un párrafo no re-embeba la página completa. Usar Batch API siempre en ingesta (50 % de descuento, la latencia no importa en pipeline offline); la API síncrona queda solo para el embedding de la query del usuario en runtime.
- El paso 9 (GDS) se ejecuta solo si hubo cambios en `LINKS_TO` o en el conjunto de páginas, y se versiona: cada ejecución crea nodos `Cluster` nuevos con `computed_at`, las relaciones `BELONGS_TO` antiguas se archivan con flag, no se borran (permite comparar evolución de comunidades entre crawls).

Borrado (página desaparece del crawl o da 410):
```
1. Neo4j: DETACH DELETE del nodo Page (las Entity huérfanas se conservan)
2. PG: DELETE en pages_raw → CASCADE borra chunks
3. gsc_metrics / ga4_metrics se conservan (histórico)
```

---

## 5. patrones de lectura híbrida

Patrón único para todo lo semántico-estructural: **candidatos en pgvector, contexto en Neo4j**. Nunca al revés, y nunca cross-query en runtime.

```python
def canibalizacion(page_id: str, threshold: float = 0.85) -> list[dict]:
    # 1. PG: pares de chunks similares entre páginas distintas
    candidates = pg.query("""
        SELECT c2.page_id, MAX(1 - (c1.embedding <=> c2.embedding)) AS sim
        FROM chunks c1
        JOIN chunks c2 ON c1.page_id != c2.page_id
        WHERE c1.page_id = %s
          AND 1 - (c1.embedding <=> c2.embedding) > %s
        GROUP BY c2.page_id
    """, (page_id, threshold))

    # 2. Neo4j: ¿comparten comunidad? ¿compiten por las mismas queries?
    enriched = neo4j.query("""
        MATCH (a:Page {page_id: $pid}), (b:Page)
        WHERE b.page_id IN $candidates
        OPTIONAL MATCH (a)-[:COVERS]->(q:Query)<-[:COVERS]-(b)
        RETURN b.page_id, b.community_id = a.community_id AS misma_comunidad,
               count(q) AS queries_compartidas
    """, pid=page_id, candidates=[c["page_id"] for c in candidates])

    # 3. Python: merge, scoring y decisión
    return merge_and_score(candidates, enriched)
```

La query inversa (estructura → semántica) usa el centroide nativo de Neo4j y no toca PG:

```cypher
// enlaces internos sugeridos: cercanas semánticamente, misma comunidad, hacia transaccional
MATCH (origen:Page {page_id: $pid})
CALL db.index.vector.queryNodes('page_centroid', 20, origen.embedding)
YIELD node AS candidata, score
WHERE candidata.community_id = origen.community_id
  AND candidata.funnel_stage = 'transaccional'
  AND NOT (origen)-[:LINKS_TO]->(candidata)
RETURN candidata.url, score, candidata.pagerank
ORDER BY score * candidata.pagerank DESC LIMIT 10
```

---

## 6. multi-tenancy

| motor | mecanismo | coste |
|---|---|---|
| Postgres | un schema por cliente, mismo servidor | cero; `SET search_path` |
| Neo4j Community | un contenedor por cliente en el VPS | RAM por instancia (~1–2 GB/cliente con heap ajustado) |

Regla: la configuración de conexión por cliente vive en un único `clients.yaml` que ambas capas leen. Ningún script hardcodea host/schema.

```yaml
ilerna:
  pg_schema: ilerna
  neo4j_uri: bolt://localhost:7688
  neo4j_database: neo4j
equiron:
  pg_schema: equiron
  neo4j_uri: bolt://localhost:7689
  neo4j_database: neo4j
```

---

## 7. contrato de tools (la frontera del bicho)

El agente solo ve estas firmas. Internamente cada tool decide a qué motor(es) va. Cypher parametrizado fijo en producción; Text2Cypher solo para exploración interactiva.

| tool | motor(es) | devuelve |
|---|---|---|
| `graph_canibalizacion(page_id)` | PG → Neo4j | pares con sim, comunidad, queries compartidas |
| `graph_internal_links(page_id, funnel_stage)` | Neo4j | sugerencias rankeadas |
| `graph_required_entities(query_id)` | Neo4j | entidades Seontology que la pieza debe cubrir |
| `graph_content_gaps(cluster_id)` | Neo4j | gaps vs competencia |
| `graph_architecture(max_depth)` | Neo4j | páginas huérfanas, profundidad excesiva |
| `metrics_decay(page_id, window)` | PG | series GSC/GA4, deltas |
| `graph_ingest(url, content)` | PG → Neo4j | cierra el loop de reingesta (pipeline sección 4) |

Regla de evolución: añadir tools es libre; cambiar la firma o el shape de retorno de una existente exige versionar (`graph_canibalizacion_v2`) hasta migrar el `program.md` del agente.

---

## 8. anti-patrones (prohibido)

1. **Duplicar embeddings de chunks en Neo4j.** Solo el centroide de página, y siempre derivado de PG.
2. **Guardar texto en el grafo.** Si una query Cypher necesita el contenido, es una query de dos pasos (Neo4j → ids → PG), resuelta en Python.
3. **Joins cross-DB en runtime del agente.** El merge siempre ocurre dentro de la tool, nunca lo orquesta el LLM.
4. **Segundo modelo de embeddings o segunda dimensionalidad** para "casos especiales". Fragmenta el espacio vectorial y rompe la comparabilidad canibalización ↔ retrieval. Con Gemini esto incluye cambiar `output_dimensionality` o el `task_type` de indexación entre pipelines: mismos 768d, mismo `RETRIEVAL_DOCUMENT` en todo lo que se almacena.
5. **Escribir en Neo4j antes que en PG.** Rompe la garantía de que el grafo nunca referencia contenido inexistente.
6. **Borrar comunidades antiguas.** Se archivan con `computed_at`; la evolución de clusters entre crawls es señal SEO, no basura.

---

## 9. checklist de implantación por cliente

- [ ] `clients.yaml` con schema PG + contenedor Neo4j
- [ ] DDL Postgres (sección 3) ejecutado en el schema
- [ ] Constraints + índice vectorial Neo4j (sección 3)
- [ ] Pipeline 1–9 corriendo end-to-end sobre 50 URLs de prueba
- [ ] Verificación de idempotencia: segundo run sin cambios = 0 writes en Neo4j
- [ ] Tools del bicho respondiendo contra ambos motores
- [ ] `program.md` del agente actualizado con el catálogo de tools
