"""Los avisos agregados se anclan en la URL menor, no en el id menor (decision 88).

La comparacion entre censos casa una incidencia por (tipo, url, group_key). El
id de una fila de `urls` sigue al orden en que se rastreo, asi que un aviso por
host, por plantilla o por valor anclado en el id mas bajo cae en una pagina
distinta en dos censos IGUALES rastreados en otro orden, y el mismo hallazgo
sale «resuelto» y «nuevo» a la vez.

Se monta el mismo censo dos veces —en orden de URL y al reves— y se ejecutan
los checks de verdad contra SQLite: lo que identifica cada aviso (tipo, URL
del ancla, clave) y sus ejemplos tienen que salir identicos. Cubre los avisos
que anclaban por id: los tres de robots.txt (decisiones 112, 113 y 129),
`respuesta_no_valida`, los de datos estructurados, `head_cortado`,
`meta_fuera_del_head`, `image_missing_alt` y las cabeceras de seguridad por
host.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from analysis.robots_txt import MUESTRA_POR_PLANTILLA, MuestraPorPlantilla
from shared.models import (
    Base, HtmlMeta, Issue, Job, Resource, RobotsTxt, SecurityHeaders, StructuredData, Url,
)
from shared.robots_txt import fila_de_respuesta


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


ROBOTS_X = (b"User-agent: Googlebot\nDisallow: /solo-google/\n\n"
            b"User-agent: PerplexityBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")

# (host, ruta). La raiz de x.com tiene una variante con parametros: las dos
# tienen `path` "/", y en orden inverso la de parametros tiene el id menor.
PAGINAS = [("x.com", "/"), ("x.com", "/?a=1")] + [
    ("x.com", f"/solo-google/{i}") for i in range(4)] + [
    ("x.com", f"/blog/post-{i}") for i in range(6)] + [
    ("b.x.com", f"/p/{i}") for i in range(5)] + [
    ("x.com", f"/muro/{i}") for i in range(3)]

TIPOS = ("robots_txt_inaccesible", "robots_difiere_para_googlebot",
         "bot_busqueda_ia_bloqueado", "respuesta_no_valida",
         "structured_data_error", "head_cortado",
         "meta_fuera_del_head", "image_missing_alt", "missing_hsts", "missing_csp")


def _censo(paginas):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, HtmlMeta.__table__,
        RobotsTxt.__table__, StructuredData.__table__, Resource.__table__,
        SecurityHeaders.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config={"robots_mode": "respect"})
    s.add(j)
    s.flush()
    for host, ruta in paginas:
        muro = ruta.startswith("/muro/")
        u = Url(job_id=j.id, url=f"https://{host}{ruta}", url_hash=f"{host}{ruta}",
                host=host, scheme="https", path=ruta.split("?")[0], is_internal=True,
                is_html=True, status_code=403 if muro else 200,
                status_group="4xx" if muro else "2xx", indexable=not muro,
                indexability_status="Indexable", blocked_by_robots=False,
                response_class="challenge_waf" if muro else None)
        s.add(u)
        s.flush()
        if muro:
            continue
        # Todas con el mismo hallazgo de pagina: el aviso es uno por valor.
        # x.com con el head cortado; b.x.com con la description en el body.
        corte = "img" if host == "x.com" else None
        s.add(HtmlMeta(url_id=u.id, title="t", head_cortado_por=corte,
                       fuera_del_head="robots,title" if corte else "description"))
        s.add(StructuredData(url_id=u.id, format="jsonld", schema_type="Product",
                             raw={"@context": "https://schema.org", "@type": "Product"}))
        s.add(Resource(url_id=u.id, resource_url=f"https://{host}/logo.png",
                       resource_type="image", alt_text=None))
        s.add(SecurityHeaders(url_id=u.id, is_https=True, has_hsts=False, has_csp=False,
                              has_x_content_type_options=True, has_x_frame_options=True))
    for fila in (
        fila_de_respuesta("https://x.com/robots.txt", status_code=200,
                          cabeceras={"content-type": "text/plain"}, cuerpo=ROBOTS_X),
        fila_de_respuesta("https://b.x.com/robots.txt", status_code=502,
                          cabeceras={"content-type": "text/plain"}, cuerpo=b"x", intentos=3),
    ):
        s.add(RobotsTxt(job_id=j.id, **fila))
    s.flush()
    return s, j


def _hallazgos(paginas):
    s, j = _censo(paginas)
    az = SEOAnalyzer(s, j.id)
    az.analyze_status_codes()
    az.analyze_structured_data()
    az.analyze_html_ignorado()
    az.analyze_robots_txt()
    az.analyze_indexability()
    az.analyze_images()
    az.analyze_security()
    az._flush_issues()
    s.commit()
    filas = s.execute(
        select(Issue.issue_type, Url.url, Issue.group_key, Issue.details)
        .join(Url, Url.id == Issue.url_id)
        .where(Issue.job_id == j.id, Issue.issue_type.in_(TIPOS))).all()
    return {(t, u, g): d for t, u, g, d in filas}


@pytest.fixture(scope="module")
def dos_ordenes():
    en_orden = sorted(PAGINAS, key=lambda p: f"https://{p[0]}{p[1]}")
    return _hallazgos(en_orden), _hallazgos(list(reversed(en_orden)))


def test_el_mismo_censo_en_otro_orden_da_los_mismos_hallazgos(dos_ordenes):
    directo, inverso = dos_ordenes
    # Todos los tipos salen: si alguno no saliera, el test no probaria nada.
    assert {t for t, _, _ in directo} == set(TIPOS)
    assert sorted(directo) == sorted(inverso)


def test_y_con_los_mismos_ejemplos(dos_ordenes):
    directo, inverso = dos_ordenes
    for clave, detalle in directo.items():
        assert inverso[clave] == detalle, clave


def test_el_ancla_es_la_raiz_o_la_url_menor(dos_ordenes):
    _, inverso = dos_ordenes
    anclas = {(t, u) for t, u, _ in inverso}
    # La raiz de x.com, no su variante con parametros (que tiene el id menor).
    assert ("robots_difiere_para_googlebot", "https://x.com/") in anclas
    assert ("bot_busqueda_ia_bloqueado", "https://x.com/") in anclas
    # b.x.com no tiene raiz rastreada: su URL menor.
    assert ("robots_txt_inaccesible", "https://b.x.com/p/0") in anclas
    assert ("respuesta_no_valida", "https://x.com/muro/0") in anclas
    assert ("missing_hsts", "https://b.x.com/p/0") in anclas
    assert ("missing_hsts", "https://x.com/") in anclas
    assert ("structured_data_error", "https://b.x.com/p/0") in anclas
    assert ("head_cortado", "https://x.com/") in anclas
    assert ("meta_fuera_del_head", "https://b.x.com/p/0") in anclas
    # Una fila por imagen (por host aqui), en la pagina menor que la lleva.
    assert ("image_missing_alt", "https://x.com/") in anclas
    assert ("image_missing_alt", "https://b.x.com/p/0") in anclas


def test_la_muestra_de_robots_por_plantilla_no_depende_del_orden():
    """La muestra decide si un bot de IA tiene cerrado «todo» o «parte» del
    sitio, y con ello si sale `bot_busqueda_ia_bloqueado`: tampoco puede
    seguir al id."""
    n = MUESTRA_POR_PLANTILLA + 4
    urls = [f"https://x.com/blog/post-{i:02d}" for i in range(n)]

    def muestra(orden):
        m = MuestraPorPlantilla()
        for i, u in enumerate(orden):  # el id sigue al orden de llegada
            m.anadir("https://x.com", i + 1, u)
        return [u for _, u in next(iter(m.de("https://x.com").values()))]

    assert muestra(urls) == muestra(list(reversed(urls))) == urls[:MUESTRA_POR_PLANTILLA]
