"""Unit tests for main-content extraction (``extract_main_content`` and the
boilerplate stripper behind it).

Regression context: the stripper removed EVERY ``<header>``/``<footer>``,
so pages whose content lives in a hero ``<header>`` inside ``<main>`` (Astro,
Next, Nuxt component libraries; workoholics.es was the trigger) were stored
as two words of content.  lxml's ``remove()`` also dropped tail text, and
the fallback threshold compared against whole-body word count.

Only ``parsel`` + ``lxml`` are needed; trafilatura is optional (the
end-to-end test passes through the fallback when it is absent).
"""

from __future__ import annotations

from parsel import Selector

from seo_crawler import extractors as ex


def sel(html: str) -> Selector:
    return Selector(text=html)


_SITE = (
    "<header id='top'><nav><a href='/'>Home</a></nav></header>"
    "<main>{main}</main>"
    "<footer>© 2026 Acme. All rights reserved.</footer>"
)


# ---------------------------------------------------------------------------
# Landmark-aware stripping
# ---------------------------------------------------------------------------
def test_strip_keeps_header_inside_main():
    """Regression: hero <header> inside <main> held the whole page."""
    html = _SITE.format(
        main="<header class='hero'><h1>Contact</h1>"
             "<p>Looking for a digital agency to build your idea? Write to us "
             "and we will get back within a day.</p></header>"
             "<address>Main St 2, Bilbao</address>"
    )
    out = ex._strip_boilerplate_html(html)
    assert "Looking for a digital agency" in out
    assert "Main St 2" in out
    assert "Home" not in out            # site header still stripped
    assert "All rights reserved" not in out


def test_strip_removes_page_level_header_and_footer():
    html = _SITE.format(main="<p>Body copy here.</p>")
    out = ex._strip_boilerplate_html(html)
    assert "Body copy here" in out
    assert "<nav" not in out
    assert "<footer" not in out
    assert "id=\"top\"" not in out


def test_strip_keeps_card_headers_and_article_footers():
    html = _SITE.format(
        main="<article><header><h2>Card title</h2></header><p>Card body.</p>"
             "<footer>By Ana, 3 min read</footer></article>"
    )
    out = ex._strip_boilerplate_html(html)
    assert "Card title" in out
    assert "By Ana" in out


def test_strip_respects_explicit_aria_roles_inside_main():
    html = _SITE.format(
        main="<div role='banner'>Promo banner</div>"
             "<div role='navigation'>Sub nav</div><p>Real text.</p>"
    )
    out = ex._strip_boilerplate_html(html)
    assert "Promo banner" not in out
    assert "Sub nav" not in out
    assert "Real text" in out


def test_strip_keeps_body_level_hero_header_with_h1_and_paragraph():
    html = (
        "<body><header class='hero'><h1>Product</h1>"
        "<p>" + "word " * 25 + "</p></header><main><p>More.</p></main></body>"
    )
    out = ex._strip_boilerplate_html(html)
    assert "word word" in out


def test_strip_drops_body_level_header_with_h1_logo_only():
    """WordPress-style <h1 class="site-title"> in the banner is not a hero."""
    html = "<body><header><h1>Acme</h1><nav>x</nav></header><main><p>Body.</p></main></body>"
    out = ex._strip_boilerplate_html(html)
    assert "Acme" not in out
    assert "Body." in out


def test_strip_preserves_tail_text_of_removed_nodes():
    """lxml's remove() drops the element's tail; ' world' used to vanish."""
    html = "<main><p>Hello <span class='share-button'>x</span> world</p></main>"
    out = ex._strip_boilerplate_html(html)
    assert "Hello" in out
    assert "world" in out
    assert ">x<" not in out


def test_strip_drops_video_fallback_text():
    html = ("<main><video><p>Your browser does not support video.</p></video>"
            "<p>Ok.</p></main>")
    out = ex._strip_boilerplate_html(html)
    assert "does not support" not in out
    assert "Ok." in out


def test_strip_custom_selectors_still_apply():
    html = _SITE.format(main="<div class='promo-x'>Buy now</div><p>Text.</p>")
    out = ex._strip_boilerplate_html(html, extra_selectors=[".promo-x"])
    assert "Buy now" not in out
    assert "Text." in out


