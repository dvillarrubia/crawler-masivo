"""XML sitemap parsing helpers — pure functions, no Scrapy imports.

Covers the sitemap protocol (https://www.sitemaps.org/protocol.html):
``<urlset>`` files, ``<sitemapindex>`` files, gzip-compressed bodies, and
``Sitemap:`` directives in robots.txt. Namespace-agnostic on purpose —
real-world sitemaps use the standard namespace, no namespace at all, or
Google extensions, and Screaming Frog accepts them all.
"""

from __future__ import annotations

import gzip
import os
from urllib.parse import urljoin

_GZIP_MAGIC = b"\x1f\x8b"

# Hard caps so a hostile/broken sitemap tree cannot blow up the crawl.
#
# 50 se quedaba muy corto con CMS que parten el sitemap por plantilla: un
# Liferay real declaraba 395 hijos, asi que se leia el 12% y el resto de URLs
# quedaba sin membresia. Ahora son 500 y es configurable por entorno, porque el
# numero adecuado depende del sitio. El tope sigue existiendo como proteccion
# ante un arbol de sitemaps hostil o roto; cuando se alcanza, el spider deja la
# membresia en NULL en vez de afirmar que las URLs no estan en el sitemap.
MAX_SITEMAP_FILES = int(os.getenv("MAX_SITEMAP_FILES", "500"))
MAX_URLS_PER_SITEMAP = int(os.getenv("MAX_URLS_PER_SITEMAP", "50000"))


def parse_robots_sitemaps(robots_txt: str, base_url: str = "") -> list[str]:
    """Extract ``Sitemap:`` directive URLs from a robots.txt body.

    The directive is case-insensitive and may appear anywhere in the file
    (it is not tied to a User-agent group). Relative values (non-standard
    but seen in the wild) are resolved against *base_url*.
    """
    found: list[str] = []
    seen: set[str] = set()
    for line in (robots_txt or "").splitlines():
        # Strip comments, then match the directive prefix.
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() != "sitemap":
            continue
        url = value.strip()
        if not url:
            continue
        if base_url:
            url = urljoin(base_url, url)
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def _maybe_gunzip(body: bytes) -> bytes:
    """Transparently decompress a gzipped sitemap body (.xml.gz files)."""
    if body[:2] == _GZIP_MAGIC:
        try:
            return gzip.decompress(body)
        except Exception:
            return body
    return body


def _localname(tag) -> str:
    """Element tag without its XML namespace, lowercased."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(body: bytes | str, base_url: str = "") -> tuple[list[str], list[str]]:
    """Parse a sitemap file into ``(page_urls, child_sitemap_urls)``.

    Handles both ``<urlset>`` (leaf sitemaps: returns page URLs) and
    ``<sitemapindex>`` (index files: returns child sitemap URLs). Gzip
    bodies are decompressed automatically. Returns ``([], [])`` on any
    parse failure — a broken sitemap must never break the crawl.
    """
    if isinstance(body, str):
        raw = body.encode("utf-8", errors="ignore")
    else:
        raw = body
    raw = _maybe_gunzip(raw)

    try:
        from lxml import etree

        # recover=True tolerates the malformed XML that real sites serve.
        root = etree.fromstring(raw, parser=etree.XMLParser(recover=True, huge_tree=True))
    except Exception:
        return ([], [])
    if root is None:
        return ([], [])

    root_kind = _localname(root.tag)
    page_urls: list[str] = []
    child_sitemaps: list[str] = []
    seen: set[str] = set()

    # Walk <url>/<sitemap> entries and pull their <loc> children. Iterating
    # entries (instead of every <loc> in the doc) keeps Google extension
    # blocks (image:loc, video:...) from leaking into the URL list.
    for entry in root:
        entry_kind = _localname(entry.tag)
        if entry_kind not in ("url", "sitemap"):
            continue
        loc_text = None
        for child in entry:
            if _localname(child.tag) == "loc":
                loc_text = (child.text or "").strip()
                break
        if not loc_text:
            continue
        url = urljoin(base_url, loc_text) if base_url else loc_text
        if url in seen:
            continue
        seen.add(url)
        if entry_kind == "sitemap" or root_kind == "sitemapindex":
            child_sitemaps.append(url)
        else:
            page_urls.append(url)
        if len(page_urls) >= MAX_URLS_PER_SITEMAP:
            break

    return (page_urls, child_sitemaps)
