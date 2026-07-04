"""
Tests de la Fase 4 — calidad del crawl: T13 trampas, T5 frescura,
T6 soft 404, T14 near-duplicates.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _url(db_session, job, path, *, status=200, body_hash=None, words=None,
         title=None, content=None, lastmod=None, simhash=None, host="toy.local"):
    from shared.models import HtmlMeta, PageContent, Url
    from shared.url_normalization import compute_url_hash

    full = f"https://{host}{path}"
    u = Url(
        job_id=job.id, url=full, url_hash=compute_url_hash(full),
        host=host, path=path, scheme="https",
        is_internal=True, is_html=True, status_code=status,
        status_group=f"{status // 100}xx", body_hash=body_hash,
        word_count=words, sitemap_lastmod=lastmod, simhash=simhash,
    )
    db_session.add(u)
    db_session.flush()
    if title is not None:
        db_session.add(HtmlMeta(url_id=u.id, title=title))
    if content is not None:
        db_session.add(PageContent(url_id=u.id, content_text=content))
    db_session.flush()
    return u


# ---------------------------------------------------------------------------
# T13 — trampas de rastreo
# ---------------------------------------------------------------------------

def test_pattern_signature():
    from seo_crawler.trap_detection import pattern_signature

    assert pattern_signature(
        "https://x.com/producto/123?color=rojo&talla=m"
    ) == "x.com/producto/*?color=*&talla=*"
    assert pattern_signature("https://x.com/blog/mi-post") == "x.com/blog/mi-post"
    assert pattern_signature("https://x.com/p/1") == pattern_signature("https://x.com/p/999")


def test_trap_detector_caps_pattern():
    from seo_crawler.trap_detection import TrapDetector

    det = TrapDetector(max_urls_per_pattern=5, max_param_combinations=10)
    allowed = sum(det.allow(f"https://x.com/faceta/{i}") for i in range(20))
    assert allowed == 5

    events = det.events()
    assert len(events) == 1
    assert events[0]["pattern"] == "x.com/faceta/*"
    assert events[0]["urls_seen"] == 5
    assert events[0]["urls_skipped"] == 15
    assert events[0]["first_url_sample"] == "https://x.com/faceta/0"


def test_trap_detector_param_explosion():
    from seo_crawler.trap_detection import TrapDetector

    det = TrapDetector(max_urls_per_pattern=500, max_param_combinations=3)
    assert det.allow("https://x.com/lista?a=1&b=2&c=3") is True
    assert det.allow("https://x.com/lista?a=1&b=2&c=3&d=4") is False  # 4 > 3
    assert det.events()[0]["urls_skipped"] == 1


def test_trap_detector_distinct_patterns_not_capped():
    from seo_crawler.trap_detection import TrapDetector

    det = TrapDetector(max_urls_per_pattern=5, max_param_combinations=3)
    # rutas distintas → firmas distintas → no se capan entre sí
    for i in range(20):
        assert det.allow(f"https://x.com/seccion-{i}/") is True
    assert det.events() == []


def test_analyze_crawl_traps_emits_issue(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import CrawlTrapEvent, Issue

    job = make_job()
    sample = _url(db_session, job, "/faceta/0")
    db_session.add(CrawlTrapEvent(
        job_id=job.id, pattern="toy.local/faceta/*",
        urls_seen=5, urls_skipped=95,
        first_url_sample="https://toy.local/faceta/0",
    ))
    db_session.flush()

    SEOAnalyzer(db_session, job.id).analyze_crawl_traps()
    db_session.flush()

    issue = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "crawl_trap_detected"
    ).one()
    assert issue.url_id == sample.id
    assert issue.details["pattern"] == "toy.local/faceta/*"
    assert issue.details["urls_skipped"] == 95


# ---------------------------------------------------------------------------
# T5 — frescura
# ---------------------------------------------------------------------------

def _client_pair(db_session, make_job):
    a = make_job(name="run-1")
    b = make_job(name="run-2")
    a.client_id = b.client_id = "cliente-x"
    db_session.flush()
    return a, b


def test_freshness_endpoint_flags_only_the_changed_url(db_session, make_job):
    from api.routers.results import get_freshness

    a, b = _client_pair(db_session, make_job)
    _url(db_session, a, "/igual", body_hash="h1")
    _url(db_session, b, "/igual", body_hash="h1")
    _url(db_session, a, "/cambiada", body_hash="h2")
    _url(db_session, b, "/cambiada", body_hash="h2-bis")

    result = get_freshness(
        b.id, compare_to=a.id, only_changed=True, page=1, page_size=50,
        db=db_session,
    )
    assert result["total"] == 1
    assert result["items"][0]["url"] == "https://toy.local/cambiada"
    assert result["items"][0]["body_changed"] is True


def test_freshness_requires_same_client(db_session, make_job):
    from fastapi import HTTPException

    from api.routers.results import get_freshness

    a = make_job(name="run-1")
    b = make_job(name="run-2")
    a.client_id = "uno"
    b.client_id = "otro"
    db_session.flush()

    with pytest.raises(HTTPException) as exc:
        get_freshness(b.id, compare_to=a.id, only_changed=False,
                      page=1, page_size=50, db=db_session)
    assert exc.value.status_code == 422


def test_stale_lastmod_issue(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    a, b = _client_pair(db_session, make_job)
    b.config = {"compare_to_job_id": str(a.id)}
    db_session.flush()

    t1 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    # lastmod cambia pero contenido idéntico → sitemap miente
    _url(db_session, a, "/mentirosa", body_hash="h1", lastmod=t1)
    _url(db_session, b, "/mentirosa", body_hash="h1", lastmod=t2)
    # contenido cambia pero lastmod se queda → sitemap desactualizado
    _url(db_session, a, "/desactualizada", body_hash="h2", lastmod=t1)
    _url(db_session, b, "/desactualizada", body_hash="h2-bis", lastmod=t1)
    # coherente: cambia todo
    _url(db_session, a, "/coherente", body_hash="h3", lastmod=t1)
    _url(db_session, b, "/coherente", body_hash="h3-bis", lastmod=t2)

    SEOAnalyzer(db_session, b.id).analyze_freshness()
    db_session.flush()

    issues = db_session.query(Issue).filter(
        Issue.job_id == b.id, Issue.issue_type == "stale_lastmod"
    ).all()
    reasons = {i.details["reason"] for i in issues}
    assert len(issues) == 2
    assert reasons == {
        "lastmod_changed_content_identical",
        "content_changed_lastmod_stale",
    }


# ---------------------------------------------------------------------------
# T6 — soft 404
# ---------------------------------------------------------------------------

ERROR_TEXT = ("Lo sentimos la página que buscas no existe puede que haya "
              "sido eliminada o que la dirección sea incorrecta vuelve a la "
              "portada o usa el buscador para encontrar lo que necesitas")


def _soft404_job(db_session, make_job, probe_status=404):
    job = make_job(config={
        "detect_soft_404": True,
        "_soft404_signature": {
            "toy.local": {
                "status": probe_status,
                "body_hash": "PROBE_HASH",
                "sample_text": ERROR_TEXT,
            },
        },
    })
    return job


def test_soft404_by_template_hash(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = _soft404_job(db_session, make_job)
    ghost = _url(db_session, job, "/fantasma", body_hash="PROBE_HASH")
    _url(db_session, job, "/normal", body_hash="OTRA")

    SEOAnalyzer(db_session, job.id).analyze_soft_404()
    db_session.flush()

    issues = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "soft_404"
    ).all()
    assert [i.url_id for i in issues] == [ghost.id]
    assert issues[0].severity == "error"
    assert issues[0].details["reason"] == "probe_template_hash"


def test_soft404_by_text_similarity(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = _soft404_job(db_session, make_job)
    similar = _url(db_session, job, "/casi-error", body_hash="X",
                   content=ERROR_TEXT + " gracias")
    _url(db_session, job, "/articulo", body_hash="Y",
         content="Guía completa de fiscalidad internacional para pymes con "
                 "ejemplos casos prácticos deducciones plazos y modelos")

    SEOAnalyzer(db_session, job.id).analyze_soft_404()
    db_session.flush()

    issues = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "soft_404"
    ).all()
    assert [i.url_id for i in issues] == [similar.id]
    assert issues[0].details["reason"] == "probe_template_similarity"


def test_soft404_by_error_title_and_low_words(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = _soft404_job(db_session, make_job, probe_status=200)  # probe inútil
    flagged = _url(db_session, job, "/rara", words=20,
                   title="Error 404 — página no encontrada")
    _url(db_session, job, "/landing-corta", words=20, title="Reserva tu demo")

    SEOAnalyzer(db_session, job.id).analyze_soft_404()
    db_session.flush()

    issues = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "soft_404"
    ).all()
    assert [i.url_id for i in issues] == [flagged.id]
    assert issues[0].details["probe_returned_200"] is True


def test_soft404_flag_off_is_noop(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue

    job = make_job()
    _url(db_session, job, "/rara", words=5, title="404 not found")
    SEOAnalyzer(db_session, job.id).analyze_soft_404()
    db_session.flush()
    assert db_session.query(Issue).count() == 0


# ---------------------------------------------------------------------------
# T14 — near duplicates (simhash)
# ---------------------------------------------------------------------------

def test_simhash_properties():
    from shared.simhash import from_signed, hamming, simhash64, to_signed

    base = ("La ficha del producto incluye materiales dimensiones garantía "
            "y opiniones de clientes verificados sobre el uso diario")
    variant = base.replace("garantía", "garantia extendida")
    different = ("Política de privacidad sobre tratamiento de datos "
                 "personales cookies analíticas y derechos del usuario")

    h1, h2, h3 = simhash64(base), simhash64(variant), simhash64(different)
    assert hamming(h1, h2) < hamming(h1, h3)
    assert hamming(h1, h1) == 0
    # round-trip signed BIGINT
    assert from_signed(to_signed(h1)) == h1
    assert simhash64("una dos") is None  # demasiado corto


def test_near_duplicates_cluster(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue
    from shared.simhash import simhash64, to_signed

    base = ("Camiseta de algodón orgánico con cuello redondo disponible en "
            "varios colores tallas s m l xl envío gratuito a partir de "
            "cincuenta euros devolución en treinta días")
    near = base.replace("talla s m l xl", "tallas m l")
    far = ("Aviso legal condiciones de contratación jurisdicción aplicable "
           "resolución de conflictos y datos registrales de la sociedad")

    job = make_job(config={"analysis_thresholds": {"near_duplicate_detection": "simhash"}})
    a = _url(db_session, job, "/camiseta-roja", simhash=to_signed(simhash64(base)))
    b = _url(db_session, job, "/camiseta-azul", simhash=to_signed(simhash64(near)))
    c = _url(db_session, job, "/aviso-legal", simhash=to_signed(simhash64(far)))

    SEOAnalyzer(db_session, job.id).analyze_near_duplicates()
    db_session.flush()

    issues = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "near_duplicate_content"
    ).all()
    flagged = {i.url_id for i in issues}
    assert flagged == {a.id, b.id}
    assert all(i.details["method"] == "simhash" for i in issues)
    assert all(i.details["cluster_size"] == 2 for i in issues)


def test_near_duplicates_off_is_noop(db_session, make_job):
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue
    from shared.simhash import simhash64, to_signed

    text = ("Contenido idéntico para las dos páginas del test que no debe "
            "generar issues porque el modo está apagado por defecto")
    job = make_job()
    _url(db_session, job, "/a", simhash=to_signed(simhash64(text)))
    _url(db_session, job, "/b", simhash=to_signed(simhash64(text)))

    SEOAnalyzer(db_session, job.id).analyze_near_duplicates()
    db_session.flush()
    assert db_session.query(Issue).count() == 0
