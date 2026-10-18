"""robots.txt guardado, evaluado por el analizador contra SQLite (decisiones 112 y 113).

Se ejecuta el analizador real (`analyze_robots_txt` + `analyze_indexability`)
sobre filas de `robots_txt` como las que deja el rastreo:

- 503/timeout: UN `robots_txt_inaccesible` por host aunque el host tenga
  muchas URLs, `error` si fallo tambien al cerrar y `warning` si a la segunda
  respondio o si es un 429; un 404 o un muro delante no avisan;
- la indexabilidad no cambia por un 5xx (no se afirma lo que no se sabe);
- un robots.txt que bloquea a Googlebot y no a Chrome (y al reves): la columna
  `urls.bloqueada_googlebot`, el `Blocked by robots.txt` de la pagina que solo
  Google no puede pedir y un `robots_difiere_para_googlebot` por host;
- un censo sin `robots_txt` (los de antes) sale exactamente igual.
"""

from __future__ import annotations

import random
import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, HtmlMeta, Issue, Job, RobotsTxt, Url
from shared.robots_txt import fila_de_respuesta


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _montar(robots_mode="respect"):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, HtmlMeta.__table__,
        RobotsTxt.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config={"robots_mode": robots_mode})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, host="x.com", status=200, bloqueada=None):
    estado = "Blocked by robots.txt" if bloqueada and status is None else "Indexable"
    u = Url(job_id=j.id, url=f"https://{host}{ruta}", url_hash=f"{host}{ruta}", host=host,
            scheme="https", path=ruta, is_internal=True, is_html=True, status_code=status,
            status_group="blocked" if status is None else f"{status // 100}xx",
            indexability_status=estado, blocked_by_robots=bloqueada)
    s.add(u)
    s.flush()
    return u


def _robots(s, j, host="x.com", status=200, cuerpo=b"", error=None, comprobacion=None):
    fila = fila_de_respuesta(f"https://{host}/robots.txt", status_code=status,
                             cabeceras={"content-type": "text/plain"}, cuerpo=cuerpo,
                             error=error, intentos=3 if status in (503, 429) else 1)
    if comprobacion is not None:
        fila["comprobacion"] = comprobacion
    s.add(RobotsTxt(job_id=j.id, **fila))
    s.flush()


def _analizar(s, j):
    az = SEOAnalyzer(s, j.id)
    az.analyze_robots_txt()
    az.analyze_indexability()
    az._flush_issues()
    s.commit()
    return az


def _issues(s, tipo):
    return s.execute(select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
                     .where(Issue.issue_type == tipo).order_by(Issue.url_id)).all()


def _col(s, j, columna):
    return dict(s.execute(select(Url.path, columna).where(Url.job_id == j.id)).all())


# ---------------------------------------------------------------------------
# 5xx, 429 o inaccesible (decision 112)
# ---------------------------------------------------------------------------
def test_503_un_aviso_por_host_aunque_haya_muchas_urls_y_sin_tocar_la_indexabilidad():
    s, j = _montar()
    raiz = _url(s, j, "/")
    for i in range(500):
        _url(s, j, f"/p/{i}")
    _robots(s, j, status=503, cuerpo=b"<html>mantenimiento</html>",
            comprobacion={"status_code": 503, "error": None, "response_class": "contenido"})
    _analizar(s, j)

    filas = _issues(s, "robots_txt_inaccesible")
    assert [(f.url_id, f.severity) for f in filas] == [(raiz.id, "error")]
    d = filas[0].details
    assert (d["status_code"], d["intentos"], d["intermitente"], d["limitado"]) == (503, 3, False, False)
    assert set(_col(s, j, Url.indexable).values()) == {True}
    # Inaccesible: no se afirma que Googlebot pueda o no pedir nada.
    assert set(_col(s, j, Url.bloqueada_googlebot).values()) == {None}


def test_timeout_es_inaccesible_y_si_a_la_segunda_responde_es_intermitente():
    s, j = _montar()
    _url(s, j, "/")
    _robots(s, j, status=None, error="TimeoutError: took longer than 30.0 seconds",
            comprobacion={"status_code": 200, "error": None, "response_class": "contenido"})
    _analizar(s, j)
    [fila] = _issues(s, "robots_txt_inaccesible")
    assert fila.severity == "warning" and fila.details["intermitente"] is True
    assert fila.details["error"].startswith("TimeoutError")


