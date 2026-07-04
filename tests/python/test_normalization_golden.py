"""
Lista dorada de normalización de URLs (Fase 0.2 — prerrequisito de T8).

Congela la salida EXACTA de ``normalize_url`` y ``compute_url_hash`` para
33 URLs representativas. Si este test falla, la semántica de deduplicación
ha cambiado: los ``url_hash`` de crawls anteriores dejan de casar con los
nuevos y se rompe la comparabilidad entre jobs (T7 diff, frescura T5…).

Un fallo aquí NUNCA se arregla actualizando el JSON a la ligera:
1. Si el cambio es un accidente → revertir el cambio de código.
2. Si es intencionado (T8) → debe ir detrás de un flag de JobConfig con
   default = comportamiento actual, y este test debe seguir pasando para
   el default. Solo entonces se regenera el JSON con
   ``regenerate_normalization_golden.py`` (revisando el diff URL a URL).

Hechos notables que esta lista deja congelados (semántica w3lib actual):
* Los puertos por defecto ``:443``/``:80`` NO se eliminan.
* Los parámetros de tracking (utm_*, fbclid, gclid) NO se eliminan.
* El trailing slash NO se normaliza (``/a`` ≠ ``/a/``).
* Los dot-segments (``/a/../b``) NO se resuelven.
* La query se ordena alfabéticamente y el fragmento se elimina.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "normalization_golden.json"
GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "entry", GOLDEN, ids=[e["input"] for e in GOLDEN]
)
def test_normalization_is_frozen(entry):
    from seo_crawler.extractors import compute_url_hash, normalize_url

    assert normalize_url(entry["input"]) == entry["normalized"], (
        "normalize_url ha cambiado de semántica para esta URL. "
        "Lee el docstring del módulo antes de tocar el JSON dorado."
    )
    assert compute_url_hash(entry["input"]) == entry["url_hash"]


def test_golden_list_covers_minimum():
    """El documento maestro exige ≥30 URLs representativas."""
    assert len(GOLDEN) >= 30
    # sin duplicados de input
    assert len({e["input"] for e in GOLDEN}) == len(GOLDEN)


def test_hash_is_sha256_of_normalized():
    """Invariante estructural: hash = sha256(normalized), no de la cruda."""
    import hashlib

    for e in GOLDEN:
        expected = hashlib.sha256(e["normalized"].encode("utf-8")).hexdigest()
        assert e["url_hash"] == expected, e["input"]
