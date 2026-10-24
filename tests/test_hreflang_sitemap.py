"""Hreflang del sitemap (decision 167): se guarda y se suma al del HTML.

Google acepta tres formas de declarar hreflang —el `<link>` del `<head>`, la
cabecera HTTP `Link` y el `<xhtml:link>` del sitemap— y las trata igual. El
parser ya leia la del sitemap, pero se tiraba: un sitio que solo la declara
ahi (Typeform, medido en la PR) salia sin hreflang. Aqui:

- `shared.sitemaps`: compactar / expandir y cuando el sitemap esta completo.
- El spider real (arnes): cada entrada lleva su hreflang y el job la marca.
- El spider con la tuberia real contra Postgres y el analisis encima.
- `analyze_hreflang` contra SQLite: la union de fuentes, la discrepancia entre
  fuentes agrupada, y que un sitemap leido a medias no afirma ausencias.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared.sitemaps import compactar_hreflang, estado_hreflang_sitemap, expandir_hreflang


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


# ---------------------------------------------------------------------------
# shared/sitemaps.py
# ---------------------------------------------------------------------------
def test_compactar_quita_el_origen_propio_y_expandir_lo_devuelve():
    url = "https://www.x.com/es/a"
    pares = [("es", "https://www.x.com/es/a"), ("en", "https://www.x.com/en/a"),
             ("fr", "https://fr.x.com/a"), ("de", "//www.x.com/de/a"),
             ("x-default", "https://www.x.com/")]
    guardado = compactar_hreflang(url, pares)
    assert guardado == [["es", "/es/a"], ["en", "/en/a"], ["fr", "https://fr.x.com/a"],
                        ["de", "//www.x.com/de/a"], ["x-default", "/"]]
    assert expandir_hreflang(url, guardado) == pares
    # Otro esquema u otro puerto no es el mismo origen: se guarda entero.
    assert compactar_hreflang(url, [("en", "http://www.x.com/en/a")]) == [
        ["en", "http://www.x.com/en/a"]]
    assert compactar_hreflang(url, [("en", "https://www.x.com:8443/en/a")]) == [
        ["en", "https://www.x.com:8443/en/a"]]


def test_una_ruta_con_doble_barra_hace_el_viaje_de_ida_y_vuelta():
    """`https://x.com//fr/a` compactado a `//fr/a` volvia como URL relativa al
    protocolo con host `fr`: discrepancia falsa con el HTML y destino roto
    perdido (revision de la PR #231)."""
    url = "https://x.com/es/a"
    pares = [("fr", "https://x.com//fr/a"), ("en", "https://x.com/en//a")]
    guardado = compactar_hreflang(url, pares)
    assert guardado == [["fr", "https://x.com//fr/a"], ["en", "/en//a"]]
    assert expandir_hreflang(url, guardado) == pares


def test_expandir_distingue_no_se_sabe_de_ninguno():
    assert expandir_hreflang("https://x.com/", None) is None
    assert expandir_hreflang("https://x.com/", []) == []
    # Basura en la columna no tumba nada.
    assert expandir_hreflang("https://x.com/", [["es"], None, ["", "/a"], ["en", "/b"]]) == [
        ("en", "https://x.com/b")]


def test_estado_hreflang_sitemap():
    assert estado_hreflang_sitemap(None) == (False, False)
    # Censo anterior a la columna: sin marca no se sabe nada.
    assert estado_hreflang_sitemap({"parcial": False, "ficheros": []}) == (False, False)
    bien = {"hreflang": 1, "parcial": False, "entradas_recortadas": False,
            "hreflang_recortado": False,
            "ficheros": [{"formato": "sitemapindex", "declaradas": 2, "devueltas": 0},
                         {"formato": "urlset", "declaradas": 10, "devueltas": 10}]}
    assert estado_hreflang_sitemap(bien) == (True, True)
    for cambio in ({"parcial": True}, {"entradas_recortadas": True},
                   {"hreflang_recortado": True}):
        assert estado_hreflang_sitemap({**bien, **cambio}) == (True, False)
    cortado = {**bien, "ficheros": [{"formato": "urlset", "cortado": True}]}
    assert estado_hreflang_sitemap(cortado) == (True, False)
    tope = {**bien, "ficheros": [{"formato": "urlset", "declaradas": 60000,
                                  "devueltas": 50000}]}
    assert estado_hreflang_sitemap(tope) == (True, False)


# ---------------------------------------------------------------------------
# Spider real (arnes, sin BD)
# ---------------------------------------------------------------------------
HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
NS = ('xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
      'xmlns:xhtml="http://www.w3.org/1999/xhtml"')


def _sitemap_con_hreflang(base="http://localhost:{port}"):
    def entrada(ruta, otros):
        enlaces = "".join(
            f'<xhtml:link rel="alternate" hreflang="{lang}" href="{href}"/>'
            for lang, href in otros)
        return f"<url><loc>{base}{ruta}</loc>{enlaces}</url>"
    cuerpo = (
        entrada("/es/a", [("es", f"{base}/es/a"), ("en", f"{base}/en/a"),
                          ("x-default", f"{base}/es/a")])
        + entrada("/en/a", [("es", f"{base}/es/a"), ("en", f"{base}/en/a"),
                            ("x-default", f"{base}/es/a")])
        + entrada("/sin", []))
    return [200, {"Content-Type": "application/xml"},
            f'<?xml version="1.0"?><urlset {NS}>{cuerpo}</urlset>']


def _pagina(titulo):
    return [200, {"Content-Type": "text/html"},
            f"<html><head><title>{titulo}</title></head><body>{titulo}</body></html>"]


def _rastrear(entrada, env=None):
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, **(env or {})})
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    job = None
    if "@@JOB@@" in proc.stdout:
        job = json.loads(proc.stdout.split("@@JOB@@", 1)[1].split("\n", 1)[0])
    return items, job


