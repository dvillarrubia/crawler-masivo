"""Al reanudar, la frontera entra con la profundidad que habria tenido (#276).

Antes cada destino pendiente entraba con profundidad 1: con `max_depth`, la
reanudacion volvia a bajar `max_depth` niveles desde cada uno, y pedia los
destinos de las paginas del ultimo nivel y los de enlaces nofollow, que el
rastreo sin interrumpir no habria pedido.

Necesita Postgres de verdad:

    PAGERANK_TEST_DATABASE_URL=postgresql+psycopg2://crawler:crawler@postgres:5432/crawler_test pytest tests/test_frontera_reanudar_db.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import uuid

import pytest

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL")
pytest.importorskip("scrapy")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crawler"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import shared.database  # noqa: E402
from shared.models import Base, Job, Link, Url  # noqa: E402

BASE = "https://x.com"


def _h(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


@pytest.fixture()
def bd(monkeypatch):
    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    s = Sesion()
    j = Job(id=uuid.uuid4(), name="test frontera", status="running",
            seeds=[BASE + "/"], config={})
    s.add(j)
    s.flush()
    filas = {}
    for ruta, depth in [("/", 0), ("/a", 1), ("/b", 2), ("/c", 3), ("/sin", None)]:
        u = Url(job_id=j.id, url=BASE + ruta, url_hash=_h(BASE + ruta),
                is_internal=True, status_code=200, is_html=True, crawl_depth=depth)
        s.add(u)
        filas[ruta] = u
    s.flush()

    def enlace(desde, hacia, follow=True, interno=True):
        s.add(Link(job_id=j.id, from_url_id=filas[desde].id, to_url=BASE + hacia,
                   to_url_hash=_h(BASE + hacia), is_internal=interno, follow=follow))

    enlace("/", "/a")              # ya rastreada: no es frontera
    enlace("/", "/p1")             # desde 0 -> 1
    enlace("/b", "/p1")            # y desde 2: cuenta la menor
    enlace("/a", "/p2")            # desde 1 -> 2
    enlace("/b", "/p3")            # desde 2 -> 3
    enlace("/c", "/p4")            # desde 3 = max_depth: no se habria seguido
    enlace("/a", "/p5", follow=False)  # nofollow: tampoco
    enlace("/sin", "/p6")          # origen sin profundidad: la de antes, 1
    enlace("/a", "/fuera", interno=False)
    s.commit()
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    yield j
    s.delete(s.get(Job, j.id))
    s.commit()
    s.close()
    engine.dispose()


def _spider(job, max_depth=3, follow_nofollow=False):
    from seo_crawler.spiders.seo_spider import SeoSpider

    sp = SeoSpider(job_id=str(job.id))
    sp.max_depth = max_depth
    sp._follow_nofollow = follow_nofollow
    sp._redis = None
    sp._write_heartbeat = lambda: None
    return sp


def test_la_frontera_lleva_la_menor_profundidad_de_sus_origenes(bd):
    frontera = dict(_spider(bd)._iter_frontier())
    assert frontera == {BASE + "/p1": 1, BASE + "/p2": 2, BASE + "/p3": 3, BASE + "/p6": 1}


def test_con_follow_nofollow_entran_los_nofollow(bd):
    frontera = dict(_spider(bd, follow_nofollow=True)._iter_frontier())
    assert frontera[BASE + "/p5"] == 2


def test_con_mas_profundidad_entra_lo_del_ultimo_nivel(bd):
    frontera = dict(_spider(bd, max_depth=4)._iter_frontier())
    assert frontera[BASE + "/p4"] == 4


def test_la_paginacion_por_lotes_no_pierde_destinos(bd, monkeypatch):
    """El keyset va por grupos (`GROUP BY`): con lotes de 1 sale lo mismo."""
    from seo_crawler.spiders.seo_spider import SeoSpider

    entera = list(_spider(bd)._iter_frontier())
    monkeypatch.setattr(SeoSpider, "_LOTE_FRONTERA", 1)
    assert list(_spider(bd)._iter_frontier()) == entera
