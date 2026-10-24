"""El analisis no afirma ausencias en una carpeta topada (decision 172).

Con un tope por carpeta que se quedo corto, las paginas que enlazaban a una
URL de esa carpeta pueden ser justo las que no se pidieron: que no tenga
entrantes (`orphan_page`, `sitemap_orphan`) o que no se llegue a ella desde la
home no se puede afirmar (decisiones 67 y 70). Fuera de esas carpetas el
analisis dice lo de siempre.

Estos tests EJECUTAN el analizador contra SQLite (decision 74).
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, HtmlMeta, Issue, Job, Link, Url
from shared.topes_carpeta import Presupuesto, compilar


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


CONFIG = {"topes_por_carpeta": {"*": 2}}


def _resumen_cortado():
    """Lo que escribiria el spider: /blog/ lleno con 3 sin pedir, /docs/ holgado."""
    p = Presupuesto(compilar(CONFIG["topes_por_carpeta"]))
    for i in range(5):
        p.admitir(f"https://x.com/blog/{i}", i)
    p.admitir("https://x.com/docs/0", 100)
    return p.resumen()


def _montar(topes):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[Job.__table__, Url.__table__, Issue.__table__, Link.__table__,
                HtmlMeta.__table__],
    )
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config=CONFIG, topes_carpeta=topes,
            finish_reason="tope_por_carpeta" if topes else "finished")
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, **kw):
    datos = dict(
        job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
        is_html=True, status_code=200, indexable=True, inlinks_count=0, crawl_depth=1,
        in_sitemap=False,
    )
    datos.update(kw)
    u = Url(**datos)
    s.add(u)
    s.flush()
    return u


def _ids(s, j, tipo):
    return {r for (r,) in s.execute(
        select(Issue.url_id).where(Issue.job_id == j.id, Issue.issue_type == tipo))}


def test_la_huerfana_de_una_carpeta_topada_no_se_afirma():
    s, j = _montar(_resumen_cortado())
    en_blog = _url(s, j, "/blog/0")
    en_docs = _url(s, j, "/docs/0")
    suelta = _url(s, j, "/contacto")
    s.commit()
    SEOAnalyzer(s, j.id).analyze_links()
    s.commit()
    huerfanas = _ids(s, j, "orphan_page")
    assert en_blog.id not in huerfanas
    # Fuera de la carpeta cortada se afirma como siempre.
    assert {en_docs.id, suelta.id} <= huerfanas


def test_sitemap_orphan_tampoco_en_la_carpeta_topada():
    s, j = _montar(_resumen_cortado())
    en_blog = _url(s, j, "/blog/1", in_sitemap=True)
    en_docs = _url(s, j, "/docs/0", in_sitemap=True)
    s.commit()
    SEOAnalyzer(s, j.id).analyze_sitemap()
    s.commit()
    assert _ids(s, j, "sitemap_orphan") == {en_docs.id}
    assert en_blog.id not in _ids(s, j, "sitemap_orphan")


def test_sin_corte_la_carpeta_se_analiza_entera():
    s, j = _montar(None)
    en_blog = _url(s, j, "/blog/0")
    s.commit()
    SEOAnalyzer(s, j.id).analyze_links()
    s.commit()
    assert _ids(s, j, "orphan_page") == {en_blog.id}