# ---------------------------------------------------------------------------
# Text flattening / dedupe
# ---------------------------------------------------------------------------
def test_dedupe_lines_collapses_marquee_repeats():
    text = "\n".join(["Contact"] * 12 + ["Digital by nature", "nature"] * 5 + ["Unique line"])
    assert ex._dedupe_lines(text) == "Contact\nDigital by nature\nnature\nUnique line"


def test_dedupe_lines_keeps_paragraph_breaks():
    assert ex._dedupe_lines("a\n\n\nb\n\n") == "a\n\nb"


def test_fallback_text_is_block_aware_and_scoped_to_main():
    html = _SITE.format(
        main="<h1>Title</h1><p>First <b>para</b>.</p><ul><li>one</li><li>two</li></ul>"
    )
    out = ex._fallback_extract_text(sel(html))
    assert out == "Title\nFirst para.\none\ntwo"


def test_fallback_text_uses_body_when_no_main():
    out = ex._fallback_extract_text(sel("<body><nav>menu</nav><p>Only text.</p></body>"))
    assert out == "Only text."


def test_should_fall_back_thresholds():
    assert ex._should_fall_back("a b", 10) is False            # container too small
    assert ex._should_fall_back("a b c", 100) is True          # kept 3 of 100 words
    assert ex._should_fall_back(" ".join(["w"] * 90), 100) is False


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------
def test_extract_main_content_recovers_hero_page():
    """Hero-in-main page must yield its prose, never the old two-word result."""
    html = _SITE.format(
        main="<header class='hero'><h1>Contact</h1>"
             "<p>Looking for a digital agency with experience to bring your "
             "idea or project to life? We would love to read you and meet "
             "you in person soon.</p></header>"
             "<address>Main St 2, 48001 Bilbao</address>"
             "<p>+34 944 000 000</p>"
    )
    out = ex.extract_main_content(sel(html), word_count=60)
    assert out
    assert "digital agency" in out
    assert "Bilbao" in out
    assert "All rights reserved" not in out


def test_extract_main_content_markdown_recovers_hero_page():
    html = _SITE.format(
        main="<header class='hero'><h1>Contact</h1>"
             "<p>Looking for a digital agency with experience to bring your "
             "idea or project to life? We would love to read you and meet "
             "you in person soon.</p></header>"
             "<address>Main St 2, 48001 Bilbao</address>"
    )
    out = ex.extract_main_content_markdown(sel(html), word_count=60)
    assert out
    assert "digital agency" in out


def test_dedupe_lines_does_not_accumulate_blanks_around_dropped_repeats():
    md = "# T\n\nT\n\nT\n\nT\n\nBody"
    assert ex._dedupe_lines(md) == "# T\n\nT\n\nBody"

def test_hero_outside_main_is_kept():
    """Landing con <section> hero hermano de <main>: el h1 y el claim cuentan."""
    html = (
        "<html><body>"
        "<section class='landingpage'><figure><figcaption>"
        "<h1>Aerotermia GeniaAir:</h1><h2>Disenada para ti.</h2>"
        "<p>Una nueva generacion. Tres gamas. Flexibilidad sin limites.</p>"
        "</figcaption></figure></section>"
        "<main class='main__content'><p>"
        + ("Texto largo del cuerpo de la pagina con suficientes palabras para "
           "que el extractor lo tome como contenido principal. " * 6)
        + "</p></main></body></html>"
    )
    out = ex.extract_main_content(sel(html))
    assert out
    assert "Aerotermia GeniaAir" in out
    assert "Tres gamas" in out
    assert "Texto largo del cuerpo" in out


def test_hero_inside_main_is_not_duplicated():
    html = (
        "<html><body><main><header class='hero'><h1>Titular unico</h1>"
        "<p>Claim de la pagina.</p></header><p>"
        + ("Cuerpo con texto suficiente para que el extractor lo considere "
           "contenido principal de verdad. " * 6)
        + "</p></main></body></html>"
    )
    out = ex.extract_main_content(sel(html))
    assert out
    assert out.lower().count("titular unico") == 1


