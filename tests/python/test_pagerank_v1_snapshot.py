"""
Snapshot del PageRank v1 sobre un grafo de juguete (Fase 0.3 — prerrequisito de T3).

Congela los valores EXACTOS (redondeados a 4 decimales, como persiste el
analyzer) que ``compute_pagerank`` produce hoy. T3 introducirá un PageRank
v2 conmutado por ``job.config``; el v1 debe seguir produciendo estos
mismos números para que los jobs históricos sigan siendo comparables.

El grafo ejercita todas las ramas del algoritmo v1:
* pesos por posición: content=1.0, header=0.3, footer=0.2
* BUG LATENTE CONGELADO (C3 del documento maestro): ``nav`` y ``sidebar``
  no están en ``_POSITION_WEIGHT`` → caen al default 0.5, con lo que un
  enlace de menú pesa más que header/footer. Se corrige SOLO en v2.
* deduplicación de aristas src→dst quedándose con el peso máximo
* nofollow (follow=False) excluido del grafo
* enlaces externos (is_internal=False) excluidos
* self-links excluidos
* nodos dangling (sin outlinks) redistribuyen su masa
* normalización final a escala 0–10 (el máximo siempre vale 10.0)
"""

from __future__ import annotations

import pytest

from toygraph import build_toy_graph

# Valores congelados el 2026-07-04 con damping=0.85, max_iter=100, tol=1e-6.
# Regenerar SOLO si se cambia adrede el v1 (no debería ocurrir nunca según
# la regla "no cambiar PageRank v1" del plan): ver regenerate al final.
EXPECTED_PAGERANK = {
    "https://toy.local/": 10.0,
    "https://toy.local/b": 8.1651,
    "https://toy.local/c": 8.8253,
    "https://toy.local/d": 5.9452,
    "https://toy.local/e-nofollow-target": 2.4984,
}


def test_pagerank_v1_snapshot(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Url

    job, urls = build_toy_graph(db_session, make_job)

    analyzer = SEOAnalyzer(db_session, job.id)
    analyzer.compute_pagerank()

    rows = db_session.query(Url.url, Url.pagerank).filter(
        Url.job_id == job.id, Url.is_internal.is_(True)
    ).all()
    actual = {url: pr for url, pr in rows}

    assert set(actual) == set(EXPECTED_PAGERANK)
    for url, expected in EXPECTED_PAGERANK.items():
        assert actual[url] == pytest.approx(expected, abs=1e-4), url


def test_pagerank_excludes_external_urls(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Url

    job, _ = build_toy_graph(db_session, make_job)
    SEOAnalyzer(db_session, job.id).compute_pagerank()

    external = db_session.query(Url).filter(
        Url.job_id == job.id, Url.is_internal.is_(False)
    ).one()
    assert external.pagerank is None


def test_pagerank_ranking_order(db_session, make_job):
    """El orden relativo también forma parte del contrato v1."""
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Url

    job, _ = build_toy_graph(db_session, make_job)
    SEOAnalyzer(db_session, job.id).compute_pagerank()

    rows = db_session.query(Url.url, Url.pagerank).filter(
        Url.job_id == job.id, Url.is_internal.is_(True)
    ).all()
    ranking = [u for u, _ in sorted(rows, key=lambda r: r[1], reverse=True)]
    assert ranking == [
        "https://toy.local/",
        "https://toy.local/c",
        "https://toy.local/b",
        "https://toy.local/d",
        "https://toy.local/e-nofollow-target",
    ]
