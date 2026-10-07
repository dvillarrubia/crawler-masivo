"""Contenido escaso: se mide el contenido propio y solo en paginas indexables.

Criterio SEO de cada decision (medido en dos censos reales):

- Google trata el menu, el megamenu y el pie como boilerplate: son la misma
  plantilla en todas las paginas y no cuentan como contenido de ninguna.
  Midiendo el body entero salian 997 paginas escasas de 29.808; midiendo el
  contenido principal, 10.433. Las 9.436 de diferencia son fichas y archivos
  con dos frases propias que el menu disfrazaba de pagina completa.
- Que una pagina noindex o canonicalizada tenga poco texto no decide nada:
  Google no la va a posicionar. De esas 10.433, solo 4.736 son indexables.
- El ratio texto/HTML ya no genera issue: ver `analyze_content`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[Job.__table__, Url.__table__, Issue.__table__]
    )
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, *, body, contenido, indexable=True, ratio=1.0):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta,
            is_internal=True, is_html=True, status_code=200,
            word_count=body, content_word_count=contenido,
            text_ratio=ratio, indexable=indexable)
    s.add(u)
    s.flush()
    return u


def _tipos(s, job):
    return [i.issue_type for i in s.query(Issue).filter(Issue.job_id == job.id).all()]


def test_el_menu_no_tapa_una_pagina_escasa():
    """420 palabras de plantilla y 30 propias: es contenido escaso."""
    s, j = _montar()
    u = _url(s, j, "/ficha", body=450, contenido=30)
    SEOAnalyzer(s, j.id).analyze_content()
    s.flush()
    assert _tipos(s, j) == ["low_word_count"]
    issue = s.query(Issue).filter(Issue.url_id == u.id).one()
    # El detalle lleva las dos cifras: sin la del body no se entiende por que
    # salta una pagina que "tiene 450 palabras".
    assert issue.details["content_word_count"] == 30
    assert issue.details["body_word_count"] == 450


def test_una_pagina_con_contenido_de_sobra_no_salta():
    s, j = _montar()
    _url(s, j, "/articulo", body=1200, contenido=900)
    SEOAnalyzer(s, j.id).analyze_content()
    s.flush()
    assert _tipos(s, j) == []


def test_las_no_indexables_no_se_avisan():
    """Noindex o canonicalizada: Google no la posiciona, su texto no decide."""
    s, j = _montar()
    _url(s, j, "/tag/algo", body=400, contenido=12, indexable=False)
    SEOAnalyzer(s, j.id).analyze_content()
    s.flush()
    assert _tipos(s, j) == []


def test_sin_la_columna_se_cae_al_recuento_del_body():
    """Rastreos anteriores a `content_word_count`: se usa lo que hay."""
    s, j = _montar()
    _url(s, j, "/vieja", body=40, contenido=None)
    SEOAnalyzer(s, j.id).analyze_content()
    s.flush()
    assert _tipos(s, j) == ["low_word_count"]
    assert s.query(Issue).one().details == {"word_count": 40}


def test_el_ratio_de_texto_ya_no_genera_issue():
    """Salta en el 97% de las paginas de un sitio moderno: era ruido puro."""
    s, j = _montar()
    _url(s, j, "/home", body=3000, contenido=2000, ratio=0.9)
    SEOAnalyzer(s, j.id).analyze_content()
    s.flush()
    assert _tipos(s, j) == []
