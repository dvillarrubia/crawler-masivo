"""Avisos del servidor (decisiones 109 y 110), EJECUTANDO el analizador.

`respuesta_lenta`, `html_sin_comprimir` y los tres de TLS contra SQLite: una
fila por (host, plantilla) o por host, anclada donde no cambia entre
analisis, y nada de lo que no se puede afirmar (decision 74: leer el fuente no
vale).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis import avisos_de_servidor
from analysis.analyzer import SEOAnalyzer
from shared.models import Base, HtmlMeta, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _censo(config: dict | None = None, tls: dict | None = None):
    engine = create_engine("sqlite://")
    # `html_meta` la lee `run_all` en los avisos de la decision 108.
    Base.metadata.create_all(engine, tables=[Job.__table__, Url.__table__, Issue.__table__,
                                             HtmlMeta.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config=config or {}, tls=tls)
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, *, host="x.com", status=200, html=True, interna=True,
         esquema="https", **cols):
    u = Url(job_id=j.id, url=f"{esquema}://{host}{ruta}", url_hash=f"{host}{ruta}",
            host=host, path=ruta, scheme=esquema, is_internal=interna, is_html=html,
            status_code=status, **cols)
    s.add(u)
    s.flush()
    return u


def _issues(s, j, tipo):
    return s.execute(
        select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
        .where(Issue.job_id == j.id, Issue.issue_type == tipo)
    ).all()


def _analizar(s, j):
    an = SEOAnalyzer(s, j.id)
    avisos_de_servidor.avisar(an)
    return an


# -- respuesta_lenta -----------------------------------------------------

def test_plantilla_lenta_da_una_fila_info_con_mediana_y_ejemplos():
    s, j = _censo()
    for i in range(25):
        _url(s, j, f"/blog/post-{i}", ttfb_ms=1400 + i * 10)  # mediana 1.520
    for i in range(25):
        _url(s, j, f"/tienda/producto-{i}", ttfb_ms=200)
    _analizar(s, j)
    filas = _issues(s, j, "respuesta_lenta")
    assert len(filas) == 1
    url_id, severidad, det, clave = filas[0]
    assert severidad == "info"
    assert det["plantilla"] == "/blog/:slug (2n)" and det["host"] == "x.com"
    assert det["paginas"] == 25 and det["mediana_ms"] == 1520.0
    assert det["p90_ms"] >= det["mediana_ms"] and det["fuente"] == "curl"
    assert len(det["paginas_ejemplo"]) == 5
    assert det["paginas_ejemplo"][0] == "https://x.com/blog/post-24"  # la mas lenta
    # Anclada en la URL menor del grupo, no en el primer id
    menor = s.execute(select(Url.id).where(Url.url == "https://x.com/blog/post-0")).scalar()
    assert url_id == menor and clave


def test_muy_lenta_es_warning_y_las_reglas_del_job_mandan():
    s, j = _censo({"templates": [{"nombre": "ficha", "patron": r"^/p/"}],
                   "render_js": True})
    for i in range(20):
        _url(s, j, f"/p/{i}/x", ttfb_ms=3500)
    _analizar(s, j)
    (fila,) = _issues(s, j, "respuesta_lenta")
    assert fila.severity == "warning"
    assert fila.details["plantilla"] == "ficha" and fila.details["fuente"] == "navegador"


def test_pocas_paginas_muros_redirecciones_y_censos_viejos_no_cuentan():
    s, j = _censo()
    for i in range(10):  # por debajo del minimo de 20
        _url(s, j, f"/lentas/a-{i}", ttfb_ms=5000)
    for i in range(30):  # un muro no es la respuesta del sitio (103)
        _url(s, j, f"/muro/a-{i}", ttfb_ms=5000, response_class="challenge_waf")
    for i in range(30):
        _url(s, j, f"/salto/a-{i}", status=301, html=False, ttfb_ms=5000)
    for i in range(30):  # sin TTFB medido: censo anterior, no se cae a response_time_ms
        _url(s, j, f"/viejo/a-{i}", response_time_ms=9000)
    _analizar(s, j)
    assert _issues(s, j, "respuesta_lenta") == []


def test_el_umbral_es_configurable():
    s, j = _censo({"analysis_thresholds": {"respuesta_lenta_ms": 300,
                                           "respuesta_lenta_min_paginas": 5}})
    for i in range(5):
        _url(s, j, f"/a/b-{i}", ttfb_ms=400)
    _analizar(s, j)
    assert len(_issues(s, j, "respuesta_lenta")) == 1


# -- html_sin_comprimir --------------------------------------------------

def test_host_sin_comprimir_da_una_fila_anclada_en_la_raiz():
    s, j = _censo()
    raiz = _url(s, j, "/", content_encoding="identity", content_length=50_000)
    for i in range(29):
        _url(s, j, f"/p-{i}", content_encoding="identity", content_length=40_000)
    _url(s, j, "/diminuta", content_encoding="identity", content_length=500)
    _analizar(s, j)
    (fila,) = _issues(s, j, "html_sin_comprimir")
    assert fila.url_id == raiz.id and fila.severity == "info"
    assert fila.details["paginas_afectadas"] == 30  # la de 500 bytes no cuenta
    assert fila.details["paginas_del_host"] == 30
    assert fila.details["bytes_medianos"] == 40_000
    assert "por_plantilla" not in fila.details


def test_host_que_mezcla_dice_por_plantilla_y_se_ancla_tras_la_redireccion():
    s, j = _censo()
    _url(s, j, "/", status=302, html=False, redirect_url="https://x.com/es/")
    destino = _url(s, j, "/es/", content_encoding="gzip", content_length=30_000)
    for i in range(10):
        _url(s, j, f"/es/blog/n-{i}", content_encoding="br", content_length=30_000)
    for i in range(10):
        _url(s, j, f"/es/legacy/n-{i}", content_encoding="identity", content_length=30_000)
    _analizar(s, j)
    (fila,) = _issues(s, j, "html_sin_comprimir")
    assert fila.url_id == destino.id
    assert fila.details["paginas_afectadas"] == 10 and fila.details["paginas_del_host"] == 21
    assert fila.details["por_plantilla"] == {"/:idioma/legacy/:slug (3n)": 10}


def test_sin_cabecera_medida_o_con_accept_encoding_del_job_no_se_afirma():
    s, j = _censo()
    for i in range(30):  # censo anterior: content_encoding NULL
        _url(s, j, f"/p-{i}", content_length=40_000, transfer_size=40_000)
    _analizar(s, j)
    assert _issues(s, j, "html_sin_comprimir") == []

    s, j = _censo({"http": {"custom_headers": {"accept-encoding": "identity"}}})
    for i in range(30):
        _url(s, j, f"/p-{i}", content_encoding="identity", content_length=40_000)
    _analizar(s, j)
    assert _issues(s, j, "html_sin_comprimir") == []


def test_zstd_cuenta_como_comprimido():
    s, j = _censo()
    for i in range(30):
        _url(s, j, f"/p-{i}", content_encoding="zstd", content_length=40_000)
    _analizar(s, j)
    assert _issues(s, j, "html_sin_comprimir") == []


# -- TLS -----------------------------------------------------------------

def _ficha(**k):
    base = {"fuente": "socket", "fecha": "2026-10-17T10:00:00+00:00", "verificado": True,
            "version": "TLSv1.3", "not_after": "2027-01-01T00:00:00+00:00",
            "dias_restantes": 76.0, "version_antigua": False}
    base.update(k)
    return base


def test_certificado_caducado_una_fila_por_host_en_la_raiz_y_sin_connection_error():
    tls = {"hosts": {"x.com": _ficha(verificado=False, motivo="caducado",
                                     detalle="certificate has expired",
                                     dias_restantes=-3.2)}}
    s, j = _censo(tls=tls)
    raiz = _url(s, j, "/", status=None, html=False, status_group="tls_error")
    _url(s, j, "/otra", status=None, html=False, status_group="tls_error")
    an = SEOAnalyzer(s, j.id)
    an.analyze_status_codes()
    avisos_de_servidor.avisar(an)
    an._flush_issues()
    (fila,) = _issues(s, j, "tls_certificado_invalido")
    assert fila.url_id == raiz.id and fila.severity == "error"
    assert fila.details["motivo"] == "caducado" and fila.details["dias_restantes"] == -3.2
    assert fila.details["paginas_con_error_tls"] == 2
    # Un aviso por hallazgo (36): las paginas tls_error no repiten connection_error
    assert _issues(s, j, "connection_error") == []
    assert _issues(s, j, "tls_caduca_pronto") == []


def test_tls_error_sin_sonda_que_lo_confirme_es_connection_error():
    s, j = _censo()
    u = _url(s, j, "/", status=None, html=False, status_group="tls_error")
    an = SEOAnalyzer(s, j.id)
    an.analyze_status_codes()
    an._flush_issues()
    (fila,) = _issues(s, j, "connection_error")
    assert fila.url_id == u.id and fila.details == {"error_type": "tls_error"}


def test_caduca_en_tres_dias_es_warning_y_en_veinte_no():
    s, j = _censo(tls={"hosts": {"x.com": _ficha(dias_restantes=3.0),
                                 "y.com": _ficha(dias_restantes=20.0)}})
    raiz = _url(s, j, "/")
    _url(s, j, "/", host="y.com")
    _analizar(s, j)
    (fila,) = _issues(s, j, "tls_caduca_pronto")
    assert fila.url_id == raiz.id and fila.severity == "warning"
    assert fila.details["umbral_dias"] == 7 and fila.details["host"] == "x.com"


def test_version_antigua_es_error():
    s, j = _censo(tls={"hosts": {"x.com": _ficha(version="TLSv1", version_antigua=True)}})
    _url(s, j, "/")
    _analizar(s, j)
    (fila,) = _issues(s, j, "tls_version_antigua")
    assert fila.severity == "error" and fila.details["version"] == "TLSv1"


def test_fallo_de_la_sonda_en_un_host_que_respondio_200_no_se_afirma():
    """El almacen de CA de la maquina o un WAF que corta el handshake: si curl
    o Chromium cargaron las paginas, el certificado valia."""
    s, j = _censo(tls={"hosts": {"x.com": _ficha(verificado=False, motivo="cadena_incompleta")}})
    _url(s, j, "/")
    _analizar(s, j)
    assert _issues(s, j, "tls_certificado_invalido") == []


def test_sonda_omitida_o_con_error_no_dice_nada():
    s, j = _censo(tls={"hosts": {"x.com": {"host": "x.com", "omitida": "proxy"},
                                 "y.com": {"host": "y.com", "error": "timeout"}}})
    _url(s, j, "/")
    _url(s, j, "/", host="y.com")
    _analizar(s, j)
    for tipo in avisos_de_servidor.TIPOS:
        assert _issues(s, j, tipo) == [], tipo


def test_run_all_emite_los_avisos():
    """Que `run_all` llama al modulo (y no solo los tests)."""
    s, j = _censo(tls={"hosts": {"x.com": _ficha(dias_restantes=1.0)}})
    _url(s, j, "/", content_encoding="identity", content_length=5000)
    from analysis import analyzer as mod

    an = SEOAnalyzer(s, j.id)
    llamado = []
    original = mod.avisos_de_servidor.avisar
    try:
        mod.avisos_de_servidor.avisar = lambda a: (llamado.append(a), original(a))
        for nombre in [n for n in dir(an) if n.startswith(("analyze_", "compute_"))
                       or n in ("actualizar_estadisticas",)]:
            setattr(an, nombre, lambda *a, **k: None)
        an._sellar_version = lambda: None
        an.run_all()
    finally:
        mod.avisos_de_servidor.avisar = original
    assert llamado == [an]
    assert len(_issues(s, j, "tls_caduca_pronto")) == 1
    assert len(_issues(s, j, "html_sin_comprimir")) == 1


@pytest.mark.parametrize("tipo", avisos_de_servidor.TIPOS)
def test_cada_tipo_tiene_ficha_nombre_y_ambito(tipo):
    import os
    import re

    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    inc = open(os.path.join(raiz, "frontend", "incidencias.js"), encoding="utf-8").read()
    app = open(os.path.join(raiz, "frontend", "app.js"), encoding="utf-8").read()
    fichas = inc[inc.index("const ISSUE_INFO = {"):]
    fichas = fichas[:fichas.index("\n};")]
    nombres = inc[inc.index("const ISSUE_LABEL_EXTRA = {"):]
    nombres = nombres[:nombres.index("\n};")]
    assert f"'{tipo}': {{" in fichas, f"{tipo} sin ficha en ISSUE_INFO"
    assert re.search(rf"['\s]{tipo}'?\s*:", nombres), f"{tipo} sin nombre"
    linea = next(l for l in app.splitlines() if "key: 'a_servidor'" in l)
    assert f"'{tipo}'" in linea, f"{tipo} no esta en el ambito Servidor"
