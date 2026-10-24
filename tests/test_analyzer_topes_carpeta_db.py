"""El analisis no afirma ausencias en un censo topado (decision 172).

Con un tope por carpeta que se quedo corto, las paginas que no se pidieron
pueden ser justo las que enlazaban a una URL, de esa carpeta o de cualquier
otra (un post de /blog/ sin pedir puede ser el unico que enlaza a
/producto/x): que no tenga entrantes (`orphan_page`, `sitemap_orphan`) o que no
se llegue a ella desde la home no se puede afirmar en NINGUNA parte del censo
(decisiones 67 y 70). Sin corte, el analisis dice lo de siempre.

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


def test_ninguna_huerfana_se_afirma_en_un_censo_topado():
    s, j = _montar(_resumen_cortado())
    _url(s, j, "/blog/0")
    # Fuera de la carpeta cortada tampoco: lo que no se pidio de /blog/ puede
    # ser lo que enlazaba aqui.
    _url(s, j, "/docs/0")
    _url(s, j, "/contacto")
    s.commit()
    SEOAnalyzer(s, j.id).analyze_links()
    s.commit()
    assert _ids(s, j, "orphan_page") == set()


def test_sitemap_orphan_tampoco_fuera_de_la_carpeta_topada():
    """El caso de la revision: /blog/2 no se pidio y era la que enlazaba a
    /producto/x, que esta en el sitemap sin entrantes."""
    p = Presupuesto(compilar({"/blog/": 1}))
    p.admitir("https://x.com/blog/1", 1)
    assert not p.admitir("https://x.com/blog/2", 2)
    s, j = _montar(p.resumen())
    j.config = {"topes_por_carpeta": {"/blog/": 1}}
    en_blog = _url(s, j, "/blog/1", in_sitemap=True)
    producto = _url(s, j, "/producto/x", in_sitemap=True)
    s.commit()
    a = SEOAnalyzer(s, j.id)
    a.analyze_links()
    a.analyze_sitemap()
    s.commit()
    assert not ({en_blog.id, producto.id} & (_ids(s, j, "sitemap_orphan")
                                             | _ids(s, j, "orphan_page")))


def test_sin_corte_la_carpeta_se_analiza_entera():
    s, j = _montar(None)
    en_blog = _url(s, j, "/blog/0")
    en_docs = _url(s, j, "/docs/0", in_sitemap=True)
    s.commit()
    a = SEOAnalyzer(s, j.id)
    a.analyze_links()
    a.analyze_sitemap()
    s.commit()
    assert _ids(s, j, "orphan_page") == {en_blog.id}
    assert _ids(s, j, "sitemap_orphan") == {en_docs.id}


def test_con_topes_que_no_cortan_se_afirma_como_siempre():
    p = Presupuesto(compilar(CONFIG["topes_por_carpeta"]))
    p.admitir("https://x.com/blog/0", 1)
    s, j = _montar(p.resumen())
    j.finish_reason = "finished"
    en_blog = _url(s, j, "/blog/0")
    s.commit()
    SEOAnalyzer(s, j.id).analyze_links()
    s.commit()
    assert _ids(s, j, "orphan_page") == {en_blog.id}
