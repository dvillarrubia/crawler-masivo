"""analyze_near_duplicates contra una BD real (SQLite en memoria).

Los tests de `test_near_duplicates.py` cubren las funciones puras de MinHash,
pero no la parte que lee y escribe en la BD. Ahi habia dos fallos que tiraban
el analisis ENTERO de cada job sin que ningun test lo viese: `Select.yield_per`
(no existe en SQLAlchemy 2.x) y un UPDATE en lote con WHERE que SQLAlchemy
rechaza. El job quedaba `completed` sin issues ni PageRank.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared.models import Base, Issue, Job, PageContent, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    # En SQLite solo INTEGER PRIMARY KEY se autoincrementa; BIGINT no.
    return "INTEGER"

TEXTO = " ".join(f"palabra{i}" for i in range(300))


@pytest.fixture()
def sesion():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[Job.__table__, Url.__table__, Issue.__table__, PageContent.__table__]
    )
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_analyze_near_duplicates_escribe_recuento_e_issues(sesion):
    from analysis.analyzer import SEOAnalyzer

    job_id = uuid.uuid4()
    sesion.add(Job(id=job_id, name="t", seeds=[], config={}))
    textos = {
        1: TEXTO,
        2: TEXTO.replace("palabra150", "otra"),  # casi identica a la 1
        3: " ".join(f"distinta{i}" for i in range(300)),
    }
    for uid, texto in textos.items():
        sesion.add(Url(id=uid, job_id=job_id, url=f"https://e.com/{uid}",
                       url_hash=f"h{uid}", is_html=True, status_code=200))
        sesion.add(PageContent(url_id=uid, content_text=texto))
    sesion.commit()

    SEOAnalyzer(sesion, job_id).analyze_near_duplicates()
    sesion.commit()

    cuentas = dict(sesion.execute(select(Url.id, Url.near_duplicate_count)).all())
    assert cuentas[1] == 1 and cuentas[2] == 1
    assert not cuentas[3]
    tipos = [i.issue_type for i in sesion.query(Issue).all()]
    assert tipos.count("near_duplicate_content") == 2