def test_spider_guarda_el_hreflang_de_cada_entrada_compactado():
    pytest.importorskip("scrapy")
    rutas = {"/sitemap_index.xml": _sitemap_con_hreflang(),
             "/es/a": _pagina("es"), "/en/a": _pagina("en"), "/sin": _pagina("sin")}
    items, job = _rastrear({"seeds": ["http://localhost:{port}/"],
                            "config": {"max_depth": 1}, "rutas": rutas, "ver_job": True})
    (entradas,) = [i["entradas"] for i in items if i["_tipo"] == "SitemapEntradasItem"]
    por_url = {e["url"].rsplit("SEMILLA", 1)[1]: e["hreflang"] for e in entradas}
    # Del mismo origen, sin el; la entrada sin anotaciones lleva [] (no None).
    assert por_url == {
        "/es/a": [["es", "/es/a"], ["en", "/en/a"], ["x-default", "/es/a"]],
        "/en/a": [["es", "/es/a"], ["en", "/en/a"], ["x-default", "/es/a"]],
        "/sin": [],
    }
    (resumen,) = [a["sitemaps"] for a in job if "sitemaps" in a]
    assert resumen["hreflang"] == 1
    assert resumen["hreflang_guardadas"] == 6
    assert resumen["hreflang_recortado"] is False


def test_spider_con_tope_de_anotaciones_las_que_no_caben_son_none():
    pytest.importorskip("scrapy")
    rutas = {"/sitemap_index.xml": _sitemap_con_hreflang()}
    items, job = _rastrear({"seeds": ["http://localhost:{port}/"],
                            "config": {"max_depth": 0}, "rutas": rutas, "ver_job": True},
                           env={"SITEMAP_HREFLANG_MAX": "4"})
    (entradas,) = [i["entradas"] for i in items if i["_tipo"] == "SitemapEntradasItem"]
    por_url = {e["url"].rsplit("SEMILLA", 1)[1]: e["hreflang"] for e in entradas}
    assert len(por_url["/es/a"]) == 3
    assert por_url["/en/a"] is None     # no cabe: no se sabe, no «ninguno»
    assert por_url["/sin"] == []        # cero anotaciones caben siempre
    (resumen,) = [a["sitemaps"] for a in job if "sitemaps" in a]
    assert resumen["hreflang_recortado"] is True
    assert resumen["hreflang_guardadas"] == 3
    assert estado_hreflang_sitemap(resumen) == (True, False)


