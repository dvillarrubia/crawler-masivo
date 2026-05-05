# Handoff: Semantic Authority & Performance Mapper (v2)

## 1. Visión general del sistema

El sistema procesa la estructura completa de un sitio web para generar un **mapa vectorial semántico 2D** donde:

- La **posición** de cada URL refleja su significado semántico (embeddings).
- La **masa** (tamaño del punto) depende de su autoridad estructural (PageRank interno) y su rendimiento en clics (GSC).
- El **color** indica su rol en la arquitectura semántica: núcleo, periferia, outlier o par de canibalización.

Salidas principales:

- **Scatter UMAP** interactivo con clusters temáticos HDBSCAN (vista de análisis técnico).
- **Mapa de anillos concéntricos** con clasificación Core / Focus / Expansion / Peripheral (vista de presentación a cliente).
- Ranking de URLs que desvían el centroide semántico del sitio.
- Detección de pares de canibalización por similitud coseno.
- Análisis de brechas: vector desde el centroide hacia un topic objetivo.
- Informe CSV exportable con todas las métricas por URL.

---

## 2. Stack tecnológico

| capa | herramienta | motivo |
|---|---|---|
| Fuente de datos SEO | PostgreSQL (psycopg2 / SQLAlchemy) | Contenido ya normalizado y datos estructurales del sitio |
| Fuente de datos GSC | Google Search Console API v1 (cuenta de servicio) | Métricas por URL y queries por URL sin exportación manual |
| Embeddings | `text-embedding-3-large` (OpenAI) o `voyage-large-2-instruct` | Voyage superior en tareas retrieval; OpenAI más económico |
| Procesamiento | pandas, numpy, scikit-learn | estándar |
| Reducción dimensional | PCA (50 dims) → UMAP | PCA primero evita colapso de UMAP en corpus grandes |
| Grafos | NetworkX | cálculo de PageRank interno |
| Clustering | HDBSCAN | no requiere fijar k; robusto a ruido |
| Visualización | Plotly (interactivo) | hover, zoom, filtros por cluster |
| UI | Streamlit | bajo coste de desarrollo |

---

## 3. Fuentes de datos

### A. PostgreSQL — datos SEO y contenido

El contenido ya llega normalizado desde la base de datos. Desaparecen trafilatura, Playwright y cualquier lógica de extracción HTML. El pipeline asume que el texto en PostgreSQL es el contenido main body limpio, listo para embeber.

**Schema mínimo esperado:**

```sql
-- Tabla principal de páginas
CREATE TABLE pages (
    url          TEXT PRIMARY KEY,
    content      TEXT,           -- Contenido normalizado, ya limpio
    status_code  INT,
    indexable    BOOL,
    inlinks      INT,            -- Enlaces internos entrantes
    content_type TEXT,
    updated_at   TIMESTAMPTZ
);

-- Tabla de enlaces internos (para PageRank)
CREATE TABLE internal_links (
    source_url   TEXT,
    target_url   TEXT,
    link_count   INT DEFAULT 1,
    PRIMARY KEY (source_url, target_url)
);
```

**Conexión y carga:**

```python
import psycopg2
import pandas as pd
from sqlalchemy import create_engine

def load_pages_from_postgres(dsn: str) -> pd.DataFrame:
    """
    Carga páginas indexables con contenido no nulo.
    El filtrado se hace en SQL para evitar traer datos innecesarios.
    """
    engine = create_engine(dsn)
    query = """
        SELECT
            url,
            content,
            inlinks,
            updated_at
        FROM pages
        WHERE status_code = 200
          AND indexable = TRUE
          AND content_type LIKE 'text/html%'
          AND content IS NOT NULL
          AND LENGTH(content) > 0
        ORDER BY url
    """
    return pd.read_sql(query, engine)


def load_links_from_postgres(dsn: str) -> pd.DataFrame:
    """Carga el grafo de enlaces internos para cálculo de PageRank."""
    engine = create_engine(dsn)
    return pd.read_sql("SELECT source_url, target_url, link_count FROM internal_links", engine)
```

> **Nota sobre contenido vacío:** aunque el contenido llega normalizado, pueden existir filas con `content` no nulo pero con menos de 150 tokens (páginas de paginación, etiquetas, etc.). El pipeline aplica un umbral mínimo de 150 tokens tras la carga y marca esas URLs como `low_content`, excluyéndolas del mapa sin lanzar excepción.

---

### B. Google Search Console API — métricas y queries

La autenticación se gestiona con una **cuenta de servicio** (service account) de Google Cloud. No hay exportación manual de CSV ni dependencia de la interfaz web de GSC.

**Requisitos previos:**

1. Crear cuenta de servicio en Google Cloud Console con rol de solo lectura.
2. Descargar el fichero JSON de credenciales.
3. Añadir el email de la cuenta de servicio como usuario con permiso de lectura en GSC (Configuración → Usuarios y permisos).
4. Guardar la ruta al JSON en variable de entorno `GOOGLE_SERVICE_ACCOUNT_JSON`.

**Autenticación:**

```python
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

def get_gsc_service():
    credentials = service_account.Credentials.from_service_account_file(
        os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"],
        scopes=SCOPES,
    )
    return build("searchconsole", "v1", credentials=credentials)
```

**Extracción de métricas por URL (agregado):**

