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
    # v2 (decision 169): el criterio de la revision humana del juego de #8.
    # Cuenta tambien lo que existe por obligacion (cumplimiento, ESG,
    # subvenciones, gobierno corporativo, alertas de producto), no solo los
    # cuatro textos legales de siempre.
    version=2,
    # Medido el 24-oct-2026 con 300 ejemplos de Lopesan y Saunier Duval
    # (decision 169): 99 % de acierto, y 0,55 cubriria el 100 % con cota de
    # Wilson 0,971. No se pone: es de ejemplo y no decide nada.
    instrucciones=(
        "Is this web page a legal, regulatory or compliance page rather than "
        "regular site content? Count the classic legal texts (legal notice, "
        "privacy policy, cookie policy, terms and conditions) and also pages "
        "that exist to meet an obligation: compliance and ethics (criminal "
        "risk prevention, code of conduct, whistleblowing channel), ESG or "
        "sustainability policies, notices of public grants or subsidies "
        "received, corporate governance or legal information for "
        "shareholders, and product safety alerts or regulatory notices."
    ),
    opciones={
        "true": ("The page's main purpose is a legal, regulatory or compliance text, "
                 "including ESG policies, grant notices and governance information."),
        "false": "The page is regular content: product, article, service, listing or home.",
    },
    descripcion=("Ejemplo de #7, ampliada en la decision 169 a lo normativo y de "
                 "cumplimiento. Sin umbral: no usar para decidir nada."),
    ejemplo=True,
)

PREGUNTAS = [ES_PAGINA_LEGAL]
