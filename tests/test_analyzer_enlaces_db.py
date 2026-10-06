"""Conteos de enlaces contra SQLite: el megamenu no es un problema de la pagina.

Dos criterios SEO, uno por test:

- `high_outlink_count` pregunta si la pagina diluye su presupuesto de rastreo y
  su autoridad entre demasiados enlaces PROPIOS. Un menu de cientos de entradas
  repetido en todo el sitio es un hecho de la plantilla, igual en todas las
  paginas: no dice nada de ninguna. Medido en blogs.uoc.edu, contarlo avisaba
  en 7.011 paginas (el 24% de las HTML) y contando solo contenido, en 51.
- `inlinks_count` pregunta si la pagina esta enlazada desde el sitio. Un enlace
  de una pagina a si misma no responde a eso (62.326 de 2.342.192 inlinks del
  mismo censo), y los `nofollow` si cuentan: la pagina esta enlazada, solo que
  sin respaldo. La autoridad se mira en el PageRank, que lleva su propio grafo.
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
        engine, tables=[Job.__table__, Url.__table__, Issue.__table__, Link.__table__]
    )
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, status=200):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta,
            is_internal=True, is_html=True, status_code=status)
    s.add(u)
    s.flush()
    return u


def _enlace(s, j, origen, destino_ruta, posicion="content", follow=True):
    s.add(Link(job_id=j.id, from_url_id=origen.id, to_url=f"https://x.com{destino_ruta}",
               to_url_hash=destino_ruta, is_internal=True, link_position=posicion,
               follow=follow))


def test_el_megamenu_no_dispara_high_outlink():
    s, j = _montar()
    plantilla = _url(s, j, "/plantilla")
    editorial = _url(s, j, "/editorial")
    # 150 enlaces de menu, como cualquier pagina de un e-commerce
    for i in range(150):
        _enlace(s, j, plantilla, f"/cat/{i}", posicion="nav")
    # 150 enlaces editoriales de verdad en el cuerpo
    for i in range(150):
        _enlace(s, j, editorial, f"/post/{i}", posicion="content")
    s.commit()

    analizador = SEOAnalyzer(s, j.id)
    analizador.max_outlinks = 100
    analizador.analyze_links()
    s.commit()

    marcadas = s.execute(
        select(Issue.url_id).where(Issue.issue_type == "high_outlink_count")
    ).scalars().all()
    assert marcadas == [editorial.id]


def test_el_mismo_destino_repetido_no_cuenta_dos_veces():
    s, j = _montar()
    u = _url(s, j, "/con-repes")
    for i in range(80):
        _enlace(s, j, u, f"/p/{i}")
        _enlace(s, j, u, f"/p/{i}")  # el mismo destino, dos veces en el cuerpo
    s.commit()

    analizador = SEOAnalyzer(s, j.id)
    analizador.max_outlinks = 100
    analizador.analyze_links()
    s.commit()

    assert s.execute(select(Issue.id).where(Issue.issue_type == "high_outlink_count")).first() is None


def test_los_nofollow_no_diluyen_nada():
    s, j = _montar()
    u = _url(s, j, "/faceta")
    for i in range(150):
        _enlace(s, j, u, f"/filtro/{i}", follow=False)
    s.commit()

    analizador = SEOAnalyzer(s, j.id)
    analizador.max_outlinks = 100
    analizador.analyze_links()
    s.commit()

    assert s.execute(select(Issue.id).where(Issue.issue_type == "high_outlink_count")).first() is None


def test_el_autoenlace_no_cuenta_como_inlink():
    s, j = _montar()
    a = _url(s, j, "/a")
    b = _url(s, j, "/b")
    _enlace(s, j, a, "/a")      # se enlaza a si misma (migas, logo)
    _enlace(s, j, b, "/a")      # un inlink de verdad
    s.commit()

    SEOAnalyzer(s, j.id).compute_link_counts()
    s.commit()
    s.refresh(a)
    assert a.inlinks_count == 1
    assert a.unique_inlinks_count == 1


def test_un_nofollow_sigue_contando_como_enlazada():
    s, j = _montar()
    a = _url(s, j, "/a")
    b = _url(s, j, "/b")
    _enlace(s, j, b, "/a", follow=False)
    s.commit()

    SEOAnalyzer(s, j.id).compute_link_counts()
    s.commit()
    s.refresh(a)
    # Esta enlazada —no es huerfana— aunque el enlace no reparta autoridad.
    assert a.inlinks_count == 1


def test_los_conteos_viejos_no_sobreviven_a_un_reanalisis():
    s, j = _montar()
    a = _url(s, j, "/a")
    b = _url(s, j, "/b")
    _enlace(s, j, b, "/a")
    s.commit()
    SEOAnalyzer(s, j.id).compute_link_counts()
    s.commit()
    s.refresh(a)
    assert a.inlinks_count == 1

    # Se rehace el rastreo y /a pierde su unico inlink
    s.query(Link).delete()
    s.commit()
    SEOAnalyzer(s, j.id).compute_link_counts()
    s.commit()
    s.refresh(a)
    assert a.inlinks_count == 0, "conteo rancio: la pagina ya no tiene inlinks"
    assert a.unique_inlinks_count == 0


def test_el_filtro_del_job_va_en_links_no_en_urls():
    """La tabla de enlaces guarda los de TODOS los rastreos.

    Poner el job en el lado de `urls` (uniendo) deja el filtro fuera del
    alcance del indice y Postgres recorre la tabla entera: medido con EXPLAIN
    sobre una instalacion con 46 GB y 181 millones de filas, coste 6.189.456
    frente a 122.247 filtrando por `links.job_id`. En tiempo real,
    `analyze_links` paso de minutos a 1,7 s (blogs) y 11,2 s (uoc).

    Se comprueba sobre el SQL generado porque en SQLite no hay plan que medir.
    """
    import re

    s, j = _montar()
    analizador = SEOAnalyzer(s, j.id)
    analizador.max_outlinks = 100

    # El `select` de los enlaces de contenido no puede unir con `urls`.
    import inspect

    fuente = inspect.getsource(analizador.analyze_links)
    bloque = fuente.split("enlaces_de_contenido")[1].split(")\n\n")[0]
    assert "Link.job_id == self.job_id" in bloque, "el filtro tiene que ir en links"
    assert not re.search(r"\.join\(\s*Url", bloque), (
        "unir con urls para filtrar por job recorre la tabla de enlaces entera"
    )
