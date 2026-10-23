"""`encabezado_salta_nivel` EJECUTADO contra SQLite (decisiones 153 y 74)."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Heading, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _censo():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[Job.__table__, Url.__table__, Issue.__table__, Heading.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _pagina(s, j, ruta, titulares, *, status=200, indexable=True, oculto=False):
    """`titulares`: lista de etiquetas, o de (etiqueta, texto, oculto)."""
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com",
            is_internal=True, is_html=True, status_code=status, indexable=indexable)
    s.add(u)
    s.flush()
    for i, t in enumerate(titulares):
        tag, texto, oc = (t, "Texto", oculto) if isinstance(t, str) else t
        s.add(Heading(url_id=u.id, tag=tag, position=i, text=texto, oculto=oc))
    s.flush()
    return u


def _avisos(s, j):
    SEOAnalyzer(s, j.id).analyze_saltos_de_encabezado()
    return s.execute(
        select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
        .where(Issue.job_id == j.id, Issue.issue_type == "encabezado_salta_nivel")
    ).all()


def test_un_salto_por_plantilla_anclado_en_la_url_menor():
    s, j = _censo()
    ids = {}
    for slug in ("zeta", "alfa", "beta"):
        ids[slug] = _pagina(s, j, f"/blog/{slug}", ["h1", "h2", "h4", "h2", "h3"]).id
    _pagina(s, j, "/tienda/x", ["h1", "h3"])
    filas = _avisos(s, j)
    assert len(filas) == 2
    blog = next(f for f in filas if f.details["plantilla"].startswith("/blog"))
    assert blog.url_id == ids["alfa"]
    assert blog.severity == "info"
    assert blog.details["salto"] == "h2>h4"
    assert blog.details["paginas_afectadas"] == 3
    assert blog.details["paginas_ejemplo"][0] == "https://x.com/blog/alfa"
    assert blog.group_key
    tienda = next(f for f in filas if f is not blog)
    assert tienda.details["salto"] == "h1>h3"


def test_lo_que_no_es_un_salto():
    s, j = _censo()
    # Bajar de uno en uno y subir de golpe (h4 -> h2) es correcto.
    _pagina(s, j, "/a", ["h1", "h2", "h3", "h4", "h2", "h3"])
    # El primer titular no se compara con nada: sin h1 es h1_missing.
    _pagina(s, j, "/b", ["h3", "h4"])
    # El titular oculto o vacio no lo oye nadie: no hace salto.
    _pagina(s, j, "/e", ["h1", ("h4", "oculto", True), ("h2", "Visible", False)])
    _pagina(s, j, "/f", ["h1", ("h5", "  ", False), ("h2", "Visible", False)])
    # Ni en paginas que no cuentan: no indexable, 404.
    _pagina(s, j, "/noindex", ["h1", "h4"], indexable=False)
    _pagina(s, j, "/roto", ["h1", "h4"], status=404)
    assert _avisos(s, j) == []


def test_el_oculto_y_el_vacio_no_tapan_el_salto_de_los_visibles():
    # Lo que se oye es h1 -> h4: el h2 vacio y el h3 oculto no existen para
    # quien navega por titulares.
    s, j = _censo()
    _pagina(s, j, "/a", ["h1", ("h2", "", False), ("h3", "x", True), ("h4", "Visible", False)])
    filas = _avisos(s, j)
    assert len(filas) == 1 and filas[0].details["salto"] == "h1>h4"


def test_salto_mas_frecuente_y_los_distintos():
    s, j = _censo()
    _pagina(s, j, "/p/1", ["h1", "h3"])
    _pagina(s, j, "/p/2", ["h2", "h4"])
    _pagina(s, j, "/p/3", ["h2", "h4"])
    filas = _avisos(s, j)
    assert len(filas) == 1
    assert filas[0].details["salto"] == "h2>h4"
    assert filas[0].details["saltos_distintos"] == ["h1>h3", "h2>h4"]


def test_censo_anterior_a_la_decision_46_no_se_evalua():
    s, j = _censo()
    _pagina(s, j, "/a", [("h1", "T", None), ("h4", "T", None)])
    assert _avisos(s, j) == []
