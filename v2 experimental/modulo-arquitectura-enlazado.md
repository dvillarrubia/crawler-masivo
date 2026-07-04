# Módulo de arquitectura y enlazado interno para crawler-masivo

Especificación para capturar, clasificar y explotar el grafo de enlaces internos de un site a partir del crawl, sin fuentes externas. Produce: profundidad de clic, PageRank interno ponderado, flujo de autoridad entre secciones y checks automáticos de enlazado.

**Principio de diseño:** la jerarquía (árbol por niveles) y el enlazado (grafo) se modelan por separado sobre los mismos nodos. La jerarquía es la intención; el enlazado es lo que realmente ocurre. El valor del módulo está en detectar dónde se contradicen.

---

## 1. Datos a capturar durante el crawl

### 1.1 Cambio en el extractor

Por cada `<a href>` interno encontrado, además de origen y destino, capturar el **contexto DOM del enlace** en el momento del parseo:

- Cadena de ancestros relevante: primer ancestro entre `nav`, `header`, `footer`, `aside`, `main`, `article`.
- Clases/id del contenedor inmediato (para detectar bloques `related`, `listing`, `breadcrumb`, `pagination`, `sidebar`).
- Texto ancla y atributo `rel` si existe.

Este es el único cambio necesario en el crawler: el resto del módulo es post-proceso.

### 1.2 Tabla de nodos (Postgres)

```sql
CREATE TABLE arch_nodes (
    url_hash        BYTEA PRIMARY KEY,          -- misma clave determinista que el resto del sistema
    url             TEXT NOT NULL,
    status_code     SMALLINT,
    indexable       BOOLEAN,                    -- combina meta robots + canonical + status
    seccion         TEXT,                       -- primer segmento del path (o clasificador semántico)
    tipo_pagina     TEXT,                       -- home | categoria | ficha | listado | post | transversal | paginacion
    click_depth     SMALLINT,                   -- calculado en §3.1
    pagerank        DOUBLE PRECISION,           -- calculado en §3.2
    in_total        INTEGER,
    in_contextual   INTEGER,
    out_total       INTEGER,
    out_contextual  INTEGER
);
```

### 1.3 Tabla de aristas (Postgres)

```sql
CREATE TABLE arch_edges (
    source_hash     BYTEA NOT NULL REFERENCES arch_nodes(url_hash),
    target_hash     BYTEA NOT NULL REFERENCES arch_nodes(url_hash),
    tipo_enlace     TEXT NOT NULL,              -- contextual | listado | breadcrumb | paginacion | menu | footer | sidebar
    dom_ancestro    TEXT,                       -- nav | header | footer | aside | main | article
    dom_container   TEXT,                       -- clases del contenedor inmediato
    anchor          TEXT,
    rel             TEXT,
    sitewide        BOOLEAN DEFAULT FALSE,      -- calculado en §2.2
    n_paginas       INTEGER DEFAULT 1,          -- en cuántas páginas de origen distintas aparece el par (dedupe)
    peso            REAL,                       -- asignado en §3.2 según tipo_enlace
    PRIMARY KEY (source_hash, target_hash, tipo_enlace)
);
CREATE INDEX idx_edges_target ON arch_edges(target_hash);
CREATE INDEX idx_edges_tipo   ON arch_edges(tipo_enlace);
```

Nota de volumen: deduplicar el par (origen, destino, tipo) y acumular en `n_paginas`. Los enlaces sitewide no se materializan página a página (un menú de 15 ítems en 100.000 URLs son 1,5 M de filas inútiles): se marca `sitewide = TRUE` y se trata analíticamente en el PageRank (§3.2).

---

## 2. Clasificación automática del tipo de enlace

Dos señales combinadas clasifican de forma fiable ~95 % de las aristas sin intervención humana.

### 2.1 Regla DOM (primera pasada)

