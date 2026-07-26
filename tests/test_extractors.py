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


# ---------------------------------------------------------------------------
# extract_headings  (skip template/noscript/svg, ordering)
# ---------------------------------------------------------------------------
def test_extract_headings_order_and_skips_hidden():
    s = sel(
        "<h1>Title</h1>"
        "<template><h2>Tmpl</h2></template>"
        "<noscript><h3>NS</h3></noscript>"
        "<h2>Sub</h2>"
    )
    heads = ex.extract_headings(s)
    assert [(h["tag"], h["text"]) for h in heads] == [("h1", "Title"), ("h2", "Sub")]
    assert [h["position"] for h in heads] == [0, 1]


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
