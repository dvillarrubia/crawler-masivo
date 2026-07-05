"""
Validación de rich results de datos estructurados.

`validate_rich_result` comprueba campos obligatorios/recomendados por tipo
de schema. Antes las columnas validation_status/issues quedaban NULL y los
issues structured_data_error/warning no saltaban nunca.
"""

from __future__ import annotations

from analysis.rich_results import validate_rich_result


def test_product_ok_with_offers():
    status, issues = validate_rich_result("Product", {
        "@type": "Product", "name": "Zapato", "image": "x.jpg",
        "brand": "Acme", "description": "d",
        "offers": {"@type": "Offer", "price": "10", "priceCurrency": "EUR"},
    })
    assert status == "ok" and issues is None


def test_product_missing_name_is_error():
    status, issues = validate_rich_result("Product", {
        "@type": "Product", "offers": {"price": "10"}})
    assert status == "error"
    assert any(i["field"] == "name" and i["level"] == "error" for i in issues)


def test_product_without_offer_review_rating_is_error():
    # name presente pero sin offers/review/aggregateRating → error (one_of)
    status, issues = validate_rich_result("Product", {
        "@type": "Product", "name": "Zapato", "image": "x.jpg"})
    assert status == "error"
    assert any("/" in i["field"] for i in issues)  # el grupo one_of


def test_product_missing_recommended_is_warning():
    status, issues = validate_rich_result("Product", {
        "@type": "Product", "name": "Zapato",
        "offers": {"price": "10"}})   # sin image/brand/description
    assert status == "warning"
    assert all(i["level"] == "warning" for i in issues)
    fields = {i["field"] for i in issues}
    assert "image" in fields and "brand" in fields


def test_article_alias_newsarticle():
    # NewsArticle usa el spec de Article
    status, issues = validate_rich_result("NewsArticle", {
        "@type": "NewsArticle"})   # sin headline
    assert status == "error"
    assert any(i["field"] == "headline" for i in issues)


def test_faqpage_needs_mainentity():
    err, _ = validate_rich_result("FAQPage", {"@type": "FAQPage"})
    ok, _ = validate_rich_result("FAQPage", {
        "@type": "FAQPage", "mainEntity": [{"@type": "Question"}]})
    assert err == "error" and ok == "ok"


def test_localbusiness_alias_and_required():
    status, issues = validate_rich_result("Restaurant", {
        "@type": "Restaurant", "name": "Bar Paco"})   # falta address
    assert status == "error"
    assert any(i["field"] == "address" for i in issues)


def test_unknown_type_is_not_validated():
    status, issues = validate_rich_result("WebSite", {"@type": "WebSite"})
    assert status is None and issues is None


def test_type_as_schema_org_url():
    # @type como URL completa se normaliza
    status, _ = validate_rich_result("https://schema.org/Product", {
        "name": "X", "offers": {"price": "1"}, "image": "i", "brand": "b",
        "description": "d"})
    assert status == "ok"


def test_graph_node_resolution():
    # raw con @graph: encuentra el nodo del tipo
    raw = {"@graph": [
        {"@type": "WebSite", "name": "site"},
        {"@type": "Product", "name": "P", "offers": {"price": "1"},
         "image": "i", "brand": "b", "description": "d"},
    ]}
    status, _ = validate_rich_result("Product", raw)
    assert status == "ok"


def test_list_raw_resolution():
    raw = [{"@type": "Organization", "name": "Acme"}]  # falta url/logo → warning
    status, issues = validate_rich_result("Organization", raw)
    assert status == "warning"
    assert any(i["field"] == "logo" for i in issues)


# --- integración con el analyzer -----------------------------------------
def test_analyzer_emits_and_persists(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue, StructuredData, Url
    from shared.url_normalization import compute_url_hash

    job = make_job()
    full = "https://toy.local/producto"
    u = Url(job_id=job.id, url=full, url_hash=compute_url_hash(full),
            is_internal=True, is_html=True, status_code=200, status_group="2xx")
    db_session.add(u)
    db_session.flush()
    # un Product sin oferta/valoración → error de rich result
    sd = StructuredData(url_id=u.id, format="jsonld", schema_type="Product",
                        raw={"@type": "Product", "name": "Zapato"})
    db_session.add(sd)
    db_session.flush()

    SEOAnalyzer(db_session, job.id).analyze_structured_data()
    db_session.flush()

    issue = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "structured_data_error").one()
    assert issue.url_id == u.id
    assert issue.details["validation_issues"]           # lista de campos
    # persistido en la propia fila
    db_session.refresh(sd)
    assert sd.validation_status == "error"


def test_analyzer_valid_product_no_issue(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue, StructuredData, Url
    from shared.url_normalization import compute_url_hash

    job = make_job()
    full = "https://toy.local/ok"
    u = Url(job_id=job.id, url=full, url_hash=compute_url_hash(full),
            is_internal=True, is_html=True, status_code=200, status_group="2xx")
    db_session.add(u)
    db_session.flush()
    sd = StructuredData(url_id=u.id, format="jsonld", schema_type="Product",
                        raw={"@type": "Product", "name": "Z", "image": "i",
                             "brand": "b", "description": "d",
                             "offers": {"price": "1"}})
    db_session.add(sd)
    db_session.flush()

    SEOAnalyzer(db_session, job.id).analyze_structured_data()
    db_session.flush()

    assert db_session.query(Issue).filter(Issue.job_id == job.id).count() == 0
    db_session.refresh(sd)
    assert sd.validation_status == "ok"
