"""Lo que el HTML declara de si mismo (decision 108): los extractores puros y
las reglas de `shared/metadatos_html.py`, ejecutados caso a caso."""

from __future__ import annotations

import pytest
from parsel import Selector

from seo_crawler.extractors import (
    charset_declarado,
    extract_meta,
    extract_meta_refresh_target,
    extract_redireccion_js,
)
from shared.metadatos_html import (
    diagnostico_charset,
    charset_de_content_type,
    forma_de_href,
    idioma_principal,
    motivo_viewport,
    normalizar_charset,
)

PAGINA = "https://x.com/a/b"


def _sel(head: str = "", body: str = "", html_attrs: str = "") -> Selector:
    return Selector(text=f"<html{html_attrs}><head>{head}</head><body>{body}</body></html>")


# -- Viewport -----------------------------------------------------------------

def test_sin_viewport_es_cero_y_no_none():
    meta = extract_meta(_sel("<title>t</title>"), base_url=PAGINA)
    # 0 es «medido y no hay»; NULL queda para los censos anteriores.
    assert meta["viewport_count"] == 0
    assert meta["viewport"] is None


def test_viewport_sin_distinguir_mayusculas_ni_espacios():
    meta = extract_meta(_sel('<meta name=" ViewPort " content="width=device-width,  initial-scale=1">'),
                        base_url=PAGINA)
    assert meta["viewport_count"] == 1
    assert meta["viewport"] == "width=device-width, initial-scale=1"
    assert motivo_viewport(meta["viewport"]) is None


def test_dos_viewports_se_cuentan_y_vale_el_primero():
    meta = extract_meta(_sel('<meta name="viewport" content="width=1024">'
                             '<meta name="viewport" content="width=device-width">'),
                        base_url=PAGINA)
    assert meta["viewport_count"] == 2
    assert meta["viewport"] == "width=1024"


def test_viewport_con_content_vacio_se_guarda_vacio():
    meta = extract_meta(_sel('<meta name="viewport" content="">'), base_url=PAGINA)
    assert meta["viewport"] == ""
    assert motivo_viewport("") == "vacio"


@pytest.mark.parametrize("content, motivo", [
    ("width=device-width, initial-scale=1", None),
    # Accesibilidad, no adaptacion al movil: no es un viewport incorrecto.
    ("width=device-width, initial-scale=1, user-scalable=no", None),
    ("width=device-width, maximum-scale=1", None),
    ("initial-scale=1", None),
    ("WIDTH = Device-Width", None),
    ("width=device-width; initial-scale=1", None),
    ("width=1024", "ancho_fijo"),
    ("width=980px, initial-scale=1", "ancho_fijo"),
    ("user-scalable=no", "sin_device_width"),
    ("   ", "vacio"),
    (None, "vacio"),
])
def test_motivo_viewport(content, motivo):
    assert motivo_viewport(content) == motivo


# -- <html lang> --------------------------------------------------------------

@pytest.mark.parametrize("attrs, esperado", [
    (' lang="es-ES"', "es-ES"),
    (' xml:lang="ca"', "ca"),
    (' lang="es" xml:lang="en"', "es"),
    (' lang=""', ""),
    (' lang="   "', ""),
    ("", None),
])
def test_html_lang(attrs, esperado):
    assert extract_meta(_sel(html_attrs=attrs), base_url=PAGINA)["html_lang"] == esperado


def test_html_lang_recortado_a_la_columna():
    lang = extract_meta(_sel(html_attrs=f' lang="{"x" * 80}"'), base_url=PAGINA)["html_lang"]
    assert len(lang) == 35


@pytest.mark.parametrize("etiqueta, principal", [
    ("es-ES", "es"), ("ES", "es"), ("zh-Hant-TW", "zh"), ("sr-Latn", "sr"),
    ("pt_BR", "pt"), ("", None), (None, None),
])
def test_idioma_principal(etiqueta, principal):
    assert idioma_principal(etiqueta) == principal


# -- Forma del canonical y de la og:image -------------------------------------

@pytest.mark.parametrize("href, forma", [
    ("https://x.com/a", "absoluta"),
    ("HTTP://x.com/a", "absoluta"),
    ("/a", "relativa_a_raiz"),
    ("a", "relativa"),
    ("../a", "relativa"),
    ("?p=2", "relativa"),
    ("//cdn.x.com/a", "relativa_al_protocolo"),
    ("https://x.com/a#top", "con_fragmento"),
    ("/#/producto/1", "con_fragmento"),
    ("", "vacia"),
    (None, None),
])
def test_forma_de_href(href, forma):
    assert forma_de_href(href) == forma


