"""Structured-data validation helpers.

Pure functions (no SQLAlchemy / DB imports) that validate a structured-data
block extracted by the crawler. Kept separate from ``analyzer.py`` so they can
be unit-tested in isolation.

Validation is deliberately conservative: only genuinely mandatory fields for
common Google rich-result types are checked, and any unexpected shape is
treated as valid, so it never produces false positives on real markup.
"""

from __future__ import annotations

# Minimum required properties for common schema.org types used in Google
# rich results. Kept small on purpose — only truly mandatory fields.
SD_REQUIRED_PROPS: dict[str, list[str]] = {
    "product": ["name"],
    "article": ["headline"],
    "newsarticle": ["headline"],
    "blogposting": ["headline"],
    "breadcrumblist": ["itemlistelement"],
    "recipe": ["name"],
    "event": ["name", "startdate"],
    "faqpage": ["mainentity"],
    "qapage": ["mainentity"],
    "localbusiness": ["name", "address"],
    "organization": ["name"],
    "person": ["name"],
    "videoobject": ["name", "thumbnailurl", "uploaddate"],
    "jobposting": ["title", "dateposted", "hiringorganization"],
}


def sd_item_types(item: dict) -> list[str]:
    """Return the declared ``@type``(s) of a structured-data item."""
    t = item.get("@type", item.get("type"))
    if t is None:
        return []
    if isinstance(t, list):
        return [str(x) for x in t if x]
    return [str(t)]


def validate_sd_item(item: dict) -> list[str]:
    """Return a list of validation problems for a single SD entity."""
    problems: list[str] = []
    types = sd_item_types(item)
    if not types:
        problems.append("missing @type")
        return problems
    present = {k.lower() for k in item.keys()}
    for t in types:
        for prop in SD_REQUIRED_PROPS.get(t.lower(), []):
            if prop not in present:
                problems.append(f"{t}: missing required property '{prop}'")
    return problems


def validate_structured_data(raw) -> tuple[str, list[str]]:
    """Validate a structured-data block.

    Returns ``(status, issues)`` where *status* is ``"ok"``, ``"warning"``
    (present but missing a required property), or ``"error"`` (missing
    ``@type`` entirely). Handles JSON-LD ``@graph`` containers and bare
    lists. Any unexpected shape is treated as ``"ok"`` (no false positives).
    """
    items: list[dict] = []
    if isinstance(raw, dict):
        graph = raw.get("@graph")
        if isinstance(graph, list) and graph:
            items = [g for g in graph if isinstance(g, dict)]
        else:
            items = [raw]
    elif isinstance(raw, list):
        items = [g for g in raw if isinstance(g, dict)]
    else:
        return ("ok", [])

    issues: list[str] = []
    for it in items:
        issues += validate_sd_item(it)
    if not issues:
        return ("ok", [])
    status = "error" if any("missing @type" in i for i in issues) else "warning"
    return (status, issues)