def test_429_avisa_como_warning_porque_el_limite_puede_ser_para_nuestra_ip():
    s, j = _montar()
    _url(s, j, "/")
    _robots(s, j, status=429, cuerpo=b"Too many requests")
    _analizar(s, j)
    [fila] = _issues(s, "robots_txt_inaccesible")
    assert fila.severity == "warning" and fila.details["limitado"] is True
    assert fila.details["response_class"] == "rate_limited"


def test_404_y_muro_no_avisan():
    s, j = _montar()
    _url(s, j, "/")
    _url(s, j, "/", host="b.x.com")
    _robots(s, j, status=404, cuerpo=b"no")
    with open("tests/fixtures/respuestas/cloudflare_desafio_progym.html", "rb") as f:
        desafio = f.read()
    fila = fila_de_respuesta("https://b.x.com/robots.txt", status_code=403,
                             cabeceras={"cf-mitigated": "challenge",
                                        "content-type": "text/html"}, cuerpo=desafio)
    assert fila["response_class"] == "challenge_waf"
    s.add(RobotsTxt(job_id=j.id, **fila))
    s.flush()
    _analizar(s, j)
    assert _issues(s, "robots_txt_inaccesible") == []
    # 404: todo permitido para Google. Muro: no evaluado.
    assert dict(s.execute(select(Url.host, Url.bloqueada_googlebot)).all()) == {
        "x.com": False, "b.x.com": None}


def test_dos_hosts_dos_avisos_y_el_ancla_no_depende_del_orden():
    s, j = _montar()
    rutas = [f"/p/{i}" for i in range(20)]
    random.Random(7).shuffle(rutas)
    urls = [_url(s, j, r, host="b.x.com") for r in rutas]
    raiz = _url(s, j, "/")
    _robots(s, j, status=500, cuerpo=b"x")
    _robots(s, j, host="b.x.com", status=502, cuerpo=b"x")
    _analizar(s, j)
    filas = _issues(s, "robots_txt_inaccesible")
    assert {(f.details["host"], f.url_id) for f in filas} == {
        ("x.com", raiz.id), ("b.x.com", min(u.id for u in urls))}
    assert len({f.group_key for f in filas}) == 2


# ---------------------------------------------------------------------------
# Evaluado como Googlebot (decision 113)
# ---------------------------------------------------------------------------
SOLO_GOOGLE = b"User-agent: Googlebot\nDisallow: /solo-google/\n\nUser-agent: *\nAllow: /\n"
SOLO_MOZILLA = b"User-agent: Mozilla\nDisallow: /moz/\n\nUser-agent: Googlebot\nAllow: /\n"


def test_robots_que_bloquea_a_googlebot_y_no_a_chrome():
    """Rastreamos /solo-google/ (Chrome cae en `*`), Google no puede pedirla."""
    s, j = _montar()
    _url(s, j, "/")
    _url(s, j, "/solo-google/a")
    _url(s, j, "/solo-google/b")
    _robots(s, j, cuerpo=SOLO_GOOGLE)
    _analizar(s, j)

    assert _col(s, j, Url.bloqueada_googlebot) == {
        "/": False, "/solo-google/a": True, "/solo-google/b": True}
    estado = _col(s, j, Url.indexability_status)
    assert estado["/solo-google/a"] == "Blocked by robots.txt"
    assert _col(s, j, Url.indexable)["/solo-google/a"] is False
    assert _col(s, j, Url.indexable)["/"] is True
    [fila] = _issues(s, "robots_difiere_para_googlebot")
    d = fila.details
    assert (d["solo_googlebot"], d["solo_rastreador"], d["evaluadas"]) == (2, 0, 3)
    assert d["ejemplos_solo_googlebot"] == ["https://x.com/solo-google/a",
                                            "https://x.com/solo-google/b"]
    ev = s.execute(select(RobotsTxt.evaluacion)).scalar()
    assert ev["googlebot"]["bloqueadas"] == 2 and ev["googlebot"]["estado"] == "procesado"


