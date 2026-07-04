"""
64-bit SimHash for near-duplicate detection (T14).

Own implementation (~40 lines) to avoid a dependency: word-shingle
hashing + weighted bit voting. Deterministic across runs and platforms
(blake2b), so values are comparable between jobs.

Values are stored in a signed BIGINT column: use :func:`to_signed` /
:func:`from_signed` at the DB boundary.
"""

from __future__ import annotations

import hashlib
import re

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_MASK = (1 << 64) - 1


def _hash64(token: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big"
    )


def simhash64(text: str, shingle: int = 3) -> int | None:
    """SimHash of *text* over word shingles. None for empty/too-short text."""
    words = [w.lower() for w in _WORD_RE.findall(text or "")]
    if len(words) < shingle:
        return None

    counts = [0] * 64
    for i in range(len(words) - shingle + 1):
        h = _hash64(" ".join(words[i:i + shingle]))
        for bit in range(64):
            counts[bit] += 1 if (h >> bit) & 1 else -1

    value = 0
    for bit in range(64):
        if counts[bit] > 0:
            value |= 1 << bit
    return value


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & _MASK).bit_count()


def to_signed(value: int) -> int:
    """Unsigned 64-bit → signed (two's complement) for BIGINT storage."""
    return value - (1 << 64) if value >= (1 << 63) else value


def from_signed(value: int) -> int:
    return value + (1 << 64) if value < 0 else value
