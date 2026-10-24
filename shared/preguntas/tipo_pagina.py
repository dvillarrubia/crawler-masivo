"""Tipo de pagina y categoria del cliente (#12, decision 178).

Dos preguntas `choice`, sobre una MUESTRA por plantilla (no sobre cada URL):
lo que se paga es por plantilla, y la respuesta de sus muestras se propone
para la plantilla entera con su acuerdo y su confianza
(`shared/tipo_pagina.propuesta_por_plantilla`).

- `tipo_pagina`: una taxonomia general para SEO, la misma en todos los
  clientes, porque de ella cuelgan los umbrales por tipo (#12), la relevancia
  de cada incidencia (#22) y que la poda (#198) no pregunte por paginas legales
  o de contacto. Doce opciones, cada una con lo que es, ejemplos y lo que NO
  es (en jev-seo las opciones asi subieron el acuerdo de 20 a 27 sobre 30).
  `legal` y `utilidad` con el mismo criterio que `es_pagina_legal` v2 y
  `es_pagina_de_utilidad` v2 (decision 169). Los casos de produccion que
  caian entre dos opciones estan en `CASOS_FRONTERA`, cada uno reclamado por
  UNA sola opcion (lo comprueba `tests/test_tipo_pagina.py`).
  La v2 (decision 181) pregunta que HACE la pagina y no de que item habla:
  lo que nombra un programa sin venderlo (captacion, pagos y apoyo al
  alumno, instalaciones propias, documentacion de calidad) ya no es
  `ficha_producto`; sus fronteras salen de los fallos medidos de la v1.
- `categoria_cliente`: a cual de las categorias de negocio del cliente
  (`taxonomia` del cliente, `shared/tipo_pagina.taxonomia_de`) pertenece la
  pagina. Un solo criterio, de que trata; que tipo de pagina es lo dice
  `tipo_pagina`. Sus opciones las pone el cliente
  (`opciones_del_cliente`): la de aqui son de muestra y nunca se envian; el
  modulo manda `con_opciones(...)` con las del job.

Sin evaluar (#8): sin umbral, todo sale `incierto` y nada se propaga.
"""

from __future__ import annotations

from shared.preguntas import Pregunta