def test_hero_inside_main_discarded_by_trafilatura_is_recovered():
    """Plantilla ``<main><article><div class=hero><h1>``: el hero esta DENTRO
    del contenedor y aun asi trafilatura lo tira, sin que la comprobacion de
    share salte porque conserva el resto del articulo."""
    html = (
        "<html><body><main><article>"
        "<div class='hero hero--post'><div class='hero__content'>"
        "<h1 class='hero__title'>Centro de XPERIENCIA Saunier Duval</h1>"
        "<a class='category' href='/proyectos'>Proyectos</a>"
        "</div></div>"
        "<div class='post__body'><h2>Predicar con el ejemplo</h2><p>"
        + ("Cuerpo del reportaje con palabras de sobra para que el extractor "
           "lo tome por contenido principal y no se dispare el fallback. " * 8)
        + "</p></div></article></main></body></html>"
    )
    out = ex.extract_main_content(sel(html))
    assert out
    assert out.splitlines()[0] == "Centro de XPERIENCIA Saunier Duval"
    assert out.lower().count("centro de xperiencia saunier duval") == 1
    assert "Predicar con el ejemplo" in out


def test_hero_present_only_mid_sentence_still_counts_as_missing():
    """El titular reaparece a media frase en el cuerpo: eso no es tenerlo."""
    html = (
        "<html><body><main><article>"
        "<div class='hero'><h1>Centro de XPERIENCIA</h1></div>"
        "<div class='post__body'><p>"
        + ("Este parrafo menciona el Centro de XPERIENCIA a media frase y "
           "sigue con mucho mas texto de relleno para el extractor. " * 8)
        + "</p></div></article></main></body></html>"
    )
    out = ex.extract_main_content(sel(html))
    assert out
    assert out.splitlines()[0] == "Centro de XPERIENCIA"


# ---------------------------------------------------------------------------
# Metricas de texto: word_count, text_ratio y texto oculto (#27)
# ---------------------------------------------------------------------------


def test_word_count_japones_no_da_una_palabra():
    """Sin espacios entre palabras, split() daba 1 y TODA pagina CJK era thin."""
    html = (
        "<html><body>\n     <div>\n"
        "         東京は日本の首都です。人口は約1400万人です。\n"
        "     </div>\n </body></html>"
    )
    assert ex.extract_word_count(sel(html)) > 10


def test_word_count_chino_y_tailandes():
    assert ex.contar_palabras("北京是中国的首都") == 8
    assert ex.contar_palabras("กรุงเทพมหานคร") == 13
    # El latino sigue contando por tokens, no por caracteres.
    assert ex.contar_palabras("Madrid es la capital") == 4


def test_contar_palabras_ignora_los_signos_sueltos():
    """Los separadores suelen ir en su propio nodo: cada uno sumaba 1."""
    assert ex.contar_palabras("Zapatillas — € | Nike") == 2
    assert ex.contar_palabras("1.299 €") == 1
    assert ex.contar_palabras("") == 0
    assert ex.contar_palabras(None) == 0


def test_text_ratio_no_mide_la_indentacion():
    """Los nodos de solo espacios contaban como texto: 67,6 % en vez de 5 %."""
    html = (
        "<html><head><title>x</title></head><body>\n"
        + " " * 12 + "<div class='wrap'>\n"
        + " " * 16 + "<div class='inner'>\n"
        + " " * 20 + "<p>Hola mundo.</p>\n"
        + " " * 16 + "</div>\n" + " " * 12 + "</div>\n" + " " * 8 + "</body></html>"
    )
    visible = ex.extract_visible_text(sel(html))
    assert visible == "Hola mundo."
    assert ex.compute_text_ratio(html, visible) < 8.0


def test_texto_oculto_fuera_de_word_count():
    html = (
        "<html><body>"
        "<p>Texto visible real</p>"
        "<p hidden>palabra oculta uno</p>"
        '<div style="display: none">otra oculta aqui</div>'
        '<span style="visibility:hidden">tambien oculta</span>'
        "<svg><title>icono de carrito</title></svg>"
        '<iframe src="x">Tu navegador no soporta iframes</iframe>'
        "</body></html>"
    )
    s = sel(html)
    assert ex.extract_word_count(s) == 3
    visible = ex.extract_visible_text(s)
    for fuera in ("oculta", "carrito", "iframes"):
        assert fuera not in visible


def test_hidden_until_found_si_es_contenido():
    """El navegador lo revela al buscar en la pagina y Google lo indexa."""
    html = '<html><body><p hidden="until-found">esto si cuenta</p></body></html>'
    assert ex.extract_word_count(sel(html)) == 3


