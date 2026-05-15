"""Embedding backends: pluggable providers for the semantic engine.

A backend takes a list of page texts and returns a `(n_pages, dim)` matrix of
L2-normalized embeddings — one representative vector per page. The internal
chunking + representative-chunk selection strategy is shared. Today the only
supported provider is Gemini; the abstraction exists so a future second
provider can slot in without touching the engine.
"""
from __future__ import annotations

from POC_centro_semantico.src.embedding_backends.base import (
    EmbeddingBackend,
    ProgressCallback,
)
from POC_centro_semantico.src.embedding_backends.factory import (
    EMBEDDING_DIM,
    SUPPORTED_PROVIDERS,
    get_backend,
)

__all__ = [
    "EmbeddingBackend",
    "ProgressCallback",
    "get_backend",
    "EMBEDDING_DIM",
    "SUPPORTED_PROVIDERS",
]
