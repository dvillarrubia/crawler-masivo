"""Validacion de datos estructurados (``analysis.sd_validation``).

Criterio SEO que fija esta suite: faltar una propiedad OBLIGATORIA no es un
aviso de estilo, es que la pagina **no sale como resultado enriquecido** —el
marcado esta puesto y no sirve de nada—, asi que es `error`. Faltar una
RECOMENDADA si es un aviso: sale, pero mas pobre (sin precio, sin estrellas,
sin imagen). Antes los dos casos eran `warning` y la tabla pedia propiedades
que no faltan nunca (`Product` solo pedia `name`), de modo que 459.510 bloques
de un censo real salian todos "ok".

Sigue siendo conservadora: tipo desconocido o forma rara, nunca se marca.
"""

from __future__ import annotations

import pytest

from analysis.sd_validation import (
    problemas_structured_data,
    requisitos_de,
    sd_item_types,
    validate_sd_item,
    validate_structured_data,
)


def test_sd_item_types_variants():
    assert sd_item_types({"@type": "Product"}) == ["Product"]
    assert sd_item_types({"@type": ["Product", "Thing"]}) == ["Product", "Thing"]
    assert sd_item_types({"type": "Article"}) == ["Article"]
    assert sd_item_types({"name": "x"}) == []


def test_un_product_solo_con_nombre_no_es_resultado_enriquecido():
    """El caso que antes salia "ok" y es el que de verdad falla en los sitios.

    Google pide, ademas del nombre, al menos una de `offers`, `review` o
    `aggregateRating`: sin precio, resena ni valoracion no hay ficha de
    producto en el buscador.
    """
    estado, problemas = validate_structured_data({"@type": "Product", "name": "Widget"})
    assert estado == "error"
    assert any("offers" in p for p in problemas)


def test_un_product_completo_si_lo_es():
    estado, problemas = validate_structured_data({
        "@type": "Product", "name": "Widget",
        "offers": {"@type": "Offer", "price": "9.99"},
        "image": "a.png", "brand": "Acme", "description": "d",
    })
    assert (estado, problemas) == ("ok", [])


def test_basta_una_del_grupo():
    for propiedad in ("offers", "review", "aggregateRating"):
        estado, _ = validate_structured_data({
            "@type": "Product", "name": "W", propiedad: {"x": 1},
            "image": "a.png", "brand": "b", "description": "d",
        })
        assert estado == "ok", f"{propiedad} deberia bastar"


def test_falta_una_obligatoria_es_error_no_aviso():
    estado, problemas = validate_structured_data({"@type": "Product", "image": "a.png"})
    assert estado == "error"
    assert any("name" in p and "obligatoria" in p for p in problemas)


def test_solo_faltan_recomendadas_es_aviso():
    """Sale como resultado enriquecido, pero mas pobre: eso es un warning."""
    estado, problemas = validate_structured_data({"@type": "Article", "headline": "h"})
    assert estado == "warning"
    assert all("recomendada" in p for p in problemas)
    assert any("image" in p for p in problemas)


def test_sin_tipo_es_error():
    estado, problemas = validate_structured_data({"name": "x"})
    assert estado == "error"
    assert problemas == ["sin @type: el bloque no identifica ninguna entidad"]


def test_tipo_desconocido_nunca_se_marca():
    assert validate_structured_data({"@type": "WebPage", "foo": 1}) == ("ok", [])


def test_los_subtipos_heredan_los_requisitos():
    # Un NewsArticle se valida como Article; un Restaurant, como LocalBusiness.
    assert requisitos_de("NewsArticle") == requisitos_de("Article")
    assert requisitos_de("Restaurant") == requisitos_de("LocalBusiness")
    # Y con la URL completa de schema.org, que es como lo escriben muchos CMS.
    assert requisitos_de("https://schema.org/BlogPosting") == requisitos_de("Article")
    # Google no exige ninguna propiedad para Article, asi que un NewsArticle
    # pelado es un aviso (sale, pero pobre), no un error.
    estado, problemas = validate_structured_data({"@type": "NewsArticle", "foo": 1})
    assert estado == "warning"
    assert any("headline" in p for p in problemas)


