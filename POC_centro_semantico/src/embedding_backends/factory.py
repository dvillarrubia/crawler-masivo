"""Backend factory.

Currently Gemini-only. Kept as a factory so a future second provider can
slot in without touching the engine. The dimension is part of the
product contract because pgvector columns are sized at schema time —
changing it requires a migration.
"""
from __future__ import annotations

from POC_centro_semantico.src.embedding_backends.base import EmbeddingBackend
from POC_centro_semantico.src.embedding_backends.gemini import (
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_DIM,
    GeminiBackend,
)

EMBEDDING_DIM = DEFAULT_OUTPUT_DIM  # 1024, matches pgvector(1024) schema
SUPPORTED_PROVIDERS = ("gemini",)


def get_backend(
    provider: str = "gemini",
    model: str | None = None,
    api_key: str | None = None,
) -> EmbeddingBackend:
    """Return an EmbeddingBackend instance.

    Args:
        provider: must be "gemini" today.
        model: provider-specific model id. Defaults to gemini-embedding-001.
        api_key: optional override; otherwise read from GEMINI_API_KEY
            or GOOGLE_API_KEY env vars.
    """
    if provider != "gemini":
        raise ValueError(
            f"Unsupported embedding provider: {provider!r}. "
            f"Supported: {SUPPORTED_PROVIDERS}"
        )
    return GeminiBackend(
        model=model or DEFAULT_MODEL,
        dim=EMBEDDING_DIM,
        api_key=api_key,
    )