```python
from datetime import date, timedelta

def fetch_url_metrics(service, site_url: str,
                      days: int = 90) -> pd.DataFrame:
    """
    Extrae clicks, impressions y position por URL.
    Periodo recomendado: 90 días.
    Límite de la API: 25.000 filas por request; paginar si es necesario.
    """
    end_date = date.today() - timedelta(days=3)  # GSC tiene ~3 días de lag
    start_date = end_date - timedelta(days=days)

    rows = []
    start_row = 0
    page_size = 25000

    while True:
        response = service.searchanalytics().query(
            siteUrl=site_url,
            body={
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "dimensions": ["page"],
                "rowLimit": page_size,
                "startRow": start_row,
            }
        ).execute()

        batch = response.get("rows", [])
        if not batch:
            break

        for row in batch:
            rows.append({
                "url":         row["keys"][0],
                "clicks":      row["clicks"],
                "impressions": row["impressions"],
                "position":    row["position"],
            })

        start_row += page_size
        if len(batch) < page_size:
            break

    return pd.DataFrame(rows)
```

**Extracción de queries por URL (para intent layer):**

```python
def fetch_queries_for_url(service, site_url: str, page_url: str,
                          days: int = 90) -> pd.DataFrame:
    """
    Extrae los queries con los que aparece una URL específica.
    Límite de GSC: 1.000 queries por URL por request.
    Para corpus grandes, procesar en batch con rate limiting.
    """
    end_date = date.today() - timedelta(days=3)
    start_date = end_date - timedelta(days=days)

    response = service.searchanalytics().query(
        siteUrl=site_url,
        body={
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["query"],
            "dimensionFilterGroups": [{
                "filters": [{
                    "dimension": "page",
                    "operator": "equals",
                    "expression": page_url,
                }]
            }],
            "rowLimit": 1000,
        }
    ).execute()

    rows = []
    for row in response.get("rows", []):
        rows.append({
            "url":         page_url,
            "query":       row["keys"][0],
            "clicks":      row["clicks"],
            "impressions": row["impressions"],
            "position":    row["position"],
        })

    return pd.DataFrame(rows)


def fetch_all_queries(service, site_url: str,
                      urls: list[str], days: int = 90,
                      rate_limit_rps: float = 5.0) -> pd.DataFrame:
    """
    Itera sobre todas las URLs y extrae sus queries.
    Rate limiting: la GSC API permite ~200 requests/100s por proyecto.
    Usar exponential backoff ante errores 429.
    """
    import time
    from tenacity import retry, stop_after_attempt, wait_exponential

    @retry(stop=stop_after_attempt(5),
           wait=wait_exponential(multiplier=1, min=2, max=60))
    def fetch_with_retry(url):
        return fetch_queries_for_url(service, site_url, url, days)

    all_dfs = []
    delay = 1.0 / rate_limit_rps

    for i, url in enumerate(urls):
        try:
            df = fetch_with_retry(url)
            all_dfs.append(df)
        except Exception as e:
            logging.warning(f"[GSC queries] Error en {url}: {e}")
        time.sleep(delay)

        if i % 100 == 0:
            logging.info(f"[GSC queries] Procesadas {i}/{len(urls)} URLs")

    return pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
```

> **Variables de entorno requeridas:**

```bash
GOOGLE_SERVICE_ACCOUNT_JSON=/ruta/a/credenciales.json
GSC_SITE_URL=sc-domain:ejemplo.com   # o https://www.ejemplo.com/
DATABASE_URL=postgresql://user:pass@host:5432/dbname
OPENAI_API_KEY=sk-...
```

> **Sobre el formato de `GSC_SITE_URL`:** GSC distingue entre propiedades de dominio (`sc-domain:ejemplo.com`) y propiedades de URL (`https://www.ejemplo.com/`). El tipo de propiedad determina el formato de las URLs devueltas por la API. Debe coincidir con cómo está registrado el sitio en GSC, y las URLs que devuelve la API deben normalizarse antes del join con PostgreSQL igual que antes (lowercase, trailing slash, sin parámetros UTM).

---

## 4. Algoritmos y modelado matemático

### A. Validación de contenido desde PostgreSQL

El contenido llega ya normalizado desde PostgreSQL. No hay extracción HTML, no hay trafilatura, no hay Playwright. La única validación necesaria es el umbral mínimo de tokens para excluir páginas demasiado cortas para ser semánticamente representativas (paginaciones, etiquetas, páginas de categoría vacías).

```python
MIN_CONTENT_TOKENS = 150  # Ajustable según tipo de sitio

def validate_content(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filtra páginas con contenido insuficiente.
    Las URLs excluidas se loggean para auditoría pero no lanzan excepción.
    El pipeline continúa con el resto del corpus.
    """
    df = df.copy()
    df["token_count"] = df["content"].str.split().str.len()
    low_content_mask = df["token_count"] < MIN_CONTENT_TOKENS

    if low_content_mask.any():
        logging.warning(
            f"[validate_content] {low_content_mask.sum()} URLs excluidas "
            f"por low_content (< {MIN_CONTENT_TOKENS} tokens): "
            f"{df[low_content_mask]['url'].tolist()[:10]}..."
        )

    return df[~low_content_mask].copy()
```

### B. Vectorización: estrategia híbrida por longitud

**Problema del contenido completo:** aunque el contenido ya llega limpio desde PostgreSQL, embeber la página entera en un solo vector promedia todo su contenido. Una guía de 5.000 palabras que cubre tres subtemas produce un embedding "promedio" que no representa bien ninguno de los tres y queda semánticamente en tierra de nadie.

