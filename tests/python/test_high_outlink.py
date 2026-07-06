"""
high_outlink_count cuenta solo los enlaces DEL CONTENIDO, no la plantilla.

Falso positivo reportado: una página de blog salía con "101 enlaces
salientes" porque se contaba el mega-menú + pie (sitewide) además del
contenido. Ahora el check ignora nav/header/footer/sidebar.
"""

from __future__ import annotations


def _url(db_session, job, path):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(job_id=job.id, url=full, url_hash=compute_url_hash(full),
            is_internal=True, is_html=True, status_code=200, status_group="2xx")
    db_session.add(u)
    db_session.flush()
    return u


def _links(db_session, job, src, n, position):
    from shared.models import Link
    from shared.url_normalization import compute_url_hash

    for i in range(n):
        to = f"https://toy.local/dest/{position}/{i}"
        db_session.add(Link(
            job_id=job.id, from_url_id=src.id, to_url=to,
            to_url_hash=compute_url_hash(to), is_internal=True,
            link_position=position, follow=True))
    db_session.flush()


def test_template_links_do_not_trigger(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job(config={"analysis_thresholds": {"max_outlinks": 5}})
    page = _url(db_session, job, "/blog/articulo")
    # 20 de menú/pie + 2 de contenido → contenido=2 ≤ 5 → NO dispara
    _links(db_session, job, page, 12, "nav")
    _links(db_session, job, page, 8, "footer")
    _links(db_session, job, page, 2, "content")

    SEOAnalyzer(db_session, job.id).analyze_links()
    db_session.flush()

    assert db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "high_outlink_count").count() == 0


def test_many_content_links_trigger(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job(config={"analysis_thresholds": {"max_outlinks": 5}})
    page = _url(db_session, job, "/blog/enlazadisimo")
    _links(db_session, job, page, 8, "content")   # contenido=8 > 5 → dispara
    _links(db_session, job, page, 30, "nav")      # plantilla no cuenta

    SEOAnalyzer(db_session, job.id).analyze_links()
    db_session.flush()

    issue = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "high_outlink_count").one()
    assert issue.details["count"] == 8
    assert issue.details["content_links"] == 8
