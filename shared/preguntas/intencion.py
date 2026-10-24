"""Intencion de busqueda y etapa del embudo con Jev (#39, #169 puntos 1 y 2;
decision 176).

Tres preguntas, cada una con UN criterio (leccion de la decision 169: una
pregunta = un hallazgo):

- `intencion_pagina`: a que necesidad de busqueda sirve la pagina.
- `etapa_embudo`: en que etapa del recorrido del cliente es util. Aparte de la
  intencion, no mezclada: una comparativa es comercial y MOFU, pero una pagina
  de precios tambien es comercial y suele ser BOFU (#39).
- `intencion_busqueda`: que quiere quien escribe una busqueda real de Search
  Console. Las MISMAS opciones que `intencion_pagina`, para poder compararlas
  (el desajuste ponderado por clics, `shared/intencion.py`).

Las opciones se redactan como «quien llegaria aqui» (la redaccion con la que
jev-seo obtuvo 59 de 59 repeticiones iguales, comentario de #39), con un
desempate escrito donde dos opciones se tocan (reservar un hotel es
transaccional aunque el hotel este en un sitio; «hoteles en X» es comercial,
no local) y con lo que NO es cada una. `unclear` es la salida obligatoria de
#9: sin ella el modelo tiene que elegir aunque el estado no diga nada.

Sin evaluar (#8): sin juego etiquetado no hay umbral, todo sale `incierto` y
nada va a un informe. La incidencia `intencion_desalineada` esta preparada
pero apagada hasta que `intencion_pagina` e `intencion_busqueda` tengan umbral.
"""

from __future__ import annotations

from shared.preguntas import Pregunta

# Etiquetas comunes de la pagina y de la busqueda. El orden es el de la
# pantalla; no cambiar una etiqueta sin subir la version de las dos preguntas.
INTENCIONES = ("informational", "commercial", "transactional", "navigational", "local", "unclear")
ETAPAS = ("tofu", "mofu", "bofu", "post_purchase", "out_of_funnel", "unclear")

INTENCION_PAGINA = Pregunta(
    nombre="intencion_pagina",
    tipo="choice",
    version=1,
    instrucciones=(
        "Judging by this web page's address, title, main heading and the start of its main "
        "content, which search need is the page built to satisfy? Think of the person who would "
        "land here from a search engine and leave satisfied, and pick the page's MAIN purpose, "
        "not everything it mentions. Classify the page itself, not the searches it might rank for."
    ),
    opciones={
        "informational": (
            "Someone who wants to learn or find information: a guide, how-to, explanation, news, "
            "ideas, things to do, a recipe. A blog post that mentions a product in passing is still "
            "informational. NOT a page whose main purpose is selling or booking one specific "
            "product or service."),
        "commercial": (
            "Someone comparing options before deciding: comparisons, 'best of' lists, reviews, a "
            "listing or category page that helps choose between several products, services, "
            "hotels or plans. NOT the page of one specific product or service with a way to buy "
            "or book it (that is transactional)."),
        "transactional": (
            "Someone ready to act on one specific product, service or offer: its page with price, "
            "booking, purchase, quote request, sign-up or download. A hotel, room, ticket or "
            "product page with a booking or buy option is transactional even if it also describes "
            "the place. NOT a guide or a comparison of several options."),
        "navigational": (
            "Someone who already wants this specific brand or organisation and is looking for it "
            "or one of its sections: the home page, about us, corporate information, the brand's "
            "contact details, a brand landing page. NOT the page of one specific product or "
            "service that can be booked or bought, even if people find it by searching the brand."),
        "local": (
            "Someone who wants to find or get to a physical place: the page's main content is "
            "where a venue, shop or branch is, how to get there, opening hours, a map or a store "
            "locator. NOT a hotel, shop or service page whose main purpose is booking or buying, "
            "even if it names its location."),
        "unclear": (
            "The title, heading and content are too thin, generic or contradictory to tell. Do "
            "not use it just because two options are close: pick the main purpose."),
    },
    descripcion=("Intencion de la pagina (#39): informacional, comercial, transaccional, "
                 "navegacional, local o no esta claro. Sin evaluar (#8): todo incierto."),
)

