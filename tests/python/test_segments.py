"""
Tests de T12 — motor de segmentación.

Criterios del plan: prioridad respetada con ^/blog/ y ^/producto/;
get_stats?segment_id= cuadra con el recuento manual; sin parámetro el
comportamiento no varía; re-crawl reasigna sin duplicar filas.
"""

from __future__ import annotations

import pytest


def _segment(db_session, client_id, name, rule, *, rule_type="regex",
             priority=100):
    from shared.models import Segment

    seg = Segment(
        client_id=client_id, name=name, rule=rule,
        rule_type=rule_type, priority=priority,
    )
    db_session.add(seg)
    db_session.flush()
    return seg


def _url(db_session, job, path, *, is_html=True, status=200):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        host="toy.local", path=path, scheme="https",
        is_internal=True, is_html=is_html, status_code=status,
        status_group=f"{status // 100}xx",
    )
    db_session.add(u)
    db_session.flush()
    return u


@pytest.fixture()
def client_job(db_session, make_job):
    job = make_job(name="con-cliente")
    job.client_id = "cliente-x"
    db_session.flush()
    return job


def test_assign_segments_priority_first_match_wins(db_session, client_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import UrlSegment

    blog = _segment(db_session, "cliente-x", "Blog", r"^/blog/", priority=10)
    producto = _segment(db_session, "cliente-x", "Producto", r"^/producto/", priority=20)
    # regla amplia con MENOR prioridad (número mayor): no debe robar URLs
    catchall = _segment(db_session, "cliente-x", "Todo", r"^/", priority=999)

    b1 = _url(db_session, client_job, "/blog/post-1")
    b2 = _url(db_session, client_job, "/blog/post-2")
    p1 = _url(db_session, client_job, "/producto/42")
    otra = _url(db_session, client_job, "/quienes-somos")
    img = _url(db_session, client_job, "/logo.png", is_html=False)

    SEOAnalyzer(db_session, client_job.id).assign_segments()
    db_session.flush()

    rows = db_session.query(UrlSegment).filter(
        UrlSegment.job_id == client_job.id
    ).all()
    by_url = {r.url_id: r.segment_id for r in rows}

    assert by_url[b1.id] == blog.id
    assert by_url[b2.id] == blog.id
    assert by_url[p1.id] == producto.id
    assert by_url[otra.id] == catchall.id  # capturada por la amplia
    assert img.id not in by_url            # solo HTML


def test_prefix_rule_type(db_session, client_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import UrlSegment

    seg = _segment(db_session, "cliente-x", "Legal", "/legal/",
                   rule_type="prefix")
    legal = _url(db_session, client_job, "/legal/aviso")
    _url(db_session, client_job, "/otra")

    SEOAnalyzer(db_session, client_job.id).assign_segments()
    db_session.flush()

    rows = db_session.query(UrlSegment).filter(
        UrlSegment.job_id == client_job.id
    ).all()
    assert [(r.url_id, r.segment_id) for r in rows] == [(legal.id, seg.id)]


def test_reassignment_does_not_duplicate(db_session, client_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import UrlSegment

    _segment(db_session, "cliente-x", "Blog", r"^/blog/")
    _url(db_session, client_job, "/blog/post")

    analyzer = SEOAnalyzer(db_session, client_job.id)
    analyzer.assign_segments()
    analyzer.assign_segments()  # re-análisis
    db_session.flush()

    assert db_session.query(UrlSegment).filter(
        UrlSegment.job_id == client_job.id
    ).count() == 1


def test_no_client_or_rules_is_noop(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import UrlSegment

    job = make_job()  # sin client_id
    _url(db_session, job, "/blog/post")
    SEOAnalyzer(db_session, job.id).assign_segments()
    db_session.flush()
    assert db_session.query(UrlSegment).count() == 0


def test_invalid_regex_rule_is_skipped(db_session, client_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import UrlSegment

    _segment(db_session, "cliente-x", "Rota", r"[invalid(")
    ok = _segment(db_session, "cliente-x", "Blog", r"^/blog/", priority=200)
    u = _url(db_session, client_job, "/blog/post")

    SEOAnalyzer(db_session, client_job.id).assign_segments()
    db_session.flush()

    rows = db_session.query(UrlSegment).all()
    assert [(r.url_id, r.segment_id) for r in rows] == [(u.id, ok.id)]


# ---------------------------------------------------------------------------
# Filtros en endpoints
# ---------------------------------------------------------------------------

def test_get_stats_with_segment_filter(db_session, client_job):
    from analysis.analyzer import SEOAnalyzer
    from api.routers.results import get_stats

    blog = _segment(db_session, "cliente-x", "Blog", r"^/blog/")
    _url(db_session, client_job, "/blog/a")
    _url(db_session, client_job, "/blog/b", status=404)
    _url(db_session, client_job, "/fuera")

    SEOAnalyzer(db_session, client_job.id).assign_segments()
    db_session.flush()

    # sin filtro: comportamiento actual
    full = get_stats(client_job.id, segment_id=None, db=db_session)
    assert full.total_urls == 3

    # con filtro: solo el segmento
    seg_stats = get_stats(client_job.id, segment_id=blog.id, db=db_session)
    assert seg_stats.total_urls == 2
    groups = {g.status_group: g.count for g in seg_stats.urls_by_status_group}
    assert groups == {"2xx": 1, "4xx": 1}


def test_list_urls_with_segment_filter(db_session, client_job):
    from unittest.mock import MagicMock

    from analysis.analyzer import SEOAnalyzer
    from api.routers.results import list_urls

    blog = _segment(db_session, "cliente-x", "Blog", r"^/blog/")
    _url(db_session, client_job, "/blog/a")
    _url(db_session, client_job, "/fuera")
    SEOAnalyzer(db_session, client_job.id).assign_segments()
    db_session.flush()

    request = MagicMock()
    request.query_params = {}

    result = list_urls(
        client_job.id, request, page=1, page_size=50,
        status_group=None, is_internal=None, resource_type=None,
        search=None, sort_by=None, sort_dir="asc", indexable=None,
        segment_id=blog.id, status_code=None, issue_type=None,
        severity=None, db=db_session,
    )
    assert result["total"] == 1
    assert result["items"][0].url == "https://toy.local/blog/a"


# ---------------------------------------------------------------------------
# CRUD + preview
# ---------------------------------------------------------------------------

def test_segment_crud_and_preview(db_session, client_job):
    from api.routers.segments import (
        SegmentPreviewRequest,
        SegmentRule,
        create_segment,
        delete_segment,
        list_segments,
        preview_segments,
        update_segment,
    )

    _url(db_session, client_job, "/blog/a")
    _url(db_session, client_job, "/blog/b")
    _url(db_session, client_job, "/producto/1")
    db_session.commit()

    seg = create_segment(
        "cliente-x",
        SegmentRule(name="Blog", rule_type="regex", rule=r"^/blog/", priority=10),
        db=db_session,
    )
    assert seg.id is not None
    assert [s.name for s in list_segments("cliente-x", db=db_session)] == ["Blog"]

    updated = update_segment(
        "cliente-x", seg.id,
        SegmentRule(name="Blog ES", rule_type="regex", rule=r"^/blog/", priority=5),
        db=db_session,
    )
    assert updated.name == "Blog ES"

    preview = preview_segments(
        "cliente-x",
        SegmentPreviewRequest(rules=[
            SegmentRule(name="Blog", rule_type="regex", rule=r"^/blog/", priority=1),
            SegmentRule(name="Producto", rule_type="prefix", rule="/producto/", priority=2),
        ]),
        db=db_session,
    )
    assert preview.total_urls == 3
    assert preview.unmatched_urls == 0
    counts = {e.name: e.matched_urls for e in preview.entries}
    assert counts == {"Blog": 2, "Producto": 1}
    assert len(preview.entries[0].sample) == 2

    delete_segment("cliente-x", seg.id, db=db_session)
    assert list_segments("cliente-x", db=db_session) == []


def test_preview_rejects_bad_regex(db_session, client_job):
    from fastapi import HTTPException

    from api.routers.segments import (
        SegmentPreviewRequest, SegmentRule, preview_segments,
    )

    _url(db_session, client_job, "/a")
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        preview_segments(
            "cliente-x",
            SegmentPreviewRequest(rules=[
                SegmentRule(name="Rota", rule_type="regex", rule="[oops("),
            ]),
            db=db_session,
        )
    assert exc.value.status_code == 422
