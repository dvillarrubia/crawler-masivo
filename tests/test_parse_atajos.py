"""Los atajos de CPU de `parse` (#284) no cambian NADA de lo que se extrae.

`SeoSpider.parse` se ejecuta dos veces sobre las mismas paginas: con el camino
de antes (sin caches, la posicion de cada enlace con un XPath por ancestro y
extruct pidiendo siempre microdata y RDFa) y con el de ahora. Cada item tiene
que salir igual, en el mismo orden: pagina, contenido y markdown, texto tras
el h1, enlaces con su posicion, encabezados, recursos, datos estructurados y
las peticiones que se encolan.

No se guarda una salida "esperada": trafilatura y extruct no van fijados en
`requirements.txt` y una foto de su salida caducaria sola con cada version. El
camino de antes vive aqui, como oraculo, y se compara en la misma ejecucion.

Paginas (`tests/fixtures/paginas/`, ver su README): dos del sitio sintetico del
banco de pruebas (megamenu, pie, JSON-LD, hreflang), la portada del BOE, el
articulo de Barcelona de Wikipedia (1,5 MB, con RDFa de verdad) y una hecha a
mano con los bordes de cada atajo. `PARSE_ATAJOS_PAGINAS=<carpeta>` anade los
`.html` de esa carpeta (paginas de terceros que no pueden ir en un repo publico).
"""

from __future__ import annotations

import gzip
import os
import re
import sys
from collections import Counter

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGINAS = os.path.join(RAIZ, "tests", "fixtures", "paginas")

BORDES = """<!doctype html><html lang="es"><head><title>Ficha  de prueba</title>
<meta name="description" content="Una ficha con todos los bordes">
<link rel="canonical" href="https://x.com:443/tienda/./ficha?b=2&a=1">
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Product",
 "name": "Botas de monte", "offers": {"@type": "Offer", "price": "89.90", "priceCurrency": "EUR"}}</script>
<script>if (typeof window === "undefined") {}</script>
</head><body class="has-sidebar ast-header-sticky">
<div id="cookie-banner" class="cookie-consent">Aceptar cookies <a href="/cookies">info</a></div>
<header class="site-header"><a href="/">Inicio</a><a href="HTTPS://X.COM/tienda/../tienda/">Tienda</a></header>
<nav role="navigation"><main><a href="/mega/1">Mega 1</a><a href="/mega/2">Mega 2</a></main></nav>
<div class="NoJsNavigation-styles__NoJsListItemStyled"><a href="/camel">Camel</a></div>
<main>
 <article>
  <header><h1>Botas de monte </h1><a href="/autor">Autor</a></header>
  <p>89,90 € · En stock · Envio en 24 h</p>
  <div ITEMSCOPE itemtype="https://schema.org/Offer"><span itemprop="price">89.90</span></div>
  <div vocab="https://schema.org/" TYPEOF = "Review"><span property="reviewBody">Muy buenas</span></div>
  <span property="og:title">sin tipo</span>
  <aside class="sidebar"><a href="/especificaciones">Especificaciones</a></aside>
  <p>Unas botas de monte impermeables con suela de goma, pensadas para rutas largas
  y terreno mojado. Pesan 600 gramos el par y llevan membrana transpirable.</p>
  <div class="promo-bloque"><a href="/oferta">Oferta</a></div>
  <p hidden>texto oculto que no cuenta</p>
  <a href="/relacionada?utm_source=x">Relacionada</a><a href="/relacionada?utm_source=x">Otra vez</a>
  <footer><a href="/etiqueta/monte">monte</a></footer>
 </article>
</main>
<aside id="sidebar"><a href="/lateral">Lateral</a></aside>
<footer class="site-footer"><a href="/aviso-legal">Aviso legal</a><a href="javascript:void(0)">Nada</a>
<span onclick="location.href='/onclick'">onclick</span></footer>
</body></html>"""


