"""
Tests de T4 — meta refresh (parseo de la columna existente, C1) y js_redirect.
"""

from __future__ import annotations

import pytest

from analysis.analyzer import parse_meta_refresh


# ---------------------------------------------------------------------------
# parse_meta_refresh (función pura)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content,expected", [
    ("5; url=https://x.com/nuevo", (5, "https://x.com/nuevo")),
    ("0;URL=/interna", (0, "/interna")),
    ("3; url='https://x.com/q'", (3, "https://x.com/q")),
    ('10; url="/con-comillas"', (10, "/con-comillas")),
    ("30", (30, None)),                       # reload sin destino
    ("0.5;url=/decimal", (0, "/decimal")),    # delay decimal → int
    ("", (None, None)),
    (None, (None, None)),
    ("garbage", (None, None)),
    ("url=/sin-delay", (None, "/sin-delay")),
])
def test_parse_meta_refresh(content, expected):
    assert parse_meta_refresh(content) == expected


# ---------------------------------------------------------------------------
# analyze_meta_refresh
# ---------------------------------------------------------------------------

def _page_with_refresh(db_session, job, path, refresh):
    from shared.models import HtmlMeta, Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        is_internal=True, is_html=True, status_code=200, status_group="2xx",
    )
    db_session.add(u)
    db_session.flush()
    db_session.add(HtmlMeta(url_id=u.id, meta_refresh=refresh))
    db_session.flush()
    return u


def test_meta_refresh_fast_is_warning_with_resolved_target(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import HtmlMeta, Issue

    job = make_job()
    fast = _page_with_refresh(db_session, job, "/vieja", "0; url=/nueva")

    SEOAnalyzer(db_session, job.id).analyze_meta_refresh()
    db_session.flush()

    issue = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "meta_refresh_redirect"
    ).one()
    assert issue.url_id == fast.id
    assert issue.severity == "warning"
    # destino relativo resuelto contra la URL de la página (criterio T4)
    assert issue.details["target"] == "https://toy.local/nueva"
    assert issue.details["delay"] == 0

    meta = db_session.query(HtmlMeta).filter(HtmlMeta.url_id == fast.id).one()
    assert meta.meta_refresh_url == "https://toy.local/nueva"
    assert meta.meta_refresh_delay == 0


def test_meta_refresh_slow_is_info(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job()
    _page_with_refresh(db_session, job, "/lenta", "30; url=/destino")

    SEOAnalyzer(db_session, job.id).analyze_meta_refresh()
    db_session.flush()

    issue = db_session.query(Issue).filter(Issue.job_id == job.id).one()
    assert issue.severity == "info"


def test_meta_refresh_without_target_or_self_is_not_issue(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import HtmlMeta, Issue

    job = make_job()
    reload_only = _page_with_refresh(db_session, job, "/reload", "300")
    self_ref = _page_with_refresh(
        db_session, job, "/self", "5; url=https://toy.local/self"
    )

    SEOAnalyzer(db_session, job.id).analyze_meta_refresh()
    db_session.flush()

    assert db_session.query(Issue).filter(Issue.job_id == job.id).count() == 0
    # pero el delay sí se persiste
    meta = db_session.query(HtmlMeta).filter(
        HtmlMeta.url_id == reload_only.id
    ).one()
    assert meta.meta_refresh_delay == 300
    assert meta.meta_refresh_url is None
    meta_self = db_session.query(HtmlMeta).filter(
        HtmlMeta.url_id == self_ref.id
    ).one()
    assert meta_self.meta_refresh_url == "https://toy.local/self"


# ---------------------------------------------------------------------------
# analyze_js_redirects
# ---------------------------------------------------------------------------

def test_js_redirect_issue(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue, Url
    from shared.url_normalization import compute_url_hash

    job = make_job()
    u = Url(
        job_id=job.id, url="https://toy.local/spa",
        url_hash=compute_url_hash("https://toy.local/spa"),
        is_internal=True, is_html=True, status_code=200, status_group="2xx",
        js_redirect_url="https://toy.local/destino-final",
    )
    normal = Url(
        job_id=job.id, url="https://toy.local/normal",
        url_hash=compute_url_hash("https://toy.local/normal"),
        is_internal=True, is_html=True, status_code=200, status_group="2xx",
    )
    db_session.add_all([u, normal])
    db_session.flush()

    SEOAnalyzer(db_session, job.id).analyze_js_redirects()
    db_session.flush()

    issues = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "js_redirect"
    ).all()
    assert len(issues) == 1
    assert issues[0].url_id == u.id
    assert issues[0].severity == "warning"
    assert issues[0].details["target"] == "https://toy.local/destino-final"


# ---------------------------------------------------------------------------
# _detect_js_redirect (lógica del spider, sin Scrapy corriendo)
# ---------------------------------------------------------------------------

class _FakePageMethod:
    def __init__(self, args, result=None):
        self.args = args
        self.result = result


class _FakeRequest:
    def __init__(self, url, page_methods):
        self.url = url
        self.meta = {"playwright": True,
                     "playwright_page_methods": page_methods}


class _FakeResponse:
    def __init__(self, url, request):
        self.url = url
        self.request = request


def _detect(requested, final, nav_entries):
    pytest.importorskip("scrapy_playwright")
    from seo_crawler.spiders.seo_spider import _NAV_ENTRIES_JS, SeoSpider

    pm = _FakePageMethod((_NAV_ENTRIES_JS,), nav_entries)
    resp = _FakeResponse(final, _FakeRequest(requested, [pm]))
    return SeoSpider._detect_js_redirect(object.__new__(SeoSpider), resp)


def test_detect_js_redirect_same_url_is_none():
    assert _detect("https://x.com/a", "https://x.com/a", []) is None


def test_detect_js_redirect_flags_js_navigation():
    # URL cambió y la navegación final NO tuvo redirecciones HTTP → JS
    result = _detect(
        "https://x.com/a", "https://x.com/b",
        [{"name": "https://x.com/b", "redirectCount": 0, "type": "navigate"}],
    )
    assert result == "https://x.com/b"


def test_detect_js_redirect_ignores_http_redirects():
    # URL cambió pero fue una redirección HTTP dentro del navegador
    result = _detect(
        "https://x.com/a", "https://x.com/b",
        [{"name": "https://x.com/b", "redirectCount": 1, "type": "navigate"}],
    )
    assert result is None


def test_detect_js_redirect_without_probe_is_conservative():
    pytest.importorskip("scrapy_playwright")
    from seo_crawler.spiders.seo_spider import SeoSpider

    resp = _FakeResponse(
        "https://x.com/b", _FakeRequest("https://x.com/a", []),
    )
    assert SeoSpider._detect_js_redirect(object.__new__(SeoSpider), resp) is None
