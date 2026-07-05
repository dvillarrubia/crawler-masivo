"""Lado Neo4j del contrato Seontology (grafo = estructura y semántica).

Reparto ESTRICTO (principio rector del contrato): el grafo guarda nodos,
relaciones y propiedades mínimas de navegación; el texto, los chunks,
los embeddings 1024d y las series de métricas viven SOLO en Postgres.
El grafo es derivado: Postgres primero, Neo4j después, siempre.

- contract_schema  constraints + índice vectorial (§3, idempotente)
- collect          colectores puros PG → filas (testeables sin Neo4j)
- sync             CLI: upsert MERGE por job (pasos 7-9) + prune + GDS
- tools            la frontera del agente (§7): firmas fijas
"""
