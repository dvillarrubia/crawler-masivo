"""
Post-crawl content cleaning engine — pure functions, no DB access.

Pages are grouped by URL *shape* so template-level noise can be removed
in bulk (WebKnoGraph-style, applied a posteriori and optionally).

Rule types (applied in order):
- ``line_exact``     drop lines whose stripped text equals *value*
- ``line_prefix``    drop lines whose stripped text starts with *value*
- ``line_contains``  drop lines containing *value*
- ``cut_from_line``  drop the first line containing *value* and EVERYTHING after
- ``remove_substring`` remove every literal occurrence of *value*
- ``regex``          remove every match of *value* (MULTILINE)

``content_text`` is often a single long line, so line rules are only
useful on markdown; substring/regex rules work on both.
"""

from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import urlparse

# Applying rules must never gut a page: if the cleaned result loses more
# than this fraction of the original characters, the page is skipped.
SAFETY_MAX_REMOVAL = 0.60

LINE_RULE_TYPES = {"line_exact", "line_prefix", "line_contains", "cut_from_line"}
TEXT_RULE_TYPES = {"remove_substring", "regex"}
ALL_RULE_TYPES = LINE_RULE_TYPES | TEXT_RULE_TYPES


def url_group_key(path: str | None) -> str:
    """Collapse a URL path to its template *shape*.

    First segment is kept literal (usually a locale or section); later
    segments become ``{n}`` when they start with a digit (ids/skus) or
    ``{s}`` otherwise.

    Examples
    --------
    /es/literatura/10197-libro-x  -> /es/{s}/{n}
    /es/10000-nathan-hill         -> /es/{n}
    /es/novedades                 -> /es/{s}
    """
    segs = [s for s in (path or "/").split("/") if s]
    if not segs:
        return "/"
    tokens: list[str] = []
    for i, seg in enumerate(segs):
        if seg and seg[0].isdigit():
            tokens.append("{n}")
        elif i == 0:
            tokens.append(seg)
        else:
            tokens.append("{s}")
    return "/" + "/".join(tokens)


def validate_rules(rules: list[dict[str, Any]]) -> list[str]:
    """Return a list of human-readable problems ([] when all rules are valid)."""
    problems: list[str] = []
    if not rules:
        problems.append("No rules provided")
        return problems
    for i, rule in enumerate(rules):
        rtype = rule.get("type")
        value = rule.get("value")
        if rtype not in ALL_RULE_TYPES:
            problems.append(f"Rule {i}: unknown type '{rtype}'")
            continue
        if not value or not str(value).strip():
            problems.append(f"Rule {i}: empty value")
            continue
        if rtype == "regex":
            try:
                re.compile(value)
            except re.error as exc:
                problems.append(f"Rule {i}: invalid regex: {exc}")
        if rtype in LINE_RULE_TYPES and len(str(value).strip()) < 3:
            problems.append(f"Rule {i}: value too short (min 3 chars) for a line rule")
        if rtype == "remove_substring" and len(str(value)) < 4:
            problems.append(f"Rule {i}: value too short (min 4 chars) for remove_substring")
    return problems


