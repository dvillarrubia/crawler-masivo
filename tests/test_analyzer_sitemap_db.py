"""URLs declaradas en el sitemap que no son indexables (decision 100).

El sitemap es la lista de URLs canonicas que el sitio pide indexar. Una entrada
que salta, da error, lleva noindex o se canonicaliza a otra se contradice con
la propia pagina. Medido sobre censos reales: 5.828 noindex en el sitemap de
penguin, 782 saltos y 501 errores 500 en el de www.uoc.edu, y 59 de las 68
entradas del de Lopesan.

Estos tests EJECUTAN `analyze_sitemap` contra SQLite (decision 74).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer, motivo_sitemap_no_indexable
from shared.models import Base, HtmlMeta, Issue, Job, Link, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar(**job_kw):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[Job.__table__, Url.__table__, Issue.__table__, Link.__table__,
                HtmlMeta.__table__],
    )
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing",
            seeds=["https://x.com/"], config={}, **job_kw)
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, canonical=None, **kw):
    datos = dict(
        job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
        is_html=True, status_code=200, status_group="2xx", indexable=True,
        indexability_status="Indexable", inlinks_count=3, crawl_depth=1,
        in_sitemap=True,
    )
    datos.update(kw)
    u = Url(**datos)
    s.add(u)
    s.flush()
    if canonical is not None:
        s.add(HtmlMeta(url_id=u.id, canonical_href=canonical))
        s.flush()
    return u


def _issues(s, j, tipo):
    return s.execute(
        select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
        .where(Issue.job_id == j.id, Issue.issue_type == tipo)
        .order_by(Issue.url_id)
    ).all()


def _analizar(s, j):
    SEOAnalyzer(s, j.id).analyze_sitemap()
    s.commit()


def test_cada_motivo_sale_con_su_detalle():
    s, j = _montar()
    _url(s, j, "/ok")
    salto = _url(s, j, "/vieja", status_code=301, status_group="3xx", is_html=False,
                 indexable=False, indexability_status="Redirect (301)",
                 redirect_url="https://x.com/nueva")
    roto = _url(s, j, "/rota", status_code=404, status_group="4xx",
                indexable=False, indexability_status="Client Error (404)")
    caida = _url(s, j, "/caida", status_code=500, status_group="5xx",
                 indexable=False, indexability_status="Server Error (500)")
    noidx = _url(s, j, "/noidx", indexable=False, noindex=True,
                 indexability_status="Noindex")
    canon = _url(s, j, "/variante", indexable=False,
                 indexability_status="Canonicalised", canonical="https://x.com/buena")
    robots = _url(s, j, "/privada", status_code=None, status_group="blocked",
                  is_html=False, indexable=False,
                  indexability_status="Blocked by robots.txt")
    s.commit()

    _analizar(s, j)

    avisos = {a[0]: a for a in _issues(s, j, "sitemap_no_indexable")}
    motivos = {uid: a[2]["motivo"] for uid, a in avisos.items()}
    assert motivos == {
        salto.id: "redireccion", roto.id: "4xx", caida.id: "5xx",
        noidx.id: "noindex", canon.id: "canonicalizada",
        robots.id: "bloqueada_robots",
    }
    # Ninguna saca una indexable del indice: aviso, no error (decision 64).
    assert {a[1] for a in avisos.values()} == {"warning"}
    # Una por URL: el par (tipo, URL) ya identifica el hallazgo (decision 88).
    assert {a[3] for a in avisos.values()} == {None}
    assert avisos[salto.id][2]["destino"] == "https://x.com/nueva"
    assert avisos[canon.id][2]["destino"] == "https://x.com/buena"
    assert avisos[roto.id][2]["status_code"] == 404
    assert "destino" not in avisos[noidx.id][2]


def test_lo_que_no_se_sabe_no_se_afirma():
    """Un 429 es el sitio frenando al rastreador; sin respuesta no hay estado."""
    s, j = _montar()
    _url(s, j, "/frenada", status_code=429, status_group="4xx",
         indexable=False, indexability_status="Client Error (429)")
    _url(s, j, "/timeout", status_code=None, status_group="timeout", is_html=False,
         indexable=False, indexability_status="Sin respuesta")
    _url(s, j, "/sin-estado", indexable=None, indexability_status=None)
    s.commit()
    _analizar(s, j)
    assert _issues(s, j, "sitemap_no_indexable") == []


def test_una_no_indexable_del_sitemap_no_es_ademas_huerfana():
    """Un aviso por hallazgo (decision 36): con noindex, «enlazala» seria el
    consejo equivocado. Medido en penguin: 5.825 noindex del sitemap salian
    como `sitemap_orphan`."""
    s, j = _montar()
    noidx = _url(s, j, "/autor", indexable=False, noindex=True,
                 indexability_status="Noindex", inlinks_count=0)
    huerfana = _url(s, j, "/sola", inlinks_count=0)
    s.commit()
    _analizar(s, j)
    assert [a[0] for a in _issues(s, j, "sitemap_orphan")] == [huerfana.id]
    assert [a[0] for a in _issues(s, j, "sitemap_no_indexable")] == [noidx.id]


def test_fuera_del_sitemap_o_sin_datos_de_sitemap_no_hay_aviso():
    s, j = _montar()
    _url(s, j, "/ok")
    _url(s, j, "/fuera", in_sitemap=False, status_code=404, status_group="4xx",
         indexable=False, indexability_status="Client Error (404)")
    _url(s, j, "/desconocida", in_sitemap=None, status_code=404, status_group="4xx",
         indexable=False, indexability_status="Client Error (404)")
    s.commit()
    _analizar(s, j)
    assert _issues(s, j, "sitemap_no_indexable") == []

    s2, j2 = _montar()
    _url(s2, j2, "/rota", in_sitemap=None, status_code=404, status_group="4xx",
         indexable=False, indexability_status="Client Error (404)")
    s2.commit()
    _analizar(s2, j2)
    assert _issues(s2, j2, "sitemap_no_indexable") == []


def test_una_url_externa_del_sitemap_no_se_juzga_aqui():
    s, j = _montar()
    _url(s, j, "/ok")
    _url(s, j, "/fuera-de-casa", is_internal=False, status_code=404,
         status_group="4xx", indexable=False,
         indexability_status="Client Error (404)")
    s.commit()
    _analizar(s, j)
    assert _issues(s, j, "sitemap_no_indexable") == []


def test_con_los_enlaces_archivados_sigue_valiendo():
    """No lee `links` (decision 95): solo urls y html_meta."""
    s, j = _montar(enlaces_archivados={"fichero": "x.parquet"})
    rota = _url(s, j, "/rota", status_code=404, status_group="4xx",
                indexable=False, indexability_status="Client Error (404)")
    s.commit()
    _analizar(s, j)
    assert [a[0] for a in _issues(s, j, "sitemap_no_indexable")] == [rota.id]


@pytest.mark.parametrize("codigo,estado,esperado", [
    (301, "Redirect (301)", "redireccion"),
    (None, "Redirect (JS)", "redireccion"),
    (410, "Client Error (410)", "4xx"),
    (429, "Client Error (429)", None),
    (503, "Server Error (503)", "5xx"),
    (200, "Noindex", "noindex"),
    (200, "Canonicalised", "canonicalizada"),
    (None, "Blocked by robots.txt", "bloqueada_robots"),
    (204, "Sin contenido (204)", "sin_contenido"),
    (None, "Sin respuesta", None),
    (200, "Indexable", None),
    (200, None, None),
])
def test_motivo(codigo, estado, esperado):
    assert motivo_sitemap_no_indexable(codigo, estado) == esperado
