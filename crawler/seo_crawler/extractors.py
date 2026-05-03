"""
Pure extraction helpers -- no Scrapy imports.

Every public function receives either a ``parsel.Selector`` or raw HTML
bytes/str and returns plain Python data structures.  This keeps the
extraction logic testable without bringing in a full Scrapy response.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

import extruct
from w3lib.url import canonicalize_url

# ---- regex helpers ---------------------------------------------------------
_WHITESPACE = re.compile(r"\s+")


def _clean(text: str | None) -> str | None:
    """Collapse whitespace and strip a string, returning None when empty."""
    if not text:
        return None
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def _parse_int(value: str | None) -> int | None:
    """Safely parse an integer from an HTML attribute value."""
    if not value:
        return None
    cleaned = value.strip().rstrip("px%").strip()
    try:
        return int(cleaned)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------

def compute_url_hash(url: str) -> str:
    """Return the hex SHA-256 of the canonicalized URL."""
    normalized = normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_url(url: str) -> str:
    """Canonicalize a URL using w3lib for consistent dedup."""
    return canonicalize_url(url, keep_fragments=False)


def compute_status_group(status_code: int | None) -> str:
    """Map an HTTP status code to a human-friendly group label."""
    if status_code is None:
        return "unknown"
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    if 500 <= status_code < 600:
        return "5xx"
    return "other"


def classify_resource_type(content_type: str | None, url: str) -> str:
    """Guess the resource type from Content-Type header or URL extension."""
    ct = (content_type or "").lower().split(";")[0].strip()

    if "html" in ct:
        return "html"
    if ct.startswith("image/"):
        return "image"
    if "css" in ct:
        return "css"
    if "javascript" in ct or "ecmascript" in ct:
        return "js"
    if "pdf" in ct:
        return "pdf"
    if "font" in ct or "woff" in ct:
        return "font"

    # Fallback: look at extension
    path = urlparse(url).path.lower()
    ext_map = {
        ".html": "html", ".htm": "html",
        ".css": "css",
        ".js": "js", ".mjs": "js",
        ".jpg": "image", ".jpeg": "image", ".png": "image",
        ".gif": "image", ".svg": "image", ".webp": "image", ".ico": "image",
        ".pdf": "pdf",
        ".woff": "font", ".woff2": "font", ".ttf": "font", ".eot": "font",
    }
    for ext, rtype in ext_map.items():
        if path.endswith(ext):
            return rtype

    return "other"


def is_internal_url(url: str, allowed_hosts: set[str]) -> bool:
    """Check whether *url* belongs to one of the *allowed_hosts*."""
    host = urlparse(url).hostname
    if host is None:
        return False
    # Strip leading www. for comparison
    bare = host.lower().removeprefix("www.")
    return bare in allowed_hosts or host.lower() in allowed_hosts


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------

def extract_meta(selector) -> dict[str, Any]:
    """
    Extract SEO-relevant <head> metadata from a *parsel.Selector*.

    Returns a flat dict that maps 1-to-1 with ``HtmlMetaItem`` fields.
    """
    def _meta(name: str) -> str | None:
        """Get content="" of a <meta name="..."> or <meta property="...">."""
        val = selector.css(
            f'meta[name="{name}"]::attr(content), '
            f'meta[property="{name}"]::attr(content)'
        ).get()
        return _clean(val)

    title_text = _clean(selector.css("title::text").get())
    desc = _meta("description")

    canonical = selector.css('link[rel="canonical"]::attr(href)').get()

    return {
        "title": title_text,
        "title_len": len(title_text) if title_text else None,
        "meta_description": desc,
        "meta_description_len": len(desc) if desc else None,
        "meta_keywords": _meta("keywords"),
        "meta_robots": _meta("robots"),
        "canonical_href": _clean(canonical),
        # OG
        "og_title": _meta("og:title"),
        "og_description": _meta("og:description"),
        "og_image": _meta("og:image"),
        "og_url": _meta("og:url"),
        "og_type": _meta("og:type"),
        # Twitter
        "twitter_card": _meta("twitter:card"),
        "twitter_title": _meta("twitter:title"),
        "twitter_description": _meta("twitter:description"),
        # Pagination
        "rel_next": _clean(
            selector.css('link[rel="next"]::attr(href)').get()
        ),
        "rel_prev": _clean(
            selector.css('link[rel="prev"]::attr(href)').get()
        ),
    }


def extract_headings(selector) -> list[dict[str, Any]]:
    """Return an ordered list of heading dicts ``{tag, position, text}`` in document order.

    Headings inside ``<template>``, ``<noscript>``, and ``<svg>`` elements
    are excluded because they are not visible to users and would otherwise
    duplicate headings rendered by JavaScript frameworks or SSR.
    """
    results: list[dict[str, Any]] = []
    pos = 0
    for node in selector.css("h1, h2, h3, h4, h5, h6"):
        # Skip headings inside invisible / framework-duplicated elements
        if node.xpath("ancestor::template | ancestor::noscript | ancestor::svg"):
            continue
        tag_name = node.xpath("name()").get()
        if tag_name and tag_name.lower().startswith("h"):
            inner = " ".join(node.css("::text").getall())
            results.append({
                "tag": tag_name.lower(),
                "position": pos,
                "text": _clean(inner),
            })
            pos += 1
    return results



def extract_links(selector, base_url: str, allowed_hosts: set[str]) -> list[dict[str, Any]]:
    """
    Extract all ``<a>`` links from the page.

    Returns a list of dicts with: url, anchor_text, rel, is_internal,
    link_position, target, alt_text, follow, link_type.
    """
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in selector.css("a[href]"):
        raw_href = a.attrib.get("href", "").strip()
        if not raw_href or raw_href.startswith(("javascript:", "mailto:", "tel:", "data:")):
            continue

        absolute = urljoin(base_url, raw_href)
        normalized = normalize_url(absolute)

        # Deduplicate within one page
        url_hash = compute_url_hash(normalized)
        if url_hash in seen:
            continue
        seen.add(url_hash)

        anchor_text = _clean(" ".join(a.css("::text").getall()))
        rel = _clean(a.attrib.get("rel", ""))

        # Heuristic link position
        link_position = _detect_link_position(a)

        # Target attribute (_blank, _self, _parent, _top, or custom)
        target = _clean(a.attrib.get("target", ""))

        # Alt text from child <img> elements (useful for image links)
        child_imgs = a.css("img")
        alt_text_parts: list[str] = []
        for img in child_imgs:
            alt = img.attrib.get("alt", "")
            if alt and alt.strip():
                alt_text_parts.append(alt.strip())
        alt_text = _clean(" ".join(alt_text_parts)) if alt_text_parts else None

        # Follow: True unless rel contains "nofollow"
        rel_tokens = {t.strip().lower() for t in (rel or "").split()} if rel else set()
        follow = "nofollow" not in rel_tokens

        # Link type classification
        has_child_imgs = len(child_imgs) > 0
        has_text = bool(anchor_text)
        if has_child_imgs and not has_text:
            link_type = "image"
        elif has_child_imgs and has_text:
            link_type = "image_text"
        else:
            link_type = "hyperlink"

        results.append({
            "url": normalized,
            "anchor_text": anchor_text,
            "rel": rel,
            "is_internal": is_internal_url(normalized, allowed_hosts),
            "link_position": link_position,
            "target": target,
            "alt_text": alt_text,
            "follow": follow,
            "link_type": link_type,
        })

    return results


def _detect_link_position(a_selector) -> str:
    """
    Simple heuristic: check ancestor element names for nav / footer / header.
    Falls back to ``content``.
    """
    # Walk up the ancestor axis looking for semantic elements
    for ancestor_tag in a_selector.xpath("ancestor::*/@class").getall():
        lower = ancestor_tag.lower()
        if "nav" in lower:
            return "nav"
        if "footer" in lower:
            return "footer"
        if "header" in lower:
            return "header"
        if "sidebar" in lower:
            return "sidebar"

    # Also check tag names
    ancestor_names = [
        node.xpath("name()").get()
        for node in a_selector.xpath("ancestor::*")
    ]
    for name in ancestor_names:
        nl = name.lower()
        if nl == "nav":
            return "nav"
        if nl == "footer":
            return "footer"
        if nl == "header":
            return "header"
        if nl == "aside":
            return "sidebar"

    return "content"


def extract_hreflang(selector) -> list[dict[str, Any]]:
    """Extract ``<link rel="alternate" hreflang="...">`` tags."""
    results: list[dict[str, Any]] = []
    for link in selector.css('link[rel="alternate"][hreflang]'):
        lang = _clean(link.attrib.get("hreflang", ""))
        href = _clean(link.attrib.get("href", ""))
        if lang and href:
            results.append({"lang": lang, "href": href})
    return results


def extract_structured_data(html_body: str, url: str = "") -> list[dict[str, Any]]:
    """
    Extract JSON-LD, Microdata, and RDFa using *extruct*.

    Returns a list of dicts with: raw, format, schema_type.
    """
    results: list[dict[str, Any]] = []

    try:
        data = extruct.extract(
            html_body,
            base_url=url,
            syntaxes=["json-ld", "microdata", "rdfa"],
            uniform=True,
        )
    except Exception:
        return results

    for fmt, items in data.items():
        if not isinstance(items, list):
            continue
        for item in items:
            schema_type = None
            if isinstance(item, dict):
                schema_type = item.get("@type")
                if isinstance(schema_type, list):
                    schema_type = ", ".join(str(t) for t in schema_type)
                elif schema_type is not None:
                    schema_type = str(schema_type)
            results.append({
                "raw": item,
                "format": fmt.replace("-", ""),  # jsonld, microdata, rdfa
                "schema_type": schema_type,
            })

    return results


def extract_resources(selector, base_url: str) -> list[dict[str, Any]]:
    """
    Collect references to images, scripts, stylesheets, and other assets.

    Returns a list of dicts with: url, resource_type, alt_text, width,
    height, is_mixed_content.
    """
    is_https = base_url.startswith("https://")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(
        raw_url: str,
        rtype: str,
        alt: str | None = None,
        width: str | None = None,
        height: str | None = None,
    ):
        if not raw_url:
            return
        absolute = urljoin(base_url, raw_url.strip())
        normalized = normalize_url(absolute)
        if normalized in seen:
            return
        seen.add(normalized)

        mixed = is_https and absolute.startswith("http://")
        results.append({
            "url": normalized,
            "resource_type": rtype,
            "alt_text": _clean(alt),
            "width": _parse_int(width),
            "height": _parse_int(height),
            "is_mixed_content": mixed,
        })

    # Images
    for img in selector.css("img[src]"):
        _add(
            img.attrib.get("src", ""),
            "image",
            img.attrib.get("alt"),
            img.attrib.get("width"),
            img.attrib.get("height"),
        )

    # Stylesheets
    for link in selector.css('link[rel="stylesheet"][href]'):
        _add(link.attrib.get("href", ""), "css")

    # Scripts
    for script in selector.css("script[src]"):
        _add(script.attrib.get("src", ""), "js")

    # srcset images (first URL only per element for simplicity)
    for img in selector.css("[srcset]"):
        srcset = img.attrib.get("srcset", "")
        first_url = srcset.split(",")[0].strip().split()[0] if srcset else ""
        _add(
            first_url,
            "image",
            img.attrib.get("alt"),
            img.attrib.get("width"),
            img.attrib.get("height"),
        )

    return results


# ---------------------------------------------------------------------------
# Screaming-Frog-style extraction helpers
# ---------------------------------------------------------------------------

# Tags whose text content should be excluded from visible-text counts.
_INVISIBLE_TAGS = frozenset({"script", "style", "noscript"})


def extract_word_count(selector) -> int:
    """Count words in visible body text, excluding script/style/noscript.

    Uses XPath to pull all text nodes inside ``<body>`` that are not
    descendants of invisible elements.
    """
    body = selector.css("body")
    if not body:
        return 0

    word_count = 0
    for text_piece in body.xpath(
        ".//text()[not(ancestor::script)"
        " and not(ancestor::style)"
        " and not(ancestor::noscript)]"
    ).getall():
        words = text_piece.split()
        word_count += len(words)

    return word_count


def extract_visible_text(selector) -> str:
    """Extract concatenated visible text from the ``<body>`` element.

    Returns an empty string when no ``<body>`` is found.
    """
    body = selector.css("body")
    if not body:
        return ""

    parts = body.xpath(
        ".//text()[not(ancestor::script)"
        " and not(ancestor::style)"
        " and not(ancestor::noscript)]"
    ).getall()
    return " ".join(parts)


def compute_text_ratio(html_text: str, visible_text: str) -> float:
    """Return the visible-text to raw-HTML size ratio as a percentage (0-100).

    Parameters
    ----------
    html_text:
        The full raw HTML source of the page.
    visible_text:
        The extracted visible text (e.g. from ``extract_visible_text``).

    Returns
    -------
    float
        Ratio expressed as a percentage.  Returns ``0.0`` when *html_text*
        is empty.
    """
    html_len = len(html_text)
    if html_len == 0:
        return 0.0
    text_len = len(visible_text)
    return round((text_len / html_len) * 100, 2)


def compute_folder_depth(url: str) -> int:
    """Count non-empty path segments of *url*.

    Examples
    --------
    >>> compute_folder_depth("https://example.com/")
    0
    >>> compute_folder_depth("https://example.com/blog/post/1")
    3
    >>> compute_folder_depth("https://example.com/blog/post/1/")
    3
    """
    path = urlparse(url).path
    segments = [seg for seg in path.split("/") if seg]
    return len(segments)


# ---------------------------------------------------------------------------
# SERP pixel-width estimation
# ---------------------------------------------------------------------------

# Approximate character widths (in pixels) for Arial at 20px.
# Measured from font metrics; values are rounded to one decimal place.
_ARIAL_20PX_WIDTHS: dict[str, float] = {
    # Lowercase letters
    "a": 9.8,  "b": 9.8,  "c": 8.9,  "d": 9.8,  "e": 9.8,
    "f": 5.6,  "g": 9.8,  "h": 9.8,  "i": 4.4,  "j": 4.4,
    "k": 8.9,  "l": 4.4,  "m": 14.5, "n": 9.8,  "o": 9.8,
    "p": 9.8,  "q": 9.8,  "r": 5.6,  "s": 8.9,  "t": 5.6,
    "u": 9.8,  "v": 8.9,  "w": 12.2, "x": 8.9,  "y": 8.9,
    "z": 8.9,
    # Uppercase letters
    "A": 12.2, "B": 11.1, "C": 11.1, "D": 12.2, "E": 10.0,
    "F": 10.0, "G": 12.2, "H": 12.2, "I": 4.4,  "J": 7.8,
    "K": 11.1, "L": 10.0, "M": 13.3, "N": 12.2, "O": 12.2,
    "P": 10.0, "Q": 12.2, "R": 11.1, "S": 10.0, "T": 10.0,
    "U": 12.2, "V": 11.1, "W": 15.6, "X": 11.1, "Y": 10.0,
    "Z": 10.0,
    # Digits
    "0": 9.8,  "1": 9.8,  "2": 9.8,  "3": 9.8,  "4": 9.8,
    "5": 9.8,  "6": 9.8,  "7": 9.8,  "8": 9.8,  "9": 9.8,
    # Common symbols and punctuation
    " ": 5.0,  "!": 5.6,  '"': 7.1,  "#": 9.8,  "$": 9.8,
    "%": 15.6, "&": 11.7, "'": 3.9,  "(": 5.6,  ")": 5.6,
    "*": 6.7,  "+": 10.3, ",": 5.0,  "-": 5.6,  ".": 5.0,
    "/": 5.6,  ":": 5.6,  ";": 5.6,  "<": 10.3, "=": 10.3,
    ">": 10.3, "?": 9.8,  "@": 17.8, "[": 5.6,  "\\": 5.6,
    "]": 5.6,  "^": 10.3, "_": 9.8,  "`": 5.6,  "{": 5.6,
    "|": 4.6,  "}": 5.6,  "~": 10.3,
}

# Fallback width for characters not in the lookup table.
_ARIAL_20PX_DEFAULT: float = 9.6


def _estimate_pixel_width(text: str, scale: float = 1.0) -> int:
    """Sum per-character pixel widths using the Arial 20px table.

    Parameters
    ----------
    text:
        The string to measure.
    scale:
        Multiplier applied to each character width.  Use ``1.0`` for 20px
        and ``0.7`` for 14px.

    Returns
    -------
    int
        Total estimated pixel width, rounded to the nearest integer.
    """
    if not text:
        return 0
    total = 0.0
    for ch in text:
        width = _ARIAL_20PX_WIDTHS.get(ch, _ARIAL_20PX_DEFAULT)
        total += width * scale
    return round(total)


def estimate_title_pixel_width(text: str) -> int:
    """Approximate SERP title pixel width using Arial at 20px.

    Google renders title tags in ~20px Arial (or a similar font) on
    desktop SERPs.  Titles are typically truncated around 580-600 pixels.
    """
    return _estimate_pixel_width(text, scale=1.0)


def estimate_description_pixel_width(text: str) -> int:
    """Approximate SERP description pixel width using Arial at 14px.

    Meta descriptions on desktop SERPs are rendered at roughly 14px,
    which is approximately 70% of the 20px title size.  Descriptions
    are typically truncated around 920-960 pixels.
    """
    return _estimate_pixel_width(text, scale=0.7)


# ---------------------------------------------------------------------------
# Meta-refresh and security helpers
# ---------------------------------------------------------------------------

def extract_meta_refresh(selector) -> str | None:
    """Find ``<meta http-equiv="refresh">`` and return its *content* value.

    Returns ``None`` when no refresh directive is present.
    """
    # http-equiv is case-insensitive in HTML; try common variants.
    content = selector.css(
        'meta[http-equiv="refresh"]::attr(content), '
        'meta[http-equiv="Refresh"]::attr(content), '
        'meta[http-equiv="REFRESH"]::attr(content)'
    ).get()
    return _clean(content)


def detect_mixed_content(selector, page_url: str) -> list[str]:
    """Detect HTTP resources loaded on an HTTPS page (mixed content).

    Parameters
    ----------
    selector:
        A ``parsel.Selector`` for the page HTML.
    page_url:
        The fully-qualified URL of the page being inspected.

    Returns
    -------
    list[str]
        HTTP (non-HTTPS) resource URLs found on the page.  Returns an
        empty list if *page_url* itself is not HTTPS.
    """
    parsed_page = urlparse(page_url)
    if parsed_page.scheme != "https":
        return []

    http_resources: list[str] = []
    seen: set[str] = set()

    # Selectors for resource attributes that can reference external URLs.
    resource_selectors = [
        ("img[src]", "src"),
        ("script[src]", "src"),
        ('link[rel="stylesheet"][href]', "href"),
        ("iframe[src]", "src"),
    ]

    for css_sel, attr in resource_selectors:
        for element in selector.css(css_sel):
            raw_url = (element.attrib.get(attr) or "").strip()
            if not raw_url:
                continue
            absolute = urljoin(page_url, raw_url)
            if absolute in seen:
                continue
            seen.add(absolute)
            if urlparse(absolute).scheme == "http":
                http_resources.append(absolute)

    return http_resources


def extract_security_headers(headers: dict) -> dict:
    """Inspect response headers for common security-related directives.

    Parameters
    ----------
    headers:
        A dict (or dict-like) of HTTP response headers.  Keys are expected
        to be strings; look-ups are performed case-insensitively.

    Returns
    -------
    dict
        A dict with boolean flags for HSTS, CSP, X-Content-Type-Options,
        X-Frame-Options, and the ``Referrer-Policy`` value (or ``None``).
    """
    # Build a lower-cased lookup for case-insensitive matching.
    lower_headers: dict[str, str] = {
        k.lower(): v for k, v in headers.items()
    }

    return {
        "has_hsts": "strict-transport-security" in lower_headers,
        "has_csp": "content-security-policy" in lower_headers,
        "has_x_content_type_options": "x-content-type-options" in lower_headers,
        "has_x_frame_options": "x-frame-options" in lower_headers,
        "referrer_policy": lower_headers.get("referrer-policy") or None,
    }


# ---------------------------------------------------------------------------
# HTTP status helpers
# ---------------------------------------------------------------------------

_HTTP_STATUS_TEXT: dict[int, str] = {
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    410: "Gone",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


def http_status_text(status_code: int) -> str:
    """Map an HTTP status code to its standard reason phrase.

    Returns ``"Unknown"`` for codes not in the lookup table.
    """
    return _HTTP_STATUS_TEXT.get(status_code, "Unknown")


# ---------------------------------------------------------------------------
# Content extraction — using trafilatura for site-agnostic boilerplate removal
# ---------------------------------------------------------------------------

# Boilerplate CSS selectors stripped from HTML *before* trafilatura runs.
# Covers cookie-consent libraries, chat widgets, ARIA modals, and common
# non-content structural elements.  Uses lxml.cssselect (always available
# via parsel/Scrapy).
_BOILERPLATE_CSS_SELECTORS: list[str] = [
    # ---- Known consent-management libraries ----
    "#CybotCookiebotDialog", "#CybotCookiebotDialogBodyUnderlay",
    "#onetrust-banner-sdk", "#onetrust-consent-sdk",
    ".osano-cm-window", ".cc-window", ".cc-banner", ".cc-revoke",
    "#tarteaucitronRoot", "#usercentrics-root", "#sp-consent-message",
    "#ez-cookie-dialog", "#catapult-cookie-bar", "#moove_gdpr_cookie_info_bar",
    # ---- Chat widgets ----
    "#hubspot-messages-iframe-container",
    "#intercom-container", "#intercom-frame",
    "#crisp-chatbox",
    "#drift-widget-container", "#drift-frame-chat",
    "#tawk-bubble-container",
    # ---- ARIA modals / HTML5 dialogs ----
    "[aria-modal='true']",
    "dialog[open]", "dialog",
]

# Substrings matched against element id and class attributes (case-insensitive).
# An element is removed if ANY of its id/class tokens contains one of these.
_BOILERPLATE_STRUCTURAL_TAGS: frozenset[str] = frozenset({
    "form", "nav", "aside", "footer", "header",
})

_BOILERPLATE_ID_CLASS_PATTERNS: list[str] = [
    # Cookie / consent / GDPR / privacy
    "cookie-consent", "cookie-banner", "cookie-notice", "cookie-bar",
    "cookie-popup", "cookie-modal", "cookie-wall", "cookie-law",
    "cookie-policy", "cookie-message", "cookie-alert", "cookie-overlay",
    "cookieconsent", "cookiebanner", "cookienotice", "cookiebar",
    "cookies-eu", "cookies-modal", "cookies-overlay",
    "gdpr-banner", "gdpr-notice", "gdpr-popup", "gdpr-overlay", "gdpr-consent",
    "consent-banner", "consent-modal", "consent-popup", "consent-overlay",
    "privacy-banner", "privacy-notice", "privacy-popup",
    # Chat widgets
    "chat-widget", "livechat",
]


def _strip_boilerplate_html(html: str) -> str:
    """Remove cookie banners, chat widgets, overlays, and structural
    non-content elements (form, nav, aside, footer, header) from raw HTML.

    Uses lxml to parse and strip elements matching known boilerplate
    patterns, then serialises back to a string.  The cleaned HTML is
    suitable for passing to trafilatura or html2text.
    """
    try:
        from lxml import html as lxml_html
        from lxml.cssselect import CSSSelector

        doc = lxml_html.fromstring(html)

        # 1) Remove by exact CSS selector
        for css in _BOILERPLATE_CSS_SELECTORS:
            try:
                sel = CSSSelector(css)
                for el in sel(doc):
                    el.getparent().remove(el)
            except Exception:
                pass

        # 2) Remove by id/class substring pattern
        for el in doc.iter():
            el_id = (el.get("id") or "").lower()
            el_class = (el.get("class") or "").lower()
            if not el_id and not el_class:
                continue
            for pattern in _BOILERPLATE_ID_CLASS_PATTERNS:
                if pattern in el_id or pattern in el_class:
                    parent = el.getparent()
                    if parent is not None:
                        parent.remove(el)
                    break  # element already removed

        # 3) Remove structural non-content elements
        structural = [
            el for el in doc.iter()
            if isinstance(el.tag, str) and el.tag in _BOILERPLATE_STRUCTURAL_TAGS
        ]
        for el in structural:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

        return lxml_html.tostring(doc, encoding="unicode")
    except Exception:
        return html  # on any failure, return original HTML unchanged


def _trafilatura_extract(html: str) -> tuple[str | None, str | None]:
    """Use trafilatura to extract clean text and markdown from raw HTML.

    Before running trafilatura, strips known boilerplate elements (cookie
    banners, chat widgets, consent overlays) via ``_strip_boilerplate_html``
    so they never pollute the extracted content.

    Trafilatura uses heuristics (text density, DOM structure, link ratio)
    to isolate editorial content — works on any website without
    site-specific selectors.

    Returns (plain_text, markdown) — either may be None.
    """
    try:
        import trafilatura

        clean = _strip_boilerplate_html(html)

        text = trafilatura.extract(
            clean,
            include_links=False,
            include_images=False,
            include_tables=True,
            include_comments=False,
            include_formatting=False,
            favor_recall=True,
        )

        md = trafilatura.extract(
            clean,
            include_links=True,
            include_images=True,
            include_tables=True,
            include_comments=False,
            include_formatting=True,
            output_format="markdown",
            favor_recall=True,
        )

        return (text or None, md or None)
    except Exception:
        return (None, None)


def _fallback_extract_text(selector) -> str | None:
    """Fallback text extraction when trafilatura returns nothing.

    Looks for ``<main>``, ``<article>``, or ``[role="main"]``, then
    falls back to body content between the first ``<h1>`` and ``<footer>``.
    Strips script/style/nav/header/footer nodes via XPath.
    """
    container = selector.css('main, article, [role="main"]')
    if container:
        container = container[0]
    else:
        body = selector.css("body")
        if not body:
            return None
        container = body[0]

    parts = container.xpath(
        ".//text()[not(ancestor::script)"
        " and not(ancestor::style)"
        " and not(ancestor::noscript)"
        " and not(ancestor::nav)"
        " and not(ancestor::header)"
        " and not(ancestor::footer)"
        " and not(ancestor::form)"
        " and not(ancestor::aside)"
        " and not(ancestor::dialog)"
        " and not(ancestor::*[@aria-modal='true'])"
        " and not(ancestor::*[@role='dialog'])"
        " and not(ancestor::*[@role='alertdialog'])"
        " and not(ancestor::*[@role='complementary'])]"
    ).getall()
    text = _WHITESPACE.sub(" ", " ".join(parts)).strip()
    return text or None


# ---------------------------------------------------------------------------
# Indexability analysis
# ---------------------------------------------------------------------------

def extract_main_content(selector, *, word_count: int | None = None) -> str | None:
    """Extract the main textual content of a page.

    Uses trafilatura for site-agnostic content extraction.  When the page
    has a known *word_count* (total visible words), we can detect cases
    where trafilatura discards too much (hub/landing pages with cards,
    grids, or accordions) and fall back to a simpler full-text extractor.
    """
    raw_html = selector.get()
    if not raw_html:
        return None

    text, _ = _trafilatura_extract(raw_html)
    traf_len = len(text) if text else 0

    if traf_len == 0:
        return _fallback_extract_text(selector)

    # Estimate how many words trafilatura captured vs page total.
    # avg ~5 chars/word in Spanish/English (including spaces).
    traf_words = traf_len / 5 if traf_len else 0
    page_words = word_count or 0

    # If trafilatura captured a decent share of the page, trust it.
    # Threshold: at least 20% of the visible words — below that it is
    # likely discarding real content (cards, grids, accordions, FAQs).
    if page_words > 200 and traf_words < page_words * 0.20:
        fallback = _fallback_extract_text(selector)
        if fallback and len(fallback) > traf_len:
            return fallback

    return text


def _get_main_container_html(selector) -> str | None:
    """Return the inner HTML of the main content container.

    Used only as fallback for markdown extraction.  Strips boilerplate
    (cookie banners, chat widgets, etc.) before returning.
    """
    container = selector.css('main, article, [role="main"]')
    if container:
        node = container[0]
    else:
        body = selector.css("body")
        if not body:
            return None
        node = body[0]
    html = node.get()
    if not html or not html.strip():
        return None
    return _strip_boilerplate_html(html)


def _fallback_extract_markdown(selector) -> str | None:
    """Fallback markdown extraction via html2text on the main container."""
    container_html = _get_main_container_html(selector)
    if not container_html:
        return None
    try:
        import html2text

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = False
        h.ignore_emphasis = False
        h.body_width = 0
        h.skip_internal_links = False
        h.inline_links = True
        h.protect_links = True
        h.ignore_tables = False
        h.single_line_break = False

        md = h.handle(container_html)
        md = re.sub(r'\n{3,}', '\n\n', md).strip()
        return md or None
    except Exception:
        return None


def extract_main_content_markdown(selector, *, word_count: int | None = None) -> str | None:
    """Extract the main content as clean Markdown.

    Uses trafilatura's markdown output for site-agnostic extraction.
    Like ``extract_main_content``, uses *word_count* to detect when
    trafilatura is too aggressive and falls back to html2text.
    """
    raw_html = selector.get()
    if not raw_html:
        return None

    _, md = _trafilatura_extract(raw_html)
    traf_len = len(md) if md else 0

    if traf_len == 0:
        return _fallback_extract_markdown(selector)

    traf_words = traf_len / 5 if traf_len else 0
    page_words = word_count or 0

    if page_words > 200 and traf_words < page_words * 0.20:
        fallback_md = _fallback_extract_markdown(selector)
        if fallback_md and len(fallback_md) > traf_len:
            return fallback_md

    return md


def compute_indexability_status(
    status_code: int | None,
    meta_robots: str | None,
    x_robots: str | None,
    canonical_href: str | None,
    page_url: str,
) -> tuple[bool, str | None]:
    """Determine whether a page is indexable and, if not, why.

    Parameters
    ----------
    status_code:
        HTTP response status code.
    meta_robots:
        Value of the ``<meta name="robots">`` tag (may be ``None``).
    x_robots:
        Value of the ``X-Robots-Tag`` response header (may be ``None``).
    canonical_href:
        The resolved ``<link rel="canonical">`` href (may be ``None``).
    page_url:
        The URL of the page itself (used for canonical comparison).

    Returns
    -------
    tuple[bool, str | None]
        ``(is_indexable, reason)`` -- *reason* is ``None`` when the page
        is indexable; otherwise a short human-readable explanation.
    """
    # 1. Server errors
    if status_code is not None and 500 <= status_code < 600:
        return False, "5xx Server Error"

    # 2. Client errors
    if status_code is not None and 400 <= status_code < 500:
        return False, "4xx Client Error"

    # 3. Redirects
    if status_code is not None and 300 <= status_code < 400:
        return False, "3xx Redirect"

    # 4. Noindex in meta robots
    if meta_robots:
        directives = {d.strip().lower() for d in meta_robots.split(",")}
        if "noindex" in directives:
            return False, "Noindex"

    # 5. Noindex in X-Robots-Tag header
    if x_robots:
        directives = {d.strip().lower() for d in x_robots.split(",")}
        if "noindex" in directives:
            return False, "Noindex"

    # 6. Canonicalised to a different URL
    if canonical_href:
        normalized_canonical = normalize_url(canonical_href)
        normalized_page = normalize_url(page_url)
        if normalized_canonical != normalized_page:
            return False, "Canonicalised"

    return True, None
