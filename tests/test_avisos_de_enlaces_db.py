"""Avisos de enlaces contra SQLite (decision 106).

Lo que se comprueba es el criterio de agregacion, que es lo que decide si el
informe sirve o es ruido:

- por pagina de origen, una incidencia con la cuenta (nunca una por enlace);
- lo que esta en la plantilla (el mismo enlace en 10 paginas o mas) se avisa
  UNA vez, y no en cada pagina del sitio (decision 64);
- a un 4xx/5xx de plantilla no se anade nada: la URL rota ya tiene su aviso
  (decision 36);
- `utm` por destino, sustituyendo al `url_has_parameters` de esa URL;
- y que nada de lo no rastreable toca los entrantes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, EnlaceNoRastreable, Issue, Job, Link, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar(con_tabla_nueva=True):
    engine = create_engine("sqlite://")
    tablas = [Job.__table__, Url.__table__, Issue.__table__, Link.__table__]
    if con_tabla_nueva:
        tablas.append(EnlaceNoRastreable.__table__)
    Base.metadata.create_all(engine, tables=tablas)
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, status=200, indexable=True, html=True):
    grupo = f"{status // 100}xx"
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
            is_html=html, status_code=status, status_group=grupo, indexable=indexable,
            crawl_depth=1)
    s.add(u)
    s.flush()
    return u


def _enlace(s, j, origen, destino_ruta, veces=1):
    s.add(Link(job_id=j.id, from_url_id=origen.id, to_url=f"https://x.com{destino_ruta}",
               to_url_hash=destino_ruta, is_internal=True, link_position="content",
               follow=True, repeticiones=veces))


def _issues(s, tipo):
    return s.execute(
        select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
        .where(Issue.issue_type == tipo).order_by(Issue.url_id)).all()


def _contar(s, j):
    az = SEOAnalyzer(s, j.id)
    az.compute_link_counts()
    az._flush_issues()
    s.commit()
    return az


def test_enlaces_a_error_por_origen_y_no_por_enlace():
    s, j = _montar()
    p = _url(s, j, "/p")
    rotas = [_url(s, j, f"/rota/{i}", status=404) for i in range(3)]
    caida = _url(s, j, "/caida", status=500)
    for r in rotas:
        _enlace(s, j, p, r.url_hash, veces=2)
    _enlace(s, j, p, caida.url_hash)
    s.commit()
    _contar(s, j)

    filas = _issues(s, "enlace_interno_a_error")
    assert [(f.url_id, f.severity) for f in filas] == [(p.id, "warning")]
    d = filas[0].details
    assert (d["alcance"], d["destinos"], d["enlaces"]) == ("pagina", 4, 7)
    assert {e["status_code"] for e in d["ejemplos"]} == {404, 500}


def test_un_404_de_plantilla_no_se_avisa_en_cada_pagina():
    """El 404 del pie de 12 paginas ya tiene su `4xx_error`; avisarlo en las 12
    origenes seria el mismo hallazgo doce veces."""
    s, j = _montar()
    rota = _url(s, j, "/rota", status=404)
    paginas = [_url(s, j, f"/p/{i}") for i in range(12)]
    for p in paginas:
        _enlace(s, j, p, rota.url_hash)
    s.commit()
    _contar(s, j)
    assert _issues(s, "enlace_interno_a_error") == []


def test_una_redireccion_de_plantilla_se_avisa_una_vez_en_la_url_que_redirige():
    s, j = _montar()
    salto = _url(s, j, "/viejo", status=301)
    salto_propio = _url(s, j, "/otro-viejo", status=302)
    paginas = [_url(s, j, f"/p/{i}") for i in range(12)]
    for p in paginas:
        _enlace(s, j, p, salto.url_hash)
    _enlace(s, j, paginas[0], salto_propio.url_hash)
    s.commit()
    _contar(s, j)

    filas = _issues(s, "enlace_interno_a_redireccion")
    assert len(filas) == 2 and {f.severity for f in filas} == {"info"}
    por_alcance = {f.details["alcance"]: f for f in filas}
    assert por_alcance["plantilla"].url_id == salto.id
    assert por_alcance["plantilla"].details["paginas_que_enlazan"] == 12
    assert por_alcance["pagina"].url_id == paginas[0].id
    assert por_alcance["pagina"].details["ejemplos"] == [
        {"url": "https://x.com/otro-viejo", "status_code": 302}]


def test_el_origen_roto_no_cuenta_y_el_autoenlace_tampoco():
    s, j = _montar()
    roto = _url(s, j, "/roto", status=500)
    otra = _url(s, j, "/otra", status=404)
    _enlace(s, j, roto, otra.url_hash)     # desde un 500: ya tiene su aviso
    s.commit()
    _contar(s, j)
    assert _issues(s, "enlace_interno_a_error") == []


def test_utm_va_por_destino_y_sustituye_a_url_has_parameters():
    s, j = _montar()
    utm = _url(s, j, "/promo?utm_source=home")
    canon = _url(s, j, "/promo2?utm_medium=x", indexable=False)
    paginas = [_url(s, j, f"/p/{i}") for i in range(3)]
    for p in paginas:
        _enlace(s, j, p, utm.url_hash)
    _enlace(s, j, paginas[0], canon.url_hash)
    # Enlace con utm a una URL que no se rastreo (excluida por patron).
    _enlace(s, j, paginas[1], "/no-rastreada?utm_campaign=z")
    s.commit()

    az = SEOAnalyzer(s, j.id)
    az.analyze_url_issues()
    az.compute_link_counts()
    az._flush_issues()
    s.commit()

    filas = {f.url_id: f for f in _issues(s, "utm_en_enlace_interno")}
    assert filas[utm.id].severity == "warning"          # duplicado indexable
    assert filas[utm.id].details["paginas_que_enlazan"] == 3
    assert filas[canon.id].severity == "info"           # canonicalizada: no se indexa
    no_rastreada = [f for f in filas.values() if not f.details["destino_rastreado"]]
    assert len(no_rastreada) == 1 and no_rastreada[0].severity == "info"
    assert no_rastreada[0].details["destino"].endswith("/no-rastreada?utm_campaign=z")
    assert all(f.group_key for f in filas.values())
    # Un aviso por hallazgo: la URL con utm ya no lleva el generico.
    con_parametros = {f.url_id for f in _issues(s, "url_has_parameters")}
    assert utm.id not in con_parametros and canon.id not in con_parametros


def _no_rastreable(s, j, origen, tipo="javascript_href", href="javascript:void(0)",
                   destino=None, interno=None, veces=1):
    # En los tests el url_hash es la ruta: el destino se identifica igual.
    hash_ = destino.replace("https://x.com", "") if destino else None
    s.add(EnlaceNoRastreable(job_id=j.id, from_url_id=origen.id, crawlability=tipo,
                             etiqueta="a", href=href, destino=destino,
                             destino_interno=interno, destino_hash=hash_,
                             link_position="content", repeticiones=veces))


def test_no_rastreables_por_pagina_y_de_plantilla():
    s, j = _montar()
    paginas = [_url(s, j, f"/p/{i}") for i in range(12)]
    # El mismo enlace del menu en las 12: plantilla, una incidencia.
    for p in paginas:
        _no_rastreable(s, j, p, href="javascript:abrir()", destino="https://x.com/menu",
                       interno=True)
    # Dos propios en la pagina 3, sin destino reconocible: info.
    _no_rastreable(s, j, paginas[3], href="javascript:a()", veces=2)
    _no_rastreable(s, j, paginas[3], tipo="fragmento_como_ruta", href="#/x")
    # Uno propio con destino interno en la 5: warning.
    _no_rastreable(s, j, paginas[5], tipo="href_fuera_de_a", href="/oculta",
                   destino="https://x.com/oculta", interno=True)
    s.commit()

    az = SEOAnalyzer(s, j.id)
    from analysis import avisos_de_enlaces
    avisos_de_enlaces.avisar_enlaces_no_rastreables(az)
    s.commit()

    filas = _issues(s, "enlace_no_rastreable")
    plantilla = [f for f in filas if f.details["alcance"] == "plantilla"]
    pagina = {f.url_id: f for f in filas if f.details["alcance"] == "pagina"}
    assert len(plantilla) == 1
    assert plantilla[0].severity == "warning" and plantilla[0].group_key
    assert plantilla[0].details["paginas_afectadas"] == 12
    assert set(pagina) == {paginas[3].id, paginas[5].id}
    assert pagina[paginas[3].id].severity == "info"
    assert pagina[paginas[3].id].details["enlaces"] == 3
    assert pagina[paginas[3].id].details["por_tipo"] == {
        "javascript_href": 2, "fragmento_como_ruta": 1}
    assert pagina[paginas[5].id].severity == "warning"
    # /oculta no se rastreo: solo se llega por un enlace que Google no sigue.
    assert pagina[paginas[5].id].details["destinos_fuera_del_censo"] == 1
    assert plantilla[0].details["destinos_fuera_del_censo"] == 1


def test_el_selector_de_idioma_es_un_componente_aunque_cambie_el_destino():
    """Medido en Lopesan: `onclick="location.href='/en/<esta pagina>'"`. Por
    destino salia una incidencia en 201 de 216 paginas; es UN componente."""
    s, j = _montar()
    paginas = [_url(s, j, f"/es/p/{i}") for i in range(12)]
    for i, p in enumerate(paginas):
        s.add(EnlaceNoRastreable(job_id=j.id, from_url_id=p.id, crawlability="onclick_sin_href",
                                 etiqueta="a", href=f"location.href='/en/p/{i}'",
                                 destino=f"https://x.com/en/p/{i}", destino_interno=True,
                                 destino_hash=f"/en/p/{i}", anchor_text="English",
                                 link_position="nav", repeticiones=1))
    s.commit()
    from analysis import avisos_de_enlaces
    avisos_de_enlaces.avisar_enlaces_no_rastreables(SEOAnalyzer(s, j.id))
    s.commit()
    filas = _issues(s, "enlace_no_rastreable")
    assert len(filas) == 1 and filas[0].details["alcance"] == "plantilla"
    assert filas[0].details["destinos_internos_distintos"] == 12
    assert filas[0].details["destinos_fuera_del_censo"] == 12


def test_lo_no_rastreable_no_toca_los_entrantes():
    s, j = _montar()
    a = _url(s, j, "/a")
    b = _url(s, j, "/b")
    _no_rastreable(s, j, a, tipo="href_fuera_de_a", href="/b", destino="https://x.com/b",
                   interno=True, veces=5)
    s.commit()
    _contar(s, j)
    s.refresh(b)
    assert (b.inlinks_count, b.unique_inlinks_count) == (0, 0)


def test_sin_la_tabla_el_analisis_sigue():
    """Primer minuto de un despliegue: la tabla aun no existe. No es «no hay
    enlaces no rastreables», y tampoco puede tumbar el resto del analisis."""
    s, j = _montar(con_tabla_nueva=False)
    a = _url(s, j, "/a")
    s.commit()
    az = SEOAnalyzer(s, j.id)
    az._add_issue(a.id, "pendiente", "info")
    from analysis import avisos_de_enlaces
    avisos_de_enlaces.avisar_enlaces_no_rastreables(az)
    az._flush_issues()
    s.commit()
    assert _issues(s, "pendiente")


def test_en_un_censo_truncado_no_se_afirma_que_un_destino_no_este():
    """Con el tope de URLs alcanzado, que /oculta no este en el censo puede ser
    solo que no se llego a ella (decision 67): la cifra va a None."""
    s, j = _montar()
    j.finish_reason = "max_urls_reached"
    p = _url(s, j, "/p")
    _no_rastreable(s, j, p, tipo="href_fuera_de_a", href="/oculta",
                   destino="https://x.com/oculta", interno=True)
    s.commit()
    from analysis import avisos_de_enlaces
    avisos_de_enlaces.avisar_enlaces_no_rastreables(SEOAnalyzer(s, j.id))
    s.commit()
    (fila,) = _issues(s, "enlace_no_rastreable")
    assert fila.details["destinos_fuera_del_censo"] is None
    assert fila.severity == "warning"


# --- Revision de la decision 106 --------------------------------------------
def test_no_rastreables_desde_un_404_o_un_500_no_se_avisan():
    """El menu de un 500 ya tiene su `5xx_error` (decision 57), y una pagina
    re-rastreada que ahora da 404 no puede conservar los de cuando respondia."""
    s, j = _montar()
    caida = _url(s, j, "/caida", status=500)
    rota = _url(s, j, "/rota", status=404)
    viva = _url(s, j, "/viva")
    for p in (caida, rota, viva):
        _no_rastreable(s, j, p, tipo="onclick_sin_href", href="location='/x'",
                       destino="https://x.com/x", interno=True)
    s.commit()
    from analysis import avisos_de_enlaces
    avisos_de_enlaces.avisar_enlaces_no_rastreables(SEOAnalyzer(s, j.id))
    s.commit()
    assert [f.url_id for f in _issues(s, "enlace_no_rastreable")] == [viva.id]


def test_un_utm_a_una_redireccion_o_a_un_404_es_un_aviso_no_dos():
    """El enlace con utm a una URL que redirige o falla ya lo cuentan los
    avisos de respuesta, y la correccion es la misma: enlazar la URL limpia."""
    s, j = _montar()
    p = _url(s, j, "/p")
    salto = _url(s, j, "/a?utm_source=x", status=301)
    rota = _url(s, j, "/b?utm_source=x", status=404)
    _enlace(s, j, p, salto.url_hash)
    _enlace(s, j, p, rota.url_hash)
    s.commit()
    _contar(s, j)
    assert _issues(s, "utm_en_enlace_interno") == []
    assert [f.url_id for f in _issues(s, "enlace_interno_a_redireccion")] == [p.id]
    assert [f.url_id for f in _issues(s, "enlace_interno_a_error")] == [p.id]


def test_la_redireccion_a_una_solo_enlazada_no_repite_el_aviso():
    """`solo_enlazada_por_redireccion` (63) en la final ya pide apuntar los
    enlaces a ella: medido en la UOC, 381 URLs que redirigian llevaban ademas
    `enlace_interno_a_redireccion` de plantilla."""
    s, j = _montar()
    final = _url(s, j, "/nueva")
    salto = _url(s, j, "/vieja", status=301)
    salto.redirect_url = final.url
    otro_final = _url(s, j, "/destino")
    otro_salto = _url(s, j, "/antes", status=301)
    otro_salto.redirect_url = otro_final.url
    paginas = [_url(s, j, f"/p/{i}") for i in range(12)]
    for p in paginas:
        _enlace(s, j, p, salto.url_hash)
        _enlace(s, j, p, otro_salto.url_hash)
    _enlace(s, j, paginas[0], otro_final.url_hash)   # este SI tiene enlace directo
    s.commit()
    az = _contar(s, j)
    az._avisar_solo_por_redireccion()
    az._flush_issues()
    s.commit()
    assert [f.url_id for f in _issues(s, "solo_enlazada_por_redireccion")] == [final.id]
    assert [f.url_id for f in _issues(s, "enlace_interno_a_redireccion")] == [otro_salto.id]


def test_el_anclaje_no_depende_del_orden_de_rastreo():
    """La comparacion identifica el hallazgo por (tipo, URL, clave): anclado en
    el id menor, otro orden de rastreo lo daba por resuelto y por nuevo."""
    s, j = _montar()
    # Se rastrean en orden inverso: el id menor es la URL MAYOR.
    paginas = [_url(s, j, f"/p/{i:02d}") for i in reversed(range(12))]
    for p in paginas:
        _no_rastreable(s, j, p, href="javascript:menu()")
    _enlace(s, j, paginas[0], "/sin-rastrear?utm_source=x")
    _enlace(s, j, paginas[-1], "/sin-rastrear?utm_source=x")
    s.commit()
    _contar(s, j)
    from analysis import avisos_de_enlaces
    avisos_de_enlaces.avisar_enlaces_no_rastreables(SEOAnalyzer(s, j.id))
    s.commit()
    menor = min(paginas, key=lambda u: u.url)
    (plantilla,) = _issues(s, "enlace_no_rastreable")
    assert plantilla.url_id == menor.id
    assert plantilla.details["paginas_ejemplo"][0] == menor.url
    (utm,) = _issues(s, "utm_en_enlace_interno")
    assert utm.url_id == menor.id and utm.details["ejemplos_origen"] == [menor.url]
