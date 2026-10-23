"""Reglas de las sondas de respuesta, sin red (decisiones 123 y 124)."""

from __future__ import annotations

import pytest

from shared import sondas as S
from shared.red import es_host_privado


@pytest.mark.parametrize("status,clase,esperado", [
    (200, None, S.OK), (206, None, S.OK), (304, None, S.OK), (416, None, S.OK),
    (404, None, S.ROTO), (410, None, S.ROTO), (400, None, S.ROTO), (405, None, S.ROTO),
    # lo que responde un WAF o quien pide credenciales no es un enlace roto
    (401, None, S.NO_VERIFICABLE), (403, None, S.NO_VERIFICABLE),
    (429, None, S.NO_VERIFICABLE), (999, None, S.NO_VERIFICABLE),
    (451, None, S.NO_VERIFICABLE), (406, None, S.NO_VERIFICABLE),
    (200, "challenge_waf", S.NO_VERIFICABLE), (503, "challenge_waf", S.NO_VERIFICABLE),
    (500, None, S.ERROR_SERVIDOR), (503, None, S.ERROR_SERVIDOR),
    (302, None, S.NO_VERIFICABLE), (None, None, S.NO_VERIFICABLE),
])
def test_veredicto_http(status, clase, esperado):
    assert S.veredicto_http(status, clase) == esperado


def test_un_roto_tras_un_salto_es_redirige_a_error():
    assert S.veredicto_final(404, None, 1) == S.REDIRIGE_A_ERROR
    assert S.veredicto_final(404, None, 0) == S.ROTO
    assert S.veredicto_final(200, None, 3) == S.OK


@pytest.mark.parametrize("status,repetir", [
    (200, False), (301, False), (404, True), (410, True), (405, True), (501, True),
    (403, True), (429, True), (500, True), (503, True),
])
def test_cuando_un_head_no_basta(status, repetir):
    assert S.repetir_con_get(status) is repetir


def test_tamano():
    assert S.tamano_de_cabeceras({"content-range": "bytes 0-1023/48213"}, 206, 1024) == 48213
    assert S.tamano_de_cabeceras({"content-range": "bytes 0-1023/*"}, 206, 1024) is None
    assert S.tamano_de_cabeceras({}, 206, 1024) is None
    assert S.tamano_de_cabeceras({"content-length": "5000"}, 200, None) == 5000
    assert S.tamano_de_cabeceras({}, 200, 812) == 812
    assert S.tamano_de_cabeceras({}, 404, 812) is None


@pytest.mark.parametrize("host,privado", [
    ("127.0.0.1", True), ("10.1.2.3", True), ("192.168.0.4", True), ("172.20.0.1", True),
    ("169.254.169.254", True), ("::1", True), ("[::1]", True), ("fe80::1", True),
    ("localhost", True), ("intranet.local", True), ("db.internal", True), ("", True),
    ("0.0.0.0", True),
    ("example.com", False), ("8.8.8.8", False), ("2001:4860:4860::8888", False),
    ("cdn.otro.es", False),
])
def test_host_privado(host, privado):
    assert es_host_privado(host) is privado


# Revision de #184: lo que curl convierte en una IP de la red interna y
# `ipaddress` no reconocia, y los nombres de una sola etiqueta que resuelve el
# DNS de Docker. Con la sonda de og:image encendida de serie y con GET, cada
# uno era un SSRF en cada rastreo.
@pytest.mark.parametrize("host", [
    "api", "postgres", "redis", "crawler",                  # servicios de docker-compose
    "2130706433", "0x7f000001", "0X7F000001", "127.1", "0177.0.0.1", "0x7f.0.0.1",
    "2852039166",                                             # 169.254.169.254
    "0xa9fea9fe", "169.254.43518",                            # tambien 169.254.169.254
    "012.0.0.1",                                              # 10.0.0.1 en octal
    "::ffff:127.0.0.1", "[::ffff:169.254.169.254]",
    "100.64.0.1",                                             # CGNAT, no global
    "999.1.1.1", "99999999999",                               # forma de IP que no cabe
])
def test_host_privado_en_las_formas_que_lee_curl(host):
    assert es_host_privado(host) is True