def apply_rules(text: str | None, rules: list[dict[str, Any]]) -> str | None:
    """Apply *rules* to *text* and return the cleaned text.

    Returns the input unchanged when it is None/empty.  Whitespace is
    normalised only where lines were removed; untouched lines keep their
    original spacing.
    """
    if not text:
        return text

    cleaned = text

    # Line-based rules first (order preserved within the text)
    line_rules = [r for r in rules if r.get("type") in LINE_RULE_TYPES]
    if line_rules and "\n" in cleaned:
        lines = cleaned.split("\n")
        out: list[str] = []
        cut = False
        for line in lines:
            stripped = line.strip()
            drop = False
            for rule in line_rules:
                rtype, value = rule["type"], str(rule["value"])
                if rtype == "line_exact" and stripped == value:
                    drop = True
                elif rtype == "line_prefix" and stripped.startswith(value):
                    drop = True
                elif rtype == "line_contains" and value in line:
                    drop = True
                elif rtype == "cut_from_line" and value in line:
                    cut = True
                if drop or cut:
                    break
            if cut:
                break
            if not drop:
                out.append(line)
        cleaned = "\n".join(out)

    # Whole-text rules
    for rule in rules:
        rtype, value = rule.get("type"), str(rule.get("value", ""))
        if rtype == "remove_substring":
            cleaned = cleaned.replace(value, " ")
        elif rtype == "regex":
            try:
                cleaned = re.sub(value, " ", cleaned, flags=re.MULTILINE)
            except re.error:
                pass  # validated upstream; never break the batch on one rule

    # Tidy leftover whitespace introduced by removals
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def clean_with_safety(
    text: str | None, rules: list[dict[str, Any]]
) -> tuple[str | None, int, bool]:
    """Apply rules with the safety valve.

    Returns ``(result_text, chars_removed, skipped)``.  When the rules
    would remove more than ``SAFETY_MAX_REMOVAL`` of the content, the
    original text is returned with ``skipped=True``.
    """
    if not text:
        return text, 0, False
    cleaned = apply_rules(text, rules)
    removed = len(text) - len(cleaned or "")
    if len(text) > 0 and removed / len(text) > SAFETY_MAX_REMOVAL:
        return text, 0, True
    return cleaned, removed, False


def make_excerpt(text: str | None, limit: int = 1200) -> str:
    """Head+tail excerpt for previews."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n[...]\n" + text[-half:]


# Texto repetido de un grupo (decision 198). Una linea que sale en al menos la
# mitad de las paginas de la muestra es casi seguro plantilla (menu, pie,
# avisos), y es lo que la limpieza quita: ordenar los grupos por ella pone
# arriba lo que merece la pena limpiar, no lo que mas paginas tiene.
REPETIDO_PARTE = 0.5
REPETIDO_MIN_CHARS = 3   # el minimo de una regla de linea (validate_rules)
REPETIDO_MAX_LINEAS = 8


def lineas_repetidas(
    textos: list[str | None], parte: float = REPETIDO_PARTE
) -> dict[str, Any] | None:
    """Las lineas que se repiten en `textos` (una muestra de paginas de un
    grupo) y cuanto del texto son.

    Devuelve None con menos de 2 paginas con texto: con una no hay nada que
    «se repita». Si no, ``{"muestra": n, "parte": 0-1 de los caracteres que
    son lineas repetidas, "chars_medio": por pagina, "lineas": [{"texto",
    "paginas"}]}``, las lineas de mas caracteres repetidos primero. Una linea
    cuenta una vez por pagina aunque salga dos veces en ella.
    """
    paginas = [t for t in textos if t and t.strip()]
    n = len(paginas)
    if n < 2:
        return None
    umbral = max(2, math.ceil(n * parte))  # nunca una sola pagina
    cuenta: dict[str, int] = {}
    por_pagina: list[list[str]] = []
    for t in paginas:
        lineas = [ln.strip() for ln in t.split("\n")]
        lineas = [ln for ln in lineas if len(ln) >= REPETIDO_MIN_CHARS]
        por_pagina.append(lineas)
        for ln in set(lineas):
            cuenta[ln] = cuenta.get(ln, 0) + 1
    repetidas = {ln: c for ln, c in cuenta.items() if c >= umbral}
    total = sum(len(t) for t in paginas)
    rep = sum(len(ln) for lineas in por_pagina for ln in lineas if ln in repetidas)
    orden = sorted(repetidas.items(), key=lambda kv: (-len(kv[0]) * kv[1], kv[0]))
    return {
        "muestra": n,
        "parte": round(rep / total, 3) if total else 0.0,
        "chars_medio": round(rep / n),
        "lineas": [{"texto": ln[:300], "paginas": c} for ln, c in orden[:REPETIDO_MAX_LINEAS]],
    }
