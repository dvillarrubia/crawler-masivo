"""
Zona de trabajo "Acciones propuestas": listado unificado con filtros +
decisión por lotes. Sustituye las 5 colas separadas por una bandeja.
"""

from __future__ import annotations


def _url(db_session, job, path):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(job_id=job.id, url=full, url_hash=compute_url_hash(full),
            host="toy.local", path=path, scheme="https", is_internal=True,
            is_html=True, status_code=200, status_group="2xx", indexable=True)
    db_session.add(u)
    db_session.flush()
    return u


def _issue(db_session, job, u, itype, details=None, review="pending"):
    from shared.models import Issue

    i = Issue(job_id=job.id, url_id=u.id, issue_type=itype, severity="warning",
              review_status=review, details=details or {})
    db_session.add(i)
    db_session.flush()
    return i


def _suggestion(db_session, job, src, dst, score):
    from shared.models import LinkSuggestion
    from shared.url_normalization import compute_url_hash

    s = LinkSuggestion(job_id=job.id, target_url_hash=compute_url_hash(dst),
                       source_url_hash=compute_url_hash(src), target_url=dst,
                       source_url=src, cosine_similarity=0.8, score=score,
                       status="pending")
    db_session.add(s)
    db_session.flush()
    return s


def _proposals(db_session, job_id, **kw):
    """Llama al endpoint con TODOS los params (llamada directa, no HTTP:
    los Query() por defecto son objetos, hay que pasarlos)."""
    from api.routers.results import list_proposals

    params = dict(kind=None, state=None, search=None, to_contains=None,
                  from_contains=None, order="prioridad", page=1, page_size=50)
    params.update(kw)
    return list_proposals(job_id, db=db_session, **params)


def _scenario(db_session, make_job):
    job = make_job()
    a = _url(db_session, job, "/a")
    b = _url(db_session, job, "/b")
    # firmables de varias familias + un determinista (NO debe aparecer)
    _issue(db_session, job, a, "semantic_cannibalization",
           {"prioridad": 500, "dominant_url": "x"})
    _issue(db_session, job, b, "passage_gap", {"impressions": 900, "query": "kw"})
    _issue(db_session, job, a, "entity_cannibalization", {"prioridad": 100})
    _issue(db_session, job, b, "low_word_count", review=None)  # determinista
    _suggestion(db_session, job, "https://toy.local/a", "https://toy.local/b", 0.9)
    return job, a, b


def test_proposals_unified_and_filters(db_session, make_job):
    job, a, b = _scenario(db_session, make_job)
    r = _proposals(db_session, job.id)
    assert r["status"] == "ok"
    assert r["total"] == 4  # 3 issues firmables + 1 sugerencia; el determinista no
    tipos = {x["familia"] for x in r["items"]}
    assert tipos == {"enlace", "canibalizacion", "cobertura", "entidades"}
    assert r["items"][0]["prioridad"] >= r["items"][-1]["prioridad"]
    assert r["counts"]["cobertura"]["pendiente"] == 1

    solo = _proposals(db_session, job.id, kind="enlace")
    assert solo["total"] == 1 and solo["items"][0]["kind_row"] == "suggestion"

    porurl = _proposals(db_session, job.id, search="/b")
    assert all("/b" in (x["url"] or "") for x in porurl["items"])


def test_proposals_blocked_when_none(db_session, make_job):
    job = make_job()
    r = _proposals(db_session, job.id)
    assert r["status"] == "blocked" and r["reason"] == "sin_propuestas"


def test_bulk_decision_mixed(db_session, make_job):
    from api.routers.review import BulkDecision, BulkItem, bulk_decision
    from shared.models import Issue, LinkSuggestion

    job, a, b = _scenario(db_session, make_job)
    r = _proposals(db_session, job.id, state="pendiente")
    payload = BulkDecision(
        decision="aceptar", decided_by="tester",
        items=[BulkItem(kind_row=x["kind_row"], id=x["id"]) for x in r["items"]],
    )
    out = bulk_decision(job.id, payload, db=db_session)
    assert out["applied"] == 4

    assert all(i.review_status == "signed" and i.reviewed_by == "tester"
               for i in db_session.query(Issue).filter(
                   Issue.job_id == job.id, Issue.review_status.isnot(None)))
    assert all(s.status == "accepted" and s.decided_by == "tester"
               for s in db_session.query(LinkSuggestion).filter(
                   LinkSuggestion.job_id == job.id))

    assert _proposals(db_session, job.id, state="pendiente")["total"] == 0


def test_proposals_seo_from_to_filters(db_session, make_job):
    """Filtros pensados como un SEO: origen y destino por separado."""
    job = make_job()
    _url(db_session, job, "/servicios/seo")
    _suggestion(db_session, job, "https://toy.local/blog/post-1",
                "https://toy.local/servicios/seo", 0.9)
    _suggestion(db_session, job, "https://toy.local/blog/post-2",
                "https://toy.local/servicios/seo", 0.8)
    _suggestion(db_session, job, "https://toy.local/home",
                "https://toy.local/otra", 0.7)

    hacia = _proposals(db_session, job.id, kind="enlace", to_contains="/servicios")
    assert hacia["total"] == 2

    desde = _proposals(db_session, job.id, kind="enlace", from_contains="/blog")
    assert desde["total"] == 2
    assert all("/blog" in x["source_url"] for x in desde["items"])

    combo = _proposals(db_session, job.id, kind="enlace",
                       to_contains="/servicios", from_contains="/blog")
    assert combo["total"] == 2


def test_link_targets_grouped_by_destination(db_session, make_job):
    """La vista 'URLs a potenciar': sugerencias agrupadas por destino,
    ordenadas por número de enlaces (más refuerzo primero)."""
    from api.routers.results import link_targets

    job = make_job()
    pot = _url(db_session, job, "/potenciar")
    pot.pagerank = 1.2
    _url(db_session, job, "/otra")
    db_session.flush()
    for src in ("/blog/a", "/blog/b", "/home"):
        _suggestion(db_session, job, f"https://toy.local{src}",
                    "https://toy.local/potenciar", 0.9)
    _suggestion(db_session, job, "https://toy.local/x",
                "https://toy.local/otra", 0.5)

    r = link_targets(job.id, to_contains=None, from_contains=None,
                     only_pending=True, page=1, page_size=50, db=db_session)
    assert r["status"] == "ok" and r["total"] == 2
    top = r["items"][0]
    assert top["target_url"].endswith("/potenciar")
    assert top["n_sugerencias"] == 3
    assert top["pagerank_actual"] == 1.2
    assert len(top["suggestion_ids"]) == 3

    solo_blog = link_targets(job.id, to_contains=None, from_contains="/blog",
                             only_pending=True, page=1, page_size=50, db=db_session)
    pot_row = next(x for x in solo_blog["items"] if x["target_url"].endswith("/potenciar"))
    assert pot_row["n_sugerencias"] == 2


def test_bulk_decision_ignores_deterministic_and_missing(db_session, make_job):
    from api.routers.review import BulkDecision, BulkItem, bulk_decision
    from shared.models import Issue

    job, a, b = _scenario(db_session, make_job)
    det = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.review_status.is_(None)).one()
    payload = BulkDecision(
        decision="rechazar", decided_by="t",
        items=[BulkItem(kind_row="issue", id=det.id),
               BulkItem(kind_row="issue", id=999999)],
    )
    out = bulk_decision(job.id, payload, db=db_session)
    assert out["applied"] == 0
    db_session.refresh(det)
    assert det.review_status is None
