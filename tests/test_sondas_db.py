"""Sondas de respuesta contra Postgres (decisiones 123 y 124): la tuberia real
(upsert por job y URL), la reanudacion (lo ya sondeado no se repite y gasta su
presupuesto) y el analisis entero, con el UPDATE de `resources.size_bytes`.

Necesita Postgres de verdad (`PAGERANK_TEST_DATABASE_URL`, nunca `crawler_db`).
"""

from __future__ import annotations

import hashlib
import os
import types
import uuid

import pytest

pytest.importorskip("scrapy")

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL")


def _h(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


@pytest.fixture()
def bd(monkeypatch):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    import shared.database
    from shared.models import Base, Job

    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="test sondas", status="running",
                  seeds=["https://x.com/"], config={}))
        s.commit()
    yield engine, Sesion, jid
    with engine.begin() as c:
        c.execute(text("DELETE FROM resources WHERE url_id IN (SELECT id FROM urls WHERE job_id = :j)"),
                  {"j": str(jid)})
        for tabla in ("respuestas_sondeadas", "issues", "links", "urls", "jobs"):
            col = "id" if tabla == "jobs" else "job_id"
            c.execute(text(f"DELETE FROM {tabla} WHERE {col} = :j"), {"j": str(jid)})
    engine.dispose()


def _item(jid, url, veredicto, **kw):
    from seo_crawler.items import RespuestaSondeadaItem

    campos = dict(job_id=str(jid), url_hash=_h(url), url=url, host="otro.com", uso="enlace",
                  interno=False, metodo="GET", status_code=404, final_url=None,
                  final_status=None, saltos=0, veredicto=veredicto, response_class=None,
                  host_caido=None, content_type="text/html", content_length=None, error=None)
    campos.update(kw)
    return RespuestaSondeadaItem(**campos)


def test_la_tuberia_guarda_una_fila_por_url_y_gana_la_ultima(bd):
    from seo_crawler.pipelines import PostgresPipeline
    from shared.models import RespuestaSondeada

    _, Sesion, jid = bd
    spider = types.SimpleNamespace(job_id=str(jid))
    tub = PostgresPipeline(batch_size=2, commit_every=50)
    tub.open_spider(spider)
    tub.process_item(_item(jid, "https://otro.com/a", "no_verificable", status_code=403), spider)
    tub.process_item(_item(jid, "https://otro.com/b", "roto"), spider)  # vuelca el lote
    tub.process_item(_item(jid, "https://otro.com/a", "ok", status_code=200), spider)
    tub.close_spider(spider)
    with Sesion() as s:
        filas = {f.url: (f.veredicto, f.status_code) for f in
                 s.query(RespuestaSondeada).filter(RespuestaSondeada.job_id == jid)}
    assert filas == {"https://otro.com/a": ("ok", 200), "https://otro.com/b": ("roto", 404)}


def test_al_reanudar_lo_ya_sondeado_no_se_repite(bd):
    from seo_crawler.sonda_respuestas import SondaRespuestasMixin
    from shared.models import RespuestaSondeada

    _, Sesion, jid = bd
    with Sesion() as s:
        s.add(RespuestaSondeada(job_id=jid, url_hash=_h("https://otro.com/ya"),
                                url="https://otro.com/ya", host="otro.com", uso="enlace",
                                interno=False, veredicto="ok"))
        s.commit()

    class Spider(SondaRespuestasMixin):
        job_id = jid
        job_config = {"crawl_behavior": {"comprobar_enlaces_externos": True,
                                         "enlaces_externos_max": 2}}
        settings = None
        _exclude_patterns: list = []

        def _is_internal(self, url):
            return "x.com" in url

    sp = Spider()
    sp._iniciar_sondas_respuesta()
    assert sp._sr_usadas["enlace"] == 1
    import seo_crawler.extractors as ex

    assert ex.normalize_url("https://otro.com/ya") == "https://otro.com/ya"
    ya = list(sp._sondear("https://otro.com/ya", "enlace", interno=False))
    assert not ya, "lo sondeado en la ejecucion anterior no se repite"
    nueva = list(sp._sondear("https://nuevo.com/x", "enlace", interno=False))
    otra = list(sp._sondear("https://nuevo.com/y", "enlace", interno=False))
    assert nueva and not otra, "el tope cuenta lo ya sondeado en la ejecucion anterior"
    assert sp._sr_sin_comprobar["tope"] == 1


def test_el_analisis_entero_con_postgres(bd):
    pytest.importorskip("numpy")
    from sqlalchemy import select

    from analysis.analyzer import SEOAnalyzer
    from shared.models import Issue, Job, Link, Resource, RespuestaSondeada, Url

    _, Sesion, jid = bd
    with Sesion() as s:
        p = Url(job_id=jid, url="https://x.com/", url_hash=_h("https://x.com/"),
                is_internal=True, is_html=True, status_code=200, status_group="2xx",
                crawl_depth=0)
        s.add(p)
        s.flush()
        for destino, v, st, caido in (("https://otro.com/roto", "roto", 410, None),
                                      ("https://muerto.com/x", "dominio_inexistente", None, True)):
            s.add(Link(job_id=jid, from_url_id=p.id, to_url=destino, to_url_hash=_h(destino),
                       is_internal=False, link_position="content", follow=True, repeticiones=1))
            s.add(RespuestaSondeada(job_id=jid, url_hash=_h(destino), url=destino,
                                    host=destino.split("/")[2], uso="enlace", interno=False,
                                    veredicto=v, status_code=st, host_caido=caido))
        s.add(Resource(url_id=p.id, resource_url="https://x.com/a.png", resource_type="image"))
        s.add(Resource(url_id=p.id, resource_url="https://x.com/b.css", resource_type="css"))
        s.add(RespuestaSondeada(job_id=jid, url_hash=_h("https://x.com/a.png"),
                                url="https://x.com/a.png", host="x.com", uso="recurso",
                                interno=True, veredicto="roto", status_code=404))
        s.add(RespuestaSondeada(job_id=jid, url_hash=_h("https://x.com/b.css"),
                                url="https://x.com/b.css", host="x.com", uso="recurso",
                                interno=True, veredicto="ok", status_code=200,
                                content_length=777))
        s.commit()
        SEOAnalyzer(s, jid).run_all()
        tipos = {t for (t,) in s.execute(select(Issue.issue_type).where(Issue.job_id == jid))}
        assert {"enlace_externo_roto", "dominio_externo_muerto", "broken_resource"} <= tipos
        assert s.execute(select(Resource.size_bytes).where(
            Resource.resource_url == "https://x.com/b.css", Resource.url_id == p.id)).scalar() == 777
        s.expire_all()
        resumen = s.get(Job, jid).sondas_resumen
        assert resumen["enlaces_externos"]["distintos"] == 2
        assert resumen["enlaces_externos"]["comprobados"] == 2
