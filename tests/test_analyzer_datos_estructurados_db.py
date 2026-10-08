"""Datos estructurados: un aviso por HALLAZGO, no por bloque.

Un bloque de plantilla —el `Organization` del pie, la miga de pan— es el mismo
en todo el sitio y se arregla una vez. Medido en tres censos reales:

| censo | filas de datos estructurados | hallazgos distintos |
|---|---|---|
| penguin (87.531 paginas) | **226.651** | **4** |
| Lopesan | 11.186 | 13 |
| Saunier Duval | 288 | 7 |

En penguin, 226.651 filas son el **39% de las incidencias del censo entero**
diciendo cuatro cosas. Es la misma cura que la decision 64b aplico a las
imagenes y la 53 a las cabeceras de seguridad.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Issue, Job, StructuredData, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _montar():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, StructuredData.__table__, Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing",
            seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return s, j


def _pagina_con_bloque(s, j, n, raw):
    u = Url(job_id=j.id, url=f"https://x.com/p{n}", url_hash=f"p{n}",
            is_internal=True, is_html=True, status_code=200)
    s.add(u)
    s.flush()
    s.add(StructuredData(url_id=u.id, raw=raw, format="jsonld",
                         schema_type=raw.get("@type")))
    s.flush()
    return u


ORG_SIN_NADA = {"@type": "Organization", "name": "Tienda"}


def test_el_mismo_bloque_en_mil_paginas_es_un_aviso():
    s, j = _montar()
    for n in range(50):
        _pagina_con_bloque(s, j, n, dict(ORG_SIN_NADA))
    SEOAnalyzer(s, j.id).analyze_structured_data()
    s.flush()

    filas = s.query(Issue).filter(Issue.job_id == j.id).all()
    assert len(filas) == 1, "50 paginas con el MISMO bloque son un hallazgo"
    det = filas[0].details
    assert det["paginas_afectadas"] == 50
    assert det["schema_type"] == "Organization"
    assert len(det["paginas_ejemplo"]) == 5, "con ejemplos para ir a mirar"
    assert all(u.startswith("https://x.com/p") for u in det["paginas_ejemplo"])


def test_dos_problemas_distintos_siguen_siendo_dos_avisos():
    """Agrupar no puede tapar un hallazgo distinto."""
    s, j = _montar()
    for n in range(10):
        _pagina_con_bloque(s, j, n, dict(ORG_SIN_NADA))
    for n in range(10, 20):
        # Sin `name`: propiedad obligatoria, asi que es un error, no un aviso.
        _pagina_con_bloque(s, j, n, {"@type": "Organization", "url": "https://x.com/"})
    SEOAnalyzer(s, j.id).analyze_structured_data()
    s.flush()

    tipos = sorted(i.issue_type for i in s.query(Issue).filter(Issue.job_id == j.id))
    assert tipos == ["structured_data_error", "structured_data_warning"]


def test_la_validacion_se_guarda_en_TODOS_los_bloques():
    """Agrupar los avisos no puede dejar sin validar los bloques.

    Los UPDATE van por lotes —en penguin eran 226.651 sentencias para escribir
    cuatro valores distintos— y es justo donde se pierde una fila sin que nada
    lo diga.
    """
    s, j = _montar()
    for n in range(30):
        _pagina_con_bloque(s, j, n, dict(ORG_SIN_NADA))
    SEOAnalyzer(s, j.id).analyze_structured_data()
    s.flush()

    bloques = s.query(StructuredData).all()
    assert len(bloques) == 30
    assert all(b.validation_status == "warning" for b in bloques)
    assert all(b.validation_issues for b in bloques)
