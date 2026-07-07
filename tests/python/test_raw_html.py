"""store_raw_html: el flag existía en la API y el formulario pero nunca
persistía nada. Estos tests fijan el contrato del arreglo:

- ``page_content.raw_html`` guarda y devuelve el HTML tal cual.
- ``PageContentResponse`` NO incluye el HTML (el detalle de URL seguiría
  siendo ligero); solo se sirve por el endpoint dedicado ``/raw-html``.
- ``ContentItem`` acepta el campo (el pipeline lo aplica genéricamente).
"""

from __future__ import annotations

import pytest


def _page(db_session, job, path="/", raw=None):
    from shared.models import PageContent, Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(job_id=job.id, url=full, url_hash=compute_url_hash(full),
            host="toy.local", path=path, scheme="https", is_internal=True,
            is_html=True, status_code=200, status_group="2xx")
    db_session.add(u)
    db_session.flush()
    db_session.add(PageContent(url_id=u.id, content_text="hola mundo",
                               content_length=10, raw_html=raw))
    db_session.flush()
    return u


def test_raw_html_roundtrip(db_session, make_job):
    from shared.models import PageContent

    job = make_job()
    html = "<!doctype html><html><body><h1>Hola</h1>áñ</body></html>"
    u = _page(db_session, job, raw=html)
    stored = db_session.get(PageContent, u.id)
    assert stored.raw_html == html

    # Sin el flag el campo queda NULL (no se inventa nada)
    u2 = _page(db_session, job, path="/otra", raw=None)
    assert db_session.get(PageContent, u2.id).raw_html is None


def test_page_content_response_does_not_leak_raw_html(db_session, make_job):
    from api.schemas import PageContentResponse

    job = make_job()
    u = _page(db_session, job, raw="<html>enorme</html>")
    from shared.models import PageContent

    resp = PageContentResponse.model_validate(db_session.get(PageContent, u.id))
    assert "raw_html" not in resp.model_dump()


def test_content_item_accepts_raw_html():
    pytest.importorskip("scrapy")
    from seo_crawler.items import ContentItem

    item = ContentItem(url_hash="x", job_id="j", content_text="t",
                       content_length=1, content_markdown=None,
                       raw_html="<html></html>")
    assert item["raw_html"] == "<html></html>"
