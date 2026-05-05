"""Plotly visualization data builders for the frontend."""
from __future__ import annotations

import math
from typing import Any


def build_scatter_umap(pages_data: list[dict], site_name: str = "") -> dict:
    """Build a Plotly JSON spec for UMAP scatter with HDBSCAN clusters.

    Each entry in pages_data should have:
      x, y, url, cluster_id, ring, weight, distance_to_centroid, semantic_role
    """
    # Group by cluster
    clusters: dict[int, list[dict]] = {}
    for p in pages_data:
        cid = p.get("cluster_id", -1)
        clusters.setdefault(cid, []).append(p)

    # Color palette for clusters
    palette = [
        "#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
        "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52",
    ]

    traces: list[dict] = []
    for cid in sorted(clusters.keys()):
        pages = clusters[cid]
        is_noise = cid == -1
        color = "#999999" if is_noise else palette[cid % len(palette)]
        label = "Sin cluster" if is_noise else f"Cluster {cid}"

        # Size by weight (scaled)
        weights = [p.get("weight", 0.5) for p in pages]
        max_w = max(weights) if weights else 1
        sizes = [max(6, (w / max(max_w, 0.001)) * 20) for w in weights]

        # Red border for outliers
        line_colors = [
            "red" if p.get("semantic_role") == "outlier" else color
            for p in pages
        ]
        line_widths = [
            2 if p.get("semantic_role") == "outlier" else 0.5
            for p in pages
        ]

        traces.append({
            "type": "scatter",
            "mode": "markers",
            "name": label,
            "x": [p["x"] for p in pages],
            "y": [p["y"] for p in pages],
            "text": [p.get("url", "") for p in pages],
            "customdata": [
                [
                    p.get("ring", ""),
                    round(p.get("distance_to_centroid", 0), 3),
                    round(p.get("weight", 0), 3),
                ]
                for p in pages
            ],
            "hovertemplate": (
                "<b>%{text}</b><br>"
                "Anillo: %{customdata[0]}<br>"
                "Distancia: %{customdata[1]}<br>"
                "Peso: %{customdata[2]}<br>"
                "<extra>%{fullData.name}</extra>"
            ),
            "marker": {
                "size": sizes,
                "color": color,
                "opacity": 0.3 if is_noise else 0.8,
                "line": {
                    "color": line_colors,
                    "width": line_widths,
                },
            },
        })

    layout: dict[str, Any] = {
        "title": {
            "text": f"Mapa Semantico UMAP — {site_name}" if site_name else "Mapa Semantico UMAP",
            "font": {"size": 16},
        },
        "xaxis": {"title": "UMAP 1", "showgrid": False},
        "yaxis": {"title": "UMAP 2", "showgrid": False},
        "paper_bgcolor": "#ffffff",
        "plot_bgcolor": "#fafafa",
        "legend": {"orientation": "h", "y": -0.15},
        "hovermode": "closest",
        "margin": {"t": 50, "b": 80, "l": 50, "r": 30},
    }

    return {"data": traces, "layout": layout}