# ---------------------------------------------------------------------------
# Spider + tuberia real contra Postgres, y el analisis encima
# ---------------------------------------------------------------------------
URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")


def test_de_punta_a_punta_postgres_el_hreflang_solo_del_sitemap_se_analiza():
    """Como Typeform: hreflang SOLO en el sitemap. Se guarda en
    `sitemap_entries.hreflang` y `analyze_hreflang` lo ve: `/en/a` no
    devuelve el enlace a `/es/a` y el aviso dice que sale del sitemap."""
    pytest.importorskip("scrapy")
    if not URL_BD:
        pytest.skip("sin PAGERANK_TEST_DATABASE_URL")
    import socket

    from sqlalchemy import text

    from analysis.analyzer import SEOAnalyzer
    from shared.models import Base, Issue, Job, SitemapEntry

    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    jid = uuid.uuid4()
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        puerto = so.getsockname()[1]
    base = "http://127.0.0.1:{port}"
    estado, cabeceras, xml = _sitemap_con_hreflang(base)
    # `/en/a` no devuelve el enlace a `/es/a` (solo se anota a si misma; el
    # x-default hacia `/es/a` tambien seria retorno).
    i = xml.index(f"<url><loc>{base}/en/a</loc>")
    k = xml.index("</url>", i) + len("</url>")
    xml = (xml[:i] + f'<url><loc>{base}/en/a</loc><xhtml:link rel="alternate" '
           f'hreflang="en" href="{base}/en/a"/></url>' + xml[k:])
    with Sesion() as s:
        s.add(Job(id=jid, name="test hreflang sitemap", status="running", seeds=[],
                  config={}))
        s.commit()
    try:
        entrada = {"seeds": ["http://127.0.0.1:{port}/"], "config": {"max_depth": 1},
                   "rutas": {"/sitemap_index.xml": [estado, cabeceras, xml],
                             "/es/a": _pagina("es"), "/en/a": _pagina("en"),
                             "/sin": _pagina("sin")},
                   "bd": URL_BD, "job_id": str(jid), "puerto": puerto}
        _rastrear(entrada)
        with Sesion() as s:
            filas = {u.rsplit(str(puerto), 1)[1]: h for u, h in s.query(
                SitemapEntry.url, SitemapEntry.hreflang).filter(SitemapEntry.job_id == jid)}
            assert filas["/es/a"] == [["es", "/es/a"], ["en", "/en/a"],
                                      ["x-default", "/es/a"]]
            assert filas["/sin"] == []
            # Lo que se guarda, en bytes: es lo que se mide en la PR.
            peso = s.execute(text(
                "SELECT sum(pg_column_size(hreflang)) FROM sitemap_entries "
                "WHERE job_id = :j"), {"j": str(jid)}).scalar()
            assert 0 < peso < 300
            assert s.get(Job, jid).sitemaps["hreflang"] == 1
            SEOAnalyzer(s, jid).analyze_hreflang()
            s.commit()
            avisos = [(i.issue_type, i.details) for i in s.query(Issue)
                      .filter(Issue.job_id == jid)]
        sin_retorno = [d for t, d in avisos if t == "hreflang_missing_return"]
        assert len(sin_retorno) == 1, avisos
        assert sin_retorno[0]["lang"] == "en" and sin_retorno[0]["origen"] == "sitemap"
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": str(jid)})
        engine.dispose()


# ---------------------------------------------------------------------------
# analyze_hreflang contra SQLite
# ---------------------------------------------------------------------------
from analysis.analyzer import SEOAnalyzer  # noqa: E402
from shared.models import (  # noqa: E402
    Base, Hreflang, HtmlMeta, Issue, Job, SitemapEntry, Url)

