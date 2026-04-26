"""
Scrapy Item definitions matching the shared SQLAlchemy models.

Each item class maps to a database table and carries exactly the
fields the pipeline needs for persistence.
"""

import scrapy


class PageItem(scrapy.Item):
    """Core URL record -- one per response."""

    url = scrapy.Field()
    url_hash = scrapy.Field()
    host = scrapy.Field()
    path = scrapy.Field()
    scheme = scrapy.Field()
    is_internal = scrapy.Field()
    crawl_depth = scrapy.Field()
    content_type = scrapy.Field()
    content_length = scrapy.Field()
    status_code = scrapy.Field()
    status_group = scrapy.Field()
    response_time_ms = scrapy.Field()
    is_html = scrapy.Field()
    resource_type = scrapy.Field()
    redirect_url = scrapy.Field()
    body_hash = scrapy.Field()
    job_id = scrapy.Field()

    # Screaming Frog parity fields
    url_length = scrapy.Field()
    folder_depth = scrapy.Field()
    word_count = scrapy.Field()
    text_ratio = scrapy.Field()
    redirect_type = scrapy.Field()
    status_text = scrapy.Field()
    last_modified = scrapy.Field()
    http_version = scrapy.Field()
    transfer_size = scrapy.Field()
    indexability_status = scrapy.Field()


class HtmlMetaItem(scrapy.Item):
    """On-page SEO metadata extracted from HTML <head>."""

    url_hash = scrapy.Field()  # used to look up parent Url row
    job_id = scrapy.Field()

    title = scrapy.Field()
    title_len = scrapy.Field()
    meta_description = scrapy.Field()
    meta_description_len = scrapy.Field()
    meta_keywords = scrapy.Field()
    meta_robots = scrapy.Field()
    x_robots_tag = scrapy.Field()

    canonical_href = scrapy.Field()
    canonical_header = scrapy.Field()

    og_title = scrapy.Field()
    og_description = scrapy.Field()
    og_image = scrapy.Field()
    og_url = scrapy.Field()
    og_type = scrapy.Field()

    twitter_card = scrapy.Field()
    twitter_title = scrapy.Field()
    twitter_description = scrapy.Field()

    rel_next = scrapy.Field()
    rel_prev = scrapy.Field()

    # Screaming Frog parity fields
    title_pixel_width = scrapy.Field()
    meta_description_pixel_width = scrapy.Field()
    meta_refresh = scrapy.Field()
    has_meta_outside_head = scrapy.Field()


class HeadingItem(scrapy.Item):
    """A single heading element (h1-h6)."""

    url_hash = scrapy.Field()
    job_id = scrapy.Field()
    tag = scrapy.Field()
    position = scrapy.Field()
    text = scrapy.Field()


class LinkItem(scrapy.Item):
    """A hyperlink from one page to another."""

    from_url_hash = scrapy.Field()
    to_url = scrapy.Field()
    to_url_hash = scrapy.Field()
    anchor_text = scrapy.Field()
    rel = scrapy.Field()
    is_internal = scrapy.Field()
    link_position = scrapy.Field()
    job_id = scrapy.Field()

    # Screaming Frog parity fields
    follow = scrapy.Field()
    target = scrapy.Field()
    alt_text = scrapy.Field()
    link_type = scrapy.Field()


class HreflangItem(scrapy.Item):
    """An hreflang annotation."""

    url_hash = scrapy.Field()
    job_id = scrapy.Field()
    lang = scrapy.Field()
    href = scrapy.Field()


class StructuredDataItem(scrapy.Item):
    """A structured-data block (JSON-LD, Microdata, RDFa)."""

    url_hash = scrapy.Field()
    job_id = scrapy.Field()
    raw = scrapy.Field()
    format = scrapy.Field()
    schema_type = scrapy.Field()


class ResourceItem(scrapy.Item):
    """A page-level resource reference (img, script, stylesheet, ...)."""

    url_hash = scrapy.Field()
    job_id = scrapy.Field()
    resource_url = scrapy.Field()
    resource_type = scrapy.Field()
    alt_text = scrapy.Field()

    # Screaming Frog parity fields
    width = scrapy.Field()
    height = scrapy.Field()
    is_mixed_content = scrapy.Field()


class ContentItem(scrapy.Item):
    """Main textual content of a page -- one per HTML response."""

    url_hash = scrapy.Field()
    job_id = scrapy.Field()
    content_text = scrapy.Field()
    content_length = scrapy.Field()
    content_markdown = scrapy.Field()


class SecurityItem(scrapy.Item):
    """Security header analysis for a page -- one per response."""

    url_hash = scrapy.Field()
    job_id = scrapy.Field()
    is_https = scrapy.Field()
    has_mixed_content = scrapy.Field()
    has_hsts = scrapy.Field()
    has_csp = scrapy.Field()
    has_x_content_type_options = scrapy.Field()
    has_x_frame_options = scrapy.Field()
    referrer_policy = scrapy.Field()
    has_unsafe_crossorigin = scrapy.Field()
