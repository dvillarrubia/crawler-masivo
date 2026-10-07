"""Unit tests for the pure extraction helpers in ``seo_crawler.extractors``.

These are the functions that read HTML and produce the data the crawler
persists. Cases marked "regression" pin down bugs fixed in this branch so
they cannot silently come back.
"""

from __future__ import annotations

import pytest
from parsel import Selector

from seo_crawler import extractors as ex


def sel(html: str) -> Selector:
    return Selector(text=html)


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------
def test_normalize_url_drops_fragment():
    assert ex.normalize_url("https://e.com/a#frag") == "https://e.com/a"


def test_compute_url_hash_is_fragment_insensitive():
    a = ex.compute_url_hash("https://e.com/a#one")
    b = ex.compute_url_hash("https://e.com/a#two")
    assert a == b
    assert len(a) == 64  # sha-256 hex


def test_compute_url_hash_differs_for_different_urls():
    assert ex.compute_url_hash("https://e.com/a") != ex.compute_url_hash("https://e.com/b")


@pytest.mark.parametrize("code,group", [
    (200, "2xx"), (204, "2xx"), (301, "3xx"), (404, "4xx"),
    (500, "5xx"), (None, "unknown"), (600, "other"),
])
def test_compute_status_group(code, group):
    assert ex.compute_status_group(code) == group


def test_classify_resource_type_by_content_type():
    assert ex.classify_resource_type("text/html; charset=utf-8", "https://e.com/") == "html"
    assert ex.classify_resource_type("image/png", "https://e.com/x") == "image"
    assert ex.classify_resource_type("application/pdf", "https://e.com/x") == "pdf"


def test_classify_resource_type_by_extension_fallback():
    assert ex.classify_resource_type("", "https://e.com/style.css") == "css"
    assert ex.classify_resource_type(None, "https://e.com/app.js") == "js"
    assert ex.classify_resource_type("", "https://e.com/page") == "other"


def test_is_internal_url_matches_bare_and_www():
    hosts = {"example.com"}
    assert ex.is_internal_url("https://example.com/x", hosts) is True
    assert ex.is_internal_url("https://www.example.com/x", hosts) is True
    assert ex.is_internal_url("https://other.com/x", hosts) is False


@pytest.mark.parametrize("url,depth", [
    ("https://e.com/", 0),
    ("https://e.com/a", 1),
    ("https://e.com/a/b/c", 3),
    ("https://e.com/a/b/c/", 3),
])
def test_compute_folder_depth(url, depth):
    assert ex.compute_folder_depth(url) == depth


# ---------------------------------------------------------------------------
# _resolve / effective_base_url  (regression: relative-URL resolution)
# ---------------------------------------------------------------------------
def test_resolve_relative_and_absolute():
    assert ex._resolve("https://e.com/dir/page", "/x") == "https://e.com/x"
    assert ex._resolve("https://e.com/dir/page", "https://o.com/y") == "https://o.com/y"
    assert ex._resolve(None, "/x") == "/x"      # no base -> unchanged
    assert ex._resolve("https://e.com/", None) is None


def test_effective_base_url_honours_base_href():
    s = sel('<html><head><base href="/es/"></head><body></body></html>')
    assert ex.effective_base_url(s, "https://e.com/page") == "https://e.com/es/"


def test_effective_base_url_defaults_to_page_url():
    s = sel("<html><head></head><body></body></html>")
    assert ex.effective_base_url(s, "https://e.com/page") == "https://e.com/page"


# ---------------------------------------------------------------------------
# extract_meta  (regression: canonical resolved to absolute)
# ---------------------------------------------------------------------------
def test_extract_meta_resolves_relative_canonical():
    s = sel('<html><head><link rel="canonical" href="/producto/x"></head></html>')
    meta = ex.extract_meta(s, base_url="https://e.com/producto/x")
    assert meta["canonical_href"] == "https://e.com/producto/x"


def test_extract_meta_without_base_leaves_canonical_relative():
    s = sel('<html><head><link rel="canonical" href="/producto/x"></head></html>')
    meta = ex.extract_meta(s)
    assert meta["canonical_href"] == "/producto/x"


