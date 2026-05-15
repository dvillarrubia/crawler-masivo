"""Configuration constants for semantic analysis."""
from __future__ import annotations

# --- Content filtering ---
MIN_TOKENS = 150

# --- Cannibalization ---
CANNIBAL_THRESHOLD = 0.92

# --- Authority weighting ---
DEFAULT_ALPHA = 0.6   # PageRank weight (when GSC data available)
DEFAULT_BETA = 0.4    # Clicks weight (when GSC data available)
# Without GSC: alpha=1.0, beta=0.0

# Embedding model / dimension are owned by the backend module
# (POC_centro_semantico/src/embedding_backends/gemini.py).

# --- UMAP ---
def get_umap_config(n_urls: int) -> dict:
    """Return adaptive UMAP parameters based on dataset size."""
    if n_urls < 200:
        return {
            "n_neighbors": max(5, n_urls // 5),
            "min_dist": 0.1,
            "n_components": 2,
            "metric": "cosine",
        }
    elif n_urls <= 5000:
        return {
            "n_neighbors": 30,
            "min_dist": 0.05,
            "n_components": 2,
            "metric": "cosine",
        }
    else:
        return {
            "n_neighbors": 50,
            "min_dist": 0.02,
            "n_components": 2,
            "metric": "cosine",
        }
