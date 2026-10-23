"""Los avisos de las decisiones 154, 155 y 156 EJECUTADOS contra SQLite
(decision 74): og:image medida, twitter:card, videos fuera del sitemap de
video y las versiones AMP y movil anunciadas."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from analysis.avisos_de_medios import TIPOS, avisar
from shared.models import (
    Base, HtmlMeta, Issue, Job, Resource, RespuestaSondeada, SitemapEntry, Url,
)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


# Una fila de html_meta medida con el codigo de estas decisiones y sana.
SANA = dict(og_image=None, twitter_card=None, videos=[], amphtml=None, alternate_movil=None)


def _censo():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, HtmlMeta.__table__,
        RespuestaSondeada.__table__, SitemapEntry.__table__, Resource.__table__,
    ])
    s = sessionmaker(bind=engine)()
    # Una lectura de sitemaps completa: sin ella, el aviso de video no se afirma.
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={},
            sitemaps={"ficheros": [], "parcial": False, "entradas_recortadas": False})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, *, status=200, indexable=True, host="x.com", meta=None, **cols):
    u = Url(job_id=j.id, url=f"https://{host}{ruta}", url_hash=f"h:{host}{ruta}", host=host,
            is_internal=True, is_html=True, status_code=status, indexable=indexable, **cols)
    s.add(u)
    s.flush()
    if meta is not False:
        s.add(HtmlMeta(url_id=u.id, **{**SANA, **(meta or {})}))
        s.flush()
    return u


def _sonda(s, j, url, *, uso="og_image", veredicto="ok", ancho=None, alto=None, status=200):
    s.add(RespuestaSondeada(job_id=j.id, url_hash=f"s:{url}", url=url, host="cdn.x.com",
                            uso=uso, interno=False, metodo="GET", status_code=status,
                            veredicto=veredicto, ancho=ancho, alto=alto))
    s.flush()


def _analizar(s, j):
    az = SEOAnalyzer(s, j.id)
    avisar(az)
    filas = {
        tipo: s.execute(
            select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
            .where(Issue.job_id == j.id, Issue.issue_type == tipo)
        ).all()
        for tipo in TIPOS
    }
    return filas, az


def test_una_pagina_sana_no_da_ningun_aviso():
    s, j = _censo()
    _url(s, j, "/a", meta={"og_image": "https://cdn.x.com/og.jpg", "twitter_card": "summary"})
    _sonda(s, j, "https://cdn.x.com/og.jpg", ancho=1200, alto=630)
    filas, az = _analizar(s, j)
    assert not any(filas.values())
    assert az.sondas_resumen["og_image"] == {"comprobadas": 1, "medidas": 1,
                                             "por_veredicto": {"ok": 1}}


# -- og:image (decision 154) -----------------------------------------------------

def test_og_image_pequena_por_plantilla_y_motivo():
    s, j = _censo()
    ids = {}
    for slug in ("zeta", "alfa"):
        ids[slug] = _url(s, j, f"/blog/{slug}",
                         meta={"og_image": "https://cdn.x.com/logo.png"}).id
    _url(s, j, "/tienda/x", meta={"og_image": "https://cdn.x.com/p.jpg",
                                  "twitter_card": "summary_large_image"})
    _sonda(s, j, "https://cdn.x.com/logo.png", ancho=120, alto=60)
    _sonda(s, j, "https://cdn.x.com/p.jpg", ancho=250, alto=250)
    # Sin medir: ni fila de sonda, ni fila sin dimensiones. No se afirma.
    _url(s, j, "/blog/sin-sonda", meta={"og_image": "https://cdn.x.com/otra.jpg"})
    _url(s, j, "/blog/ilegible", meta={"og_image": "https://cdn.x.com/raro.svg"})
    _sonda(s, j, "https://cdn.x.com/raro.svg")
    # Ni en una no indexable.
    _url(s, j, "/blog/noindex", indexable=False, meta={"og_image": "https://cdn.x.com/logo.png"})
    filas = _analizar(s, j)[0]["og_image_pequena"]
    assert len(filas) == 2
    blog = next(f for f in filas if f.details["motivo"] == "menor_de_200x200")
    assert blog.url_id == ids["alfa"] and blog.severity == "info"
    assert blog.details["paginas_afectadas"] == 2
    assert (blog.details["ancho"], blog.details["alto"]) == (120, 60)
    assert blog.details["imagen"] == "https://cdn.x.com/logo.png"
    tienda = next(f for f in filas if f is not blog)
    assert tienda.details["motivo"] == "menor_de_300x157_con_tarjeta_grande"


def test_og_image_rota_y_la_que_ya_tiene_aviso_no_se_repite():
    s, j = _censo()
    a = _url(s, j, "/a", meta={"og_image": "https://cdn.x.com/no.jpg"})
    _sonda(s, j, "https://cdn.x.com/no.jpg", veredicto="roto", status=404)
    # La misma imagen rota cargada por un <img>: ya es broken_resource (36).
    b = _url(s, j, "/otra/b", meta={"og_image": "https://cdn.x.com/img.jpg"})
    _sonda(s, j, "https://cdn.x.com/img.jpg", uso="recurso", veredicto="roto", status=404)
    s.add(Resource(url_id=b.id, resource_url="https://cdn.x.com/img.jpg", resource_type="image"))
    # Un 403 de un WAF no es una imagen rota.
    _url(s, j, "/tercera/c", meta={"og_image": "https://cdn.x.com/waf.jpg"})
    _sonda(s, j, "https://cdn.x.com/waf.jpg", veredicto="no_verificable", status=403)
    s.flush()
    filas = _analizar(s, j)[0]["og_image_rota"]
    assert len(filas) == 1
    assert filas[0].url_id == a.id
    assert filas[0].details["status_code"] == 404
    assert filas[0].details["veredicto"] == "roto"


def test_twitter_card_invalida_agrupada_por_valor():
    s, j = _censo()
    _url(s, j, "/p/1", meta={"twitter_card": "large"})
    _url(s, j, "/p/2", meta={"twitter_card": "large"})
    _url(s, j, "/p/3", meta={"twitter_card": "summary_large_image"})
    _url(s, j, "/p/4", meta={"twitter_card": None})  # sin etiqueta: X usa OG
    filas = _analizar(s, j)[0]["twitter_card_invalida"]
    assert len(filas) == 1
    assert filas[0].details["valor"] == "large"
    assert filas[0].details["paginas_afectadas"] == 2


# -- Videos (decision 155) ----------------------------------------------------------

YT = [{"tipo": "youtube", "id": "AAAAAAAAAAA", "src": None, "decorativo": False}]
FONDO = [{"tipo": "video", "id": None, "src": "https://x.com/bg.mp4", "decorativo": True}]


def test_sin_sitemap_de_video_no_falta_nada():
    s, j = _censo()
    _url(s, j, "/v/1", meta={"videos": YT})
    s.add(SitemapEntry(job_id=j.id, url_hash="h:x.com/v/1", url="https://x.com/v/1",
                       fichero="https://x.com/sitemap.xml", videos=0))
    s.flush()
    assert _analizar(s, j)[0]["video_sin_sitemap_de_video"] == []


def test_video_fuera_del_sitemap_de_video_del_sitio():
    s, j = _censo()
    _url(s, j, "/v/dentro", meta={"videos": YT})
    fuera = _url(s, j, "/v/fuera", meta={"videos": YT + YT})
    _url(s, j, "/v/fondo", meta={"videos": FONDO})        # el fondo animado no es un video
    _url(s, j, "/v/viejo", meta={"videos": None})         # no medido
    _url(s, j, "/v/noindex", indexable=False, meta={"videos": YT})
    s.add(SitemapEntry(job_id=j.id, url_hash="h:x.com/v/dentro", url="https://x.com/v/dentro",
                       fichero="https://x.com/video-sitemap.xml", videos=1))
    s.flush()
    filas = _analizar(s, j)[0]["video_sin_sitemap_de_video"]
    assert len(filas) == 1
    assert filas[0].url_id == fuera.id
    assert filas[0].details["paginas_afectadas"] == 1
    assert filas[0].details["videos"] == {"youtube": 2}


# -- AMP y version movil (decision 156) --------------------------------------------

def test_amp_que_no_apunta_de_vuelta_y_movil_rota():
    s, j = _censo()
    pag = _url(s, j, "/n/1", meta={"amphtml": "https://x.com/amp/n/1",
                                   "alternate_movil": "https://m.x.com/n/1"})
    _url(s, j, "/amp/n/1", meta={"canonical_href": "https://x.com/otra"})
    _url(s, j, "/n/1", host="m.x.com", status=404, meta=False)
    filas = _analizar(s, j)[0]
    amp = filas["amphtml_incorrecto"]
    assert len(amp) == 1 and amp[0].url_id == pag.id
    assert amp[0].severity == "warning"
    assert amp[0].details["motivo"] == "destino_sin_canonical_de_vuelta"
    assert amp[0].details["destino_canonical"] == "https://x.com/otra"
    movil = filas["alternate_movil_incorrecto"]
    assert len(movil) == 1 and movil[0].severity == "warning"
    assert movil[0].details["motivo"] == "destino_error"
    assert movil[0].details["destino_status"] == 404


def test_amp_bien_emparejada_fuera_del_censo_o_no_medida_no_avisa():
    s, j = _censo()
    _url(s, j, "/n/1", meta={"amphtml": "https://x.com/amp/n/1"})
    _url(s, j, "/amp/n/1", meta={"canonical_href": "https://x.com/n/1"})
    # La AMP no esta en el censo: no se juzga.
    _url(s, j, "/n/2", meta={"amphtml": "https://x.com/amp/n/2"})
    # Censo anterior a la columna (videos NULL): no se mira.
    _url(s, j, "/n/3", meta={"videos": None, "amphtml": "https://x.com/amp/n/3"})
    _url(s, j, "/amp/n/3", status=404, meta=False)
    filas = _analizar(s, j)[0]
    assert filas["amphtml_incorrecto"] == []
    assert filas["alternate_movil_incorrecto"] == []


def test_movil_que_redirige_es_info():
    s, j = _censo()
    _url(s, j, "/n/1", meta={"alternate_movil": "https://m.x.com/n/1"})
    _url(s, j, "/n/1", host="m.x.com", status=301, meta=False,
         redirect_url="https://x.com/n/1")
    movil = _analizar(s, j)[0]["alternate_movil_incorrecto"]
    assert len(movil) == 1
    assert movil[0].severity == "info" and movil[0].details["motivo"] == "destino_redirige"


# -- Revision de la PR #218 ----------------------------------------------------------

def test_una_url_que_no_se_puede_normalizar_no_tumba_nada():
    """`misma_url` lanza ValueError con un puerto imposible o un IPv6 sin
    cerrar; antes eso tumbaba `run_all` entero."""
    s, j = _censo()
    _url(s, j, "/n/1", meta={"amphtml": "https://e.com:99999/amp",
                             "alternate_movil": "http://[::1"})
    pag = _url(s, j, "/n/2", meta={"amphtml": "https://x.com/amp/n/2"})
    # El destino existe y su canonical es una URL rota: no apunta de vuelta.
    _url(s, j, "/amp/n/2", meta={"canonical_href": "https://x.com:99999/n/2"})
    filas = _analizar(s, j)[0]
    amp = filas["amphtml_incorrecto"]
    assert len(amp) == 1 and amp[0].url_id == pag.id
    assert amp[0].details["motivo"] == "destino_sin_canonical_de_vuelta"


def test_un_fallo_dentro_no_para_el_analisis_ni_deja_avisos_a_medias(monkeypatch):
    import analysis.avisos_de_medios as m

    s, j = _censo()
    _url(s, j, "/p/1", meta={"twitter_card": "large"})

    def _revienta(*a, **k):
        raise RuntimeError("dato imposible")

    monkeypatch.setattr(m, "_videos", _revienta)
    filas, az = _analizar(s, j)  # no lanza
    assert not any(filas.values())  # la twitter_card ya anotada se descarta
    assert az._pending_issues == []


def test_video_en_un_censo_con_sitemaps_incompletos_no_se_afirma():
    for resumen, motivo in (
        ({"entradas_recortadas": True, "ficheros": []}, "entradas_recortadas"),
        ({"parcial": True, "ficheros": []}, "sitemaps_sin_leer"),
        ({"ficheros": [{"url": "a", "status": 500}]}, "sitemap_fallido"),
        ({"ficheros": [{"url": "a", "status": 200, "cortado": True}]}, "sitemap_cortado"),
        ({"ficheros": [{"url": "a", "status": 200, "formato": "urlset",
                        "declaradas": 60000, "devueltas": 50000}]}, "sitemap_recortado"),
        (None, "sin_resumen_de_sitemaps"),
    ):
        s, j = _censo()
        j.sitemaps = resumen
        _url(s, j, "/v/fuera", meta={"videos": YT})
        _url(s, j, "/v/dentro", meta={"videos": YT})
        s.add(SitemapEntry(job_id=j.id, url_hash="h:x.com/v/dentro", url="https://x.com/v/dentro",
                           fichero="https://x.com/video-sitemap.xml", videos=1))
        s.flush()
        filas, az = _analizar(s, j)
        assert filas["video_sin_sitemap_de_video"] == [], motivo
        assert az.sondas_resumen["sitemap_de_video"] == {"evaluado": False, "motivo": motivo}


def test_reutiliza_las_fichas_de_los_canonicals_y_las_suelta():
    from analysis.analyzer import _Fichas

    s, j = _censo()
    _url(s, j, "/n/1", meta={"amphtml": "https://x.com/amp/n/1"})
    _url(s, j, "/amp/n/1", status=404, meta=False)
    az = SEOAnalyzer(s, j.id)
    fichas = _Fichas(s, j.id)
    usadas = []
    original = fichas.buscar
    fichas.buscar = lambda u: usadas.append(u) or original(u)
    az._fichas_canonicals = fichas
    avisar(az)
    assert usadas == ["https://x.com/amp/n/1"]
    assert not hasattr(az, "_fichas_canonicals")