def test_canonical_relativo_guarda_el_crudo_y_el_resuelto():
    meta = extract_meta(_sel('<link rel="canonical" href="/pagina">'), base_url=PAGINA)
    assert meta["canonical_href"] == "https://x.com/pagina"
    assert meta["canonical_crudo"] == "/pagina"
    assert forma_de_href(meta["canonical_crudo"]) == "relativa_a_raiz"


def test_canonical_con_fragmento_se_conserva_crudo():
    meta = extract_meta(_sel('<link rel="canonical" href="https://x.com/a/b#top">'),
                        base_url=PAGINA)
    assert meta["canonical_crudo"] == "https://x.com/a/b#top"
    assert forma_de_href(meta["canonical_crudo"]) == "con_fragmento"


def test_canonical_crudo_es_el_del_head_que_se_usa():
    meta = extract_meta(_sel('<link rel="canonical" href="relativa">',
                             '<link rel="canonical" href="https://x.com/body">'),
                        base_url=PAGINA)
    assert meta["canonical_crudo"] == "relativa"
    assert extract_meta(_sel("<title>t</title>"), base_url=PAGINA)["canonical_crudo"] is None


def test_og_presentes_relativa_y_twitter_image():
    meta = extract_meta(_sel('<meta property="og:title" content="T">'
                             '<meta property="og:image" content="/img/og.jpg">'
                             '<meta name="twitter:image" content="img/tw.jpg">'),
                        base_url=PAGINA)
    assert meta["og_title"] == "T"
    assert meta["og_image"] == "https://x.com/img/og.jpg"
    assert meta["og_image_forma"] == "relativa_a_raiz"
    assert meta["twitter_image"] == "https://x.com/a/img/tw.jpg"


def test_og_absoluta_y_og_ausente():
    meta = extract_meta(_sel('<meta property="og:image" content="https://cdn.x.com/og.jpg">'),
                        base_url=PAGINA)
    assert meta["og_image_forma"] == "absoluta"
    vacia = extract_meta(_sel("<title>t</title>"), base_url=PAGINA)
    assert vacia["og_title"] is None and vacia["og_image"] is None
    assert vacia["og_image_forma"] is None and vacia["twitter_image"] is None


# -- Redireccion por JavaScript ------------------------------------------------

def _js(codigo: str, atributos: str = "", envoltura: str = "{}") -> str | None:
    script = f"<script{atributos}>{codigo}</script>"
    return extract_redireccion_js(_sel(envoltura.format(script)), PAGINA, PAGINA)


# Los ocho casos de la guia de #150 (repro3.py): con las regex de FreeCrawl,
# cuatro de los cinco que no son una pagina puente salian como redireccion.
@pytest.mark.parametrize("codigo, destino", [
    ('window.location.replace("https://x.com/nueva");', "https://x.com/nueva"),
    ('function irLogin(){ window.location.href = "/login"; }', None),
    ('if (navigator.language.indexOf("en")===0) { location.replace("/en/"); }', None),
    ('setTimeout(function(){ location.href="/gracias"; }, 5000);', None),
    ('document.getElementById("b").addEventListener("click",()=>{location.assign("/carrito")});',
     None),
    ('location.hash = "#top";', None),
    ('location.href = "/buscar?q=" + q;', None),
    ("location = '/destino'\n", "https://x.com/destino"),
])
def test_redireccion_js_casos_de_la_guia(codigo, destino):
    assert _js(codigo) == destino


@pytest.mark.parametrize("codigo, destino", [
    # Minificado en una linea: la funcion no corre, la sentencia de fuera si.
    ("var a=1;function f(){location=\"/no\"};f();document.location.href=\"/si\";var b=/it's/;",
     "https://x.com/si"),
    # Una regex con comillas delante no abre una cadena falsa.
    ('var r=/"/g; location.replace("/tr")', "https://x.com/tr"),
    ('if (a) location.href="/x";', None),                     # if sin llaves
    ('if(a){x()}else location.href="/e"', None),
    ('const f = () => location.href = "/f";', None),
    ('if (location.href == "/x") {}', None),                  # comparacion
    ("var s = \"location.href='/s'\";", None),                # dentro de una cadena
    ('// location.href="/c"\nvar x=1;', None),               # comentario
    ('/* location.href="/c" */', None),
    ('location.href="/a/b"', None),                          # la propia pagina
    ('location.href="javascript:void(0)"', None),
    ('location.href=""', None),
    ('location.href = "https://otro.com/"', "https://otro.com/"),
    ('foo()\nlocation.href="/c"', None),  # tras `)` puede ser un `if (...)`: no se afirma
    ('var x = 1\nlocation.href="/d"', "https://x.com/d"),   # ASI tras una sentencia
])
def test_redireccion_js_otros_casos(codigo, destino):
    assert _js(codigo) == destino


