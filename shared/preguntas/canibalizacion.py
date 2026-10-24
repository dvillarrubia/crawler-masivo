"""Canibalizacion juzgada sobre las busquedas repartidas (#18, #169 punto 3,
decision 175).

Entrada: los avisos `canibalizacion_por_consulta` de la decision 157 (una
busqueda de Search Console cuyos clics se reparten entre dos o mas URLs del
sitio). Esa regla dice QUE se reparte; no si las paginas compiten de verdad ni
que hacer. Son dos hallazgos distintos y por eso dos preguntas (leccion de la
decision 169: una pregunta, un criterio):

1. `paginas_sustitutas` (noul): ¿quien hace esta busqueda quedaria igual de
   servido con cualquiera de las paginas? Es lo que separa la canibalizacion
   de un reparto legitimo (un listado y una de sus fichas, un hotel y su spa).
2. `recomendacion_canibalizacion` (choice): fusionar, diferenciar,
   canonicalizar o dejar. **Solo se lee cuando la primera dice que si**
   (`solo_si`): su instruccion da por hecho que las paginas compiten, y su
   respuesta sobre un reparto legitimo no significa nada.

Las dos van en la MISMA llamada (decision 84: las preguntas de un estado se
pagan juntas), no la segunda despues de la primera. Medido en Lopesan
(300 grupos): 1.361 tokens por llamada con las dos, 1.000 con solo la
primera y 1.045 con solo la segunda (el estado se paga otra vez). Preguntar
aparte solo sale mas barato si menos del 35 % de los grupos son sustitutas,
y aun entonces ahorra como mucho medio centimo por ejecucion; a cambio, la
estimacion seria una cota y no una cifra, y la ejecucion necesitaria una
segunda pasada.

Sin evaluar (#8) y sin umbral: todo sale `incierto` y nada va a un informe.
"""

from __future__ import annotations

from shared.preguntas import Pregunta

PAGINAS_SUSTITUTAS = Pregunta(
    nombre="paginas_sustitutas",
    tipo="noul",
    version=1,
    instrucciones=(
        "Several pages of the same website get Google clicks for the same search (field "
        "'busqueda'). For each page you get its path, title, main heading, the start of its "
        "main content and its rounded Google figures for that search (share of clicks, clicks, "
        "impressions, average position). Would a person typing this search be served equally "
        "well by any of these pages, because they cover the same thing for that need? Answer "
        "true when the pages are substitutes: two pages about the same hotel, product, service "
        "or topic, an old and a new version of the same content, or two near-identical pages. "
        "Answer false when each page serves a different need that happens to match the search: "
        "a listing or destination page versus one single item of it, a main page versus a page "
        "about one specific aspect (its spa, its rooms, its prices, its location), an article "
        "versus a product or booking page, or pages for different countries or audiences. "
        "Judge only whether they are substitutes, not which page is better or what to do."
    ),
    opciones={
        "true": "The pages are substitutes: either one would satisfy this search equally well.",
        "false": "The pages serve different needs (scope, aspect, intent or audience) for this search.",
    },
    descripcion=("Si las paginas que se reparten una busqueda son sustitutas para quien la hace "
                 "(canibalizacion real) o un reparto legitimo. Sin evaluar (#8): todo incierto."),
)

RECOMENDACION_CANIBALIZACION = Pregunta(
    nombre="recomendacion_canibalizacion",
    tipo="choice",
    version=1,
    instrucciones=(
        "Several pages of the same website compete for the same Google search (field "
        "'busqueda') and are substitutes for it: take that as given. For each page you get its "
        "path, title, main heading, the start of its main content and its rounded Google figures "
        "for that search. Which single action fits best? Choose 'fusionar' only when the pages "
        "say essentially the same thing and one of them is redundant. Choose 'diferenciar' when "
        "each page has its own reason to exist but both target this search: NOT when one page "
        "is a copy or a variant of the other. Choose 'canonicalizar' only when one page is a "
        "variant of the other that users still need (filtered, sorted, printable, campaign or "
        "parameter version): NOT for two pages with different content. Choose 'dejar' when the "
        "split does no harm: both pages already hold the top positions together, or the search "
        "is a misspelled or bare brand name where any page of the site works."
    ),
    opciones={
        "fusionar": "Merge: keep the stronger page, move any unique content into it and redirect the other.",
        "diferenciar": ("Differentiate: refocus the title, heading and content of one page on a distinct "
                        "need and link between them."),
        "canonicalizar": "Canonicalise: the variant keeps existing for users and points its canonical to the main page.",
        "dejar": "Leave as is: the split is harmless (both on top, or a bare or misspelled brand search).",
    },
    descripcion=("Que hacer con una canibalizacion: fusionar, diferenciar, canonicalizar o dejar. "
                 "Solo se lee si `paginas_sustitutas` dice que si. Sin evaluar (#8)."),
    solo_si=("paginas_sustitutas", "true"),
)

PREGUNTAS = [PAGINAS_SUSTITUTAS, RECOMENDACION_CANIBALIZACION]