def test_el_grafo_agrega_a_sus_hijos_y_manda_el_peor():
    raw = {"@graph": [
        {"@type": "Article", "headline": "h", "image": "i", "datePublished": "d",
         "author": "a", "dateModified": "m"},                       # ok
        {"@type": "Product", "name": "p", "offers": {"price": 1},
         "image": "i", "brand": "b"},                               # falta description
    ]}
    estado, problemas = validate_structured_data(raw)
    assert estado == "warning"
    assert any("description" in p for p in problemas)


def test_un_hijo_sin_tipo_hace_error_todo_el_bloque():
    raw = {"@graph": [{"@type": "Article", "headline": "h"}, {"name": "sin tipo"}]}
    estado, _ = validate_structured_data(raw)
    assert estado == "error"


def test_lista_suelta_de_entidades():
    raw = [{"@type": "Organization", "name": "Acme"}, {"@type": "Person", "name": "Ann"}]
    estado, problemas = validate_structured_data(raw)
    # Organization tiene recomendadas (url, logo...), Person no tiene ninguna.
    assert estado == "warning"
    assert all("Organization" in p for p in problemas)


def test_lo_que_no_es_dict_ni_lista_nunca_se_marca():
    assert validate_structured_data("nonsense") == ("ok", [])
    assert validate_structured_data(None) == ("ok", [])


def test_en_un_nodo_multitipo_se_revisa_cada_tipo_conocido():
    problemas = validate_sd_item({"@type": ["Article", "CreativeWork"], "name": "x"})
    assert any("Article" in m and "headline" in m for _, m in problemas)
    # CreativeWork no tiene reglas: no aporta problemas.
    assert not any("CreativeWork" in m for _, m in problemas)


# ---------------------------------------------------------------------------
# Casos de la auditoria (#28)
# ---------------------------------------------------------------------------
def test_una_miga_de_pan_sin_eslabones_no_esta_bien():
    """`itemListElement: []` daba "ok": la propiedad esta, pero vacia no genera
    ningun resultado enriquecido."""
    estado, problemas = validate_structured_data(
        {"@type": "BreadcrumbList", "itemListElement": []})
    assert estado == "error"
    assert any("itemlistelement" in p for p in problemas)
    assert validate_structured_data(
        {"@type": "BreadcrumbList",
         "itemListElement": [{"@type": "ListItem", "name": "a"}]}) == ("ok", [])


def test_un_name_en_blanco_es_un_name_que_falta():
    estado, _ = validate_structured_data(
        {"@type": "LocalBusiness", "name": "   ", "address": "Calle 1"})
    assert estado == "error"


def test_organization_sin_name_es_un_aviso_no_un_error():
    """Google: «There are no required properties» para Organization. El `name`
    es recomendado, como todo lo de Article (decision 51)."""
    estado, problemas = validate_structured_data(
        {"@type": "Organization", "url": "https://acme.com", "logo": "l",
         "sameAs": ["s"], "contactPoint": {"telephone": "1"}})
    assert estado == "warning"
    assert problemas == [
        "Organization: falta la propiedad recomendada 'name'; sale como "
        "resultado enriquecido, pero mas pobre"
    ]


def test_un_nodo_de_referencia_no_es_un_bloque_roto():
    """En el `@graph` de Yoast estan por todas partes; salian como "sin @type"."""
    assert validate_structured_data({"@id": "https://x.com/#org"}) == ("ok", [])
    assert validate_structured_data({"@graph": []}) == ("ok", [])
    assert validate_structured_data(
        {"@context": "https://schema.org", "@graph": []}) == ("ok", [])


def test_una_entidad_anidada_tambien_se_valida():
    """Un Product dentro de `WebPage.mainEntity` es el producto de la pagina:
    sus requisitos son los mismos. Shopify y los constructores de paginas
    anidan, y asi no se validaba nada."""
    estado, problemas = validate_structured_data(
        {"@type": "WebPage", "name": "p",
         "mainEntity": {"@type": "Product", "name": "X"}})
    assert estado == "error"
    assert any("offers" in p for p in problemas)
    # Y si la anidada esta completa, no se inventa nada.
    assert validate_structured_data(
        {"@type": "WebPage", "name": "p",
         "mainEntity": {"@type": "Product", "name": "X", "offers": {"price": 1},
                        "image": "i", "brand": "b", "description": "d"}}) == ("ok", [])


