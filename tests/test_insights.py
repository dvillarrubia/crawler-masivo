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
from shared.models import Base, Hreflang, Issue, Job, Link, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    # En SQLite solo INTEGER PRIMARY KEY se autoincrementa; BIGINT no.
    return "INTEGER"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[Job.__table__, Url.__table__, Issue.__table__, Hreflang.__table__,
                        Link.__table__]
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


def test_i18n_hreflang_solo_del_sitemap_con_un_error_no_es_100(db, job):
    """Decision 167: un sitio que declara el hreflang solo en el sitemap no
    tiene filas en `hreflang`, pero el analisis abre sus avisos. Salia
    score 100 sin recomendaciones con un `hreflang_broken_target` abierto."""
    u = _url(db, job, "/es/")
    db.add(Issue(job_id=job.id, url_id=u.id, issue_type="hreflang_broken_target",
                 severity="error", details={"origen": "sitemap"}))
    db.add(Issue(job_id=job.id, url_id=u.id, issue_type="hreflang_missing_return",
                 severity="warning", details={"origen": "sitemap"}))
    db.commit()
    ins = results._calc_i18n(job.id, db)
    assert ins.score == results.TOPE_CON_ERRORES
    assert ins.metrics["solo_sitemap"] is True
    assert ins.metrics["destino_malo"] == 1
    assert ins.metrics["return_missing"] == 1
    assert ins.metrics["return_unknown"] == 0
    tipos = {t for r in ins.recommendations for t in (r.issue_types or [])}
    assert {"hreflang_broken_target", "hreflang_missing_return"} <= tipos
    assert "Hreflang sin verificar" not in [r.title for r in ins.recommendations]


def test_i18n_sin_hreflang_ni_avisos_sigue_en_100(db, job):
    u = _url(db, job, "/es/")
    _issue(db, job, u, "title_too_long")
    db.commit()
    ins = results._calc_i18n(job.id, db)
    assert ins.score == 100 and ins.recommendations == []


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


def test_una_soft_404_no_mejora_la_nota_de_contenido(db, job):
    """Decision 105: el analisis le quita a la soft 404 su `low_word_count`,
    su `title_duplicate`... (un aviso por hallazgo). Sin contarla aqui, la
    pagina dejaba de restar y la nota SUBIA por un hallazgo peor."""
    escasa = [_url(db, job, f"/e{i}") for i in range(2)]
    for p in escasa:
        _issue(db, job, p, "low_word_count")
        _issue(db, job, p, "title_duplicate")
    sanas = [_url(db, job, f"/s{i}") for i in range(2)]
    db.commit()
    antes = results._calc_content(job.id, db)

    # Las mismas dos paginas, ahora reconocidas como soft 404
    db.query(Issue).filter(Issue.job_id == job.id).delete()
    for p in escasa:
        _issue(db, job, p, "soft_404")
    _issue(db, job, sanas[0], "soft_404_candidato")
    db.commit()
    ahora = results._calc_content(job.id, db)

    assert ahora.score <= antes.score
    assert ahora.metrics["soft404_count"] == 2 and ahora.metrics["soft404_indexables"] == 2
    recs = {r.title: r for r in ahora.recommendations}
    soft = recs["Corregir paginas de error que responden 200 (soft 404)"]
    assert soft.priority == "alta" and soft.affected_count == 2
    assert recs["Revisar posibles soft 404"].affected_count == 1
    # Y no se recomienda arreglar el titulo de una pagina de error
    assert "Optimizar titulos" not in recs


def test_lo_que_solo_cuelga_de_una_paginacion_excluida_resta_como_una_huerfana(db, job):
    """Decision 122: el analisis pasa paginas de `orphan_page`/`sitemap_orphan`
    a `solo_enlazada_desde_paginacion_excluida`. Contando solo `orphan_page`, la
    nota de Enlazado SUBIA en el censo donde se encuentra el problema."""
    paginas = [_url(db, job, f"/p{i}") for i in range(10)]
    for p in paginas[:3]:
        _issue(db, job, p, "orphan_page")
    _issue(db, job, paginas[3], "sitemap_orphan")
    db.commit()
    antes = results._calc_links(job.id, db)

    db.query(Issue).filter(Issue.job_id == job.id).delete()
    _issue(db, job, paginas[0], "orphan_page")
    for p in paginas[1:4]:
        _issue(db, job, p, "solo_enlazada_desde_paginacion_excluida")
    db.commit()
    ahora = results._calc_links(job.id, db)

    assert ahora.metrics["orphan_count"] == antes.metrics["orphan_count"] == 4
    assert ahora.score == antes.score
    rec = next(r for r in ahora.recommendations if r.title == "Enlazar paginas huerfanas")
    assert rec.affected_count == 4
    assert "solo_enlazada_desde_paginacion_excluida" in rec.issue_types


def test_un_titulo_de_relleno_no_mejora_la_nota_de_contenido(db, job):
    """Revision de #159: `title_generico` (decision 121) es UNA fila por valor
    —en la pagina de URL menor, con `paginas_afectadas`— y el analisis les
    quita a esas paginas su `title_duplicate` y su `title_too_short` (decision
    36). La nota cuenta paginas DISTINTAS con incidencia, asi que tres paginas
    «Sin título | Marca» pasaban de restar tres a restar una: la nota SUBIA por
    un hallazgo mas preciso, lo mismo que la decision 105 arreglo con la soft
    404. Las filas son las que escribe `analyze_titles` (lo comprueba
    `test_titulares_titulos_anclas_db.py`; este trabajo no lleva `analysis/`)."""
    paginas = [_url(db, job, f"/p{i}") for i in range(10)]
    # Antes de la 121: las tres de relleno con `title_duplicate` cada una.
    for p in paginas[7:]:
        _issue(db, job, p, "title_duplicate")
    db.commit()
    antes = results._calc_content(job.id, db)

    db.query(Issue).filter(Issue.job_id == job.id).delete()
    db.add(Issue(job_id=job.id, url_id=paginas[7].id, issue_type="title_generico",
                 severity="warning",
                 details={"title": "Sin título | Marca", "paginas_afectadas": 3,
                          "paginas_ejemplo": [f"https://x.com/p{i}" for i in (7, 8, 9)]}))
    _issue(db, job, paginas[0], "title_too_long")
    db.commit()
    ahora = results._calc_content(job.id, db)

    assert antes.metrics["pct_title_ok"] == 70.0
    assert ahora.metrics["title_problems"] == 4   # tres de relleno + una larga
    assert ahora.metrics["pct_title_ok"] == 60.0
    assert ahora.score <= antes.score
    recs = {r.title: r for r in ahora.recommendations}
    assert recs["Optimizar titulos"].affected_count == 4
    assert "title_generico" in recs["Optimizar titulos"].issue_types
