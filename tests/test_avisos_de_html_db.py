"""Los avisos de la decision 108 EJECUTADOS contra SQLite (decision 74).

Viewport, `<html lang>` y su cruce con el hreflang, charset, forma del canonical
y de la og:image, Open Graph y la redireccion por JavaScript.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from analysis.avisos_de_html import TIPOS, avisar_declaraciones_html
from shared.models import Base, Hreflang, HtmlMeta, Issue, Job, Url
from shared.respuestas import CHALLENGE_WAF


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


# Una fila de html_meta como la deja un rastreo sin render con el codigo de
# la decision 108: todo medido y correcto. Cada test cambia lo que prueba.
SANA = dict(
    viewport="width=device-width, initial-scale=1", viewport_count=1, html_lang="es",
    og_title="T", og_image="https://x.com/og.jpg", og_image_forma="absoluta",
    canonical_crudo=None, canonical_href=None,
    charset_meta="utf-8", charset_bom=None, cuerpo_solo_ascii=False,
    texto_mal_codificado=False,
)


def _censo(render_js: bool = False):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, HtmlMeta.__table__,
        Hreflang.__table__,
    ])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing",
            seeds=["https://x.com/"], config={"render_js": render_js})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, *, status=200, indexable=True, interna=True, host="x.com",
         content_type="text/html; charset=utf-8", meta=None, **cols):
    u = Url(job_id=j.id, url=f"https://{host}{ruta}", url_hash=f"{host}{ruta}", host=host,
            is_internal=interna, is_html=True, status_code=status, indexable=indexable,
            content_type=content_type, **cols)
    s.add(u)
    s.flush()
    if meta is not False:
        s.add(HtmlMeta(url_id=u.id, **{**SANA, **(meta or {})}))
        s.flush()
    return u


def _analizar(s, j):
    avisar_declaraciones_html(SEOAnalyzer(s, j.id))
    return {
        tipo: s.execute(
            select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
            .where(Issue.job_id == j.id, Issue.issue_type == tipo)
        ).all()
        for tipo in TIPOS
    }


def test_una_pagina_sana_no_da_ningun_aviso():
    s, j = _censo()
    _url(s, j, "/a")
    assert not any(_analizar(s, j).values())


# -- Viewport ------------------------------------------------------------------

def test_viewport_ausente_agrupado_por_plantilla_y_anclado_en_la_url_menor():
    s, j = _censo()
    # Se crean en desorden: el ancla es la URL menor, no el id menor (88).
    ids = {}
    for slug in ("zeta", "beta", "alfa"):
        ids[slug] = _url(s, j, f"/blog/{slug}", meta={"viewport": None, "viewport_count": 0}).id
    _url(s, j, "/tienda/x", meta={"viewport": None, "viewport_count": 0})
    # No cuentan: no indexable, 404, externa, y un censo viejo (NULL = no medido).
    _url(s, j, "/blog/noindex", indexable=False, meta={"viewport": None, "viewport_count": 0})
    _url(s, j, "/blog/404", status=404, meta={"viewport": None, "viewport_count": 0})
    _url(s, j, "/blog/ext", interna=False, meta={"viewport": None, "viewport_count": 0})
    _url(s, j, "/blog/viejo", meta={"viewport": None, "viewport_count": None})

    filas = _analizar(s, j)["viewport_ausente"]
    assert len(filas) == 2  # una por plantilla
    blog = next(f for f in filas if f.details["paginas_afectadas"] == 3)
    assert blog.url_id == ids["alfa"]
    assert blog.severity == "warning"
    assert blog.details["paginas_ejemplo"] == [
        "https://x.com/blog/alfa", "https://x.com/blog/beta", "https://x.com/blog/zeta"]
    assert blog.group_key


def test_viewport_incorrecto_por_motivo_y_user_scalable_no_cuenta():
    s, j = _censo()
    _url(s, j, "/p/1", meta={"viewport": "width=1024"})
    _url(s, j, "/p/2", meta={"viewport": "width=1024", "viewport_count": 2})
    _url(s, j, "/p/3", meta={"viewport": "width=device-width, user-scalable=no"})
    filas = _analizar(s, j)["viewport_incorrecto"]
    assert len(filas) == 1
    assert filas[0].details["motivo"] == "ancho_fijo"
    assert filas[0].details["paginas_afectadas"] == 2
    assert filas[0].details["con_varios_viewports"] == 1


# -- <html lang> y hreflang ----------------------------------------------------

def test_html_lang_ausente_info_y_no_medido_no_cuenta():
    s, j = _censo()
    _url(s, j, "/a", meta={"html_lang": None})
    _url(s, j, "/b", meta={"html_lang": ""})
    _url(s, j, "/c", meta={"html_lang": None, "viewport_count": None})  # censo viejo
    filas = _analizar(s, j)["html_lang_ausente"]
    assert len(filas) == 1
    assert filas[0].severity == "info"
    assert filas[0].details["paginas_afectadas"] == 2
    assert filas[0].details["atributo_vacio"] == 1


def _hreflang(s, u, lang, href=None, en_body=False):
    s.add(Hreflang(url_id=u.id, lang=lang, href=href or u.url, en_body=en_body))
    s.flush()


def test_lang_que_no_coincide_con_la_autorreferencia_del_hreflang():
    s, j = _censo()
    mal = _url(s, j, "/en/a", meta={"html_lang": "es"})
    _hreflang(s, mal, "en-GB")
    _hreflang(s, mal, "es-ES", "https://x.com/es/a")       # no es la autorreferencia
    _hreflang(s, mal, "x-default", "https://x.com/en/a")
    # Misma lengua con otra region: no es un hallazgo.
    mx = _url(s, j, "/mx/a", meta={"html_lang": "es-MX"})
    _hreflang(s, mx, "es-ES")
    # Una fuente noindex no cuenta.
    noindex = _url(s, j, "/en/b", indexable=False, meta={"html_lang": "es"})
    _hreflang(s, noindex, "en")
    # El hreflang del body Google lo ignora.
    body = _url(s, j, "/en/c", meta={"html_lang": "es"})
    _hreflang(s, body, "en", en_body=True)
    # Varios codigos para si misma, y uno coincide: nada.
    doble = _url(s, j, "/en/d", meta={"html_lang": "es"})
    _hreflang(s, doble, "en")
    _hreflang(s, doble, "es")

    filas = _analizar(s, j)["html_lang_no_coincide_hreflang"]
    assert [f.url_id for f in filas] == [mal.id]
    assert filas[0].severity == "info"
    assert filas[0].details["html_lang"] == "es"
    assert filas[0].details["hreflang"] == "en"


# -- Charset -------------------------------------------------------------------

def test_texto_mal_codificado_por_plantilla_con_la_causa_en_el_detalle():
    s, j = _censo()
    _url(s, j, "/f/1", meta={"texto_mal_codificado": True, "charset_meta": ""})
    _url(s, j, "/f/2", meta={"texto_mal_codificado": True, "charset_meta": ""})
    avisos = _analizar(s, j)
    filas = avisos["texto_mal_codificado"]
    assert len(filas) == 1 and filas[0].severity == "warning"
    assert filas[0].details["charset_cabecera"] == "utf-8"
    assert filas[0].details["paginas_afectadas"] == 2
    # Un aviso por hallazgo: no sale tambien como incoherente ni ausente.
    assert not avisos["charset_incoherente"] and not avisos["charset_ausente"]


def test_charset_incoherente_por_host_y_ausente_solo_con_no_ascii():
    s, j = _censo()
    for i in range(3):
        _url(s, j, f"/a/{i}/x", meta={"charset_meta": "cp1252"})          # cabecera utf-8
    _url(s, j, "/b", content_type="text/html", meta={"charset_meta": ""},
         host="otro.x.com")
    _url(s, j, "/c", content_type="text/html", meta={"charset_meta": "", "cuerpo_solo_ascii": True},
         host="ascii.x.com")
    # latin1 frente a windows-1252: la misma (se normaliza en el spider).
    _url(s, j, "/d", content_type="text/html; charset=latin1", meta={"charset_meta": "cp1252"},
         host="latin.x.com")
    # Con render, o censo viejo: NULL, no medido.
    _url(s, j, "/e", content_type="text/html", meta={"charset_meta": None}, host="render.x.com")
    avisos = _analizar(s, j)
    inco = avisos["charset_incoherente"]
    assert len(inco) == 1
    assert inco[0].details["paginas_afectadas"] == 3
    assert inco[0].details["manda"] == "cabecera"
    assert inco[0].details["charset_meta"] == "cp1252"
    aus = avisos["charset_ausente"]
    assert [a.details["paginas_ejemplo"] for a in aus] == [["https://otro.x.com/b"]]
    assert aus[0].severity == "info"


# -- Canonical y og:image ------------------------------------------------------

def test_veinte_canonicals_relativos_dan_una_fila_info():
    s, j = _censo()
    for i in range(20):
        _url(s, j, f"/p/slug-{i}", meta={"canonical_crudo": f"/p/slug-{i}",
                                         "canonical_href": f"https://x.com/p/slug-{i}"})
    _url(s, j, "/p/abs", meta={"canonical_crudo": "https://x.com/p/abs",
                               "canonical_href": "https://x.com/p/abs"})
    filas = _analizar(s, j)["canonical_relativo"]
    assert len(filas) == 1
    assert filas[0].severity == "info"
    assert filas[0].details["forma"] == "relativa_a_raiz"
    assert filas[0].details["paginas_afectadas"] == 20


def test_canonical_con_fragmento_aviso_si_apunta_a_otra_pagina():
    s, j = _censo()
    _url(s, j, "/a", meta={"canonical_crudo": "https://x.com/a#top",
                           "canonical_href": "https://x.com/a#top"})
    _url(s, j, "/producto/1", meta={"canonical_crudo": "/#/producto/1",
                                    "canonical_href": "https://x.com/#/producto/1"})
    filas = {f.details["apunta_a_si_misma"]: f
             for f in _analizar(s, j)["canonical_con_fragmento"]}
    assert filas[True].severity == "info"
    assert filas[False].severity == "warning"


def test_og_ausente_solo_indexables_y_og_relativa():
    s, j = _censo()
    for i in range(30):
        _url(s, j, f"/ficha/{i}-x", meta={"og_title": None, "og_image": None,
                                          "og_image_forma": None})
    _url(s, j, "/ficha/noindex", indexable=False,
         meta={"og_title": None, "og_image": None, "og_image_forma": None})
    _url(s, j, "/solo-titulo", meta={"og_image": None, "og_image_forma": None})
    _url(s, j, "/rel", meta={"og_image": "https://x.com/i.jpg", "og_image_forma": "relativa"})
    avisos = _analizar(s, j)
    sin = avisos["og_ausente"]
    assert len(sin) == 1
    assert sin[0].details["paginas_afectadas"] == 30
    assert sin[0].severity == "info"
    rel = avisos["og_image_relativa"]
    assert len(rel) == 1 and rel[0].details["forma"] == "relativa"


def test_og_ausente_tambien_en_censos_viejos():
    """og_title y og_image se guardan desde siempre: no hace falta la marca."""
    s, j = _censo()
    _url(s, j, "/a", meta={"og_title": None, "og_image": None, "og_image_forma": None,
                           "viewport_count": None, "charset_meta": None})
    assert len(_analizar(s, j)["og_ausente"]) == 1


# -- Redireccion por JavaScript ------------------------------------------------

def test_redireccion_js_por_pagina_con_el_destino():
    s, j = _censo()
    puente = _url(s, j, "/puente", content_word_count=3,
                  meta={"redireccion_js": "https://x.com/destino"})
    _url(s, j, "/destino")
    otra = _url(s, j, "/puente2", meta={"redireccion_js": "https://x.com/no-rastreada"})
    # Una meta refresh con destino ya es una redireccion: no se repite.
    _url(s, j, "/mr", indexability_status="Redirect (meta refresh)",
         meta={"redireccion_js": "https://x.com/destino"})
    filas = {f.url_id: f for f in _analizar(s, j)["redireccion_por_javascript"]}
    assert set(filas) == {puente.id, otra.id}
    d = filas[puente.id].details
    assert d["destino"] == "https://x.com/destino"
    assert d["destino_status"] == 200
    assert d["palabras"] == 3
    assert d["fuente"] == "html_crudo"
    assert filas[puente.id].severity == "info"
    assert filas[puente.id].group_key is None
    assert filas[otra.id].details["destino_no_comprobado"] is True


def test_un_muro_no_lleva_avisos_de_pagina():
    s, j = _censo()
    _url(s, j, "/muro", response_class=CHALLENGE_WAF,
         meta={"viewport": None, "viewport_count": 0, "og_title": None, "og_image": None})
    assert not any(_analizar(s, j).values())


def test_run_all_los_emite():
    """El check esta enchufado al analisis completo, no solo a mano."""
    s, j = _censo()
    _url(s, j, "/", meta={"viewport": None, "viewport_count": 0})
    analizador = SEOAnalyzer(s, j.id)
    for nombre in dir(analizador):
        if (nombre.startswith(("analyze_", "compute_")) and nombre not in (
                "analyze_indexability",)):
            setattr(analizador, nombre, lambda *a, **k: None)
    import analysis.analyzer as modulo

    original = modulo.avisos_de_enlaces.avisar_enlaces_no_rastreables
    modulo.avisos_de_enlaces.avisar_enlaces_no_rastreables = lambda a: None
    try:
        analizador.run_all()
    finally:
        modulo.avisos_de_enlaces.avisar_enlaces_no_rastreables = original
    tipos = {t for (t,) in s.execute(select(Issue.issue_type).where(Issue.job_id == j.id))}
    assert "viewport_ausente" in tipos