def test_una_resena_dentro_de_un_producto_no_necesita_itemreviewed():
    """Lo resenado es el producto que la contiene y Google documenta que no hay
    que repetirlo. Validarla como si estuviera suelta producia **675 errores
    falsos** en un censo de 4.785 paginas de comercio electronico, justo en las
    fichas con resenas: donde el marcado esta BIEN."""
    ficha = {
        "@type": "Product", "name": "X", "offers": {"@type": "Offer", "price": "1"},
        "image": "i", "brand": {"@type": "Organization", "name": "Acme"},
        "description": "d",
        "review": [{"@type": "Review", "reviewRating": {"ratingValue": 5},
                    "author": {"@type": "Person", "name": "A"}}],
    }
    assert validate_structured_data(ficha) == ("ok", [])


def test_una_resena_suelta_si_necesita_itemreviewed():
    suelta = {"@type": "Review", "reviewRating": {"ratingValue": 5},
              "author": {"@type": "Person", "name": "A"}}
    estado, problemas = validate_structured_data(suelta)
    assert estado == "error"
    assert any("itemreviewed" in p for p in problemas)


def test_una_entidad_anidada_no_avisa_de_lo_recomendado():
    """Una Organization puesta como marca de un producto no necesita logo ni
    perfiles sociales: lo recomendado pesa en la entidad principal."""
    ficha = {
        "@type": "Product", "name": "X", "offers": {"@type": "Offer", "price": "1"},
        "image": "i", "description": "d",
        "brand": {"@type": "Organization", "name": "Acme"},
    }
    assert validate_structured_data(ficha) == ("ok", [])
    # Pero una obligatoria que falta en la anidada sigue siendo error.
    ficha["review"] = {"@type": "Review", "reviewRating": {"ratingValue": 5}}
    estado, problemas = validate_structured_data(ficha)
    assert estado == "error"
    assert any("author" in p for p in problemas)


# ---------------------------------------------------------------------------
# Lo que un lector de RDFa saca de una pagina normal no son datos estructurados
# ---------------------------------------------------------------------------
def test_un_role_de_accesibilidad_no_es_un_bloque_roto():
    """`<div role="alert">` deja un nodo RDFa con la propiedad del vocabulario
    XHTML y sin `@type`. No es marcado roto: no es marcado.

    Medido al re-analizar el censo de CST: **88.838 "errores" de los que 81.023
    eran atributos `role`**, el 100% de los errores de datos estructurados de
    ese informe, y la nota de la categoria caia de 100 a 60 por ellos.
    """
    item = {
        "@id": "https://x.com/p#__next-route-announcer__",
        "http://www.w3.org/1999/xhtml/vocab#role": [
            {"@id": "http://www.w3.org/1999/xhtml/vocab#alert"}
        ],
    }
    assert validate_sd_item(item) == []


def test_el_open_graph_leido_como_rdfa_tampoco():
    """Un `<meta property="og:title">` deja `http://ogp.me/ns#title`. El Open
    Graph ya se extrae aparte, a `html_meta.og_*`: contarlo aqui es contarlo
    dos veces y ademas como error. Eran 7.815 en el mismo censo."""
    item = {
        "@id": "https://x.com/p",
        "http://ogp.me/ns#title": [{"@value": "Un anuncio"}],
        "http://ogp.me/ns#type": [{"@value": "article"}],
    }
    assert validate_sd_item(item) == []


def test_un_bloque_de_schema_sin_tipo_SI_es_un_error():
    """La guarda no puede tragarse el caso de verdad: un bloque con
    propiedades de schema.org y sin `@type` no declara ninguna entidad."""
    item = {"name": "Algo", "description": "sin @type"}
    assert any(sev == "error" and "@type" in msg for sev, msg in validate_sd_item(item))


def test_mezclar_schema_con_un_vocabulario_ajeno_sigue_siendo_error():
    item = {
        "@id": "https://x.com/p",
        "http://ogp.me/ns#title": [{"@value": "x"}],
        "name": "y",
    }
    assert any(sev == "error" and "@type" in msg for sev, msg in validate_sd_item(item))


