"""Soft 404 con Jev (#11, decision 163): la mitad con modelo de lo que la
version determinista (decision 105) deja como candidato.

La determinista afirma lo que se puede afirmar sin modelo —la pagina es la de
error del sitio (sonda), titulo de error, busqueda vacia, OutOfStock— y lo
dudoso lo marca `soft_404_candidato` con `candidato_llm`. Esta pregunta solo
se hace sobre esos candidatos: es el prefiltro barato del plan.

Sin umbral: no esta evaluada (#8), asi que todo sale `incierto` y nada de lo
que responda llega a un informe hasta que un juego etiquetado le de umbral.
"""

from __future__ import annotations

from shared.preguntas import Pregunta

ES_SOFT_404 = Pregunta(
    nombre="es_soft_404",
    tipo="noul",
    version=1,
    instrucciones=(
        "This web page answered with HTTP 200. Judging only by its title, main heading and the start "
        "of its main content, does it actually tell the visitor that what they wanted is not there: "
        "an error or 'page not found' message, a product or offer that no longer exists or is sold "
        "out with nothing else to offer, a search or listing with no results, or an empty "
        "placeholder ('coming soon', lorem ipsum)? A short page that still delivers real content "
        "(a contact form, a short news item, a legal notice) is NOT this."
    ),
    opciones={
        "true": "The page communicates an error, missing item, no results or an empty placeholder.",
        "false": "The page delivers real content, even if short.",
    },
    descripcion=("Soft 404 sobre los candidatos de la decision 105. Sin evaluar (#8): todo "
                 "incierto hasta que tenga umbral."),
)

PREGUNTAS = [ES_SOFT_404]
