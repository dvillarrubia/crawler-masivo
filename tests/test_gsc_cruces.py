"""Search Console cruzado con el rastreo (decision 142), EJECUTADO contra
SQLite: columnas por URL, las cinco incidencias, la ventana que no se cubre,
las variantes con parametros y lo que el rastreo no encontro."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis import avisos_de_gsc
from analysis.analyzer import SEOAnalyzer
from shared.gsc import clave_de_url
from shared.models import Base, Client, GscEstado, GscPagina, Issue, Job, Url
from shared.prioridad import importancia

DIA = date(2026, 10, 14)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture
def s():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _montar(s, *, primer_dia=DIA - timedelta(days=89), truncados=()):
    c = Client(id=uuid.uuid4(), nombre="Acme", slug="acme", dominios=[], config_defecto={}, integraciones=[])
    s.add(c)
    s.flush()
    j = Job(id=uuid.uuid4(), name="censo", status="analyzing", seeds=["https://x.com/"], config={},
            cliente_id=c.id, started_at=datetime(2026, 10, 14, 3, tzinfo=timezone.utc))
    s.add(j)
    s.add(GscEstado(cliente_id=c.id, propiedad="sc-domain:x.com", estado="ok",
                    dias_truncados=list(truncados)))
    s.flush()
    return c, j


def _url(s, j, ruta, *, status=200, indexable=True, estado="Indexable", inlinks=5, **kw):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com", is_internal=True,
            is_html=True, status_code=status, indexable=indexable, indexability_status=estado,
            inlinks_count=inlinks, **kw)
    s.add(u)
    s.flush()
    return u


def _gsc(s, c, ruta, clics, imp, *, dia=DIA - timedelta(days=5), posicion=4.0):
    url = f"https://x.com{ruta}"
    s.add(GscPagina(cliente_id=c.id, propiedad="sc-domain:x.com", fecha=dia, url=url,
                    url_clave=clave_de_url(url), clics=clics, impresiones=imp, posicion=posicion))


def _analizar(s, j):
    s.add(GscPagina(cliente_id=j.cliente_id, propiedad="sc-domain:x.com", fecha=DIA - timedelta(days=89),
                    url="https://x.com/__marca", url_clave="marca", clics=0, impresiones=0))
    s.commit()
    an = SEOAnalyzer(s, j.id)
    avisos_de_gsc.avisar(an)
    s.commit()
    return {(u, t): (sev, d) for u, t, sev, d in s.execute(
        select(Issue.url_id, Issue.issue_type, Issue.severity, Issue.details).where(Issue.job_id == j.id))}


def test_columnas_e_incidencias(s):
    c, j = _montar(s)
    sana = _url(s, j, "/sana")
    rota = _url(s, j, "/rota", status=404, indexable=False, estado="Client Error (404)")
    noidx = _url(s, j, "/noidx", indexable=False, estado="Noindex", noindex=True)
    salta = _url(s, j, "/vieja", status=301, indexable=False, estado="Redirect (301)")
    huerfana = _url(s, j, "/huerfana", inlinks=0)
    semilla = _url(s, j, "/semilla", inlinks=0, crawl_depth=0)   # sin aviso de huerfana: no lo es
    s.add(Issue(job_id=j.id, url_id=huerfana.id, issue_type="sitemap_orphan", severity="warning", details={}))
    muerta = _url(s, j, "/muerta")
    _gsc(s, c, "/sana", 100, 2000)
    _gsc(s, c, "/sana", 20, 500, dia=DIA - timedelta(days=6), posicion=8.0)
    _gsc(s, c, "/rota", 40, 400)
    _gsc(s, c, "/noidx", 3, 30)
    _gsc(s, c, "/vieja", 7, 70)
    _gsc(s, c, "/huerfana", 12, 300)
    _gsc(s, c, "/semilla", 30, 300)
    _gsc(s, c, "/antes", 99, 99, dia=DIA - timedelta(days=95))    # fuera de la ventana
    _gsc(s, c, "/despues", 99, 99, dia=DIA + timedelta(days=2))
    issues = _analizar(s, j)
    for u in (sana, rota, muerta):
        s.refresh(u)
    assert (sana.gsc_clics, sana.gsc_impresiones, sana.gsc_posicion) == (120, 2500, 4.8)
    assert (muerta.gsc_clics, muerta.gsc_impresiones) == (0, 0)        # cubre: 0 medido
    assert issues[(rota.id, "trafico_en_pagina_perdida")][0] == "error"
    assert issues[(rota.id, "trafico_en_pagina_perdida")][1]["clics"] == 40
    assert (noidx.id, "trafico_en_pagina_perdida") in issues
    assert issues[(salta.id, "trafico_en_redireccion")][0] == "info"
    assert issues[(huerfana.id, "huerfana_con_trafico")][0] == "warning"
    assert (huerfana.id, "sitemap_orphan") not in issues          # un aviso por hallazgo
    assert (semilla.id, "huerfana_con_trafico") not in issues
    assert (muerta.id, "indexable_sin_impresiones") in issues
    assert (sana.id, "indexable_sin_impresiones") not in issues
    r = s.get(Job, j.id).gsc_resumen
    assert (r["clics"], r["clics_en_urls_del_censo"], r["urls_fuera_del_censo"]) == (212, 212, 0)
    assert r["cubre_la_ventana"] is True


@pytest.mark.parametrize("como", ["empieza_tarde", "truncado"])
def test_sin_cubrir_la_ventana_no_se_afirma_que_no_tuvo_impresiones(s, como):
    if como == "empieza_tarde":
        c, j = _montar(s, primer_dia=DIA - timedelta(days=10))
    else:
        c, j = _montar(s, truncados=[(DIA - timedelta(days=20)).isoformat()])
    muerta = _url(s, j, "/muerta")
    _url(s, j, "/sana")
    _gsc(s, c, "/sana", 5, 50)
    s.commit()
    an = SEOAnalyzer(s, j.id)
    if como == "empieza_tarde":
        avisos_de_gsc.avisar(an)
    else:
        s.add(GscPagina(cliente_id=c.id, propiedad="sc-domain:x.com", fecha=DIA - timedelta(days=89),
                        url="https://x.com/__m", url_clave="m", clics=0, impresiones=0))
        s.commit()
        avisos_de_gsc.avisar(an)
    s.commit()
    s.refresh(muerta)
    assert muerta.gsc_clics is None
    assert not s.query(Issue).filter(Issue.issue_type == "indexable_sin_impresiones").count()
    assert s.get(Job, j.id).gsc_resumen["cubre_la_ventana"] is False


def test_variantes_con_parametros_y_lo_que_el_rastreo_no_encontro(s):
    c, j = _montar(s)
    ficha = _url(s, j, "/ficha")
    _gsc(s, c, "/ficha", 30, 300)
    _gsc(s, c, "/ficha?partner=59", 50, 500)
    _gsc(s, c, "/ficha?partner=12", 20, 200)
    _gsc(s, c, "/otra?utm_source=x", 2, 20)          # la ruta no esta en el censo
    _gsc(s, c, "/blog/vieja", 15, 150)                 # no encontrada
    issues = _analizar(s, j)
    sev, d = issues[(ficha.id, "trafico_en_variante_con_parametros")]
    assert (sev, d["clics_variantes"], d["clics_pagina"], d["variantes"]) == ("warning", 70, 30, 2)
    assert d["ejemplos"][0] == {"url": "https://x.com/ficha?partner=59", "clics": 50}
    r = s.get(Job, j.id).gsc_resumen
    assert (r["clics_en_variantes"], r["urls_fuera_del_censo"], r["clics_fuera_del_censo"]) == (70, 2, 17)
    assert r["fuera_del_censo"][0]["url"] == "https://x.com/blog/vieja"


def test_sin_search_console_no_hace_nada(s):
    c, j = _montar(s)
    u = _url(s, j, "/a")
    s.commit()
    avisos_de_gsc.avisar(SEOAnalyzer(s, j.id))
    s.commit()
    s.refresh(u)
    assert u.gsc_clics is None and s.get(Job, j.id).gsc_resumen is None


def test_los_clics_pesan_en_la_prioridad():
    assert importancia(50, 1, True, gsc_clics=1000) == pytest.approx(2 * importancia(50, 1, True))
    assert importancia(50, 1, True, gsc_clics=0) == importancia(50, 1, True)
    assert importancia(50, 1, True, gsc_clics=None) == importancia(50, 1, True)
