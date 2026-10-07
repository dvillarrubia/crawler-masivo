"""El titulo y la descripcion se cortan por PIXELES, no por caracteres.

Google trunca el titulo del resultado hacia los 580 px y el fragmento hacia los
985: un titulo de 65 letras estrechas cabe y uno de 55 en mayusculas no. Medido
en tres censos, 8.801 titulos pasaban de 60 caracteres SIN pasar del ancho en
pixeles —o sea sin truncarse en el resultado—, el 29% de los avisos de titulo;
al contrario solo pasaba en 3 paginas de mas de 80.000.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, HtmlMeta, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, *, titulo=None, px=None, desc=None, desc_px=None):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
            is_html=True, status_code=200, indexable=True)
    s.add(u)
    s.flush()
    s.add(HtmlMeta(url_id=u.id, title=titulo,
                   title_len=len(titulo or "") or None, title_pixel_width=px,
                   meta_description=desc,
                   meta_description_len=len(desc or "") or None,
                   meta_description_pixel_width=desc_px))
    s.flush()
    return u


def _tipos(s, j):
    return sorted(i.issue_type for i in s.query(Issue).filter(Issue.job_id == j.id).all())


def test_un_titulo_largo_que_cabe_no_se_avisa():
    """65 caracteres estrechos que miden 520 px: en el resultado no se corta."""
    s, j = _montar()
    _url(s, j, "/a", titulo="i" * 65, px=520)
    SEOAnalyzer(s, j.id).analyze_titles()
    s.flush()
    assert _tipos(s, j) == []


def test_un_titulo_corto_que_no_cabe_si_se_avisa():
    """45 caracteres en mayusculas que miden 620 px: se corta."""
    s, j = _montar()
    u = _url(s, j, "/a", titulo="W" * 45, px=620)
    SEOAnalyzer(s, j.id).analyze_titles()
    s.flush()
    assert _tipos(s, j) == ["title_too_long"]
    detalle = s.query(Issue).filter(Issue.url_id == u.id).one().details
    assert detalle["pixels"] == 620
    assert detalle["max_pixels"] == 580


def test_sin_ancho_medido_se_cae_a_los_caracteres():
    """Rastreos anteriores a la columna: se usa lo que hay."""
    s, j = _montar()
    _url(s, j, "/a", titulo="x" * 80, px=None)
    SEOAnalyzer(s, j.id).analyze_titles()
    s.flush()
    assert _tipos(s, j) == ["title_too_long"]


def test_la_descripcion_tambien_va_por_pixeles():
    s, j = _montar()
    _url(s, j, "/a", titulo="Un titulo correcto", px=300,
         desc="d" * 200, desc_px=900)
    SEOAnalyzer(s, j.id).analyze_descriptions()
    s.flush()
    assert _tipos(s, j) == []
    s2, j2 = _montar()
    _url(s2, j2, "/b", titulo="Un titulo correcto", px=300,
         desc="D" * 150, desc_px=1200)
    SEOAnalyzer(s2, j2.id).analyze_descriptions()
    s2.flush()
    assert _tipos(s2, j2) == ["description_too_long"]
