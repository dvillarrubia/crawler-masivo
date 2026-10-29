"""Borrar las filas de un censo, entero o por trozos (decisiones 96 y 223).

Lo comparten el borrado de un job de la API (`api/routers/jobs.py`, que
ademas borra la fila de `jobs` y sus ficheros) y la retencion por perfil del
worker (`shared/retencion.py`, que vacia el censo y conserva su fila y su
punto de evolucion). El worker no lleva `api/`: por eso vive aqui.
"""

from __future__ import annotations

import time
from typing import Callable


def borrar_filas_del_job(db, job_id, filas: dict, *, trozo: int | None = None,
                         hasta: float | None = None,
                         reloj: Callable[[], float] = time.monotonic,
                         conservar_evolucion: bool = False) -> bool:
    """Borra todo lo que cuelga del job salvo su fila de `jobs`. True si ha
    terminado; False si se agoto el tiempo (`hasta`, en el `reloj` dado) y hay
    que volver. Apunta en `filas` cuantas borro de cada tabla.

    Sin `trozo`, un DELETE por tabla y sin commit (lo hace quien llama). Con
    `trozo`, las tablas grandes por lotes de ese tamano con un commit por lote.
    """
    from shared.models import (CleaningRuleset, Decision, EnrichmentRun, Issue, JobFrontera,
                               Link, ProfileRun, RobotsTxt, SitemapArrastre, Url)

    # Analisis enriquecido (#40) y reglas de limpieza: a mano, como links e
    # issues (en SQLite no hay ON DELETE CASCADE). Una ejecucion de
    # enriquecido en curso se para sola: en el siguiente lote su fila ya no
    # existe y el worker deja de llamar.
    #
    # Lo del analisis semantico y Search Console NO se borra a mano, y es a
    # proposito: `semantic_analyses` cuelga del job y el resto de las URLs,
    # los cuatro con ON DELETE CASCADE. Importar aqui `shared.semantic_models`
    # registraria sus tablas de pgvector en el `MetaData` compartido, y
    # entonces el `create_all` de los tests contra un Postgres SIN la
    # extension (el `postgres:16-alpine` de CI) revienta — se llevaria por
    # delante `test_pagerank_db.py`, que no tiene nada que ver con esto.
    # El robots.txt de cada host (decisiones 112 y 113): unas pocas filas.
    pequenas = [("decisions", Decision), ("enrichment_runs", EnrichmentRun),
                ("cleaning_rulesets", CleaningRuleset), ("robots_txt", RobotsTxt),
                # Rastreo incremental (decision 220).
                ("job_frontera", JobFrontera), ("sitemap_arrastre", SitemapArrastre)]
    if not conservar_evolucion:
        # La fila de su evolucion (decision 130): una por job. Vaciar un censo
        # por retencion la conserva (decision 223).
        pequenas.append(("profile_runs", ProfileRun))
    for nombre, modelo in pequenas:
        filas[nombre] = db.query(modelo).filter(modelo.job_id == job_id).delete(
            synchronize_session=False)

    # Las grandes. `urls` arrastra html_meta, headings, page_content y
    # demas por el ON DELETE CASCADE de la BD. `custom_extraction` (decision
    # 104) lleva `job_id` y va a mano: en SQLite no hay CASCADE, y por trozos
    # como las otras, porque son una fila por URL y regla.
    from shared.models import (AdapterEntity, CapturaAlternativa, CustomExtraction,
                               EnlaceNoRastreable, RespuestaSondeada, SitemapEntry)

    for nombre, modelo in (("links", Link), ("issues", Issue),
                           ("custom_extraction", CustomExtraction),
                           # Enlaces no rastreables (decision 106): job_id, igual.
                           ("enlaces_no_rastreables", EnlaceNoRastreable),
                           # Capturas con otro perfil (decision 119): job_id, igual.
                           ("capturas_alternativas", CapturaAlternativa),

                           # Sondas de respuesta (decisiones 123 y 124): job_id, igual.
                           ("respuestas_sondeadas", RespuestaSondeada),
                           # Entradas de sitemap (decision 126): job_id, igual.
                           ("sitemap_entries", SitemapEntry),
                           # Entidades de la REST API de WordPress (decision 161).
                           ("adapter_entities", AdapterEntity), ("urls", Url)):
        borradas = 0
        while True:
            if trozo:
                sub = (db.query(modelo.id).filter(modelo.job_id == job_id)
                       .limit(trozo).scalar_subquery())
                n = db.query(modelo).filter(modelo.id.in_(sub)).delete(
                    synchronize_session=False)
            else:
                n = db.query(modelo).filter(modelo.job_id == job_id).delete(
                    synchronize_session=False)
            borradas += n
            if not trozo:
                break
            db.commit()
            if n < trozo:
                break
            if hasta is not None and reloj() >= hasta:
                filas[nombre] = borradas
                return False
        filas[nombre] = borradas
    return True
