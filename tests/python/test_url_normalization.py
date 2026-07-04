"""
Tests de T8 — política de parámetros de URL (normalización configurable).

La lista dorada (test_normalization_golden.py) protege el default bit a bit;
aquí se prueba la parte nueva: configs con strip, fingerprint, config activa
por proceso y el normalizador de matching unificado (C4).
"""

from __future__ import annotations

import pytest

from shared.url_normalization import (
    DEFAULT_CONFIG,
    UrlNormalizationConfig,
    compute_url_hash,
    get_active_config,
    is_tracking_param,
    normalize_for_match,
    normalize_url,
    set_active_config,
)


# ---------------------------------------------------------------------------
# Default = comportamiento histórico
# ---------------------------------------------------------------------------

def test_default_config_matches_w3lib_bit_for_bit():
    from w3lib.url import canonicalize_url

    urls = [
        "https://Example.COM/Path?b=2&a=1#frag",
        "https://example.com/a?utm_source=x&id=5",
        "https://example.com/ñoño?q=café",
    ]
    for u in urls:
        assert normalize_url(u) == canonicalize_url(u, keep_fragments=False)
        assert normalize_url(u, DEFAULT_CONFIG) == normalize_url(u)


def test_extractors_delegate_to_shared_module():
    """Las funciones históricas de extractors.py son la misma semántica."""
    from seo_crawler.extractors import (
        compute_url_hash as legacy_hash,
        normalize_url as legacy_norm,
    )

    u = "https://Example.com/A?b=2&a=1&utm_source=x#s"
    assert legacy_norm(u) == normalize_url(u)
    assert legacy_hash(u) == compute_url_hash(u)


# ---------------------------------------------------------------------------
# Propiedad: N escrituras → mismo hash bajo la misma config
# ---------------------------------------------------------------------------

TRACKING_CFG = UrlNormalizationConfig(strip_common_tracking=True)

SPELLINGS_SAME_PAGE = [
    "https://example.com/page?b=2&a=1",
    "https://EXAMPLE.com/page?a=1&b=2",
    "https://example.com/page?b=2&a=1#section",
    "https://example.com/page?a=1&utm_source=news&b=2",
    "https://example.com/page?gclid=XYZ&a=1&b=2&fbclid=AB",
    "https://example.com/page?a=1&b=2&msclkid=m&mc_cid=c&mc_eid=e",
    "https://example.com/page?_ga=1.2.3&a=1&b=2&utm_campaign=x&utm_medium=y",
]


def test_property_spellings_collapse_under_tracking_config():
    hashes = {compute_url_hash(u, TRACKING_CFG) for u in SPELLINGS_SAME_PAGE}
    assert len(hashes) == 1


def test_without_config_tracking_variants_do_not_collapse():
    clean = compute_url_hash("https://example.com/page?a=1&b=2")
    dirty = compute_url_hash("https://example.com/page?a=1&b=2&utm_source=news")
    assert clean != dirty


def test_strip_params_custom():
    cfg = UrlNormalizationConfig(strip_params=("sessionid", "ref"))
    assert (
        normalize_url("https://example.com/p?sessionid=99&x=1&ref=home", cfg)
        == "https://example.com/p?x=1"
    )
    # los que no están en la lista se conservan, con valores vacíos incluidos
    assert (
        normalize_url("https://example.com/p?keep=&sessionid=1", cfg)
        == "https://example.com/p?keep="
    )


def test_strip_all_params_leaves_clean_url():
    cfg = UrlNormalizationConfig(strip_common_tracking=True)
    assert (
        normalize_url("https://example.com/p?utm_source=a&utm_medium=b", cfg)
        == "https://example.com/p"
    )


def test_is_tracking_param():
    assert is_tracking_param("utm_source")
    assert is_tracking_param("UTM_CAMPAIGN")
    assert is_tracking_param("gclid")
    assert is_tracking_param("MSCLKID")
    assert not is_tracking_param("id")
    assert not is_tracking_param("uttm_source")


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_default_is_none():
    assert DEFAULT_CONFIG.fingerprint() is None
    assert UrlNormalizationConfig().fingerprint() is None
    assert UrlNormalizationConfig.from_job_config({}).fingerprint() is None
    assert UrlNormalizationConfig.from_job_config(None).fingerprint() is None


def test_fingerprint_is_order_independent_and_distinct():
    a = UrlNormalizationConfig(strip_params=("x", "y"))
    b = UrlNormalizationConfig(strip_params=("y", "x"))
    c = UrlNormalizationConfig(strip_params=("x",))
    d = UrlNormalizationConfig(strip_common_tracking=True)
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != c.fingerprint()
    assert a.fingerprint() != d.fingerprint()
    assert len(a.fingerprint()) == 64


def test_from_job_config_reads_url_normalization_key():
    cfg = UrlNormalizationConfig.from_job_config({
        "max_depth": 3,
        "url_normalization": {
            "strip_params": ["sid"],
            "strip_common_tracking": True,
        },
    })
    assert cfg.strip_params == ("sid",)
    assert cfg.strip_common_tracking is True
    assert not cfg.is_default()


# ---------------------------------------------------------------------------
# Config activa por proceso (la que activa el spider)
# ---------------------------------------------------------------------------

def test_active_config_applies_to_implicit_calls():
    set_active_config(TRACKING_CFG)
    assert get_active_config() is TRACKING_CFG
    # llamadas sin config explícita (como las de extractors) la usan
    from seo_crawler.extractors import normalize_url as legacy_norm

    assert (
        legacy_norm("https://example.com/p?utm_source=x&a=1")
        == "https://example.com/p?a=1"
    )


def test_pydantic_schema_default_is_default_semantics():
    """El JobConfig por defecto de la API produce fingerprint NULL."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    try:
        from api.schemas import JobConfig
    except ImportError:
        pytest.skip("api deps (fastapi/pydantic) not installed in test env")

    dumped = JobConfig().model_dump()
    cfg = UrlNormalizationConfig.from_job_config(dumped)
    assert cfg.is_default()
    assert cfg.fingerprint() is None


# ---------------------------------------------------------------------------
# Normalizador de matching unificado (C4)
# ---------------------------------------------------------------------------

def _legacy_normalize_url_for_match(u: str) -> str:
    """Copia literal del normalizador que vivía en api/routers/semantic.py,
    como referencia de comportamiento para la unificación C4."""
    if not u:
        return u
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
    parts = urlparse(u.strip().lower())
    drop = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "mc_cid", "mc_eid", "_ga",
    }
    q = [(k, v) for k, v in parse_qsl(parts.query) if k not in drop]
    path = parts.path.rstrip("/") or "/"
    return urlunparse((parts.scheme, parts.netloc, path, parts.params, urlencode(q), ""))


@pytest.mark.parametrize("url", [
    "",
    "https://example.com/",
    "https://Example.COM/Page/",
    "https://example.com/page?utm_source=x&b=2&a=1",
    "https://example.com/page?fbclid=1&gclid=2&mc_cid=3&mc_eid=4&_ga=5",
    "https://example.com/page/?id=7#frag",
    "  https://example.com/spaces  ",
])
def test_match_normalizer_preserves_legacy_behaviour(url):
    assert normalize_for_match(url) == _legacy_normalize_url_for_match(url)


def test_match_normalizer_extends_tracking_coverage():
    """La lista unificada añade msclkid/_gl/yclid al matching (superset)."""
    assert (
        normalize_for_match("https://example.com/p?msclkid=1&a=2")
        == "https://example.com/p?a=2"
    )
