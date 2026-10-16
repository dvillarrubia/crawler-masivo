"""Cifras de /insights que se entregan al cliente (SQLite en memoria).

Los fallos de #29 y #26 no rompian nada: daban numeros falsos. `pct_thin`
sumaba incidencias y podia pasar del 100%, `pct_indexable` dividia tambien por
saltos y PDFs, y la nota i18n contaba "sin verificar" como "sin retorno":
Lopesan salia con nota 0 y dos recomendaciones falsas de prioridad alta.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("fastapi")

from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from api.routers import results  # noqa: E402
from shared.models import Base, Hreflang, Issue, Job, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    # En SQLite solo INTEGER PRIMARY KEY se autoincrementa; BIGINT no.
    return "INTEGER"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[Job.__table__, Url.__table__, Issue.__table__, Hreflang.__table__]
    )
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture()
def job(db):
    j = Job(id=uuid.uuid4(), name="t", status="completed", seeds=["https://x.com/"], config={})
    db.add(j)
    db.flush()
    return j


def _url(db, job, ruta, status=200, html=True, indexable=True):
    u = Url(job_id=job.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
            status_code=status, is_html=html, indexable=indexable)
    db.add(u)
    db.flush()
    return u


def _issue(db, job, url, tipo):
    db.add(Issue(job_id=job.id, url_id=url.id, issue_type=tipo, severity="warning"))


def test_contenido_cuenta_paginas_no_incidencias(db, job):
    paginas = [_url(db, job, f"/p{i}") for i in range(4)]
    for p in paginas:  # las 4 flojas por dos motivos cada una
        _issue(db, job, p, "low_word_count")
        _issue(db, job, p, "low_text_ratio")
    _issue(db, job, paginas[0], "title_too_long")
    _issue(db, job, paginas[0], "title_duplicate")
    _url(db, job, "/viejo", status=301, html=False, indexable=None)
    _url(db, job, "/roto", status=404, indexable=None)
    db.commit()

    m = results._calc_content(job.id, db).metrics
    assert m["total_html"] == 4          # el 404 HTML no cuenta
    assert m["thin_count"] == 4
    assert m["pct_thin"] == 100.0        # antes: 8 incidencias / 5 = 160%
    assert m["title_problems"] == 1      # una pagina, aunque tenga dos
    assert m["pct_title_ok"] == 75.0


def test_indexables_sobre_paginas_html_que_responden_200(db, job):
    _url(db, job, "/a")
    _url(db, job, "/b", indexable=False)
    for i in range(6):  # saltos, PDF y 404: indexable NULL
        _url(db, job, f"/r{i}", status=301, html=False, indexable=None)
    _url(db, job, "/doc.pdf", html=False, indexable=None)
    _url(db, job, "/roto", status=404, indexable=None)
    db.commit()

    m = results._calc_crawlability(job.id, db).metrics
    assert m["html_2xx"] == 2
    assert m["pct_indexable"] == 50.0    # antes: 1/10 = 10%


def test_i18n_sin_verificar_no_es_fallo(db, job):
    u = _url(db, job, "/es/")
    for lang in ("es", "en", "x-default"):
        db.add(Hreflang(url_id=u.id, lang=lang, href=f"https://x.com/{lang}/"))
    db.commit()

    ins = results._calc_i18n(job.id, db)
    assert ins.score == 100
    assert ins.metrics["return_unknown"] == 3
    assert [r.priority for r in ins.recommendations] == ["baja"]


def test_i18n_cluster_correcto_con_autorreferencia(db, job):
    # es y en con retorno, autorreferencia y x-default sin verificar (NULL)
    u = _url(db, job, "/es/")
    db.add(Hreflang(url_id=u.id, lang="es", href="https://x.com/es/", lang_valid=True))
    db.add(Hreflang(url_id=u.id, lang="en", href="https://x.com/en/",
                    return_tag_ok=True, lang_valid=True))
    db.add(Hreflang(url_id=u.id, lang="x-default", href="https://x.com/",
                    lang_valid=True))
    db.commit()

    ins = results._calc_i18n(job.id, db)
    assert ins.score == 100                # antes ~50 con prioridad alta
    assert ins.metrics["pct_return_ok"] == 100.0
    assert not [r for r in ins.recommendations if r.priority == "alta"]


def test_i18n_sin_retorno_real_si_se_avisa(db, job):
    u = _url(db, job, "/es/")
    db.add(Hreflang(url_id=u.id, lang="en", href="https://x.com/en/",
                    return_tag_ok=False, lang_valid=True))
    db.add(Hreflang(url_id=u.id, lang="fr", href="https://x.com/fr/",
                    return_tag_ok=True, lang_valid=True))
    db.commit()

    ins = results._calc_i18n(job.id, db)
    assert ins.metrics["pct_return_ok"] == 50.0
    assert ins.metrics["return_missing"] == 1
    alta = [r for r in ins.recommendations if r.priority == "alta"]
    assert alta and alta[0].affected_count == 1


def test_i18n_un_destino_descartado_resta_y_se_recomienda(db, job):
    """Lopesan: 27 `hreflang_destino_no_indexable` y 4 `broken_target`
    abiertos, con su `return_tag_ok` a NULL, y la categoria pasaba de 96 a 100
    sin una recomendacion (decision 99)."""
    u = _url(db, job, "/es/")
    for lang in ("en", "fr", "de"):
        db.add(Hreflang(url_id=u.id, lang=lang, href=f"https://x.com/{lang}/",
                        return_tag_ok=True, lang_valid=True))
    db.add(Hreflang(url_id=u.id, lang="it", href="https://x.com/it/",
                    return_tag_ok=None, lang_valid=True))
    db.add(Issue(job_id=job.id, url_id=u.id, issue_type="hreflang_destino_no_indexable",
                 severity="error"))
    db.commit()

    ins = results._calc_i18n(job.id, db)
    assert ins.metrics["destino_malo"] == 1
    assert ins.metrics["pct_destino_ok"] == 75.0
    assert ins.score == results.TOPE_CON_ERRORES  # media 91,7, con un error
    alta = [r for r in ins.recommendations if r.priority == "alta"]
    assert alta and alta[0].affected_count == 1
    assert "hreflang_destino_no_indexable" in alta[0].issue_types


def test_i18n_un_error_entre_miles_tampoco_deja_la_nota_en_100(db, job):
    """Lopesan: 31 errores en 3.635 anotaciones; la media sola redondea a 100."""
    u = _url(db, job, "/es/")
    for i in range(300):
        db.add(Hreflang(url_id=u.id, lang="en", href=f"https://x.com/en/{i}",
                        return_tag_ok=True, lang_valid=True))
    db.add(Issue(job_id=job.id, url_id=u.id, issue_type="hreflang_broken_target",
                 severity="error"))
    db.commit()
    ins = results._calc_i18n(job.id, db)
    assert ins.score == results.TOPE_CON_ERRORES


@pytest.fixture()
def db_completa():
    """El informe entero lee todas las tablas (enlaces, seguridad...)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture()