def test_robots_que_bloquea_a_chrome_y_no_a_googlebot():
    """`User-agent: Mozilla` nos bloquea a nosotros: la URL no se pidio, pero
    Google si puede. No se inventa que sea indexable (no se ha visto)."""
    s, j = _montar()
    _url(s, j, "/")
    moz = _url(s, j, "/moz/a", status=None, bloqueada=True)
    _robots(s, j, cuerpo=SOLO_MOZILLA)
    _analizar(s, j)
    assert _col(s, j, Url.bloqueada_googlebot)["/moz/a"] is False
    assert _col(s, j, Url.indexable)["/moz/a"] is False
    assert _col(s, j, Url.indexability_status)["/moz/a"] == "Blocked by robots.txt"
    [fila] = _issues(s, "robots_difiere_para_googlebot")
    assert fila.url_id != moz.id
    assert (fila.details["solo_rastreador"], fila.details["solo_googlebot"]) == (1, 0)


def test_audit_una_pagina_pedida_y_marcada_que_googlebot_si_puede_pedir_es_indexable():
    s, j = _montar(robots_mode="audit")
    u = _url(s, j, "/moz/a", status=200, bloqueada=True)
    u.indexability_status = "Blocked by robots.txt"
    _robots(s, j, cuerpo=SOLO_MOZILLA)
    _analizar(s, j)
    assert _col(s, j, Url.indexable)["/moz/a"] is True
    assert _col(s, j, Url.indexability_status)["/moz/a"] == "Indexable"


def test_ignore_evalua_googlebot_pero_no_compara_con_el_rastreo():
    s, j = _montar(robots_mode="ignore")
    _url(s, j, "/solo-google/a")
    _robots(s, j, cuerpo=SOLO_GOOGLE)
    _analizar(s, j)
    assert _col(s, j, Url.bloqueada_googlebot)["/solo-google/a"] is True
    assert _col(s, j, Url.indexable)["/solo-google/a"] is False
    assert _issues(s, "robots_difiere_para_googlebot") == []


def test_sin_robots_txt_guardado_nada_cambia():
    """Un censo anterior a la tabla: la columna queda NULL y no sale ningun
    aviso; la indexabilidad es la de siempre."""
    s, j = _montar()
    _url(s, j, "/")
    _url(s, j, "/privado", status=None, bloqueada=True)
    _analizar(s, j)
    assert set(_col(s, j, Url.bloqueada_googlebot).values()) == {None}
    assert _col(s, j, Url.indexable) == {"/": True, "/privado": False}
    assert _issues(s, "robots_difiere_para_googlebot") == []
    assert _issues(s, "robots_txt_inaccesible") == []


def test_otro_esquema_no_usa_ese_robots_txt():
    """El robots.txt de https no vale para http (Google los trata aparte)."""
    s, j = _montar()
    u = Url(job_id=j.id, url="http://x.com/solo-google/a", url_hash="h", host="x.com",
            scheme="http", path="/solo-google/a", is_internal=True, status_code=301,
            status_group="3xx")
    s.add(u)
    _robots(s, j, cuerpo=SOLO_GOOGLE)
    _analizar(s, j)
    assert s.get(Url, u.id).bloqueada_googlebot is None


def test_reanalizar_da_lo_mismo():
    s, j = _montar()
    _url(s, j, "/")
    _url(s, j, "/solo-google/a")
    _robots(s, j, cuerpo=SOLO_GOOGLE)
    _analizar(s, j)
    antes = (_col(s, j, Url.bloqueada_googlebot), _col(s, j, Url.indexability_status))
    s.execute(Issue.__table__.delete())
    _analizar(s, j)
    assert (_col(s, j, Url.bloqueada_googlebot), _col(s, j, Url.indexability_status)) == antes
    assert len(_issues(s, "robots_difiere_para_googlebot")) == 1


def test_sin_la_tabla_el_analisis_sigue_como_antes():
    """Una base sin migrar: ni aviso ni cambio, y lo demas se analiza."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, HtmlMeta.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=[], config={})
    s.add(j)
    s.flush()
    _url(s, j, "/")
    _analizar(s, j)
    assert _col(s, j, Url.indexable) == {"/": True}
