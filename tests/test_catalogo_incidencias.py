"""Ningun tipo que emite el analizador se queda sin ficha en `incidencias.js`.

Es el criterio de la ola 1 del plan (`grep` del analyzer contra el catalogo):
la vista agrupada de Problemas enseña la ficha —que es, que decide Google,
como se arregla— y un tipo sin ella sale con su nombre tecnico en la reunion
con el cliente. Lee el fuente a proposito: lo que se comprueba es que dos
ficheros digan lo mismo, no un comportamiento (decision 74).
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _tipos_emitidos() -> set[str]:
    fuente = (RAIZ / "analysis" / "analyzer.py").read_text(encoding="utf-8")
    tipos = set(re.findall(r'_add_issue\(\s*[^,()]+,\s*"([a-z0-9_]+)"', fuente))
    # La forma partida en lineas: `self._add_issue(\n url_id,\n "tipo",\n "sev"`.
    tipos |= set(re.findall(
        r'^\s*"([a-z0-9_]+)",\s*\n\s*"(?:error|warning|info)"', fuente, re.M))
    return tipos


def test_el_patron_encuentra_los_tipos():
    tipos = _tipos_emitidos()
    # Si el patron se rompe, el test de abajo pasaria sin mirar nada.
    assert {"4xx_error", "hreflang_missing_return",
            "hreflang_destino_no_indexable"} <= tipos
    assert len(tipos) > 40


def test_cada_tipo_emitido_tiene_ficha():
    catalogo = (RAIZ / "frontend" / "incidencias.js").read_text(encoding="utf-8")
    sin_ficha = sorted(t for t in _tipos_emitidos() if f"'{t}': {{" not in catalogo)
    assert not sin_ficha, f"tipos sin entrada en ISSUE_INFO: {sin_ficha}"
