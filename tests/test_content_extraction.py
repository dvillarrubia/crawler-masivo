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