| Condición sobre el contexto DOM | tipo_enlace |
|---|---|
| ancestro `nav` o `header` | `menu` |
| ancestro `footer` | `footer` |
| ancestro `aside` o container con `sidebar` | `sidebar` |
| container con `breadcrumb`, marcado `BreadcrumbList` | `breadcrumb` |
| container con `pagination`, `page-numbers`, `rel=next/prev` | `paginacion` |
| container con `related`, `listing`, `grid`, `card`, `archive` | `listado` |
| ancestro `main`/`article` sin match anterior | `contextual` |
| sin señal | `desconocido` (resuelve §2.2) |

Los selectores de la columna izquierda deben ser configurables por cliente (ver §6): cada CMS tiene sus clases.

### 2.2 Regla estadística de sitewide (segunda pasada)

Independientemente del DOM:

```sql
-- un destino que recibe el mismo enlace desde >80 % de las páginas es estructural por definición
UPDATE arch_edges e
SET sitewide = TRUE,
    tipo_enlace = CASE WHEN e.tipo_enlace IN ('menu','footer') THEN e.tipo_enlace ELSE 'menu' END
WHERE e.n_paginas::float / (SELECT count(*) FROM arch_nodes WHERE indexable) > 0.80;
```

Esta regla corrige los `desconocido` y los falsos contextuales (p. ej. banners en el body presentes en toda la plantilla). Umbral 0,80 configurable.

### 2.3 Regla de plantilla (opcional, tercera pasada)

Si un par (container DOM, destino) se repite de forma idéntica en >90 % de las páginas de un mismo `tipo_pagina`, es un bloque de plantilla → reclasificar como `listado` aunque esté en `main`. Distingue el enlace editorial real del módulo automático de "relacionados", que es la distinción que importa para el diagnóstico.

---

## 3. Cálculos

### 3.1 Profundidad de clic

BFS desde la home sobre **todas** las aristas (incluidas sitewide). No confundir con profundidad de directorio.

```python
from collections import deque

def click_depth(edges, home_hash):
    depth = {home_hash: 0}
    q = deque([home_hash])
    while q:
        cur = q.popleft()
        for tgt in edges.get(cur, ()):
            if tgt not in depth:
                depth[tgt] = depth[cur] + 1
                q.append(tgt)
    return depth   # las urls indexables ausentes del dict son huérfanas de enlazado
```

Las URLs indexables sin profundidad asignada (inaccesibles por enlaces, solo descubiertas por sitemap) se marcan huérfanas: es un check en sí mismo.

### 3.2 PageRank interno ponderado

Pesos por defecto (configurables):

| tipo_enlace | peso |
|---|---|
| contextual | 1.0 |
| listado | 0.6 |
| paginacion | 0.4 |
| breadcrumb | 0.3 |
| sidebar | 0.25 |
| menu | 0.2 |
| footer | 0.1 |

Parámetros: damping 0.85, 40–60 iteraciones o convergencia < 1e-9. Solo nodos indexables; los enlaces a no indexables se descartan del cálculo (fugas se reportan aparte).

Implementación según destino:

- **Python (networkx/igraph):** `pagerank(G, alpha=0.85, weight='peso')`. Los sitewide se materializan solo aquí, en memoria, como distribución analítica: cada nodo reparte `peso_menu × n_items` entre los destinos del menú sin crear aristas físicas.
- **Neo4j GDS (integración WebKnograph):** proyección con peso en la relación y `gds.pageRank.stream` con `relationshipWeightProperty: 'peso'`.

### 3.3 Flujo agregado entre secciones

Para cada arista, el flujo es `d × PR(origen) × peso / peso_saliente_total(origen)`. Agregación:

```sql
SELECT ns.seccion AS origen, nt.seccion AS destino,
       SUM(0.85 * ns.pagerank * e.peso / ow.total) AS flujo
FROM arch_edges e
JOIN arch_nodes ns ON ns.url_hash = e.source_hash
JOIN arch_nodes nt ON nt.url_hash = e.target_hash
JOIN out_weight ow ON ow.url_hash = e.source_hash   -- vista materializada con Σ pesos salientes por nodo
GROUP BY 1, 2
ORDER BY flujo DESC;
```

