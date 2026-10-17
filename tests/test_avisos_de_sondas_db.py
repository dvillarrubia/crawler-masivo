"""Avisos de las sondas de respuesta contra SQLite (decisiones 123 y 124).

- ``enlace_externo_roto`` uno por DESTINO, con `paginas_afectadas` y los
  enlaces por aparicion (SUM de repeticiones), anclado en la pagina de URL
  menor; un 403/429/5xx no se avisa;
- ``dominio_externo_muerto`` uno por host, sin repetir sus URLs como enlaces
  rotos; un host que respondio una vez no esta muerto;
- un origen que no responde 200 no emite (decision 57);
- ``broken_resource`` uno por recurso, y `resources.size_bytes` de paso;
- y el resumen «comprobados N de M» en `jobs.sondas_resumen`.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import (Base, Issue, Job, Link, Resource, RespuestaSondeada, Url)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _h(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _montar(con_tabla=True):
    engine = create_engine("sqlite://")
    tablas = [Job.__table__, Url.__table__, Issue.__table__, Link.__table__,
              Resource.__table__]
    if con_tabla:
        tablas.append(RespuestaSondeada.__table__)
    Base.metadata.create_all(engine, tables=tablas)
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, status=200, html=True):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=_h(f"https://x.com{ruta}"),
            is_internal=True, is_html=html, status_code=status,
            status_group=f"{status // 100}xx", indexable=True, crawl_depth=1)
    s.add(u)
    s.flush()
    return u


def _enlace(s, j, origen, destino, veces=1, interno=False):
    s.add(Link(job_id=j.id, from_url_id=origen.id, to_url=destino, to_url_hash=_h(destino),
               is_internal=interno, link_position="content", follow=True,
               repeticiones=veces))


def _sonda(s, j, url, veredicto, status=None, uso="enlace", interno=False,
           host_caido=None, metodo="GET", content_length=None):
    from urllib.parse import urlsplit

    s.add(RespuestaSondeada(job_id=j.id, url_hash=_h(url), url=url,
                            host=urlsplit(url).hostname, uso=uso, interno=interno,
                            metodo=metodo, status_code=status, veredicto=veredicto,
                            host_caido=host_caido, content_length=content_length, saltos=0))


def _issues(s, tipo):
    return s.execute(
        select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
        .where(Issue.issue_type == tipo).order_by(Issue.url_id)).all()


def _analizar(s, j):
    az = SEOAnalyzer(s, j.id)
    az.compute_link_counts()
    from analysis import avisos_de_sondas

    avisos_de_sondas.avisar_recursos_rotos(az)
    az._flush_issues()
    az._sellar_version()
    s.commit()
    return az


def test_un_aviso_por_destino_con_las_paginas_que_lo_enlazan():
    s, j = _montar()
    paginas = [_url(s, j, f"/p/{i}") for i in range(3)]
    roto = "https://otro.com/no-existe"
    for p in paginas:
        _enlace(s, j, p, roto, veces=2)
    _sonda(s, j, roto, "roto", 404)
    s.commit()
    _analizar(s, j)

    filas = _issues(s, "enlace_externo_roto")
    assert len(filas) == 1
    f = filas[0]
    assert f.severity == "warning"
    # anclado en la de URL menor
    assert f.url_id == min(paginas, key=lambda p: p.url).id
    d = f.details
    assert (d["destino"], d["paginas_afectadas"], d["enlaces"], d["status_code"]) == (
        roto, 3, 6, 404)
    assert d["ejemplos"] == sorted(p.url for p in paginas)
    assert f.group_key


def test_lo_que_no_se_puede_afirmar_no_se_avisa():
    s, j = _montar()
    p = _url(s, j, "/p")
    for i, (v, st) in enumerate([("no_verificable", 403), ("no_verificable", 429),
                                 ("error_servidor", 500), ("tiempo_agotado", None),
                                 ("prohibido_por_robots", None), ("tls_invalido", None),
                                 ("ok", 200)]):
        url = f"https://t{i}.com/x"
        _enlace(s, j, p, url)
        _sonda(s, j, url, v, st)
    s.commit()
    _analizar(s, j)
    assert _issues(s, "enlace_externo_roto") == []
    assert _issues(s, "dominio_externo_muerto") == []


def test_redirige_a_error_cuenta_como_roto():
    s, j = _montar()
    p = _url(s, j, "/p")
    _enlace(s, j, p, "https://otro.com/viejo")
    _sonda(s, j, "https://otro.com/viejo", "redirige_a_error", 301)
    s.commit()
    _analizar(s, j)
    assert [f.details["veredicto"] for f in _issues(s, "enlace_externo_roto")] == [
        "redirige_a_error"]


def test_dominio_muerto_una_vez_y_sin_duplicar_los_enlaces():
    s, j = _montar()
    a, b = _url(s, j, "/a"), _url(s, j, "/b")
    for ruta in ("/1", "/2"):
        _enlace(s, j, a, f"https://muerto.com{ruta}")
        _enlace(s, j, b, f"https://muerto.com{ruta}", veces=3)
    _sonda(s, j, "https://muerto.com/1", "dominio_inexistente", host_caido=True, metodo=None)
    _sonda(s, j, "https://muerto.com/2", "dominio_inexistente", host_caido=True, metodo=None)
    s.commit()
    _analizar(s, j)

    filas = _issues(s, "dominio_externo_muerto")
    assert len(filas) == 1
    d = filas[0].details
    assert filas[0].url_id == a.id
    assert (d["host"], d["urls_distintas"], d["paginas_afectadas"], d["enlaces"]) == (
        "muerto.com", 2, 2, 8)
    assert _issues(s, "enlace_externo_roto") == []


def test_un_host_que_respondio_alguna_vez_no_esta_muerto():
    s, j = _montar()
    p = _url(s, j, "/p")
    _enlace(s, j, p, "https://vivo.com/1")
    _enlace(s, j, p, "https://vivo.com/2")
    _sonda(s, j, "https://vivo.com/1", "sin_conexion", host_caido=True)
    _sonda(s, j, "https://vivo.com/2", "roto", 404)
    s.commit()
    _analizar(s, j)
    assert _issues(s, "dominio_externo_muerto") == []
    assert len(_issues(s, "enlace_externo_roto")) == 1


def test_un_fallo_suelto_sin_host_caido_no_es_un_dominio_muerto():
    s, j = _montar()
    p = _url(s, j, "/p")
    _enlace(s, j, p, "https://lento.com/1")
    _sonda(s, j, "https://lento.com/1", "sin_conexion", host_caido=None)
    s.commit()
    _analizar(s, j)
    assert _issues(s, "dominio_externo_muerto") == []


def test_un_resolver_caido_no_mata_todos_los_dominios():
    """Si «no existe» la mitad de Internet, lo que fallo es el DNS del rastreo."""
    s, j = _montar()
    p = _url(s, j, "/p")
    for i in range(12):
        url = f"https://d{i}.com/x"
        _enlace(s, j, p, url)
        _sonda(s, j, url, "dominio_inexistente", host_caido=True, metodo=None)
    s.commit()
    _analizar(s, j)
    assert _issues(s, "dominio_externo_muerto") == []


def test_un_origen_que_no_responde_200_no_emite():
    s, j = _montar()
    p404 = _url(s, j, "/rota", status=404)
    _enlace(s, j, p404, "https://otro.com/x")
    _sonda(s, j, "https://otro.com/x", "roto", 404)
    s.commit()
    _analizar(s, j)
    assert _issues(s, "enlace_externo_roto") == []


def test_un_destino_que_esta_en_el_censo_no_se_repite():
    """Con follow_external la URL externa esta en `urls` con su 4xx_error."""
    s, j = _montar()
    p = _url(s, j, "/p")
    _enlace(s, j, p, "https://x.com/externa-en-censo")
    s.add(Url(job_id=j.id, url="https://x.com/externa-en-censo",
              url_hash=_h("https://x.com/externa-en-censo"), is_internal=False,
              is_html=True, status_code=404, status_group="4xx"))
    _sonda(s, j, "https://x.com/externa-en-censo", "roto", 404)
    s.commit()
    _analizar(s, j)
    assert _issues(s, "enlace_externo_roto") == []


def test_resumen_comprobados_de_distintos():
    s, j = _montar()
    p = _url(s, j, "/p")
    for i in range(4):
        _enlace(s, j, p, f"https://otro.com/{i}")
    _sonda(s, j, "https://otro.com/0", "ok", 200)
    _sonda(s, j, "https://otro.com/1", "roto", 404)
    s.commit()
    _analizar(s, j)
    s.expire_all()
    r = s.get(Job, j.id).sondas_resumen["enlaces_externos"]
    assert (r["distintos"], r["comprobados"], r["rotos"]) == (4, 2, 1)


def test_sin_sondas_no_hay_resumen_ni_columna():
    s, j = _montar()
    p = _url(s, j, "/p")
    _enlace(s, j, p, "https://otro.com/0")
    s.commit()
    _analizar(s, j)
    s.expire_all()
    assert s.get(Job, j.id).sondas_resumen is None


def test_sin_la_tabla_el_analisis_sigue():
    s, j = _montar(con_tabla=False)
    p = _url(s, j, "/p")
    _enlace(s, j, p, "https://otro.com/0")
    s.commit()
    _analizar(s, j)
    assert s.get(Url, p.id).external_outlinks_count == 1


# ---------------------------------------------------------------------------
# Recursos embebidos (#101)
# ---------------------------------------------------------------------------
def test_un_aviso_por_recurso_roto_y_el_tamano_de_paso():
    s, j = _montar()
    paginas = [_url(s, j, f"/p/{i}") for i in range(4)]
    for p in paginas:
        s.add(Resource(url_id=p.id, resource_url="https://x.com/logo.png", resource_type="image"))
        s.add(Resource(url_id=p.id, resource_url="https://x.com/app.css", resource_type="css"))
    s.add(Resource(url_id=paginas[0].id, resource_url="https://cdn.com/x.js",
                   resource_type="js"))
    _sonda(s, j, "https://x.com/logo.png", "roto", 404, uso="recurso", interno=True)
    _sonda(s, j, "https://x.com/app.css", "ok", 200, uso="recurso", interno=True,
           metodo="HEAD", content_length=1234)
    _sonda(s, j, "https://cdn.com/x.js", "dominio_inexistente", uso="recurso",
           host_caido=True, metodo=None)
    s.commit()
    _analizar(s, j)

    filas = _issues(s, "broken_resource")
    por = {f.details["recurso"]: f for f in filas}
    assert set(por) == {"https://x.com/logo.png", "https://cdn.com/x.js"}
    logo = por["https://x.com/logo.png"].details
    assert (logo["paginas_afectadas"], logo["resource_type"], logo["interno"]) == (4, "image", True)
    assert por["https://x.com/logo.png"].url_id == min(paginas, key=lambda p: p.url).id
    assert por["https://cdn.com/x.js"].details["veredicto"] == "dominio_inexistente"
    tamanos = {r.size_bytes for r in s.query(Resource).filter(
        Resource.resource_url == "https://x.com/app.css")}
    assert tamanos == {1234}
    s.expire_all()
    r = s.get(Job, j.id).sondas_resumen["recursos"]
    assert (r["comprobados"], r["rotos"]) == (3, 2)


def test_un_recurso_en_el_censo_no_se_repite():
    s, j = _montar()
    p = _url(s, j, "/p")
    img = _url(s, j, "/img.png", status=404, html=False)
    s.add(Resource(url_id=p.id, resource_url=img.url, resource_type="image"))
    _sonda(s, j, img.url, "roto", 404, uso="recurso", interno=True)
    s.commit()
    _analizar(s, j)
    assert _issues(s, "broken_resource") == []
