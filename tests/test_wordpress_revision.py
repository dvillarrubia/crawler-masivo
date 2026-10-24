"""Revision de la PR #226 (decision 161): el adaptador WordPress en el spider,
sin red y sin arnes.

- Latido y cancelacion mientras lee la REST API, y un tope de tiempo: con un
  Crawl-delay alto la cola sigue cuando ya no quedan paginas.
- Una reanudacion no borra lo leido ni afirma «no detectado» si la ejecucion
  anterior lo detecto; si lo leyo entero, no lo repite.
- Al reanudar se vuelve a pedir la raiz que confirmo la ejecucion anterior
  (subcarpeta), no `/wp-json/` del origen.
- Solo se piden raices y saltos del sitio: ni otro host ni red privada.
- Un 429/503 en el indice es «limitada», no «cerrada»; en una sonda, corta.
"""

from __future__ import annotations

import json
import types
import uuid

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

pytest.importorskip("scrapy")

import scrapy  # noqa: E402
from scrapy.http import HtmlResponse, TextResponse  # noqa: E402

from shared.models import AdapterEntity, Base, Job  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


class _Redis:
    def __init__(self, cancel=False):
        self.escrito: dict = {}
        self.cancel = cancel

    def set(self, k, v, *a, **kw):
        self.escrito[k] = v

    def get(self, k):
        return "1" if self.cancel and k.endswith(":cancel") else None


@pytest.fixture()
def base(tmp_path, monkeypatch):
    import shared.database

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    return Sesion


def _job(Sesion, adapters=None):
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="t", status="running", seeds=["https://x.com/"],
                  config={"max_depth": 5}, adapters=adapters))
        s.add(AdapterEntity(job_id=jid, adaptador="wordpress", tipo="post",
                            url="https://x.com/e/", url_hash="h"))
        s.commit()
        return s.get(Job, jid)


def _spider(job=None, config=None, redis=None):
    from seo_crawler.spiders.seo_spider import SeoSpider

    sp = SeoSpider(job_id=str(job.id) if job else "t")
    sp.job_config = config or {"max_depth": 5}
    sp.max_depth = 5
    sp.seed_urls = ["https://x.com/"]
    sp.allowed_hosts = {"x.com", "www.x.com"}
    sp._crawl_subdomains = False
    sp._exclude_patterns, sp._include_patterns = [], []
    sp._max_url_length = sp._max_folder_depth = 0
    sp._allowed_resource_types = {"html", "redirect"}
    sp._redis = redis
    sp._resume_mode = False
    sp._iniciar_wordpress(job)
    return sp


def _respuesta(url, status=200, cuerpo=b"", meta=None, headers=None, html=False):
    req = scrapy.Request(url, meta=meta or {})
    clase = HtmlResponse if html else TextResponse
    return clase(url, status=status, body=cuerpo, headers=headers or {}, request=req,
                 encoding="utf-8")


def _cuantas(Sesion, job):
    with Sesion() as s:
        return s.query(AdapterEntity).filter(AdapterEntity.job_id == job.id).count()


# --- 1. Latido, cancelacion y tope de tiempo ----------------------------------

def test_cada_respuesta_de_la_rest_api_late_y_mira_la_cancelacion():
    redis = _Redis(cancel=True)
    cerrado = []
    sp = _spider(redis=redis)
    sp.crawler = types.SimpleNamespace(engine=types.SimpleNamespace(
        close_spider=lambda spider, motivo: cerrado.append(motivo)))
    sp._wp_ultimo_latido = 0.0
    list(sp.parse_wp(_respuesta("https://x.com/wp-json/", 404, meta={"wp_rol": "indice",
                                                                       "wp_raiz": "https://x.com/wp-json/"})))
    assert any(k.endswith(":heartbeat") for k in redis.escrito)
    assert cerrado == ["cancelled"]


def test_el_error_tambien_late():
    redis = _Redis()
    sp = _spider(redis=redis)
    sp._wp_ultimo_latido = 0.0
    fallo = types.SimpleNamespace(request=scrapy.Request("https://x.com/wp-json/",
                                                         meta={"wp_rol": "indice"}),
                                  value=RuntimeError("x"), check=lambda *a: False)
    list(sp.error_wp(fallo))
    assert any(k.endswith(":heartbeat") for k in redis.escrito)


def test_pasado_el_tope_de_tiempo_se_deja_de_pedir():
    sp = _spider(config={"max_depth": 5, "adapters": {"wordpress": {"max_minutos": 1}}})
    sp._wp_encolar("https://x.com/wp-json/", "indice")
    sp._wp_inicio_s -= 61
    sp._wp_encolar("https://x.com/wp-json/wp/v2/types", "tipos")
    assert sp._wp_resumen["cortada"] == "tope_de_tiempo"
    assert not sp._wp_cola


# --- 1. Reanudacion -----------------------------------------------------------

