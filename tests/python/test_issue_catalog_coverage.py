"""
Guardia: todo issue_type que emite el análisis debe tener entrada en el
catálogo del frontend (nombre + explicación). Si no, en Incidencias sale
el aviso «tipo sin descripción en el catálogo».

Regenera el catálogo si añades un issue_type nuevo:
`frontend-v2/src/issueCatalog.js` (objeto ISSUE_CATALOG).
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Tipos que NO se muestran en la lista de Incidencias por tipo (métricas de
# grafo agregadas, no incidencias por-URL): se permiten fuera del catálogo.
_NON_ISSUE = set()


def _emitted_types() -> set[str]:
    types: set[str] = set()
    for sub in ("analysis", "crawler"):
        for p in (ROOT / sub).rglob("*.py"):
            t = p.read_text(encoding="utf-8")
            types |= set(re.findall(r'_add_issue\(\s*[^,]+,\s*["\']([a-z0-9_]+)["\']', t))
            types |= set(re.findall(r'issue_type\s*=\s*["\']([a-z0-9_]+)["\']', t))
            types |= set(re.findall(r'"issue_type"\s*:\s*["\']([a-z0-9_]+)["\']', t))
    return types - _NON_ISSUE


def _catalog_keys() -> set[str]:
    js = (ROOT / "frontend-v2" / "src" / "issueCatalog.js").read_text(encoding="utf-8")
    # corta en el primer objeto (ISSUE_CATALOG) para no coger DETAIL_RENDERERS
    body = js.split("const DETAIL_RENDERERS")[0]
    return set(re.findall(r'^\s{2}"?([a-z0-9_]+)"?:\s*\[', body, re.M))


def test_every_emitted_issue_type_is_in_catalog():
    emitted = _emitted_types()
    catalog = _catalog_keys()
    missing = sorted(emitted - catalog)
    assert not missing, (
        "Estos issue_type se emiten pero no están en issueCatalog.js "
        f"(saldría «sin descripción» en Incidencias): {missing}")


def test_catalog_has_the_previously_missing_ones():
    # regresión concreta del reporte del usuario
    catalog = _catalog_keys()
    for t in ("content_only_after_js", "schema_only_after_js",
              "no_inlinks_with_traffic", "underlinked_high_performer"):
        assert t in catalog, f"{t} debería estar en el catálogo"
