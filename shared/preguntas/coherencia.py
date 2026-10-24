"""Coherencia del title y del marcado con el contenido (#16, #17; decision 177).

Tres preguntas, una por hallazgo (decision 169: no mezclar dos criterios):

- `title_describe_contenido` (score): si el title dice de que va el contenido.
  Solo el asunto; ni el estilo, ni la longitud, ni las palabras clave.
- `title_es_generico` (noul): si el title, quitada la marca, no identifica la
  pagina. Se juzga leyendo SOLO el title, como en un resultado de busqueda; si
  ademas describe o no el contenido es la pregunta anterior.
- `schema_contradice_contenido` (noul): si lo visible contradice un valor del
  marcado (precio, valoracion, fecha de un evento u oferta, disponibilidad).
  Solo la contradiccion: que un valor no aparezca en los fragmentos no prueba
  nada (se guarda el contenido principal, no la pagina entera). Ni el autor ni
  la fecha de publicacion: la firma no esta en el contenido guardado.

Ninguna esta evaluada (#8): sin umbral, todo sale `incierto` y nada va a un
informe. Las incidencias de #16/#17 que saldrian de ellas (`title_desalineado`,
`title_generico_jev`, `schema_valor_contradicho`) no existen todavia: se emiten
el dia que la pregunta tenga umbral, no antes.
"""

from __future__ import annotations

from shared.preguntas import Pregunta

TITLE_DESCRIBE_CONTENIDO = Pregunta(
    nombre="title_describe_contenido",
    tipo="score",
    version=1,
    instrucciones=(
        "Compare this web page's title with its main heading and the start of its main content. "
        "How well does the title state what the content of this page is mainly about? Judge only "
        "whether the subject matches: do not judge style, length, keywords, spelling or whether the "
        "brand name is present, and do not penalise a short title that names the right subject. If "
        "the content is a listing, a category or a hub, a title that names that section is a precise "
        "match. A title that only names the website, the brand or a broad category while the content "
        "is about one specific product, place, service or topic is NOT a precise match."
    ),
    opciones=[
        "The title is about a different subject than the content, or promises something the content does not deliver.",
        "The title shares only a broad theme with the content (the site, the brand or a wide category), not what this page is about.",
        "The title names the right subject but leaves out or distorts what the page mainly offers.",
        "The title states precisely the main subject of the content.",
    ],
    # Lo que ensena la pantalla por nivel (no se envia: no entra en la huella).
    etiquetas=("otro asunto", "solo el tema general", "parcial", "describe el contenido"),
    descripcion=("Si el title dice de que va el contenido (#16). Sin evaluar (#8): todo incierto."),
)

TITLE_ES_GENERICO = Pregunta(
    nombre="title_es_generico",
    tipo="noul",
    version=1,
    instrucciones=(
        "Read only this page title and the website's domain, as a searcher would see them in a "
        "results page. Once the brand or site name is set aside, does the title fail to identify "
        "what this specific page is about? Answer true when nothing that names a subject is left: "
        "the title is only the brand or domain, a placeholder or default text (Home, Untitled, New "
        "page, Default, Welcome, Lorem ipsum, {{title}}), a code or acronym that means nothing to a "
        "searcher, or a label so general it would fit many different pages of the site (Product, "
        "Details, News detail, Information, Results, Category, Article, Page 2). Answer false when "
        "the title names a concrete subject, even in one or two words: a product, a book, a place, "
        "a person, a service, a topic, or a clearly identified section or function of the site "
        "(Contact, Privacy policy, Hotel offers, Careers). A title that is too long, keyword-stuffed, "
        "badly written or in another language but names its subject is NOT generic."
    ),
    opciones={
        "true": "Without the brand, the title does not say what the page is about (brand only, placeholder or a label that fits many pages).",
        "false": "The title names a concrete subject or a clearly identified section, even briefly.",
    },
    descripcion=("Title que no identifica la pagina, mas alla de la lista cerrada de la decision "
                 "121 (#16). Sin evaluar (#8): todo incierto."),
)

SCHEMA_CONTRADICE_CONTENIDO = Pregunta(
    nombre="schema_contradice_contenido",
    tipo="noul",
    version=1,
    instrucciones=(
        "A web page declares the values under 'marcado' in its structured data (schema.org markup, "
        "which visitors do not see). 'texto_visible' holds fragments of the text visitors do see on "
        "that page; they are partial. Does the visible text clearly contradict at least one declared "
        "value? Answer true only when the fragments show a different value for the same thing: a "
        "different price or currency for the same product, room or offer, a different rating or "
        "number of reviews, a different event date or offer validity date, or a different "
        "availability (the markup says InStock and the page says sold out). Answer false when the "
        "visible values match; when a declared value is simply not mentioned in the fragments "
        "(absence proves nothing); when the visible figure clearly refers to something else (another "
        "room or product, a crossed-out old price, a 'from' price consistent with the declared one, "
        "a total for several nights or people); or when only the format differs (116€ and 116,00 EUR, "
        "2026-10-31 and 31 October 2026, 4.5 and 4,5/5)."
    ),
    opciones={
        "true": "A declared value is visibly contradicted for the same thing (price, rating, date or availability).",
        "false": "The visible values match the markup, refer to something else, or the declared values are not shown.",
    },
    descripcion=("Marcado que contradice lo visible: precio, valoracion, fecha de un evento u "
                 "oferta o disponibilidad (#17). Sin evaluar (#8): todo incierto."),
)

PREGUNTAS = [TITLE_DESCRIBE_CONTENIDO, TITLE_ES_GENERICO, SCHEMA_CONTRADICE_CONTENIDO]
