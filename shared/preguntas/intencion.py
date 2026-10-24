"""Intencion de busqueda y etapa del embudo con Jev (#39, #169 puntos 1 y 2;
decision 176).

Tres preguntas, cada una con UN criterio (leccion de la decision 169: una
pregunta = un hallazgo):

- `intencion_pagina`: a que necesidad de busqueda sirve la pagina.
- `etapa_embudo`: en que etapa del recorrido del cliente es util. Aparte de la
  intencion, no mezclada: una comparativa es comercial y MOFU, pero una pagina
  de precios tambien es comercial y suele ser BOFU (#39).
- `intencion_busqueda`: que quiere quien escribe una busqueda real de Search
  Console. Las opciones de `intencion_pagina` salvo las dos que solo tiene la
  pagina (`SOLO_PAGINA`, decision 180), para poder compararlas (el desajuste
  ponderado por clics, `shared/intencion.py`).

Las opciones se redactan como «quien llegaria aqui» (la redaccion con la que
jev-seo obtuvo 59 de 59 repeticiones iguales, comentario de #39), con lo que
NO es cada una y, en la pagina y en la busqueda, con REGLAS EN ORDEN («la
primera que encaje»): con definiciones sueltas, «day pass gran canaria» o una
ficha de hotel sin el widget de reserva en el texto encajaban en dos opciones
o en ninguna (revision de la decision 176). `unclear` es la salida obligatoria de
#9: sin ella el modelo tiene que elegir aunque el estado no diga nada.

`intencion_pagina` v2 (decision 180): medida la v1 contra la etiqueta (76,5 %),
el fallo era `navigational`, que juntaba la portada, lo corporativo y las
paginas para quien ya es cliente o alumno. La v2 deja `navigational` solo para
la portada y separa `existing_users` y `corporate`.

Sin umbral (#8): sin juego etiquetado con su criterio no hay umbral, todo sale `incierto` y
nada va a un informe. La incidencia `intencion_desalineada` esta preparada
pero apagada hasta que `intencion_pagina` e `intencion_busqueda` tengan umbral.
"""

from __future__ import annotations

from shared.preguntas import Pregunta

# Etiquetas comunes de la pagina y de la busqueda. El orden es el de la
# pantalla; no cambiar una etiqueta sin subir la version de las dos preguntas.
INTENCIONES = ("informational", "commercial", "transactional", "navigational", "local", "unclear")
# La pagina tiene dos mas (decision 180): una busqueda no es «de usuarios ya
# existentes» ni «corporativa» (se reparten entre navegacional e
# informacional), una pagina si. Con ellas el desajuste no se afirma
# (`shared.intencion.SOLO_PAGINA`).
SOLO_PAGINA = ("existing_users", "corporate")
INTENCIONES_PAGINA = INTENCIONES[:-1] + SOLO_PAGINA + ("unclear",)
ETAPAS = ("tofu", "mofu", "bofu", "post_purchase", "out_of_funnel", "unclear")

