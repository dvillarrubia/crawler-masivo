"""Modo lista en el spider de verdad (decision 128): `max_depth=0` rastrea las
semillas —y el destino de sus redirecciones, que no es un clic— y nada mas.
Ni sus enlaces, ni el sitemap salvo que se pida, ni la frontera al reanudar.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")

import shared.database  # noqa: E402

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")


def _rastrear(seeds, config):
    entrada = {"seeds": seeds, "config": config}
    proc = subprocess.run(
        [sys.executable, HARNESS, json.dumps(entrada)],
        capture_output=True, text=True, timeout=120,
    )
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    return {i["url"]: i for i in items if i["_tipo"] == "PageItem"}


def test_una_lista_rastrea_solo_las_semillas_y_sus_saltos():
    paginas = _rastrear(
        ["http://localhost:{port}/", "http://localhost:{port}/solo-sitemap"],
        {"max_depth": 0, "use_sitemap": False},
    )
    # La semilla que redirige se registra y su destino tambien: una
    # redireccion no es un clic (decision 27), y en QA la URL final es la que
    # importa.
    assert set(paginas) == {
        "http://SEMILLA/", "http://OTRO/home", "http://SEMILLA/solo-sitemap"}
    assert paginas["http://OTRO/home"]["crawl_depth"] == 0


def test_una_lista_con_sitemap_pedido_lo_lee_pero_no_sigue_enlaces():
    paginas = _rastrear(["http://localhost:{port}/"],
                        {"max_depth": 0, "use_sitemap": True})
    assert "http://SEMILLA/solo-sitemap" in paginas
    assert "http://SEMILLA/nivel2" not in paginas  # enlace de la del sitemap
    assert "http://OTRO/a" not in paginas          # enlace de la home


# --- Reanudar una lista no rastrea la frontera --------------------------------

@pytest.fixture
def spider(monkeypatch):
    monkeypatch.setattr(shared.database, "SessionLocal", lambda: None, raising=False)
    from seo_crawler.extractors import compute_url_hash, normalize_url
    from seo_crawler.spiders.seo_spider import SeoSpider, _hash_key

    sp = SeoSpider(job_id="t")
    sp.seed_urls = ["https://x.com/hecha", "https://x.com/pendiente"]
    sp._already_crawled_hashes = {
        _hash_key(compute_url_hash(normalize_url("https://x.com/hecha")))}
    sp._resume_mode = True
    sp._reintentar = []
    sp._use_sitemap = False
    sp._redis = None
    sp.render_js = False
    sp.allowed_hosts = {"x.com"}
    sp._crawl_subdomains = False
    sp._sondas_de_semillas = lambda: iter(())
    sp._should_follow = lambda url: True
    sp._iter_frontier = lambda: iter([("https://x.com/enlazada-desde-una-semilla", 1)])
    return sp


def _pedidas(sp):
    return {r.url for r in sp.start_requests()}


def test_al_reanudar_una_lista_no_se_rastrea_la_frontera(spider):
    """La frontera son los destinos de los enlaces de las semillas: con ella,
    un estancamiento reanudado convertia una lista de 50 URLs en un rastreo de
    todo lo que enlazan."""
    spider.max_depth = 0
    assert _pedidas(spider) == {"https://x.com/pendiente"}


def test_con_profundidad_si_se_rastrea_la_frontera(spider):
    spider.max_depth = 1
    assert "https://x.com/enlazada-desde-una-semilla" in _pedidas(spider)
