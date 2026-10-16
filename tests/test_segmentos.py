"""Segmentos de negocio: reglas, precedencia y logica de tres valores
(decision 108, `shared/segmentos.py`).

Lo que se sujeta, ejecutando el segmentador sobre filas como las que lee de
`urls` y `custom_extraction`:
- la primera regla que casa gana, y `patron` es el de `config.templates`;
- Y / O / NO, campos y reglas a medida;
- lo que no se sabe (sin fila de extraccion, pagina bloqueada, NULL) no casa,
  tampoco negado, y se cuenta;
- sin reglas o sin coincidencia, la forma de la ruta; las externas aparte;
- la validacion dice que esta mal y donde, y JobConfig la aplica.
"""

from __future__ import annotations

import pytest

pytest.importorskip("regex")

from shared.plantillas import firma_de_ruta  # noqa: E402
from shared.segmentos import SEGMENTO_EXTERNA, Segmentador, validar  # noqa: E402


def _fila(path="/", x=None, **campos):
    base = {"path": path, "url": "https://x.com" + path, "is_internal": True,
            "status_code": 200, "status_group": "2xx", "indexable": True,
            "content_type": "text/html; charset=utf-8"}
    base.update(campos)
    base["_x"] = x
    return base


def _x(estado="ok", valor=None, valor_num=None, n=None, clase="extractor"):
    return {"estado": estado, "valor": valor, "valor_num": valor_num, "n": n, "clase": clase}


def test_la_primera_regla_que_casa_gana_y_patron_es_el_de_templates():
    seg = Segmentador([
        {"nombre": "blog", "patron": "^/blog/"},
        {"nombre": "todo", "patron": "."},
    ])
    assert seg.segmento(_fila("/BLOG/post-1")) == "blog"  # sin distinguir mayusculas
    assert seg.segmento(_fila("/otra")) == "todo"
    assert seg.casadas == [1, 1]


def test_sin_reglas_o_sin_coincidencia_la_forma_de_la_ruta_y_externas_aparte():
    seg = Segmentador([{"nombre": "blog", "patron": "^/blog/"}])
    assert seg.segmento(_fila("/es/libros/123-algo")) == firma_de_ruta("/es/libros/123-algo")
    assert seg.segmento(_fila("/fuera", is_internal=False)) == SEGMENTO_EXTERNA
    assert Segmentador([]).segmento(_fila("/")) == "/ (home)"
    assert (seg.por_defecto, seg.externas) == (1, 1)


def test_y_o_no_con_campos():
    seg = Segmentador([{"nombre": "fichas_rotas", "si": {"todas": [
        {"ruta": "^/p/"},
        {"alguna": [{"campo": "status_code", "en": [404, 410]},
                    {"campo": "status_group", "igual": "5XX"}]},
    ]}}, {"nombre": "fichas", "si": {"todas": [{"ruta": "^/p/"},
                                               {"no": {"campo": "indexable", "igual": False}}]}}])
    assert seg.segmento(_fila("/p/a", status_code=404, status_group="4xx")) == "fichas_rotas"
    assert seg.segmento(_fila("/p/b", status_code=503, status_group="5xx")) == "fichas_rotas"
    assert seg.segmento(_fila("/p/c")) == "fichas"
    assert seg.segmento(_fila("/p/d", indexable=False)) != "fichas"


def test_content_type_se_compara_sin_el_juego_de_caracteres():
    seg = Segmentador([{"nombre": "html", "si": {"campo": "content_type", "igual": "text/html"}}])
    assert seg.segmento(_fila("/a")) == "html"


def test_regla_a_medida_por_valor_y_numero():
    seg = Segmentador([
        {"nombre": "agotadas", "si": {"x": "stock", "contiene": "agotado"}},
        {"nombre": "caras", "si": {"x": "precio", "gte": 100}},
        {"nombre": "fichas", "si": {"x": "og_type", "igual": "product"}},
    ])
    assert seg.segmento(_fila("/a", x={"stock": _x(valor="Producto AGOTADO")})) == "agotadas"
    assert seg.segmento(_fila("/b", x={"precio": _x(valor="150 €", valor_num=150.0)})) == "caras"
    assert seg.segmento(_fila("/c", x={"og_type": _x(valor=" Product ")})) == "fichas"


def test_lo_que_no_se_sabe_no_casa_ni_negado_y_se_cuenta():
    """Una pagina sin fila de extraccion (bloqueada, fuera de `solo_urls`, censo
    anterior a la regla) no es «no es producto»: con `no` tambien queda fuera
    de la regla, y el resumen dice cuantas no se pudieron juzgar."""
    seg = Segmentador([
        {"nombre": "no_fichas", "si": {"no": {"x": "og_type", "igual": "product"}}},
    ])
    assert seg.segmento(_fila("/sin-fila", x=None)) == firma_de_ruta("/sin-fila")
    assert seg.segmento(_fila("/bloq", x={"og_type": _x(estado="pagina_bloqueada")})) != "no_fichas"
    assert seg.segmento(_fila("/web", x={"og_type": _x(valor="website")})) == "no_fichas"
    assert seg.desconocidas == [2]


