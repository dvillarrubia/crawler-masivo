"""Embedding generation with sentence-transformers."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model_cache: dict[str, SentenceTransformer] = {}


def load_model(model_name: str) -> SentenceTransformer:
    """Load a SentenceTransformer model (singleton)."""
    if model_name not in _model_cache:
        from sentence_transformers import SentenceTransformer
        _model_cache[model_name] = SentenceTransformer(model_name)
    return _model_cache[model_name]


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into chunks by paragraph boundaries, with fallback to sentences.

    Returns non-empty chunks of roughly *size* tokens (word-based approximation).
    """
    if not text or not text.strip():
        return []

    # Try paragraphs first
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    units = paragraphs if len(paragraphs) > 1 else _split_sentences(text)

    for unit in units:
        unit_len = len(unit.split())
        if current_len + unit_len > size and current:
            chunks.append(" ".join(current))
            # Keep overlap
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


def _split_sentences(text: str) -> list[str]:
    """Basic sentence splitting."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in parts if s.strip()]


def get_representative_chunk(chunks: list[str], model: SentenceTransformer) -> np.ndarray:
    """Embed all chunks and return the embedding closest to the doc centroid."""
    if len(chunks) == 1:
        return model.encode(chunks, show_progress_bar=False)[0]

    embeddings = model.encode(chunks, show_progress_bar=False)
    centroid = embeddings.mean(axis=0)
    distances = np.linalg.norm(embeddings - centroid, axis=1)
    best_idx = int(np.argmin(distances))
    return embeddings[best_idx]


def embed_pages(
    texts: list[str],
    model: SentenceTransformer,
    batch_size: int = 64,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> np.ndarray:
    """Generate one representative embedding per page.

    For each text: chunk → embed chunks → pick representative chunk embedding.
    Returns array of shape (n_pages, embedding_dim).
    """
    results: list[np.ndarray] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        for text in batch:
            chunks = chunk_text(text, size=chunk_size, overlap=chunk_overlap)
            if not chunks:
                # Fallback: embed the raw text directly
                emb = model.encode([text], show_progress_bar=False)[0]
            else:
                emb = get_representative_chunk(chunks, model)
            results.append(emb)
    return np.array(results)
