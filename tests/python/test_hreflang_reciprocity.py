"""
Validación de return-tags de hreflang (reciprocidad).

Antes `return_tag_ok`/`lang_valid` quedaban siempre NULL y por eso
Insights → i18n salía "sin validar". Ahora `analyze_hreflang` calcula la
reciprocidad con tres estados honestos (True/False/None) y rellena las
columnas + emite issues.
"""

from __future__ import annotations


def _url(db_session, job, path, *, status=200):
    from shared.models import Url
    from shared.url_normalization import compute_url_hash

    full = f"https://toy.local{path}"
    u = Url(job_id=job.id, url=full, url_hash=compute_url_hash(full),
            is_internal=True, is_html=True, status_code=status,
            status_group=f"{status // 100}xx")
    db_session.add(u)
    db_session.flush()
    return u


def _hl(db_session, url, lang, href):
    from shared.models import Hreflang

    h = Hreflang(url_id=url.id, lang=lang, href=href)
    db_session.add(h)
    db_session.flush()
    return h


def _run(db_session, job):
    from analysis.analyzer import SEOAnalyzer

    SEOAnalyzer(db_session, job.id).analyze_hreflang()
    db_session.flush()


def test_reciprocal_pair_is_ok(db_session, make_job):
    from shared.models import Hreflang, Issue

    job = make_job()
    es = _url(db_session, job, "/es/servicio")
    en = _url(db_session, job, "/en/service")
    # cada una declara a la otra + autorreferencia (patrón correcto)
    _hl(db_session, es, "es", "https://toy.local/es/servicio")
    _hl(db_session, es, "en", "https://toy.local/en/service")
    _hl(db_session, en, "es", "https://toy.local/es/servicio")
    _hl(db_session, en, "en", "https://toy.local/en/service")

    _run(db_session, job)

    # todas recíprocas → return_tag_ok True, ningún issue de retorno
    assert all(h.return_tag_ok is True for h in db_session.query(Hreflang).all())
    assert db_session.query(Issue).filter(
        Issue.issue_type == "hreflang_missing_return").count() == 0


def test_one_way_is_missing_return(db_session, make_job):
    from shared.models import Hreflang, Issue

    job = make_job()
    es = _url(db_session, job, "/es/p")
    en = _url(db_session, job, "/en/p")
    # es → en, pero en NO enlaza de vuelta a es
    _hl(db_session, es, "en", "https://toy.local/en/p")
    _hl(db_session, en, "en", "https://toy.local/en/p")  # solo autorreferencia

    _run(db_session, job)

    to_en = db_session.query(Hreflang).filter(
        Hreflang.url_id == es.id, Hreflang.lang == "en").one()
    assert to_en.return_tag_ok is False    # en no devuelve a es
    assert db_session.query(Issue).filter(
        Issue.job_id == job.id,
        Issue.issue_type == "hreflang_missing_return").count() == 1


def test_target_not_crawled_is_none(db_session, make_job):
    from shared.models import Hreflang, Issue

    job = make_job()
    es = _url(db_session, job, "/es/x")
    # destino en otro dominio no rastreado: no se puede confirmar → None
    _hl(db_session, es, "fr", "https://otro-dominio.fr/x")

    _run(db_session, job)

    h = db_session.query(Hreflang).filter(Hreflang.lang == "fr").one()
    assert h.return_tag_ok is None         # honesto: desconocido
    # None no es False → no se emite missing_return
    assert db_session.query(Issue).filter(
        Issue.issue_type == "hreflang_missing_return").count() == 0


def test_relative_href_resolved_against_source(db_session, make_job):
    from shared.models import Hreflang

    job = make_job()
    es = _url(db_session, job, "/es/rel")
    en = _url(db_session, job, "/en/rel")
    # hrefs RELATIVOS: deben resolverse contra la URL de origen
    _hl(db_session, es, "en", "/en/rel")
    _hl(db_session, en, "es", "/es/rel")

    _run(db_session, job)

    assert all(h.return_tag_ok is True for h in db_session.query(Hreflang).all())


def test_invalid_lang_flagged(db_session, make_job):
    from shared.models import Hreflang, Issue

    job = make_job()
    p = _url(db_session, job, "/p")
    _hl(db_session, p, "es", "https://toy.local/p")        # válido
    _hl(db_session, p, "zz-XX-nope!!", "https://toy.local/p")  # inválido

    _run(db_session, job)

    bad = db_session.query(Hreflang).filter(Hreflang.lang.like("zz%")).one()
    good = db_session.query(Hreflang).filter(Hreflang.lang == "es").one()
    assert bad.lang_valid is False and good.lang_valid is True
    assert db_session.query(Issue).filter(
        Issue.issue_type == "hreflang_invalid_lang").count() == 1


def test_broken_target_uses_resolved_url(db_session, make_job):
    from shared.models import Issue

    job = make_job()
    es = _url(db_session, job, "/es/ok")
    _url(db_session, job, "/en/roto", status=404)
    # href relativo a un destino rastreado con 404
    _hl(db_session, es, "en", "/en/roto")
    _hl(db_session, es, "es", "/es/ok")

    _run(db_session, job)

    broken = db_session.query(Issue).filter(
        Issue.job_id == job.id, Issue.issue_type == "hreflang_broken_target").one()
    assert broken.severity == "error"
    assert broken.details["target_status"] == 404


def test_xdefault_is_valid_lang(db_session, make_job):
    from shared.models import Hreflang

    job = make_job()
    p = _url(db_session, job, "/home")
    _hl(db_session, p, "x-default", "https://toy.local/home")

    _run(db_session, job)

    h = db_session.query(Hreflang).filter(Hreflang.lang == "x-default").one()
    assert h.lang_valid is True
    assert h.return_tag_ok is True   # autorreferencia
