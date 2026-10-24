"""Canibalizacion juzgada sobre las busquedas repartidas (#18, #169 punto 3,
decision 175).

Entrada: los avisos `canibalizacion_por_consulta` de la decision 157 (una
busqueda de Search Console cuyos clics se reparten entre dos o mas URLs del
sitio), partidos en PARES de paginas del mismo idioma
(`shared/enriquecido_canibalizacion.py`). Esa regla dice QUE se reparte; no si
las paginas compiten de verdad ni que hacer. Son dos hallazgos distintos y por
eso dos preguntas (leccion de la decision 169: una pregunta, un criterio):

1. `paginas_sustitutas` (noul): ¿quien hace esta busqueda quedaria igual de
   servido con cualquiera de las dos? Es lo que separa la canibalizacion de un
   reparto legitimo (un listado y una de sus fichas, un hotel y su spa, la
   home y un hotel en una busqueda de marca).
2. `recomendacion_canibalizacion` (choice): fusionar, diferenciar,
   canonicalizar o dejar. **Solo se lee cuando la primera dice que si**
   (`solo_si`): su respuesta sobre un reparto legitimo no significa nada.

**Van en llamadas SEPARADAS** (la capa nunca junta una pregunta con `solo_si`
con otras, `CapaDecisiones._partir`): la recomendacion parte de que las
paginas compiten, y leida en la misma llamada empujaria a la primera hacia el
si. Asi tambien la evaluacion (`scripts/evaluar_decisiones.py responder`, una
pregunta por llamada) mide exactamente el prompt de produccion, y la cache de
una sirve para la otra. Cuesta un estado mas por par: en Lopesan, 0,03 $ por
ejecucion en vez de 0,02 $ (cifras en la decision 175).

Sin evaluar (#8) y sin umbral: todo sale `incierto` y nada va a un informe.
"""

from __future__ import annotations

from shared.preguntas import Pregunta

_CAMPOS = (
    "For each page you get its path, title, main heading, the start of its main content and its "
    "rounded Google figures for that search: 'parte_pct' is its share of the search's clicks, or of its "
    "impressions when 'reparto_por' is 'impresiones'; 'clics', 'impresiones' and 'posicion' (average "
    "position). 'no_indexable' is the reason the page is not indexable (empty if it is), and 'canonica' "
    "the other URL it already declares as its canonical (empty if none). 'otras_paginas' is how many other "
    "pages of the site also share this search; they are judged in their own pairs, so ignore them. "
)

PAGINAS_SUSTITUTAS = Pregunta(
    nombre="paginas_sustitutas",
    tipo="noul",
    version=1,
    instrucciones=(
        "Two pages of the same website, in the same language, both get Google clicks for the same search "
        "(field 'busqueda'). " + _CAMPOS +
        "Would a person typing this search be served equally well by either page, because both cover the "
        "same thing for that need? Answer true when the two pages are substitutes: two pages about the "
        "same hotel, product, service or topic, two articles on the same subject, an old and a new version "
        "of the same content, or two near-identical pages. Answer false when each page serves a different "
        "need that happens to match the search: a listing, destination or category page versus one single "
        "item of it; a main page versus a page about one specific aspect of it (its spa, its rooms, its "
        "prices, its location); an article versus a product or booking page; two pages about two different "
        "hotels or products. A search for the site's own name or brand (even misspelled, alone or with a "
        "generic word such as hotel, hotels or resort) looks for the site itself: the homepage or a "
        "language homepage (such as /en/ or /es/) versus the page of one single hotel, product or section "
        "is false. Judge only whether the two pages are substitutes, not which one is better or what to do."
    ),
    opciones={
        "true": "Substitutes: either page would satisfy this search equally well.",
        "false": "Different needs (scope, aspect, item or intent) that happen to match this search.",
    },
    descripcion=("Si las dos paginas que se reparten una busqueda son sustitutas para quien la hace "
                 "(canibalizacion real) o un reparto legitimo. Sin evaluar (#8): todo incierto."),
)

RECOMENDACION_CANIBALIZACION = Pregunta(
    nombre="recomendacion_canibalizacion",
    tipo="choice",
    version=1,
    instrucciones=(
        "Two pages of the same website, in the same language, compete for the same Google search (field "
        "'busqueda') and have been judged substitutes for it. " + _CAMPOS +
        "Which single action fits these two pages best? Choose 'fusionar' only when both say essentially "
        "the same thing and one of them is redundant. Choose 'diferenciar' when each page has its own "
        "reason to exist but both target this search: NOT when one page is a copy or a variant of the "
        "other. Choose 'canonicalizar' only when one page is a variant of the other that users still need "
        "(a campaign landing, a printable version, a filtered or sorted listing with its own path) and it "
        "does not declare a canonical yet: NOT for two pages with different content, and NOT for a page "
        "whose 'canonica' is already set. Choose 'dejar' only when the split does no harm because both "
        "pages already rank at positions 1 to 3 for this search."
    ),
    opciones={
        "fusionar": "Merge: keep the stronger page, move any unique content into it and redirect the other.",
        "diferenciar": ("Differentiate: refocus the title, heading and content of one page on a distinct "
                        "need and link between them."),
        "canonicalizar": "Canonicalise: the variant keeps existing for users and points its canonical to the main page.",
        "dejar": "Leave as is: both pages already rank at positions 1 to 3, so the split is harmless.",
    },
    descripcion=("Que hacer con dos paginas que se canibalizan: fusionar, diferenciar, canonicalizar o "
                 "dejar. Solo se lee si `paginas_sustitutas` dice que si. Sin evaluar (#8)."),
    solo_si=("paginas_sustitutas", "true"),
)

PREGUNTAS = [PAGINAS_SUSTITUTAS, RECOMENDACION_CANIBALIZACION]
