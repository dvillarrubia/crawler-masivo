"""Capa de extracción de entidades con GLiNER2 (POC entidad-query).

Ver `v2 experimental/INVESTIGACION.md` (fase 0 aprobada) y
`v2 experimental/brief_gliner2_crawler.md`. Estructura:

- schema_config  el schema.yaml por cliente (parse + validación)
- extraction     núcleo puro: chunking, normalización, agregación de spans
- gliner_adapter adaptador del modelo GLiNER2 local (CPU)
- pipeline       01/02: extraer páginas y queries → tablas gliner_*
- resolve        03: gate de resolución a catálogo (coseno / zona gris LLM)
- report         04: joins deterministas → issues + Excel/JSON
- gold_set       00: muestreo para anotación manual + evaluación F1
- run            CLI orquestador por job_id
"""