**Problema del max-pooling dimensional:** tomar el valor máximo por cada dimensión de forma independiente produce un vector que no corresponde a ningún chunk real del documento. Es geométricamente incoherente.

**Solución: selector automático por longitud.**

```python
LONG_PAGE_THRESHOLD = 800  # tokens; ajustar según corpus

def vectorize_page(content: str, model) -> np.ndarray:
    """
    Páginas cortas (< 800 tokens): embedding directo del contenido completo.
    Más fiel al documento, menos superficie de error.

    Páginas largas (≥ 800 tokens): chunk representativo.
    Evita que páginas heterogéneas queden en tierra de nadie semántica.
    """
    tokens = len(content.split())

    if tokens < LONG_PAGE_THRESHOLD:
        return model.embed([content])[0]
    else:
        chunks = chunk_text(content,
                            size=500,      # tokens por chunk
                            overlap=0.10)  # 10% overlap
        return get_representative_chunk(chunks, model)


def get_representative_chunk(chunks: list[str], model) -> np.ndarray:
    """
    1. Genera embeddings para todos los chunks del documento.
    2. Calcula el centroide del documento (media aritmética).
    3. Selecciona el chunk con mayor similitud coseno al centroide:
       representa el tema dominante sin distorsión geométrica.

    Nota: favorece el tema más denso, no necesariamente el más
    relevante para el negocio. Mitigado parcialmente por el peso
    de PageRank y clics en el centroide del sitio.
    """
    embeddings = np.array(model.embed(chunks))
    centroid = embeddings.mean(axis=0)
    similarities = cosine_similarity([centroid], embeddings)[0]
    best_idx = similarities.argmax()
    return embeddings[best_idx]
```

Parámetros de chunking:
- Tamaño: 500 tokens
- Overlap: 10% (50 tokens)
- Separador preferido: párrafo (`\n\n`), con fallback a oración

### C. Vector de intención (Intent Layer)

**El problema que ningún enfoque de contenido resuelve solo:** dos páginas semánticamente similares ("cómo hacer pan" y "receta de pan casero") pueden apuntar a intenciones de búsqueda distintas (informacional vs. transaccional). Un mapa basado únicamente en contenido agrupa páginas por tema pero ignora para qué sirven en el funnel. Esto produce decisiones de arquitectura erróneas: se marcan como "canibalización" páginas que en realidad son complementarias, o se consolida contenido que debería mantenerse separado porque sirve a usuarios en momentos distintos.

**Solución: vector híbrido contenido + intención.**

El vector final de cada página es una combinación ponderada del vector de contenido y un vector de intención derivado de los queries reales con los que la página aparece en Google.

```python
def build_intent_vector(url: str, gsc_queries: pd.DataFrame,
                        model) -> np.ndarray | None:
    """
    Construye un vector de intención a partir de los queries de GSC
    con los que esta URL tiene impresiones.

    Pondera cada query por sus impresiones (no por clics):
    las impresiones reflejan para qué muestra Google la página,
    independientemente de si el usuario hace clic o no.
    """
    page_queries = gsc_queries[gsc_queries["url"] == url].copy()

    if page_queries.empty or page_queries["impressions"].sum() == 0:
        return None  # Sin datos de intención; usar solo vector de contenido

    # Construir corpus ponderado: repetir cada query proporcional a impresiones
    # (alternativa más eficiente: weighted average de embeddings)
    query_texts = page_queries["query"].tolist()
    query_weights = minmax_normalize(page_queries["impressions"].values)

    query_embeddings = np.array(model.embed(query_texts))
    intent_vector = np.average(query_embeddings, axis=0, weights=query_weights)
    return intent_vector


def build_final_vector(content_vector: np.ndarray,
                       intent_vector: np.ndarray | None,
                       gamma: float = 0.4) -> np.ndarray:
    """
    Vector final = (1 - γ) · V_content + γ · V_intent

    γ = 0.0 → solo contenido (sin datos GSC o modo degradado)
    γ = 0.4 → balance recomendado (default)
    γ = 1.0 → solo intención (experimental, no recomendado)

    Si no hay vector de intención, γ se fuerza a 0.0 automáticamente.
    """
    if intent_vector is None:
        return content_vector

    # Normalizar ambos vectores antes de combinar
    v_c = content_vector / (np.linalg.norm(content_vector) + 1e-9)
    v_i = intent_vector / (np.linalg.norm(intent_vector) + 1e-9)

    return (1 - gamma) * v_c + gamma * v_i
```

**Columnas adicionales necesarias en gsc.csv para esta función:**

| columna | tipo | notas |
|---|---|---|
| `query` | string | query exacta de búsqueda |
| `url` | string | URL que aparece para ese query |
| `impressions` | int | impresiones del par query-URL |
| `clicks` | int | clics del par query-URL |

> La exportación de GSC por defecto agrega métricas a nivel de URL. Para el intent layer se necesita la exportación a nivel de **query + URL** (máximo 1.000 queries por URL en la API de GSC, suficiente para la mayoría de sitios).

**Interpretación del parámetro γ:**

| γ | resultado | cuándo usarlo |
|---|---|---|
| 0.0 | Mapa puramente temático | Sitios sin datos GSC, sitios nuevos |
| 0.2 | Contenido dominante, intención como señal suave | Sitios con GSC pero poca cobertura de queries |
| 0.4 | Balance (recomendado) | Sitios establecidos con GSC completo |
| 0.6 | Intención dominante | Sitios de ecommerce donde el funnel es la variable crítica |

