"""`Retry-After` en 429 y 503 (decision 111): el parser y el piso por slot.

El ritmo real contra un servidor se mide en `test_handler_medidas.py`, con el
spider de verdad; aqui la parte pura y el middleware con un slot simulado,
como `test_crawl_delay.py`.
"""

from __future__ import annotations

import time
import types
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import pytest

from shared.respuestas import retry_after_segundos


# -- El parser (shared/respuestas.py) --------------------------------------

def test_segundos():
    assert retry_after_segundos("120") == 120
    assert retry_after_segundos(b" 5 ") == 5
    assert retry_after_segundos("0") == 0


def test_fecha_http_en_30_segundos():
    ahora = time.time()
    fecha = format_datetime(datetime.fromtimestamp(ahora + 30, tz=timezone.utc), usegmt=True)
    assert retry_after_segundos(fecha, ahora=ahora) == pytest.approx(30, abs=1)


def test_fecha_pasada_es_cero():
    fecha = format_datetime(datetime.now(timezone.utc) - timedelta(hours=1), usegmt=True)
    assert retry_after_segundos(fecha) == 0


@pytest.mark.parametrize("valor", ["abc", "-3", "nan", "inf", "", None, "Mon, 99 Foo"])
def test_lo_ilegible_no_es_espera(valor):
    assert retry_after_segundos(valor) is None


def test_llm_usa_la_misma_funcion():
    """Que no haya dos copias: `retry-after-ms` es solo del cliente del modelo."""
    from shared import llm

    assert llm.retry_after_segundos is retry_after_segundos
    assert llm._retry_after_s({"retry-after": "abc"}) is None
    assert llm._retry_after_s({"retry-after-ms": "500", "retry-after": "9"}) == 0.5


# -- El middleware, con un slot simulado ------------------------------------

pytest.importorskip("scrapy")


class _Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _montar(settings=None, crawl_delay: float | None = None):
    from scrapy.http import Request, Response
    from scrapy.utils.test import get_crawler

    from seo_crawler import middlewares as mw

    crawler = get_crawler(settings_dict={"ROBOTSTXT_OBEY": False, **(settings or {})})
    slot = types.SimpleNamespace(delay=0.0, jitter=0.5)
    redis = {}

    class _Redis:
        def get(self, k):
            return redis.get(k)

        def set(self, k, v, **kw):
            redis[k] = v

    middlewares = []
    if crawl_delay is not None:
        cd = object.__new__(mw.RobotsCrawlDelayMiddleware)
        cd._retrasos = {"x.com": crawl_delay}
        middlewares.append(cd)
    crawler.engine = types.SimpleNamespace(downloader=types.SimpleNamespace(
        slots={"x.com": slot}, per_slot_settings={},
        get_slot_key=lambda req: "x.com",
        middleware=types.SimpleNamespace(middlewares=middlewares)))
    crawler.spider = types.SimpleNamespace(job_id="j1", _redis=_Redis())
    reloj = _Reloj()
    m = mw.RetryAfterMiddleware(crawler, reloj=reloj)

    def respuesta(status, cabeceras=None):
        req = Request("https://x.com/p", meta={"download_slot": "x.com"})
        return Response("https://x.com/p", status=status, headers=cabeceras or {},
                        request=req), req

    def autothrottle_baja():
        # Lo que hace AutoThrottle con un 200 rapido: bajar el retraso.
        slot.delay = 0.0

    return m, slot, reloj, respuesta, autothrottle_baja, crawler, redis


def test_un_429_con_retry_after_sube_el_slot_y_un_200_no_lo_baja():
    m, slot, reloj, respuesta, baja, crawler, _ = _montar()
    m._response_downloaded(*respuesta(429, {"Retry-After": "5"}))
    assert slot.delay == 5 and slot.jitter == 0.0
    assert crawler.engine.downloader.per_slot_settings["x.com"]["delay"] == 5
    reloj.t += 2
    baja()
    m._response_downloaded(*respuesta(200))  # dentro de la ventana
    assert slot.delay == 5


def test_pasada_la_ventana_vuelve_al_crawl_delay():
    m, slot, reloj, respuesta, baja, crawler, _ = _montar(crawl_delay=2.0)
    m._response_downloaded(*respuesta(503, {"Retry-After": "5"}))
    assert slot.delay == 5
    reloj.t += 6
    m._response_downloaded(*respuesta(200))
    assert slot.delay == 2.0  # el piso de la decision 81, no 0
    assert crawler.engine.downloader.per_slot_settings["x.com"]["delay"] == 2.0
    assert slot.jitter == 0.0  # con Crawl-delay, sin jitter