def _paginas():
    salida = [("https://x.com/tienda/ficha", BORDES.encode("utf-8"))]
    for nombre in sorted(os.listdir(PAGINAS)):
        if nombre.endswith(".html.gz"):
            with gzip.open(os.path.join(PAGINAS, nombre), "rb") as f:
                salida.append((f"https://x.com/{nombre[:-8]}", f.read()))
    extra = os.environ.get("PARSE_ATAJOS_PAGINAS")
    for nombre in sorted(os.listdir(extra)) if extra else ():
        if nombre.endswith(".html"):
            with open(os.path.join(extra, nombre), "rb") as f:
                salida.append((f"https://x.com/{nombre[:-5]}", f.read()))
    return salida


# -- El camino de antes, tal cual estaba (el oraculo) -------------------------

def _clasificar_original(ex, node):
    tag = (node.xpath("name()").get() or "").lower()
    if tag in ("nav", "header", "footer"):
        return tag, True
    if tag == "aside":
        return "sidebar", True
    if tag in ("main", "article"):
        return "content+", True
    role = (node.attrib.get("role", "") or "").strip().lower()
    if role in ex._POSITION_ROLES:
        kind = ex._POSITION_ROLES[role]
        return ("content+" if kind == "content" else kind), True
    raw_cls = node.attrib.get("class", "") or ""
    raw_id = (node.attrib.get("id", "") or "").strip()
    cls, node_id = raw_cls.lower(), raw_id.lower()
    tokens = ex._hint_tokens(raw_cls + " " + raw_id)
    for kind, words in ex._POSITION_TOKENS.items():
        if tokens & words:
            return kind, False
    if node_id in ex._SIDEBAR_IDS:
        return "sidebar", False
    class_tokens = set(cls.split())
    if class_tokens & ex._CONTENT_STRONG:
        return "content+", False
    if class_tokens & ex._CONTENT_WEAK or node_id in ex._CONTENT_IDS:
        return "content", False
    return None, False


def _posicion_original(ex, a_selector, cache=None):
    ancestors = [
        n for n in a_selector.xpath("ancestor::*")
        if (n.xpath("name()").get() or "").lower() not in ("html", "body")
    ]
    kinds = [_clasificar_original(ex, n) for n in reversed(ancestors)]
    for i, (kind, _structural) in enumerate(kinds):
        if kind is None:
            continue
        outer = kinds[i + 1:]
        if kind == "nav":
            return kind
        if kind in ("sidebar", "header", "footer"):
            article_scoped = False
            for okind, _ in outer:
                if okind == "content+":
                    article_scoped = True
                    break
                if okind in ex._TEMPLATE_KINDS:
                    break
            if article_scoped:
                continue
            return kind
        if any(ok in ex._TEMPLATE_KINDS and st for ok, st in outer):
            continue
        return "content"
    return "content"


def _camino_original(monkeypatch, ex):
    """Deja `extractors` (y quien importo sus funciones) como antes de #284."""
    cacheadas = {ex.normalize_url: ex.normalize_url.__wrapped__,
                 ex.compute_url_hash: ex.compute_url_hash.__wrapped__}
    for modulo in list(sys.modules.values()):
        for nombre, valor in list(getattr(modulo, "__dict__", {}).items()):
            try:
                original = cacheadas.get(valor)
            except Exception:  # noqa: BLE001 -- objetos sin hash
                continue
            if original is not None:
                monkeypatch.setattr(modulo, nombre, original)
    monkeypatch.setattr(ex, "_strip_de_pagina", lambda html, promo, extra: ex._strip_boilerplate_html(
        html, strip_promo=promo, extra_selectors=list(extra) or None))
    monkeypatch.setattr(ex, "_trafilatura_de_pagina", ex._trafilatura_de_pagina.__wrapped__)
    monkeypatch.setattr(ex, "_detect_link_position", lambda a, cache=None: _posicion_original(ex, a))
    siempre = re.compile("")
    monkeypatch.setattr(ex, "_HAY_MICRODATA", siempre)
    monkeypatch.setattr(ex, "_HAY_RDFA", siempre)


# -- Comparar ------------------------------------------------------------------

_ANONIMO = re.compile(r"_:N[0-9a-f]{32}")


