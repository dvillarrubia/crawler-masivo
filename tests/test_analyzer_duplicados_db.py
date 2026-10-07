"""Duplicados: solo entre paginas que Google puede posicionar, y por contenido.

Dos criterios SEO, los dos medidos en tres censos reales:

- Un grupo de duplicados solo significa algo entre paginas INDEXABLES. Dos
  variantes `?utm`, dos ordenaciones de un listado o un `/page/2` comparten
  titulo a proposito y estan canonicalizadas o en noindex: no compiten con
  nadie. Medido, los avisos de titulo duplicado pasan de 5.936 a 2.788 paginas
  en un censo, de 14.580 a 5.299 en otro y de 5.557 a 1.417 en el tercero; los
  de description, de 2.807 a 659; los de h1, de 15.235 a 10.838.
- El duplicado exacto se mide sobre el contenido, no sobre los bytes. Con
  `body_hash` (SHA-256 de la respuesta), un token CSRF o un nonce de CSP hacen
  que dos paginas identicas no coincidan: en blogs.uoc.edu encontraba **0**
  duplicados y el hash del contenido normalizado encuentra **745 paginas en 304
  grupos** — entre ellos la misma politica de privacidad de 652 palabras
  publicada e indexable en decenas de blogs del multisite.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Heading, HtmlMeta, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Heading.__table__,
        Issue.__table__,
    ])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, *, indexable=True, titulo=None, desc=None, h1=None,
         content_hash=None, body_hash=None):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta,
            is_internal=True, is_html=True, status_code=200, indexable=indexable,
            content_hash=content_hash, body_hash=body_hash)
    s.add(u)
    s.flush()
    if titulo is not None or desc is not None:
        s.add(HtmlMeta(url_id=u.id, title=titulo, title_len=len(titulo or ""),
                       meta_description=desc,
                       meta_description_len=len(desc or "")))
    if h1 is not None:
        s.add(Heading(url_id=u.id, tag="h1", position=0, text=h1))
    s.flush()
    return u


def _tipos(s, j, tipo):
    return [i.url_id for i in s.query(Issue).filter(
        Issue.job_id == j.id, Issue.issue_type == tipo).all()]


def test_el_titulo_duplicado_solo_cuenta_entre_indexables():
    s, j = _montar()
    a = _url(s, j, "/a", titulo="Zapatillas de running")
    _url(s, j, "/a?utm_source=nl", titulo="Zapatillas de running", indexable=False)
    SEOAnalyzer(s, j.id).analyze_titles()
    s.flush()
    assert _tipos(s, j, "title_duplicate") == []
    # Con dos indexables si salta, y en las dos.
    b = _url(s, j, "/b", titulo="Zapatillas de running")
    SEOAnalyzer(s, j.id).analyze_titles()
    s.flush()
    assert sorted(set(_tipos(s, j, "title_duplicate"))) == sorted([a.id, b.id])


def test_la_description_duplicada_solo_cuenta_entre_indexables():
    s, j = _montar()
    texto = "La mejor tienda de material deportivo con envio en 24 horas y garantia."
    _url(s, j, "/a", titulo="A", desc=texto)
    _url(s, j, "/orden-precio", titulo="B", desc=texto, indexable=False)
    SEOAnalyzer(s, j.id).analyze_descriptions()
    s.flush()
    assert _tipos(s, j, "description_duplicate") == []


def test_el_h1_duplicado_solo_cuenta_entre_indexables():
    s, j = _montar()
    _url(s, j, "/a", h1="Ofertas de verano")
    _url(s, j, "/page/2", h1="Ofertas de verano", indexable=False)
    SEOAnalyzer(s, j.id).analyze_headings()
    s.flush()
    assert _tipos(s, j, "h1_duplicate") == []


def test_el_duplicado_exacto_va_por_contenido_no_por_bytes():
    """Mismo contenido, bytes distintos (otro token CSRF): es duplicado."""
    s, j = _montar()
    a = _url(s, j, "/a", content_hash="abc", body_hash="bytes-1")
    b = _url(s, j, "/b", content_hash="abc", body_hash="bytes-2")
    SEOAnalyzer(s, j.id).analyze_duplicates()
    s.flush()
    assert sorted(_tipos(s, j, "duplicate_content")) == sorted([a.id, b.id])


def test_una_pagina_no_indexable_no_entra_en_el_grupo():
    s, j = _montar()
    _url(s, j, "/a", content_hash="abc")
    _url(s, j, "/a?utm_source=nl", content_hash="abc", indexable=False)
    SEOAnalyzer(s, j.id).analyze_duplicates()
    s.flush()
    assert _tipos(s, j, "duplicate_content") == []


def test_sin_content_hash_se_cae_a_los_bytes():
    """Rastreos anteriores a la columna: se usa lo que hay."""
    s, j = _montar()
    a = _url(s, j, "/a", body_hash="iguales")
    b = _url(s, j, "/b", body_hash="iguales")
    SEOAnalyzer(s, j.id).analyze_duplicates()
    s.flush()
    assert sorted(_tipos(s, j, "duplicate_content")) == sorted([a.id, b.id])
