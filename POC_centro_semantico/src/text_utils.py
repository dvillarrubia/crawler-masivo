"""Provider-agnostic text utilities: chunking + numeric helpers.

These functions have no dependency on any embedding model and are reused
by every embedding backend (chunking inputs, normalising outputs).
"""
from __future__ import annotations

import re

import numpy as np


def _split_sentences(text: str) -> list[str]:
    """Basic sentence splitting on `.!?` boundaries."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in parts if s.strip()]


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into chunks by paragraph boundaries, with sentence fallback.

    Returns non-empty chunks of roughly *size* tokens (word-based
    approximation). Paragraphs longer than *size* are subdivided into
    sentences first so no individual chunk grows unboundedly large.
    """
    if not text or not text.strip():
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    raw_units = paragraphs if len(paragraphs) > 1 else _split_sentences(text)

    units: list[str] = []
    for unit in raw_units:
        if len(unit.split()) > size:
            units.extend(_split_sentences(unit))
        else:
            units.append(unit)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for unit in units:
        unit_len = len(unit.split())
        if current_len + unit_len > size and current:
            chunks.append(" ".join(current))
            overlap_words: list[str] = []
            overlap_count = 0
            for part in reversed(current):
                wc = len(part.split())
                if overlap_count + wc > overlap:
                    break
                overlap_words.insert(0, part)
                overlap_count += wc
            current = overlap_words
            current_len = overlap_count
        current.append(unit)
        current_len += unit_len

    if current:
        chunks.append(" ".join(current))

    return [c for c in chunks if len(c.split()) >= 10]


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    """L2-normalize a vector or each row of a 2D matrix. Safe for zero rows."""
    if vec.ndim == 1:
        n = float(np.linalg.norm(vec))
        return vec if n == 0 else vec / n
    norms = np.linalg.norm(vec, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vec / norms
