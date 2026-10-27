"""Lo que puede traer trafico (decision 157), EJECUTADO contra SQLite:
oportunidades de posicion con sus fuentes, canibalizacion por busqueda con
sus exclusiones, CTR bajo con la curva del propio sitio (de busqueda x pagina),
sitemaps segun Google y rendimiento por plantilla."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis import oportunidades_gsc as O
from analysis.analyzer import SEOAnalyzer
from shared.gsc import clave_de_url
from shared.models import Base, Client, GscConsulta, GscEstado, GscSitemap, HtmlMeta, Issue, Job, Link, Url

DIA = date(2026, 10, 14)
EN_VENTANA = DIA - timedelta(days=5)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture
def s():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _montar(s, *, config=None, detalle=None, sitemaps=None):
    c = Client(id=uuid.uuid4(), nombre="Acme", slug="acme", dominios=[], config_defecto={}, integraciones=[])
    s.add(c)
    s.flush()
    j = Job(id=uuid.uuid4(), name="censo", status="analyzing", seeds=["https://x.com/"], config=config or {},
            cliente_id=c.id, started_at=datetime(2026, 10, 14, 3, tzinfo=timezone.utc),
            gsc_resumen={"clics": 1}, sitemaps=sitemaps)
    s.add(j)
    s.add(GscEstado(cliente_id=c.id, propiedad="sc-domain:x.com", estado="ok",
                    detalle=detalle if detalle is not None else {"sitemaps": 0}))
    s.flush()
    return c, j


def _url(s, j, ruta, *, pr=50, depth=1, clics=None, imp=None, pos=None, noindex=False, indexable=True, **kw):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com", path=ruta.split("?")[0],
            is_internal=True, is_html=True, status_code=200, indexable=indexable and not noindex,
            noindex=noindex, indexability_status="Indexable" if indexable and not noindex else "Noindex",
            pagerank_score=pr, click_depth=depth, gsc_clics=clics, gsc_impresiones=imp, gsc_posicion=pos, **kw)
    s.add(u)
    s.flush()
    return u


def _consulta(s, c, ruta, consulta, clics, imp, pos=5.0, dia=EN_VENTANA):
    s.add(GscConsulta(cliente_id=c.id, propiedad="sc-domain:x.com", fecha=dia,
                      url_clave=clave_de_url(f"https://x.com{ruta}"), consulta=consulta,
                      clics=clics, impresiones=imp, posicion=pos))


def _analizar(s, j):
    s.commit()
    an = SEOAnalyzer(s, j.id)
    resumen = O.avisar(an)
    s.commit()
    issues = {}
    for u, t, sev, d in s.execute(select(Issue.url_id, Issue.issue_type, Issue.severity, Issue.details)
                                  .where(Issue.job_id == j.id)):
        issues.setdefault((u, t), (sev, d))
    return resumen, issues


def test_oportunidad_de_posicion_con_sus_fuentes(s):
    c, j = _montar(s)
    obj = _url(s, j, "/blog/zapatos", pr=10, clics=20, imp=1000, pos=7.0)
    lejos = _url(s, j, "/blog/lejos", pr=60, depth=5, clics=5, imp=900, pos=12.0)
    fuerte = _url(s, j, "/blog/fuerte", pr=95, clics=50, imp=2000, pos=6.0)      # enlazada de sobra
    pocas = _url(s, j, "/blog/pocas", pr=10, clics=1, imp=100, pos=7.0)          # pocas impresiones
    arriba = _url(s, j, "/blog/arriba", pr=10, clics=90, imp=1000, pos=2.0)      # ya esta arriba
    comparte = _url(s, j, "/tienda/zapatos", pr=80)
    ya_enlaza = _url(s, j, "/tienda/otra", pr=90)
    noidx = _url(s, j, "/tienda/noidx", pr=99, noindex=True)
    for i in range(8):
        _url(s, j, f"/blog/p{i}", pr=20 + 5 * i)
    _consulta(s, c, "/blog/zapatos", "zapatos rojos", 10, 600, 7.0)
    _consulta(s, c, "/tienda/zapatos", "zapatos rojos", 30, 300, 3.0)
    _consulta(s, c, "/tienda/otra", "zapatos rojos", 3, 100, 9.0)
    _consulta(s, c, "/tienda/noidx", "zapatos rojos", 3, 100, 9.0)
    s.add(Link(job_id=j.id, from_url_id=ya_enlaza.id, to_url=obj.url, to_url_hash=obj.url_hash,
               is_internal=True, follow=True))
    _, issues = _analizar(s, j)
    sev, d = issues[(obj.id, "oportunidad_posicion")]
    assert sev == "info" and d["motivos"] == ["pagerank_bajo"]
    fuentes = [f["url"] for f in d["fuentes"]]
    assert fuentes[0] == comparte.url and d["fuentes"][0]["consulta_compartida"] == "zapatos rojos"
    assert ya_enlaza.url not in fuentes and noidx.url not in fuentes and obj.url not in fuentes
    # El resto, de su plantilla y con mas PageRank que ella, de mas a menos
    assert len(fuentes) == 5 and all(f["misma_plantilla"] for f in d["fuentes"][1:])
    assert [f["pagerank_score"] for f in d["fuentes"][1:]] == sorted(
        [f["pagerank_score"] for f in d["fuentes"][1:]], reverse=True)
    assert d["consultas"][0]["consulta"] == "zapatos rojos"
    assert issues[(lejos.id, "oportunidad_posicion")][1]["motivos"] == ["profundidad"]
    for u in (fuerte, pocas, arriba):
        assert (u.id, "oportunidad_posicion") not in issues, u.url


def test_canibalizacion_por_busqueda_y_sus_exclusiones(s):
    c, j = _montar(s, config={"marcas": ["Acmé"]})
    p1, p2 = _url(s, j, "/a"), _url(s, j, "/b")
    p3, p4 = _url(s, j, "/c"), _url(s, j, "/d")
    var, canon = _url(s, j, "/e?orden=precio"), _url(s, j, "/e")
    s.add(HtmlMeta(url_id=var.id, canonical_href="https://x.com/e"))
    home, p6 = _url(s, j, "/"), _url(s, j, "/f")
    _consulta(s, c, "/a", "zapatos rojos", 60, 500)
    _consulta(s, c, "/b", "zapatos rojos", 40, 500)
    _consulta(s, c, "/fuera", "zapatos rojos", 1, 50)            # fuera del censo y < 20 %
    _consulta(s, c, "/c", "botas", 95, 500)
    _consulta(s, c, "/d", "botas", 5, 500)                       # 5 %: no reparte
    _consulta(s, c, "/e?orden=precio", "sandalias", 50, 500)
    _consulta(s, c, "/e", "sandalias", 50, 500)                  # mismo grupo canonical
    _consulta(s, c, "/", "acme tienda", 50, 500)
    _consulta(s, c, "/f", "acme tienda", 50, 500)                # la home en busqueda de marca
    _consulta(s, c, "/c", "rara", 0, 30)
    _consulta(s, c, "/d", "rara", 0, 30)                         # pocas impresiones
    _consulta(s, c, "/c", "sin clics", 0, 600)
    _consulta(s, c, "/d", "sin clics", 0, 500)                   # reparte impresiones
    _consulta(s, c, "/c", "poca cosa", 5, 600)
    _consulta(s, c, "/d", "poca cosa", 4, 600)                   # menos de 10 clics
    resumen, issues = _analizar(s, j)
    sev, d = issues[(p1.id, "canibalizacion_por_consulta")]
    assert sev == "warning" and d["consulta"] == "zapatos rojos" and d["reparto_por"] == "clics"
    assert issues[(p3.id, "canibalizacion_por_consulta")][0] == "info"     # sin clics: informativa
    assert [u["url"] for u in d["urls"]] == [p1.url, p2.url] and d["urls"][0]["parte"] == pytest.approx(60 / 101, abs=1e-3)
    assert (p2.id, "canibalizacion_por_consulta") not in issues        # una por busqueda
    assert issues[(p3.id, "canibalizacion_por_consulta")][1]["consulta"] == "sin clics"
    assert issues[(p3.id, "canibalizacion_por_consulta")][1]["reparto_por"] == "impresiones"
    consultas = {d["consulta"] for (_, t), (_, d) in issues.items() if t == "canibalizacion_por_consulta"}
    assert consultas == {"zapatos rojos", "sin clics"}
    assert resumen["canibalizacion_por_consulta"] == 2


def test_ctr_bajo_con_la_curva_del_propio_sitio(s):
    c, j = _montar(s, config={"marcas": ["acme"]})
    # 40 busquedas en 2.ª posicion con un CTR del 20-24 %: la curva del sitio
    for i in range(40):
        _url(s, j, f"/p{i}")
        _consulta(s, c, f"/p{i}", f"cosa {i}", 200 + i, 1000, 2.0)
    mala = _url(s, j, "/mala")
    _consulta(s, c, "/mala", "cosa mala", 10, 1000, 2.1)
    _consulta(s, c, "/mala", "acme mala", 0, 5000, 2.0)              # de marca: fuera de la curva
    s.add(Issue(job_id=j.id, url_id=mala.id, issue_type="title_generico", severity="warning", details={}))
    poca = _url(s, j, "/poca")
    _consulta(s, c, "/poca", "poca", 0, 60, 2.0)                       # pocas impresiones
    lejos = _url(s, j, "/lejos")
    _consulta(s, c, "/lejos", "lejos", 0, 1000, 15.0)                  # tramo sin filas: no se afirma
    resumen, issues = _analizar(s, j)
    sev, d = issues[(mala.id, "ctr_bajo_para_posicion")]
    assert sev == "info" and d["snippet"] == ["title_generico"] and d["impresiones"] == 1000
    assert d["ctr"] == 0.01 and d["clics_que_faltan"] == round(d["ctr_esperado"] * 1000 - 10) > 150
    assert d["consultas"][0]["consulta"] == "cosa mala" and d["sin_marcas"] is False
    assert (poca.id, "ctr_bajo_para_posicion") not in issues
    assert (lejos.id, "ctr_bajo_para_posicion") not in issues
    curva = resumen["curva_ctr"]["tramos"]
    assert curva["2"]["valida"] is True and curva["2"]["filas"] == 42
    assert curva["11-20"]["valida"] is False


def test_sitemaps_segun_google(s):
    leido = (datetime.now(timezone.utc)).isoformat()
    ficheros = [{"url": "https://x.com/sitemap_index.xml", "status": 200, "hijos": 2, "leido_at": leido},
                {"url": "https://x.com/otro_index.xml", "status": 200, "hijos": 1, "leido_at": leido},
                {"url": "https://x.com/sitemap_posts.xml", "status": 200, "hijos": 0, "leido_at": leido}]
    c, j = _montar(s, sitemaps={"ficheros": ficheros}, detalle={"sitemaps": 1})
    home = _url(s, j, "/")
    s.add(GscSitemap(cliente_id=c.id, propiedad="sc-domain:x.com", path="https://x.com/sitemap_index.xml",
                     descargado=datetime(2026, 8, 1, tzinfo=timezone.utc), errores=2, avisos=0,
                     visto_en=datetime(2026, 10, 14, tzinfo=timezone.utc)))
    resumen, issues = _analizar(s, j)
    no_leido = [d for (u, t), (_, d) in issues.items() if t == "sitemap_no_leido_por_google"]
    assert [d["fichero"] for d in no_leido] == ["https://x.com/sitemap_index.xml"]
    assert issues[(home.id, "sitemap_con_errores_en_google")][1]["errores"] == 2
    assert issues[(home.id, "sitemap_no_enviado_a_google")][1]["fichero"] == "https://x.com/otro_index.xml"
    # El hijo de un indice no sale en la lista de Google: no se mira
    assert resumen["sitemaps"] == {"no_enviados": 1, "no_leidos": 1, "con_errores": 1}


def test_sin_saber_los_sitemaps_de_google_no_se_afirma_nada(s):
    ficheros = [{"url": "https://x.com/sitemap.xml", "status": 200, "hijos": 0}]
    c, j = _montar(s, sitemaps={"ficheros": ficheros}, detalle={})
    _url(s, j, "/")
    resumen, issues = _analizar(s, j)
    assert resumen["sitemaps"] is None
    assert not [t for (_, t) in issues if t.startswith("sitemap_")]


def test_rendimiento_por_plantilla_y_profundidad(s):
    c, j = _montar(s, config={"templates": [{"nombre": "fichas", "patron": "^/p/"}]})
    _url(s, j, "/p/1", depth=2, clics=10, imp=100, pos=5.0)
    _url(s, j, "/p/2", depth=2, clics=0, imp=0, pos=None)
    _url(s, j, "/blog/x", depth=12, clics=30, imp=300, pos=2.0)
    resumen, _ = _analizar(s, j)
    fichas = next(f for f in resumen["por_plantilla"] if f["grupo"] == "fichas")
    assert (fichas["indexables"], fichas["con_impresiones"], fichas["clics"], fichas["ctr"]) == (2, 1, 10, 0.1)
    assert [f["grupo"] for f in resumen["por_profundidad"]] == ["2", "10+"]


def test_sin_resumen_de_search_console_no_hace_nada(s):
    c, j = _montar(s)
    j.gsc_resumen = None
    _url(s, j, "/a", clics=20, imp=1000, pos=7.0, pr=1)
    assert _analizar(s, j)[0] is None


def test_las_fuentes_son_del_mismo_idioma(s):
    c, j = _montar(s)
    obj = _url(s, j, "/blog/en/flag", pr=10, clics=20, imp=1000, pos=7.0)
    otro = _url(s, j, "/blog/de/flagge", pr=90)
    mismo = _url(s, j, "/blog/en/tips", pr=80)
    for i in range(10):
        _url(s, j, f"/blog/en/p{i}", pr=20 + i)
    _consulta(s, c, "/blog/en/flag", "flag", 10, 600, 7.0)
    _consulta(s, c, "/blog/de/flagge", "flag", 30, 300, 3.0)
    _consulta(s, c, "/blog/en/tips", "flag", 3, 100, 9.0)
    _, issues = _analizar(s, j)
    fuentes = [f["url"] for f in issues[(obj.id, "oportunidad_posicion")][1]["fuentes"]]
    assert fuentes[0] == mismo.url and otro.url not in fuentes
    assert O.idioma_de_ruta("/blog/en/x") == "en" and O.idioma_de_ruta("/es/hoteles") == "es"
    assert O.idioma_de_ruta("/blog/que-hacer/") == ""


@pytest.mark.parametrize("pos, esperado", [(1.2, "1"), (2.4, "2"), (3.4, "3"), (5.0, "4-5"), (9.9, "6-10"),
                                           (20.4, "11-20"), (21.0, None), (None, None)])
def test_tramos(pos, esperado):
    assert O.tramo(pos) == esperado


def test_marca_sin_acentos_ni_mayusculas():
    assert O.es_de_marca("Tienda ACME online", ["Acmé"])
    assert not O.es_de_marca("zapatos", ["acme"])
    assert O.cubre("sc-domain:x.com", "https://blog.x.com/a") and not O.cubre("sc-domain:x.com", "https://y.com/")
    assert O.cubre("https://www.x.com/", "https://www.x.com/a") and not O.cubre("https://www.x.com/", "https://x.com/a")


def test_idiomas_por_hreflang_y_variantes_no_son_canibalizacion(s):
    from shared.models import Hreflang

    c, j = _montar(s)
    es, en = _url(s, j, "/es/hotel"), _url(s, j, "/en/hotel")
    s.add(Hreflang(url_id=es.id, lang="en", href="https://x.com/en/hotel", en_body=False))
    limpia = _url(s, j, "/es/spa")
    _consulta(s, c, "/es/hotel", "hotel x", 60, 500)
    _consulta(s, c, "/en/hotel", "hotel x", 40, 500)
    _consulta(s, c, "/es/spa?partner=5908", "spa x", 70, 500)    # solo en Search Console
    _consulta(s, c, "/es/spa", "spa x", 30, 500)
    _, issues = _analizar(s, j)
    assert not [t for (_, t) in issues if t == "canibalizacion_por_consulta"]


def test_un_href_imposible_no_tumba_el_analisis_ni_borra_lo_hecho(s):
    from shared.models import Hreflang

    c, j = _montar(s)
    a, b = _url(s, j, "/a"), _url(s, j, "/b")
    s.add(Hreflang(url_id=a.id, lang="en", href="https://[lang].x.com/page", en_body=False))
    s.add(HtmlMeta(url_id=b.id, canonical_href="https://x.com:99999/b"))
    _consulta(s, c, "/a", "q", 60, 500)
    _consulta(s, c, "/b", "q", 40, 500)
    previa = Issue(job_id=j.id, url_id=a.id, issue_type="title_missing", severity="error", details={})
    s.add(previa)
    s.commit()
    an = SEOAnalyzer(s, j.id)
    O.avisar_sin_tumbar(an)
    s.commit()
    tipos = {t for (t,) in s.execute(select(Issue.issue_type).where(Issue.job_id == j.id))}
    assert "title_missing" in tipos and "canibalizacion_por_consulta" in tipos


def test_si_algo_revienta_se_deshace_solo_lo_suyo(s, monkeypatch):
    c, j = _montar(s)
    a = _url(s, j, "/a")
    s.add(Issue(job_id=j.id, url_id=a.id, issue_type="title_missing", severity="error", details={}))
    s.commit()

    def revienta(ctx, consultas):
        ctx.an._add_issue(a.id, "oportunidad_posicion", "info", {})
        ctx.an._flush_issues()
        raise RuntimeError("dato raro")

    monkeypatch.setattr(O, "_oportunidades", revienta)
    an = SEOAnalyzer(s, j.id)
    assert O.avisar_sin_tumbar(an) is None
    s.commit()
    tipos = [t for (t,) in s.execute(select(Issue.issue_type).where(Issue.job_id == j.id))]
    assert tipos == ["title_missing"]


def test_sitemap_enviado_hace_poco_no_es_no_leido(s):
    ficheros = [{"url": "https://x.com/sitemap.xml", "status": 200, "hijos": 3}]
    c, j = _montar(s, sitemaps={"ficheros": ficheros}, detalle={"sitemaps": 1})
    _url(s, j, "/")
    s.add(GscSitemap(cliente_id=c.id, propiedad="sc-domain:x.com", path="https://x.com/sitemap.xml",
                     enviado=datetime(2026, 10, 13, tzinfo=timezone.utc), descargado=None, pendiente=True,
                     errores=0, visto_en=datetime(2026, 10, 14, tzinfo=timezone.utc)))
    resumen, issues = _analizar(s, j)
    assert resumen["sitemaps"] == {"no_enviados": 0, "no_leidos": 0, "con_errores": 0}


def test_una_url_que_solo_esta_en_search_console_no_canibaliza(s):
    c, j = _montar(s)
    nueva = _url(s, j, "/slug-nuevo")
    _consulta(s, c, "/slug-viejo", "zapatos", 50, 500)     # 301 a mitad de ventana, fuera del censo
    _consulta(s, c, "/slug-nuevo", "zapatos", 50, 500)
    _, issues = _analizar(s, j)
    assert not [t for (_, t) in issues if t == "canibalizacion_por_consulta"]


def test_el_cuartil_bajo_del_pagerank_con_empates(s):
    c, j = _montar(s)
    obj = _url(s, j, "/obj", pr=10, clics=20, imp=1000, pos=7.0)
    for i in range(3):
        _url(s, j, f"/e{i}", pr=10)                        # 4 de 12 empatadas en el minimo
    for i in range(8):
        _url(s, j, f"/f{i}", pr=60 + i)
    _, issues = _analizar(s, j)
    assert issues[(obj.id, "oportunidad_posicion")][1]["motivos"] == ["pagerank_bajo"]


def test_la_comparacion_no_afirma_nada_de_los_avisos_de_trafico():
    from shared.comparacion import comparar_incidencias

    a = [{"issue_type": "oportunidad_posicion", "url": "https://x.com/a", "group_key": None, "severity": "info"},
         {"issue_type": "title_missing", "url": "https://x.com/a", "group_key": None, "severity": "error"}]
    b = [{"issue_type": "canibalizacion_por_consulta", "url": "https://x.com/b", "group_key": "k", "severity": "warning"}]
    r = comparar_incidencias(a, b, analisis_a="v", analisis_b="v")
    assert r["nuevas"]["total"] == 0
    assert [f["tipo"] for f in r["resueltas"]["por_tipo"]] == ["title_missing"]


def _pagina_gsc(s, c, ruta, clics, dia=EN_VENTANA):
    from shared.models import GscPagina

    s.add(GscPagina(cliente_id=c.id, propiedad="sc-domain:x.com", fecha=dia, url=f"https://x.com{ruta}",
                    url_clave=clave_de_url(f"https://x.com{ruta}"), clics=clics, impresiones=clics * 10))


def test_la_parte_de_clics_sin_busqueda_se_mide_en_los_dias_de_las_dos_series(s):
    """#163: lo que Google anonimiza se mide en el sitio. Un dia de paginas
    ANTERIOR a la primera busqueda no cuenta: alli no hay con que comparar."""
    c, j = _montar(s, detalle={"sitemaps": 0, "consultas": {"primer_dia": EN_VENTANA.isoformat()}})
    _url(s, j, "/a/", clics=100, imp=1000, pos=5.0)
    _pagina_gsc(s, c, "/a/", 100)
    _pagina_gsc(s, c, "/a/", 900, dia=EN_VENTANA - timedelta(days=1))   # sin busquedas ese dia
    _consulta(s, c, "/a/", "hoteles", 40, 400)
    _consulta(s, c, "/a/", "hotel gran canaria", 20, 200)
    resumen, _ = _analizar(s, j)
    sb = resumen["sin_busqueda"]
    assert (sb["clics_paginas"], sb["clics_busquedas"]) == (100, 60)
    assert sb["parte"] == 0.4 and sb["desde"] == EN_VENTANA.isoformat()
    assert sb["con_dias_cortados"] is False


def test_un_dia_de_busquedas_cortado_se_dice(s):
    c, j = _montar(s, detalle={"sitemaps": 0, "consultas": {"primer_dia": EN_VENTANA.isoformat(),
                                                           "truncados": [EN_VENTANA.isoformat()]}})
    _url(s, j, "/a/", clics=10, imp=100, pos=5.0)
    _pagina_gsc(s, c, "/a/", 10)
    _consulta(s, c, "/a/", "hoteles", 5, 50)
    resumen, _ = _analizar(s, j)
    assert resumen["sin_busqueda"]["con_dias_cortados"] is True


def test_sin_serie_de_busquedas_no_se_afirma_ninguna_parte(s):
    c, j = _montar(s)
    _url(s, j, "/a/", clics=10, imp=100, pos=5.0)
    _pagina_gsc(s, c, "/a/", 10)
    resumen, _ = _analizar(s, j)
    assert resumen["sin_busqueda"] is None
