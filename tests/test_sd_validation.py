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

from analysis.sd_validation import (
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
    estado, _ = validate_structured_data({"@type": "Organization", "name": "   "})
    assert estado == "error"


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