**Efecto en el mapa:** con γ > 0, páginas con el mismo tema pero distinta intención se separan en el espacio vectorial. Una página transaccional de "comprar zapatillas running" y una informacional de "cómo elegir zapatillas running" dejan de colapsar en el mismo punto del mapa aunque compartan vocabulario. La canibalización detectada con γ > 0 es más precisa: ya no confunde complementariedad con competencia.

**Detección de intención dominante por URL:**

```python
INTENT_LABELS = {
    "informacional": ["cómo", "qué es", "guía", "tutorial", "diferencia entre"],
    "navegacional":  ["marca", "login", "acceder", "página oficial"],
    "transaccional": ["comprar", "precio", "oferta", "contratar", "presupuesto"],
    "comercial":     ["mejor", "comparativa", "opiniones", "reseña", "alternativa"],
}

def classify_intent(queries: list[str]) -> str:
    """
    Clasificación heurística de intención dominante basada en
    los queries con más impresiones. Útil para colorear el mapa
    por intención en lugar de por cluster temático.
    """
    scores = {intent: 0 for intent in INTENT_LABELS}
    for query in queries:
        q_lower = query.lower()
        for intent, signals in INTENT_LABELS.items():
            if any(s in q_lower for s in signals):
                scores[intent] += 1
    dominant = max(scores, key=scores.get)
    return dominant if scores[dominant] > 0 else "sin_clasificar"
```

**Nueva capa de color disponible en la UI:** además del color por cluster HDBSCAN, el usuario puede alternar a color por intención dominante (informacional / navegacional / transaccional / comercial / sin clasificar). Esto permite detectar de inmediato si el contenido de tipo comercial está semánticamente cerca del núcleo del sitio o desplazado a la periferia.

### C. Centroide pesado del sitio

El centroide semántico del sitio `C_site` es la media ponderada de todos los vectores de página, donde el peso `w_i` combina PageRank normalizado y clics normalizados:

```
w_i = α · Norm(PR_i) + β · Norm(Ck_i)

α + β = 1  (hiperparámetros configurables, default: α=0.6, β=0.4)

Norm(x) = (x - x_min) / (x_max - x_min)  [Min-Max, rango 0–1]

C_site = Σ(w_i · V_i) / Σ(w_i)
```

**Caso URLs sin clics (`Ck_i = 0`):**
- `Norm(Ck_i) = 0`
- Su peso queda en `w_i = α · Norm(PR_i)`
- Participan en el cálculo del centroide con menor influencia
- No se excluyen: un PageRank alto sin clics puede indicar contenido estructuralmente importante no descubierto aún

**Caso sitio sin datos GSC en absoluto:**
- Modo degradado: `β = 0`, `α = 1`
- El sistema emite warning explícito en log

### D. Detección de outliers semánticos (IQR)

```
d_i = ||V_i - C_site||₂   (distancia euclídea de cada página al centroide)

Q1, Q3 = percentiles 25 y 75 de {d_i}
IQR = Q3 - Q1

Outlier si:  d_i > Q3 + 1.5 · IQR
Periférico si: Q3 < d_i ≤ Q3 + 1.5 · IQR  [zona gris, no marcada como outlier]
Core si: d_i ≤ Q3
```

### E. Detección de canibalización semántica

Dos URLs canibalizan si compiten por el mismo espacio semántico y ninguna tiene autoridad estructural dominante clara.

```python
CANNIBAL_THRESHOLD = 0.92  # similitud coseno

def detect_cannibalization(vectors: dict[str, np.ndarray],
                           weights: dict[str, float]) -> list[tuple]:
    pairs = []
    urls = list(vectors.keys())
    for i in range(len(urls)):
        for j in range(i + 1, len(urls)):
            sim = cosine_similarity([vectors[urls[i]]],
                                    [vectors[urls[j]]])[0][0]
            if sim >= CANNIBAL_THRESHOLD:
                # Identificar cuál de las dos tiene más autoridad
                dominant = urls[i] if weights[urls[i]] >= weights[urls[j]] else urls[j]
                weak = urls[j] if dominant == urls[i] else urls[i]
                pairs.append({
                    "url_dominant": dominant,
                    "url_weak": weak,
                    "cosine_similarity": round(sim, 4),
                    "recommendation": "consolidar en dominant o diferenciar topic"
                })
    return sorted(pairs, key=lambda x: x["cosine_similarity"], reverse=True)
```

> El umbral `0.92` es conservador. Para sitios de ecommerce con fichas de producto muy similares puede bajarse a `0.88`. Para sitios editoriales con artículos temáticamente cercanos pero diferenciados, puede subirse a `0.95`.

---

## 5. Pipeline completo

