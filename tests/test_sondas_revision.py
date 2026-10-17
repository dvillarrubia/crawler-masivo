"""Revision de las sondas de respuesta (decisiones 123 y 124).

Tres fallos reproducidos ejecutando el mixin y los middlewares de Scrapy:

- una redireccion de un tercero hacia la red interna se seguia (SSRF): el
  filtro de `shared/red.py` solo miraba la URL ENLAZADA, no los saltos;
- lo mismo con el robots.txt del tercero, que redirigia el `RedirectMiddleware`
  de Scrapy sin pasar por ningun filtro;
- un fallo de DNS TEMPORAL (el resolver no contesta, `EAI_AGAIN`) salia como
  `sin_conexion` y, con dos, el host acababa en `dominio_externo_muerto`: un
  fallo del resolver del rastreo afirmado como un dominio muerto del tercero.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("scrapy")

from test_sondas import _Spider  # noqa: E402


def _respuesta(url: str, status: int, meta: dict, headers: dict | None = None,
               method: str = "HEAD"):
    from scrapy.http import Request, Response

    req = Request(url, method=method, meta=meta)
    return Response(url, status=status, headers=headers or {}, request=req)


def _abierto(sp, origen: str) -> None:
    sp._sr_origenes[origen] = {"estado": "abierto", "robots": None, "espera": [],
                               "fallos": 0, "veredicto": None, "error": None}


def _ctx(url: str) -> dict:
    from seo_crawler.extractors import compute_url_hash

    return {"url": url, "url_hash": compute_url_hash(url), "uso": "enlace",
            "interno": False, "host": "otro.com", "saltos": 0, "status": None}


# ---------------------------------------------------------------------------
# SSRF por redireccion
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("destino", [
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/admin",
    "http://localhost:6379/",
    "http://redis.internal/",
])
def test_una_redireccion_de_un_tercero_a_la_red_interna_no_se_sigue(destino):
    from scrapy import Request

    from shared import sondas as S

    sp = _Spider({"comprobar_enlaces_externos": True}).obj
    _abierto(sp, "https://otro.com")
    url = "https://otro.com/salto"
    resp = _respuesta(url, 302, {"sonda_respuesta": _ctx(url),
                                 "sonda_origen": "https://otro.com"},
                      {"Location": destino})
    salida = list(sp._sonda_respuesta(resp))
    pedidas = [r for r in salida if isinstance(r, Request)]
    assert pedidas == [], [r.url for r in pedidas]
    (fila,) = salida
    assert fila["veredicto"] == S.NO_VERIFICABLE
    assert fila["final_url"] == destino and "red interna" in fila["error"]


def test_una_redireccion_a_otro_tercero_publico_si_se_sigue():
    sp = _Spider({"comprobar_enlaces_externos": True}).obj
    _abierto(sp, "https://otro.com")
    url = "https://otro.com/salto"
    resp = _respuesta(url, 301, {"sonda_respuesta": _ctx(url),
                                 "sonda_origen": "https://otro.com"},
                      {"Location": "https://tercero.org/fin"})
    (req,) = list(sp._sonda_respuesta(resp))
    assert req.url == "https://tercero.org/robots.txt"


# ---------------------------------------------------------------------------
# El robots.txt del tercero
# ---------------------------------------------------------------------------
def _redirect_mw():
    from scrapy.downloadermiddlewares.redirect import RedirectMiddleware
    from scrapy.utils.test import get_crawler

    return RedirectMiddleware.from_crawler(get_crawler())


def test_el_redirect_middleware_no_sigue_el_robots_del_tercero():
    """Lo seguia Scrapy por su cuenta: a donde dijera el tercero."""
    from scrapy.http import Response

    sp = _Spider({"comprobar_enlaces_externos": True}).obj
    (req,) = list(sp._sondas_de_pagina([{"url": "https://otro.com/a"}], []))
    resp = Response(req.url, status=301, request=req,
                    headers={"Location": "http://169.254.169.254/robots.txt"})
    salida = _redirect_mw().process_response(req, resp)
    assert salida is resp  # sin seguir: lo decide la sonda


def test_el_robots_que_redirige_a_la_red_interna_se_trata_como_sin_robots():
    from scrapy.http import Response

    sp = _Spider({"comprobar_enlaces_externos": True}).obj
    (req,) = list(sp._sondas_de_pagina([{"url": "https://otro.com/a"}], []))
    resp = Response(req.url, status=301, request=req,
                    headers={"Location": "http://169.254.169.254/robots.txt"})
    salida = list(sp._sonda_robots(resp))
    assert [r.url for r in salida] == ["https://otro.com/a"]  # sin pedir la interna
    assert sp._sr_origenes["https://otro.com"]["estado"] == "abierto"


def test_el_robots_que_redirige_se_sigue_y_sus_reglas_valen():
    from scrapy.http import Response

    from shared import sondas as S

    sp = _Spider({"comprobar_enlaces_externos": True}).obj
    (req,) = list(sp._sondas_de_pagina([{"url": "http://otro.com/privado/a"}], []))
    r1 = Response(req.url, status=301, request=req,
                  headers={"Location": "https://otro.com/robots.txt"})
    (req2,) = list(sp._sonda_robots(r1))
    assert req2.url == "https://otro.com/robots.txt"
    assert req2.meta["sonda_robots"] == "http://otro.com"
    r2 = Response(req2.url, status=200, request=req2,
                  body=b"User-agent: *\nDisallow: /privado/\n")
    (fila,) = list(sp._sonda_robots(r2))
    assert fila["veredicto"] == S.PROHIBIDO_POR_ROBOTS


def test_el_robots_no_sigue_redirecciones_sin_fin():
    from scrapy.http import Response

    sp = _Spider({"comprobar_enlaces_externos": True}).obj
    (req,) = list(sp._sondas_de_pagina([{"url": "https://otro.com/a"}], []))
    for _ in range(10):
        resp = Response(req.url, status=302, request=req,
                        headers={"Location": req.url + "?x"})
        salida = list(sp._sonda_robots(resp))
        if not salida or salida[0].url == "https://otro.com/a":
            break
        (req,) = salida
    assert [r.url for r in salida] == ["https://otro.com/a"]


# ---------------------------------------------------------------------------
# DNS temporal no es un dominio muerto
# ---------------------------------------------------------------------------
class _CurlError(Exception):
    def __init__(self, code):
        super().__init__(f"curl: ({code}) Could not resolve host")
        self.code = code


class _Failure:
    def __init__(self, exc, request):
        self.value = exc
        self.request = request

    def check(self, *tipos):
        return any(isinstance(self.value, t) for t in tipos)


async def _todo(agen):
    return [x async for x in agen]


def test_un_fallo_temporal_del_resolver_no_da_un_dominio_muerto():
    from analysis.avisos_de_sondas import hosts_muertos
    from shared import sondas as S

    sp = _Spider({"comprobar_enlaces_externos": True}).obj

    async def _temporal(host, puerto):
        return "temporal"

    sp._resolver = _temporal
    links = [{"url": "https://vivo.com/a"}, {"url": "https://vivo.com/b"}]
    (robots,) = list(sp._sondas_de_pagina(links, []))
    filas = []
    salida = asyncio.run(_todo(sp._sonda_robots_error(_Failure(_CurlError(6), robots))))
    (prueba,) = salida  # se prueba una URL
    filas += asyncio.run(_todo(sp._sonda_respuesta_error(_Failure(_CurlError(6), prueba))))
    assert {f["url"] for f in filas} == {"https://vivo.com/a", "https://vivo.com/b"}
    # El origen se deja de pedir (ahorra esperas), pero no se afirma nada.
    assert sp._sr_origenes["https://vivo.com"]["estado"] == "caido"
    assert all(f["veredicto"] not in S.MUERTOS for f in filas), filas
    filas_ns = [SimpleNamespace(**dict(f)) for f in filas]
    assert hosts_muertos(filas_ns) == {}


def test_un_nxdomain_confirmado_sigue_siendo_dominio_muerto():
    from analysis.avisos_de_sondas import hosts_muertos
    from shared import sondas as S

    sp = _Spider({"comprobar_enlaces_externos": True}).obj

    async def _no_existe(host, puerto):
        return "no_existe"

    sp._resolver = _no_existe
    (robots,) = list(sp._sondas_de_pagina([{"url": "https://muerto.com/a"}], []))
    filas = asyncio.run(_todo(sp._sonda_robots_error(_Failure(_CurlError(6), robots))))
    assert [f["veredicto"] for f in filas] == [S.DOMINIO_INEXISTENTE]
    assert set(hosts_muertos([SimpleNamespace(**dict(f)) for f in filas])) == {"muerto.com"}


# ---------------------------------------------------------------------------
# Lo que el cierre deja en la cola se dice
# ---------------------------------------------------------------------------
def test_al_cerrar_se_avisa_de_las_sondas_que_quedaron_en_la_cola(caplog):
    """Van detras de las paginas: un `max_urls` con frontera por delante cierra
    con ellas encoladas, y el log decia «N pedidas» sin mas."""
    import logging

    from scrapy.http import Response

    sp = _Spider({"comprobar_enlaces_externos": True}).obj
    links = [{"url": f"https://otro.com/{i}"} for i in range(3)]
    (robots,) = list(sp._sondas_de_pagina(links, []))
    (req, *_resto) = list(sp._sonda_robots(Response(robots.url, status=404, request=robots)))
    list(sp._sonda_respuesta(Response(req.url, status=200, request=req)))
    with caplog.at_level(logging.WARNING, logger="seo_crawler.sonda_respuestas"):
        sp._sr_al_cerrar("max_urls_reached")
    assert "2 de 3 sin respuesta al cerrar (max_urls_reached)" in caplog.text
