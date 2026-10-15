"""El SeoSpider real contra un sitio detras de un muro (decision 103).

El desafio es el REAL de Cloudflare, descargado de progym.es el 2026-10-16
(desde esta IP, por curl_cffi con la huella de Chrome, la home sale de la cache
con 200 y el resto responde 403 con `cf-mitigated: challenge`). Se lanza con el
arnes de `spider_harness.py`, en un subproceso.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")


def _rastrear(config: dict, muro: dict | None = None):
    entrada = {
        "seeds": ["http://localhost:{port}/muro-home"],
        "config": {"max_depth": 2, "use_sitemap": False, **config},
        "muro": muro or {},
    }
    proc = subprocess.run(
        [sys.executable, HARNESS, json.dumps(entrada)],
        capture_output=True, text=True, timeout=180,
    )
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    cierre = json.loads(proc.stdout.split("@@CIERRE@@", 1)[1].split("\n", 1)[0])
    return items, cierre, proc.stderr


def _paginas(items):
    return {i["url"]: i for i in items if i["_tipo"] == "PageItem"}


@pytest.fixture(scope="module")
def sin_parar():
    """Con `parar_si_bloqueado=false` se rastrea todo: se ve cada clase."""
    return _rastrear({"crawl_behavior": {"parar_si_bloqueado": False}})


@pytest.fixture(scope="module")
def parando():
    return _rastrear({"crawl_behavior": {"salud_ventana": 10, "salud_umbral": 0.8}},
                     muro={"n_muros": 300})


def test_el_desafio_de_cloudflare_no_es_la_pagina(sin_parar):
    items, cierre, _ = sin_parar
    p = _paginas(items)["http://SEMILLA/muro/0"]
    assert p["status_code"] == 403
    assert p["response_class"] == "challenge_waf"
    # No es un «Client Error (403)» del sitio: es que no nos dejaron verla.
    assert p["indexability_status"] == "Bloqueada (WAF)"
    assert cierre["reason"] == "finished"


def test_el_desafio_servido_con_200_tampoco_es_indexable(sin_parar):
    """El caso peligroso: 200, HTML valido, «Just a moment...» y sin cabecera."""
    items, _, _ = sin_parar
    p = _paginas(items)["http://SEMILLA/muro-200"]
    assert p["status_code"] == 200
    assert p["response_class"] == "challenge_waf"
    assert p["indexability_status"] == "Bloqueada (WAF)"


def test_el_muro_no_aporta_metadatos_ni_enlaces(sin_parar):
    items, _, _ = sin_parar
    muro = _paginas(items)["http://SEMILLA/muro-200"]
    metas = {i["url_hash"] for i in items if i["_tipo"] == "HtmlMetaItem"}
    origenes = {i["from_url_hash"] for i in items if i["_tipo"] == "LinkItem"}
    assert muro["url_hash"] not in metas
    assert muro["url_hash"] not in origenes


def test_el_cuerpo_binario_de_la_decision_11(sin_parar):
    items, _, _ = sin_parar
    p = _paginas(items)["http://SEMILLA/binaria"]
    assert p["response_class"] == "respuesta_binaria"
    assert p["indexability_status"] == "Respuesta no valida (binaria)"


def test_un_articulo_sobre_captchas_es_contenido(sin_parar):
    items, _, _ = sin_parar
    paginas = _paginas(items)
    art = paginas["http://SEMILLA/articulo-captcha"]
    assert art["response_class"] == "contenido"
    assert art["indexability_status"] == "Indexable"
    assert paginas["http://SEMILLA/muro-home"]["response_class"] == "contenido"


def test_la_ventana_para_el_rastreo_bloqueado(parando):
    items, cierre, stderr = parando
    assert cierre["reason"] == "bloqueado_por_waf"
    # Para pronto: de las 303 paginas que enlaza la home no se piden mas que
    # las que ya estaban en vuelo (la concurrencia de Scrapy es 16 por host).
    assert len(_paginas(items)) < 100
    assert "BLOQUEADO" in stderr


def test_un_429_masivo_para_como_rate_limited():
    items, cierre, _ = _rastrear(
        {"crawl_behavior": {"salud_ventana": 10}},
        muro={"codigo": 429, "n_muros": 300},
    )
    assert cierre["reason"] == "rate_limited"
    assert len(_paginas(items)) < 100
    limitadas = [p for p in _paginas(items).values() if p["status_code"] == 429]
    assert limitadas and all(p["response_class"] == "rate_limited" for p in limitadas)
    assert all(p["indexability_status"] == "Limitada (rate limit)" for p in limitadas)