```python
class SemanticEngine:
    def __init__(self, alpha: float = 0.6, beta: float = 0.4,
                 cannibal_threshold: float = 0.92):
        assert abs(alpha + beta - 1.0) < 1e-6, "α + β debe ser igual a 1"
        self.alpha = alpha
        self.beta = beta
        self.cannibal_threshold = cannibal_threshold

    def process_site(self, dsn: str, site_url: str) -> dict:
        """
        dsn:      Cadena de conexión PostgreSQL.
                  Ejemplo: "postgresql://user:pass@host:5432/dbname"
                  Mejor práctica: leer de DATABASE_URL en env.

        site_url: Propiedad GSC.
                  Ejemplo: "sc-domain:ejemplo.com"
                  Leer de GSC_SITE_URL en env.
        """

        # 1. Carga desde PostgreSQL
        df = load_pages_from_postgres(dsn)
        links_df = load_links_from_postgres(dsn)
        df = validate_content(df)  # Excluir low_content (< 150 tokens)

        # 2. Métricas GSC por URL (clicks, impressions, position)
        gsc_service = get_gsc_service()
        gsc_metrics = fetch_url_metrics(gsc_service, site_url, days=90)
        gsc_metrics["url"] = gsc_metrics["url"].apply(normalize_url)
        df["url_norm"] = df["url"].apply(normalize_url)
        df = df.merge(gsc_metrics.rename(columns={"url": "url_norm"}),
                      on="url_norm", how="left")
        df["clicks"] = df["clicks"].fillna(0)
        df["impressions"] = df["impressions"].fillna(0)

        # 3. Vectorización de contenido (híbrida por longitud)
        df["content_vector"] = df["content"].apply(
            lambda c: vectorize_page(c, embedding_model))

        # 3b. Intent layer: queries GSC por URL
        # fetch_all_queries itera sobre todas las URLs con rate limiting
        gsc_queries = fetch_all_queries(
            gsc_service, site_url, df["url"].tolist(), days=90)

        df["intent_vector"] = df["url"].apply(
            lambda u: build_intent_vector(u, gsc_queries, embedding_model))
        df["intent_label"] = df["url"].apply(
            lambda u: classify_intent(
                gsc_queries[gsc_queries["url"] == u]["query"].tolist()))

        # Vector final: combinación ponderada contenido + intención
        df["vector"] = df.apply(
            lambda r: build_final_vector(r["content_vector"],
                                         r["intent_vector"],
                                         gamma=0.4), axis=1)

        # 4. PageRank interno desde grafo en PostgreSQL
        G = nx.from_pandas_edgelist(
            links_df, "source_url", "target_url",
            edge_attr="link_count", create_using=nx.DiGraph())
        pr = nx.pagerank(G, weight="link_count", alpha=0.85)
        df["pagerank"] = df["url"].map(pr).fillna(0)

        # 5. Pesos combinados
        df["pr_norm"] = minmax_normalize(df["pagerank"])
        df["clicks_norm"] = minmax_normalize(df["clicks"])
        df["weight"] = (self.alpha * df["pr_norm"] +
                        self.beta * df["clicks_norm"])

        # 6. Centroide pesado
        vectors = np.vstack(df["vector"].values)
        weights = df["weight"].values
        centroid = np.average(vectors, axis=0, weights=weights)

        # 7. Distancias y detección de outliers
        df["distance_to_centroid"] = np.linalg.norm(
            vectors - centroid, axis=1)
        df["semantic_role"] = classify_by_iqr(df["distance_to_centroid"])

        # 8. Reducción dimensional: PCA → UMAP
        # PCA a 50 dims primero: reduce coste computacional de UMAP
        # y evita el colapso de la proyección en corpus > 5.000 URLs
        n_components_pca = min(50, vectors.shape[0] - 1, vectors.shape[1])
        pca = PCA(n_components=n_components_pca)
        vectors_pca = pca.fit_transform(vectors)

        n_neighbors = min(15, len(df) - 1)  # Adaptativo al tamaño del corpus
        umap_model = UMAP(n_neighbors=n_neighbors,
                          min_dist=0.1,
                          metric="cosine",
                          random_state=42)
        coords_2d = umap_model.fit_transform(vectors_pca)
        df[["x", "y"]] = coords_2d

        # 9. Clustering temático post-UMAP
        clusterer = hdbscan.HDBSCAN(min_cluster_size=max(5, len(df) // 50),
                                     metric="euclidean")
        df["cluster"] = clusterer.fit_predict(coords_2d)
        # cluster == -1 → ruido / no asignado

        # 10. Canibalización semántica
        vectors_dict = dict(zip(df["url"], df["vector"]))
        weights_dict = dict(zip(df["url"], df["weight"]))
        cannibalization_pairs = detect_cannibalization(
            vectors_dict, weights_dict, self.cannibal_threshold)

        return {
            "dataframe": df,
            "centroid": centroid,
            "cannibalization": cannibalization_pairs,
        }
```

---

## 6. Reducción dimensional: configuración adaptativa

UMAP con parámetros fijos es una trampa. `n_neighbors=15` produce clusters artificiales en corpus pequeños y es computacionalmente inviable en corpus grandes sin reducción previa.

| tamaño del corpus | estrategia |
|---|---|
| < 200 URLs | UMAP directo, `n_neighbors = max(5, n//10)` |
| 200–5.000 URLs | PCA a 50 dims → UMAP, `n_neighbors=15` |
| > 5.000 URLs | PCA a 50 dims → UMAP con `low_memory=True`, `n_neighbors=30` |

```python
def get_umap_config(n_urls: int) -> dict:
    if n_urls < 200:
        return {"n_neighbors": max(5, n_urls // 10), "low_memory": False}
    elif n_urls < 5000:
        return {"n_neighbors": 15, "low_memory": False}
    else:
        return {"n_neighbors": 30, "low_memory": True}
```

---

## 7. Visualización interactiva

El sistema ofrece dos modos de visualización complementarios, seleccionables desde la UI. Ninguno reemplaza al otro: tienen audiencias y propósitos distintos.