def _normalizar(items):
    """Cada item como texto comparable, sin lo que cambia de una vuelta a otra.

    El RDFa sale en orden distinto y con ids de nodo anonimo al azar en cada
    lectura, tambien con el codigo de antes (lo hace rdflib): de el se compara
    cuantos items de cada tipo hay. Todo lo demas, entero y en orden.
    """
    from scrapy import Request

    orden, rdfa = [], Counter()
    for it in items:
        if isinstance(it, Request):
            orden.append(("REQ", it.url, it.meta.get("depth"), it.priority))
            continue
        d = dict(it)
        for k in ("crawled_at", "last_crawled_at", "response_time_ms"):
            d.pop(k, None)
        if type(it).__name__ == "StructuredDataItem" and d.get("format") == "rdfa":
            rdfa[", ".join(sorted((d.get("schema_type") or "").split(", ")))] += 1
            continue
        orden.append((type(it).__name__, _ANONIMO.sub("_:X", repr(sorted(d.items())))))
    return orden, rdfa


@pytest.fixture
def entorno():
    pytest.importorskip("scrapy")
    pytest.importorskip("trafilatura")
    pytest.importorskip("extruct")
    import shared.database

    shared.database.SessionLocal = lambda: None
    from seo_crawler import extractors as ex
    from seo_crawler.spiders.seo_spider import SeoSpider

    return ex, SeoSpider


def _parse(SeoSpider, url, body, extraction):
    from scrapy.http import HtmlResponse, Request

    sp = SeoSpider(job_id="t")
    sp._allowed_resource_types = {"html", "redirect"}
    sp._crawled_count = 0
    sp.allowed_hosts = {"x.com"}
    sp._crawl_subdomains = False
    sp.max_urls = None
    sp.max_depth = 10
    sp._max_url_length = 0
    sp._max_folder_depth = 0
    sp._follow_nofollow = False
    sp.render_js = False
    sp._extraction = dict(extraction)
    respuesta = HtmlResponse(url, status=200, body=body,
                             headers={b"Content-Type": b"text/html; charset=utf-8"},
                             request=Request(url, meta={"depth": 1, "download_latency": 0.01}))
    from seo_crawler.items import desplegar

    # Los hijos de cada tipo salen juntos (#284): se comparan fila a fila.
    return list(desplegar(sp.parse(respuesta)))


@pytest.mark.parametrize("extraction", [
    {"extract_page_content": True},
    {"extract_page_content": True, "strip_promo_blocks": False,
     "custom_boilerplate_selectors": [".promo-bloque", "#mw-navigation"]},
], ids=["por-defecto", "selectores-propios"])
@pytest.mark.parametrize("url,body", _paginas(), ids=lambda v: v.rsplit("/", 1)[-1] if isinstance(v, str) else "")
def test_parse_igual_que_antes(entorno, monkeypatch, url, body, extraction):
    ex, SeoSpider = entorno
    ahora = _parse(SeoSpider, url, body, extraction)
    with monkeypatch.context() as m:
        _camino_original(m, ex)
        antes = _parse(SeoSpider, url, body, extraction)

    orden_antes, rdfa_antes = _normalizar(antes)
    orden_ahora, rdfa_ahora = _normalizar(ahora)
    assert rdfa_ahora == rdfa_antes
    assert len(orden_ahora) == len(orden_antes)
    for i, (x, y) in enumerate(zip(orden_antes, orden_ahora)):
        assert y == x, f"item {i} distinto"
    tipos = Counter(t[0] for t in orden_ahora)
    assert tipos["PageItem"] == 1


# -- Los bordes de cada atajo --------------------------------------------------

def test_la_pagina_de_bordes_ejercita_cada_atajo(entorno):
    ex, SeoSpider = entorno
    items = _parse(SeoSpider, "https://x.com/tienda/ficha", BORDES.encode("utf-8"),
                   {"extract_page_content": True})
    datos = [dict(i) for i in items if type(i).__name__ == "StructuredDataItem"]
    formatos = Counter(d["format"] for d in datos)
    # ITEMSCOPE en mayusculas y `TYPEOF = ` con espacios tambien se leen.
    assert formatos["microdata"] >= 1
    assert any(d["format"] == "rdfa" and d["schema_type"] == "Review" for d in datos)
    posiciones = {dict(i)["link_position"] for i in items if type(i).__name__ == "LinkItem"}
    assert {"nav", "header", "footer", "sidebar", "content"} <= posiciones
    # El texto tras el h1 sale (la ficha tiene Product con precio).
    contenido = next(dict(i) for i in items if type(i).__name__ == "ContentItem")
    assert contenido["texto_tras_h1"].startswith("Botas de monte")


