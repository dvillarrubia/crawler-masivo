"""
Single source of truth for URL normalization (T8).

``normalize_url`` feeds ``url_hash``, the dedup key for every join in the
system, so its default behaviour is sacred: with no config (or a default
config) the output is bit-for-bit identical to the historical
``w3lib.canonicalize_url(url, keep_fragments=False)`` — protected by the
golden list in ``tests/python/test_normalization_golden.py``.

Per-job configuration (``JobConfig.url_normalization``) can strip specific
query parameters and/or a maintained list of common tracking parameters.
Jobs that use a non-default config get a ``normalization_fingerprint``
stored on the ``jobs`` row; two jobs are only comparable (T7 diff) when
their fingerprints match (both NULL = both default).

The crawler runs one subprocess per job, so the active per-job config is
process-global: the spider calls :func:`set_active_config` once at startup
and every ``normalize_url`` call without an explicit config picks it up.

This module also hosts :func:`normalize_for_match` — the *matching*
normalizer used for cross-system joins (GSC ↔ crawl). It intentionally has
looser semantics (lowercase, trailing-slash strip) and is NOT used for
``url_hash``. It replaces the old private copy in ``api/routers/semantic.py``
so there is exactly one tracking-param list in the codebase (C4).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from w3lib.url import canonicalize_url

# ---------------------------------------------------------------------------
# Tracking parameters (single maintained list — C4)
# ---------------------------------------------------------------------------

# Any query param that starts with one of these prefixes is tracking.
TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_",)

# Exact (lowercased) tracking param names.
TRACKING_PARAMS: frozenset[str] = frozenset({
    "gclid",       # Google Ads
    "fbclid",      # Facebook
    "msclkid",     # Microsoft Ads
    "mc_cid",      # Mailchimp campaign
    "mc_eid",      # Mailchimp email
    "_ga",         # Google Analytics linker
    "_gl",         # Google Analytics cross-domain
    "yclid",       # Yandex
    "igshid",      # Instagram
})


def is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in TRACKING_PARAMS or lowered.startswith(TRACKING_PARAM_PREFIXES)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UrlNormalizationConfig:
    """Per-job URL normalization policy.

    The default instance reproduces the historical behaviour exactly and
    yields a NULL fingerprint.
    """

    strip_params: tuple[str, ...] = field(default=())
    strip_common_tracking: bool = False

    @classmethod
    def from_job_config(cls, job_config: dict[str, Any] | None) -> "UrlNormalizationConfig":
        raw = ((job_config or {}).get("url_normalization") or {})
        return cls(
            strip_params=tuple(raw.get("strip_params") or ()),
            strip_common_tracking=bool(raw.get("strip_common_tracking", False)),
        )

    def is_default(self) -> bool:
        return not self.strip_params and not self.strip_common_tracking

    def fingerprint(self) -> str | None:
        """sha256 of the canonical JSON of this config; None for the default.

        NULL fingerprints are comparable with each other (T7); param order
        does not matter.
        """
        if self.is_default():
            return None
        canonical = json.dumps(
            {
                "strip_common_tracking": self.strip_common_tracking,
                "strip_params": sorted(self.strip_params),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _should_strip(self, param: str) -> bool:
        if param in self.strip_params:
            return True
        return self.strip_common_tracking and is_tracking_param(param)


DEFAULT_CONFIG = UrlNormalizationConfig()

# ---------------------------------------------------------------------------
# Per-process active config (one crawl subprocess = one job)
# ---------------------------------------------------------------------------

_active_config: UrlNormalizationConfig = DEFAULT_CONFIG


def set_active_config(config: UrlNormalizationConfig) -> None:
    """Set the process-wide config (called once by the spider at startup)."""
    global _active_config
    _active_config = config


def get_active_config() -> UrlNormalizationConfig:
    return _active_config


# ---------------------------------------------------------------------------
# Normalization (url_hash semantics)
# ---------------------------------------------------------------------------

def _strip_query_params(url: str, config: UrlNormalizationConfig) -> str:
    parts = urlparse(url)
    if not parts.query:
        return url
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not config._should_strip(k)
    ]
    return urlunparse(parts._replace(query=urlencode(kept)))


def normalize_url(url: str, config: UrlNormalizationConfig | None = None) -> str:
    """Canonicalize a URL for dedup.

    With a default config this is bit-for-bit the historical behaviour
    (w3lib ``canonicalize_url`` with fragments dropped). When *config* is
    None the process-wide active config applies.
    """
    cfg = config if config is not None else _active_config
    if not cfg.is_default():
        url = _strip_query_params(url, cfg)
    return canonicalize_url(url, keep_fragments=False)


def compute_url_hash(url: str, config: UrlNormalizationConfig | None = None) -> str:
    """Hex SHA-256 of the normalized URL (the system-wide dedup key)."""
    normalized = normalize_url(url, config)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Matching normalizer (cross-system joins, NOT url_hash) — C4
# ---------------------------------------------------------------------------

def normalize_for_match(url: str) -> str:
    """Lowercase + strip trailing slash + drop tracking params.

    Used for fuzzy joins between external sources (GSC) and crawled URLs,
    where minor formatting differences would silently drop rows. Loose on
    purpose; never use it to compute ``url_hash``.
    """
    if not url:
        return url
    parts = urlparse(url.strip().lower())
    kept = [(k, v) for k, v in parse_qsl(parts.query) if not is_tracking_param(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunparse(
        (parts.scheme, parts.netloc, path, parts.params, urlencode(kept), "")
    )
