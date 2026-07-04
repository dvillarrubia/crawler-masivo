"""
T17.1 — equivalencia numérica entre la power iteration en Python puro y el
camino scipy.sparse, sobre un grafo mediano pseudoaleatorio (criterio del
plan: tolerancia 1e-6). El snapshot v1 protege además que la extracción
del bucle no cambió la semántica histórica.
"""

from __future__ import annotations

import random

import pytest


def _random_graph(n=400, avg_out=6, seed=42):
    rng = random.Random(seed)
    edge_weight = {}
    out_total = {}
    weights = [1.0, 0.5, 0.3, 0.2]
    for src in range(n):
        if rng.random() < 0.05:
            continue  # dangling node
        for _ in range(rng.randint(1, avg_out * 2)):
            dst = rng.randrange(n)
            if dst == src:
                continue
            w = rng.choice(weights)
            key = (src, dst)
            if key not in edge_weight or w > edge_weight[key]:
                edge_weight[key] = w
    for (src, _), w in edge_weight.items():
        out_total[src] = out_total.get(src, 0.0) + w
    return edge_weight, out_total


def test_sparse_matches_python_loop():
    pytest.importorskip("scipy")
    from analysis.analyzer import run_power_iteration

    n = 400
    edge_weight, out_total = _random_graph(n)

    py = run_power_iteration(n, edge_weight, out_total, 0.85, 100, 1e-9,
                             force="python")
    sp = run_power_iteration(n, edge_weight, out_total, 0.85, 100, 1e-9,
                             force="sparse")

    assert max(abs(a - b) for a, b in zip(py, sp)) < 1e-6
    # ambas conservan la masa total (≈1 por construcción del PageRank)
    assert sum(py) == pytest.approx(1.0, abs=1e-6)
    assert sum(sp) == pytest.approx(1.0, abs=1e-6)


def test_auto_dispatch_by_size():
    from analysis import analyzer as mod
    from analysis.analyzer import run_power_iteration

    edge_weight, out_total = _random_graph(50)
    # n pequeño → camino Python aunque scipy exista (semántica default)
    result = run_power_iteration(50, edge_weight, out_total, 0.85, 100, 1e-6)
    assert len(result) == 50
    assert mod.SPARSE_PAGERANK_THRESHOLD == 50_000


def test_v1_snapshot_still_holds_after_extraction(db_session, make_job):
    """La extracción del bucle no puede mover el snapshot congelado."""
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Url
    from test_pagerank_v1_snapshot import EXPECTED_PAGERANK
    from toygraph import build_toy_graph

    job, _ = build_toy_graph(db_session, make_job)
    SEOAnalyzer(db_session, job.id).compute_pagerank()

    rows = db_session.query(Url.url, Url.pagerank).filter(
        Url.job_id == job.id, Url.is_internal.is_(True)
    ).all()
    for url, pr in rows:
        assert pr == pytest.approx(EXPECTED_PAGERANK[url], abs=1e-4), url
