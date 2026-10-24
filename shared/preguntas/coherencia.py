"""Coherencia del title y del marcado con el contenido (#16, #17; decision 177).

Tres preguntas, una por hallazgo (decision 169: no mezclar dos criterios):

- `title_describe_contenido` (score): si el title dice de que va el contenido.
  Solo el asunto; ni el estilo, ni la longitud, ni las palabras clave. v2
  (decision 180): el nivel alto es nombrar el item o la seccion que es la
  pagina aunque el title sea corto; los bajos, otro asunto, generico o engaño.
- `title_es_generico` (noul): si el title, quitada la marca, no identifica la
  pagina. Se juzga con el title, el dominio y la ruta, como en un resultado de
  busqueda (la ruta dice QUE es la pagina: «SAT» en /sats/a-coruna-100 frente a
  /sat/); si ademas describe o no el contenido es la pregunta anterior. v2
  (decision 182): un nombre propio o un tema concreto no es generico aunque
  sea corto o lo siga el nombre de la seccion; si lo es la etiqueta que tienen
  todos los items de una seccion sin decir cual. Los casos reales que caian
  entre las dos respuestas estan en `CASOS_FRONTERA`.
- `schema_contradice_contenido` (noul): si lo visible contradice un valor del
  marcado (precio, valoracion, fecha de un evento u oferta, disponibilidad).
  Solo la contradiccion: que un valor no aparezca en los fragmentos no prueba
  nada (se guarda el contenido principal, no la pagina entera). Ni el autor ni
  la fecha de publicacion: la firma no esta en el contenido guardado. v2
  (decision 184): si los fragmentos dan los precios del item y el declarado no
  es ninguno, eso no es ausencia sino contradiccion.

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
    # v2 (decision 180): el nivel alto es «nombra lo que la pagina es», aunque
    # el title sea corto y el fragmento hable de otra parte de la pagina. La v1
    # bajaba a 1 o 2 los titles de ficha de libro o de destino (78,5 %).
    version=2,
    instrucciones=(
        "You get a web page's title, its main heading and the start of its main content. First "
        "work out what the page is: which item it presents (a product, book, hotel, room, "
        "destination, programme, service, person, document) or which section or listing it is, "
        "or which topic an article covers. The heading can be a slogan, and the start of the "
        "content can be a menu, breadcrumbs, a product's details (publisher, price, 'secure "
        "payments'), the author's biography, a quote or the first item of a listing: those are "
        "parts of the page, not a different subject. Two exceptions: when the content is a list "
        "of documents, reports or files ABOUT the item the title names (each with 'View' or "
        "'Download' and a publication date), the page is that item's documents, not the item "
        "(level 2); and when the title names an event, offer or promotion that neither the "
        "heading nor the excerpt mention while they describe something else (a hotel, a "
        "company), the title promises what the page does not show (level 0). "
        "Then judge only whether the title names what the page "
        "is. A title that names that item, section or topic precisely gets the highest level even "
        "if it is short, a proper name, a question or in another language, and even if the "
        "heading and the excerpt talk about other parts of the page ('<book title> | <publisher>' "
        "on that book's page, 'Teachers | <school>' on the list of teachers, 'Hotels in <island>' "
        "on the page of the hotels there, 'News' on the news listing). "
        "Do not judge style, length, keywords, spelling or whether the brand is present. Keep the "
        "low levels for titles that are about something else, that would fit many pages, or that "
        "mislead."
    ),
    opciones=[
        "The title is about a different subject than the page, or misleads about it: it promises "
        "something the page does not have.",
        "The title is generic: only the brand or website name, or a label that would fit many "
        "pages of the site ('Home', 'Products', 'Details', a broad category), and does not name "
        "what this page is.",
        "The title names something related but not exactly this page: the parent item or "
        "section when the page is one specific part of it (its documents, its rooms, its reviews, "
        "one product of a range), or only one of several things the page presents equally.",
        "The title names the item, section, listing or topic this page is, even briefly, with a "
        "proper name or as a question.",
    ],
    # Lo que ensena la pantalla por nivel (no se envia: no entra en la huella).
    etiquetas=("otro asunto o engaña", "genérico", "algo relacionado", "nombra la página"),
    descripcion=("Si el title nombra lo que es la pagina (#16, v2 decision 180). Sin umbral: "
                 "todo incierto."),
)

TITLE_ES_GENERICO = Pregunta(
    nombre="title_es_generico",
    tipo="noul",
    # v2 (decision 182): un nombre propio o un tema concreto NO es generico
    # aunque el title sea corto o vaya seguido del nombre de la seccion, y la
    # marca se quita con cualquier grafia pero solo ella («<marca> <pais>» se
    # queda con el pais). Al otro lado, la etiqueta que tienen todos los items
    # de una seccion sin decir cual es generica, con una prueba explicita para
    # la frontera (¿valdria igual en la misma pagina de otro item de ese tipo?)
    # y el desempate «nombre» solo para la duda nombre propio o etiqueta. La v1
    # decia «si, generico» a 17 de 51 titles con nombre propio (cota de Wilson
    # 0,53) y «no» a 11 de 17 genericos del juego de 200. Los ejemplos son
    # inventados o con marcadores; un test los compara con las huellas de los
    # titles de los juegos (`tests/fixtures/titles_juegos_title_es_generico.txt`).
    version=2,
    instrucciones=(
        "You see a page title, the website's domain and the page's URL path, as a searcher would "
        "in a results page. Once the brand is set aside, does the title fail to tell this page "
        "apart from the site's other pages? Use the path only to tell what kind of page it is and "
        "which item it belongs to; do not judge whether the title matches the page's content. "
        "Set aside only the brand: the site or company name, usually the one in the domain. A word "
        "added to the brand is not the brand and counts ('<Brand> <country>', '<Brand> Kids'). "
        "Answer false when what remains names something specific: a proper name or a specific "
        "term that singles out one client, company, partner, project, product, book, course, "
        "place, country, city, person, event or app, even in one word, even if a stranger would "
        "not recognise it and even when the section name follows it ('<speaker name> | Speakers' "
        "on /speakers/<speaker>/, '<Brand> <country>' on the /<country code>/ version of the site, "
        "'<project name>' on the page of that project, a book or product name on its page); the "
        "one item the path points to described in common words ('Harbour extension' on the page "
        "of that construction project); a topic only this page has, in ordinary words ('Fishing "
        "licences', 'Coach parking'), also when the path is opaque (only ids or parameters), and "
        "also under a specific item when the topic is that item's own and not a label its sibling "
        "items repeat (a project or research line under an institute); the section itself when "
        "the page is that section and the path puts it under no specific item ('Lost property' "
        "on /lost-property/, a category on its category page); or, on a "
        "confirmation page, a title that names the action that was completed ('Subscription "
        "confirmed', 'Order placed'). "
        "A title that is too long, keyword-stuffed, badly written, in another language or about a "
        "different subject than the path is also false: those are other questions. "
        "Answer true only when what remains names nothing specific: "
        "(1) nothing at all, because the title is only the brand, in any spelling, capitalisation "
        "or spacing ('ACME', 'AcmeGroup'); "
        "(2) placeholder, default or error text (Untitled, New page, Welcome, Lorem ipsum, "
        "{{title}}, Access denied, Search results for), including a thank-you that names no "
        "action, on a confirmation page too; "
        "(3) an internal or technical name instead of a title (a template, block or file name such "
        "as 'card-grid-tpl' or 'hero_v3_final', a reference code such as REF-2231 or Node 45); "
        "(4) a vague label that names no subject (Details, Information, Overview, Article, "
        "Results, Category, Page 2); "
        "(5) when the path puts the page under ONE specific item of a kind the site has several "
        "of (one product, range, course, study level, institute, centre, magazine, hotel, city or "
        "year: a parent folder names that item) and the title does not name that item, a title "
        "that only names the general section or service those sibling pages share ('Hotels' on "
        "/hotels/<hotel name>, 'Clinics' on /clinics/<town>), or a label the "
        "matching page of any other item of that kind would carry word for word ('Exam dates' on "
        "/courses/<course>/exam-dates, 'Team' on /labs/<lab name>/team, 'Programme' on "
        "/festival/<year>/programme). The test for (5): would the same title fit, unchanged, the "
        "same page of another course, institute, magazine or year? If yes, true; if only this "
        "item could have it, false. "
        "When unsure whether an unfamiliar word is a proper name or a generic label, read it as a "
        "name; under a specific item, decide with the test for (5) instead."
    ),
    opciones={
        "true": ("Without the brand, the title names nothing specific: brand only, placeholder or "
                 "error text, an internal name or code, a vague label, or, on the page of one "
                 "specific item, only the shared section or a label every such item has."),
        "false": ("The title names something specific: a client, project, product, place, "
                  "country, person, a topic only this page has, or the section when the page is "
                  "that section."),
    },
    descripcion=("Title que no identifica la pagina, mas alla de la lista cerrada de la decision "
                 "121 (#16, v2 decision 182). Sin evaluar (#8): todo incierto."),
)

# Casos reales de produccion (24-oct-2026, solo lectura; juegos de la decision
# 182) que caian entre las dos respuestas, la que les toca con la v2 y la frase
# de las instrucciones que lo decide. Esa frase esta en la parte de su
# respuesta («Answer false ...» o «Answer true ...») y no en la otra (lo
# comprueba `tests/test_coherencia.py`); eso vigila que nadie la mueva, no que
# el significado de la otra parte no lo cubra tambien. No se envian: las
# instrucciones llevan ejemplos inventados o con marcadores, no estos (medir
# sobre el juego que los contiene lo inflaria, la leccion de la 169 y la 180).
CASOS_FRONTERA = [
    # Falsos positivos de la v1 en el juego de precision (confianza 0,50-0,73).
    ("Workoholics «Iberdrola | Work» en /eu/work/iberdrola/ (caso de un cliente)", "false",
     "even when the section name follows it"),
    ("Penguin «Penguin Libros Chile» en /cl/", "false", "'<Brand> <country>' on the /<country code>/"),
    ("Lopesan «Planta Fotovoltaica | Grupo Lopesan» en /corporativa/subvenciones/proyecto-planta-...",
     "false", "described in common words"),
    ("Salle «Servicios del Campus» en /es/grupos_campos/faq/62075", "false", "also when the path is opaque"),
    ("Workoholics «Work» en /work/", "false", "the path puts it under no specific item"),
    ("Saunier «Registration sent» en /solicitar-alta-en-area-privada/registration-sent-366273.html",
     "false", "names the action that was completed"),
    # Falsos negativos de la v1 en el juego de 200.
    ("Salle «Normativa» en /ca/estudis/enginyeria/normativa", "true", "would carry word for word"),
    ("Saunier «Notas de prensa» en /sobre-saunier-duval/noticias/2016/", "true",
     "only names the general section or service"),
    ("Saunier «Gracias» en /buscador-textos-prescripcion/gracias", "true", "a thank-you"),
    # Pares que la v1 (y la etiqueta de Claude) separaban sin criterio: misma
    # etiqueta de subseccion bajo un item concreto. La prueba de (5) los junta.
    ("Salle «Formació» en /innova-institute/formacio-innova-institute", "true",
     "would the same title fit, unchanged"),
    ("Salle «Training and Capacity Building» en /cfi-centre-funding-innovation/...", "true",
     "would the same title fit, unchanged"),
    ("Salle «Presentació» en /revista-comprendre/presentacio", "true",
     "the same page of another course, institute, magazine or year"),
    ("Salle «Redacció» en /revista-comprendre/redaccio y «Subscription» en "
     "/comprendre-magazine/subscription", "true", "would carry word for word"),
    ("Salle «Nirvana» en /innova-institute/nirvana (un proyecto del instituto)", "false",
     "the topic is that item's own"),
    ("Salle «Scholarships and grants» en /schools-families/study-la-salle/...", "false",
     "a topic only this page has"),
    ("Saunier «Muchas gracias» en /confirmacion/ y /formulario-enviado/", "true",
     "on a confirmation page too"),
    # Verdaderos positivos que la v2 tiene que conservar.
    ("Workoholics «WORKOHOLICS» en /legal/ y Penguin «PenguinLibros»", "true",
     "in any spelling, capitalisation"),
    ("Saunier «SAT» en /sats/bizkaia-26/", "true", "those sibling pages share"),
    ("Lopesan «headerpage» en /es/headerpage", "true", "an internal or technical name"),
    ("Saunier «401 – No autorizado»", "true", "placeholder, default or error text"),
]

SCHEMA_CONTRADICE_CONTENIDO = Pregunta(
    nombre="schema_contradice_contenido",
    tipo="noul",
    # v2 (decision 184): si los fragmentos dan los precios del item de la
    # pagina y el importe declarado no es ninguno de ellos, se contradice. La
    # v1 juntaba ese caso con «no aparece» (que no prueba nada) y se callaba
    # 15 de 19 contradicciones reales en los censos del 24-oct.
    version=2,
    instrucciones=(
        "A web page declares the values under 'marcado' in its structured data (schema.org markup, "
        "which visitors do not see). 'texto_visible' holds fragments of the text visitors do see on "
        "that page; they are partial. 'titulo' and 'h1' tell which item the page is about: the one "
        "product, room, course, venue or service it presents. Does the visible text clearly "
        "contradict at least one declared value for that item? "
        "Answer true when the fragments show that same item with a different value for the same "
        "thing. For a price: the markup declares one amount (in price, lowPrice or a priceRange "
        "written as a single amount) and the fragments show the item's own prices, but the declared "
        "amount is none of them; markup left out of date is exactly what this question looks for, "
        "so a declared amount that differs from every price the page gives for the item is a "
        "contradiction even when the difference is small or the field is priceRange. Also true for "
        "a different currency for the same item's price, a different rating or number of reviews, "
        "a different event date or offer validity date, or a different availability (the markup "
        "says InStock and the page says sold out). "
        "Answer false when the fragments give no price, rating, date or availability for that item "
        "at all (a declared value that is simply not mentioned proves nothing); when the declared "
        "amount equals any of the amounts the page gives for the item, in any role (base, per "
        "hour, per night, discounted, or a crossed-out old price); when the differing figure belongs "
        "to something else: another product, room, venue or branch listed on the same page, an "
        "add-on, a monthly instalment or deposit, a total for several items, nights or people, or "
        "a 'from' price for a whole range that is consistent with the declared one; or when only "
        "the format differs (116€ and 116,00 EUR, 2026-10-31 and 31 October 2026, 4.5 and 4,5/5)."
    ),
    opciones={
        "true": "A declared value is visibly contradicted for the page's own item (price, currency, rating, date or availability).",
        "false": "The values match, the differing figure belongs to something else, or the item's values are not shown.",
    },
    descripcion=("Marcado que contradice lo visible: precio, valoracion, fecha de un evento u "
                 "oferta o disponibilidad (#17, v2 decision 184). Sin evaluar (#8): todo incierto."),
)

PREGUNTAS = [TITLE_DESCRIBE_CONTENIDO, TITLE_ES_GENERICO, SCHEMA_CONTRADICE_CONTENIDO]