def test_extract_meta_basic_fields_and_lengths():
    s = sel(
        '<html><head><title> Hello World </title>'
        '<meta name="description" content="A desc">'
        '<meta property="og:image" content="/img.png">'
        "</head></html>"
    )
    meta = ex.extract_meta(s, base_url="https://e.com/p")
    assert meta["title"] == "Hello World"
    assert meta["title_len"] == len("Hello World")
    assert meta["meta_description"] == "A desc"
    assert meta["meta_description_len"] == len("A desc")
    assert meta["og_image"] == "https://e.com/img.png"  # resolved


# ---------------------------------------------------------------------------
# extract_links  (regression: no per-page dedup, follow, link_type)
# ---------------------------------------------------------------------------
def test_extract_links_keeps_duplicate_instances():
    s = sel('<a href="/x">one</a><a href="/x">two</a>')
    links = ex.extract_links(s, "https://e.com/", {"e.com"})
    assert len(links) == 2  # not deduped
    assert {l["anchor_text"] for l in links} == {"one", "two"}


def test_extract_links_nofollow_and_internal_flags():
    s = sel('<a href="https://e.com/a" rel="nofollow">a</a><a href="https://x.com/b">b</a>')
    links = ex.extract_links(s, "https://e.com/", {"e.com"})
    by_url = {l["url"]: l for l in links}
    internal = next(l for l in links if l["is_internal"])
    assert internal["follow"] is False
    external = next(l for l in links if not l["is_internal"])
    assert external["follow"] is True


def test_extract_links_skips_non_http_schemes():
    s = sel('<a href="mailto:a@e.com">m</a><a href="javascript:void(0)">j</a><a href="/ok">ok</a>')
    links = ex.extract_links(s, "https://e.com/", {"e.com"})
    assert len(links) == 1
    assert links[0]["url"].endswith("/ok")


def test_extract_links_link_type_classification():
    s = sel(
        '<a href="/img"><img src="a.png"></a>'          # image only
        '<a href="/imgtext"><img src="b.png">caption</a>'  # image + text
        '<a href="/plain">plain</a>'                     # hyperlink
    )
    links = {l["url"].rsplit("/", 1)[-1]: l for l in ex.extract_links(s, "https://e.com/", {"e.com"})}
    assert links["img"]["link_type"] == "image"
    assert links["imgtext"]["link_type"] == "image_text"
    assert links["plain"]["link_type"] == "hyperlink"


# ---------------------------------------------------------------------------
# _detect_link_position  (regression: nearest ancestor wins)
# ---------------------------------------------------------------------------
def test_link_position_nearest_ancestor_wins():
    # A nav nested inside a header: nearest semantic ancestor is <nav>.
    s = sel('<header><nav><a href="/x">L</a></nav></header>')
    a = s.css("a")[0]
    assert ex._detect_link_position(a) == "nav"


def test_link_position_footer_and_content():
    s_footer = sel('<footer><a href="/x">L</a></footer>')
    assert ex._detect_link_position(s_footer.css("a")[0]) == "footer"
    s_content = sel('<div><p><a href="/x">L</a></p></div>')
    assert ex._detect_link_position(s_content.css("a")[0]) == "content"