def test_elementos_en_linea_no_parten_la_palabra():
    """Unir los nodos con espacio daba "Zapa tillas": 2 palabras donde hay 1."""
    html = "<html><body><h2>Zapa<span>tillas</span></h2><p>Pre<span>cio</span></p></body></html>"
    s = sel(html)
    assert ex.extract_word_count(s) == 2
    assert ex.extract_visible_text(s).splitlines() == ["Zapatillas", "Precio"]


def test_la_cola_de_un_nodo_oculto_si_cuenta():
    """El texto que va DESPUES del nodo oculto se pinta: no puede desaparecer."""
    html = "<html><body><p><span hidden>oculto</span>visible de verdad</p></body></html>"
    assert ex.extract_visible_text(sel(html)) == "visible de verdad"


def test_contenido_principal_sin_el_modal_oculto():
    """Login, carrito y promos van en modales ocultos: no son el contenido."""
    html = (
        "<html><body><main><article>"
        "<h1>Ficha del producto</h1>"
        '<div class="modal" style="display:none"><p>Accede a tu cuenta</p>'
        "<p>El login con Facebook ha sido deshabilitado</p></div>"
        "<p>" + ("Descripcion real del producto con texto de sobra. " * 10) + "</p>"
        "</article></main></body></html>"
    )
    out = ex.extract_main_content(sel(html))
    assert out
    assert "Descripcion real del producto" in out
    assert "Facebook" not in out
    assert "Accede a tu cuenta" not in out


# ---------------------------------------------------------------------------
# El limpiador de plantilla no puede borrar la pagina (#27, alta)
# ---------------------------------------------------------------------------

_CUERPO = "Texto real del articulo con palabras de sobra para ser contenido. " * 6


def _pagina(main_html: str, body_attr: str = "") -> str:
    return f"<html><body {body_attr}><main>{main_html}</main></body></html>"


def test_una_clase_de_estado_en_el_body_no_borra_la_pagina():
    """`cookie-bar-active` dice que la barra esta abierta, no que la pagina sea
    la barra. Casaba por subcadena sobre cualquier elemento, body incluido, y
    dejaba la pagina a 0 palabras."""
    for clase in ("cookie-bar-active", "has-lightbox", "newsletter-popup-open",
                  "subscriptions-page", "signup-page"):
        html = _pagina(f"<h1>Titular</h1><p>{_CUERPO}</p>", f"class='{clase}'")
        out = ex.extract_main_content(sel(html)) or ""
        assert len(out.split()) > 50, f"{clase} dejo {len(out.split())} palabras"


def test_la_pagina_legal_no_se_borra_a_si_misma():
    """`id="cookie-policy"` en la pagina de politica de cookies, o
    `privacy-notice-content` en la de privacidad, ES el contenido."""
    html = ("<html><body><main id='cookie-policy'><h1>Politica de cookies</h1>"
            f"<p>{_CUERPO}</p></main></body></html>")
    assert "Texto real" in (ex.extract_main_content(sel(html)) or "")
    html = _pagina(f"<h1>Privacidad</h1><div class='privacy-notice-content'><p>{_CUERPO}</p></div>")
    assert "Texto real" in (ex.extract_main_content(sel(html)) or "")
    html = _pagina(f"<h1>Planes</h1><div class='subscription-plans'><p>{_CUERPO}</p></div>")
    assert "Texto real" in (ex.extract_main_content(sel(html)) or "")


def test_la_barra_de_cookies_de_verdad_sigue_fuera():
    """La proteccion es por tamano: un banner real es una fraccion pequena."""
    html = ("<html><body><div class='cookie-banner'><p>Utilizamos cookies propias "
            "y de terceros. Aceptar cookies</p></div>"
            f"<main><h1>Titular</h1><p>{_CUERPO}</p></main></body></html>")
    out = ex.extract_main_content(sel(html)) or ""
    assert "cookies" not in out.lower()
    assert "Texto real" in out


def test_la_pagina_entera_dentro_de_un_form_no_desaparece():
    """ASP.NET WebForms envuelve TODO en <form id="aspnetForm">: esos sitios
    salian a 0 palabras."""
    html = (f"<html><body><form id='aspnetForm'><main><h1>Producto</h1><p>{_CUERPO}</p>"
            "</main></form></body></html>")
    assert "Texto real" in (ex.extract_main_content(sel(html)) or "")