| modo | audiencia | propósito |
|---|---|---|
| Scatter UMAP (análisis) | SEO técnico / analista | Detectar clusters temáticos reales, canibalización, estructura |
| Anillos concéntricos (cliente) | Cliente / dirección | Comunicar salud semántica del sitio de forma inmediata |

---

### Modo A: scatter UMAP (análisis técnico)

| capa | codificación visual |
|---|---|
| Posición (x, y) | Proyección UMAP sobre embeddings |
| Tamaño del punto | Proporcional a `weight` (PageRank + clics) |
| Color del punto | Cluster HDBSCAN (paleta categórica) |
| Borde del punto | Rojo si `semantic_role == "outlier"` |
| Líneas | Semitransparentes entre puntos del mismo cluster |
| Marcador especial | Diamante negro = centroide del sitio |

---

### Modo B: anillos concéntricos (presentación a cliente)

Inspirado en la visualización de Go Fish Digital, adaptado con pesos propios. Las distancias al centroide se calculan en el espacio de alta dimensión (no en UMAP 2D), lo que garantiza que los anillos reflejen distancia semántica real y no artefactos de la proyección.

**Clasificación por cuartiles dinámicos (IQR sobre no-outliers):**

Los anillos no tienen radios fijos: se adaptan automáticamente a la dispersión de cada sitio. Esto es crucial: un sitio muy especializado tendrá anillos más estrechos que uno generalista, sin que el analista tenga que calibrar umbrales manualmente.

```python
def classify_rings(df: pd.DataFrame) -> pd.DataFrame:
    """
    Outliers: IQR clásico (Q3 + 1.5·IQR) sobre todas las distancias.
    Para las URLs no-outlier, se dividen en tres anillos por cuartiles:
      - Core:      Q0–Q1 (25% más cercanas al centroide)
      - Focus:     Q1–Q3 (50% centrales)
      - Expansion: Q3–max_non_outlier (25% más alejadas, sin ser outlier)
    """
    d = df["distance_to_centroid"]
    Q1, Q3 = d.quantile(0.25), d.quantile(0.75)
    IQR = Q3 - Q1
    outlier_threshold = Q3 + 1.5 * IQR

    conditions = [
        d > outlier_threshold,
        d > Q3,
        d > Q1,
        d <= Q1,
    ]
    labels = ["Peripheral", "Expansion", "Focus", "Core"]
    df["ring"] = np.select(conditions, labels)

    # Métricas de resumen del sitio
    ring_counts = df["ring"].value_counts()
    focus_score = round(
        100 * (1 - df[df["ring"] == "Peripheral"]["weight"].sum() /
               df["weight"].sum()), 1)

    return df, {
        "focus_score": focus_score,          # 0–100, mayor = más enfocado
        "semantic_radius": round(d.median(), 4),
        "drift_score": round(d[d > outlier_threshold].mean(), 4),
        "ring_counts": ring_counts.to_dict(),
    }
```

**Codificación visual de los anillos:**

| anillo | color | distancia | significado |
|---|---|---|---|
| Core | verde | Q0–Q1 | 25% de páginas más alineadas con el topic principal |
| Focus | azul | Q1–Q3 | 50% de páginas de soporte temático |
| Expansion | naranja | Q3–outlier | 25% de páginas en expansión temática legítima |
| Peripheral | rojo | > outlier | Páginas que distorsionan el foco semántico del sitio |

**Métricas del dashboard de anillos:**

```
Focus Score:     88.1  (0–100, mayor = sitio más enfocado)
Semantic Radius: 87.5  (distancia mediana al centroide, en espacio original)
Drift Score:      0.6  (distancia media de páginas Peripheral)
Total páginas:    318
```

> **Diferencia clave respecto a Go Fish Digital:** sus anillos usan centroide sin ponderar (media simple de todos los embeddings). El nuestro usa centroide pesado por PageRank y clics, de modo que páginas con alta autoridad y tráfico anclan el centro semántico. En sitios con mucho contenido de baja calidad o huérfano, esto produce un centroide más representativo del topic real del negocio y no del promedio bruto de todo lo publicado.

**Renderizado con Plotly:**

```python
def build_ring_map(df: pd.DataFrame, site_metrics: dict) -> go.Figure:
    """
    Coordenadas del scatter: usar las mismas coords_2d del UMAP,
    pero el radio visual de cada anillo se calcula proporcionalmente
    a los cuartiles de distancia real (no a las coords 2D).
    El centro visual es siempre el centroide proyectado en 2D.
    """
    color_map = {
        "Core": "#4CAF50",
        "Focus": "#2196F3",
        "Expansion": "#FF9800",
        "Peripheral": "#F44336",
    }

    fig = go.Figure()

    # Anillos de fondo (círculos SVG con relleno semitransparente)
    for ring, radius_quantile in [("Peripheral", 1.0),
                                   ("Expansion", 0.75),
                                   ("Focus", 0.50),
                                   ("Core", 0.25)]:
        fig.add_shape(type="circle",
                      xref="x", yref="y",
                      x0=-radius_quantile, y0=-radius_quantile,
                      x1=radius_quantile,  y1=radius_quantile,
                      line=dict(color=color_map[ring], dash="dash", width=1.5),
                      fillcolor=color_map[ring],
                      opacity=0.06)

    # Scatter de páginas
    for ring in ["Core", "Focus", "Expansion", "Peripheral"]:
        mask = df["ring"] == ring
        fig.add_trace(go.Scatter(
            x=df[mask]["x"], y=df[mask]["y"],
            mode="markers",
            name=f"{ring} ({mask.sum()})",
            marker=dict(
                color=color_map[ring],
                size=df[mask]["weight"] * 20 + 5,  # Tamaño = autoridad
                opacity=0.8,
            ),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "Anillo: " + ring + "<br>"
                "Distancia: %{customdata[1]:.4f}<br>"
                "Clics: %{customdata[2]}<br>"
                "Peso: %{customdata[3]:.3f}<extra></extra>"
            ),
            customdata=df[mask][["url", "distance_to_centroid",
                                  "clicks", "weight"]].values,
        ))

    # Centroide
    fig.add_trace(go.Scatter(
        x=[0], y=[0], mode="markers+text",
        marker=dict(symbol="star", size=16, color="black"),
        text=["Site Center"], textposition="bottom center",
        showlegend=False,
    ))

    fig.update_layout(
        title=f"Mapa semántico — Focus: {site_metrics['focus_score']} | "
              f"Radius: {site_metrics['semantic_radius']} | "
              f"Drift: {site_metrics['drift_score']}",
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False,
                   scaleanchor="x"),
        plot_bgcolor="white",
    )
    return fig
```

