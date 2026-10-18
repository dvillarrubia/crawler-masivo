"""H1 vacio, titulos de relleno, description igual al titulo y enlaces sin
ancla (decision 121, #150 parte B, puntos 1-3).

Todo sale de datos ya guardados: se EJECUTA el analizador contra SQLite
(decision 74), y la funcion pura de `shared/titulos.py` aparte.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Heading, HtmlMeta, Issue, Job, Link, Url
from shared.titulos import (description_igual_title, es_title_generico, forma,
                            marcas_del_host)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Issue.__table__,
        Heading.__table__, Link.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, *, status=200, title=None, description=None, indexable=True,
         noindex=False, **kw):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
            is_html=True, status_code=status, status_group=f"{status // 100}xx",
            host="x.com", path=ruta.split("?")[0], indexable=indexable, noindex=noindex,
            crawl_depth=1, **kw)
    s.add(u)
    s.flush()
    s.add(HtmlMeta(url_id=u.id, title=title, meta_description=description))
    s.flush()
    return u


def _h1(s, u, texto, oculto=False, pos=0):
    s.add(Heading(url_id=u.id, tag="h1", position=pos, text=texto, oculto=oculto))
    s.flush()


def _issues(s, j, tipo):
    return s.execute(
        select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
        .where(Issue.job_id == j.id, Issue.issue_type == tipo).order_by(Issue.url_id)
    ).all()


def _headings(s, j):
    SEOAnalyzer(s, j.id).analyze_headings()
    s.flush()


# -- 1. H1 vacio --------------------------------------------------------------

def test_h1_vacio_y_no_h1_missing():
    s, j = _montar()
    u = _url(s, j, "/a")
    _h1(s, u, None)
    _headings(s, j)
    [fila] = _issues(s, j, "h1_vacio")
    assert fila.url_id == u.id and fila.severity == "warning"
    assert fila.details == {"h1_vacios": 1}
    assert _issues(s, j, "h1_missing") == []


def test_un_h1_bueno_y_uno_vacio_no_es_vacio_ni_multiple():
    s, j = _montar()
    u = _url(s, j, "/a")
    _h1(s, u, "Titular")
    _h1(s, u, None, pos=1)
    _headings(s, j)
    assert _issues(s, j, "h1_vacio") == []
    assert _issues(s, j, "h1_multiple") == []


def test_dos_h1_con_texto_son_h1_multiple():
    s, j = _montar()
    u = _url(s, j, "/a")
    _h1(s, u, "Uno")
    _h1(s, u, "Dos", pos=1)
    _headings(s, j)
    [fila] = _issues(s, j, "h1_multiple")
    assert fila.details == {"count": 2}


def test_solo_vacios_no_es_h1_multiple():
    s, j = _montar()
    u = _url(s, j, "/a")
    _h1(s, u, None)
    _h1(s, u, "   ", pos=1)
    _headings(s, j)
    assert _issues(s, j, "h1_multiple") == []
    assert _issues(s, j, "h1_vacio")[0].details == {"h1_vacios": 2}


def test_un_h1_vacio_oculto_es_h1_missing():
    s, j = _montar()
    u = _url(s, j, "/a")
    _h1(s, u, None, oculto=True)
    _headings(s, j)
    assert [f.url_id for f in _issues(s, j, "h1_missing")] == [u.id]
    assert _issues(s, j, "h1_vacio") == []


def test_un_404_con_h1_vacio_no_da_nada():
    s, j = _montar()
    u = _url(s, j, "/roto", status=404)
    _h1(s, u, None)
    _headings(s, j)
    assert _issues(s, j, "h1_vacio") == []


def test_censo_anterior_a_la_decision_46_no_da_h1_vacio():
    s, j = _montar()
    u = _url(s, j, "/a")
    _h1(s, u, None, oculto=None)
    v = _url(s, j, "/b")
    _h1(s, v, "Titular", oculto=None)
    _headings(s, j)
    assert _issues(s, j, "h1_vacio") == []
    assert _issues(s, j, "h1_missing") == []


# -- 3. Titulo de relleno y description igual al titulo -----------------------

def test_funcion_pura_del_title_generico():
    marcas = marcas_del_host(["Inicio | Marca", "Blog | Marca", "Sin título | Marca",
                              "Contacto | Marca", "Otra cosa"])
    assert marcas == frozenset({"marca"})
    assert not es_title_generico("Inicio | Marca", marcas)
    assert es_title_generico("Sin título | Marca", marcas)
    assert es_title_generico("Untitled document")
    assert es_title_generico("Página sin título")
    assert not es_title_generico("Electrodomésticos")
    assert not es_title_generico("Home")
    # «Título» y «título.» son el mismo texto.
    assert forma("Título") == forma("título.") == "titulo"
    assert description_igual_title("Página de Marca", "pagina  de marca.")
    assert not description_igual_title("Página de Marca y más", "Página de Marca")


def test_inicio_con_la_marca_no_es_generico():
    s, j = _montar()
    _url(s, j, "/", title="Inicio | Marca")
    for i in range(4):
        _url(s, j, f"/p{i}", title=f"Producto número {i} | Marca")
    SEOAnalyzer(s, j.id).analyze_titles()
    s.flush()
    assert _issues(s, j, "title_generico") == []


def test_sin_titulo_en_doce_paginas_es_una_fila_y_sin_duplicados():
    s, j = _montar()
    for i in range(12):
        _url(s, j, f"/p{i:02d}", title="Sin título | Marca")
    for i in range(3):
        _url(s, j, f"/q{i}", title=f"Página buena {i} | Marca")
    SEOAnalyzer(s, j.id).analyze_titles()
    s.flush()
    [fila] = _issues(s, j, "title_generico")
    assert fila.details["paginas_afectadas"] == 12
    assert fila.details["title"] == "Sin título | Marca"
    assert fila.details["paginas_ejemplo"][0] == "https://x.com/p00"
    assert _issues(s, j, "title_duplicate") == []


def test_placeholder_corto_no_es_ademas_title_too_short():
    s, j = _montar()
    u = _url(s, j, "/a", title="Página")
    SEOAnalyzer(s, j.id).analyze_titles()
    s.flush()
    assert [f.url_id for f in _issues(s, j, "title_generico")] == [u.id]
    assert _issues(s, j, "title_too_short") == []


def test_untitled_en_una_noindex_no_da_nada():
    s, j = _montar()
    _url(s, j, "/a", title="Untitled document", indexable=False, noindex=True)
    SEOAnalyzer(s, j.id).analyze_titles()
    s.flush()
    assert _issues(s, j, "title_generico") == []


def test_description_igual_al_titulo_por_plantilla_es_una_fila():
    s, j = _montar()
    for i in range(15):
        _url(s, j, f"/blog/post-{i:02d}", title=f"Post {i} | Marca",
             description=f"Post {i} | Marca")
    _url(s, j, "/blog/otro", title="Otro | Marca", description="Otro | Marca y algo mas")
    SEOAnalyzer(s, j.id).analyze_descriptions()
    s.flush()
    [fila] = _issues(s, j, "description_igual_title")
    assert fila.severity == "info"
    assert fila.details["paginas_afectadas"] == 15
    assert fila.details["plantilla"] == "/blog/:slug (2n)"


def test_description_ausente_no_se_compara():
    s, j = _montar()
    _url(s, j, "/a", title="Algo")
    SEOAnalyzer(s, j.id).analyze_descriptions()
    s.flush()
    assert _issues(s, j, "description_igual_title") == []


# -- 2. Enlace interno sin ancla ----------------------------------------------

def _enlace(s, j, origen, destino_ruta, *, ancla=None, alt=None, veces=1,
            posicion="content", interno=True, tipo="hyperlink"):
    s.add(Link(job_id=j.id, from_url_id=origen.id, to_url=f"https://x.com{destino_ruta}",
               to_url_hash=destino_ruta, is_internal=interno, link_position=posicion,
               follow=True, repeticiones=veces, anchor_text=ancla, alt_text=alt,
               link_type=tipo))
    s.flush()


def _contar(s, j):
    az = SEOAnalyzer(s, j.id)
    az.compute_link_counts()
    az._flush_issues()
    s.flush()


def test_icono_del_pie_en_diez_paginas_es_una_fila_de_plantilla():
    s, j = _montar()
    _url(s, j, "/instagram")
    paginas = [_url(s, j, f"/p{i:02d}") for i in range(10)]
    for p in paginas:
        _enlace(s, j, p, "/instagram", posicion="footer", tipo="image")
    _contar(s, j)
    [fila] = _issues(s, j, "enlace_sin_ancla")
    assert fila.url_id == paginas[0].id
    assert fila.details["alcance"] == "plantilla"
    assert fila.details["paginas_afectadas"] == 10
    assert fila.details["tipo"] == "imagen"
    assert fila.severity == "info"


def test_por_pagina_suma_las_repeticiones():
    s, j = _montar()
    p = _url(s, j, "/p")
    for d in ("/a", "/b", "/c"):
        _url(s, j, d)
    _enlace(s, j, p, "/a")
    _enlace(s, j, p, "/b", veces=2)
    _enlace(s, j, p, "/c", ancla="   ")
    _enlace(s, j, p, "/a", ancla="Con texto")
    _contar(s, j)
    [fila] = _issues(s, j, "enlace_sin_ancla")
    assert fila.url_id == p.id
    assert fila.details["alcance"] == "pagina"
    assert fila.details["enlaces"] == 4
    assert fila.details["tipo"] == "texto_vacio"


def test_enlace_externo_sin_ancla_no_da_nada():
    s, j = _montar()
    p = _url(s, j, "/p")
    _enlace(s, j, p, "/fuera", interno=False)
    _contar(s, j)
    assert _issues(s, j, "enlace_sin_ancla") == []


def test_origen_404_o_noindex_no_da_nada():
    s, j = _montar()
    roto = _url(s, j, "/roto", status=404)
    privada = _url(s, j, "/privada", noindex=True)
    _url(s, j, "/a")
    _enlace(s, j, roto, "/a")
    _enlace(s, j, privada, "/a")
    _contar(s, j)
    assert _issues(s, j, "enlace_sin_ancla") == []


def test_enlace_con_alt_y_sin_ancla_no_cuenta():
    s, j = _montar()
    p = _url(s, j, "/p")
    _url(s, j, "/a")
    _enlace(s, j, p, "/a", alt="Logo", tipo="image")
    _contar(s, j)
    assert _issues(s, j, "enlace_sin_ancla") == []


def test_sin_ancla_no_lee_links_otra_vez(monkeypatch):
    """La unica lectura de `links` es la de la temporal (decisiones 37 y 41):
    se cuentan las sentencias que nombran `links` durante el conteo."""
    from sqlalchemy import event

    s, j = _montar()
    p = _url(s, j, "/p")
    _url(s, j, "/a")
    _enlace(s, j, p, "/a")
    sentencias = []
    engine = s.get_bind()

    def _ver(conn, cursor, statement, *a):
        if " links" in statement.lower():
            sentencias.append(statement)

    event.listen(engine, "before_cursor_execute", _ver)
    try:
        _contar(s, j)
    finally:
        event.remove(engine, "before_cursor_execute", _ver)
    assert len(_issues(s, j, "enlace_sin_ancla")) == 1
    assert len([x for x in sentencias if "from links" in x.lower()
                or "join links" in x.lower()]) == 1, sentencias