def test_link_position_by_class_hint():
    s = sel('<div class="site-sidebar"><a href="/x">L</a></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "sidebar"


def test_link_position_ignores_body_and_html_classes():
    # Astra/Elementor hang layout flags on <body>; they must not tag the page.
    s = sel('<body class="ast-header-sticky has-sidebar"><main><p><a href="/x">L</a></p></main></body>')
    assert ex._detect_link_position(s.css("a")[0]) == "content"


def test_link_position_tokens_not_substrings():
    s = sel('<div class="canvas-wrapper"><div class="unavailable"><a href="/x">L</a></div></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "content"
    s = sel('<div class="elementor-widget"><a href="/x">L</a></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "content"
    # Underscore-joined CMS classes still expose whole tokens.
    s = sel('<section class="portlet portlet_com_liferay_site_navigation_menu_web_portlet"><a href="/x">L</a></section>')
    assert ex._detect_link_position(s.css("a")[0]) == "nav"
    s = sel('<section class="tab-pane tab-footer-0-panel"><a href="/x">L</a></section>')
    assert ex._detect_link_position(s.css("a")[0]) == "footer"


def test_link_position_article_scoped_header_footer_is_content():
    s = sel('<article><header class="entry-header"><h1><a href="/x">Title</a></h1></header></article>')
    assert ex._detect_link_position(s.css("a")[0]) == "content"
    s = sel('<main><div class="card"><div class="card-footer"><a href="/x">L</a></div></div></main>')
    assert ex._detect_link_position(s.css("a")[0]) == "content"
    # ...but a real page footer outside main/article still wins.
    s = sel('<div><footer><a href="/x">L</a></footer></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "footer"


def test_link_position_aria_roles():
    s = sel('<div role="navigation"><a href="/x">L</a></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "nav"
    s = sel('<div role="contentinfo"><a href="/x">L</a></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "footer"
    s = sel('<div role="banner"><a href="/x">L</a></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "header"
    s = sel('<div class="site-header-wrap"><div role="main"><a href="/x">L</a></div></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "content"


def test_link_position_content_region_stops_outer_wrappers():
    # Outer wrapper carries "header" but the link sits in the content region.
    s = sel('<div class="site-header-wrapper"><div class="entry-content"><a href="/x">L</a></div></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "content"
    # Nav nested inside main (breadcrumbs) is still nav.
    s = sel('<main><nav class="breadcrumb"><a href="/x">L</a></nav></main>')
    assert ex._detect_link_position(s.css("a")[0]) == "nav"


def test_link_position_liferay_mega_menu_with_main_inside_nav():
    # Liferay builds the mega menu with <main> and .portlet-content inside <nav>.
    s = sel('<header class="header"><nav class="navbar"><div class="portlet-content">'
            '<main class="d-flex justify-content-between"><a href="/x">L</a></main>'
            '</div></nav></header>')
    assert ex._detect_link_position(s.css("a")[0]) == "nav"


def test_link_position_utility_classes_are_not_content_markers():
    s = sel('<footer><div class="d-flex justify-content-between"><a href="/x">L</a></div></footer>')
    assert ex._detect_link_position(s.css("a")[0]) == "footer"
    # A site-level #content wrapper does not demote a footer nested in it.
    s = sel('<div id="content"><footer class="site-footer"><a href="/x">L</a></footer></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "footer"


def test_link_position_camelcase_css_in_js_classes():
    # styled-components glue words together; the token must still be found.
    s = sel('<div class="NoJsNavigation-styles__NoJsListItemStyled-sc-a2077f0f-3 hKbV"><a href="/x">L</a></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "nav"
    s = sel('<div class="PageFooterStyled-sc-1"><a href="/x">L</a></div>')
    assert ex._detect_link_position(s.css("a")[0]) == "footer"


# ---------------------------------------------------------------------------
# extract_headings  (marca template/noscript/svg, ordering)
# ---------------------------------------------------------------------------
def test_extract_headings_order_and_flags_hidden():
    """Los de template/noscript se guardan MARCADOS, no se tiran en silencio:
    los enlaces y las imagenes de esos mismos elementos si se guardaban."""
    s = sel(
        "<h1>Title</h1>"
        "<template><h2>Tmpl</h2></template>"
        "<noscript><h3>NS</h3></noscript>"
        "<h2>Sub</h2>"
    )
    heads = ex.extract_headings(s)
    visibles = [(h["tag"], h["text"]) for h in heads if not h["oculto"]]
    assert visibles == [("h1", "Title"), ("h2", "Sub")]
    assert [(h["tag"], h["oculto"]) for h in heads] == [
        ("h1", False), ("h2", True), ("h3", True), ("h2", False)
    ]
    assert [h["position"] for h in heads] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# extract_hreflang  (href resolved to absolute)
# ---------------------------------------------------------------------------
def test_extract_hreflang_resolves_href():
    s = sel('<link rel="alternate" hreflang="es" href="/es"><link rel="alternate" hreflang="en" href="https://e.com/en">')
    out = ex.extract_hreflang(s, base_url="https://e.com/x")
    langs = {r["lang"]: r["href"] for r in out}
    assert langs["es"] == "https://e.com/es"
    assert langs["en"] == "https://e.com/en"


# ---------------------------------------------------------------------------
# extract_resources  (mixed content, srcset)
# ---------------------------------------------------------------------------
def test_extract_resources_detects_mixed_content():
    s = sel('<img src="http://e.com/a.png" alt="A" width="10" height="20">')
    res = ex.extract_resources(s, "https://e.com/page")
    assert len(res) == 1
    r = res[0]
    assert r["resource_type"] == "image"
    assert r["is_mixed_content"] is True
    assert r["alt_text"] == "A"
    assert r["width"] == 10 and r["height"] == 20


def test_extract_resources_srcset_first_url():
    s = sel('<img srcset="/a.png 1x, /b.png 2x">')
    res = ex.extract_resources(s, "https://e.com/")
    assert any(r["url"].endswith("/a.png") for r in res)


# ---------------------------------------------------------------------------
# meta refresh, security headers, indexability, text ratio, pixel widths
# ---------------------------------------------------------------------------
def test_extract_meta_refresh():
    s = sel('<meta http-equiv="refresh" content="0;url=/next">')
    assert ex.extract_meta_refresh(s) == "0;url=/next"


def test_detect_mixed_content_only_on_https_pages():
    s = sel('<img src="http://e.com/a.png"><script src="https://e.com/x.js"></script>')
    assert ex.detect_mixed_content(s, "https://e.com/p") == ["http://e.com/a.png"]
    assert ex.detect_mixed_content(s, "http://e.com/p") == []


def test_extract_security_headers_case_insensitive():
    headers = {"Strict-Transport-Security": "max-age=1", "content-security-policy": "default-src 'self'"}
    out = ex.extract_security_headers(headers)
    assert out["has_hsts"] is True
    assert out["has_csp"] is True
    assert out["has_x_frame_options"] is False


@pytest.mark.parametrize("status,robots,xrobots,canonical,page,expected_indexable,reason", [
    (200, None, None, None, "https://e.com/p", True, None),
    (404, None, None, None, "https://e.com/p", False, "4xx Client Error"),
    (500, None, None, None, "https://e.com/p", False, "5xx Server Error"),
    (200, "noindex", None, None, "https://e.com/p", False, "Noindex"),
    (200, None, "noindex", None, "https://e.com/p", False, "Noindex"),
    (200, None, None, "https://e.com/p", "https://e.com/p", True, None),          # self canonical
    (200, None, None, "https://e.com/other", "https://e.com/p", False, "Canonicalised"),
])
def test_compute_indexability_status(status, robots, xrobots, canonical, page, expected_indexable, reason):
    ok, why = ex.compute_indexability_status(status, robots, xrobots, canonical, page)
    assert ok is expected_indexable
    assert why == reason


def test_compute_text_ratio():
    assert ex.compute_text_ratio("", "abc") == 0.0
    assert ex.compute_text_ratio("a" * 100, "a" * 25) == 25.0


def test_pixel_width_title_wider_than_description_for_same_text():
    text = "Hello World"
    assert ex.estimate_title_pixel_width(text) > 0
    assert ex.estimate_description_pixel_width(text) < ex.estimate_title_pixel_width(text)
    assert ex._estimate_pixel_width("") == 0


# ---------------------------------------------------------------------------
# Real-world HTML robustness (second-pass audit)
# ---------------------------------------------------------------------------
def test_extract_meta_case_insensitive_names():
    # Old CMS/IIS markup uses capitalised meta names; SF/Google match them.
    s = sel(
        '<html><head>'
        '<meta name="Description" content="Cap desc">'
        '<meta name="ROBOTS" content="NOINDEX">'
        '</head></html>'
    )
    meta = ex.extract_meta(s)
    assert meta["meta_description"] == "Cap desc"
    assert "NOINDEX" in meta["meta_robots"]


def test_extract_meta_combines_multiple_robots_tags():
    # Directives from several robots meta tags combine (most restrictive wins
    # downstream) — the second tag's noindex must not be lost.
    s = sel(
        '<head><meta name="robots" content="index, follow">'
        '<meta name="robots" content="noindex"></head>'
    )
    meta = ex.extract_meta(s)
    assert "noindex" in meta["meta_robots"].lower()
    ok, reason = ex.compute_indexability_status(
        200, meta["meta_robots"], None, None, "https://e.com/p")
    assert ok is False and reason == "Noindex"


def test_title_ignores_inline_svg_title():
    s = sel(
        "<html><head><title>Real Title</title></head>"
        "<body><svg><title>SVG tooltip</title></svg></body></html>"
    )
    assert ex.extract_meta(s)["title"] == "Real Title"


def test_title_from_svg_not_used_when_no_head_title():
    s = sel("<html><body><svg><title>SVG tooltip</title></svg></body></html>")
    assert ex.extract_meta(s)["title"] is None


@pytest.mark.parametrize("robots,expected", [
    ("none", False),                 # Google shorthand for noindex,nofollow
    ("noindex nofollow", False),     # space-separated, no commas
    ("NOINDEX", False),              # uppercase
    ("max-snippet:-1, index", True), # unrelated directives stay indexable
])
def test_indexability_robots_token_parsing(robots, expected):
    ok, _ = ex.compute_indexability_status(200, robots, None, None, "https://e.com/p")
    assert ok is expected


def test_robots_tokens_helper():
    assert ex.robots_tokens("noindex, nofollow") == {"noindex", "nofollow"}
    assert ex.robots_tokens("noindex nofollow") == {"noindex", "nofollow"}
    assert ex.robots_tokens(None) == set()


# -- Sintaxis robots con separador invalido ---------------------------------
# La barra no es sintaxis valida y Google ignora lo que no reconoce: la pagina
# se indexa igual. Se reporta como problema de sintaxis, nunca se interpreta.

@pytest.mark.parametrize("robots", ["noindex/nofollow", "noindex|nofollow", "NOINDEX/NOFOLLOW"])
def test_robots_con_barra_no_saca_del_indice(robots):
    ok, _ = ex.compute_indexability_status(200, robots, None, None, "https://e.com/p")
    assert ok is True


def test_robots_bad_separators_marca_lo_que_se_pierde():
    [p] = ex.robots_bad_separators("noindex/nofollow")
    assert p["token"] == "noindex/nofollow"
    assert p["directivas"] == ["noindex", "nofollow"]
    assert p["ignoradas"] == ["noindex", "nofollow"]


def test_robots_bad_separators_index_follow_no_pierde_nada():
    # `index` y `follow` son el comportamiento por defecto: la sintaxis esta
    # mal, pero no hay ninguna intencion incumplida.
    [p] = ex.robots_bad_separators("index/follow")
    assert p["ignoradas"] == []


@pytest.mark.parametrize("valor", [
    None,
    "",
    "noindex, nofollow",                        # sintaxis correcta
    "noindex nofollow",                         # tolerada por Google
    "max-snippet:-1, max-image-preview:large",  # los dos puntos son validos
    "unavailable_after: 30/06/2025",            # barra en una fecha, no directivas
    "index",
])
def test_robots_bad_separators_no_da_falsos_positivos(valor):
    assert ex.robots_bad_separators(valor) == []


def test_robots_bad_separators_solo_la_parte_mala():
    # Un valor mixto: la coma parte bien, y solo el token pegado se reporta.
    problemas = ex.robots_bad_separators("noarchive, noindex/nofollow")
    assert [p["token"] for p in problemas] == ["noindex/nofollow"]


def test_extract_links_includes_area_maps():
    s = sel('<map><area href="/zone" alt="Zona norte"></map>')
    links = ex.extract_links(s, "https://e.com/", {"e.com"})
    assert len(links) == 1
    assert links[0]["url"].endswith("/zone")
    assert links[0]["anchor_text"] == "Zona norte"


def test_extract_links_skips_fragment_only():
    s = sel('<a href="#top">up</a><a href="/real#frag">real</a>')
    links = ex.extract_links(s, "https://e.com/page", {"e.com"})
    assert len(links) == 1
    assert links[0]["url"].endswith("/real")  # fragment dropped by normalize


def test_extract_links_page_nofollow_overrides_all():
    s = sel('<a href="/a">a</a><a href="/b" rel="nofollow">b</a>')
    links = ex.extract_links(s, "https://e.com/", {"e.com"}, page_nofollow=True)
    assert all(l["follow"] is False for l in links)
    # And without the flag, the plain link stays follow.
    links2 = ex.extract_links(s, "https://e.com/", {"e.com"})
    by_anchor = {l["anchor_text"]: l for l in links2}
    assert by_anchor["a"]["follow"] is True
    assert by_anchor["b"]["follow"] is False


def test_word_count_excludes_template():
    s = sel(
        "<body><p>one two three</p>"
        "<template><p>hidden words never rendered</p></template></body>"
    )
    assert ex.extract_word_count(s) == 3
    assert "hidden" not in ex.extract_visible_text(s)


# ---------------------------------------------------------------------------
# Normalizacion y enlaces que perdian paginas (issue #25)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,esperada", [
    ("https://x.com:443/a", "https://x.com/a"),
    ("http://x.com:80/a", "http://x.com/a"),
    ("https://x.com:8443/a", "https://x.com:8443/a"),
    ("https://x.com./a", "https://x.com/a"),
    ("https://x.com/a/../b/./c", "https://x.com/b/c"),
    ("https://x.com/a/..", "https://x.com/"),
])
def test_normalize_url_puerto_punto_final_y_segmentos(url, esperada):
    assert ex.normalize_url(url) == esperada


def test_normalize_url_no_toca_las_urls_normales():
    # El hash de las URLs corrientes no puede cambiar: rompería el resume
    assert ex.normalize_url("https://x.com/a?b=1") == "https://x.com/a?b=1"


def test_is_internal_url_con_dominio_idn():
    hosts = {ex.normalize_host("españa.com")}
    enlace = ex.normalize_url("https://españa.com/a")
    assert ex.is_internal_url(enlace, hosts) is True
    assert ex.is_internal_url("https://ESPAÑA.com./b", hosts) is True


@pytest.mark.parametrize("href", ["https://[LINK]/x", "http://ex.com]/x"])
def test_extract_links_ignora_href_malformado_y_sigue(href):
    sel = Selector(text=f'<a href="{href}">roto</a><a href="/ok">ok</a>')
    links = ex.extract_links(sel, "https://e.com/", {"e.com"})
    assert [l["url"] for l in links] == ["https://e.com/ok"]


def test_extract_resources_srcset_vacio_no_lanza():
    sel = Selector(text='<img srcset=" "><img src="https://[bad/x.png"><img src="/a.png">')
    urls = [r["url"] for r in ex.extract_resources(sel, "https://e.com/")]
    assert urls == ["https://e.com/a.png"]


@pytest.mark.parametrize("rel", ["nofollow,noopener", "noopener, NOFOLLOW", "nofollow"])
def test_extract_links_rel_separado_por_comas(rel):
    sel = Selector(text=f'<a href="/x" rel="{rel}">x</a>')
    (link,) = ex.extract_links(sel, "https://e.com/", {"e.com"})
    assert link["follow"] is False


@pytest.mark.parametrize("content,destino", [
    ("0; url=/dest", "https://e.com/dest"),
    ("5;URL='https://e.com/otra'", "https://e.com/otra"),
    ("0; /dest", "https://e.com/dest"),
    ("300", None),
])
def test_extract_meta_refresh_target(content, destino):
    sel = Selector(text=f'<head><meta http-equiv="Refresh" content="{content}"></head>')
    assert ex.extract_meta_refresh_target(sel, "https://e.com/p") == destino


def test_extract_meta_refresh_target_ignora_noscript():
    sel = Selector(text='<noscript><meta http-equiv="refresh" content="0;url=/x"></noscript>')
    assert ex.extract_meta_refresh_target(sel, "https://e.com/") is None


def test_compile_url_patterns():
    casan, invalidos = ex.compile_url_patterns(
        ["*/tag/*", "glob:*.pdf", r"re:/p/\d+$", "re:(", r"\?sort="]
    )
    assert invalidos == ["*/tag/*", "re:("]
    comprueba = lambda u: [c(u) for c in casan]  # noqa: E731
    assert comprueba("https://e.com/tag/x") == [True, False, False, False]
    assert comprueba("https://e.com/doc.pdf") == [False, True, False, False]
    assert comprueba("https://e.com/mapdfx") == [False, False, False, False]
    assert comprueba("https://e.com/p/12") == [False, False, True, False]
    assert comprueba("https://e.com/c?sort=1") == [False, False, False, True]


# ---------------------------------------------------------------------------
# Titulares: ocultos, clones y texto que se lee (#27)
# ---------------------------------------------------------------------------
def test_headings_clones_movil_escritorio_no_dan_multiple_h1():
    """Un `d-none` y un `aria-hidden` con el mismo titular daban 3 h1 donde
    hay 1. Se guardan los tres, marcados: el que cuenta es uno."""
    s = sel("<h1 class='d-none'>Zapatillas</h1>"
            "<h1 aria-hidden='true'>Zapatillas</h1>"
            "<h1>Zapatillas running</h1>")
    heads = ex.extract_headings(s)
    assert len(heads) == 3
    assert [h["oculto"] for h in heads] == [True, True, False]
    # El texto del oculto tambien se guarda: lo que no se guarda no se audita.
    assert heads[0]["text"] == "Zapatillas"


def test_heading_sin_script_y_sin_partir_palabras():
    s = sel("<h1>Zapa<span>tillas</span><script>var x=1;</script></h1>")
    assert ex.extract_headings(s)[0]["text"] == "Zapatillas"


def test_heading_de_solo_logo_usa_el_alt():
    """Es de donde lo lee Google; antes salia vacio."""
    s = sel("<h1><a href='/'><img src='l.svg' alt='Acme Deportes'></a></h1>")
    assert ex.extract_headings(s)[0]["text"] == "Acme Deportes"


def test_role_heading_con_aria_level_cuenta():
    s = sel("<div role='heading' aria-level='1'>Titular ARIA</div>"
            "<div role='heading'>Sin nivel</div>")
    heads = ex.extract_headings(s)
    assert [(h["tag"], h["text"]) for h in heads] == [
        ("h1", "Titular ARIA"), ("h2", "Sin nivel")  # ARIA: sin aria-level es 2
    ]


# ---------------------------------------------------------------------------
# Anclas: el texto que anuncia un lector de pantalla (#27)
# ---------------------------------------------------------------------------
def test_anchor_de_icono_no_es_un_anchor_vacio():
    s = sel("<a href='/a'>Zapa<span>tillas</span><script>var x=1</script></a>"
            "<a href='/b' aria-label='Ir al carrito'><svg><path/></svg></a>"
            "<a href='/c' title='Ver la ficha'><i class='icon'></i></a>"
            "<a href='/d'><svg aria-label='Buscar'><path/></svg></a>"
            "<a href='/e'><svg><title>Mi cuenta</title></svg></a>")
    anchors = [l["anchor_text"] for l in ex.extract_links(s, "https://x.com/", {"x.com"})]
    assert anchors == ["Zapatillas", "Ir al carrito", "Ver la ficha", "Buscar", "Mi cuenta"]


# ---------------------------------------------------------------------------
# template / noscript no estan en el DOM (#27)
# ---------------------------------------------------------------------------
def test_template_y_noscript_no_aportan_enlaces_ni_imagenes():
    """El `<noscript><img>` de la carga diferida duplicaba la imagen real: 583
    de 4.409 en 58 paginas de control, cada una un `image_missing_alt` posible
    sobre una imagen que nadie ve."""
    s = sel("<a href='/real'>Real</a><template><a href='/tpl'>Tpl</a></template>"
            "<noscript><img src='/n.png'></noscript><img src='/r.png' alt='Real'>"
            "<template><img src='/t.png'></template>")
    assert [l["url"] for l in ex.extract_links(s, "https://x.com/", {"x.com"})] == [
        "https://x.com/real"]
    assert [r["url"] for r in ex.extract_resources(s, "https://x.com/")] == [
        "https://x.com/r.png"]


def test_hash_de_contenido_ignora_espacios_y_caja():
    """`body_hash` es el de los bytes: un token CSRF o un nonce bastaban para
    que dos paginas identicas no coincidieran. En un censo real encontraba 0
    duplicados donde hay 745 paginas duplicadas."""
    a = ex.hash_de_contenido("Politica de privacidad\nEste sitio usa datos.")
    b = ex.hash_de_contenido("  politica de PRIVACIDAD   Este sitio usa datos. ")
    assert a == b
    assert a != ex.hash_de_contenido("Politica de privacidad. Otro texto.")
    assert ex.hash_de_contenido(None) is None
    assert ex.hash_de_contenido("   ") is None


# ---------------------------------------------------------------------------
# Alcance del rastreo: Public Suffix List y <base href> roto (#25)
# ---------------------------------------------------------------------------
def test_dominio_registrable_usa_la_public_suffix_list():
    """Cortar por las dos ultimas etiquetas daba `co.uk` como raiz, asi que
    cualquier sitio `.co.uk` salia interno para una semilla `.co.uk`: el rastreo
    se metia en la competencia y sus paginas entraban en el informe."""
    assert ex.dominio_registrable("www.competidor.co.uk") == "competidor.co.uk"
    assert ex.dominio_registrable("tienda.cliente.com.au") == "cliente.com.au"
    assert ex.dominio_registrable("blogs.uoc.edu") == "uoc.edu"
    assert ex.dominio_registrable("www.x.com") == "x.com"
    # `notx.com` no es un subdominio de `x.com`
    assert ex.dominio_registrable("notx.com") == "notx.com"
    # Sufijos privados: dos usuarios de github.io son dos sitios distintos
    assert ex.dominio_registrable("usuario.github.io") == "usuario.github.io"
    # Hosts sin sufijo publico: tal cual (los usan los tests y las redes internas)
    assert ex.dominio_registrable("localhost") == "localhost"
    assert ex.dominio_registrable("127.0.0.1") == "127.0.0.1"
    assert ex.dominio_registrable("") == ""


def test_una_base_href_no_navegable_se_ignora():
    """Comprobado en Chromium: con `javascript:` o `data:` la base se ignora y
    sigue siendo la URL del documento; con `mailto:` el navegador deja los
    enlaces sin resolver, o sea roto para todos. Antes se aceptaba cualquier
    cosa y en esas plantillas TODOS los enlaces relativos salian externos y sin
    rastrear: el grafo interno de la pagina desaparecia."""
    for base in ("javascript:void(0)", "mailto:x@y.com", "data:text/html,x"):
        s = sel(f"<html><head><base href='{base}'></head><body>"
                "<a href='/pagina'>p</a></body></html>")
        assert ex.effective_base_url(s, "https://x.com/seccion/") == "https://x.com/seccion/"
        enlace = ex.extract_links(s, ex.effective_base_url(s, "https://x.com/seccion/"),
                                  {"x.com"})[0]
        assert enlace["url"] == "https://x.com/pagina"
        assert enlace["is_internal"] is True


def test_una_base_href_valida_se_respeta():
    for base, esperada in (("/es/", "https://x.com/es/"),
                           ("https://otro.com/", "https://otro.com/"),
                           ("//cdn.x.com/", "https://cdn.x.com/")):
        s = sel(f"<html><head><base href='{base}'></head><body></body></html>")
        assert ex.effective_base_url(s, "https://x.com/seccion/") == esperada


# ---------------------------------------------------------------------------
# Datos estructurados: @graph, CDATA y el tipo con dos nombres (#28)
# ---------------------------------------------------------------------------
_YOAST = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"WebPage","@id":"https://x.com/#webpage","url":"https://x.com/","name":"Home"},
 {"@type":"Organization","@id":"https://x.com/#org","name":"Acme"},
 {"@type":["Article","BlogPosting"],"@id":"https://x.com/#art","headline":"T"},
 {"@id":"https://x.com/#ref"}
]}</script></head><body>x</body></html>"""


def test_el_grafo_de_yoast_da_una_entidad_por_nodo():
    """Antes el bloque entero daba UNA fila con schema_type NULL. El `@graph`
    es lo que emite Yoast: en casi todo WordPress no habia forma de filtrar ni
    de informar por tipo, ni de validar cada entidad por separado."""
    items = ex.extract_structured_data(_YOAST, "https://x.com/")
    assert [i["schema_type"] for i in items] == [
        "WebPage", "Organization", "Article, BlogPosting"
    ]
    # El nodo que solo es una referencia (`{"@id": ...}`) no es una entidad.
    assert all(i["raw"].get("@type") for i in items)


def test_el_json_ld_envuelto_en_cdata_se_lee():
    """Sintaxis de XHTML que usan Drupal y los portales antiguos: el bloque
    entero desaparecia sin aviso."""
    html = ('<html><head><script type="application/ld+json">//<![CDATA[\n'
            '{"@context":"https://schema.org","@type":"Product","name":"X"}\n'
            '//]]></script></head><body>x</body></html>')
    items = ex.extract_structured_data(html, "https://x.com/")
    assert [i["schema_type"] for i in items] == ["Product"]


def test_el_tipo_se_guarda_con_el_mismo_nombre_en_los_tres_formatos():
    """RDFa lo escribe como IRI completo y los otros dos como nombre corto, asi
    que el mismo tipo salia con dos nombres y los filtros no casaban."""
    rdfa = ('<html><body><div vocab="https://schema.org/" typeof="Product">'
            '<span property="name">X</span></div></body></html>')
    micro = ('<html><body><div itemscope itemtype="https://schema.org/Product">'
             '<span itemprop="name">X</span></div></body></html>')
    tipos = {ex.extract_structured_data(h, "https://x.com/")[0]["schema_type"]
             for h in (rdfa, micro)}
    assert tipos == {"Product"}
