"""El analisis de la respuesta frente al render (decision 140), EJECUTADO
contra SQLite: una fila por (plantilla, tipo), severidad por consecuencia, lo
no evaluado no avisa, y la paridad movil no se mezcla con esto."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis import avisos_de_render
from analysis.analyzer import SEOAnalyzer
from shared.models import Base, CapturaAlternativa, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, CapturaAlternativa.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _pagina(s, j, ruta, *, status=200, clase="contenido"):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
            is_html=True, status_code=status, host="x.com", resource_type="html",
            response_class=clase, crawl_depth=1)
    s.add(u)
    s.flush()
    return u


def _captura(s, j, u, *, estado="comparada", perfil="crudo", plantilla="/ficha/:slug", difs=(), **kw):
    s.add(CapturaAlternativa(job_id=j.id, url_id=u.id, perfil=perfil,
                             modo="sin_render" if perfil == "crudo" else "render",
                             estado=estado, plantilla=plantilla, diferencias=list(difs), **kw))
    s.flush()


def _analizar(s, j, *, movil=False):
    az = SEOAnalyzer(s, j.id)
    avisos_de_render.avisar(az)
    if movil:
        az.analyze_paridad_movil()
    s.flush()
    return s.execute(select(Issue.url_id, Issue.issue_type, Issue.severity, Issue.details)
                     .where(Issue.job_id == j.id)).all()


NOINDEX_CRUDO = {"campo": "noindex", "render": False, "crudo": True}


def test_noindex_solo_en_el_html_del_servidor_es_error_y_va_por_plantilla():
    s, j = _montar()
    b, a, c = (_pagina(s, j, f"/ficha/{x}") for x in "bac")
    _captura(s, j, b, difs=[NOINDEX_CRUDO])
    _captura(s, j, a, difs=[NOINDEX_CRUDO, {"campo": "title", "render": "R", "crudo": "C"}])
    _captura(s, j, c)  # muestreada y sin diferencias
    filas = {f.issue_type: f for f in _analizar(s, j)}
    assert set(filas) == {"noindex_solo_en_crudo", "cambia_al_renderizar"}
    n = filas["noindex_solo_en_crudo"]
    assert (n.url_id, n.severity) == (a.id, "error")              # la URL menor
    assert (n.details["paginas_afectadas"], n.details["paginas_muestreadas"]) == (2, 3)
    assert filas["cambia_al_renderizar"].severity == "info"


def test_severidad_por_consecuencia():
    s, j = _montar()
    casos = {
        "/a/x": [{"campo": "noindex", "render": True, "crudo": False}],
        "/b/x": [{"campo": "canonical", "render": "https://x.com/o", "crudo": "https://x.com/b/x"}],
        "/c/x": [{"campo": "nofollow", "render": True, "crudo": False}],
        "/d/x": [{"campo": "enlaces", "render": 12, "crudo": 0, "faltan": 12, "sobran": 0}],
        "/e/x": [{"campo": "palabras", "render": 400, "crudo": 10}],
        "/f/x": [{"campo": "palabras", "render": 10, "crudo": 400}],
    }
    for ruta, difs in casos.items():
        _captura(s, j, _pagina(s, j, ruta), plantilla=ruta, difs=difs)
    _captura(s, j, _pagina(s, j, "/g/x"), plantilla="/g/x", estado="respuesta_distinta", status_code=302,
             redirige_a="https://x.com/login")
    vistos = {(f.details["plantilla"], f.issue_type, f.severity) for f in _analizar(s, j)}
    assert vistos == {
        ("/a/x", "noindex_solo_al_renderizar", "warning"),
        ("/b/x", "canonical_cambia_al_renderizar", "warning"),
        ("/c/x", "nofollow_cambia_al_renderizar", "warning"),
        ("/d/x", "contenido_solo_con_js", "warning"),
        ("/e/x", "contenido_solo_con_js", "warning"),
        ("/f/x", "cambia_al_renderizar", "info"),
        ("/g/x", "respuesta_cambia_al_renderizar", "warning"),
    }


def test_lo_que_no_se_pudo_medir_no_avisa():
    s, j = _montar()
    _captura(s, j, _pagina(s, j, "/a/1"), estado="no_evaluada", difs=[NOINDEX_CRUDO])
    _captura(s, j, _pagina(s, j, "/a/2", status=404), difs=[NOINDEX_CRUDO])
    _captura(s, j, _pagina(s, j, "/a/3", clase="challenge_waf"), difs=[NOINDEX_CRUDO])
    assert _analizar(s, j) == []


def test_la_paridad_movil_y_el_render_no_se_mezclan():
    s, j = _montar()
    u = _pagina(s, j, "/ficha/a")
    _captura(s, j, u, difs=[NOINDEX_CRUDO])
    _captura(s, j, u, perfil="movil", difs=[{"campo": "noindex", "base": False, "alternativa": True}])
    tipos = sorted(f.issue_type for f in _analizar(s, j, movil=True))
    assert tipos == ["noindex_solo_en_crudo", "paridad_movil_indexacion"]