ETAPA_EMBUDO = Pregunta(
    nombre="etapa_embudo",
    tipo="choice",
    version=1,
    instrucciones=(
        "Judging by this web page's address, title, main heading and the start of its main "
        "content, at which stage of a customer's journey is it most useful? Judge the stage of "
        "the reader the page is written for, not the topic alone: a guide about hotels is for "
        "someone who has not chosen yet, a hotel's booking page is for someone ready to book."
    ),
    opciones={
        "tofu": (
            "Top of the funnel: a reader with a need, question or curiosity who is not yet "
            "considering a specific provider or product: guides, inspiration, ideas, news, how-to, "
            "destination or topic information. NOT a page that pushes one specific offer."),
        "mofu": (
            "Middle of the funnel: a reader weighing options: comparisons, listings or category "
            "pages of several products or services, 'best of' lists, features, reviews, case "
            "studies, a page presenting a brand's range. NOT the page where they book or buy one "
            "specific item."),
        "bofu": (
            "Bottom of the funnel: a reader ready to decide on one specific product, service or "
            "offer: its page with price, booking, purchase, quote request, pricing plans or a "
            "special offer. NOT a general guide or a comparison."),
        "post_purchase": (
            "After the purchase: existing customers or guests: manage a booking, online check-in, "
            "instructions or FAQs for guests, support, user manuals, a loyalty programme members' "
            "area. NOT a page that tries to win a new customer."),
        "out_of_funnel": (
            "Not part of a purchase journey: legal and compliance texts, careers and job offers, "
            "press room, investor or corporate governance information, login, a generic contact "
            "form. NOT a page about a product, service or destination."),
        "unclear": "The title, heading and content are too thin or contradictory to tell.",
    },
    descripcion=("Etapa del embudo de la pagina (#39): TOFU, MOFU, BOFU, postventa o fuera del "
                 "embudo. Pregunta aparte de la intencion. Sin evaluar (#8): todo incierto."),
)

INTENCION_BUSQUEDA = Pregunta(
    nombre="intencion_busqueda",
    tipo="choice",
    version=1,
    instrucciones=(
        "'busqueda' is a real search someone typed into a search engine before visiting the "
        "website in 'sitio'; 'marcas' are that website's own brand names (it may be empty). "
        "Judging only by the words of the search, what does the person want? Classify the "
        "search, not the website. If the search names a specific brand, product or place AND "
        "has an action or price word (book, buy, price, offer, tickets), it is transactional."
    ),
    opciones={
        "informational": (
            "To learn or find information: how, what, why, ideas, things to do, weather, history, "
            "news. NOT a search to compare options, or to book or buy something."),
        "commercial": (
            "To compare options before choosing: 'best', 'top', 'vs', reviews, opinions, "
            "cheapest, or a kind of product or place without a specific one in mind ('family "
            "hotels in <island>', 'all inclusive resorts'). NOT a search that names one "
            "specific brand, product or place."),
        "transactional": (
            "To act now: book, buy, reserve, price, offer, discount, tickets, sign up, download "
            "('book hotel <town>', 'spa day price'), also with a brand or place name. NOT a "
            "search without any action or price word."),
        "navigational": (
            "To reach a specific brand, organisation, website or a section of it: the search "
            "names one of the 'marcas', or another specific company, hotel, product or website, "
            "with or without a section ('<brand>', '<hotel name> contact', '<brand> login'). NOT when "
            "it adds an action or price word (that is transactional)."),
        "local": (
            "To find or get to a physical place: address, directions, how to get to, opening "
            "hours, map, parking, 'near me'. NOT 'hotels in <town>' (that is choosing a place to "
            "stay: commercial)."),
        "unclear": ("The search is too short or ambiguous to tell: a single generic word, a code, "
                    "a misspelling with no clear meaning."),
    },
    descripcion=("Intencion de una busqueda real de Search Console (#169 punto 1), con las mismas "
                 "opciones que la de la pagina. Sin evaluar (#8): todo incierto."),
)

PREGUNTAS = [INTENCION_PAGINA, ETAPA_EMBUDO, INTENCION_BUSQUEDA]