def test_sin_crawl_delay_vuelve_al_retraso_del_job_y_recupera_el_jitter():
    m, slot, reloj, respuesta, baja, crawler, _ = _montar({"DOWNLOAD_DELAY": 0.5})
    m._response_downloaded(*respuesta(429, {"Retry-After": "3"}))
    reloj.t += 3
    m.process_request(respuesta(200)[1])  # la red de seguridad tambien la cierra
    assert slot.delay == 0.5 and slot.jitter == 0.5
    assert "x.com" not in m._pisos


def test_funciona_sin_robotstxt_obey_y_con_tope():
    m, slot, reloj, respuesta, *_rest, redis = _montar({"RETRY_AFTER_MAX": 60})
    m._response_downloaded(*respuesta(429, {"Retry-After": "3600"}))
    assert slot.delay == 60
    import json

    resumen = json.loads(redis["job:j1:retry_after"])
    assert resumen == {"veces": 1, "truncados": 1, "max_pedido_s": 3600.0,
                       "max_aplicado_s": 60.0, "tope_s": 60.0}


def test_cada_pausa_estampa_el_latido():
    m, slot, reloj, respuesta, _baja, crawler, _ = _montar()
    latidos = []
    crawler.spider._write_heartbeat = lambda: latidos.append(1)
    m._response_downloaded(*respuesta(429, {"Retry-After": "600"}))
    m._response_downloaded(*respuesta(200))
    assert latidos == [1]


def test_sin_cabecera_o_en_otro_codigo_no_hace_nada():
    m, slot, reloj, respuesta, *_ = _montar()
    m._response_downloaded(*respuesta(429))
    m._response_downloaded(*respuesta(500, {"Retry-After": "9"}))
    m._response_downloaded(*respuesta(503, {"Retry-After": "basura"}))
    assert slot.delay == 0.0 and m.resumen["veces"] == 0


def test_se_puede_apagar():
    from scrapy.exceptions import NotConfigured
    from scrapy.utils.test import get_crawler

    from seo_crawler.middlewares import RetryAfterMiddleware

    with pytest.raises(NotConfigured):
        RetryAfterMiddleware.from_crawler(get_crawler(settings_dict={"RETRY_AFTER_ENABLED": False}))


def _campos_de(clase: str) -> dict:
    """Los `Field(...)` de una clase de `api/schemas.py`, leidos con `ast`:
    la imagen del crawler no lleva pydantic ni `api/`."""
    import ast
    from pathlib import Path

    arbol = ast.parse((Path(__file__).resolve().parent.parent / "api" / "schemas.py").read_text())
    nodo = next(n for n in ast.walk(arbol) if isinstance(n, ast.ClassDef) and n.name == clase)
    def literal(valor):
        try:
            return ast.literal_eval(valor)
        except ValueError:  # default_factory=list y similares
            return None

    campos = {}
    for hijo in nodo.body:
        if isinstance(hijo, ast.AnnAssign) and isinstance(hijo.value, ast.Call):
            campos[hijo.target.id] = {k.arg: literal(k.value) for k in hijo.value.keywords}
        elif isinstance(hijo, ast.AnnAssign) and hijo.value is not None:
            campos[hijo.target.id] = {"default": literal(hijo.value)}
    return campos


def test_el_tope_queda_por_debajo_del_vigilante_de_estancamiento():
    """Cada pausa estampa el latido (abajo), asi que el hueco maximo entre
    latidos es el tope: con el mayor que admite el schema (600 s) sigue por
    debajo de los 30 minutos sin progreso que reencolan el job. Sin el latido,
    600 s x 3 intentos eran justo 30 minutos."""
    campos = _campos_de("CrawlBehaviorConfig")
    maximo = campos["retry_after_max_s"]["le"]
    assert maximo < campos["stall_timeout_minutes"]["default"] * 60
    assert campos["respetar_retry_after"]["default"] is True
    assert campos["retry_after_max_s"]["default"] == 60


def test_el_worker_pasa_las_claves_por_s():
    from pathlib import Path

    fuente = (Path(__file__).resolve().parent.parent / "crawler" / "worker.py").read_text()
    assert "RETRY_AFTER_ENABLED=False" in fuente and "RETRY_AFTER_MAX=" in fuente