def test_un_dato_desconocido_no_impide_que_casen_las_demas():
    seg = Segmentador([{"nombre": "blog", "si": {"alguna": [
        {"x": "tipo", "igual": "post"}, {"ruta": "^/blog/"}]}}])
    assert seg.segmento(_fila("/blog/a", x=None)) == "blog"
    seg = Segmentador([{"nombre": "post", "si": {"todas": [
        {"x": "tipo", "igual": "post"}, {"ruta": "^/blog/"}]}}])
    # Y con algo falso es falso aunque lo otro no se sepa.
    assert seg.segmento(_fila("/tienda/a", x=None)) != "post"
    assert seg.desconocidas == [0]


def test_null_en_un_campo_es_desconocido_pero_vacio_lo_pregunta():
    seg = Segmentador([
        {"nombre": "sin_profundidad", "si": {"campo": "click_depth", "vacio": True}},
        {"nombre": "cerca", "si": {"campo": "click_depth", "lte": 2}},
    ])
    assert seg.segmento(_fila("/a", click_depth=None)) == "sin_profundidad"
    assert seg.segmento(_fila("/b", click_depth=1)) == "cerca"
    seg = Segmentador([{"nombre": "en_sitemap", "si": {"no": {"campo": "in_sitemap", "igual": False}}}])
    assert seg.segmento(_fila("/c", in_sitemap=None)) != "en_sitemap"


def test_busqueda_es_y_sin_coincidencia_es_vacio_conocido():
    seg = Segmentador([
        {"nombre": "sin_gtm", "si": {"x": "gtm", "es": False}},
        {"nombre": "sin_precio", "si": {"x": "precio", "vacio": True}},
    ])
    assert seg.segmento(_fila("/a", x={"gtm": _x("sin_coincidencia", clase="busqueda")})) == "sin_gtm"
    assert seg.segmento(_fila("/b", x={"gtm": _x("ok", n=1, clase="busqueda"),
                                       "precio": _x("sin_coincidencia")})) == "sin_precio"


def test_estado_permite_preguntar_por_lo_no_evaluado():
    seg = Segmentador([{"nombre": "sin_evaluar", "si": {"x": "precio", "estado": ["no_evaluada", "error"]}}])
    assert seg.segmento(_fila("/a", x=None)) == "sin_evaluar"
    assert seg.segmento(_fila("/b", x={"precio": _x("ok", valor="1")})) != "sin_evaluar"


@pytest.mark.parametrize("reglas,parte", [
    ([{"nombre": "/raro", "patron": "a"}], "no puede empezar"),
    ([{"nombre": "a"}], "hace falta `patron` o `si`"),
    ([{"nombre": "a", "patron": "(sin cerrar"}], "segmentos[0].patron: regex no valida"),
    ([{"nombre": "a", "si": {"campo": "titulo", "igual": "x"}}], "campo desconocido"),
    ([{"nombre": "a", "si": {"campo": "indexable", "contiene": "x"}}], "no vale para indexable"),
    ([{"nombre": "a", "si": {"campo": "status_code", "gte": "200"}}], "necesita un numero"),
    ([{"nombre": "a", "si": {"x": "precio", "igual": "1"}}], "no tiene la regla a medida 'precio'"),
    ([{"nombre": "a", "si": {"todas": [{"ruta": "a"}, {"ruta": "b", "url": "c"}]}}],
     "segmentos[0].si.todas[1]: lleva exactamente una"),
    ([{"nombre": "a", "si": {"no": {"no": {"no": {"no": {"no": {"no": {"ruta": "a"}}}}}}}}], "anidado"),
    ([{"nombre": "a", "si": {"x": "sku", "es": True}}], "solo vale para una busqueda"),
])
def test_validar_dice_que_y_donde(reglas, parte):
    errores = validar(reglas, {"sku": "extractor"})
    assert any(parte in e for e in errores), errores


def test_validar_acepta_un_arbol_correcto():
    assert validar([{"nombre": "fichas", "patron": "^/p/", "si": {"todas": [
        {"x": "precio", "gte": 1}, {"no": {"campo": "status_group", "en": ["4xx", "5xx"]}}]}}],
        {"precio": "extractor"}) == []


def test_jobconfig_valida_los_segmentos_contra_sus_reglas_a_medida():
    pytest.importorskip("pydantic")
    from pydantic import ValidationError

    from api.schemas import JobConfig

    regla = {"nombre": "fichas", "si": {"x": "og_type", "igual": "product"}}
    with pytest.raises(ValidationError, match="no tiene la regla a medida"):
        JobConfig.model_validate({"segmentos": [regla]})
    cfg = JobConfig.model_validate({
        "segmentos": [regla, {"nombre": "legal", "patron": "aviso-legal"}],
        "custom_extraction": {"og_type": {"tipo": "css", "expresion": "meta[property='og:type']",
                                          "devuelve": "atributo", "atributo": "content"}},
    })
    assert [r.nombre for r in cfg.segmentos] == ["fichas", "legal"]


def test_un_muro_no_es_la_pagina_sus_campos_no_se_saben():
    """Decision 103: el codigo y las palabras de un challenge son del muro. Una
    condicion sobre ellos no casa (ni negada); la ruta si se sabe."""
    seg = Segmentador([
        {"nombre": "finas", "si": {"campo": "content_word_count", "lte": 50}},
        {"nombre": "no_finas", "si": {"no": {"campo": "content_word_count", "lte": 50}}},
        {"nombre": "fichas", "patron": "^/p/"},
    ])
    muro = _fila("/p/a", content_word_count=3, response_class="challenge_waf")
    assert seg.segmento(muro) == "fichas"
    assert seg.segmento(_fila("/b", content_word_count=3, response_class="contenido")) == "finas"
