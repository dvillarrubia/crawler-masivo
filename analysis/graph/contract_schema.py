"""Constraints e índices del contrato Seontology (§3) — idempotentes."""
from __future__ import annotations

CONSTRAINTS = [
    "CREATE CONSTRAINT page_pk IF NOT EXISTS FOR (p:Page) REQUIRE p.page_id IS UNIQUE",
    "CREATE CONSTRAINT entity_pk IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
    "CREATE CONSTRAINT query_pk IF NOT EXISTS FOR (q:Query) REQUIRE q.query_id IS UNIQUE",
    "CREATE CONSTRAINT cluster_pk IF NOT EXISTS FOR (c:Cluster) REQUIRE c.cluster_id IS UNIQUE",
    "CREATE INDEX page_community IF NOT EXISTS FOR (p:Page) ON (p.community_id)",
    "CREATE INDEX entity_qid IF NOT EXISTS FOR (e:Entity) ON (e.wikidata_qid)",
]

# Índice vectorial nativo para el centroide de página (768d, coseno).
# La propiedad p.embedding solo se poblará desde un origen 768d (contrato:
# derivado de pgvector, nunca al revés; jamás desde los 1024d del repo).
VECTOR_INDEX = (
    "CREATE VECTOR INDEX page_centroid IF NOT EXISTS "
    "FOR (p:Page) ON (p.embedding) "
    "OPTIONS { indexConfig: { `vector.dimensions`: 768, "
    "`vector.similarity_function`: 'cosine' } }"
)


def ensure_schema(driver) -> int:
    """Ejecuta constraints + índice vectorial. Devuelve nº de sentencias."""
    stmts = CONSTRAINTS + [VECTOR_INDEX]
    with driver.session() as s:
        for stmt in stmts:
            s.run(stmt).consume()
    return len(stmts)