TIPO_PAGINA = Pregunta(
    nombre="tipo_pagina",
    tipo="choice",
    # v2 (decision 181): las fronteras que fallaron en la v1 medida (229/300).
    version=2,
    instrucciones=(
        "Classify this web page by its main purpose for a visitor, judging only by the fields given: its "
        "address (path), title, main heading, the start of its main content and, when present, its word count, "
        "Open Graph type and structured data types. Pick the single option that describes what the page IS, "
        "not what it talks about: a blog post about a hotel is an article, not a product page, and a hotel "
        "page that mentions an offer is still the hotel's page. Naming a product, course or programme does "
        "not make a page its product page: ask what the page does for the visitor. Presenting one item for "
        "sale or booking is ficha_producto; persuading or inviting to an event is landing_comercial; explaining "
        "how to pay, apply or get support is ayuda; presenting the organisation's own facilities, services or "
        "research that are not sold is corporativa; publishing documents required by a regulation is legal. "
        "The start of the content may still include menu text; ignore it. Each option says what it is not; "
        "when two seem to fit, follow those exclusions. Use 'otro' only when none fits."
    ),
    opciones={
        "home": ("The home page of the whole site or of one of its language or country versions (path '/', "
                 "'/en/' or '/de', with or without the final slash). Not the main page of a section."),
        "listado": ("A category, hub, archive or listing whose main job is to lead to other pages of the same "
                    "kind (products, articles, hotels, offers, events, people): a product or hotel category, a "
                    "destination page that lists hotels, a brand or collection page that presents and lists the "
                    "brand's hotels or products, a blog category, tag or archive, a list of offers, projects or "
                    "people, a paginated or filtered listing, or a section hub whose content is little more "
                    "than links to its subsections. Not the page of one single item or person that happens to "
                    "link to others (ficha_producto, perfil), not a page that gives practical resources such as "
                    "schedules, catalogues or tools even through links (ayuda) and not a table of the "
                    "organisation's own departments or partner institutions without pages of their own "
                    "(corporativa)."),
        "ficha_producto": ("The page of ONE specific product, service, property or item that the site sells, "
                           "rents, books or enrols people in (a product, a hotel, a room, a book, a course or "
                           "degree, a service plan), presented as that item: what it is, includes or costs, "
                           "its features or syllabus; or a part of that one item (its restaurant, spa or "
                           "gallery, or one subject or module of a degree with its objectives and syllabus). "
                           "Not a promotion, package, campaign, 'why study or choose us' page or invitation "
                           "to an event (landing_comercial), not how to pay, finance, apply for or get "
                           "support in it (ayuda), not the organisation's own facilities, services or "
                           "research that are not sold (corporativa), not the quality, accreditation or "
                           "official documents of one programme or product (legal), not manuals, "
                           "documentation or spare parts (ayuda) and not an article that talks about it "
                           "(articulo)."),
        "landing_comercial": ("A promotional, persuasive or conversion page rather than the permanent page of "
                              "one item: a time-limited offer, a package or deal with what it includes (a "
                              "wedding, trip or stay package), an experience promoted across several "
                              "venues, a brand or awareness campaign, a commercial pitch to a type of "
                              "customer ('what we offer you'), a 'why study here' or 'why choose us' page "
                              "for a whole area or for the organisation, a recruitment page that invites "
                              "prospects to an open day, information session, talk or contest, a 'book "
                              "now' or 'request a quote' landing. Not the permanent page of one product "
                              "(ficha_producto), not a brand page or a plain list of items or offers "
                              "(listado) and not the company's history or values (corporativa)."),
        "articulo": ("Editorial content meant to be read: a blog post, news item, press release, guide, how-to, "
                     "interview, magazine article, case study, portfolio project or an informative text about "
                     "an event or contest. Not a list of articles (listado), not product documentation (ayuda), "
                     "not the description of a course or subject (ficha_producto), not the standing "
                     "presentation of the organisation's own research groups, lines or projects (corporativa) "
                     "and not a page with almost no content (otro)."),
        "perfil": ("The page of ONE person or one outside entity as a profile: an author with their biography "
                   "and their books, even if the list of books is empty; a teacher, researcher or staff "
                   "directory entry with position, email, office or building; a team member, speaker, artist "
                   "or partner. Not a list of several people (listado), not the company presenting itself or "
                   "its own research groups and departments (corporativa) and not an interview or article "
                   "about the person (articulo)."),
        "corporativa": ("Information about the organisation itself rather than what it sells: about us, "
                        "history, mission and values, careers and job offers, the press room, awards, an "
                        "investors overview, its structure and departments, its agreements and partner "
                        "institutions (also as a table or list without pages of their own), its own research "
                        "groups, research lines and R&D or innovation projects, and its own facilities, labs, "
                        "campus, incubators and in-house centres that are presented but not sold on their "
                        "own. Not a brand page that lists what the brand sells (listado), not one person's "
                        "profile (perfil), not a campaign or a 'why choose us' pitch (landing_comercial) and "
                        "not policies, compliance texts, quality reports or notices of grants received "
                        "(legal)."),
        "contacto_ubicacion": ("How to reach the business: a contact page or form, a store, office, dealer or "
                               "technical service locator, or the page of one shop, office or service point "
                               "with its address and opening hours. Not a hotel or venue presented as something "
                               "to book (ficha_producto) and not a person's directory entry (perfil)."),
        "ayuda": ("Help and practical information for customers, students or users: FAQ, support, "
                  "documentation, user or installation manuals, technical data sheets, downloads, spare parts, "
                  "warranty or troubleshooting; how to pay, financing, scholarships, how to register or apply; "
                  "practical information on exchange or mobility programmes and internships; support classes, "
                  "tutoring and other support services for those already enrolled or customers; schedules, "
                  "academic calendars, library catalogues and search portals, also as a hub that links to "
                  "them; interactive tools that help a customer choose or calculate (a grant or subsidy "
                  "finder, a sizing calculator, a product configurator or simulator) or a page that gathers "
                  "such tools for professionals; and information on public grants a customer can apply for. "
                  "Not the page that presents and sells the product (ficha_producto) and not regulations or "
                  "official documents (legal)."),
        "legal": ("A legal, regulatory or compliance text or document: legal notice, privacy, cookies, terms and "
                  "conditions, and pages that exist to meet an obligation (criminal risk prevention, code of "
                  "conduct, whistleblowing channel, ESG or sustainability policies and sustainability reports, "
                  "notices of public grants or subsidies the company itself received, also a page whose "
                  "content opens with a notice of public co-funding before describing a project, corporate "
                  "governance or legal information for shareholders, product safety alerts), academic or "
                  "internal regulations, and quality-assurance documentation: the quality system published to "
                  "comply with a law, and the verification, monitoring or accreditation reports and official "
                  "documents of a programme or product, also when the page is a list of them. Not a tool or "
                  "guide about grants a customer can apply for (ayuda) and not the page that presents the "
                  "programme itself (ficha_producto)."),
        "utilidad": ("A functional step with no search value: login or account area, registration, password "
                     "reset, cart, checkout, thank-you or confirmation, unsubscribe, online check-in, an employee "
                     "or customer portal entrance, internal search results, a test page. Not a contact page "
                     "with company details (contacto_ubicacion), not instructions on how to register or pay "
                     "(ayuda) and not a calculator, finder or configurator (ayuda)."),
        "otro": ("None of the above: an HTML sitemap, an error page, an empty page whose own content is one or "
                 "two words, or a page whose purpose cannot be told from what is shown."),
    },
    descripcion=("Tipo de pagina con una taxonomia general de 12 opciones, sobre una muestra por "
                 "plantilla (decisiones 178 y 181). Sin evaluar (#8): todo incierto y nada se propaga."),
)