def build_ring_map(pages_data: list[dict], site_metrics: dict | None = None) -> dict:
    """Build a Plotly JSON spec for concentric ring visualization.

    Matches the reference design: filled concentric zones with dashed borders,
    data points positioned by actual distance from centroid, colored by ring.
    """
    ring_colors = {
        "Core": "#34a853",       # green
        "Focus": "#4285f4",      # blue
        "Expansion": "#fbbc04",  # orange/amber
        "Peripheral": "#ea4335", # red
    }
    ring_bg_colors = {
        "Core": "rgba(52, 168, 83, 0.15)",
        "Focus": "rgba(66, 133, 244, 0.10)",
        "Expansion": "rgba(251, 188, 4, 0.08)",
        "Peripheral": "rgba(234, 67, 53, 0.06)",
    }
    # Ring outer radius in plot units
    ring_radii = {"Core": 1.0, "Focus": 2.0, "Expansion": 3.0, "Peripheral": 4.0}

    # Compute max distance for normalizing to ring system
    all_dists = [p.get("distance_to_centroid", 0) for p in pages_data]
    max_dist = max(all_dists) if all_dists else 1.0
    if max_dist == 0:
        max_dist = 1.0

    # Compute distance thresholds (IQR-based, same as classify_rings)
    import numpy as np
    dists_arr = np.array(all_dists)
    q1 = float(np.percentile(dists_arr, 25))
    q2 = float(np.percentile(dists_arr, 50))
    q3 = float(np.percentile(dists_arr, 75))
    # Ring boundary distances: Core=[0,q1], Focus=(q1,q2], Expansion=(q2,q3], Peripheral=(q3,max]

    traces: list[dict] = []

    # --- Filled background circles (outer to inner for layering) ---
    shapes: list[dict] = []
    for ring_name in ["Peripheral", "Expansion", "Focus", "Core"]:
        r = ring_radii[ring_name]
        shapes.append({
            "type": "circle",
            "xref": "x",
            "yref": "y",
            "x0": -r,
            "y0": -r,
            "x1": r,
            "y1": r,
            "fillcolor": ring_bg_colors[ring_name],
            "line": {
                "color": ring_colors[ring_name],
                "width": 1.5,
                "dash": "dash",
            },
            "layer": "below",
        })

    # --- Ring labels as annotations ---
    annotations: list[dict] = []
    for ring_name, r in ring_radii.items():
        annotations.append({
            "x": r * 0.71,  # 45 degrees
            "y": r * 0.71,
            "text": ring_name,
            "showarrow": False,
            "font": {
                "size": 12,
                "color": ring_colors[ring_name],
            },
            "opacity": 0.7,
        })

    # --- Map each page distance to radius in plot coords ---
    # Normalize: 0→center, max_dist→outermost ring
    outer_r = ring_radii["Peripheral"]

    # Group by ring for coloring
    rings: dict[str, list[dict]] = {}
    for p in pages_data:
        r = p.get("ring", "Peripheral")
        rings.setdefault(r, []).append(p)

    # Place data points: radius from actual distance, angle spread evenly per ring
    for ring_name in ["Core", "Focus", "Expansion", "Peripheral"]:
        pages = rings.get(ring_name, [])
        if not pages:
            continue

        n = len(pages)
        # Random-ish angular spread (use index-based offset)
        angles = [2 * math.pi * i / max(n, 1) + 0.5 for i in range(n)]

        xs, ys = [], []
        for i, p in enumerate(pages):
            d = p.get("distance_to_centroid", 0)
            # Scale distance to plot radius
            r = (d / max_dist) * outer_r
            # Add small angular jitter based on weight
            jitter = (p.get("weight", 0.5) - 0.5) * 0.3
            a = angles[i] + jitter
            xs.append(r * math.cos(a))
            ys.append(r * math.sin(a))

        weights = [p.get("weight", 0.5) for p in pages]
        max_w = max(weights) if weights else 1
        sizes = [max(5, (w / max(max_w, 0.001)) * 16) for w in weights]

        traces.append({
            "type": "scatter",
            "mode": "markers",
            "name": f"{ring_name} ({n})",
            "x": xs,
            "y": ys,
            "text": [p.get("url", "") for p in pages],
            "customdata": [
                [
                    ring_name,
                    round(p.get("distance_to_centroid", 0), 4),
                    round(p.get("weight", 0), 4),
                    p.get("cluster_id", -1),
                ]
                for p in pages
            ],
            "hovertemplate": (
                "<b>%{text}</b><br>"
                "Anillo: %{customdata[0]}<br>"
                "Distancia: %{customdata[1]}<br>"
                "Peso: %{customdata[2]}<br>"
                "Cluster: %{customdata[3]}<br>"
                "<extra></extra>"
            ),
            "marker": {
                "size": sizes,
                "color": ring_colors[ring_name],
                "opacity": 0.85,
                "line": {"color": "white", "width": 0.5},
            },
        })

    # --- Site Center star ---
    traces.append({
        "type": "scatter",
        "mode": "markers+text",
        "name": "Site Center",
        "x": [0],
        "y": [0],
        "text": ["Site Center"],
        "textposition": "bottom center",
        "textfont": {"size": 11, "color": "#333"},
        "marker": {
            "size": 16,
            "color": "#333",
            "symbol": "star",
        },
        "showlegend": False,
        "hoverinfo": "skip",
    })

    layout: dict[str, Any] = {
        "xaxis": {
            "showgrid": False,
            "zeroline": False,
            "showticklabels": False,
            "range": [-(outer_r + 0.5), outer_r + 0.5],
        },
        "yaxis": {
            "showgrid": False,
            "zeroline": False,
            "showticklabels": False,
            "scaleanchor": "x",
            "range": [-(outer_r + 0.5), outer_r + 0.5],
        },
        "paper_bgcolor": "#ffffff",
        "plot_bgcolor": "#ffffff",
        "shapes": shapes,
        "annotations": annotations,
        "legend": {"orientation": "h", "y": -0.05, "x": 0.5, "xanchor": "center"},
        "hovermode": "closest",
        "margin": {"t": 20, "b": 60, "l": 20, "r": 20},
    }

    return {"data": traces, "layout": layout}