def job_c(db_completa):
    j = Job(id=uuid.uuid4(), name="t", status="completed", seeds=["https://x.com/"], config={})
    db_completa.add(j)
    db_completa.flush()
    return j


def _error(db, job, url, tipo="canonical_cross_domain"):
    db.add(Issue(job_id=job.id, url_id=url.id, issue_type=tipo, severity="error"))
    db.flush()


def test_sin_errores_la_nota_no_lleva_tope(db_completa, job_c):
    db, job = db_completa, job_c
    for i in range(10):
        _url(db, job, f"/p{i}")
    r = results.get_insights(job.id, db)
    assert r.tope is None
    assert r.overall_score == r.overall_score_sin_tope


def test_con_algun_error_la_nota_no_pasa_de_mejorable(db_completa, job_c):
    db, job = db_completa, job_c
    """UX-05 / decision 92: una media que dice «Bueno» con errores abiertos
    es lo que hizo que Lopesan saliera con 88."""
    urls = [_url(db, job, f"/p{i}") for i in range(20)]
    _error(db, job, urls[0])                      # 1 de 20: 5 %
    r = results.get_insights(job.id, db)
    assert r.overall_score <= results.TOPE_CON_ERRORES
    if r.overall_score_sin_tope > results.TOPE_CON_ERRORES:
        assert r.tope is not None and r.tope.paginas_con_error == 1 and r.tope.de == 20


def test_errores_extendidos_la_dejan_en_critico(db_completa, job_c):
    db, job = db_completa, job_c
    urls = [_url(db, job, f"/p{i}") for i in range(20)]
    for u in urls[:3]:                            # 3 de 20: 15 %
        _error(db, job, u)
    _error(db, job, urls[0], "4xx_error")         # misma pagina: cuenta una vez
    r = results.get_insights(job.id, db)
    assert r.overall_score <= results.TOPE_CON_ERRORES_EXTENDIDOS
    assert r.tope is not None and r.tope.paginas_con_error == 3 and r.tope.pct == 15.0