def test_sin_marcas_no_se_llama_a_extruct(entorno, monkeypatch):
    ex, _ = entorno
    import extruct

    llamadas = []
    monkeypatch.setattr(extruct, "extract", lambda *a, **k: llamadas.append(k["syntaxes"]) or {})
    html = '<html><head><script>typeof x === "y"</script></head><body><p>hola</p></body></html>'
    ex.extract_structured_data(html, "https://x.com/")
    assert llamadas == []
    ex.extract_structured_data(html.replace("<p>", "<p itemScope>"), "https://x.com/")
    ex.extract_structured_data(html.replace("<p>", '<p typeof="Thing">'), "https://x.com/")
    ex.extract_structured_data(html.replace("<p>", '<p VOCAB ="https://schema.org/">'), "https://x.com/")
    assert llamadas == [["microdata"], ["rdfa"], ["rdfa"]]


def test_la_cache_no_mezcla_selectores_ni_paginas(entorno):
    ex, _ = entorno
    html = BORDES
    con = ex._strip_de_pagina(html, True, (".promo-bloque",))
    sin = ex._strip_de_pagina(html, True, ())
    assert "promo-bloque" not in con and "promo-bloque" in sin
    assert ex._strip_de_pagina(html, True, (".promo-bloque",)) == ex._strip_boilerplate_html(
        html, extra_selectors=[".promo-bloque"])
    otra = html.replace("Botas de monte", "Zapatillas")
    assert "Zapatillas" in ex._trafilatura_de_pagina(otra, True, ())[0]
    assert "Zapatillas" not in (ex._trafilatura_de_pagina(html, True, ())[0] or "")


def test_el_texto_tras_h1_no_ensucia_lo_que_viene_despues(entorno):
    """`extract_texto_tras_h1` escribe una marca en su arbol (y quita el U+E000
    que trae la pagina): con la limpieza en cache, el siguiente que lea la
    misma pagina no puede ver nada de eso."""
    ex, _ = entorno
    from parsel import Selector

    sel = Selector(text=BORDES)
    antes = ex.extract_main_content(sel)
    assert ex.extract_texto_tras_h1(sel)
    assert ex.extract_main_content(sel) == antes
    assert ex._strip_de_pagina(sel.get(), True, ()) == ex._strip_boilerplate_html(sel.get())


def test_posicion_con_y_sin_cache(entorno):
    ex, _ = entorno
    from parsel import Selector

    enlaces = Selector(text=BORDES).css("a")
    cache: dict = {}
    con = [ex._detect_link_position(a, cache) for a in enlaces]
    assert con == [ex._detect_link_position(a) for a in enlaces]
    assert con == [_posicion_original(ex, a) for a in enlaces]


def test_un_url_roto_no_tira_la_microdata(entorno):
    """Lo encontro la revision: con las dos sintaxis en una llamada, una
    excepcion de pyRdfa por un URL malformado se llevaba tambien la microdata.
    Es lo unico que cambia en la salida: microdata que antes se perdia."""
    ex, _ = entorno
    producto = ('<div itemscope itemtype="https://schema.org/Product">'
                '<span itemprop="name">Bota</span></div>')
    for roto in ('<a href="http://[x/en">roto</a>', '<img src="http://[x/i.png">'):
        for rdfa in ("", '<div vocab="https://schema.org/" typeof="Person"><span property="name">Ana</span></div>'):
            html = f"<html><body>{roto}{rdfa}{producto}</body></html>"
            tipos = {(d["format"], d["schema_type"]) for d in ex.extract_structured_data(html, "https://x.com/")}
            assert ("microdata", "Product") in tipos, (roto, rdfa, tipos)


def test_el_tipo_rdf_escrito_entero_tambien_es_rdfa(entorno, monkeypatch):
    ex, _ = entorno
    html = ('<html><body><div about="#x" rel="http://www.w3.org/1999/02/22-rdf-syntax-ns#type" '
            'resource="http://schema.org/Person"></div></body></html>')
    assert ex._HAY_RDFA.search(html)