SITEMAP_COMPLETO = {"hreflang": 1, "parcial": False, "entradas_recortadas": False,
                    "hreflang_recortado": False, "ficheros": []}


def _montar(sitemaps=SITEMAP_COMPLETO):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Hreflang.__table__,
        Issue.__table__, SitemapEntry.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config={}, sitemaps=sitemaps)
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, url, *, status=200, indexable=True, estado="Indexable"):
    u = Url(job_id=j.id, url=url, url_hash=url[-40:], is_internal=True, is_html=True,
            status_code=status, host=url.split("/")[2], resource_type="html",
            indexable=indexable, indexability_status=estado)
    s.add(u)
    s.flush()
    s.add(HtmlMeta(url_id=u.id, canonical_href=url))
    s.flush()
    return u


def _html(s, pagina, *pares, origen=None):
    for lang, href in pares:
        s.add(Hreflang(url_id=pagina.id, lang=lang, href=href, origen=origen))
    s.flush()


def _sitemap(s, j, url, *pares, hreflang=...):
    if hreflang is ...:
        hreflang = compactar_hreflang(url, pares)
    s.add(SitemapEntry(job_id=j.id, url_hash=url[-40:], url=url,
                       fichero="https://x.com/sitemap.xml", hreflang=hreflang))
    s.flush()


def _analizar(s, j):
    SEOAnalyzer(s, j.id).analyze_hreflang()
    s.flush()
    return s.query(Issue).filter(Issue.job_id == j.id).all()


def _de(issues, tipo):
    return [i for i in issues if i.issue_type == tipo]


ES, EN, FR = "https://x.com/es/a", "https://x.com/en/a", "https://x.com/fr/a"
CLUSTER = (("es", ES), ("en", EN), ("fr", FR), ("x-default", ES))


def test_un_cluster_solo_en_el_sitemap_bien_hecho_no_da_nada():
    s, j = _montar()
    for u in (ES, EN, FR):
        _url(s, j, u)
        _sitemap(s, j, u, *CLUSTER)
    assert _analizar(s, j) == []


def test_un_cluster_solo_en_el_sitemap_con_un_fallo_se_ve():
    s, j = _montar()
    for u in (ES, EN):
        _url(s, j, u)
        _sitemap(s, j, u, *CLUSTER)
    _url(s, j, FR)
    # FR no devuelve el enlace a EN (a ES si, por su x-default).
    _sitemap(s, j, FR, ("fr", FR), ("x-default", ES))
    sin = _de(_analizar(s, j), "hreflang_missing_return")
    assert [(i.url_id, i.details["lang"], i.details["origen"]) for i in sin] == [
        (s.query(Url.id).filter(Url.url == EN).scalar(), "fr", "sitemap")]


def test_las_fuentes_se_suman_el_retorno_puede_venir_de_otra():
    """ES lo declara en el HTML; EN y FR devuelven el enlace solo desde el
    sitemap. Para Google es lo mismo: ningun aviso."""
    s, j = _montar()
    es = _url(s, j, ES)
    _html(s, es, *CLUSTER)
    for u in (EN, FR):
        _url(s, j, u)
        _sitemap(s, j, u, *CLUSTER)
    assert _analizar(s, j) == []


def test_lo_mismo_en_dos_fuentes_es_una_anotacion():
    s, j = _montar()
    pags = [_url(s, j, u) for u in (ES, EN, FR)]
    for p in pags:
        _html(s, p, *CLUSTER)
        _sitemap(s, j, p.url, *CLUSTER)
    # Un destino roto anotado en las dos fuentes: UN aviso, no dos.
    roto = _url(s, j, "https://x.com/it/a", status=404)
    _html(s, pags[0], ("it", roto.url))
    _sitemap(s, j, ES, *CLUSTER, ("it", roto.url), hreflang=None)  # fila vieja: no cuenta
    _sitemap(s, j, ES, *CLUSTER, ("it", roto.url))
    rotos = _de(_analizar(s, j), "hreflang_broken_target")
    assert len(rotos) == 1
    assert "origen" not in rotos[0].details   # tambien en el HTML