# Paginas reales de produccion (24-oct-2026, solo lectura) que con la primera
# redaccion admitian dos o tres opciones, la opcion que les toca y la frase
# que lo dice. Cada frase esta en la parte afirmativa de UNA sola opcion (lo
# que va antes de su primer «Not») y, si aparece en otras, solo tras el
# «Not». No es una evaluacion: el juego etiquetado (#8) dira si Jev lo sigue.
CASOS_FRONTERA = [
    # Penguin d3fda8a5: 18.955 de 78.457 indexables, casi toda `/:idioma/:slug`.
    ("Penguin /es/5150-pere-cervantes, autor con su bio y sus libros", "perfil", "an author"),
    ("Penguin /es/1017738-kobi-yamada, autor con «0 Titulos»", "perfil", "even if the list of books is empty"),
    # Salle 49ddb8f5: 1.141 fichas del directorio solo en /es/.
    ("Salle /es/la-salle/directorio/*, cargo, email y edificio", "perfil", "staff directory entry"),
    # Lopesan 1e7ea651: 21 URLs con schema Hotel+Offer que listan los hoteles.
    ("Lopesan /es/abora-hotels, «Cuatro hoteles Abora...»", "listado", "brand or collection page"),
    ("Salle /es/bases-de-datos-9, «El objetivo de la asignatura...»", "ficha_producto", "one subject or module"),
    ("Saunier /para-el-profesional/herramientas/subvenciones/", "ayuda", "grant or subsidy finder"),
    ("Saunier /para-el-usuario/servicios/planes-renove/subvenciones-2610567.html, «Subvenciones y ayudas»",
     "ayuda", "public grants a customer can apply for"),
    ("Una pagina de subvenciones recibidas por la empresa", "legal", "grants or subsidies the company itself"),
    # v2 (decision 181): los fallos de la v1 en el juego de 300 (229 aciertos),
    # con la etiqueta revisada. Criterios generales, no las URLs del juego.
    # 1) Producto de mas: lo que nombra un programa pero no lo vende.
    ("Salle «Por que estudiar Arquitectura/Tecnologia y Salud en La Salle»", "landing_comercial",
     "'why study here' or 'why choose us' page"),
    ("Lopesan /eventos/bodas/endless-knot, paquete de boda con lo que incluye", "landing_comercial",
     "wedding, trip or stay package"),
    ("Lopesan /es/hoteles/cocteleria-autor, la cocteleria de todos los hoteles con Offer", "landing_comercial",
     "experience promoted across several venues"),
    ("Salle /admissions/.../financing-program, plazos y formas de pago", "ayuda", "financing"),
    ("Salle /admissions/degrees/scholarship-program", "ayuda", "scholarships"),
    ("Salle /oficina-internacional/programas-de-intercambio/alumnos-incoming", "ayuda",
     "exchange or mobility programmes"),
    ("Salle /servicio-de-acompanamiento-al-alumnado/support-classes", "ayuda", "support classes"),
    ("Salle /el-campus/installacions/laboratori-dinstrumentacio, instalacion propia que no se vende",
     "corporativa", "own facilities, labs"),
    ("Salle /universidad-y-empresa/.../la-salle-technova, el parque de emprendimiento propio", "corporativa",
     "incubators"),
    ("Salle /gestio-de-la-qualitat/marc-vsma/<grado>, memorias e informes de acreditacion", "legal",
     "accreditation reports"),
    # 2) Landing casi nunca: captacion y campanas.
    ("Salle /admissions/bachelors-degrees/open-day-and-informative-sessions", "landing_comercial", "open day"),
    ("Salle /escoles-i-families/.../xerrada-dorientacio-families, charla con fecha", "landing_comercial",
     "information session, talk or contest"),
    ("Saunier /marca-excelente/, campana de marca", "landing_comercial", "brand or awareness campaign"),
    ("Saunier /para-el-profesional/prescripcion/que-te-ofrecemos/", "landing_comercial",
     "commercial pitch to a type of customer"),
    # 3) Legal que se escapa.
    ("Salle /management-of-the-quality/quality-system (RD 822/2021)", "legal",
     "quality system published to comply with a law"),
    ("Salle /masters-y-postgrados/informacion-academica/normativa-academica", "legal",
     "academic or internal regulations"),
    ("Salle /la-salle-y-la-investigacion/iaslab, empieza por el aviso de cofinanciacion FEDER", "legal",
     "notice of public co-funding"),
    ("Lopesan /corporate/lopesan-for-good/sustainability-reports", "legal", "sustainability reports"),
    # 4) Ayuda frente a listado: el listado es de paginas del mismo tipo.
    ("Salle /academic-information/schedules, enlaza a los horarios de cada grado", "ayuda",
     "schedules, academic calendars"),
    ("Salle /biblioteca/catalegs, enlaza a catalogos y bases de datos", "ayuda", "library catalogues"),
    ("Saunier /para-el-profesional/herramientas/", "ayuda", "gathers such tools for professionals"),
    ("Salle /nuevos-alumnos/proceso-de-admision, reparte a grados y masteres", "listado",
     "section hub whose content is little more than links"),
    ("Salle /convenis-de-cooperacio-academica/*, tabla de universidades sin pagina propia", "corporativa",
     "agreements and partner institutions"),
    ("Salle /organitzacio-i-estructura/departaments-academics", "corporativa", "structure and departments"),
    # 5) Menores.
    ("Lopesan /de, portada de la version alemana", "home", "'/de'"),
    ("Lopesan /es/corporativa/noticias/detalle-noticia1, una sola palabra", "otro", "one or two words"),
    ("Salle /investigacion/grupos-e-institutos/<grupo>, grupo de investigacion propio", "corporativa",
     "own research groups"),
    ("Lopesan /corporativa/innovacion/disminucion-desperdicio-alimentario, I+D propio", "corporativa",
     "R&D or innovation projects"),
]

