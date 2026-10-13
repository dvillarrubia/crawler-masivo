"""Pregunta de ejemplo: fija la forma del catalogo, no es una pregunta de #9.

Se eligio una que no decide nada sobre el SEO de un cliente y cuyo estado es
minimo (titulo, h1 y el principio del contenido): sirve para la primera
llamada real de la verificacion de #7 sin que su respuesta acabe en un
informe. No tiene umbral porque no se ha evaluado (#8).
"""

from __future__ import annotations

from shared.preguntas import Pregunta

ES_PAGINA_LEGAL = Pregunta(
    nombre="es_pagina_legal",
    tipo="noul",
    version=1,
    instrucciones=(
        "Is this web page a legal or policy page (legal notice, privacy "
        "policy, cookie policy, terms and conditions) rather than regular "
        "site content?"
    ),
    opciones={
        "true": "The page's main purpose is a legal notice, privacy, cookie or terms text.",
        "false": "The page is regular content: product, article, service, listing or home.",
    },
    descripcion="Ejemplo de #7. Sin evaluar: no usar para decidir nada.",
    ejemplo=True,
)

PREGUNTAS = [ES_PAGINA_LEGAL]