def test_un_buscador_sigue_siendo_plantilla():
    html = (f"<html><body><main><h1>Ficha</h1><p>{_CUERPO}</p></main>"
            "<form action='/buscar'><input name='q'><button>Buscar en la web</button>"
            "</form></body></html>")
    out = ex.extract_main_content(sel(html)) or ""
    assert "Buscar en la web" not in out


def test_un_aside_dentro_del_articulo_es_contenido():
    """Un destacado o las especificaciones de un producto no son la barra
    lateral del sitio: misma regla de landmark que header/footer."""
    html = _pagina(f"<h1>Ficha</h1><p>{_CUERPO}</p>"
                   "<aside class='callout'><p>Dato importante dentro del articulo.</p></aside>")
    assert "Dato importante" in (ex.extract_main_content(sel(html)) or "")
    html = _pagina(f"<h1>Ficha</h1><p>{_CUERPO}</p>"
                   "<div role='complementary'><p>Especificaciones: 120x80 cm, 45 kg.</p></div>")
    assert "Especificaciones" in (ex.extract_main_content(sel(html)) or "")


def test_la_barra_lateral_del_sitio_sigue_fuera():
    html = (f"<html><body><main><h1>Ficha</h1><p>{_CUERPO}</p></main>"
            "<aside class='sidebar'><p>Lo mas leido de la semana en el blog, con "
            "bastante texto para que no sea un bloque corto.</p></aside></body></html>")
    assert "Lo mas leido" not in (ex.extract_main_content(sel(html)) or "")


def test_las_clases_de_utilidad_de_los_frameworks_ocultan():
    """`d-none` es display:none en todas las versiones de Bootstrap. En un
    cliente real el megamenu entero viajaba asi en el contenido de cada pagina,
    con bloques de depuracion incluidos."""
    html = _pagina("<h1>Ficha</h1><div class='d-none debugging'>null</div>"
                   "<div class='mega-menu hidden'><a>Novela romantica</a></div>"
                   f"<p>{_CUERPO}</p>")
    out = ex.extract_main_content(sel(html)) or ""
    assert "null" not in out
    assert "Novela romantica" not in out
    assert "Texto real" in out
    # Pero una variante por anchura oculta solo a partir de ese ancho: es visible.
    html = _pagina(f"<h1>Ficha</h1><div class='d-md-none'>Menu movil visible</div><p>{_CUERPO}</p>")
    assert "Menu movil visible" in ex.extract_visible_text(sel(html))


# ---------------------------------------------------------------------------
# Deduplicar no puede desalinear una tabla ni cambiar de ficha un precio
# ---------------------------------------------------------------------------


def test_la_tabla_comparativa_conserva_todas_sus_celdas():
    """De 8 celdas `Si` quedaba 1 y las filas salian desplazadas: el informe
    decia lo contrario de lo que pone la pagina."""
    filas = "".join(f"<tr><td>Fila {f}</td><td>Si</td><td>Si</td><td>No</td></tr>"
                    for f in range(4))
    html = _pagina(f"<table><tr><th>Caracteristica</th><th>Basico</th><th>Pro</th></tr>{filas}</table>")
    texto = ex._block_text(sel(html).css("main")[0].root)
    assert texto.count("Si") == 8
    assert texto.count("No") == 4
    for f in range(4):
        assert f"Fila {f}" in texto


def test_las_fichas_conservan_cada_precio():
    items = "".join(f"<li><h3>Producto {i}</h3><span>19,99 €</span></li>" for i in range(8))
    html = _pagina(f"<ul>{items}</ul>")
    texto = ex._block_text(sel(html).css("main")[0].root)
    assert texto.count("19,99 €") == 8


def test_la_marquesina_sigue_colapsando():
    html = _pagina("<div>" + "<div><span>Diseño</span></div>" * 6 + "</div>"
                   f"<p>{_CUERPO}</p>")
    texto = ex._block_text(sel(html).css("main")[0].root)
    assert texto.count("Diseño") == 1


def test_el_ciclo_de_dos_lineas_sigue_colapsando():
    """`A B A B …` de una animacion de letras: una vuelta y fuera."""
    texto = "\n".join(["Digital by nature", "nature"] * 5 + ["Linea unica"])
    assert ex._dedupe_lines(texto) == "Digital by nature\nnature\nLinea unica"
