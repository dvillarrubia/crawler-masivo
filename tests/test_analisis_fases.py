"""El analisis apunta lo que tarda cada fase (#281, decision 226)."""

from __future__ import annotations

import os

import pytest


def test_una_fase_se_apunta_aunque_reviente():
    pytest.importorskip("sqlalchemy")
    from analysis.analyzer import SEOAnalyzer

    an = SEOAnalyzer.__new__(SEOAnalyzer)
    an._fases = []
    assert an._fase("suma", lambda a, b: a + b, 2, 3) == 5

    def revienta():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        an._fase("revienta", revienta)
    assert [n for n, _ in an._fases] == ["suma", "revienta"]
    assert all(isinstance(t, float) and t >= 0 for _, t in an._fases)


@pytest.mark.skipif(not os.environ.get("PAGERANK_TEST_DATABASE_URL"),
                    reason="sin PAGERANK_TEST_DATABASE_URL")
def test_el_analisis_entero_guarda_sus_fases(sesion, job):  # noqa: F811
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Job

    j, _ = job
    SEOAnalyzer(sesion, str(j.id)).run_all()
    sesion.expire_all()
    fases = sesion.get(Job, j.id).analisis_fases
    nombres = [n for n, _ in fases["fases"]]
    assert {"compute_link_counts", "compute_pagerank", "analyze_indexability"} <= set(nombres)
    assert nombres.index("analyze_indexability") < nombres.index("compute_pagerank")
    assert fases["total"] == pytest.approx(sum(t for _, t in fases["fases"]), abs=0.2)


from test_pagerank_db import job, sesion  # noqa: E402,F401
