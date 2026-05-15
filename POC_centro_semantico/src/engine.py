"""SemanticEngine — orchestrates the full semantic analysis pipeline."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np
from sklearn.decomposition import PCA
from sqlalchemy import select
from sqlalchemy.orm import Session

from POC_centro_semantico.src.analysis import (
    classify_rings,
    detect_cannibalization,
    drift_analysis,
    minmax_normalize,
)
from POC_centro_semantico.src.config import (
    DEFAULT_ALPHA,
    DEFAULT_BETA,
    MIN_TOKENS,
    get_umap_config,
)
from POC_centro_semantico.src.embedding_backends import (
    EmbeddingBackend,
    get_backend,
)
from shared.models import PageContent, Url

logger = logging.getLogger(__name__)


class SemanticEngine:
    """Run the full semantic analysis pipeline for a crawl job."""

    def process(
        self,
        db: Session,
        job_id: str | uuid.UUID,
        alpha: float = DEFAULT_ALPHA,
        beta: float = DEFAULT_BETA,
        cannibal_threshold: float = 0.92,
        gsc_data: dict[str, dict] | None = None,
        progress_callback: Callable[[str, int], None] | None = None,
        backend: EmbeddingBackend | None = None,
    ) -> dict:
        """Execute semantic analysis and return results dict.

        Args:
            db: SQLAlchemy session.
            job_id: Crawl job UUID.
            alpha: Weight for PageRank (0-1).
            beta: Weight for GSC clicks (0-1).
            cannibal_threshold: Cosine similarity threshold for cannibalization.
            gsc_data: Optional dict mapping URL → {clicks, impressions, ctr, position}.
            progress_callback: Optional fn(stage_name, pct) for progress updates.
            backend: EmbeddingBackend (default: Gemini via get_backend()).

        Returns:
            Dict with analysis_id, pages, site_metrics, cannibalization, etc.
        """
        if backend is None:
            backend = get_backend()
        if not gsc_data:
            alpha, beta = 1.0, 0.0

        def _progress(stage: str, pct: int) -> None:
            if progress_callback:
                progress_callback(stage, pct)

        _progress("loading_data", 5)

        # 1. Load data from DB
        rows = (
            db.execute(
                select(Url.id, Url.url, Url.pagerank, PageContent.content_text)
                .outerjoin(PageContent, PageContent.url_id == Url.id)
                .where(
                    Url.job_id == job_id,
                    Url.status_code == 200,
                    Url.is_html.is_(True),
                    Url.indexable.is_(True),
                )
            )
            .all()
        )

        _progress("filtering", 10)

        # 2. Filter: content must have >= MIN_TOKENS words
        filtered = []
        for row in rows:
            text = row.content_text or ""
            if len(text.split()) >= MIN_TOKENS:
                filtered.append({
                    "url_id": row.id,
                    "url": row.url,
                    "pagerank": row.pagerank or 0.0,
                    "content_text": text,
                })

        if not filtered:
            return {
                "error": "No hay paginas con contenido suficiente para analizar.",
                "total_pages": 0,
            }

        n_pages = len(filtered)
        url_ids = [p["url_id"] for p in filtered]
        urls = [p["url"] for p in filtered]
        texts = [p["content_text"] for p in filtered]
        pageranks = np.array([p["pagerank"] for p in filtered])

        _progress("embedding", 20)

        # 3-4. Chunk + embed → representative chunk per page (Gemini backend).
        # Backends return L2-normalized vectors so the centroid lives on the
        # unit sphere as the pages → distances are pure angular distances
        # and the resulting metrics are comparable across sites.
        # Sub-progress (20% → 50%) streams from inside the backend so the
        # watchdog never thinks the thread is dead during this long stage.
        def _embed_progress(done: int, total: int) -> None:
            pct = 20 + int(30 * done / max(total, 1))
            _progress(f"embedding {done}/{total}", min(pct, 49))

        vectors = backend.embed_documents(texts, progress_callback=_embed_progress)

        _progress("weighting", 50)

        # 5. Weights: w = alpha * norm(log(PR)) + beta * norm(log(clicks))
        # log1p compresses extreme outliers (e.g. homepage PR=10 vs blog PR=0.02)
        # so the rest of the signal isn't crushed to zero by min-max.
        pr_norm = minmax_normalize(np.log1p(pageranks))

        if gsc_data and beta > 0:
            clicks = np.array([
                gsc_data.get(u, {}).get("clicks", 0) for u in urls
            ], dtype=float)
            clicks_norm = minmax_normalize(np.log1p(clicks))
        else:
            clicks_norm = np.zeros(n_pages)

        weights = alpha * pr_norm + beta * clicks_norm
        # Tiny epsilon to avoid all-zero weights (np.average breaks); does not
        # flatten the signal the way the previous 0.01 floor did.
        weights = np.maximum(weights, 1e-6)

        _progress("centroid", 55)

        # 6. Weighted centroid, re-normalized onto the unit sphere so distances
        # are comparable across sites (a diffuse corpus would otherwise produce
        # an interior centroid and inflate every distance).
        centroid_raw = np.average(vectors, axis=0, weights=weights)
        centroid_norm = float(np.linalg.norm(centroid_raw))
        centroid = centroid_raw / centroid_norm if centroid_norm > 0 else centroid_raw

        # 7. Distances + IQR outliers
        distances = np.linalg.norm(vectors - centroid, axis=1)
        q1 = np.percentile(distances, 25)
        q3 = np.percentile(distances, 75)
        iqr = q3 - q1
        outlier_threshold = float(q3 + 1.5 * iqr)

        _progress("dimensionality_reduction", 60)

        # 8. PCA(50) → UMAP
        pca_dim = min(50, n_pages - 1, vectors.shape[1])
        if pca_dim >= 2:
            pca = PCA(n_components=pca_dim)
            vectors_reduced = pca.fit_transform(vectors)
        else:
            vectors_reduced = vectors

        import umap
        umap_cfg = get_umap_config(n_pages)
        umap_cfg["n_neighbors"] = min(umap_cfg["n_neighbors"], n_pages - 1)
        reducer = umap.UMAP(**umap_cfg, random_state=42)
        coords_2d = reducer.fit_transform(vectors_reduced)

        _progress("clustering", 75)

        # 9. HDBSCAN clustering on the PCA-reduced high-dim space (not on the
        # 2D UMAP coords). UMAP can warp local/global structure, so clusters
        # built on it are less reliable; PCA preserves variance faithfully.
        # min_cluster = n_pages // 50 (was n_pages // 20): on mono-thematic
        # sites the higher floor returned 0 clusters because no group was
        # dense *enough* for the threshold. //50 reveals the real sub-themes.
        import hdbscan
        min_cluster = max(5, n_pages // 50)
        cluster_space = vectors_reduced if pca_dim >= 2 else vectors
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster,
            min_samples=max(2, min_cluster // 2),
            metric="euclidean",
        )
        cluster_labels = clusterer.fit_predict(cluster_space)

        _progress("classification", 85)

        # 10. Classify rings (Peripheral = IQR outliers, not just top quartile).
        rings = classify_rings(distances, outlier_threshold=outlier_threshold)

        # Semantic role
        roles = []
        for i in range(n_pages):
            if distances[i] > outlier_threshold:
                roles.append("outlier")
            elif rings[i] == "Core":
                roles.append("core")
            else:
                roles.append("peripheral")

        _progress("cannibalization", 90)

        # 11. Detect cannibalization
        cannibal_pairs = detect_cannibalization(
            vectors, weights, url_ids, threshold=cannibal_threshold
        )

        # 12. Build results
        pages_data: list[dict[str, Any]] = []
        for i in range(n_pages):
            pages_data.append({
                "url_id": url_ids[i],
                "url": urls[i],
                "embedding": vectors[i].tolist(),
                "cluster_id": int(cluster_labels[i]),
                "ring": rings[i],
                "semantic_role": roles[i],
                "distance_to_centroid": round(float(distances[i]), 6),
                "weight": round(float(weights[i]), 6),
                "pr_norm": round(float(pr_norm[i]), 6),
                "clicks_norm": round(float(clicks_norm[i]), 6),
                "x": round(float(coords_2d[i, 0]), 6),
                "y": round(float(coords_2d[i, 1]), 6),
            })

        # Site metrics.
        # focus_score and semantic_radius use the 95th-percentile distance as
        # reference instead of max(): a single weird page should not be able
        # to swing the whole-site headline metric.
        ring_counts = {r: rings.count(r) for r in ["Core", "Focus", "Expansion", "Peripheral"]}
        distance_p95 = float(np.percentile(distances, 95))
        distance_ref = distance_p95 if distance_p95 > 0 else 1.0
        focus_score = round(1.0 - (float(np.mean(distances)) / distance_ref), 4)
        semantic_radius = round(distance_p95, 4)

        drift_data = drift_analysis(distances, weights, url_ids, top_n=10)
        # Site-level drift = average drift of the top-5 worst offenders, so a
        # single page cannot define the whole-site metric.
        top_drift = drift_data[:5]
        drift_score = round(
            float(np.mean([d["drift_score"] for d in top_drift])) if top_drift else 0.0,
            4,
        )

        site_metrics = {
            "focus_score": focus_score,
            "semantic_radius": semantic_radius,
            "drift_score": drift_score,
            "ring_counts": ring_counts,
            "total_pages": n_pages,
            "n_clusters": len(set(cluster_labels) - {-1}),
            "n_outliers": roles.count("outlier"),
            "n_cannibal_pairs": len(cannibal_pairs),
            "outlier_threshold": round(outlier_threshold, 4),
            "distance_q1": round(float(q1), 4),
            "distance_q3": round(float(q3), 4),
        }

        _progress("done", 100)

        return {
            "pages": pages_data,
            "centroid": centroid.tolist(),
            "site_metrics": site_metrics,
            "cannibalization": cannibal_pairs,
            "drift": drift_data,
            "config": {
                "alpha": alpha,
                "beta": beta,
                "cannibal_threshold": cannibal_threshold,
                "embedding_provider": backend.name,
                "embedding_model": backend.model if hasattr(backend, "model") else None,
                "embedding_dim": backend.dim,
            },
            "total_pages": n_pages,
        }
