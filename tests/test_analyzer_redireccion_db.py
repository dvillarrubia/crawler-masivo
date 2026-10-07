"""Una pagina a la que solo se llega por un salto no puede quedarse sin aviso.

Excluir los destinos de redireccion de `orphan_page` (decision 36) evita
huerfanas falsas tras una migracion, pero dejaba sin NINGUN aviso a una pagina
indexable, con 200 y cero enlaces internos, alcanzable solo por un 301. Medido:
522 asi en www.uoc.edu —363 de ellas en el sitemap—, 109 en blogs.uoc.edu y 1 en
progym, y ninguna tenia un solo issue.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Issue, Job, Link, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[Job.__table__, Url.__table__, Issue.__table__, Link.__table__],
    )
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, **kw):
    datos = dict(
        job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
        is_html=True, status_code=200, indexable=True, inlinks_count=0, crawl_depth=1,
    )
    datos.update(kw)
    u = Url(**datos)
    s.add(u)
    s.flush()
    return u


def _issues(s, j, tipo):
    return s.execute(
        select(Issue.url_id, Issue.severity, Issue.details)
        .where(Issue.job_id == j.id, Issue.issue_type == tipo)
    ).all()


def test_el_destino_de_un_salto_sin_enlaces_recibe_su_aviso():
    s, j = _montar()
    destino = _url(s, j, "/nueva", in_sitemap=True)
    _url(s, j, "/vieja", status_code=301, indexable=False, is_html=False,
         redirect_url="https://x.com/nueva", inlinks_count=5)
    s.commit()

    SEOAnalyzer(s, j.id).analyze_links()
    s.commit()

    avisos = _issues(s, j, "solo_enlazada_por_redireccion")
    assert [a[0] for a in avisos] == [destino.id]
    assert avisos[0][1] == "warning"          # esta en el sitemap
    assert avisos[0][2]["saltos_que_apuntan"] == 1
    assert avisos[0][2]["ejemplo_origen"] == "https://x.com/vieja"
    # Y no se le pone ademas la de huerfana: es alcanzable (decision 36).
    assert _issues(s, j, "orphan_page") == []


def test_fuera_del_sitemap_es_informativo():
    s, j = _montar()
    destino = _url(s, j, "/nueva", in_sitemap=False)
    _url(s, j, "/vieja", status_code=301, indexable=False, is_html=False,
         redirect_url="https://x.com/nueva")
    s.commit()
    SEOAnalyzer(s, j.id).analyze_links()
    s.commit()
    avisos = _issues(s, j, "solo_enlazada_por_redireccion")
    assert [(a[0], a[1]) for a in avisos] == [(destino.id, "info")]


def test_si_tiene_enlaces_directos_no_hay_nada_que_decir():
    """Que ademas sea destino de un salto es normal y no es un problema."""
    s, j = _montar()
    _url(s, j, "/nueva", inlinks_count=12)
    _url(s, j, "/vieja", status_code=301, indexable=False, is_html=False,
         redirect_url="https://x.com/nueva")
    s.commit()
    SEOAnalyzer(s, j.id).analyze_links()
    s.commit()
    assert _issues(s, j, "solo_enlazada_por_redireccion") == []


def test_una_noindex_no_se_reporta():
    """Solo importa entre paginas que Google puede posicionar (decision 47)."""
    s, j = _montar()
    _url(s, j, "/nueva", indexable=False)
    _url(s, j, "/vieja", status_code=301, indexable=False, is_html=False,
         redirect_url="https://x.com/nueva")
    s.commit()
    SEOAnalyzer(s, j.id).analyze_links()
    s.commit()
    assert _issues(s, j, "solo_enlazada_por_redireccion") == []


def test_una_huerfana_de_verdad_sigue_siendo_huerfana():
    """Sin ningun salto apuntandola, el aviso que toca es `orphan_page`."""
    s, j = _montar()
    sola = _url(s, j, "/sola", in_sitemap=False)
    s.commit()
    SEOAnalyzer(s, j.id).analyze_links()
    s.commit()
    assert [a[0] for a in _issues(s, j, "orphan_page")] == [sola.id]
    assert _issues(s, j, "solo_enlazada_por_redireccion") == []


def test_la_semilla_no_se_reporta():
    """Sembrar con `http://` y que el sitio salte a `https://` deja la home como
    destino de un salto y con 0 entrantes descubiertos. Decir de ella que "solo
    se llega por una redireccion" es falso: es la puerta de entrada."""
    s, j = _montar()
    _url(s, j, "/", crawl_depth=0, in_sitemap=True)
    _url(s, j, "/http", status_code=301, indexable=False, is_html=False,
         redirect_url="https://x.com/")
    s.commit()
    SEOAnalyzer(s, j.id).analyze_links()
    s.commit()
    assert _issues(s, j, "solo_enlazada_por_redireccion") == []