def test_redireccion_js_solo_scripts_que_se_ejecutan():
    codigo = 'location.replace("/puente");'
    assert _js(codigo) == "https://x.com/puente"
    assert _js(codigo, ' type="text/javascript"') == "https://x.com/puente"
    assert _js(codigo, ' type="module"') == "https://x.com/puente"
    assert _js('{"location": "/x"}', ' type="application/ld+json"') is None
    assert _js(codigo, ' type="text/template"') is None
    assert _js(codigo, ' src="/app.js"') is None
    assert _js(codigo, envoltura="<noscript>{}</noscript>") is None
    assert _js(codigo, envoltura="<template>{}</template>") is None
    # Un comentario condicional de IE es un comentario: no hay <script>.
    assert _js(codigo, envoltura="<!--[if lt IE 9]>{}<![endif]-->") is None


def test_redireccion_js_en_el_body_y_con_base():
    sel = Selector(text='<html><head><base href="https://x.com/es/"></head>'
                        '<body><p>Redirigiendo...</p><script>location.href="nueva"</script>'
                        "</body></html>")
    assert extract_redireccion_js(sel, "https://x.com/es/", PAGINA) == "https://x.com/es/nueva"


# -- Meta refresh a javascript: no es una redireccion (B3 de #150) -------------

@pytest.mark.parametrize("content, destino", [
    ("0; url=javascript:void(0)", None),
    ("0;url=data:text/html,hola", None),
    ("0; url=mailto:a@x.com", None),
    ("0;url=/dest", "https://x.com/dest"),
    ("1;https://otro.com/", "https://otro.com/"),
])
def test_meta_refresh_solo_a_http(content, destino):
    sel = _sel(f'<meta http-equiv="refresh" content="{content}">')
    assert extract_meta_refresh_target(sel, PAGINA) == destino


# -- Charset -------------------------------------------------------------------

def _diagnostico(body: bytes, content_type: str):
    d = charset_declarado(body)
    return diagnostico_charset(charset_de_content_type(content_type),
                               d["charset_meta"], d["charset_bom"])


def test_cabecera_utf8_y_meta_latin1_es_incoherente():
    body = b'<html><head><meta charset="iso-8859-1"><title>t</title></head></html>'
    assert charset_declarado(body)["charset_meta"] == "cp1252"
    assert _diagnostico(body, "text/html; charset=utf-8") == ("incoherente", "cabecera")


def test_un_meta_despues_del_byte_1024_no_cuenta():
    body = (b"<html><head><title>t</title><!--" + b"x" * 1500 + b'-->'
            b'<meta charset="utf-8"></head></html>')
    assert charset_declarado(body)["charset_meta"] == ""
    assert _diagnostico(body, "text/html") == ("ausente", None)


def test_bom_utf8_manda_sobre_la_cabecera_latin1():
    body = b"\xef\xbb\xbf<html><head><title>t\xc3\xa9</title></head></html>"
    d = charset_declarado(body)
    assert d["charset_bom"] == "utf-8"
    assert d["cuerpo_solo_ascii"] is False
    assert _diagnostico(body, "text/html; charset=iso-8859-1") == ("incoherente", "bom")


def test_latin1_y_windows_1252_son_la_misma():
    body = b'<html><head><meta http-equiv="Content-Type" content="text/html; charset=windows-1252">'
    assert _diagnostico(body, "text/html; charset=latin1") == (None, "cabecera")
    assert normalizar_charset("ISO-8859-1") == normalizar_charset("windows-1252")
    assert normalizar_charset("utf8") == normalizar_charset('"UTF-8"') == "utf-8"
    assert normalizar_charset("x-raro") == "x-raro"


def test_sin_nada_declarado_y_solo_ascii():
    body = b"<html><head><title>t</title></head></html>"
    d = charset_declarado(body)
    assert d == {"charset_meta": "", "charset_bom": None, "cuerpo_solo_ascii": True}
    assert _diagnostico(body, "text/html") == ("ausente", None)


def test_charset_de_content_type():
    assert charset_de_content_type('text/html; charset="UTF-8"') == "utf-8"
    assert charset_de_content_type("text/html;charset=Shift_JIS") == "shift_jis"
    assert charset_de_content_type("text/html") is None
    assert charset_de_content_type(None) is None
