# Brief: integración de GLiNER2 en crawler-masivo — POC cruce entidad-query

## Objetivo

Integrar una capa de extracción de información con GLiNER2 (https://github.com/fastino-ai/GLiNER2) como etapa de enriquecimiento post-crawl en crawler-masivo, para producir cuatro análisis: mismatch de relevancia entidad-query contra GSC, gaps de cobertura, canibalización semántica y clasificación funnel + tipo de página. Debe ser estándar para cualquier cliente (leads, ecommerce, educación, salud): todo lo específico de cliente vive en un único `schema.yaml`.

## Regla de oro

**No escribas ni una línea de código de implementación hasta completar la fase 0 y recibir mi aprobación explícita del informe.** La fase 0 es solo lectura, análisis y un documento de decisiones.

---

## Fase 0: investigación (obligatoria, solo lectura)

Produce un único documento `INVESTIGACION.md` con estas cinco secciones. Cita rutas de fichero y nombres de tabla reales, no supongas nada.

### 1. Punto de integración en el crawler

- Localiza dónde y cómo persiste crawler-masivo el contenido extraído de cada URL (tabla, columnas, formato del texto: ¿HTML limpio, texto plano, por bloques?).
- Confirma el identificador canónico de URL (url_hash determinista) y su función de generación.
- Identifica el mejor punto de enganche para una etapa de enriquecimiento: ¿pipeline item de Scrapy, tarea async al cerrar el job, o proceso batch independiente que lee de Postgres a posteriori? Recomienda uno con argumentos (el batch posterior desacoplado es la hipótesis de partida, rebátela si procede).
- Documenta cómo se configura un cliente hoy (`clients.yaml`, schemas de Postgres por tenant, contenedor Neo4j por cliente) y cómo debería declararse el nuevo `schema.yaml` de extracción dentro de esa convención.

### 2. Estado real de Seontology

- Lee `contrato_seontology_neo4j_postgres.md` y lista sus invariantes.
- Audita qué parte de la ontología (6 nodos, 12 relaciones) está implementada de verdad en Neo4j hoy: ¿existe un nodo de tipo entidad? ¿Existe una relación tipo MENTIONS o equivalente página→entidad? ¿Qué constraints e índices hay?
- Concluye: qué habría que añadir a Neo4j para las aristas página-entidad (nodos, relaciones, propiedades) **sin violar el contrato**, y si algo del contrato debe extenderse formalmente antes de tocar nada.
- Si la parte de grafo no está lista, la recomendación por defecto es: el POC se queda 100 % en Postgres y se documenta la migración futura a Neo4j como anexo. Valida o rebate.

### 3. Solapes con lo ya construido

Busca en los repos y en la base de datos si alguno de los cuatro análisis ya está cubierto total o parcialmente:

- Canibalización: chasis-seo tiene SEM-01. ¿Qué método usa exactamente? ¿El enfoque por entidad primaria + banda de funnel lo sustituye, lo complementa o lo alimenta?
- Extracción de entidades previa: ¿queda algo operativo del Semantic Cluster Analyzer o de Semantic Sentinel (tablas, embeddings, catálogos) que se pueda reutilizar como catálogo resoluble o como gold set?
- Embeddings existentes: qué modelo y dimensión hay hoy en pgvector por cliente. El POC introduce `gemini-embedding-001`; documenta si conviven espacios vectoriales distintos y cómo se etiquetan para no mezclarlos jamás en una misma comparación.
- Clasificación funnel: ¿existe ya alguna etiqueta TOFU/MOFU/BOFU por URL en alguna tabla o se calcula ad hoc?
- Ingesta de GSC: ¿hay conector o tablas de queries GSC por cliente? Si no, propón el formato mínimo de ingesta (export BigQuery o API) y su tabla.

Entregable de esta sección: tabla de solapes con tres columnas — qué existe, qué se reutiliza, qué se descarta para no duplicar.

### 4. Encaje en la interfaz (consola chasis-seo)

- Revisa la estructura de checks de chasis-seo (47 checks, 4 capas) y su consola React (capas, coverage bar, triaje por severidad, cola de firmas).
- Propón los checks nuevos que emite este pipeline, con código, capa y tipo:
  - Mismatch de relevancia → capa semántica, candidato a cierre automático `verified` con evidencia (query, impresiones, entidad ausente).
  - Gap de cobertura → capa semántica, `verified` con evidencia de demanda.
  - Canibalización por entidad → capa semántica, **siempre check de firma** (juicio humano: consolidar / diferenciar / desoptimizar). Define su relación con SEM-01.
  - Circuito funnel roto (URL BOFU capturando queries TOFU o viceversa) → capa semántica o GEO según cómo esté organizado, propón.
- Documenta el formato exacto de ingesta de un check en la consola (JSON/tabla) para que `04_report.py` lo emita directamente además del Excel.

### 5. Decisiones abiertas para el humano

Cierra el informe con la lista de decisiones que necesitas de mí antes de implementar (punto de enganche, Postgres-only vs Neo4j, reutilización de catálogos previos, códigos de check definitivos, cliente piloto).

**STOP. Espera aprobación del informe antes de pasar a fase 1.**

---

## Fase 1: implementación (tras aprobación)

### Configuración por cliente: `schema.yaml`

Único fichero específico de cliente, tres bloques:

```yaml
entidades:            # tipos GLiNER2 con descripción en lenguaje natural
  resolubles:         # pasan por el gate de resolución a catálogo → entity_id
    producto: "Nombre de producto o modelo concreto mencionado en el texto"
    categoria: "Categoría o familia de producto"
  senal:              # evidencia de funnel, no se resuelven
    problema: "Problema o necesidad que expresa el usuario"
    atributo: "Característica de producto: material, talla, color"

catalogo:
  fuente: feed | crawl | generado   # 'generado' = clustering + validación humana
  ruta_o_tabla: ...

clasificacion:
  funnel: [TOFU, MOFU, BOFU]        # universal
  tipo_pagina: [ficha, categoria, guia, blog]   # por vertical
```

Incluye dos variantes de ejemplo completas: `schema.ecommerce.yaml` y `schema.leads.yaml`.

### Pipeline: 5 scripts

1. `00_gold_set.py` — muestrea 50 URLs + 200 queries GSC del cliente piloto para anotación manual. Métrica verificable: F1. Gate go/no-go del castellano: si el modelo base no alcanza F1 ≥ 0,75 en entidades resolubles, parar y decidir (adapter de fine-tuning vs descarte).
2. `01_extract_pages.py` — lee contenido del crawl desde Postgres, pasada GLiNER2 local en CPU con schema combinado (entidades + clasificación multi-label en un forward pass). Chunking ~384 tokens con solape, agregación de spans por URL, deduplicación por texto normalizado. Escribe `gliner_page_entities` y `gliner_page_labels`. Probar `quantize=True` y medir throughput.
3. `02_extract_queries.py` — misma pasada sobre queries únicas de GSC (agregado 3-6 meses, filtro de impresiones mínimas configurable). Escribe `gliner_query_entities`.
4. `03_resolve.py` — gate de resolución en tres zonas:
   - Embeddings con **API directa de Gemini** (`gemini-embedding-001`, dimensión 768 por MRL) a pgvector. El catálogo se embebe una vez.
   - Zona alta (≥ umbral alto): `entity_id` asignado, `resolved_by = 'cosine'`.
   - Zona baja (< umbral bajo): queda como entidad señal sin resolver.
   - Zona gris: **Gemini Flash vía OpenRouter** en batch — se le pasa la entidad con su span en contexto + los 3 candidatos más próximos del catálogo, responde id o `ninguno`. `resolved_by = 'llm'`.
   - Umbrales: **no heredar el 0,92 histórico**; calibrar por barrido contra el gold set (maximizar F1).
5. `04_report.py` — tres joins deterministas y doble salida: Excel de 3 pestañas (mismatch, gaps, canibalización) con columnas `accion` (vocabulario cerrado: onpage, crear_contenido, consolidar, diferenciar, desoptimizar, enlazar) y `prioridad` (fórmula determinista: impresiones × posición media × confianza de extracción), **más** el JSON de checks en el formato de ingesta de chasis-seo identificado en fase 0.

### DDL

Tablas en el schema Postgres del tenant: `gliner_page_entities` (url_hash, entity_text, entity_type, span_start, span_end, confidence, entity_id NULL, resolved_by NULL), `gliner_page_labels`, `gliner_query_entities`, `entity_catalog` (entity_id, nombre, embedding vector(768), fuente). Índice HNSW coseno sobre el catálogo. Columna de versión de modelo de embedding en toda tabla con vectores.

### Invariantes innegociables

- Postgres = sustancia (texto, spans, embeddings, métricas); Neo4j = estructura. Join solo por url_hash determinista. Nada de texto en Neo4j.
- LLM solo para juicio lingüístico (zona gris, naming de clusters de catálogo generado). Todo lo determinista en Python: entidad primaria por URL = frecuencia ponderada por posición (title/H1 > body), sin LLM.
- Multi-tenancy respetada: cero rutas o nombres de cliente hardcodeados; todo por `clients.yaml` + `schema.yaml`.
- Reparto de llamadas: GLiNER2 local CPU (extracción/clasificación) · embeddings API Gemini directa (OpenRouter no sirve embeddings) · juicios Gemini Flash vía OpenRouter (failover cambiando una línea).
- Espacios vectoriales nunca mezclados: comparaciones solo entre vectores del mismo modelo y dimensión.

### Criterios de éxito del POC

1. F1 sobre el gold set (por tipo de entidad).
2. Precisión de recomendaciones de mismatch validada a mano sobre muestra de 30.
3. Throughput de extracción medido (URLs/min en CPU) para dimensionar el paso a producción.
4. Los checks generados se ingestan y renderizan en la consola de chasis-seo sin tocar la consola.

### Estructura autoresearch (opcional pero preferida)

Organiza el repo con el patrón de tres ficheros: `prepare.py` (gold set, fixtures, umbrales congelados tras calibración), `train.py` (lógica de extracción/resolución iterable), `program.md` (estas instrucciones). Métrica verificable = F1 del gold set; presupuesto fijo por experimento; keeper/revert por iteración.