# ---------------------------------------------------------------------------
# JSON-LD que no es JSON (#28)
# ---------------------------------------------------------------------------
def test_un_json_ld_con_coma_final_es_error_aunque_este_completo():
    """Google descarta el bloque entero: da igual que las propiedades esten."""
    completo = {"@type": "BreadcrumbList",
                "itemListElement": [{"@type": "ListItem", "name": "a"}]}
    assert validate_structured_data(completo) == ("ok", [])
    estado, problemas = validate_structured_data(
        completo, json_invalido="coma final antes de '}' (linea 1, columna 9)")
    assert estado == "error"
    assert problemas[0].startswith("JSON-LD no valido: coma final")


def test_un_json_ld_ilegible_es_error():
    estado, problemas = validate_structured_data(
        None, json_invalido="Expecting ',' delimiter (linea 1, columna 25)")
    assert estado == "error" and len(problemas) == 1


def test_tras_el_json_invalido_se_sigue_validando_el_resto():
    """Para que al arreglar la coma no aparezca una segunda tanda de errores."""
    _, problemas = validate_structured_data(
        {"@type": "Product", "name": "X"}, json_invalido="comentario dentro del JSON")
    assert problemas[0].startswith("JSON-LD no valido")
    assert any("offers" in p for p in problemas[1:])


# ---------------------------------------------------------------------------
# La tabla de requisitos entera, contra la documentacion de Google (#28)
# ---------------------------------------------------------------------------
# Escrita a mano a partir de las paginas de resultados enriquecidos de Google
# (developers.google.com/search/docs/appearance/structured-data/<pagina>),
# consultadas el 2026-10-13. Si cambia REQUISITOS sin cambiar esto, el test
# falla: un cambio en lo que se exige es un cambio de criterio y tiene que ser
# a proposito. Las OBLIGATORIAS son exactamente las de Google; las
# recomendadas son un subconjunto (las que mas pesan en el resultado), no la
# lista completa.
REQUISITOS_ESPERADOS = {
    # product-snippet: name + al menos uno de review/aggregateRating/offers.
    # Las recomendadas salen de merchant-listing (image, brand, description).
    "product": {
        "obligatorias": ["name"],
        "uno_de": [["offers", "review", "aggregaterating"]],
        "recomendadas": ["image", "brand", "description"],
    },
    # article: «There are no required properties».
    "article": {
        "obligatorias": [],
        "recomendadas": ["headline", "image", "datepublished", "author",
                         "datemodified"],
    },
    # recipe: obligatorias image y name.
    "recipe": {
        "obligatorias": ["name", "image"],
        "recomendadas": ["recipeingredient", "recipeinstructions",
                         "aggregaterating", "author", "datepublished"],
    },
    # qapage: mainEntity (la Question). Sigue vivo: documentado y sin aviso de
    # retirada a 2026-10-16 (developers.google.com/.../structured-data/qapage).
    # FAQPage y HowTo ya no estan: ver RETIRADOS_ESPERADOS.
    "qapage": {"obligatorias": ["mainentity"]},
    # breadcrumb: itemListElement.
    "breadcrumblist": {"obligatorias": ["itemlistelement"]},
    # organization: «There are no required properties»; name es recomendada.
    "organization": {
        "obligatorias": [],
        "recomendadas": ["name", "url", "logo", "sameas", "contactpoint"],
    },
    # local-business: obligatorias address y name. `openinghours` e `image`
    # no estan en la lista de Google (que dice openingHoursSpecification y no
    # pide imagen); se dejan como estaban porque son avisos, no errores.
    "localbusiness": {
        "obligatorias": ["name", "address"],
        "recomendadas": ["telephone", "openinghours", "geo", "pricerange", "image"],
    },
    # event: obligatorias location, name, startDate.
    "event": {
        "obligatorias": ["name", "startdate", "location"],
        "recomendadas": ["enddate", "offers", "image", "description", "performer"],
    },
    # job-posting: datePosted, description, hiringOrganization, jobLocation,
    # title. validThrough solo si la oferta caduca: recomendada.
    "jobposting": {
        "obligatorias": ["title", "description", "dateposted",
                         "hiringorganization", "joblocation"],
        "recomendadas": ["basesalary", "employmenttype", "validthrough"],
    },
    # review-snippet: author, itemReviewed (salvo anidada), reviewRating.
    "review": {
        "obligatorias": ["itemreviewed", "reviewrating", "author"],
        "recomendadas": ["datepublished", "reviewbody"],
    },
    # video: obligatorias name, thumbnailUrl, uploadDate; description es
    # recomendada.
    "videoobject": {
        "obligatorias": ["name", "thumbnailurl", "uploaddate"],
        "recomendadas": ["description", "duration", "contenturl", "embedurl"],
    },
    # course: obligatorias name y description; provider es recomendada.
    "course": {
        "obligatorias": ["name", "description"],
        "recomendadas": ["provider", "offers", "hascourseinstance"],
    },
    # software-app: name, offers(.price) y uno de aggregateRating o review.
    "softwareapplication": {
        "obligatorias": ["name", "offers"],
        "uno_de": [["aggregaterating", "review"]],
        "recomendadas": ["operatingsystem", "applicationcategory"],
    },
    # profile-page: la Person de mainEntity exige name.
    "person": {"obligatorias": ["name"]},
}


