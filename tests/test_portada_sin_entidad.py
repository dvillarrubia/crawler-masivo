"""`portada_sin_entidad` (decision 229): la portada no dice quien esta detras.

Ejecuta el analizador contra SQLite (decision 74).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis import avisos_de_entidad
from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Issue, Job, StructuredData, Url
from shared.respuestas import CHALLENGE_WAF
from shared.schema_entidades import TIPOS_DE_ENTIDAD, declara_entidad, tipos_de


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture()
def censo():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Job.__table__, Url.__table__,
                                             StructuredData.__table__, Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()

    def pagina(url, *bloques, status=200, indexable=True, **kw):
        partes = url.split("/", 3)
        u = Url(job_id=j.id, url=url, url_hash=url, host=partes[2],
                path="/" + (partes[3] if len(partes) > 3 else ""), is_internal=True,
                is_html=True, status_code=status, crawl_depth=1, indexable=indexable, **kw)
        s.add(u)
        s.flush()
        for raw in bloques:
            s.add(StructuredData(url_id=u.id, raw=raw, format="json-ld",
                                 schema_type=str(raw.get("@type"))))
        return u

    def avisos():
        avisos_de_entidad.avisar(SEOAnalyzer(s, j.id))
        s.flush()
        return s.execute(select(Issue.url_id, Issue.severity, Issue.details)
                         .where(Issue.job_id == j.id,
                                Issue.issue_type == "portada_sin_entidad")).all()

    return pagina, avisos


def test_la_lista_sale_de_la_jerarquia_de_schema_org():
    for tipo in ("organization", "localbusiness", "hotel", "resort", "dentist",
                 "collegeoruniversity", "onlinestore", "newsmediaorganization", "person"):
        assert tipo in TIPOS_DE_ENTIDAD, tipo
    for tipo in ("website", "webpage", "product", "article", "breadcrumblist"):
        assert tipo not in TIPOS_DE_ENTIDAD, tipo


def test_tipos_anidados_y_en_los_tres_formatos():
    assert declara_entidad({"@graph": [{"@type": "WebSite",
                                        "publisher": {"@type": "Hotel", "name": "X"}}]})
    assert declara_entidad({"type": "https://schema.org/Organization", "properties": {}})
    assert declara_entidad({"@type": ["http://schema.org/Person"]})
    assert tipos_de({"@type": "schema:Corporation"}) == {"corporation"}
    assert not declara_entidad({"@type": "WebSite", "name": "X"})


def test_portada_sin_entidad_es_un_aviso_info_por_host(censo):
    pagina, avisos = censo
    home = pagina("https://x.com/", {"@type": "WebSite", "name": "X"})
    pagina("https://x.com/contacto", {"@type": "Organization", "name": "X"})
    [fila] = avisos()
    assert fila.url_id == home.id and fila.severity == "info"
    assert fila.details["host"] == "x.com"
    assert fila.details["tipos_en_la_portada"] == ["website"]


def test_con_un_hotel_en_el_publisher_no_avisa(censo):
    pagina, avisos = censo
    pagina("https://x.com/", {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "publisher": {"@id": "#h"}},
        {"@type": "Hotel", "@id": "#h", "name": "X"}]})
    assert avisos() == []


def test_basta_con_la_portada_de_un_idioma(censo):
    pagina, avisos = censo
    pagina("https://x.com/", {"@type": "WebSite"})
    pagina("https://x.com/es/", {"@type": "Organization", "name": "X"})
    assert avisos() == []


def test_sin_portada_valida_no_se_afirma_nada(censo):
    pagina, avisos = censo
    pagina("https://x.com/", status=301)
    pagina("https://y.com/", response_class=CHALLENGE_WAF)
    pagina("https://z.com/", indexable=False)
    pagina("https://x.com/blog/post", {"@type": "Article"})
    assert avisos() == []


def test_si_la_raiz_no_vale_ancla_en_la_de_idioma_menor(censo):
    pagina, avisos = censo
    pagina("https://x.com/", status=301)
    pagina("https://x.com/es/")
    en = pagina("https://x.com/en/")
    [fila] = avisos()
    assert fila.url_id == en.id
    assert fila.details["portadas"] == ["https://x.com/en/", "https://x.com/es/"]
