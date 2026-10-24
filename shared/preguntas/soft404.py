"""Soft 404 con Jev (#11, decision 163): la mitad con modelo de lo que la
version determinista (decision 105) deja como candidato.

La determinista afirma lo que se puede afirmar sin modelo —la pagina es la de
error del sitio (sonda), titulo de error, busqueda vacia, OutOfStock— y lo
dudoso lo marca `soft_404_candidato` con `candidato_llm`. Esta pregunta solo
se hace sobre esos candidatos: es el prefiltro barato del plan.

Medidas en la decision 169 y todavia sin umbral, asi que todo sale
`incierto` y nada llega a un informe: en `es_soft_404` v2 un umbral comun
solo decidiria los «no», y `es_pagina_de_utilidad` v2 esta sin medir.
"""

from __future__ import annotations

from shared.preguntas import Pregunta

ES_SOFT_404 = Pregunta(
    nombre="es_soft_404",
    tipo="noul",
    # v2 (decision 169): las paginas de utilidad (login, gracias, baja...)
    # salen de aqui y van a `es_pagina_de_utilidad`: funcionan como deben.
    version=2,
    # Medido el 24-oct-2026 con 94 candidatos (decision 169): 93,6 % de
    # acierto, pero sin umbral. El comun (0,90) decidiria 62 paginas, 60 de
    # ellas «no»: de los 29 soft 404 reales solo 2 saldrian decididos, y las
    # paginas de error evidentes las contesta «si» con 0,57-0,89.
    instrucciones=(
        "This web page answered with HTTP 200. Judging only by its title, main heading and the start "
        "of its main content, does it actually tell the visitor that what they wanted is not there: "
        "an error or 'page not found' message, a product or offer that no longer exists or is sold "
        "out with nothing else to offer, a search or listing with no results, or an empty "
        "placeholder ('coming soon', lorem ipsum, a template with no content filled in, a raw "
        "error trace)? A short page that still delivers real content (a contact form, a short "
        "news item, a legal notice) is NOT this, and neither is a page that works as intended "
        "for a step of a process (login, registration, thank-you or confirmation, unsubscribe, "
        "online check-in) unless it shows an error."
    ),
    opciones={
        "true": "The page communicates an error, missing item, no results or an empty placeholder.",
        "false": "The page delivers real content, even if short.",
    },
    descripcion=("Soft 404 sobre los candidatos de la decision 105. Medida en la decision 169, "
                 "sin umbral: todo incierto."),
)

ES_PAGINA_DE_UTILIDAD = Pregunta(
    nombre="es_pagina_de_utilidad",
    tipo="noul",
    # v2: en v1 Jev contaba las paginas de error como de utilidad (7 de sus 8
    # fallos). Una pagina de error es un soft 404, no un paso de un proceso.
    version=2,
    instrucciones=(
        "Judging by its address, title, main heading and the start of its main content, is this "
        "web page a functional step of a process rather than content anyone would look for in a "
        "search engine: login or account access, registration, password reset, thank-you or "
        "confirmation after a form, unsubscribe, online check-in, cart or checkout, or an "
        "internal test page? A page with real information a visitor could search for (a product, "
        "a service, an article, a contact page with company details, a legal text) is NOT this, and "
        "neither is a page that shows an error (not found, forbidden, server error, maintenance)."
    ),
    opciones={
        "true": "The page is a functional step (login, confirmation, unsubscribe, check-in...) with no search value.",
        "false": "The page offers information someone could search for, even if short.",
    },
    descripcion=("Pagina de utilidad sin valor de busqueda (login, gracias, baja, check-in), "
                 "sobre los mismos sospechosos que el soft 404 (decision 169). Sin umbral (#8)."),
)

PREGUNTAS = [ES_SOFT_404, ES_PAGINA_DE_UTILIDAD]
