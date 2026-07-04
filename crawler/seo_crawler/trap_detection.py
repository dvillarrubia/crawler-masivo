"""
Crawl-trap detection (T13).

Protects the crawl budget from faceted navigation, infinite calendars and
session-parameter explosions. Pure in-memory logic (one instance per crawl
subprocess); the spider gates enqueueing through :meth:`TrapDetector.allow`
and persists the events at close. Nothing is lost silently: every capped
pattern becomes a ``crawl_trap_events`` row and a ``crawl_trap_detected``
issue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlparse

_NUMERIC_SEGMENT = re.compile(r"^\d+$")


def pattern_signature(url: str) -> str:
    """Template signature of a URL: numeric path segments and query values
    become wildcards. ``/producto/123?color=rojo&talla=m`` →
    ``/producto/*?color=*&talla=*`` (params sorted for stability).
    """
    parts = urlparse(url)
    segments = [
        "*" if _NUMERIC_SEGMENT.match(seg) else seg
        for seg in parts.path.split("/")
    ]
    path = "/".join(segments)
    keys = sorted({k for k, _ in parse_qsl(parts.query, keep_blank_values=True)})
    query = "&".join(f"{k}=*" for k in keys)
    host = parts.netloc.lower()
    return f"{host}{path}" + (f"?{query}" if query else "")


@dataclass
class _PatternStats:
    seen: int = 0
    skipped: int = 0
    first_url: str | None = None


@dataclass
class TrapDetector:
    max_urls_per_pattern: int = 500
    max_param_combinations: int = 3
    _stats: dict[str, _PatternStats] = field(default_factory=dict)

    def allow(self, url: str) -> bool:
        """True if the URL may be enqueued; False when its pattern is capped.

        Two triggers:
        * the pattern already produced ``max_urls_per_pattern`` URLs;
        * the URL combines more than ``max_param_combinations`` distinct
          query params (typical faceted explosion) — capped immediately.
        """
        sig = pattern_signature(url)
        stats = self._stats.setdefault(sig, _PatternStats())
        if stats.first_url is None:
            stats.first_url = url

        n_params = len(parse_qsl(urlparse(url).query, keep_blank_values=True))
        over_params = n_params > self.max_param_combinations
        over_count = stats.seen >= self.max_urls_per_pattern

        if over_params or over_count:
            stats.skipped += 1
            return False
        stats.seen += 1
        return True

    def events(self) -> list[dict]:
        """Capped patterns, ready to persist as ``crawl_trap_events``."""
        return [
            {
                "pattern": sig,
                "urls_seen": s.seen,
                "urls_skipped": s.skipped,
                "first_url_sample": s.first_url,
            }
            for sig, s in self._stats.items()
            if s.skipped > 0
        ]
