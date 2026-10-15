"""Lo que quedaba de #26: PDFs, hreflang en el body, un solo normalizador y
titulos o descriptions repetidos.

Cada caso con lo que decide Google:

- Un PDF solo puede llevar sus directivas en la CABECERA (`X-Robots-Tag`,
  `Link: rel=canonical`). El rastreo no las leia en los no HTML y, aunque las
  leyera, `analyze_indexability` reescribe la indexabilidad de todas las URLs
  con lo que hay en `html_meta`: sin fila, el PDF volvia a "Indexable".
- Un hreflang en el `<body>` Google lo ignora, como el canonical (decision 56):
  se aceptaba sin aviso.
- `analyze_canonicals` comparaba con w3lib a secas mientras la indexabilidad
  usaba `misma_url`: un canonical a `https://x.com:443/b` era "la misma URL"
  para una y "otra" para la otra, y el 404 de `/b` no salia como roto.
- Con dos `<title>` no se sabe cual usa Google, y el vacio delante del bueno se
  saltaba en silencio. Screaming Frog lo reporta como «multiple».
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Hreflang, HtmlMeta, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Hreflang.__table__,
        Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, url, *, status=200, is_html=True, estado=None, sin_meta=False,
         **meta):
    u = Url(job_id=j.id, url=url, url_hash=url[-40:], is_internal=True,
            is_html=is_html, status_code=status, host=url.split("/")[2],
            resource_type="html" if is_html else "pdf",
            indexability_status=estado)
    s.add(u)
    s.flush()
    if not sin_meta:
        s.add(HtmlMeta(url_id=u.id, **meta))
        s.flush()
    return u


def _tipos(s, j, url_id):
    return sorted(
        i.issue_type for i in
        s.query(Issue).filter(Issue.job_id == j.id, Issue.url_id == url_id).all()
    )


# -- PDF con directivas de cabecera ------------------------------------------

def test_un_pdf_en_noindex_por_cabecera_sigue_en_noindex_tras_analizar():
    s, j = _montar()
    pdf = _url(s, j, "https://x.com/catalogo.pdf", is_html=False, estado="Noindex",
               x_robots_tag="noindex")
    s.commit()
    SEOAnalyzer(s, j.id).analyze_indexability()
    s.commit()
    fila = s.execute(select(Url.indexable, Url.indexability_status, Url.noindex)
                     .where(Url.id == pdf.id)).one()
    assert tuple(fila) == (False, "Noindex", True)


def test_un_pdf_canonicalizado_por_link_sigue_canonicalizado_tras_analizar():
    s, j = _montar()
    pdf = _url(s, j, "https://x.com/ficha.pdf", is_html=False, estado="Canonicalised",
               canonical_header="https://x.com/ficha")
    s.commit()
    SEOAnalyzer(s, j.id).analyze_indexability()
    s.commit()
    fila = s.execute(select(Url.indexable, Url.indexability_status)
                     .where(Url.id == pdf.id)).one()
    assert tuple(fila) == (False, "Canonicalised")


def test_la_fila_de_un_pdf_no_dispara_avisos_de_pagina():
    """Tener ahora fila en `html_meta` no puede convertir un PDF en una pagina
    sin titulo, sin description o sin canonical."""
    s, j = _montar()
    pdf = _url(s, j, "https://x.com/ficha.pdf", is_html=False,
               canonical_header="https://x.com/ficha", x_robots_tag="noindex")
    a = SEOAnalyzer(s, j.id)
    a.analyze_titles()
    a.analyze_descriptions()
    a.analyze_canonicals()
    a.analyze_robots_syntax()
    s.flush()
    assert _tipos(s, j, pdf.id) == []


# -- Un solo normalizador ----------------------------------------------------

def test_canonical_roto_con_el_puerto_por_defecto_se_ve():
    """`https://x.com:443/b` es `https://x.com/b` (decision 39). Con w3lib a
    secas no casaba con la URL rastreada y el 404 del destino no salia."""
    s, j = _montar()
    a = _url(s, j, "https://x.com/a", canonical_href="https://x.com:443/b",
             canonical_count=1)
    _url(s, j, "https://x.com/b", status=404, sin_meta=True)
    SEOAnalyzer(s, j.id).analyze_canonicals()
    s.flush()
    # `canonical_broken` se separo en error y redireccion (decision 101).
    assert "canonical_a_error" in _tipos(s, j, a.id)


def test_la_barra_final_si_son_dos_urls():
    """El docstring viejo prometia igualar la barra final y no lo hacia; no debe:
    `/a` y `/a/` son dos URLs y normalmente una redirige a la otra."""
    s, j = _montar()
    a = _url(s, j, "https://x.com/a", canonical_href="https://x.com/a/",
             canonical_count=1)
    _url(s, j, "https://x.com/a/", status=301, sin_meta=True)
    SEOAnalyzer(s, j.id).analyze_canonicals()
    s.flush()
    assert "canonical_a_redireccion" in _tipos(s, j, a.id)


def test_el_retorno_del_hreflang_casa_con_el_puerto_por_defecto():
    s, j = _montar()
    es = _url(s, j, "https://x.com/es/", canonical_href="https://x.com/es/")
    en = _url(s, j, "https://x.com/en/", canonical_href="https://x.com/en/")
    # /es anota /en con el puerto por defecto; /en NO devuelve el enlace.
    s.add(Hreflang(url_id=es.id, lang="en", href="https://x.com:443/en/"))
    s.add(Hreflang(url_id=en.id, lang="fr", href="https://x.com/fr/"))
    s.flush()
    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()
    assert "hreflang_missing_return" in _tipos(s, j, es.id)


# -- Hreflang en el body -----------------------------------------------------

def test_un_hreflang_en_el_body_se_avisa_una_vez_por_pagina():
    s, j = _montar()
    es = _url(s, j, "https://x.com/es/", canonical_href="https://x.com/es/")
    s.add(Hreflang(url_id=es.id, lang="en", href="https://x.com/en/", en_body=True))
    s.add(Hreflang(url_id=es.id, lang="fr", href="https://x.com/fr/", en_body=True))
    s.flush()
    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()
    assert _tipos(s, j, es.id) == ["hreflang_in_body"]
    issue = s.query(Issue).filter(Issue.issue_type == "hreflang_in_body").one()
    assert issue.details["cuantos"] == 2


def test_un_hreflang_en_el_body_no_cuenta_como_retorno():
    """Google no lo lee, asi que la otra pagina sigue sin retorno."""
    s, j = _montar()
    es = _url(s, j, "https://x.com/es/", canonical_href="https://x.com/es/")
    en = _url(s, j, "https://x.com/en/", canonical_href="https://x.com/en/")
    s.add(Hreflang(url_id=es.id, lang="en", href="https://x.com/en/"))
    s.add(Hreflang(url_id=en.id, lang="es", href="https://x.com/es/", en_body=True))
    s.flush()
    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()
    assert "hreflang_missing_return" in _tipos(s, j, es.id)
    assert "hreflang_missing_return" not in _tipos(s, j, en.id)


# -- Idioma ISO 639-1 --------------------------------------------------------

@pytest.mark.parametrize("lang", ["eng", "xx", "sp-ES", "eng-GB"])
def test_un_idioma_que_no_es_iso_639_1_no_es_valido(lang):
    s, j = _montar()
    a = _url(s, j, "https://x.com/", canonical_href="https://x.com/")
    s.add(Hreflang(url_id=a.id, lang=lang, href="https://x.com/otro/"))
    s.flush()
    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()
    assert "hreflang_invalid_lang" in _tipos(s, j, a.id)


@pytest.mark.parametrize("lang", ["es", "en-GB", "zh-Hant", "zh-Hant-TW", "iw", "eu",
                                  "x-default"])
def test_los_idiomas_validos_siguen_validos(lang):
    s, j = _montar()
    a = _url(s, j, "https://x.com/", canonical_href="https://x.com/")
    s.add(Hreflang(url_id=a.id, lang=lang, href="https://x.com/otro/"))
    s.flush()
    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()
    assert "hreflang_invalid_lang" not in _tipos(s, j, a.id)


# -- Titulo y description multiples ------------------------------------------

def test_dos_titulos_se_avisan_y_se_dice_si_el_primero_esta_vacio():
    s, j = _montar()
    a = _url(s, j, "https://x.com/a", title=None, title_count=2)
    SEOAnalyzer(s, j.id).analyze_titles()
    s.flush()
    assert "title_multiple" in _tipos(s, j, a.id)
    issue = s.query(Issue).filter(Issue.issue_type == "title_multiple").one()
    assert issue.details == {"cuantos": 2, "primero_vacio": True}


def test_dos_descriptions_se_avisan():
    s, j = _montar()
    a = _url(s, j, "https://x.com/a", title="Un titulo de prueba razonable",
             meta_description="Una description con longitud suficiente para no ser corta.",
             meta_description_count=2)
    SEOAnalyzer(s, j.id).analyze_descriptions()
    s.flush()
    assert _tipos(s, j, a.id) == ["description_multiple"]


def test_un_solo_titulo_o_un_rastreo_viejo_no_avisan():
    s, j = _montar()
    a = _url(s, j, "https://x.com/a", title="Un titulo de prueba razonable",
             title_count=1)
    b = _url(s, j, "https://x.com/b", title="Otro titulo de prueba distinto",
             title_count=None)
    SEOAnalyzer(s, j.id).analyze_titles()
    s.flush()
    assert "title_multiple" not in _tipos(s, j, a.id) + _tipos(s, j, b.id)