INTENCION_PAGINA = Pregunta(
    nombre="intencion_pagina",
    tipo="choice",
    # v2 (decision 180): `navigational` solo para la portada y dos opciones
    # que la busqueda no tiene, `existing_users` y `corporate`. La v1 metia en
    # `navigational` todo lo «de la organizacion» y Jev solo acertaba 50 de 88.
    version=2,
    instrucciones=(
        "Judging by this web page's address ('ruta'), title, main heading, the start of its main "
        "content and the schema.org types it declares ('tipos_schema', may be empty), who is the "
        "page built for and what do they want from it? Classify the page itself, not the searches "
        "it might rank for, and pick its MAIN purpose. 'contenido' is only the first few hundred "
        "characters of the main text and may start with a menu or breadcrumbs: booking widgets, "
        "prices and buy buttons usually do NOT appear in it, so their absence is not evidence. "
        "Types such as Hotel, HotelRoom, Product, Service, Event or Offer mean the page presents "
        "something that can be booked or bought. Apply the FIRST rule that fits: "
        "(1) the site's home page or a language or country root ('/', '/en/', '/mx/') is "
        "navigational, and nothing else is; "
        "(2) a page for people who ALREADY are customers, students, guests, members or employees "
        "of the organisation, or who have just sent a form, so they can use a service or do "
        "something there, is existing_users: log in, private or customer area, manage a booking, "
        "warranty or support requests, manuals, technical documentation or downloads for owners "
        "or installers, academic regulations, timetables and calendars, library, tutoring, "
        "mentoring or career services for enrolled students, student clubs and associations, the "
        "alumni community, exchanges for enrolled students, a thank-you or confirmation page "
        "(signing up for the first time is rule 3); "
        "(3) a page about ONE specific product, service, hotel, villa, room, programme, course, "
        "ticket, event, information session, visit or offer that the site sells, books or lets "
        "outsiders sign up for or request, is transactional, even if its text only describes it; "
        "this includes 'why choose our <one programme>' and signing up for the first time to a "
        "membership, loyalty programme, private area or subscription; "
        "(4) a page that lists or presents several products, services, programmes or hotels to "
        "choose from (also the site's own range, 'our hotels in <place>'), a selection of several "
        "products for sale written as an article ('7 books about X'), a page arguing why to choose "
        "the organisation for a broad area ('why study at <university>', 'why study management at "
        "<university>'), or case studies and a portfolio, is commercial; "
        "(5) a page whose main content is where a venue is, how to get there, its opening hours, "
        "or a locator of branches, shops or technicians, is local; "
        "(6) any other page about the organisation itself rather than about what it sells is "
        "corporate: about us, values, history, governance, sustainability, compliance and product "
        "safety notices, quality-assurance or accreditation documents, grants it received, legal "
        "texts (privacy, cookies, terms), careers, press room, social media links, a general "
        "contact page, its staff directory or a staff profile, its schools, institutes, research "
        "groups and research projects, and its agreements with other institutions; "
        "(7) guides, how-to, news, articles, explanations, FAQs, destination information, and "
        "explanations of how admission, enrolment, scholarships or financing work for newcomers, "
        "are informational."
    ),
    opciones={
        "informational": (
            "Rule 7: someone who wants to learn something: guides, news, articles, FAQs, "
            "destinations, how admission or financing work. NOT a selection of products for sale "
            "(commercial), NOT the organisation's own research or quality documents (corporate), "
            "NOT regulations or timetables for people already enrolled (existing_users)."),
        "commercial": (
            "Rule 4: someone choosing between several products, services, programmes or hotels, "
            "including the site's own range, product selections written as articles, 'why choose "
            "us' for a broad area, case studies. NOT the page of one specific item "
            "(transactional), NOT places to visit (informational), NOT services for existing "
            "students or customers (existing_users)."),
        "transactional": (
            "Rule 3: someone ready to act on one specific product, service, hotel, room, programme, "
            "course, event, session, visit or offer, or signing up for the first time, whether or "
            "not the booking or price shows in the text. NOT a page listing several (commercial), "
            "NOT using an account someone already has (existing_users)."),
        "navigational": (
            "Rule 1: only the home page or a language or country root. NOT any other page about "
            "the organisation (corporate) or for its existing customers or students "
            "(existing_users), even if people reach it by searching the brand."),
        "local": (
            "Rule 5: someone who wants to find or get to a physical place: address, directions, "
            "map, opening hours, a locator. NOT a page that merely names its location, NOT a "
            "general contact page (corporate)."),
        "existing_users": (
            "Rule 2: someone who already is a customer, student, guest, member or employee, or has "
            "just sent a form, and comes to use a service: login, private area, support, manuals, "
            "regulations, timetables, library, student services, a confirmation page. NOT a page "
            "that tries to win a newcomer (transactional or commercial), NOT legal or corporate "
            "information (corporate)."),
        "corporate": (
            "Rule 6: someone who wants to know about the organisation itself: about us, "
            "governance, sustainability, compliance, quality documents, legal texts, careers, "
            "press, contact, staff, its institutes and research. NOT the home page (navigational), "
            "NOT services for existing users (existing_users), NOT what it sells (transactional or "
            "commercial)."),
        "unclear": (
            "Too thin, generic or contradictory to apply any rule (for example, the content is "
            "only the site menu). Do not use it just because two options are close."),
    },
    descripcion=("Intencion de la pagina (#39, v2 decision 180): informacional, comercial, "
                 "transaccional, navegacional (solo la portada), local, usuarios existentes, "
                 "corporativa o no esta claro. Sin umbral: todo incierto."),
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