def test_la_tabla_de_requisitos_coincide_con_la_documentacion_de_google():
    from analysis.sd_validation import REQUISITOS

    assert set(REQUISITOS) == set(REQUISITOS_ESPERADOS)
    for tipo, esperado in REQUISITOS_ESPERADOS.items():
        real = REQUISITOS[tipo]
        for campo in ("obligatorias", "uno_de", "recomendadas"):
            assert real.get(campo, []) == esperado.get(campo, []), (tipo, campo)


def test_un_video_sin_description_sale_enriquecido():
    estado, problemas = validate_structured_data(
        {"@type": "VideoObject", "name": "v", "thumbnailUrl": "t",
         "uploadDate": "2026-01-01"})
    assert estado == "warning"
    assert any("description" in p for p in problemas)


def test_una_app_con_resenas_y_sin_valoracion_media_sale_enriquecida():
    estado, _ = validate_structured_data(
        {"@type": "SoftwareApplication", "name": "a", "offers": {"price": 0},
         "review": {"@type": "Review", "author": "x", "reviewRating": {"ratingValue": 4}},
         "operatingSystem": "iOS", "applicationCategory": "Game"})
    assert estado == "ok"


# Resultados que Google ya no muestra (decision 97), comprobado el 2026-10-16
# en https://developers.google.com/search/updates:
# - 2023-09-14 «Removed the How-to structured data documentation, as this rich
#   result is no longer shown in search results» y FAQ limitado a «well-known,
#   authoritative government and health websites».
# - 2026-05-08 «Deprecating the FAQ rich result feature» («will no longer
#   appear in Google Search starting May 7, 2026») y 2026-06-15 «Removed
#   documentation for the FAQ rich result feature».
RETIRADOS_ESPERADOS = {"faqpage": "2026-05-07", "howto": "2023-09-14"}


def test_la_tabla_de_retirados_coincide_con_la_documentacion_de_google():
    from analysis.sd_validation import REQUISITOS, RESULTADOS_RETIRADOS

    assert {t: r["desde"] for t, r in RESULTADOS_RETIRADOS.items()} == RETIRADOS_ESPERADOS
    assert not set(RESULTADOS_RETIRADOS) & set(REQUISITOS), (
        "un tipo retirado no puede exigir propiedades")
    for r in RESULTADOS_RETIRADOS.values():
        assert r["fuente"].startswith("https://developers.google.com/search/")


def test_un_faq_sin_mainentity_ya_no_es_un_error():
    """Antes era «falta la propiedad obligatoria»: el mismo peso que un Product
    sin precio, por un resultado que Google ya no ensena."""
    estado, problemas = validate_structured_data({"@type": "FAQPage"})
    assert estado == "info"
    assert len(problemas) == 1
    assert "ya no muestra" in problemas[0]
    assert "developers.google.com/search/updates" in problemas[0], "con la fuente"


def test_un_howto_completo_tambien_avisa_de_que_no_da_nada():
    estado, problemas = validate_structured_data(
        {"@type": "HowTo", "name": "x", "step": [{"@type": "HowToStep", "text": "a"}]})
    assert estado == "info"
    assert "2023-09-14" in problemas[0]


def test_lo_retirado_no_tapa_un_error_de_otra_entidad():
    estado, problemas = validate_structured_data({"@graph": [
        {"@type": "FAQPage", "mainEntity": []},
        {"@type": "Product", "name": "p"},
    ]})
    assert estado == "error", "manda el peor"
    assert any("ya no muestra" in p for p in problemas)


