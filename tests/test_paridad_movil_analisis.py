"""Paridad movil: la comparacion (`shared/paridad.py`), los perfiles
(`shared/perfiles.py`) y los avisos del analizador contra SQLite (decision 119).

Todo se EJECUTA (decision 74); lo unico que lee un fuente es el test que
comprueba que la cadena de Googlebot smartphone del formulario es la de
`shared/perfiles.py`, que es justo lo que tiene que decir lo mismo en dos sitios.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared import paridad, perfiles
from shared.models import Base, CapturaAlternativa, Issue, Job, Url

RAIZ = Path(__file__).resolve().parent.parent


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


# -- La comparacion -------------------------------------------------------------

def _captura(**kw):
    datos = {"url": "https://x.com/a", "title": "T", "h1": "H", "description": "D",
             "canonical": None, "noindex": False, "nofollow": False, "palabras": 300,
             "enlaces": [f"https://x.com/{i}" for i in range(20)]}
    datos.update(kw)
    return datos


def test_iguales_salvo_espacios_no_difieren():
    assert paridad.diferencias(_captura(title="  Hola\n mundo "), _captura(title="Hola mundo")) == []


def test_canonical_relativo_frente_a_absoluto_es_el_mismo():
    base = _captura(canonical="https://x.com/a")
    assert paridad.diferencias(base, _captura(canonical="/a")) == []
    # sin canonical es autocanonica: lo mismo que apuntar a si misma
    assert paridad.diferencias(base, _captura(canonical=None)) == []
    [d] = paridad.diferencias(base, _captura(canonical="/b"))
    assert d == {"campo": "canonical", "base": "https://x.com/a", "alternativa": "https://x.com/b"}


def test_robots_por_directiva_no_por_cadena():
    # el orden y las mayusculas de la cadena no importan: se comparan los booleanos
    assert paridad.diferencias(_captura(noindex=True), _captura(noindex=True)) == []
    [d] = paridad.diferencias(_captura(), _captura(noindex=True))
    assert (d["campo"], d["base"], d["alternativa"]) == ("noindex", False, True)


def test_palabras_con_umbral_y_base_minima():
    assert paridad.diferencias(_captura(palabras=300), _captura(palabras=220)) == []  # 27%
    [d] = paridad.diferencias(_captura(palabras=300), _captura(palabras=200))
    assert d["campo"] == "palabras"
    # Por debajo de PALABRAS_MINIMAS, un 50% no dice nada
    assert paridad.diferencias(_captura(palabras=40), _captura(palabras=20)) == []
    # No medido no es cero
    assert paridad.diferencias(_captura(palabras=300), _captura(palabras=None)) == []


def test_enlaces_que_faltan_con_umbral():
    base = _captura()
    # faltan 4 de 20: por debajo del minimo de 5
    assert paridad.diferencias(base, _captura(enlaces=base["enlaces"][4:])) == []
    [d] = paridad.diferencias(base, _captura(enlaces=base["enlaces"][8:] + ["https://x.com/nuevo"]))
    assert (d["campo"], d["base"], d["alternativa"], d["faltan"], d["sobran"]) == (
        "enlaces", 20, 13, 8, 1)
    assert len(d["ejemplos"]) == 8


def test_vary():
    assert paridad.vary_por_user_agent("Accept-Encoding, User-Agent")
    assert paridad.vary_por_user_agent("*")
    assert not paridad.vary_por_user_agent("Accept-Encoding")
    assert not paridad.vary_por_user_agent(None)


# -- Los perfiles -----------------------------------------------------------------

def test_es_ua_movil():
    assert perfiles.es_ua_movil(perfiles.UA_GOOGLEBOT_SMARTPHONE)
    assert perfiles.es_ua_movil(perfiles.UA_CHROME_ANDROID)
    assert perfiles.es_ua_movil("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)")
    assert not perfiles.es_ua_movil(perfiles.UA_CHROME_ESCRITORIO)
    assert not perfiles.es_ua_movil("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)")
    # Android sin Mobile es una tablet: escritorio para Google
    assert not perfiles.es_ua_movil("Mozilla/5.0 (Linux; Android 13; SM-X700) Chrome/120 Safari/537.36")
    assert perfiles.perfil_de_ua(None) == "escritorio"


def test_el_preset_del_formulario_es_el_de_shared():
    app = (RAIZ / "frontend" / "app.js").read_text(encoding="utf-8")
    version = re.search(r"const CHROME_EVERGREEN = '([^']+)'", app).group(1)
    cadena = re.search(r"googlebot_mobile: `([^`]+)`", app).group(1)
    assert version == perfiles.CHROME_EVERGREEN
    assert cadena.replace("${CHROME_EVERGREEN}", version) == perfiles.UA_GOOGLEBOT_SMARTPHONE


def test_contexto_playwright_conserva_locale_y_cambia_el_dispositivo():
    ctx = perfiles.contexto_playwright(
        perfiles.UA_CHROME_ANDROID, True,
        base={"locale": "es-ES", "java_script_enabled": True, "user_agent": "x",
              "viewport": {"width": 1280, "height": 720}})
    assert ctx["locale"] == "es-ES" and ctx["java_script_enabled"] is True
    assert ctx["is_mobile"] and ctx["has_touch"] and ctx["viewport"]["width"] == 412
    assert ctx["user_agent"] == perfiles.UA_CHROME_ANDROID


def test_el_contexto_custom_lleva_el_ua_del_job():
    """El `-s USER_AGENT` del worker cambia el contexto de render (antes no)."""
    pytest.importorskip("scrapy")
    pytest.importorskip("scrapy_playwright")
    import sys

    sys.path.insert(0, str(RAIZ / "crawler"))
    from scrapy.settings import Settings

    from seo_crawler.spiders.seo_spider import SeoSpider

    ajustes = Settings()
    ajustes.set("PLAYWRIGHT_CONTEXTS", {
        "default": {"locale": "es-ES"},
        "custom": {"user_agent": "viejo", "viewport": {"width": 1280, "height": 720},
                   "locale": "es-ES"}}, priority="project")
    ajustes.set("USER_AGENT", perfiles.UA_GOOGLEBOT_SMARTPHONE, priority="cmdline")
    SeoSpider.update_settings(ajustes)
    custom = ajustes.getdict("PLAYWRIGHT_CONTEXTS")["custom"]
    assert custom["user_agent"] == perfiles.UA_GOOGLEBOT_SMARTPHONE
    assert custom["is_mobile"] and custom["viewport"]["width"] == 412
    assert custom["locale"] == "es-ES"

    # Sin UA por -s (prioridad menor que la de settings.py), no se toca
    ajustes = Settings()
    ajustes.set("PLAYWRIGHT_CONTEXTS", {"custom": {"user_agent": "viejo"}}, priority="project")
    SeoSpider.update_settings(ajustes)
    assert ajustes.getdict("PLAYWRIGHT_CONTEXTS")["custom"] == {"user_agent": "viejo"}


# -- El analizador --------------------------------------------------------------

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


def _captura_bd(s, j, u, *, estado="comparada", plantilla="/ficha/:slug (2n)", difs=(),
                vary=None, **kw):
    s.add(CapturaAlternativa(job_id=j.id, url_id=u.id, perfil="movil", modo="sin_render",
                             estado=estado, plantilla=plantilla, diferencias=list(difs),
                             vary=vary, vary_base=vary, **kw))
    s.flush()


def _analizar(s, j):
    az = SEOAnalyzer(s, j.id)
    az.analyze_paridad_movil()
    s.flush()
    return s.execute(select(Issue.url_id, Issue.issue_type, Issue.severity, Issue.details,
                            Issue.group_key).where(Issue.job_id == j.id)).all()


NOINDEX = {"campo": "noindex", "base": False, "alternativa": True}


def test_una_fila_por_plantilla_y_campo_anclada_en_la_url_menor():
    s, j = _montar()
    b = _pagina(s, j, "/ficha/b")
    a = _pagina(s, j, "/ficha/a")
    c = _pagina(s, j, "/ficha/c")
    _captura_bd(s, j, b, difs=[NOINDEX, {"campo": "title", "base": "T", "alternativa": "M"}])
    _captura_bd(s, j, a, difs=[NOINDEX])
    _captura_bd(s, j, c)  # muestreada, sin diferencias
    filas = {f.issue_type: f for f in _analizar(s, j)}
    assert set(filas) == {"paridad_movil_indexacion", "paridad_movil_contenido"}
    idx = filas["paridad_movil_indexacion"]
    assert (idx.url_id, idx.severity) == (a.id, "error")
    assert idx.details["paginas_afectadas"] == 2 and idx.details["paginas_muestreadas"] == 3
    assert idx.details["sin_vary_user_agent"] == 2
    assert [e["url"] for e in idx.details["ejemplos"]] == ["https://x.com/ficha/a", "https://x.com/ficha/b"]
    tit = filas["paridad_movil_contenido"]
    assert (tit.url_id, tit.severity, tit.details["campo"]) == (b.id, "warning", "title")
    assert idx.group_key and tit.group_key and idx.group_key != tit.group_key


def test_severidad_por_consecuencia():
    s, j = _montar()
    casos = {"canonical": "error", "nofollow": "warning", "h1": "warning",
             "description": "info", "palabras": "warning", "enlaces": "warning"}
    for i, campo in enumerate(casos):
        u = _pagina(s, j, f"/p{i}/x")
        _captura_bd(s, j, u, plantilla=f"/p{i}/:slug (2n)",
                    difs=[{"campo": campo, "base": 1, "alternativa": 2}])
    obtenido = {f.details["campo"]: f.severity for f in _analizar(s, j)}
    assert obtenido == casos


def test_redireccion_a_m_dot_anotada_es_info_y_sin_anotar_aviso():
    s, j = _montar()
    a = _pagina(s, j, "/a/1")
    b = _pagina(s, j, "/b/1")
    bien = {"tipo": "otro_host", "destino": {"canonical_a_base": True, "alternate_al_destino": True}}
    mal = {"tipo": "otro_host", "destino": {"canonical_a_base": True, "alternate_al_destino": False}}
    _captura_bd(s, j, a, estado="redirige", plantilla="/a/:slug (2n)", status_code=302,
                redirige_a="https://m.x.com/a/1", detalle=bien)
    _captura_bd(s, j, b, estado="redirige", plantilla="/b/:slug (2n)", status_code=302,
                redirige_a="https://m.x.com/b/1", detalle=mal)
    filas = {f.url_id: f for f in _analizar(s, j)}
    assert (filas[a.id].issue_type, filas[a.id].severity) == ("paridad_movil_respuesta", "info")
    assert filas[a.id].details["campo"] == "urls_separadas"
    assert (filas[b.id].issue_type, filas[b.id].severity) == ("paridad_movil_respuesta", "warning")
    assert filas[b.id].details["ejemplos"][0]["alternativa"] == "https://m.x.com/b/1"


def test_lo_que_no_se_pudo_medir_no_avisa():
    s, j = _montar()
    a = _pagina(s, j, "/a/1")
    _captura_bd(s, j, a, estado="no_evaluada",
                detalle={"motivo": "control_fallido", "diferencias_sin_control": [NOINDEX]})
    # la pagina del censo es ahora un muro: su comparacion ya no vale
    m = _pagina(s, j, "/m/1", clase="challenge_waf")
    _captura_bd(s, j, m, difs=[NOINDEX])
    v = _pagina(s, j, "/v/1")
    _captura_bd(s, j, v, difs=[], volatiles=["title"])
    assert _analizar(s, j) == []


def test_sin_tabla_no_se_evalua():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Job.__table__, Url.__table__, Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=[], config={})
    s.add(j)
    s.flush()
    SEOAnalyzer(s, j.id).analyze_paridad_movil()


# -- La comparacion entre censos ------------------------------------------------

def test_dos_censos_con_perfil_distinto_se_avisa():
    from types import SimpleNamespace

    from shared.comparacion import comparar_censos, resumen_de_job

    def _job(ua, fecha):
        return {**resumen_de_job(SimpleNamespace(
            seeds=["https://x.com/"], status="completed", finish_reason="finished",
            crawler_version="abc", name="c", started_at=None,
            config={"user_agent": ua} if ua else {})), "fecha": fecha}

    pag = [{"url": "https://x.com/a", "status_code": 200, "indexability_status": "Indexable",
            "canonical_href": None, "title": "T", "h1": "H", "word_count": 500}]
    movil = _job(perfiles.UA_GOOGLEBOT_SMARTPHONE, "2026-02-01T00:00:00")
    escritorio = _job(None, "2026-01-01T00:00:00")
    assert movil["perfil"] == "movil" and escritorio["perfil"] == "escritorio"
    r = comparar_censos(pag, pag, job_a=escritorio, job_b=movil)
    assert any("perfil movil" in a for a in r["avisos"]), r["avisos"]
    r = comparar_censos(pag, pag, job_a=escritorio, job_b={**escritorio, "fecha": "2026-03-01"})
    assert not any("perfil" in a for a in r["avisos"])


def test_m_dot_cuyo_destino_es_un_muro_o_un_error_no_avisa_de_anotacion():
    """Si el destino del m-dot es un muro (decision 103) o no se pudo pedir,
    la anotacion no se sabe: no es un m-dot «sin anotar» (revision de #161)."""
    s, j = _montar()
    a = _pagina(s, j, "/a/1")
    b = _pagina(s, j, "/b/1")
    muro = {"tipo": "otro_host", "alternate_en_base": "https://m.x.com/a/1",
            "destino": {"url": "https://m.x.com/a/1", "status": 403, "clase": "challenge_waf"}}
    error = {"tipo": "otro_host",
             "destino": {"url": "https://m.x.com/b/1", "error": "bloqueada_por_robots"}}
    _captura_bd(s, j, a, estado="redirige", plantilla="/a/:slug (2n)", status_code=302,
                redirige_a="https://m.x.com/a/1", detalle=muro)
    _captura_bd(s, j, b, estado="redirige", plantilla="/b/:slug (2n)", status_code=302,
                redirige_a="https://m.x.com/b/1", detalle=error)
    assert _analizar(s, j) == []