---

### Tooltip unificado (ambos modos)

```
URL: /categoria/articulo-ejemplo
Anillo: Focus
Cluster HDBSCAN: 3 (tecnología)
Intención dominante: informacional
Distancia al centroide: 0.34
PageRank normalizado: 0.71
Clics (90d): 1.243
Peso combinado: 0.65
γ efectivo: 0.4 (contenido + intención)
Canibaliza con: /blog/articulo-similar (sim: 0.94)
```

**Modos de color disponibles en la UI (toggle):**

| modo | variable | utilidad |
|---|---|---|
| Por cluster | HDBSCAN | Detectar grupos temáticos reales |
| Por anillo | Core/Focus/Expansion/Peripheral | Comunicar salud semántica |
| Por intención | Informacional/Transaccional/Comercial/Navegacional | Detectar huecos en el funnel |

### Análisis de brechas (Gap Analysis)

El usuario introduce un **topic objetivo** en texto libre. El sistema:

1. Genera el embedding `V_target` del topic.
2. Dibuja una flecha desde `C_site` hacia `V_target`.
3. Calcula el punto medio de esa trayectoria y busca las 5 URLs del corpus más cercanas a ese punto (contenido que podría reforzarse).
4. Identifica las coordenadas del espacio vacío en esa dirección (zona sin URLs → brecha de contenido confirmada).
5. Sugiere el título semántico aproximado del contenido que debería crearse para rellenar la brecha.

```python
def gap_analysis(centroid: np.ndarray, target_text: str,
                 coords_2d: np.ndarray, df: pd.DataFrame,
                 model) -> dict:
    v_target = model.embed([target_text])[0]

    # Midpoint en espacio de alta dimensión
    midpoint_hd = (centroid + v_target) / 2

    # URLs más cercanas al midpoint
    distances = np.linalg.norm(
        np.vstack(df["vector"].values) - midpoint_hd, axis=1)
    df["gap_distance"] = distances
    closest = df.nsmallest(5, "gap_distance")[["url", "gap_distance"]]

    return {
        "target_vector": v_target,
        "closest_urls": closest,
        "gap_confirmed": bool((distances < distances.mean()).sum() < 3)
    }
```

---

## 8. Análisis de deriva semántica

Lista las URLs que más alejan el centroide del sitio de su foco semántico principal. Útil para decisiones de poda o consolidación de contenido.

```python
def drift_analysis(df: pd.DataFrame, centroid: np.ndarray,
                   top_n: int = 10) -> pd.DataFrame:
    """
    Para cada URL, calcula cuánto se desplazaría el centroide
    si esa URL fuese eliminada del corpus. Las URLs con mayor
    delta de desplazamiento son las que más distorsionan el foco.
    """
    results = []
    vectors = np.vstack(df["vector"].values)
    weights = df["weight"].values

    for i, row in df.iterrows():
        mask = df.index != i
        c_without = np.average(vectors[mask], axis=0,
                               weights=weights[mask])
        delta = np.linalg.norm(c_without - centroid)
        results.append({"url": row["url"], "centroid_delta": delta})

    return (pd.DataFrame(results)
              .sort_values("centroid_delta", ascending=False)
              .head(top_n))
```

---

## 9. Outputs exportables

| fichero | contenido |
|---|---|
| `scatter_umap.html` | Scatter UMAP interactivo con clusters HDBSCAN (vista técnica) |
| `ring_map.html` | Mapa de anillos concéntricos Core/Focus/Expansion/Peripheral (vista cliente) |
| `urls_report.csv` | URLs con x, y, cluster, ring, intent_label, γ_efectivo, distancia, weight, clics, PR |
| `cannibalization.csv` | Pares de canibalización con similitud y URL dominante |
| `drift_top10.csv` | URLs con mayor desviación del centroide |
| `gap_analysis.json` | Resultado del análisis de brechas por topic objetivo |
| `site_summary.json` | Focus Score, Semantic Radius, Drift Score y conteos por anillo e intención |

---

## 10. CLAUDE.md para Claude Code

