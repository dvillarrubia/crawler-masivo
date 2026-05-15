"""EmbeddingBackend protocol shared by all providers."""
from __future__ import annotations

from typing import Callable, Protocol

import numpy as np

ProgressCallback = Callable[[int, int], None]  # (current, total) per page


class EmbeddingBackend(Protocol):
    """A provider that turns page texts into one normalized vector per page.

    Implementations are responsible for:
      * chunking (or letting the engine pre-chunk and pick representative),
      * applying any provider-specific prefix / instruction,
      * batching efficiently,
      * returning L2-normalized vectors of length `dim`,
      * calling `progress_callback(done, total)` periodically.
    """

    name: str
    dim: int

    def embed_documents(
        self,
        texts: list[str],
        progress_callback: ProgressCallback | None = None,
    ) -> np.ndarray:
        """Return shape (len(texts), self.dim), L2-normalized."""
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """Return shape (self.dim,), L2-normalized.

        For asymmetric retrieval (E5-instruct, Gemini retrieval task types)
        this wraps the input as a *query*, not a passage.
        """
        ...