Esta tabla es la que alimenta el mapa de secciones (bloques y flechas). A partir de ~2.000 URLs es la vista por defecto; la vista nodo a nodo solo se usa para inspeccionar una sección concreta.

---

## 4. Checks automáticos

Todos deterministas: cierran en `verified` sin firma humana (compatibles con la capa técnica de chasis-seo).

| check | definición | query resumida |
|---|---|---|
| ARQ-01 huérfanas | indexable sin `click_depth` | `click_depth IS NULL AND indexable` |
| ARQ-02 profundidad excesiva | indexable a ≥ N clics (defecto N=4 negocio, N=5 resto) | `click_depth >= :n` |
| ARQ-03 sin contextual entrante | página de negocio con `in_contextual = 0` | `tipo_pagina IN (:negocio) AND in_contextual = 0` |
| ARQ-04 sumideros | página con autoridad que no enlaza a negocio | `out_contextual = 0 AND pagerank > :p50` |
| ARQ-05 reparto de autoridad | % de PR en secciones de negocio vs soporte | `SUM(pagerank) GROUP BY es_negocio` con umbral |
| ARQ-06 paginación profunda | cadenas de `paginacion` de longitud > K | camino máximo sobre aristas `paginacion` |
| ARQ-07 fugas | Σ enlaces hacia no indexables / redirecciones / 404 | join contra `status_code`/`indexable` |
| ARQ-08 desequilibrio jerárquico | secciones cuyo flujo entrante contradice su prioridad declarada | tabla de flujos vs config del cliente |

Salida de cada check: conteo, listado de URLs afectadas (muestra + export completo) y, donde aplique, la corrección de plantilla que resuelve el grupo entero (p. ej. ARQ-04 masivo en posts → bloque de relacionados en la plantilla, un cambio que mueve miles de enlaces).

---

## 5. Pipeline completo

```
crawl (captura DOM por enlace)
  → carga arch_nodes / arch_edges
  → clasificador de tipo (§2: DOM → sitewide → plantilla)
  → click_depth (§3.1) + pagerank ponderado (§3.2)
  → agregación por sección (§3.3)
  → checks ARQ-01…08 (§4)
  → export: excel de diagnóstico + tabla de flujos para visualización
```

Orden de escritura si se sincroniza con WebKnograph: Postgres primero, Neo4j después (relación `(:Page)-[:LINKS_TO {tipo, peso, sitewide, n_paginas}]->(:Page)` y propiedades `click_depth`, `pagerank_interno` en `Page`).

---

## 6. Configuración por cliente (yaml)

```yaml
cliente: ejemplo
home: "https://www.ejemplo.com/"
secciones_negocio: ["/ciclos/", "/cursos/"]     # para ARQ-03/04/05
tipo_pagina:                                    # regex sobre path → tipo
  ficha: "^/cfg[sm]-"
  post: "^/blog/.+"
  categoria: "^/fp-[a-z-]+/$"
selectores:                                     # overrides de la regla DOM §2.1
  listado: [".related-cycles", ".card-grid"]
  breadcrumb: [".migas"]
umbrales:
  sitewide_ratio: 0.80
  profundidad_negocio: 4
  profundidad_general: 5
  paginacion_max: 3
pesos:                                          # opcional, defecto §3.2
  contextual: 1.0
  listado: 0.6
```

---

## 7. Lo que este módulo no cubre (a propósito)

- **Enlaces renderizados por JavaScript:** el grafo refleja lo que ve el crawler en el modo de render elegido. Si el site necesita render JS, decidirlo en el probe previo al crawl; el análisis de arquitectura hereda esa decisión.
- **Autoridad externa:** el PageRank es interno puro. No modela backlinks; mide cómo el site reparte lo que tiene, que es lo único accionable desde el enlazado interno.
- **Juicio semántico** (¿este enlace contextual es *relevante*?): eso es capa semántica (embeddings anchor↔destino), fuera de este módulo y con firma humana.
