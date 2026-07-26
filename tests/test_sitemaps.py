"""Unit tests for sitemap parsing (``seo_crawler.sitemaps``)."""

from __future__ import annotations

import gzip

from seo_crawler.sitemaps import parse_robots_sitemaps, parse_sitemap

SITEMAP_NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


# ---------------------------------------------------------------------------
# robots.txt Sitemap: directives
# ---------------------------------------------------------------------------
def test_robots_sitemap_directives_basic():
    robots = (
        "User-agent: *\n"
        "Disallow: /admin\n"
        "Sitemap: https://e.com/sitemap.xml\n"
        "sitemap: https://e.com/sitemap-news.xml\n"   # lowercase key
    )
    assert parse_robots_sitemaps(robots) == [
        "https://e.com/sitemap.xml",
        "https://e.com/sitemap-news.xml",
    ]


def test_robots_sitemap_relative_and_comments():
    robots = (
        "Sitemap: /sitemap.xml  # main\n"
        "# Sitemap: https://e.com/commented-out.xml\n"
    )
    out = parse_robots_sitemaps(robots, base_url="https://e.com/robots.txt")
    assert out == ["https://e.com/sitemap.xml"]


def test_robots_sitemap_dedup_and_empty():
    robots = "Sitemap: https://e.com/s.xml\nSitemap: https://e.com/s.xml\n"
    assert parse_robots_sitemaps(robots) == ["https://e.com/s.xml"]
    assert parse_robots_sitemaps("") == []
    assert parse_robots_sitemaps("User-agent: *\nDisallow:\n") == []


# ---------------------------------------------------------------------------
# <urlset> leaf sitemaps
# ---------------------------------------------------------------------------
def test_parse_urlset():
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset {SITEMAP_NS}>
      <url><loc>https://e.com/</loc><lastmod>2026-01-01</lastmod></url>
      <url><loc>https://e.com/a</loc></url>
      <url><loc> https://e.com/b </loc></url>
    </urlset>"""
    urls, children = parse_sitemap(xml)
    assert urls == ["https://e.com/", "https://e.com/a", "https://e.com/b"]
    assert children == []


def test_parse_urlset_without_namespace():
    xml = "<urlset><url><loc>https://e.com/x</loc></url></urlset>"
    urls, children = parse_sitemap(xml)
    assert urls == ["https://e.com/x"]
    assert children == []


def test_parse_urlset_ignores_google_image_extension_locs():
    # image:loc lives inside <image:image>, not directly under <url> — it must
    # not leak into the page URL list.
    xml = f"""<urlset {SITEMAP_NS} xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
      <url>
        <loc>https://e.com/page</loc>
        <image:image><image:loc>https://cdn.e.com/pic.jpg</image:loc></image:image>
      </url>
    </urlset>"""
    urls, _ = parse_sitemap(xml)
    assert urls == ["https://e.com/page"]


def test_parse_urlset_dedups_and_resolves_relative_locs():
    xml = (
        "<urlset>"
        "<url><loc>/rel</loc></url>"
        "<url><loc>https://e.com/rel</loc></url>"
        "</urlset>"
    )
    urls, _ = parse_sitemap(xml, base_url="https://e.com/sitemap.xml")
    assert urls == ["https://e.com/rel"]


# ---------------------------------------------------------------------------
# <sitemapindex> files
# ---------------------------------------------------------------------------
def test_parse_sitemapindex():
    xml = f"""<?xml version="1.0"?>
    <sitemapindex {SITEMAP_NS}>
      <sitemap><loc>https://e.com/sitemap-1.xml</loc></sitemap>
      <sitemap><loc>https://e.com/sitemap-2.xml</loc></sitemap>
    </sitemapindex>"""
    urls, children = parse_sitemap(xml)
    assert urls == []
    assert children == ["https://e.com/sitemap-1.xml", "https://e.com/sitemap-2.xml"]


# ---------------------------------------------------------------------------
# gzip + malformed input
# ---------------------------------------------------------------------------
def test_parse_gzipped_sitemap():
    xml = "<urlset><url><loc>https://e.com/gz</loc></url></urlset>"
    urls, _ = parse_sitemap(gzip.compress(xml.encode()))
    assert urls == ["https://e.com/gz"]


def test_parse_garbage_returns_empty():
    assert parse_sitemap(b"not xml at all") == ([], [])
    assert parse_sitemap(b"") == ([], [])
    # An HTML 404 page must not produce URLs.
    urls, children = parse_sitemap("<html><body><a href='/x'>x</a></body></html>")
    assert (urls, children) == ([], [])


def test_parse_malformed_xml_recovers():
    # Unclosed tag — lxml recover mode should still salvage the good entry.
    xml = "<urlset><url><loc>https://e.com/ok</loc></url><url><loc>https://e.com/broken"
    urls, _ = parse_sitemap(xml)
    assert "https://e.com/ok" in urls
