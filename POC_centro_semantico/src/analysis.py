"""Semantic analysis utilities: cannibalization, drift, gap, rings."""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

if TYPE_CHECKING:
    from POC_centro_semantico.src.embedding_backends.base import EmbeddingBackend


def minmax_normalize(series: np.ndarray) -> np.ndarray:
    """Min-max normalize to [0, 1]."""
    mn, mx = series.min(), series.max()
    if mx - mn == 0:
        return np.zeros_like(series)
    return (series - mn) / (mx - mn)


def detect_cannibalization(
    vectors: np.ndarray,
    weights: np.ndarray,
    url_ids: list[int],
    threshold: float = 0.92,
) -> list[dict]:
    """Find pairs of pages with cosine similarity >= threshold.

    For each pair, the dominant page is the one with higher weight.
    Returns list of {url_dominant_id, url_weak_id, cosine_similarity}.
    """
    sim_matrix = cosine_similarity(vectors)
    n = len(url_ids)
    pairs: list[dict] = []
    seen: set[tuple[int, int]] = set()

    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i, j] >= threshold:
                pair_key = (min(url_ids[i], url_ids[j]), max(url_ids[i], url_ids[j]))
                if pair_key in seen:
                    continue
                seen.add(pair_key)

                if weights[i] >= weights[j]:
                    dom_idx, weak_idx = i, j
                else:
                    dom_idx, weak_idx = j, i

                pairs.append({
                    "url_dominant_id": url_ids[dom_idx],
                    "url_weak_id": url_ids[weak_idx],
                    "cosine_similarity": round(float(sim_matrix[i, j]), 4),
                })

    return sorted(pairs, key=lambda p: p["cosine_similarity"], reverse=True)


def drift_analysis(
    distances: np.ndarray,
    weights: np.ndarray,
    url_ids: list[int],
    top_n: int = 10,
) -> list[dict]:
    """Find URLs whose weight-distance ratio suggests they drift the centroid.

    High weight + high distance = high drift impact.
    Returns top_n entries sorted by drift_score descending.
    """
    drift_scores = weights * distances
    indices = np.argsort(drift_scores)[::-1][:top_n]
    return [
        {
            "url_id": url_ids[idx],
            "distance": round(float(distances[idx]), 4),
            "weight": round(float(weights[idx]), 4),
            "drift_score": round(float(drift_scores[idx]), 4),
        }
        for idx in indices
    ]


def gap_analysis(
    centroid: np.ndarray,
    target_text: str,
    vectors: np.ndarray,
    url_ids: list[int],
    urls: list[str],
    backend: "EmbeddingBackend",
    top_n: int = 10,
) -> dict:
    """Find URLs most similar to a target topic.

    Embeds `target_text` through the same backend used at analysis time and
    ranks pages by cosine similarity. The backend is responsible for the
    asymmetric *query* embedding (e.g. Gemini's RETRIEVAL_QUERY task type),
    so the engine code does not need to know provider-specific details.
    """
    target_emb = backend.embed_query(target_text)

    sims_target = cosine_similarity([target_emb], vectors)[0]
    sims_centroid = cosine_similarity([centroid], vectors)[0]

    indices = np.argsort(sims_target)[::-1][:top_n]

    return {
        "candidates": [
            {
                "url_id": url_ids[idx],
                "similarity_to_topic": round(float(sims_target[idx]), 4),
                "similarity_to_centroid": round(float(sims_centroid[idx]), 4),
            }
            for idx in indices
        ],
    }


def classify_rings(
    distances: np.ndarray,
    outlier_threshold: float | None = None,
) -> list[str]:
    """Classify pages into rings using quartiles + IQR outlier rule.

    Follows sem_seo_engine_v2 spec (§7B):
    - Core:       distance <= Q1                       (25% closest)
    - Focus:      Q1 < distance <= Q3                  (50% central)
    - Expansion:  Q3 < distance <= Q3 + 1.5*IQR        (legit expansion)
    - Peripheral: distance > Q3 + 1.5*IQR              (true IQR outliers)

    Peripheral is intentionally *not* a fixed quartile: it must reflect
    pages that genuinely distort the semantic focus, so in a healthy
    site it should be a small subset, not 25% by construction.

    Args:
        distances: array of distances to centroid.
        outlier_threshold: optional precomputed threshold (Q3 + 1.5*IQR).
            If None, it is computed here. Pass it in to keep "Peripheral"
            consistent with whatever the engine called an outlier.
    """
    q1 = np.percentile(distances, 25)
    q3 = np.percentile(distances, 75)
    if outlier_threshold is None:
        iqr = q3 - q1
        outlier_threshold = q3 + 1.5 * iqr

    rings: list[str] = []
    for d in distances:
        if d <= q1:
            rings.append("Core")
        elif d <= q3:
            rings.append("Focus")
        elif d <= outlier_threshold:
            rings.append("Expansion")
        else:
            rings.append("Peripheral")
    return rings
