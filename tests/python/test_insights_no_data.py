"""
Regresión de la auditoría anti-datos-inventados: "sin datos" no puntúa.

- Seguridad sin cabeceras capturadas devolvía score=0 (léase "inseguro")
  y hundía el overall un 15%.
- i18n sin hreflang devolvía score=100 ("perfecto") e inflaba el overall.

Ahora ambas devuelven score=None, quedan fuera de la media global y el
peso se renormaliza sobre las categorías con datos.
"""

from __future__ import annotations


def test_security_and_i18n_without_data_score_none(db_session, make_job):
    from api.routers.results import _calc_i18n, _calc_security

    job = make_job()
    sec = _calc_security(job.id, db_session)
    assert sec.score is None
    assert "Sin datos" in sec.recommendations[0].title

    i18n = _calc_i18n(job.id, db_session)
    assert i18n.score is None


def test_overall_renormalizes_over_scored_categories(db_session, make_job):
    """El overall de un job vacío no se hunde por el 0 fantasma de
    seguridad ni se infla por el 100 fantasma de i18n."""
    from api.routers.results import get_insights

    job = make_job()
    resp = get_insights(job.id, db=db_session)
    by_key = {c.key: c for c in resp.categories}
    assert by_key["security"].score is None
    assert by_key["i18n"].score is None
    # las categorías sin datos no participan: el overall sale SOLO de las
    # puntuadas (y con job vacío, ninguna aporta un 100 gratis)
    scored = [c for c in resp.categories if c.score is not None]
    if scored:
        lo = min(c.score for c in scored)
        hi = max(c.score for c in scored)
        assert lo <= resp.overall_score <= hi