@pytest.mark.parametrize("host", ["8.8.8.8", "134744072", "0x08080808", "123.com",
                                  "1x.example.org", "cdn.otro.es"])
def test_lo_publico_sigue_siendo_publico(host):
    assert es_host_privado(host) is False


def test_la_sonda_no_pide_ni_redirige_a_un_host_de_docker():
    pytest.importorskip("scrapy")
    from seo_crawler.sonda_respuestas import SondaRespuestasMixin

    class Spider(SondaRespuestasMixin):
        job_id = "j"
        job_config = {"crawl_behavior": {"og_image_muestra": 10}}
        settings = None
        _exclude_patterns: list = []

        def _is_internal(self, url):
            return "x.com" in url

    sp = Spider()
    sp._iniciar_sondas_respuesta()
    for url in ("http://postgres:5432/x.png", "http://2130706433/x.png",
                "http://169.254.43518/latest/meta-data/"):
        assert not list(sp._sondear(url, "og_image", interno=False)), url
        assert sp._es_red_interna(url), url
    assert sp._sr_sin_comprobar["red_privada"] == 3


# ---------------------------------------------------------------------------
# El mixin, con lo minimo del spider
# ---------------------------------------------------------------------------
pytest.importorskip("scrapy")


class _Fallo:
    def __init__(self, exc):
        self.value = exc


def test_clasificar_fallo():
    from seo_crawler.sonda_respuestas import clasificar_fallo

    class CurlError(Exception):
        def __init__(self, code):
            super().__init__(f"curl: ({code})")
            self.code = code

    assert clasificar_fallo(_Fallo(CurlError(6))) == "dns"
    assert clasificar_fallo(_Fallo(CurlError(7))) == "conexion"
    assert clasificar_fallo(_Fallo(CurlError(28))) == "tiempo"
    assert clasificar_fallo(_Fallo(CurlError(60))) == "tls"
    assert clasificar_fallo(_Fallo(CurlError(63))) == "grande"
    assert clasificar_fallo(_Fallo(CurlError(52))) == "otro"
    from scrapy.exceptions import CannotResolveHostError, DownloadConnectionRefusedError

    assert clasificar_fallo(_Fallo(CannotResolveHostError("x"))) == "dns"
    assert clasificar_fallo(_Fallo(DownloadConnectionRefusedError("x"))) == "conexion"


class _Spider:
    def __init__(self, cb=None, rt=None):
        from seo_crawler.sonda_respuestas import SondaRespuestasMixin

        class S_(SondaRespuestasMixin):
            pass

        self.obj = S_()
        o = self.obj
        o.job_id = "j"
        o.job_config = {"crawl_behavior": cb or {}, "resource_types": rt or {}}
        o.settings = None
        o._exclude_patterns = []
        o._is_internal = lambda url: "//x.com" in url
        o.latidos = 0
        o.cancelar = False
        o.cerrado = None

        def _latido():
            o.latidos += 1

        o._write_heartbeat = _latido
        o._should_cancel = lambda: o.cancelar
        o.crawler = type("C", (), {"engine": type("E", (), {
            "close_spider": staticmethod(lambda sp, motivo: setattr(o, "cerrado", motivo))})()})()
        o._iniciar_sondas_respuesta()


def test_apagadas_no_piden_nada():
    sp = _Spider().obj
    assert list(sp._sondas_de_pagina([{"url": "https://otro.com/a"}],
                                     [{"url": "https://x.com/a.png"}])) == []


def test_un_robots_por_origen_y_lo_demas_espera():
    sp = _Spider({"comprobar_enlaces_externos": True}).obj
    links = [{"url": f"https://otro.com/{i}"} for i in range(3)] + [{"url": "https://x.com/i"}]
    salida = list(sp._sondas_de_pagina(links, []))
    assert [r.url for r in salida] == ["https://otro.com/robots.txt"]
    assert len(sp._sr_origenes["https://otro.com"]["espera"]) == 3


