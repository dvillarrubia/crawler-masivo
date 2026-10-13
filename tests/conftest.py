"""Pytest path setup so tests can import the crawler and analysis packages.

Adds the repo root (for the ``analysis`` package) and the ``crawler`` dir
(for the ``seo_crawler`` package) to ``sys.path``.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRAWLER = os.path.join(ROOT, "crawler")

TESTS = os.path.dirname(os.path.abspath(__file__))

for path in (ROOT, CRAWLER, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# Modelos de lenguaje: ningun test llama a la red (#7, decision 84)
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_llm():
    """La clase `FakeLLM` (`tests/fake_llm.py`): respuestas fijas, 429 o 503."""
    from fake_llm import FakeLLM

    return FakeLLM


@pytest.fixture(autouse=True)
def _sin_llamadas_a_modelos_de_pago(monkeypatch):
    """Red de seguridad para TODA la suite: aunque alguien tenga una clave en
    su entorno, ningun test sale hacia OpenRouter ni TypeSafe. Se borran la
    clave y el flag, y un transporte real de httpx hacia esos hosts falla."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DECISIONS_ENABLED", raising=False)
    try:
        import httpx  # noqa: F401
    except ImportError:
        yield
        return
    from fake_llm import vigilar_httpx

    vigilar_httpx(monkeypatch.setattr)
    yield