def test_discrepancia_entre_sitemap_y_html_un_aviso_por_host():
    """El sitemap dice `fr -> /fr/b` y el HTML `fr -> /fr/a` en las tres
    paginas: UN aviso agrupado, no tres; y ni `idioma_repetido` ni la falta de
    retorno de la URL que sobra (es el mismo hallazgo)."""
    s, j = _montar()
    raiz = _url(s, j, "https://x.com/")
    otra_fr = "https://x.com/fr/b"
    _url(s, j, otra_fr)
    for u in (ES, EN, FR):
        p = _url(s, j, u)
        _html(s, p, *CLUSTER)
        _sitemap(s, j, u, ("es", ES), ("en", EN), ("fr", otra_fr), ("x-default", ES))
    issues = _analizar(s, j)
    tipos = {i.issue_type for i in issues}
    assert "hreflang_idioma_repetido" not in tipos
    assert "hreflang_missing_return" not in tipos
    (d,) = _de(issues, "hreflang_fuentes_discrepan")
    assert d.url_id == raiz.id and d.severity == "warning"
    assert d.details["fuentes"] == ["html", "sitemap"]
    assert d.details["paginas_afectadas"] == 3
    assert d.details["idiomas"] == ["fr"]
    assert d.details["ejemplos"][0] == {"url": EN, "lang": "fr", "html": FR,
                                        "sitemap": otra_fr}
    assert d.group_key


def test_discrepancia_con_la_cabecera_tambien_cuenta():
    s, j = _montar()
    _url(s, j, "https://x.com/")
    otra = "https://x.com/en/b"
    _url(s, j, otra)
    for u in (ES, EN, FR):
        p = _url(s, j, u)
        _html(s, p, *CLUSTER)
        _html(s, p, ("en", otra), origen="cabecera")
    (d,) = _de(_analizar(s, j), "hreflang_fuentes_discrepan")
    assert d.details["fuentes"] == ["cabecera", "html"]


def test_si_una_de_las_urls_esta_rota_el_aviso_es_el_del_destino():
    """El sitemap apunta a la version http, que redirige: el hallazgo es
    `hreflang_broken_target` (dice que URL sobra), no ademas la discrepancia ni
    el idioma repetido."""
    s, j = _montar()
    http_fr = "http://x.com/fr/a"
    _url(s, j, http_fr, status=301)
    for u in (ES, EN, FR):
        p = _url(s, j, u)
        _html(s, p, *CLUSTER)
        _sitemap(s, j, u, ("es", ES), ("en", EN), ("fr", http_fr), ("x-default", ES))
    issues = _analizar(s, j)
    tipos = {i.issue_type for i in issues}
    assert "hreflang_fuentes_discrepan" not in tipos
    assert "hreflang_idioma_repetido" not in tipos
    rotos = _de(issues, "hreflang_broken_target")
    assert len(rotos) == 3 and all(r.details["origen"] == "sitemap" for r in rotos)


def test_repetido_dentro_de_una_fuente_sigue_siendo_idioma_repetido():
    s, j = _montar()
    otra_fr = "https://x.com/fr/b"
    _url(s, j, otra_fr)
    for u in (ES, EN, FR):
        p = _url(s, j, u)
        _html(s, p, *CLUSTER, ("fr", otra_fr))
        _sitemap(s, j, u, *CLUSTER)
    tipos = {i.issue_type for i in _analizar(s, j)}
    assert "hreflang_idioma_repetido" in tipos
    assert "hreflang_fuentes_discrepan" not in tipos