def test_ssrf_no_se_pide_a_la_red_interna():
    sp = _Spider({"comprobar_enlaces_externos": True}).obj
    links = [{"url": "http://169.254.169.254/latest/meta-data/"}, {"url": "http://10.0.0.5/"}]
    assert list(sp._sondas_de_pagina(links, [])) == []
    assert sp._sr_sin_comprobar["red_privada"] == 2


def test_tope_por_host():
    sp = _Spider({"comprobar_enlaces_externos": True, "sondas_por_host": 2}).obj
    links = [{"url": f"https://otro.com/{i}"} for i in range(5)]
    list(sp._sondas_de_pagina(links, []))
    assert len(sp._sr_origenes["https://otro.com"]["espera"]) == 2
    assert sp._sr_sin_comprobar["por_host"] == 3


def test_recursos_internos_y_externos_por_separado():
    recursos = [{"url": "https://x.com/a.png"}, {"url": "https://cdn.com/b.js"}]
    solo_int = _Spider({"comprobar_recursos": True}).obj
    pedidas = list(solo_int._sondas_de_pagina([], recursos))
    assert [(r.url, r.method) for r in pedidas] == [("https://x.com/a.png", "HEAD")]
    assert "dont_obey_robotstxt" not in pedidas[0].meta  # el robots del sitio, el de siempre
    solo_ext = _Spider(rt={"check_external_resources": True}).obj
    assert [r.url for r in solo_ext._sondas_de_pagina([], recursos)] == [
        "https://cdn.com/robots.txt"]


def test_la_sonda_no_gasta_max_urls_ni_es_parse():
    sp = _Spider({"comprobar_recursos": True}).obj
    (req,) = list(sp._sondas_de_pagina([], [{"url": "https://x.com/a.png"}]))
    assert req.callback == sp._sonda_respuesta and req.errback == sp._sonda_respuesta_error
    assert req.dont_filter and req.priority < 0 and req.meta["dont_retry"]


def test_late_por_tiempo_y_atiende_a_cancelar(monkeypatch):
    import seo_crawler.sonda_respuestas as m

    sp = _Spider({"comprobar_enlaces_externos": True}).obj
    reloj = [1000.0]
    monkeypatch.setattr(m.time, "monotonic", lambda: reloj[0])
    sp._sr_ultimo_latido = 1000.0
    sp._sr_latir()
    assert sp.latidos == 0
    reloj[0] += m.LATIDO_S + 1
    sp._sr_latir()
    assert sp.latidos == 1 and sp.cerrado is None
    sp.cancelar = True
    reloj[0] += m.LATIDO_S + 1
    sp._sr_latir()
    assert sp.cerrado == "cancelled"


def test_og_image_por_plantilla_a_cero_es_sin_tope_en_el_esquema_y_en_la_sonda():
    """Revision de #218: el esquema pedia >= 1 y la sonda trataba 0 como «sin
    tope». Ahora 0 vale en los dos sitios y significa lo mismo."""
    pytest.importorskip("pydantic")
    pytest.importorskip("scrapy")
    from api.schemas import CrawlBehaviorConfig
    from seo_crawler.sonda_respuestas import SondaRespuestasMixin

    assert CrawlBehaviorConfig(og_image_por_plantilla=0).og_image_por_plantilla == 0

    class Spider(SondaRespuestasMixin):
        job_id = "j"
        job_config = {"crawl_behavior": {"og_image_muestra": 10, "og_image_por_plantilla": 0}}
        settings = None
        _exclude_patterns: list = []

        def _is_internal(self, url):
            return "x.com" in url

    sp = Spider()
    sp._iniciar_sondas_respuesta()
    pedidas = [r for i in range(4)
               for r in sp._sonda_og_image(f"https://x.com/og{i}.png", f"https://x.com/p/{i}")]
    assert len(pedidas) == 4
    assert sp._sr_sin_comprobar["og_por_plantilla"] == 0