# Lo que se pide a cada categoria del cliente cuando la pagina no es de
# ninguna. No puede ser el nombre de una categoria (lo valida `JobConfig`).
NINGUNA = "ninguna"

CATEGORIA_CLIENTE = Pregunta(
    nombre="categoria_cliente",
    tipo="choice",
    version=1,
    instrucciones=(
        "These options are the business categories of the website's owner. Judging by the page's address "
        "(path), title, main heading and the start of its main content, which single category is the page's "
        "subject? Decide only by subject, whatever kind of page it is: a product page, an article, a support "
        "page or a contact page about one category all belong to that category, even if they mention others. "
        "Choose 'ninguna' only when the page's subject is none of the categories (the company as a whole, a "
        "topic outside them) or when it covers several categories with equal weight."
    ),
    # De muestra: las de verdad son las de la taxonomia del cliente, con
    # `con_opciones` (decision 178). Nunca se envian estas.
    opciones={"categoria de ejemplo": None,
              NINGUNA: ("The page's subject is none of these categories, or it covers several of them with "
                        "equal weight.")},
    opciones_del_cliente=True,
    descripcion=("Categoria de negocio del cliente (su `taxonomia`) por plantilla (decision 178). "
                 "Solo con taxonomia. Sin evaluar (#8)."),
)


def opciones_de_taxonomia(taxonomia) -> dict | None:
    """Las opciones de `categoria_cliente` para una taxonomia: sus categorias
    (un nombre, o `{"nombre", "descripcion"}` con lo que entra y lo que no)
    mas `ninguna`. None si no hay dos categorias validas: una sola no es una
    eleccion, y entonces el modulo no pregunta."""
    vistas: dict[str, str | None] = {}
    for c in taxonomia or []:
        if isinstance(c, dict):
            nombre = str(c.get("nombre") or "").strip()
            descripcion = " ".join(str(c.get("descripcion") or "").split()) or None
        else:
            nombre, descripcion = str(c or "").strip(), None
        if nombre and nombre.lower() != NINGUNA and nombre.lower() not in {v.lower() for v in vistas}:
            vistas[nombre] = descripcion
    if len(vistas) < 2:
        return None
    return {**vistas, NINGUNA: CATEGORIA_CLIENTE.opciones[NINGUNA]}


PREGUNTAS = [TIPO_PAGINA, CATEGORIA_CLIENTE]