def test_una_pagina_no_indexable_no_es_fuente_tampoco_por_el_sitemap():
    s, j = _montar()
    for u in (ES, EN):
        _url(s, j, u)
        _sitemap(s, j, u, ("es", ES), ("en", EN), ("x-default", ES))
    nx = _url(s, j, "https://x.com/de/a", indexable=False, estado="Noindex")
    _sitemap(s, j, nx.url, ("de", nx.url), ("es", "https://x.com/no-existe"))
    assert _analizar(s, j) == []


def test_censo_anterior_a_la_columna_no_usa_el_sitemap():
    """Sin la marca `hreflang` en `jobs.sitemaps` la columna no dice nada: el
    analisis queda como antes (aqui ni lo que hubiera en ella se lee)."""
    s, j = _montar(sitemaps={"parcial": False, "ficheros": []})
    for u in (ES, EN):
        _url(s, j, u)
        _sitemap(s, j, u, ("es", ES), ("en", EN))
    assert _analizar(s, j) == []


def _sin_retorno_desde_sitemap_desconocido(sitemaps):
    """ES (HTML) anota EN; EN no lo devuelve en el HTML ni tiene entrada de
    sitemap guardada; FR si la tiene. El sitio usa hreflang en el sitemap."""
    s, j = _montar(sitemaps=sitemaps)
    es, en, _fr = _url(s, j, ES), _url(s, j, EN), _url(s, j, FR)
    _html(s, es, ("es", ES), ("en", EN), ("x-default", ES))
    _html(s, en, ("en", EN))
    _sitemap(s, j, FR, ("fr", FR), ("es", ES), ("x-default", ES))
    _html(s, es, ("fr", FR))
    return _analizar(s, j), en


def test_sitemap_completo_la_ausencia_si_se_afirma():
    issues, _ = _sin_retorno_desde_sitemap_desconocido(SITEMAP_COMPLETO)
    sin = _de(issues, "hreflang_missing_return")
    assert [i.details["href"] for i in sin] == [EN]


@pytest.mark.parametrize("cambio", [{"parcial": True}, {"entradas_recortadas": True},
                                    {"hreflang_recortado": True}])
def test_sitemap_leido_a_medias_no_afirma_ausencias(cambio):
    issues, en = _sin_retorno_desde_sitemap_desconocido({**SITEMAP_COMPLETO, **cambio})
    tipos = {i.issue_type for i in issues}
    # EN podria devolver el enlace (y anotarse) en la parte no leida.
    assert "hreflang_missing_return" not in tipos
    assert not [i for i in _de(issues, "hreflang_sin_autorreferencia") if i.url_id == en.id]


def test_sitemap_a_medias_sin_hreflang_en_lo_leido_queda_como_antes():
    """Si en lo leido ninguna entrada declara hreflang, el sitio no lo usa en
    el sitemap (lo pone un plugin para todas o para ninguna): las ausencias
    del HTML se siguen afirmando, como antes de la decision 167."""
    s, j = _montar(sitemaps={**SITEMAP_COMPLETO, "parcial": True})
    es, en = _url(s, j, ES), _url(s, j, EN)
    _html(s, es, ("es", ES), ("en", EN))
    _html(s, en, ("en", EN))
    _sitemap(s, j, EN)
    assert [i.details["href"] for i in _de(_analizar(s, j), "hreflang_missing_return")] == [EN]


