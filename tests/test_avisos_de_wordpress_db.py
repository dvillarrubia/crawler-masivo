"""Avisos del adaptador WordPress (decision 161), EJECUTADOS contra SQLite
(decision 74): lo que la REST API publica frente al censo.

- `wp_publicado_no_rastreado`: uno por (host, tipo), solo en un censo entero
  (ni truncado ni cortado por `max_depth`), solo lo que pasa los filtros del
  job, y nunca lo que si se rastreo (eso es `orphan_page`, decision 36).
- `wp_adjunto_indexable`: del censo o de la muestra, con la indexabilidad de
  siempre (un canonical al fichero no es indexable).
- `wp_taxonomia_anemica`: solo archivos rastreados e indexables.
- `wp_paginacion_sin_fin`: la pagina N+1 con 200, salvo soft 404 de serie.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis import avisos_de_wordpress
from analysis.analyzer import SEOAnalyzer
from shared.models import AdapterEntity, Base, Issue, Job, Link, SitemapEntry, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _resumen_spider(**kw):
    r = {"detectado": True, "rest": "abierta", "raiz": "https://x.com/wp-json/",
         "tipos": {"post": {"clase": "entrada", "total": 3}, "page": {"clase": "entrada"},
                   "producto": {"clase": "entrada"}, "guest-author": {"clase": "entrada"},
                   "attachment": {"clase": "medio", "total": 900},
                   "category": {"clase": "termino"}, "post_tag": {"clase": "termino"}}}
    r.update(kw)
    return r


def _montar(finish_reason="finished", max_depth=10, wp=None, config=None, soft404=None):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, Link.__table__,
        SitemapEntry.__table__, AdapterEntity.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config={"max_depth": max_depth, **(config or {})}, finish_reason=finish_reason,
            started_at=datetime(2026, 10, 24, 9, tzinfo=timezone.utc),
            adapters={"wordpress": wp if wp is not None else _resumen_spider()},
            soft404=soft404)
    s.add(j)
    s.flush()
    _url(s, j, "/")
    return s, j


def _url(s, j, ruta, *, status=200, indexable=True, depth=1, host="x.com"):
    u = Url(job_id=j.id, url=f"https://{host}{ruta}", url_hash=f"h:{host}{ruta}", host=host,
            scheme="https", path=ruta, is_internal=True, is_html=True, status_code=status,
            indexable=indexable, crawl_depth=depth)
    s.add(u)
    s.flush()
    return u


def _entidad(s, j, tipo, ruta, *, en_alcance=True, estado="publish", cuenta=None,
             status_code=None, datos=None, host="x.com"):
    s.add(AdapterEntity(
        job_id=j.id, adaptador="wordpress", tipo=tipo, id_externo=ruta, en_alcance=en_alcance,
        url=f"https://{host}{ruta}", url_hash=f"h:{host}{ruta}", estado=estado, cuenta=cuenta,
        status_code=status_code, datos=datos))
    s.flush()


def _issues(s, j, tipo):
    return s.execute(select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
                     .where(Issue.job_id == j.id, Issue.issue_type == tipo)
                     .order_by(Issue.id)).all()


def _avisar(s, j):
    r = avisos_de_wordpress.avisar_sin_tumbar(SEOAnalyzer(s, j.id))
    s.commit()
    return r


# --- 1. Publicado y no rastreado ------------------------------------------------

def test_lo_publicado_que_nadie_enlaza_es_un_aviso_por_host_y_tipo():
    s, j = _montar()
    _url(s, j, "/rastreada/")
    _entidad(s, j, "post", "/rastreada/")
    _entidad(s, j, "post", "/huerfana-b/")
    _entidad(s, j, "post", "/huerfana-a/")
    _entidad(s, j, "page", "/pagina-suelta/")
    r = _avisar(s, j)
    filas = _issues(s, j, "wp_publicado_no_rastreado")
    assert len(filas) == 2, "uno por tipo, no uno por entrada"
    por_tipo = {f.details["tipo"]: f for f in filas}
    post = por_tipo["post"].details
    assert post["paginas_afectadas"] == 2
    assert [e["url"] for e in post["ejemplos"]] == [
        "https://x.com/huerfana-a/", "https://x.com/huerfana-b/"]
    assert post["por_motivo"] == {"sin_enlaces": 2}
    raiz = s.execute(select(Url.id).where(Url.path == "/")).scalar()
    assert {f.url_id for f in filas} == {raiz}, "anclado en la raiz del host"
    assert por_tipo["post"].severity == "warning"
    assert r["por_tipo"]["post"] == {"leidas": 3, "en_el_censo": 1}


def test_en_un_censo_truncado_o_cortado_por_profundidad_no_se_afirma():
    for kw, motivo in (({"finish_reason": "max_urls_reached"}, "truncado"),
                       ({"max_depth": 3}, "profundidad")):
        s, j = _montar(**kw)
        _url(s, j, "/hondo/", depth=3)
        _entidad(s, j, "post", "/no-vista/")
        r = _avisar(s, j)
        assert _issues(s, j, "wp_publicado_no_rastreado") == []
        assert r["wp_publicado_no_rastreado"]["no_afirmable"].startswith(motivo)
        assert r["wp_publicado_no_rastreado"]["sin_afirmar"] == 1


def test_lo_que_enlaza_una_pagina_rastreada_o_esta_fuera_de_alcance_no_se_afirma():
    s, j = _montar()
    origen = _url(s, j, "/origen/")
    _entidad(s, j, "post", "/enlazada/")
    _entidad(s, j, "post", "/solo-nofollow/")
    _entidad(s, j, "post", "/excluida/", en_alcance=False)
    s.add(Link(job_id=j.id, from_url_id=origen.id, to_url="https://x.com/enlazada/",
               to_url_hash="h:x.com/enlazada/", follow=True))
    s.add(Link(job_id=j.id, from_url_id=origen.id, to_url="https://x.com/solo-nofollow/",
               to_url_hash="h:x.com/solo-nofollow/", follow=False))
    s.flush()
    r = _avisar(s, j)
    (fila,) = _issues(s, j, "wp_publicado_no_rastreado")
    assert fila.details["por_motivo"] == {"solo_nofollow": 1}
    assert r["wp_publicado_no_rastreado"]["fuera_de_alcance"] == 1
    assert r["wp_publicado_no_rastreado"]["enlazadas_sin_rastrear"] == 1


def test_un_tipo_propio_sin_ninguna_entrada_rastreada_no_cuenta():
    """Autores invitados de Co-Authors Plus: REST API sin pagina publica."""
    s, j = _montar()
    _url(s, j, "/producto/uno/")
    _entidad(s, j, "producto", "/producto/uno/")
    _entidad(s, j, "producto", "/producto/dos/")
    _entidad(s, j, "guest-author", "/?guest-author=ana")
    r = _avisar(s, j)
    (fila,) = _issues(s, j, "wp_publicado_no_rastreado")
    assert fila.details["tipo"] == "producto" and fila.details["paginas_afectadas"] == 1
    assert r["wp_publicado_no_rastreado"]["tipo_sin_pagina_publica"] == 1


def test_sin_wordpress_detectado_o_con_la_rest_api_cerrada_no_hay_nada():
    for wp in ({"detectado": False, "rest": "no_detectado"},
               {"detectado": False, "rest": "cerrada", "codigo": 401}):
        s, j = _montar(wp=wp)
        _entidad(s, j, "post", "/x/")
        assert _avisar(s, j) is None
        assert s.execute(select(Issue.id).where(Issue.issue_type.like("wp_%"))).first() is None


# --- 2. Adjuntos ----------------------------------------------------------------

def test_paginas_de_adjunto_indexables_del_censo_y_de_la_muestra_en_un_aviso():
    s, j = _montar()
    _url(s, j, "/entrada/foto-1/")
    _entidad(s, j, "attachment", "/entrada/foto-1/", estado="inherit")
    html = {"content_type": "text/html; charset=UTF-8"}
    _entidad(s, j, "sonda_adjunto", "/entrada/foto-2/", status_code=200, datos=html)
    # Canonical al fichero (blogs.uoc.edu): no es indexable.
    _entidad(s, j, "sonda_adjunto", "/entrada/foto-3/", status_code=200,
             datos={**html, "canonical": "https://x.com/wp-content/uploads/foto-3.jpg"})
    # Lo correcto desde WordPress 6.4: redirige al fichero.
    _entidad(s, j, "sonda_adjunto", "/entrada/foto-4/", status_code=301,
             datos={"redirige_a": "https://x.com/wp-content/uploads/foto-4.jpg"})
    _avisar(s, j)
    (fila,) = _issues(s, j, "wp_adjunto_indexable")
    d = fila.details
    assert (d["paginas_afectadas"], d["en_el_censo"], d["de_la_muestra"], d["miradas"]) == (2, 1, 1, 4)
    assert d["adjuntos_en_wordpress"] == 900
    foto1 = s.execute(select(Url.id).where(Url.path == "/entrada/foto-1/")).scalar()
    assert fila.url_id == foto1, "anclado en la del censo"
    assert sum(d["no_indexables"].values()) == 2


def test_adjuntos_que_redirigen_no_avisan():
    s, j = _montar()
    _entidad(s, j, "sonda_adjunto", "/e/f/", status_code=301, datos={})
    _avisar(s, j)
    assert _issues(s, j, "wp_adjunto_indexable") == []


# --- 3. Taxonomias anemicas -----------------------------------------------------

def test_archivos_rastreados_e_indexables_con_una_entrada_o_ninguna():
    s, j = _montar()
    _url(s, j, "/tag/una/")
    _url(s, j, "/tag/vacia/")
    _url(s, j, "/tag/noindex/", indexable=False)
    _url(s, j, "/tag/muchas/")
    _entidad(s, j, "post_tag", "/tag/una/", cuenta=1)
    _entidad(s, j, "post_tag", "/tag/vacia/", cuenta=0)
    _entidad(s, j, "post_tag", "/tag/noindex/", cuenta=1)
    _entidad(s, j, "post_tag", "/tag/muchas/", cuenta=12)
    _entidad(s, j, "post_tag", "/tag/no-rastreada/", cuenta=1)
    r = _avisar(s, j)
    (fila,) = _issues(s, j, "wp_taxonomia_anemica")
    assert fila.severity == "info"
    assert fila.details["paginas_afectadas"] == 2 and fila.details["sin_entradas"] == 1
    assert fila.details["terminos_leidos"] == 5
    assert r["wp_taxonomia_anemica"]["anemicas_sin_rastrear"] == 1


def test_el_umbral_es_configurable():
    s, j = _montar(config={"analysis_thresholds": {"wp_taxonomia_anemica_max": 0}})
    _url(s, j, "/tag/una/")
    _entidad(s, j, "post_tag", "/tag/una/", cuenta=1)
    _avisar(s, j)
    assert _issues(s, j, "wp_taxonomia_anemica") == []


# --- 5. Paginacion --------------------------------------------------------------

def test_la_pagina_n_mas_1_con_200_es_un_aviso_por_host():
    s, j = _montar()
    _url(s, j, "/category/noticias/")
    html = {"content_type": "text/html", "pagina_1": "https://x.com/category/noticias/",
            "pagina_1_hash": "h:x.com/category/noticias/",
            "archivo": "category", "entradas": 7, "n": 8, "enlaza_a_la_siguiente": True}
    _entidad(s, j, "sonda_paginacion", "/category/noticias/page/8/", status_code=200, datos=html)
    _entidad(s, j, "sonda_paginacion", "/page/4/", status_code=404,
             datos={"content_type": "text/html"})
    _avisar(s, j)
    (fila,) = _issues(s, j, "wp_paginacion_sin_fin")
    assert fila.severity == "warning"
    assert fila.details["paginas_afectadas"] == 1 and fila.details["sondas_del_host"] == 2
    assert fila.details["sondas"][0]["enlaza_a_la_siguiente"] is True
    cat = s.execute(select(Url.id).where(Url.path == "/category/noticias/")).scalar()
    assert fila.url_id == cat


def test_con_soft_404_de_serie_no_se_repite():
    s, j = _montar(soft404={"sondas": [{"host": "x.com", "plantilla": None, "status": 200}]})
    _entidad(s, j, "sonda_paginacion", "/page/4/", status_code=200,
             datos={"content_type": "text/html"})
    r = _avisar(s, j)
    assert _issues(s, j, "wp_paginacion_sin_fin") == []
    assert r["wp_paginacion_sin_fin"]["cubiertas_por_soft404"] == 1


def test_noindex_mas_alla_del_final_es_info():
    s, j = _montar()
    _entidad(s, j, "sonda_paginacion", "/page/4/", status_code=200,
             datos={"content_type": "text/html", "meta_robots": "noindex, follow"})
    _avisar(s, j)
    (fila,) = _issues(s, j, "wp_paginacion_sin_fin")
    assert fila.severity == "info"


# --- Resumen e idempotencia -----------------------------------------------------

def test_el_resumen_va_al_job_y_re_analizar_da_lo_mismo():
    s, j = _montar()
    _entidad(s, j, "post", "/huerfana/")
    an = SEOAnalyzer(s, j.id)
    avisos_de_wordpress.avisar_sin_tumbar(an)
    s.commit()
    s.refresh(j)
    assert j.adapters["wordpress"]["analisis"]["wp_publicado_no_rastreado"]["emitidos"] == 1
    assert j.adapters["wordpress"]["rest"] == "abierta", "lo del spider se conserva"
    an.clear_existing_issues()
    avisos_de_wordpress.avisar_sin_tumbar(SEOAnalyzer(s, j.id))
    s.commit()
    assert len(_issues(s, j, "wp_publicado_no_rastreado")) == 1


def test_un_fallo_no_tumba_el_analisis(monkeypatch):
    s, j = _montar()
    _entidad(s, j, "post", "/x/")

    def _rompe(*a, **k):
        raise RuntimeError("dato raro")

    monkeypatch.setattr(avisos_de_wordpress, "_censo", _rompe)
    assert avisos_de_wordpress.avisar_sin_tumbar(SEOAnalyzer(s, j.id)) is None
    assert s.execute(select(Job.id)).first() is not None, "la sesion sigue viva"
