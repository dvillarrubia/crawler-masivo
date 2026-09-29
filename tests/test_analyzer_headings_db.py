"""analyze_headings contra SQLite: un 404 no es una pagina "sin H1".

Los headings solo se extraen de respuestas 2xx, pero `h1_missing` miraba toda
pagina HTML, asi que cada 404 salia sin H1 (55 de 656 en Lopesan) e inflaba
la recomendacion de encabezados del informe.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Heading, Issue, Job, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def test_h1_missing_solo_en_paginas_2xx():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[Job.__table__, Url.__table__, Issue.__table__, Heading.__table__]
    )
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()

    def url(ruta, status):
        u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta,
                is_internal=True, is_html=True, status_code=status)
        s.add(u)
        s.flush()
        return u

    con_h1 = url("/con-h1", 200)
    sin_h1 = url("/sin-h1", 200)
    url("/roto", 404)
    url("/caido", 503)
    s.add(Heading(url_id=con_h1.id, tag="h1", position=0, text="Titular"))
    s.commit()

    SEOAnalyzer(s, j.id).analyze_headings()
    s.commit()

    marcadas = s.execute(
        select(Issue.url_id).where(Issue.job_id == j.id, Issue.issue_type == "h1_missing")
    ).scalars().all()
    assert marcadas == [sin_h1.id]
