"""La tuberia REAL con los enlaces no rastreables, contra Postgres (decision 106).

Lo que importa es lo que NO puede pasar: que un fallo al guardar estas filas se
lleve por delante las paginas y los enlaces del lote. La tabla se crea con el
`create_all` de la API, que en un despliegue arranca DESPUES del worker, y con
`docker compose up -d --build crawler` (el paso de «Al operar») no llega a
crearse nunca. Medido con la tuberia de la primera version: 1 de 100 paginas y
60 de 6.000 enlaces guardados.

Necesita Postgres de verdad (`PAGERANK_TEST_DATABASE_URL`, nunca `crawler_db`).
"""

from __future__ import annotations

import os
import types
import uuid

import pytest

pytest.importorskip("scrapy")

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")


@pytest.fixture()
def bd(monkeypatch):
    if not URL_BD:
        pytest.skip("sin PAGERANK_TEST_DATABASE_URL")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    import shared.database
    from shared.models import Base, EnlaceNoRastreable, Job

    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="test tuberia no rastreables", status="running",
                  seeds=[], config={}))
        s.commit()
    yield engine, Sesion, jid
    EnlaceNoRastreable.__table__.create(engine, checkfirst=True)
    with engine.begin() as c:
        for tabla in ("enlaces_no_rastreables", "links", "urls", "jobs"):
            col = "id" if tabla == "jobs" else "job_id"
            c.execute(text(f"DELETE FROM {tabla} WHERE {col} = :j"), {"j": str(jid)})
    engine.dispose()


def _pagina(jid, i, status=200):
    from seo_crawler.items import PageItem

    return PageItem(url=f"https://x.com/p/{i}", url_hash=f"h{i}", job_id=str(jid),
                    host="x.com", path=f"/p/{i}", scheme="https", is_internal=True,
                    crawl_depth=1, status_code=status, status_group=f"{status // 100}xx",
                    is_html=True, resource_type="html")


def _rastrear(Sesion, jid, paginas=100, enlaces=60, status=200, con_no_rastreables=True):
    from seo_crawler.items import EnlacesNoRastreablesItem, LinkItem
    from seo_crawler.pipelines import PostgresPipeline

    spider = types.SimpleNamespace(job_id=str(jid))
    tub = PostgresPipeline(batch_size=200, commit_every=50)
    tub.open_spider(spider)
    for i in range(paginas):
        tub.process_item(_pagina(jid, i, status), spider)
        for k in range(enlaces):
            tub.process_item(LinkItem(
                from_url_hash=f"h{i}", to_url=f"https://x.com/d/{k}", to_url_hash=f"d{k}",
                is_internal=True, link_position="content", job_id=str(jid), follow=True,
                repeticiones=1), spider)
        if con_no_rastreables and status == 200:
            tub.process_item(EnlacesNoRastreablesItem(
                url_hash=f"h{i}", job_id=str(jid),
                filas=[{"crawlability": "javascript_href", "href": "javascript:x()",
                        "repeticiones": 1}]), spider)
    tub.close_spider(spider)


def _cuentas(Sesion, jid):
    from shared.models import Link, Url

    with Sesion() as s:
        return (s.query(Url).filter(Url.job_id == jid).count(),
                s.query(Link).filter(Link.job_id == jid).count())


def test_sin_la_tabla_no_se_pierde_ninguna_pagina(bd):
    from shared.models import EnlaceNoRastreable

    engine, Sesion, jid = bd
    EnlaceNoRastreable.__table__.drop(engine)
    _rastrear(Sesion, jid)
    assert _cuentas(Sesion, jid) == (100, 6000)


def test_un_fallo_al_guardarlas_solo_se_lleva_esas_filas(bd, monkeypatch):
    """Cualquier otro fallo (un dato que no cabe, la tabla bloqueada) tampoco:
    va en un SAVEPOINT y no en la transaccion de las paginas."""
    from sqlalchemy.orm import Session

    from shared.models import EnlaceNoRastreable

    engine, Sesion, jid = bd
    original = Session.bulk_insert_mappings

    def _falla(self, mapper, filas, *a, **k):
        if mapper is EnlaceNoRastreable:
            raise RuntimeError("fallo simulado")
        return original(self, mapper, filas, *a, **k)

    monkeypatch.setattr(Session, "bulk_insert_mappings", _falla)
    _rastrear(Sesion, jid)
    assert _cuentas(Sesion, jid) == (100, 6000)


def test_una_pagina_que_ahora_falla_pierde_sus_no_rastreables(bd):
    """El item solo llega con HTML 2xx: sin borrarlos al re-rastrear, una
    pagina que ahora da 404 conservaba los de cuando respondia."""
    from shared.models import EnlaceNoRastreable

    engine, Sesion, jid = bd
    _rastrear(Sesion, jid, paginas=3, enlaces=1)
    with Sesion() as s:
        assert s.query(EnlaceNoRastreable).filter(EnlaceNoRastreable.job_id == jid).count() == 3
    _rastrear(Sesion, jid, paginas=1, enlaces=1, status=404)
    with Sesion() as s:
        quedan = s.query(EnlaceNoRastreable).filter(EnlaceNoRastreable.job_id == jid).count()
    assert quedan == 2