```markdown
# CLAUDE.md — Semantic Authority & Performance Mapper

## Arquitectura del proyecto

sem-seo-engine/
├── src/
│   ├── db.py              # load_pages_from_postgres(), load_links_from_postgres()
│   ├── gsc.py             # get_gsc_service(), fetch_url_metrics(), fetch_all_queries()
│   ├── embeddings.py      # vectorize_page(), get_representative_chunk(), rate limiter
│   ├── intent.py          # build_intent_vector(), build_final_vector(), classify_intent()
│   ├── graph.py           # pagerank desde links_df
│   ├── engine.py          # SemanticEngine (clase principal)
│   ├── analysis.py        # drift_analysis(), gap_analysis(), detect_cannibalization()
│   └── visualization.py   # build_scatter_map(), build_ring_map(), streamlit_app()
├── outputs/               # Generado por el pipeline
├── .env                   # Variables de entorno (no commitear)
└── requirements.txt

## Variables de entorno (.env)

```
DATABASE_URL=postgresql://user:pass@host:5432/dbname
GSC_SITE_URL=sc-domain:ejemplo.com
GOOGLE_SERVICE_ACCOUNT_JSON=/ruta/a/credenciales.json
OPENAI_API_KEY=sk-...
```

## Schema PostgreSQL requerido

```sql
-- Tabla de páginas (contenido ya normalizado)
pages: url, content, status_code, indexable,
       inlinks, content_type, updated_at

-- Tabla de grafo de enlaces internos
internal_links: source_url, target_url, link_count
```

## Reglas de implementación

1. Nunca hardcodear credenciales. Leer siempre de variables de entorno
   con `os.environ` o `python-dotenv`.

2. Normalización de URLs: lowercase + strip trailing slash +
   eliminar parámetros utm_*, fbclid, gclid. Aplicar en carga desde
   PostgreSQL Y en respuestas de la GSC API antes de cualquier join.

3. Contenido: viene limpio de PostgreSQL. Validar solo umbral mínimo
   de 150 tokens. Excluir low_content sin lanzar excepción.

4. Vectorización por página: selector automático por longitud.
   Umbral: 800 tokens.
   - Páginas cortas (< 800t): model.embed([content])[0] directo.
   - Páginas largas (≥ 800t): get_representative_chunk() — chunk con
     mayor similitud coseno al centroide del documento. NUNCA max-pooling.

5. Intent layer: fetch_all_queries() con rate limiting (5 rps) y
   exponential backoff (tenacity). Ponderar queries por impresiones.
   Combinar con build_final_vector(gamma=0.4).
   Si URL sin queries → gamma=0.0 automático.

6. GSC API: paginar fetch_url_metrics() si rows > 25.000.
   Lag de ~3 días en GSC: ajustar end_date = today - 3 días.
   Distinguir entre propiedad sc-domain: y https:// en GSC_SITE_URL.

7. PageRank: construir grafo desde internal_links en PostgreSQL
   con nx.from_pandas_edgelist(). alpha=0.85 (estándar).

8. UMAP: aplicar PCA a 50 dims antes de UMAP si corpus > 200 URLs.
   n_neighbors adaptativo según get_umap_config(n_urls).
   random_state=42 siempre.

9. Clustering: HDBSCAN post-UMAP. min_cluster_size = max(5, n_urls // 50).
   cluster == -1 → gris en el mapa.

10. Canibalización: similitud coseno sobre vectores finales (contenido + intención).
    Optimizar con FAISS si n > 2.000. Umbral default: 0.92.

11. Logging: cada fase logea inicio, fin y conteo de URLs.
    Nivel INFO. Errores por URL aislados con try/except, no propagan.

12. Tests: pytest con fixtures en tests/conftest.py.
    Cubrir: normalize_url(), validate_content(), vectorize_page(),
    build_final_vector(), detect_cannibalization(), classify_rings().

9. Errores: nunca silenciar excepciones. Usar try/except solo para
   URLs individuales (aislar el fallo, continuar con el resto).

10. Tests: añadir test unitario para normalize_url(), 
    get_representative_chunk() y detect_cannibalization().
    Usar pytest con fixtures en tests/conftest.py.
```

---

## 11. Limitaciones conocidas y próximos pasos

| limitación | impacto | solución futura |
|---|---|---|
| Canibalización O(n²) | Lento en corpus > 2.000 URLs | Sustituir por búsqueda ANN con FAISS |
| Clasificación de intención heurística | Falsos positivos en queries ambiguos | Sustituir por clasificador LLM (GPT-4o-mini en batch) |
| GSC limita a 1.000 queries por URL | Sitios con URLs muy amplias pierden cobertura de intención | Agregar por clusters de queries con TF-IDF antes de embeber |
| fetch_all_queries es lento en corpus grandes | 5 rps × 10.000 URLs = ~33 min | Paralelizar con ThreadPoolExecutor (respetar cuotas GSC) |
| UMAP no determinista entre ejecuciones | Coordenadas cambian entre runs | `random_state=42` mitiga; pendiente evaluación de estabilidad |
| Sin soporte multilingüe explícito | Sitios con varios idiomas generan clusters artificiales por idioma | Separar corpus por hreflang en PostgreSQL antes de vectorizar |
| γ fijo por sitio, no por URL | Páginas con distintas coberturas de GSC merecen γ distintos | `gamma = min(0.4, n_queries / 100)` adaptativo por URL |
| Calidad del contenido normalizado depende del proceso upstream | Contenido mal limpiado en PostgreSQL contamina embeddings | Añadir validación de calidad: ratio boilerplate, densidad de entidades |
