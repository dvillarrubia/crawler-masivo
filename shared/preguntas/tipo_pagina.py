"""Tipo de pagina y categoria del cliente (#12, decision 178).

Dos preguntas `choice`, sobre una MUESTRA por plantilla (no sobre cada URL):
lo que se paga es por plantilla, y la respuesta de sus muestras se propone
para la plantilla entera con su acuerdo y su confianza
(`shared/tipo_pagina.propuesta_por_plantilla`).

- `tipo_pagina`: una taxonomia general para SEO, la misma en todos los
  clientes, porque de ella cuelgan los umbrales por tipo (#12), la relevancia
  de cada incidencia (#22) y que la poda (#198) no pregunte por paginas legales
  o de contacto. Once opciones, como la taxonomia probada de jev-seo, y cada
  una con lo que es, ejemplos y lo que NO es (en jev-seo las opciones asi
  subieron el acuerdo de 20 a 27 sobre 30). `legal` y `utilidad` con el mismo
  criterio que `es_pagina_legal` v2 y `es_pagina_de_utilidad` v2 (decision 169).
- `categoria_cliente`: a cual de las categorias de negocio del cliente
  (`config.taxonomia`) pertenece la pagina. Sus opciones las pone el cliente
  (`opciones_del_cliente`): la de aqui son de muestra y nunca se envian; el
  modulo manda `con_opciones(...)` con las del job.

Sin evaluar (#8): sin umbral, todo sale `incierto` y nada se propaga.
"""

from __future__ import annotations

from shared.preguntas import Pregunta

TIPO_PAGINA = Pregunta(
    nombre="tipo_pagina",
    tipo="choice",
    version=1,
    instrucciones=(
        "Classify this web page by its main purpose for a visitor, judging only by its address (path), "
        "title, main heading, structured data types, word count and the start of its main content. Pick "
        "the single option that describes what the page IS, not what it talks about: a blog post about a "
        "hotel is an article, not a product page, and a hotel page that mentions an offer is still the "
        "hotel's page. Each option says what it is not; when two seem to fit, follow those exclusions. "
        "Use 'otro' only when none fits."
    ),
    opciones={
        "home": ("The home page of the whole site or of one of its language versions (path '/' or '/en/'). "
                 "Not the main page of a section."),
        "listado": ("A category, hub, archive or listing whose main job is to lead to other pages of the same "
                    "kind: a product or hotel category, a destination page that lists hotels, a blog category, "
                    "tag or archive, a list of offers or projects, a paginated or filtered listing. Not a page "
                    "about one single item that happens to link to others."),
        "ficha_producto": ("The page of ONE specific product, service, property or item that can be bought, "
                           "booked or enrolled in (a product, a hotel, a room, a book, a course or degree, a "
                           "service plan), or a section of that one item (its restaurant, spa or gallery). Not "
                           "manuals, documentation or spare parts for a product (ayuda), not a time-limited "
                           "promotion (landing_comercial) and not an article that talks about it (articulo)."),
        "landing_comercial": ("A promotional or conversion page: a time-limited offer, a package or deal, a "
                              "campaign, a 'book now' or 'request a quote' landing. Not the permanent page of "
                              "one product (ficha_producto) and not a plain list of items (listado)."),
        "articulo": ("Editorial content meant to be read: a blog post, news item, press release, guide, how-to, "
                     "interview, magazine article, case study or portfolio project. Not a list of articles "
                     "(listado) and not product documentation (ayuda)."),
        "corporativa": ("Information about the company itself rather than what it sells: about us, history, "
                        "mission and values, the brands of the group, careers and job offers, the press room, "
                        "awards, an investors overview. Not policies, compliance texts or notices of grants "
                        "received (legal)."),
        "contacto_ubicacion": ("How to reach the business: a contact page or form, a store, office, dealer or "
                               "technical service locator, or the page of one physical point of contact with "
                               "its address and opening hours. Not a hotel or venue presented as something to "
                               "book (ficha_producto)."),
        "ayuda": ("Help for customers: FAQ, support, documentation, user or installation manuals, technical data "
                  "sheets, downloads, spare parts, warranty or troubleshooting. Not the page that presents and "
                  "sells the product (ficha_producto)."),
        "legal": ("A legal, regulatory or compliance text: legal notice, privacy, cookies, terms and conditions, "
                  "and pages that exist to meet an obligation (criminal risk prevention, code of conduct, "
                  "whistleblowing channel, ESG or sustainability policies, notices of public grants or subsidies "
                  "received, corporate governance or legal information for shareholders, product safety "
                  "alerts)."),
        "utilidad": ("A functional step with no search value: login or account area, registration, password "
                     "reset, cart, checkout, thank-you or confirmation, unsubscribe, online check-in, an employee "
                     "or customer portal entrance, internal search results, a test page. Not a contact page "
                     "with company details (contacto_ubicacion)."),
        "otro": ("None of the above: an HTML sitemap, an error or empty page, or a page whose purpose cannot be "
                 "told from what is shown."),
    },
    descripcion=("Tipo de pagina con una taxonomia general de 11 opciones, sobre una muestra por "
                 "plantilla (decision 178). Sin evaluar (#8): todo incierto y nada se propaga."),
)

# Lo que se pide a cada categoria del cliente cuando la pagina no es de
# ninguna. No puede ser el nombre de una categoria (lo valida `JobConfig`).
NINGUNA = "ninguna"

CATEGORIA_CLIENTE = Pregunta(
    nombre="categoria_cliente",
    tipo="choice",
    version=1,
    instrucciones=(
        "These options are the business categories of the website's owner. Judging by the page's address "
        "(path), title, main heading and the start of its main content, which single category is this page "
        "mainly about? A page that sells, describes or explains one category belongs to it even if it "
        "mentions others. Choose 'ninguna' when the page is not about any of them: the home page, legal or "
        "contact pages, corporate information, or a page that covers several categories equally."
    ),
    # De muestra: las de verdad son las de `config.taxonomia` del job, con
    # `con_opciones` (decision 178). Nunca se envian estas.
    opciones={"categoria de ejemplo": None, NINGUNA: "The page is not mainly about any of these categories."},
    opciones_del_cliente=True,
    descripcion=("Categoria de negocio del cliente (`config.taxonomia`) por plantilla (decision 178). "
                 "Solo con taxonomia en el job. Sin evaluar (#8)."),
)


def opciones_de_taxonomia(taxonomia) -> dict | None:
    """Las opciones de `categoria_cliente` para la taxonomia de un job: sus
    categorias (sin descripcion) mas `ninguna`. None si no hay dos categorias
    validas: una sola no es una eleccion, y entonces el modulo no pregunta."""
    vistas: dict[str, None] = {}
    for c in taxonomia or []:
        nombre = str(c or "").strip()
        if nombre and nombre.lower() != NINGUNA and nombre.lower() not in {v.lower() for v in vistas}:
            vistas[nombre] = None
    if len(vistas) < 2:
        return None
    return {**vistas, NINGUNA: CATEGORIA_CLIENTE.opciones[NINGUNA]}


PREGUNTAS = [TIPO_PAGINA, CATEGORIA_CLIENTE]
