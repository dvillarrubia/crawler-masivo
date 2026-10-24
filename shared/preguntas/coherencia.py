"""Coherencia del title y del marcado con el contenido (#16, #17; decision 177).

Tres preguntas, una por hallazgo (decision 169: no mezclar dos criterios):

- `title_describe_contenido` (score): si el title dice de que va el contenido.
  Solo el asunto; ni el estilo, ni la longitud, ni las palabras clave.
- `title_es_generico` (noul): si el title, quitada la marca, no identifica la
  pagina. Se juzga con el title, el dominio y la ruta, como en un resultado de
  busqueda (la ruta dice QUE es la pagina: «SAT» en /sats/a-coruna-100 frente a
  /sat/); si ademas describe o no el contenido es la pregunta anterior.
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
        "You see a page title, the website's domain and the page's URL path, as a searcher would "
        "in a results page. Set the brand or site name aside. Does the rest of the title fail to "
        "name what this specific page is? Use the path only to tell what kind of page it is and "
        "which item it is about (a product, a book, a city, an article, a section); do not judge "
        "whether the title matches the page's content in detail. "
        "Answer true when the title is only the brand or domain; placeholder or default text (Home, "
        "Untitled, New page, Default, Welcome, Lorem ipsum, {{title}}); a reference code (REF-2231, "
        "P00123, Node 45); a label that would fit many pages of the site (Product, Details, News "
        "detail, Information, Results, Category, Article, Page 2); or when the path shows the page "
        "is about one specific item and the title only names the general section or service it "
        "belongs to ('SAT' on /sats/a-coruna-100, 'Hotels' on /hotels/costa-meloneras, 'News detail' "
        "on /news/2026/new-opening). "
        "Answer false when the title names the item or section the path points to, even in one or "
        "two words and even if it is a proper name a stranger would not recognise: a product or book "
        "name ('SafiraAqua' on /products/safiraaqua.html, 'Culpa' on /es/novela/123-libro-culpa), a "
        "place, a person, a topic, or the section itself when the page is that section ('SAT' on "
        "/sat/, 'Contact' on /contact, 'Hotel offers' on /offers/). If the path is opaque (only ids "
        "or parameters), judge the title alone, and a proper name or an acronym that could name the "
        "page is false. NOT generic either: a title that is too long, keyword-stuffed, badly written "
        "or in another language but names its subject, or one that names a concrete subject "
        "different from the path (that is a different question)."
    ),
    opciones={
        "true": "Without the brand, the title does not name this page: brand only, placeholder, code, a label that fits many pages, or only the section of a page about one specific item.",
        "false": "The title names the item or section this page is, even briefly or with a proper name.",
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