def test_sin_la_tabla_el_analisis_sigue():
    """Base sin migrar: best-effort, se analiza sin el sitemap."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Hreflang.__table__,
        Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=[], config={},
            sitemaps=SITEMAP_COMPLETO)
    s.add(j)
    s.flush()
    es, en = _url(s, j, ES), _url(s, j, EN)
    _html(s, es, ("es", ES), ("en", EN))
    _html(s, en, ("en", EN))
    assert [i.details["href"] for i in _de(_analizar(s, j), "hreflang_missing_return")] == [EN]


# ---------------------------------------------------------------------------
# Revision de la PR #231
# ---------------------------------------------------------------------------
def test_ruta_con_doble_barra_en_las_dos_fuentes_no_discrepa():
    """El mismo `https://x.com//fr/a` en el HTML y en el sitemap: el sitemap
    lo devolvia como `//fr/a` (host `fr`) y salia una discrepancia falsa."""
    s, j = _montar()
    _url(s, j, "https://x.com/")
    cl = (("es", ES), ("en", EN), ("fr", "https://x.com//fr/a"), ("x-default", ES))
    for u in (ES, EN):
        _html(s, _url(s, j, u), *cl)
        _sitemap(s, j, u, *cl)
    assert not _de(_analizar(s, j), "hreflang_fuentes_discrepan")


def test_ruta_con_doble_barra_rota_solo_en_el_sitemap_se_ve():
    s, j = _montar()
    roto = "https://x.com//fr/a"
    _url(s, j, roto, status=404)
    for u in (ES, EN):
        _url(s, j, u)
        _sitemap(s, j, u, ("es", ES), ("en", EN), ("fr", roto), ("x-default", ES))
    rotos = _de(_analizar(s, j), "hreflang_broken_target")
    assert len(rotos) == 2
    assert {i.details["origen"] for i in rotos} == {"sitemap"}


def test_sitemap_a_medias_con_hreflang_solo_de_urls_no_rastreadas_no_afirma():
    """`en_uso` miraba solo las entradas de URLs rastreadas: si lo leido
    declaraba hreflang de otras, se afirmaba el retorno que falta en EN, que
    puede estar en la parte no leida. Lo guardado (`hreflang_guardadas`)
    dice que el sitio lo usa."""
    s, j = _montar(sitemaps={**SITEMAP_COMPLETO, "parcial": True, "hreflang_guardadas": 3})
    es, en = _url(s, j, ES), _url(s, j, EN)
    _html(s, es, ("es", ES), ("en", EN))
    _html(s, en, ("en", EN))
    _sitemap(s, j, "https://x.com/it/a", ("it", "https://x.com/it/a"), ("es", ES))
    assert _de(_analizar(s, j), "hreflang_missing_return") == []


def test_sitemap_completo_con_hreflang_de_otras_urls_si_afirma():
    """Completo, la misma situacion es un hecho: EN no tiene entrada."""
    s, j = _montar(sitemaps={**SITEMAP_COMPLETO, "hreflang_guardadas": 3})
    es, en = _url(s, j, ES), _url(s, j, EN)
    _html(s, es, ("es", ES), ("en", EN))
    _html(s, en, ("en", EN))
    _sitemap(s, j, "https://x.com/it/a", ("it", "https://x.com/it/a"), ("es", ES))
    assert [i.details["href"] for i in _de(_analizar(s, j), "hreflang_missing_return")] == [EN]


def test_fallo_a_medias_de_leer_el_sitemap_no_afirma_ausencias(monkeypatch):
    """Best-effort: si la lectura por lotes falla tras alguna fila, lo leido
    vale y lo demas no se conoce (sin el fallo, EN no tiene entrada en un
    sitemap completo y su retorno que falta si se afirma)."""
    de = "https://x.com/de/a"
    s, j = _montar(sitemaps={**SITEMAP_COMPLETO, "hreflang_guardadas": 4})
    _html(s, _url(s, j, ES), ("es", ES), ("en", EN))
    _html(s, _url(s, j, EN), ("en", EN))
    for u in (FR, de):
        _url(s, j, u)
        _sitemap(s, j, u, ("fr", FR), ("de", de))
    import shared.sitemaps as sm_mod
    original = sm_mod.expandir_hreflang
    llamadas = []

    def expandir(url, guardado):
        llamadas.append(url)
        if len(llamadas) > 1:
            raise RuntimeError("corte")
        return original(url, guardado)
    monkeypatch.setattr(sm_mod, "expandir_hreflang", expandir)
    assert _de(_analizar(s, j), "hreflang_missing_return") == []
    monkeypatch.setattr(sm_mod, "expandir_hreflang", original)
    s.query(Issue).delete()
    assert [i.details["href"] for i in _de(_analizar(s, j), "hreflang_missing_return")] == [EN]
