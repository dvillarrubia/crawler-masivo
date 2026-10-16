"""Extraccion y busqueda a medida (#34, decision 104): la funcion pura y el
esquema.

Corre en los DOS trabajos de CI: la misma funcion la usan el spider y la API
(`POST /api/extraccion/probar`), asi que tiene que funcionar con las
dependencias de las dos imagenes.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("lxml")
pytest.importorskip("parsel")
pytest.importorskip("regex")

from lxml import html as lhtml  # noqa: E402

from shared import extraccion_a_medida as xm  # noqa: E402

FICHA = """<html><head><title>Zapatilla Run</title>
<script>(function(w){w.dataLayer=[];})(window,'GTM-AB12CD');</script></head><body>
<h1>Zapa<span>tillas</span> Run</h1>
<span class="precio">1.299,00 €</span>
<span class="precio-viejo">€19,95</span>
<div class="sku" data-sku="SKU-001">Ref. SKU-001<span class="d-none">texto oculto</span></div>
<p class="stock">En stock</p>
<ul class="tallas"><li>38</li><li>39</li><li>40</li></ul>
<a href="/carrito" class="cta">Comprar</a>
</body></html>"""


def _aplicar(ce=None, cs=None, html=FICHA, **kw):
    comp = xm.compilar(ce or {}, cs or {})
    raiz = lhtml.fromstring(html)
    kw.setdefault("titulo", (raiz.findtext(".//title") or "").strip())
    filas = xm.aplicar(raiz, html, comp, **kw)
    return {f["nombre"]: f for f in filas}


# ---------------------------------------------------------------------------
# Tipos de regla
# ---------------------------------------------------------------------------
def test_css_texto_atributo_y_pseudo_elementos():
    f = _aplicar({
        "precio": {"tipo": "css", "expresion": ".precio", "valor": "numero"},
        "sku": {"tipo": "css", "expresion": ".sku", "devuelve": "atributo", "atributo": "data-sku"},
        "sku2": {"tipo": "css", "expresion": ".sku::attr(data-sku)"},
        "cta": {"tipo": "css", "expresion": "a.cta::text"},
    })
    assert f["precio"]["estado"] == "ok" and f["precio"]["valor_num"] == 1299.0
    assert f["precio"]["valor"] == "1.299,00 €"
    assert f["sku"]["valor"] == f["sku2"]["valor"] == "SKU-001"
    assert f["cta"]["valor"] == "Comprar"


def test_xpath_elementos_atributos_y_escalares():
    f = _aplicar({
        "h1": {"tipo": "xpath", "expresion": "//h1"},
        "href": {"tipo": "xpath", "expresion": "//a/@href"},
        "tallas": {"tipo": "xpath", "expresion": "count(//ul[@class='tallas']/li)",
                   "cuantos": "recuento"},
        "stock": {"tipo": "xpath", "expresion": "string(//p[@class='stock'])"},
    })
    assert f["h1"]["valor"] == "Zapatillas Run"           # Zapa<span>tillas es UNA palabra
    assert f["href"]["valor"] == "/carrito"
    assert f["tallas"]["valor_num"] == 3 and f["tallas"]["n"] == 3
    assert f["stock"]["valor"] == "En stock"


def test_el_texto_de_un_elemento_no_lleva_lo_oculto():
    f = _aplicar({"sku": {"tipo": "css", "expresion": ".sku"}})
    assert f["sku"]["valor"] == "Ref. SKU-001"


def test_regex_con_grupo_y_sin_grupo():
    f = _aplicar({
        "gtm": {"tipo": "regex", "expresion": r"GTM-([A-Z0-9]+)"},
        "gtm_entero": {"tipo": "regex", "expresion": r"GTM-[A-Z0-9]+", "grupo": 0},
        "sin": {"tipo": "regex", "expresion": r"GTM-[A-Z0-9]+"},
    })
    assert f["gtm"]["valor"] == "AB12CD"                  # 1 si hay grupos
    assert f["gtm_entero"]["valor"] == "GTM-AB12CD"
    assert f["sin"]["valor"] == "GTM-AB12CD"              # 0 si no los hay


def test_primero_todos_y_recuento():
    f = _aplicar({
        "primera": {"tipo": "css", "expresion": ".tallas li"},
        "todas": {"tipo": "css", "expresion": ".tallas li", "cuantos": "todos", "max_valores": 2},
        "cuantas": {"tipo": "css", "expresion": ".tallas li", "cuantos": "recuento"},
    })
    assert f["primera"]["valor"] == "38" and f["primera"]["n"] == 3
    assert not f["primera"]["truncado"]
    assert f["todas"]["valores"] == ["38", "39"] and f["todas"]["valor"] == "38 | 39"
    assert f["todas"]["n"] == 3 and f["todas"]["truncado"]
    assert f["cuantas"]["valor_num"] == 3


def test_recorte_de_caracteres():
    f = _aplicar({"h1": {"tipo": "css", "expresion": "h1", "max_caracteres": 4}})
    assert f["h1"]["valor"] == "Zapa" and f["h1"]["truncado"]


@pytest.mark.parametrize("texto,sep,esperado", [
    ("1.299,00 €", None, 1299.0),
    ("1,234.56", None, 1234.56),
    ("€19,95", None, 19.95),
    ("1.234.567", None, 1234567.0),
    ("1.234", None, None),          # ambiguo: mil doscientos o uno coma dos
    ("1,299", None, None),
    ("1.234", ",", 1234.0),         # con el separador declarado ya no lo es
    ("1.234", ".", 1.234),
    ("Precio: 12 €", None, 12.0),
    ("-3,5", None, -3.5),
    ("sin precio", None, None),
])
def test_a_numero(texto, sep, esperado):
    assert xm.a_numero(texto, sep) == esperado


def test_numero_ambiguo_es_conversion_fallida():
    html = FICHA.replace("1.299,00 €", "1.234")
    f = _aplicar({"precio": {"tipo": "css", "expresion": ".precio", "valor": "numero"}}, html=html)
    assert f["precio"]["estado"] == "conversion_fallida"
    assert f["precio"]["valor"] == "1.234" and f["precio"]["valor_num"] is None


# ---------------------------------------------------------------------------
# Busquedas
# ---------------------------------------------------------------------------
def test_busqueda_contiene_y_no_contiene():
    f = _aplicar(cs={
        "gtm": {"patron": "gtm-"},                       # sin caja por defecto
        "sin_stock": {"patron": "sin stock"},
        "precio_re": {"modo": "regex", "patron": r"\d+,\d{2} €"},
    })
    assert f["gtm"]["estado"] == "ok" and f["gtm"]["n"] == 1
    assert "GTM-AB12CD" in f["gtm"]["valor"] and len(f["gtm"]["valor"]) <= xm.CONTEXTO
    assert f["sin_stock"]["estado"] == "sin_coincidencia" and f["sin_stock"]["n"] == 0
    assert f["precio_re"]["estado"] == "ok"


def test_busqueda_sobre_texto_y_acotada_con_dentro_de():
    f = _aplicar(cs={
        "gtm_texto": {"patron": "GTM-", "fuente": "texto"},
        "oculto": {"patron": "texto oculto", "fuente": "texto"},
        "en_sku": {"patron": "SKU", "dentro_de": ".sku"},
        "en_stock": {"patron": "SKU", "dentro_de": ".stock"},
    }, texto="Zapatillas Run\n1.299,00 €\nRef. SKU-001\nEn stock")
    assert f["gtm_texto"]["estado"] == "sin_coincidencia"   # el script no se pinta
    assert f["oculto"]["estado"] == "sin_coincidencia"
    assert f["en_sku"]["estado"] == "ok" and f["en_stock"]["estado"] == "sin_coincidencia"


def test_una_fuente_recortada_sin_coincidencias_es_incompleta(monkeypatch):
    monkeypatch.setattr(xm, "FUENTE_MAX", 100)
    f = _aplicar(cs={"cta": {"patron": "Comprar"}})
    assert f["cta"]["estado"] == "incompleta"                 # NO es «no contiene»


def test_solo_urls_deja_sin_fila():
    regla = {"precio": {"tipo": "css", "expresion": ".precio", "solo_urls": r"^/p/"}}
    assert "precio" not in _aplicar(regla, ruta="/blog/x")
    assert _aplicar(regla, ruta="/p/1")["precio"]["estado"] == "ok"


def test_un_muro_de_waf_no_es_una_pagina():
    muro = "<html><head><title>Just a moment...</title></head><body>GTM-X</body></html>"
    f = _aplicar({"precio": {"tipo": "css", "expresion": ".precio"}},
                 {"gtm": {"patron": "GTM-"}}, html=muro)
    assert {v["estado"] for v in f.values()} == {"pagina_bloqueada"}


# ---------------------------------------------------------------------------
# Tiempo
# ---------------------------------------------------------------------------
CATASTROFICA = r"(a|aa)+$"   # `(a+)+$` lo optimiza el motor de `regex`: este no


def test_una_regex_catastrofica_se_corta_y_luego_se_desactiva():
    html = "<html><body><p>" + "a" * 40 + "b</p></body></html>"
    comp = xm.compilar({"mala": {"tipo": "regex", "expresion": CATASTROFICA},
                        "buena": {"tipo": "css", "expresion": "p"}}, {})
    p = xm.Presupuesto()
    raiz = lhtml.fromstring(html)
    t0 = time.perf_counter()
    estados = []
    for _ in range(5):
        filas = {f["nombre"]: f for f in xm.aplicar(raiz, html, comp, presupuesto=p)}
        estados.append(filas["mala"]["estado"])
        assert filas["buena"]["estado"] == "ok"
    assert time.perf_counter() - t0 < 1.0
    assert estados == ["tiempo_agotado"] * 3 + ["desactivada"] * 2
    assert p.recien_desactivadas == ["mala"]


def test_el_presupuesto_por_pagina_corta_las_reglas_que_faltan():
    comp = xm.compilar({f"r{i}": {"tipo": "css", "expresion": "li"} for i in range(5)}, {})
    raiz = lhtml.fromstring(FICHA)
    filas = xm.aplicar(raiz, FICHA, comp, presupuesto=xm.Presupuesto(ms_pagina=-1))
    assert filas[0]["estado"] == "presupuesto_pagina"
    assert {f["estado"] for f in filas} == {"presupuesto_pagina"}


def test_un_fallo_de_una_regla_es_una_fila_error():
    # `foo()` compila y solo falla al evaluarse sobre un <h1>; el documento de
    # prueba de la validacion no tiene ninguno, asi que pasa.
    comp = xm.compilar({"r": {"tipo": "xpath", "expresion": "//h1[foo()]"}}, {})
    filas = xm.aplicar(lhtml.fromstring(FICHA), FICHA, comp)
    assert filas[0]["estado"] == "error" and "XPath" in filas[0]["detalle"]


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------
def _errores(ce=None, cs=None, **kw):
    return xm.validar(ce or {}, cs or {}, **kw)


def test_re_test_de_exslt_se_rechaza():
    e = _errores({"r": {"tipo": "xpath", "expresion": "//a[re:test(@href, 'x')]"}})
    assert e and "custom_extraction.r" in e[0] and "prefijo" in e[0]


def test_errores_con_su_ruta():
    e = _errores({
        "css_malo": {"tipo": "css", "expresion": "div[["},
        "xp_mala": {"tipo": "xpath", "expresion": "//a["},
        "re_mala": {"tipo": "regex", "expresion": "(a"},
        "abs": {"tipo": "xpath", "expresion": "//a[//b]"},
        "attr": {"tipo": "css", "expresion": "a", "devuelve": "atributo"},
        "grupo": {"tipo": "regex", "expresion": "a(b)", "grupo": 3},
        "fuente": {"tipo": "css", "expresion": "a", "fuente": "texto"},
    }, {"vacia": {"modo": "regex", "patron": "x*"}})
    texto = "\n".join(e)
    for ruta in ("custom_extraction.css_malo: selector CSS", "custom_extraction.xp_mala: XPath",
                 "custom_extraction.re_mala: regex", "custom_extraction.abs: XPath con //",
                 "custom_extraction.attr: atributo", "custom_extraction.grupo: grupo 3",
                 "custom_extraction.fuente:", "custom_search.vacia: la regex casa con la cadena vacia"):
        assert ruta in texto, (ruta, texto)


def test_nombres_y_contenido():
    assert _errores({"Precio": {"tipo": "css", "expresion": "a"}})
    assert "comparten" in _errores({"x": {"tipo": "css", "expresion": "a"}},
                                   {"x": {"patron": "a"}})[0]
    assert "extract_page_content" in _errores(
        {"x": {"tipo": "regex", "expresion": "a", "fuente": "contenido"}},
        extract_page_content=False)[0]


def test_tope_de_bytes_por_pagina():
    reglas = {f"r{i}": {"tipo": "css", "expresion": "li", "cuantos": "todos",
                        "max_valores": 100, "max_caracteres": 1000} for i in range(2)}
    e = _errores(reglas, max_urls=60000)
    assert e and "195 kB" in e[0] and "64 kB" in e[0] and "GB" in e[0]


def test_la_firma_no_cambia_con_la_etiqueta_ni_con_los_valores_por_defecto():
    a = {"tipo": "css", "expresion": ".precio", "valor": "numero"}
    b = {**a, "etiqueta": "Precio", "cuantos": "primero", "max_caracteres": 1000}
    assert xm.firma(a) == xm.firma(b)
    assert xm.firma(a) != xm.firma({**a, "expresion": ".precio-viejo"})
    assert xm.firma({"patron": "x"}, "busqueda") != xm.firma({"patron": "x"}, "extractor")


# ---------------------------------------------------------------------------
# Esquema (api/schemas.py) y herencia (shared/clientes.py)
# ---------------------------------------------------------------------------
# La imagen del crawler no lleva Pydantic: estos corren en el trabajo de la API.
def _pydantic():
    return pytest.importorskip("pydantic")


def test_los_valores_por_defecto_coinciden_con_los_del_esquema():
    _pydantic()
    from api.schemas import BusquedaAMedida, ExtractorAMedida

    e = ExtractorAMedida(tipo="css", expresion="a").model_dump()
    assert {k: e[k] for k in xm.DEFECTO_EXTRACTOR} == xm.DEFECTO_EXTRACTOR
    b = BusquedaAMedida(patron="a").model_dump()
    assert {k: b[k] for k in xm.DEFECTO_BUSQUEDA} == xm.DEFECTO_BUSQUEDA


def test_jobconfig_rechaza_con_el_nombre_de_la_regla():
    pydantic = _pydantic()
    from api.schemas import JobConfig

    with pytest.raises(pydantic.ValidationError) as exc:
        JobConfig.model_validate({"custom_extraction": {
            "precio": {"tipo": "xpath", "expresion": "//a[re:test(@href,'x')]"}}})
    assert "custom_extraction.precio" in str(exc.value)
    with pytest.raises(pydantic.ValidationError):
        JobConfig.model_validate({"custom_extraction": {"precio": {"tipo": "css"}}})
    with pytest.raises(pydantic.ValidationError) as exc:
        JobConfig.model_validate({"max_urls": 60000, "custom_extraction": {
            f"r{i}": {"tipo": "css", "expresion": "li", "cuantos": "todos",
                      "max_valores": 100, "max_caracteres": 1000} for i in range(2)}})
    assert "64 kB" in str(exc.value)
    with pytest.raises(pydantic.ValidationError) as exc:
        JobConfig.model_validate({"extraction": {"extract_page_content": False},
                                  "custom_search": {"s": {"patron": "a", "fuente": "contenido"}}})
    assert "custom_search.s" in str(exc.value)


def test_config_parcial_acepta_quitar_una_regla_heredada():
    _pydantic()
    from api.schemas import validar_config_parcial

    assert validar_config_parcial({"custom_extraction": {"precio": None}}) == {
        "custom_extraction": {"precio": None}}
    p = validar_config_parcial({"custom_search": {"gtm": {"patron": "GTM-"}, "viejo": None}})
    assert p == {"custom_search": {"gtm": {"patron": "GTM-"}, "viejo": None}}
    with pytest.raises(ValueError):
        # Una regla parcial no es una regla: no se «retoca» un campo de otra
        validar_config_parcial({"custom_extraction": {"precio": {"solo_urls": "^/p/"}}})


def test_la_herencia_suma_por_nombre_y_sustituye_cada_regla_entera():
    _pydantic()
    from api.schemas import JobConfig
    from shared.clientes import fusionar_config

    cliente = {"custom_extraction": {
        "precio": {"tipo": "css", "expresion": ".precio", "solo_urls": "^/p/"},
        "sku": {"tipo": "css", "expresion": ".sku"},
        "marca": {"tipo": "css", "expresion": ".marca"}}}
    perfil = {"custom_extraction": {
        "precio": {"tipo": "css", "expresion": ".price"},   # reescrita: sin solo_urls
        "marca": None}}                                      # quitada
    job = {"custom_search": {"gtm": {"patron": "GTM-"}}}
    fusion = fusionar_config(cliente, perfil, job)
    assert set(fusion["custom_extraction"]) == {"precio", "sku"}
    assert fusion["custom_extraction"]["precio"] == {"tipo": "css", "expresion": ".price"}
    cfg = JobConfig.model_validate(fusion).model_dump()
    assert cfg["custom_extraction"]["precio"]["solo_urls"] is None
    assert cfg["custom_search"]["gtm"]["patron"] == "GTM-"
