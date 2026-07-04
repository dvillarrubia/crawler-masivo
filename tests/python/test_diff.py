"""
Tests de T7 — diff entre crawls y flapping.

Criterios del plan: 1 alta + 1 baja + 1 cambio de status + 1 cambio de
title → el resumen cuadra exactamente; flapping detecta 200→404→200 en
3 jobs; clientes distintos → 422; fingerprints distintos → 409.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException


def _job(db_session, make_job, name, client="cliente-x", fingerprint=None):
    job = make_job(name=name)
    job.client_id = client
    job.normalization_fingerprint = fingerprint
    db_session.flush()
    return job


def _url(db_session, job, path, *, status=200, title=None, indexable=True,
         depth=1, pagerank=None, body_hash=None, canonical=None):
    from shared.models import HtmlMeta, Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        host="toy.local", path=path, scheme="https",
        is_internal=True, is_html=True, status_code=status,
        status_group=f"{status // 100}xx", indexable=indexable,
        crawl_depth=depth, pagerank=pagerank, body_hash=body_hash,
    )
    db_session.add(u)
    db_session.flush()
    if title is not None or canonical is not None:
        db_session.add(HtmlMeta(url_id=u.id, title=title,
                                canonical_href=canonical))
        db_session.flush()
    return u


def test_diff_summary_exact_counts(db_session, make_job):
    from api.routers.diff import diff_summary

    a = _job(db_session, make_job, "run-1")
    b = _job(db_session, make_job, "run-2")

    # sin cambios
    _url(db_session, a, "/estable", title="Igual")
    _url(db_session, b, "/estable", title="Igual")
    # baja (solo en A)
    _url(db_session, a, "/desaparecida")
    # alta (solo en B)
    _url(db_session, b, "/nueva")
    # cambio de status
    _url(db_session, a, "/rota", status=200)
    _url(db_session, b, "/rota", status=404)
    # cambio de title
    _url(db_session, a, "/retitulada", title="Antes")
    _url(db_session, b, "/retitulada", title="Después")

    summary = diff_summary(a.id, b.id, pagerank_delta=0.5, segment_id=None,
                           db=db_session)
    assert summary.new_urls == 1
    assert summary.gone_urls == 1
    assert summary.changes["status"] == 1
    assert summary.changes["title"] == 1
    assert summary.changes["indexable"] == 0
    assert summary.changes["content"] == 0


def test_diff_urls_detail(db_session, make_job):
    from api.routers.diff import diff_urls

    a = _job(db_session, make_job, "run-1")
    b = _job(db_session, make_job, "run-2")
    _url(db_session, a, "/rota", status=200)
    _url(db_session, b, "/rota", status=404)

    result = diff_urls(
        a.id, b.id, change="status", page=1, page_size=50,
        pagerank_delta=0.5, segment_id=None, db=db_session,
    )
    assert result["total"] == 1
    entry = result["items"][0]
    assert entry.url == "https://toy.local/rota"
    assert entry.old_value == "2xx"
    assert entry.new_value == "4xx"


def test_diff_pagerank_threshold(db_session, make_job):
    from api.routers.diff import diff_summary

    a = _job(db_session, make_job, "run-1")
    b = _job(db_session, make_job, "run-2")
    _url(db_session, a, "/p", pagerank=5.0)
    _url(db_session, b, "/p", pagerank=5.3)  # delta 0.3

    low = diff_summary(a.id, b.id, pagerank_delta=0.5, segment_id=None,
                       db=db_session)
    high = diff_summary(a.id, b.id, pagerank_delta=0.1, segment_id=None,
                        db=db_session)
    assert low.changes["pagerank"] == 0
    assert high.changes["pagerank"] == 1


def test_diff_requires_same_client(db_session, make_job):
    from api.routers.diff import diff_summary

    a = _job(db_session, make_job, "run-1", client="cliente-x")
    b = _job(db_session, make_job, "run-2", client="cliente-y")

    with pytest.raises(HTTPException) as exc:
        diff_summary(a.id, b.id, pagerank_delta=0.5, segment_id=None,
                     db=db_session)
    assert exc.value.status_code == 422


def test_diff_requires_same_fingerprint(db_session, make_job):
    from api.routers.diff import diff_summary

    a = _job(db_session, make_job, "run-1", fingerprint=None)
    b = _job(db_session, make_job, "run-2", fingerprint="a" * 64)

    with pytest.raises(HTTPException) as exc:
        diff_summary(a.id, b.id, pagerank_delta=0.5, segment_id=None,
                     db=db_session)
    assert exc.value.status_code == 409


def test_diff_excludes_not_crawled_rows(db_session, make_job):
    """Las filas sintéticas de T2 no cuentan como altas/bajas."""
    from api.routers.diff import diff_summary
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    a = _job(db_session, make_job, "run-1")
    b = _job(db_session, make_job, "run-2")
    _url(db_session, a, "/pagina")
    _url(db_session, b, "/pagina")
    db_session.add(Url(
        job_id=b.id, url="https://toy.local/huerfana",
        url_hash=compute_url_hash("https://toy.local/huerfana"),
        is_internal=True, is_html=False, status_group="not_crawled",
    ))
    db_session.flush()

    summary = diff_summary(a.id, b.id, pagerank_delta=0.5, segment_id=None,
                           db=db_session)
    assert summary.new_urls == 0


def test_diff_with_segment_filter(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from api.routers.diff import diff_summary
    from shared.models import Segment

    a = _job(db_session, make_job, "run-1")
    b = _job(db_session, make_job, "run-2")
    seg = Segment(client_id="cliente-x", name="Blog", rule_type="regex",
                  rule=r"^/blog/", priority=10)
    db_session.add(seg)
    db_session.flush()

    _url(db_session, a, "/blog/post", status=200)
    _url(db_session, b, "/blog/post", status=404)   # cambio DENTRO del segmento
    _url(db_session, a, "/fuera", status=200)
    _url(db_session, b, "/fuera", status=500)       # cambio FUERA

    SEOAnalyzer(db_session, a.id).assign_segments()
    SEOAnalyzer(db_session, b.id).assign_segments()
    db_session.flush()

    full = diff_summary(a.id, b.id, pagerank_delta=0.5, segment_id=None,
                        db=db_session)
    seg_only = diff_summary(a.id, b.id, pagerank_delta=0.5,
                            segment_id=seg.id, db=db_session)
    assert full.changes["status"] == 2
    assert seg_only.changes["status"] == 1


def test_flapping_detects_200_404_200(db_session, make_job):
    from api.routers.diff import diff_flapping

    j1 = _job(db_session, make_job, "run-1")
    j2 = _job(db_session, make_job, "run-2")
    j3 = _job(db_session, make_job, "run-3")

    for job, status in ((j1, 200), (j2, 404), (j3, 200)):
        _url(db_session, job, "/inestable", status=status)
        _url(db_session, job, "/estable", status=200)

    entries = diff_flapping("cliente-x", last_n=4, db=db_session)
    assert len(entries) == 1
    e = entries[0]
    assert e.url == "https://toy.local/inestable"
    assert e.field == "status_group"
    assert [s["value"] for s in e.sequence] == ["2xx", "4xx", "2xx"]


def test_flapping_needs_three_jobs(db_session, make_job):
    from api.routers.diff import diff_flapping

    j1 = _job(db_session, make_job, "run-1")
    j2 = _job(db_session, make_job, "run-2")
    for job, status in ((j1, 200), (j2, 404)):
        _url(db_session, job, "/inestable", status=status)

    assert diff_flapping("cliente-x", last_n=4, db=db_session) == []


def test_monotonic_change_is_not_flapping(db_session, make_job):
    """200→404→404 es un cambio real, no flapping."""
    from api.routers.diff import diff_flapping

    for name, status in (("r1", 200), ("r2", 404), ("r3", 404)):
        job = _job(db_session, make_job, name)
        _url(db_session, job, "/rota-de-verdad", status=status)

    assert diff_flapping("cliente-x", last_n=4, db=db_session) == []
