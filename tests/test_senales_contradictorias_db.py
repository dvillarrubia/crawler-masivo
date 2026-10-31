"""Senales que se contradicen y redirecciones que no son lo que parecen
(decision 120, #150 parte A).

- El canonical que solo va en la cabecera `Link` daba `canonical_missing` y su
  destino no se comprobaba (B1).
- El canonical del HTML distinto del de la cabecera no se veia (4a).
- `noindex` con un canonical a otra solo daba `noindex_page` (4b).
- Una redireccion que acaba en una noindex no avisaba (5).
- Una meta refresh quedaba `indexable=true` (B2), no avisaba (7), y
  `_Fichas.seguir_redirecciones` no la seguia (B6).

Todos EJECUTAN el analizador contra SQLite (decision 74).
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import ESTADO_META_REFRESH, SEOAnalyzer, _Fichas
from shared.meta_refresh import retardo_meta_refresh
from shared.models import Base, HtmlMeta, Issue, Job, Url
from shared.respuestas import CHALLENGE_WAF


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, *, status=200, canonical=None, cabecera=None, noindex=False,
         sin_meta=False, interna=True, meta_refresh=None, **kw):
    url = ruta if ruta.startswith("http") else f"https://x.com{ruta}"
    partes = url.split("/", 3)
    datos = dict(
        job_id=j.id, url=url, url_hash=url, is_internal=interna,
        is_html=not sin_meta, status_code=status, host=partes[2],
        path="/" + (partes[3] if len(partes) > 3 else ""),
        resource_type="html", noindex=noindex, crawl_depth=1,
    )
    datos.update(kw)
    u = Url(**datos)
    s.add(u)
    s.flush()
    if not sin_meta:
        s.add(HtmlMeta(url_id=u.id, canonical_href=canonical, canonical_header=cabecera,
                       canonical_count=1 if canonical else 0,
                       meta_robots="noindex" if noindex else None,
                       meta_refresh=meta_refresh))
        s.flush()
    return u


def _abs(ruta):
    return ruta if ruta.startswith("http") else f"https://x.com{ruta}"


def _salto(s, j, ruta, destino, status=301, **kw):
    return _url(s, j, ruta, status=status, sin_meta=True, redirect_url=_abs(destino), **kw)


def _refresh(s, j, ruta, destino, content="0; url=/x", **kw):
    """Una pagina 200 que redirige con meta refresh, como la guarda el spider
    sin render: con su html_meta, el estado y el destino."""
    return _url(s, j, ruta, meta_refresh=content, redirect_url=_abs(destino),
                indexability_status=ESTADO_META_REFRESH, **kw)


def _issues(s, j, tipo=None, url_id=None):
    q = select(Issue.url_id, Issue.issue_type, Issue.severity, Issue.details,
               Issue.group_key).where(Issue.job_id == j.id)
    if tipo is not None:
        q = q.where(Issue.issue_type == tipo)
    if url_id is not None:
        q = q.where(Issue.url_id == url_id)
    return s.execute(q.order_by(Issue.url_id, Issue.issue_type)).all()


def _tipos(s, j, url_id):
    return sorted(i.issue_type for i in _issues(s, j, url_id=url_id))


def _canonicals(s, j):
    SEOAnalyzer(s, j.id).analyze_canonicals()
    s.flush()


def _cadenas(s, j):
    SEOAnalyzer(s, j.id).analyze_redirect_chains()
    s.flush()


# -- B1: canonical solo en la cabecera ----------------------------------------

def test_canonical_solo_en_cabecera_no_es_canonical_missing_y_se_comprueba():
    s, j = _montar()
    a = _url(s, j, "/a", cabecera="https://x.com/b")
    _url(s, j, "/b", status=404, sin_meta=True)
    _canonicals(s, j)
    tipos = _tipos(s, j, a.id)
    assert "canonical_missing" not in tipos
    [fila] = _issues(s, j, "canonical_a_error", a.id)
    assert fila.details["fuente"] == "cabecera"


def test_canonical_de_cabecera_a_otro_dominio_dice_la_fuente():
    s, j = _montar()
    a = _url(s, j, "/a", cabecera="https://otro.com/a")
    _canonicals(s, j)
    [fila] = _issues(s, j, "canonical_cross_domain", a.id)
    assert fila.details["fuente"] == "cabecera"


def test_sin_canonical_en_ninguna_parte_sigue_siendo_canonical_missing():
    s, j = _montar()
    a = _url(s, j, "/a")
    _canonicals(s, j)
    assert _tipos(s, j, a.id) == ["canonical_missing"]


# -- 4a: HTML frente a cabecera -----------------------------------------------

def test_html_y_cabecera_distintos():
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/a", cabecera="https://x.com/b")
    _canonicals(s, j)
    [fila] = _issues(s, j, "canonical_html_y_cabecera_distintos")
    assert fila.url_id == a.id and fila.severity == "warning"
    assert fila.details["canonical_html"] == "https://x.com/a"
    assert fila.details["canonical_cabecera"] == "https://x.com/b"


def test_html_y_cabecera_con_distinta_barra_final_si_difieren():
    s, j = _montar()
    _url(s, j, "/a", canonical="https://x.com/a", cabecera="https://x.com/a/")
    _canonicals(s, j)
    assert len(_issues(s, j, "canonical_html_y_cabecera_distintos")) == 1


def test_html_y_cabecera_con_otro_orden_de_parametros_no_difieren():
    s, j = _montar()
    _url(s, j, "/a?x=1&y=2", canonical="https://x.com/a?x=1&y=2",
         cabecera="https://x.com/a?y=2&x=1")
    _canonicals(s, j)
    assert _issues(s, j, "canonical_html_y_cabecera_distintos") == []


def test_html_y_cabecera_en_todo_el_host_es_una_fila():
    s, j = _montar()
    for i in range(12):
        _url(s, j, f"/p{i:02d}", canonical=f"https://x.com/p{i:02d}",
             cabecera=f"https://x.com/p{i:02d}/")
    _canonicals(s, j)
    [fila] = _issues(s, j, "canonical_html_y_cabecera_distintos")
    assert fila.details["alcance"] == "host"
    assert fila.details["paginas_afectadas"] == 12
    assert fila.details["paginas_ejemplo"][0] == "https://x.com/p00"


def test_html_y_cabecera_en_un_muro_no_se_evalua():
    s, j = _montar()
    _url(s, j, "/a", canonical="https://x.com/a", cabecera="https://x.com/b",
         response_class=CHALLENGE_WAF)
    _canonicals(s, j)
    assert _issues(s, j) == []


# -- 4b: noindex con canonical a otra -----------------------------------------

def _indexabilidad_y_canonicals(s, j):
    az = SEOAnalyzer(s, j.id)
    az.analyze_indexability()
    az.analyze_canonicals()
    s.flush()


def test_noindex_con_canonical_a_otra_es_un_solo_aviso():
    s, j = _montar()
    a = _url(s, j, "/f/rojo", canonical="https://x.com/f", noindex=True)
    _url(s, j, "/f", canonical="https://x.com/f")
    _indexabilidad_y_canonicals(s, j)
    tipos = _tipos(s, j, a.id)
    assert tipos == ["noindex_y_canonical_a_otra"], tipos
    [fila] = _issues(s, j, "noindex_y_canonical_a_otra")
    assert fila.severity == "info"
    assert fila.details["destino_indexable"] is True
    assert fila.details["paginas_afectadas"] == 1


def test_noindex_con_canonical_a_una_noindex_no_es_contradiccion():
    s, j = _montar()
    a = _url(s, j, "/f/rojo", canonical="https://x.com/f", noindex=True)
    _url(s, j, "/f", canonical="https://x.com/f", noindex=True)
    _indexabilidad_y_canonicals(s, j)
    assert _tipos(s, j, a.id) == ["noindex_page"]
    assert _issues(s, j, "noindex_y_canonical_a_otra") == []


def test_noindex_con_canonical_a_un_destino_sin_rastrear_no_afirma_su_estado():
    s, j = _montar()
    _url(s, j, "/f/rojo", canonical="https://x.com/no-rastreada", noindex=True)
    _indexabilidad_y_canonicals(s, j)
    [fila] = _issues(s, j, "noindex_y_canonical_a_otra")
    assert fila.details["destino_indexable"] is None


def test_noindex_con_canonical_por_plantilla_es_una_fila():
    s, j = _montar()
    _url(s, j, "/f", canonical="https://x.com/f")
    ids = [_url(s, j, f"/cat/{c}", canonical="https://x.com/f", noindex=True).id
           for c in ("azul", "rojo", "verde")]
    _indexabilidad_y_canonicals(s, j)
    [fila] = _issues(s, j, "noindex_y_canonical_a_otra")
    assert fila.details["paginas_afectadas"] == 3
    assert fila.url_id == ids[0]  # la URL menor: /cat/azul
    # Ninguna de las tres conserva su `noindex_page`.
    assert [i for i in _issues(s, j, "noindex_page") if i.url_id in ids] == []


def test_noindex_autocanonica_sigue_siendo_noindex_page():
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/a", noindex=True)
    _indexabilidad_y_canonicals(s, j)
    assert _tipos(s, j, a.id) == ["noindex_page"]


# -- 5: redireccion que acaba en una noindex ----------------------------------

def test_redireccion_a_noindex_un_salto():
    s, j = _montar()
    v = _salto(s, j, "/viejo", "/noidx")
    _url(s, j, "/noidx", noindex=True)
    _cadenas(s, j)
    [fila] = _issues(s, j, "redireccion_a_noindex")
    assert fila.url_id == v.id and fila.severity == "warning"
    assert fila.details["final"] == "https://x.com/noidx"
    assert fila.details["hops"] == 1


def test_redireccion_a_noindex_dos_saltos_no_es_ademas_cadena():
    s, j = _montar()
    v = _salto(s, j, "/a", "/b")
    _salto(s, j, "/b", "/noidx")
    _url(s, j, "/noidx", noindex=True)
    _cadenas(s, j)
    assert _tipos(s, j, v.id) == ["redireccion_a_noindex"]
    assert _issues(s, j, "redirect_chain") == []


def test_redireccion_de_normalizacion_a_noindex_no_avisa():
    s, j = _montar()
    _salto(s, j, "http://x.com/noidx", "https://x.com/noidx")
    _url(s, j, "/noidx", noindex=True)
    _cadenas(s, j)
    assert _issues(s, j, "redireccion_a_noindex") == []


def test_bucle_no_es_redireccion_a_noindex():
    s, j = _montar()
    _salto(s, j, "/a", "/b")
    _salto(s, j, "/b", "/a")
    _cadenas(s, j)
    assert _issues(s, j, "redireccion_a_noindex") == []
    assert len(_issues(s, j, "redirect_loop")) == 2


def test_destino_sin_rastrear_o_muro_no_avisa():
    s, j = _montar()
    _salto(s, j, "/a", "/fuera")
    _salto(s, j, "/b", "/muro")
    _url(s, j, "/muro", noindex=True, response_class=CHALLENGE_WAF)
    _cadenas(s, j)
    assert _issues(s, j, "redireccion_a_noindex") == []


def test_meta_refresh_hacia_una_noindex():
    s, j = _montar()
    mr = _refresh(s, j, "/mr", "/noidx")
    _url(s, j, "/noidx", noindex=True)
    _cadenas(s, j)
    assert _tipos(s, j, mr.id) == ["redireccion_a_noindex"]


def test_diez_redirecciones_al_mismo_noindex_son_una_fila_en_el_destino():
    s, j = _montar()
    for i in range(10):
        _salto(s, j, f"/cuenta/{i}", "/login")
    login = _url(s, j, "/login", noindex=True)
    _cadenas(s, j)
    [fila] = _issues(s, j, "redireccion_a_noindex")
    assert fila.url_id == login.id
    assert fila.details["alcance"] == "destino"
    assert fila.details["redirecciones_que_llegan"] == 10


# -- 7 y B2: meta refresh -----------------------------------------------------

def test_retardo_de_la_meta_refresh():
    assert retardo_meta_refresh("0; url=/x") == 0
    assert retardo_meta_refresh("5; URL='/dest'") == 5
    assert retardo_meta_refresh("0.5;url=/x") == 0.5
    assert retardo_meta_refresh("1;https://otro.com/") == 1
    assert retardo_meta_refresh("url=/x") is None
    assert retardo_meta_refresh(None) is None


def test_meta_refresh_inmediata_es_info_y_con_retardo_warning():
    s, j = _montar()
    a = _refresh(s, j, "/a", "/dest", content="0; url=/dest")
    b = _refresh(s, j, "/b", "/dest", content="5; url=/dest")
    _url(s, j, "/dest")
    _cadenas(s, j)
    filas = {f.url_id: f for f in _issues(s, j, "meta_refresh_redireccion")}
    assert filas[a.id].severity == "info" and filas[a.id].details["retardo_s"] == 0
    assert filas[b.id].severity == "warning" and filas[b.id].details["retardo_s"] == 5
    assert filas[b.id].details["destino"] == "https://x.com/dest"


def test_refresh_en_noscript_o_recarga_no_avisa():
    """El spider no pone el estado ni el destino en esos casos (B5): el
    `meta_refresh` guardado no basta para afirmar una redireccion."""
    s, j = _montar()
    _url(s, j, "/noscript", meta_refresh="0; url=/x")
    _url(s, j, "/recarga", meta_refresh="300")
    _cadenas(s, j)
    assert _issues(s, j, "meta_refresh_redireccion") == []


def test_meta_refresh_a_un_error_no_se_duplica():
    s, j = _montar()
    a = _refresh(s, j, "/a", "/rota")
    _url(s, j, "/rota", status=404, sin_meta=True)
    _cadenas(s, j)
    assert _tipos(s, j, a.id) == ["redireccion_a_error"]


def test_meta_refresh_con_render_sin_html_meta_tiene_retardo_desconocido():
    s, j = _montar()
    salto = _url(s, j, "/a", sin_meta=True, redirect_url="https://x.com/dest",
                 indexability_status=ESTADO_META_REFRESH)
    _url(s, j, "/dest")
    _cadenas(s, j)
    [fila] = _issues(s, j, "meta_refresh_redireccion")
    assert fila.url_id == salto.id
    assert fila.details["retardo_s"] is None and fila.severity == "info"


def test_meta_refresh_de_plantilla_es_una_fila():
    s, j = _montar()
    for i in range(10):
        _refresh(s, j, f"/puente/{i}", f"/dest/{i}", content="0;url=/d")
    _cadenas(s, j)
    [fila] = _issues(s, j, "meta_refresh_redireccion")
    assert fila.details["alcance"] == "plantilla"
    assert fila.details["paginas_afectadas"] == 10


def test_meta_refresh_no_es_indexable_ni_canonical_missing():
    s, j = _montar()
    a = _refresh(s, j, "/a", "/dest")
    _url(s, j, "/dest", canonical="https://x.com/dest")
    _indexabilidad_y_canonicals(s, j)
    s.expire_all()
    assert s.get(Url, a.id).indexable is False
    assert s.get(Url, a.id).indexability_status == ESTADO_META_REFRESH
    assert "canonical_missing" not in _tipos(s, j, a.id)


# -- B6: _Fichas sigue la meta refresh ----------------------------------------

def test_fichas_sigue_los_saltos_de_meta_refresh():
    s, j = _montar()
    _salto(s, j, "/a", "/mr")
    _refresh(s, j, "/mr", "/final")
    _url(s, j, "/final")
    fichas = _Fichas(s, j.id)
    final, cadena, bucle = fichas.seguir_redirecciones(fichas.buscar("https://x.com/a"))
    assert final.url == "https://x.com/final"
    assert cadena == ["https://x.com/a", "https://x.com/mr", "https://x.com/final"]
    assert bucle is False


def test_canonical_a_una_meta_refresh_es_canonical_a_redireccion():
    s, j = _montar()
    a = _url(s, j, "/a", canonical="https://x.com/mr")
    _refresh(s, j, "/mr", "/final")
    _url(s, j, "/final", canonical="https://x.com/final")
    _canonicals(s, j)
    [fila] = _issues(s, j, "canonical_a_redireccion", a.id)
    assert fila.details["destino_final"] == "https://x.com/final"


# -- Re-analizar dos veces da lo mismo ----------------------------------------

import os  # noqa: E402

import pytest  # noqa: E402

URL_PG = os.environ.get("PAGERANK_TEST_DATABASE_URL")


@pytest.mark.parametrize("motor", [
    "sqlite",
    pytest.param("postgres", marks=pytest.mark.skipif(
        not URL_PG, reason="sin PAGERANK_TEST_DATABASE_URL")),
])
def test_run_all_dos_veces_da_lo_mismo(tmp_path, monkeypatch, motor):
    """Se EJECUTA `run_all` dos veces sobre el mismo censo (decision 74): el
    re-analisis reescribe `indexable`, `noindex` y el estado, y los avisos que
    se sustituyen (`noindex_page`) no pueden depender de lo que quedo del
    primero. En SQLite, el PageRank (SQL de Postgres) no corre."""
    from shared.models import Heading, Link

    if motor == "postgres":
        engine = create_engine(URL_PG)
    else:
        engine = create_engine(f"sqlite:///{tmp_path}/a.db")
        for paso in ("compute_pagerank",):
            monkeypatch.setattr(SEOAnalyzer, paso, lambda self, *a, **k: None)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config={})
    s.add(j)
    s.flush()
    def _meta(u):
        return s.query(HtmlMeta).filter(HtmlMeta.url_id == u.id).one()

    try:
        home = _url(s, j, "/", canonical="https://x.com/")
        _url(s, j, "/f", canonical="https://x.com/f", cabecera="https://x.com/f/")
        rojo = _url(s, j, "/f/rojo", canonical="https://x.com/f")
        _meta(rojo).meta_robots = "noindex"
        _refresh(s, j, "/mr", "/noidx", content="3; url=/noidx")
        noidx = _url(s, j, "/noidx")
        _meta(noidx).meta_robots = "noindex"
        _salto(s, j, "/viejo", "/noidx")
        _url(s, j, "/solo-cabecera", cabecera="https://x.com/f")
        _meta(home).title = "Sin título | Marca"
        _meta(_url(s, j, "/contacto", canonical="https://x.com/contacto")).title = "Contacto | Marca"
        s.add(Heading(url_id=home.id, tag="h1", position=0, text=None, oculto=False))
        s.add(Link(job_id=j.id, from_url_id=home.id, to_url="https://x.com/f",
                   to_url_hash="https://x.com/f", is_internal=True, follow=True,
                   link_position="content"))
        s.commit()

        def _foto():
            return sorted(
                (i.url_id, i.issue_type, i.severity, repr(i.details), i.group_key)
                for i in s.execute(select(Issue).where(Issue.job_id == j.id)).scalars())

        SEOAnalyzer(s, j.id).run_all()
        s.expire_all()
        primera = _foto()
        SEOAnalyzer(s, j.id).run_all()
        s.expire_all()
        assert _foto() == primera
        tipos = {t for _, t, *_ in primera}
        assert {"noindex_y_canonical_a_otra", "redireccion_a_noindex",
                "canonical_html_y_cabecera_distintos", "h1_vacio",
                "title_generico", "enlace_sin_ancla"} <= tipos, tipos
        # /noidx sigue con su noindex_page; /f/rojo lo cambio por el nuevo.
        noindex = {u for u, t, *_ in primera if t == "noindex_page"}
        assert noidx.id in noindex and rojo.id not in noindex
    finally:
        s.rollback()
        if motor == "postgres":
            from shared.models import Heading, Link
            ids = [u for (u,) in s.query(Url.id).filter(Url.job_id == j.id)]
            s.query(Issue).filter(Issue.job_id == j.id).delete(synchronize_session=False)
            s.query(Link).filter(Link.job_id == j.id).delete(synchronize_session=False)
            for tabla in (Heading, HtmlMeta):
                s.query(tabla).filter(tabla.url_id.in_(ids or [0])).delete(
                    synchronize_session=False)
            s.query(Url).filter(Url.job_id == j.id).delete()
            s.query(Job).filter(Job.id == j.id).delete()
            s.commit()
        s.close()
        engine.dispose()


# -- Redireccion a la portada (#11, decision 228) ------------------------------

def test_una_pagina_que_redirige_a_la_portada_avisa_en_el_origen():
    s, j = _montar()
    _url(s, j, "/")
    v = _salto(s, j, "/producto-borrado", "/")
    _cadenas(s, j)
    [fila] = _issues(s, j, "redireccion_a_portada")
    assert fila.url_id == v.id and fila.severity == "warning"
    assert fila.details["alcance"] == "origen"
    assert fila.details["final"] == "https://x.com/"


def test_a_la_portada_de_un_idioma_tambien_y_en_dos_saltos_no_es_ademas_cadena():
    s, j = _montar()
    _url(s, j, "/es/")
    v = _salto(s, j, "/a", "/b")
    _salto(s, j, "/b", "/es/")
    _cadenas(s, j)
    assert _tipos(s, j, v.id) == ["redireccion_a_portada"]
    assert _issues(s, j, "redirect_chain") == []


def test_lo_que_ya_es_la_portada_no_avisa():
    s, j = _montar()
    _url(s, j, "/")
    for origen in ("http://x.com/", "https://www.x.com/", "/es/", "/index.html",
                   "/es/inicio", "/?utm_source=x", "/index.php?x=1"):
        _salto(s, j, origen, "/")
    _cadenas(s, j)
    assert _issues(s, j, "redireccion_a_portada") == []


def test_portada_que_da_error_o_sin_rastrear_no_es_redireccion_a_portada():
    s, j = _montar()
    _url(s, j, "/es/", status=500)
    _salto(s, j, "/a", "/es/")
    _salto(s, j, "/b", "/de/")
    _cadenas(s, j)
    assert _issues(s, j, "redireccion_a_portada") == []
    assert len(_issues(s, j, "redireccion_a_error")) == 1


def test_meta_refresh_a_la_portada_es_un_solo_aviso():
    s, j = _montar()
    _url(s, j, "/")
    mr = _refresh(s, j, "/mr", "/")
    _cadenas(s, j)
    assert _tipos(s, j, mr.id) == ["redireccion_a_portada"]


def test_diez_redirecciones_a_la_portada_son_una_fila_en_ella():
    s, j = _montar()
    home = _url(s, j, "/")
    for i in range(12):
        _salto(s, j, f"/borrado-{i}", "/")
    _cadenas(s, j)
    [fila] = _issues(s, j, "redireccion_a_portada")
    assert fila.url_id == home.id
    assert fila.details["alcance"] == "destino"
    assert fila.details["redirecciones_que_llegan"] == 12
    assert len(fila.details["ejemplos"]) == 5