def test_un_faq_anidado_avisa_igual():
    estado, problemas = validate_structured_data(
        {"@type": "WebPage", "mainEntity": {"@type": "FAQPage"}})
    assert estado == "info"
    assert problemas and "FAQPage" in problemas[0]


def test_qapage_sigue_exigiendo_su_pregunta():
    estado, _ = validate_structured_data({"@type": "QAPage"})
    assert estado == "error"


# -- Formato de los valores (decision 146) ---------------------------------------

def _producto(**oferta):
    return {"@type": "Product", "name": "W", "image": "i.png", "brand": "b", "description": "d",
            "offers": {"@type": "Offer", **oferta}}


@pytest.mark.parametrize("oferta, nivel, trozo", [
    ({"price": "19,99", "priceCurrency": "EUR"}, "error", "'offers.price' no es un número"),
    ({"price": "19.99 €", "priceCurrency": "EUR"}, "error", "'offers.price' no es un número"),
    ({"price": "19.99", "priceCurrency": "€"}, "error", "ISO 4217"),
    ({"price": 19.99, "priceCurrency": "EUR", "availability": "En stock"}, "warning", "ItemAvailability"),
    ({"price": "19.99", "priceCurrency": "EUR", "priceValidUntil": "31/12/2026"}, "warning", "ISO 8601"),
])
def test_valores_mal_escritos_en_una_oferta(oferta, nivel, trozo):
    problemas = problemas_structured_data(_producto(**oferta))
    assert any(n == nivel and trozo in m for n, m in problemas), problemas


@pytest.mark.parametrize("oferta", [
    {"price": "19.99", "priceCurrency": "EUR", "availability": "https://schema.org/InStock"},
    {"price": 0, "priceCurrency": "USD", "availability": "InStock", "priceValidUntil": "2026-12-31"},
    {"price": "1299", "priceCurrency": "EUR", "availability": "http://schema.org/PreOrder"},
])
def test_valores_bien_escritos_no_avisan(oferta):
    assert problemas_structured_data(_producto(**oferta)) == []


def test_una_fecha_obligatoria_mal_escrita_es_error_y_una_recomendada_aviso():
    ev = problemas_structured_data({"@type": "Event", "name": "E", "startDate": "19 de octubre",
                                    "location": "Bilbao", "endDate": "mañana"})
    assert ("error", "Event: 'startDate' no es una fecha ISO 8601 (AAAA-MM-DD, con hora AAAA-MM-DDThh:mm y zona)") in ev
    assert any(n == "warning" and "'endDate'" in m for n, m in ev)
    assert not any("'startDate'" in m for n, m in problemas_structured_data(
        {"@type": "Event", "name": "E", "startDate": "2026-10-19T20:00+02:00", "location": "Bilbao"}))


def test_una_valoracion_que_no_es_numero_es_error_y_un_recuento_decimal_aviso():
    p = problemas_structured_data({"@type": "Product", "name": "W", "image": "i", "brand": "b",
                                   "description": "d",
                                   "aggregateRating": {"ratingValue": "cuatro", "reviewCount": "12.5"}})
    assert any(n == "error" and "'aggregateRating.ratingValue'" in m for n, m in p)
    assert any(n == "warning" and "'aggregateRating.reviewCount'" in m for n, m in p)


def test_los_mensajes_no_llevan_el_valor_para_que_agrupen():
    a = problemas_structured_data(_producto(price="19,99", priceCurrency="EUR"))
    b = problemas_structured_data(_producto(price="7,50", priceCurrency="EUR"))
    assert a == b


def test_una_entidad_anidada_no_se_repite_con_el_tipo_de_fuera():
    p = problemas_structured_data({"@type": "Product", "name": "W", "image": "i", "brand": "b",
                                   "description": "d", "offers": {"price": "9.99", "priceCurrency": "EUR"},
                                   "review": {"@type": "Review", "reviewRating": {"ratingValue": "x"},
                                              "author": "a", "datePublished": "2026-01-01"}})
    assert [m for _, m in p if "ratingValue" in m] == ["Review: 'reviewRating.ratingValue' no es un número"]