def test_una_reanudacion_no_borra_al_arrancar_ni_afirma_no_detectado(base):
    previo = {"detectado": True, "rest": "abierta", "raiz": "https://x.com/blog/wp-json/"}
    job = _job(base, {"wordpress": previo})
    sp = _spider(job)
    assert sp._wp_activo(), "sin leerla entera, se vuelve a intentar"
    assert _cuantas(base, job) == 1, "no se borra al arrancar"
    sp._wp_al_cerrar("finished")  # esta vez no llego a confirmarla
    with base() as s:
        wp = s.get(Job, job.id).adapters["wordpress"]
    assert wp["detectado"] is True and wp["rest"] == "abierta"
    assert "reanudacion" in wp
    assert _cuantas(base, job) == 1


def test_al_confirmar_en_esta_ejecucion_se_borra_lo_anterior(base):
    job = _job(base, {"wordpress": {"detectado": True, "rest": "abierta",
                                    "raiz": "https://x.com/wp-json/"}})
    sp = _spider(job)
    indice = json.dumps({"namespaces": ["wp/v2"], "home": "https://x.com"}).encode()
    list(sp.parse_wp(_respuesta("https://x.com/wp-json/", 200, indice,
                                meta={"wp_rol": "indice", "wp_raiz": "https://x.com/wp-json/"})))
    assert _cuantas(base, job) == 0


def test_si_la_anterior_la_leyo_entera_no_se_repite(base):
    job = _job(base, {"wordpress": {"detectado": True, "rest": "abierta", "completa": True}})
    sp = _spider(job)
    assert not sp._wp_activo()
    assert _cuantas(base, job) == 1


# --- 3. La raiz de la ejecucion anterior al reanudar ----------------------------

def test_al_reanudar_se_pide_la_raiz_confirmada_antes(base):
    job = _job(base, {"wordpress": {"detectado": True, "rest": "abierta",
                                    "raiz": "https://x.com/blog/wp-json/"}})
    sp = _spider(job)
    sp._resume_mode = True
    pedidas = list(sp._wp_de_pagina(_respuesta("https://x.com/blog/otra/", html=True,
                                               cuerpo=b"<html></html>"), 3, None))
    assert [r.url for r in pedidas] == [
        "https://x.com/blog/wp-json/?_fields=name,url,home,namespaces,show_on_front,page_for_posts"]


# --- 4. Solo el sitio -----------------------------------------------------------

@pytest.mark.parametrize("href", ["http://api:8000/wp-json/", "http://169.254.169.254/wp-json/",
                                  "https://otro.com/wp-json/"])
def test_una_raiz_anunciada_que_no_es_del_sitio_no_se_pide(href):
    sp = _spider()
    cuerpo = f'<html><head><link rel="https://api.w.org/" href="{href}"></head></html>'.encode()
    r = _respuesta("https://x.com/", html=True, cuerpo=cuerpo)
    pedidas = list(sp._wp_de_pagina(r, 0, r.selector))
    assert not [p for p in pedidas if href.split("/")[2] in p.url]


def test_un_salto_a_red_privada_no_se_sigue():
    sp = _spider()
    sp._wp_raiz = "https://x.com/wp-json/"
    r = _respuesta("https://x.com/wp-json/wp/v2/types", 301,
                   headers={"Location": "http://10.0.0.5/wp-json/wp/v2/types"},
                   meta={"wp_rol": "tipos"})
    assert list(sp.parse_wp(r)) == []
    r = _respuesta("http://x.com/wp-json/wp/v2/types", 301,
                   headers={"Location": "https://x.com/wp-json/wp/v2/types"},
                   meta={"wp_rol": "tipos"})
    (sigue,) = list(sp.parse_wp(r))
    assert sigue.url == "https://x.com/wp-json/wp/v2/types"
    assert sigue.meta["wp_saltos"] == 1 and sigue.meta["dont_redirect"] is True


def test_la_semilla_privada_si_se_lee():
    """Un staging en una IP privada lo elige el usuario al sembrar."""
    sp = _spider()
    sp.seed_urls = ["http://10.0.0.5/"]
    sp.allowed_hosts = {"10.0.0.5"}
    assert sp._wp_puede_pedir("http://10.0.0.5/wp-json/")


# --- 5. 429/503 -----------------------------------------------------------------

def test_un_429_en_el_indice_es_limitada():
    sp = _spider()
    list(sp.parse_wp(_respuesta("https://x.com/wp-json/", 429,
                                meta={"wp_rol": "indice", "wp_raiz": "https://x.com/wp-json/"})))
    assert sp._wp_resumen["rest"] == "limitada"


def test_un_503_en_una_sonda_corta_la_lectura():
    sp = _spider()
    sp._wp_encolar("https://x.com/page/9/", "sonda_paginacion")
    items = list(sp.parse_wp(_respuesta("https://x.com/page/8/", 503,
                                        meta={"wp_rol": "sonda_paginacion", "wp_datos": {}})))
    assert sp._wp_resumen["cortada"] == "503"
    assert not [i for i in items if not isinstance(i, scrapy.Request)]
    assert not sp._wp_cola
