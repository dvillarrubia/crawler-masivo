"""Gemini embedding backend (sole runtime provider).

Uses Google's `gemini-embedding-001` model via the official `google-genai`
SDK. We request `output_dimensionality=1024` (Matryoshka Representation
Learning) so the result fits the existing `pgvector(1024)` columns with
no schema migration.

Design notes:
- Chunking + representative-chunk selection: long pages are chunked
  paragraph- and sentence-aware, every chunk is embedded, and the chunk
  most aligned with the page's own centroid is chosen as the page vector.
  Same strategy spec'd in sem_seo_engine_v2 §4B.
- Task types are asymmetric: documents use RETRIEVAL_DOCUMENT, queries
  use RETRIEVAL_QUERY. This matches how the model was trained and gives
  meaningfully better cross-document similarity than treating both sides
  symmetrically.
- Requests are batched (default 100 contents per call) with exponential
  backoff for transient API errors.
- Progress is reported in *page* units even though batching happens at
  chunk level, so the UI shows "embedding 234/789" the way users expect.
"""
from __future__ import annotations

import logging
import os

import numpy as np

from POC_centro_semantico.src.embedding_backends.base import ProgressCallback
from POC_centro_semantico.src.text_utils import chunk_text, l2_normalize

logger = logging.getLogger(__name__)


DEFAULT_MODEL = "gemini-embedding-001"
DEFAULT_OUTPUT_DIM = 1024
DEFAULT_BATCH_SIZE = 100

DOC_CHUNK_SIZE = 500
DOC_CHUNK_OVERLAP = 50


class GeminiBackend:
    name = "gemini"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        dim: int = DEFAULT_OUTPUT_DIM,
        api_key: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self.model = model
        self.dim = dim
        self.batch_size = batch_size

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "GeminiBackend requires GEMINI_API_KEY (or GOOGLE_API_KEY) "
                "to be set or passed explicitly."
            )

        from google import genai  # lazy: package optional at import time

        self._client = genai.Client(api_key=key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def embed_documents(
        self,
        texts: list[str],
        progress_callback: ProgressCallback | None = None,
    ) -> np.ndarray:
        """Return one L2-normalized representative vector per page."""
        total = len(texts)
        if total == 0:
            return np.zeros((0, self.dim), dtype=np.float32)

        per_page_chunks: list[list[str]] = []
        flat_chunks: list[str] = []
        for text in texts:
            chunks = chunk_text(text, size=DOC_CHUNK_SIZE, overlap=DOC_CHUNK_OVERLAP)
            if not chunks:
                chunks = [text or " "]
            per_page_chunks.append(chunks)
            flat_chunks.extend(chunks)

        flat_embeddings = self._embed_batch(
            flat_chunks,
            task_type="RETRIEVAL_DOCUMENT",
            progress_callback=self._wrap_chunk_progress(
                progress_callback, per_page_chunks, total
            ),
        )

        out: list[np.ndarray] = []
        offset = 0
        for chunks in per_page_chunks:
            n = len(chunks)
            page_embs = flat_embeddings[offset : offset + n]
            offset += n
            out.append(_representative_vector(page_embs))

        return l2_normalize(np.array(out))

    def embed_query(self, text: str) -> np.ndarray:
        """Single-vector query embedding using RETRIEVAL_QUERY task type."""
        vecs = self._embed_batch([text], task_type="RETRIEVAL_QUERY")
        return l2_normalize(np.asarray(vecs[0]))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _embed_batch(
        self,
        contents: list[str],
        task_type: str,
        progress_callback: ProgressCallback | None = None,
    ) -> np.ndarray:
        from google.genai import types
        from tenacity import (
            retry,
            retry_if_exception_type,
            stop_after_attempt,
            wait_exponential,
        )

        config = types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=self.dim,
        )

        @retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type(Exception),
            reraise=True,
        )
        def _call(batch: list[str]):
            return self._client.models.embed_content(
                model=self.model,
                contents=batch,
                config=config,
            )

        results: list[list[float]] = []
        total = len(contents)
        for start in range(0, total, self.batch_size):
            batch = contents[start : start + self.batch_size]
            response = _call(batch)
            for e in response.embeddings:
                results.append(list(e.values))

            done = min(start + len(batch), total)
            if progress_callback is not None:
                try:
                    progress_callback(done, total)
                except Exception:
                    pass

        return np.array(results, dtype=np.float32)

    @staticmethod
    def _wrap_chunk_progress(
        page_cb: ProgressCallback | None,
        per_page_chunks: list[list[str]],
        n_pages: int,
    ) -> ProgressCallback | None:
        if page_cb is None:
            return None

        cum_chunks: list[int] = []
        running = 0
        for chunks in per_page_chunks:
            running += len(chunks)
            cum_chunks.append(running)

        def _cb(done_chunks: int, _total: int) -> None:
            done_pages = 0
            for i, c in enumerate(cum_chunks):
                if done_chunks >= c:
                    done_pages = i + 1
                else:
                    break
            try:
                page_cb(done_pages, n_pages)
            except Exception:
                pass

        return _cb


def _representative_vector(chunk_embeddings: np.ndarray) -> np.ndarray:
    """Pick the chunk embedding most aligned with the doc centroid (cosine)."""
    if len(chunk_embeddings) == 1:
        return chunk_embeddings[0]
    centroid = chunk_embeddings.mean(axis=0)
    cn = float(np.linalg.norm(centroid))
    if cn == 0:
        return chunk_embeddings[0]
    centroid = centroid / cn
    norms = np.linalg.norm(chunk_embeddings, axis=1)
    norms = np.where(norms == 0, 1.0, norms)
    normed = chunk_embeddings / norms[:, None]
    sims = normed @ centroid
    return chunk_embeddings[int(np.argmax(sims))]
