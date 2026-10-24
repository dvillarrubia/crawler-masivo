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
jev-seo obtuvo 59 de 59 repeticiones iguales, comentario de #39), con lo que
NO es cada una y, en la pagina y en la busqueda, con REGLAS EN ORDEN («la
primera que encaje»): con definiciones sueltas, «day pass gran canaria» o una
ficha de hotel sin el widget de reserva en el texto encajaban en dos opciones
o en ninguna (revision de la decision 176). `unclear` es la salida obligatoria de
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
        "Judging by this web page's address ('ruta'), title, main heading, the start of its main "
        "content and the schema.org types it declares ('tipos_schema', may be empty), which search "
        "need is the page built to satisfy? Classify the page itself, not the searches it might "
        "rank for, and pick its MAIN purpose. 'contenido' is only the first few hundred characters "
        "of the main text: booking widgets, prices and buy buttons usually do NOT appear in it, so "
        "their absence is not evidence. Types such as Hotel, HotelRoom, Product, Service, Event or "
        "Offer mean the page presents something that can be booked or bought. Apply the first rule "
        "that fits: (1) the site's home page ('/' or a language root such as '/en/') is "
        "navigational; (2) a page about ONE specific product, service, hotel, room, treatment, "
        "ticket or offer that the site sells or books is transactional, even if its text only "
        "describes it; (3) a page that lists several products, services or hotels to choose from, "
        "including the site's own range ('our hotels in <place>', 'hotels with day pass'), is "
        "commercial; (4) a page whose main content is where a venue is or how to get there is "
        "local; (5) any other page about the organisation itself (about us, corporate, contact, "
        "careers, employee area, legal) is navigational; (6) guides, news, ideas and destination "
        "information are informational."
    ),
    opciones={
        "informational": (
            "Rule 6: someone who wants to learn or find information: a guide, how-to, explanation, "
            "news, ideas, things to do, information about a destination, a blog post (also when it "
            "mentions a product in passing). NOT a page that presents one specific thing to book or "
            "buy, or lists several of them to choose from."),
        "commercial": (
            "Rule 3: someone choosing between options: a listing or category page of several "
            "products, services or hotels (also when they are all the site's own), comparisons, "
            "'best of' lists, reviews. NOT the page of one specific product, service or hotel "
            "(that is transactional), and NOT a list of places to visit or things to do "
            "(informational)."),
        "transactional": (
            "Rule 2: someone ready to act on one specific product, service, hotel, room, "
            "treatment, ticket or offer: the page that presents it, whether or not the booking or "
            "buy option shows in the text you get. NOT a page listing several of them, and NOT the "
            "home page."),
        "navigational": (
            "Rules 1 and 5: someone who wants this brand or organisation itself: the home page, "
            "about us, corporate information, contact, careers, an employee or customer area, legal "
            "texts. NOT the page of one specific product, service or hotel, and NOT a page listing "
            "several of them, even if people reach them by searching the brand."),
        "local": (
            "Rule 4: someone who wants to find or get to a physical place: the main content is the "
            "address, directions, a map, opening hours or a store locator. NOT a hotel, venue or "
            "service page that merely names its location."),
        "unclear": (
            "The address, title, heading, content and types are too thin, generic or contradictory "
            "to apply any rule. Do not use it just because two options are close: apply the rules "
            "in order."),
    },
    descripcion=("Intencion de la pagina (#39): informacional, comercial, transaccional, "
                 "navegacional, local o no esta claro. Sin evaluar (#8): todo incierto."),
)

ETAPA_EMBUDO = Pregunta(
    nombre="etapa_embudo",
    tipo="choice",
    version=1,
    instrucciones=(
        "Judging by this web page's address ('ruta'), title, main heading, the start of its main "
        "content and the schema.org types it declares ('tipos_schema', may be empty), at which "
        "stage of a customer's journey is it most useful? Judge the stage of the reader the page "
        "is written for, not the topic alone: a guide about hotels is for someone who has not "
        "chosen yet, a hotel's own page is for someone ready to book. 'contenido' is only the "
        "first few hundred characters: booking widgets and prices usually do NOT appear in it, so "
        "their absence is not evidence; types such as Hotel, HotelRoom, Product, Service, Event or "
        "Offer mean the page presents something that can be booked or bought."
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
            "Bottom of the funnel: a reader ready to decide on one specific product, service, "
            "hotel, room or offer: the page that presents it (whether or not the booking or price "
            "shows in the text you get), pricing plans, a quote request or a special offer. NOT a "
            "general guide, a comparison or a listing of several options."),
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
        "search, not the website. The search can be in any language. Apply the FIRST rule that "
        "fits: (1) it has an action or price word (book, booking, reserve, buy, purchase, order, "
        "price, prices, cost, rates, cheap, offer, deal, discount, promo code, last minute, "
        "availability, sign up, download, or the same in another language) -> transactional; "
        "(2) it asks for the location of or access to a place (address, directions, how to get "
        "to, map, parking, opening hours, phone, near me) -> local; (3) it names one of the "
        "'marcas' or another specific company, hotel, venue, product or website, with or without "
        "a product or section ('<brand>', '<brand> day pass', '<hotel name> spa', '<brand> "
        "login') -> navigational; (4) it names a kind of product or service, with or without a "
        "place, or compares ('hotels <island>', 'day pass <island> all inclusive', 'best spa "
        "<town>', 'resort reviews') -> commercial; (5) it asks to learn something, or is only a "
        "place name ('<island>', '<beach>', '<town>') -> informational. A product or service "
        "name ('day pass', 'spa', 'all inclusive', 'tickets', 'room') is NOT an action word. If "
        "you cannot tell whether a name is a specific business or a place, treat it as a place."
    ),
    opciones={
        "informational": (
            "Rule 5: to learn something (how, what, why, ideas, things to do, weather, history, "
            "meaning of a word) or a place name on its own ('<island>', '<beach>'). NOT a search "
            "that names a kind of product or service ('hotels <island>' is commercial)."),
        "commercial": (
            "Rule 4: a kind of product or service, with or without a place, or a comparison, and "
            "no specific business: 'hotels <island>', 'day pass <island>', 'all inclusive "
            "resorts', 'best spa <town>', 'reviews', 'vs'. NOT a search with an action or price "
            "word (transactional) or that names a specific brand, hotel or venue (navigational)."),
        "transactional": (
            "Rule 1: it has an action or price word ('book hotel <town>', 'day pass price', "
            "'<brand> offers', 'reservar <hotel>'), with or without a brand or place. NOT a search "
            "whose only product word is a product or service name such as 'day pass' or "
            "'tickets'."),
        "navigational": (
            "Rule 3: it names one of the 'marcas' or another specific company, hotel, venue, "
            "product or website, alone or with a product or section ('<brand>', '<brand> day "
            "pass', '<hotel name> spa', '<brand> login'). NOT when it also has an action or price "
            "word (transactional) or asks how to get there (local)."),
        "local": (
            "Rule 2: to find or get to a physical place: address, directions, how to get to, map, "
            "parking, opening hours, phone, 'near me'. NOT 'hotels <town>' (commercial) nor a "
            "place name on its own (informational)."),
        "unclear": ("No rule fits: a code, a string with no meaning, a single generic word that is "
                    "not a place, product or brand. Do not use it when a rule fits."),
    },
    descripcion=("Intencion de una busqueda real de Search Console (#169 punto 1), con las mismas "
                 "opciones que la de la pagina. Sin evaluar (#8): todo incierto."),
)

PREGUNTAS = [INTENCION_PAGINA, ETAPA_EMBUDO, INTENCION_BUSQUEDA]
