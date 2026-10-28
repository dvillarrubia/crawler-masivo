"""La frontera del rastreo sin crecer en memoria con cada URL (#276).

- `JOBDIR` por rastreo: la cola de peticiones de Scrapy va a disco. Sin el,
  `SCHEDULER_DISK_QUEUE` no hacia nada y cada peticion pendiente costaba ~4 KB.
- `DupefilterCompacto`: 8 bytes por peticion vista en vez de la huella entera.
- `RecientesAcotados`: las caches del pipeline con tope.
- Al reanudar, lo ya rastreado en `ClavesCompactas` y la frontera con la
  profundidad que habria tenido (antes, todo a profundidad 1).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crawler"))

from scrapy import Request  # noqa: E402
from scrapy.dupefilters import RFPDupeFilter  # noqa: E402

from seo_crawler.cola_disco import ClavesCompactas, DupefilterCompacto, RecientesAcotados  # noqa: E402

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")


# ---------------------------------------------------------------------------
# Dupefilter
# ---------------------------------------------------------------------------
def test_el_dupefilter_compacto_decide_lo_mismo_que_el_de_scrapy():
    urls = [f"https://x.com/p/{i % 700}?a={i % 3}" for i in range(3000)]
    urls += ["https://x.com/p/1?a=1&b=2", "https://x.com/p/1?b=2&a=1",  # canonicalize_url
             "https://x.com/p/1#ancla", "https://x.com/P/1"]
    peticiones = [Request(u) for u in urls] + [Request("https://x.com/p/1", method="POST")]
    scrapy_df, compacto = RFPDupeFilter(), DupefilterCompacto()
    assert [scrapy_df.request_seen(r) for r in peticiones] == \
           [compacto.request_seen(r) for r in peticiones]
    assert len(compacto) == len(scrapy_df._fingerprints)


def test_el_dupefilter_no_escribe_en_el_jobdir(tmp_path):
    """`RFPDupeFilter` con JOBDIR escribe cada huella en `requests.seen`: aqui
    nadie la relee (cada ejecucion empieza con un directorio nuevo)."""
    from scrapy.utils.test import get_crawler

    crawler = get_crawler(settings_dict={"JOBDIR": str(tmp_path)})
    df = DupefilterCompacto.from_crawler(crawler)
    df.request_seen(Request("https://x.com/"))
    df.close("finished")
    assert not (tmp_path / "requests.seen").exists()


# ---------------------------------------------------------------------------
# Conjuntos
# ---------------------------------------------------------------------------
def test_recientes_acotados_olvida_lo_mas_antiguo():
    r = RecientesAcotados(3)
    for i in range(5):
        r[i] = i * 10
    assert len(r) == 3 and 0 not in r and 1 not in r and r.get(4) == 40
    r[2] = 99  # reescribir lo renueva
    r.add(5)
    assert 2 in r and 3 not in r and r.get(2) == 99
    r.update([6, 7])
    assert set(r._d) == {5, 6, 7}
    r.clear()
    assert len(r) == 0 and r.get(5) is None


def test_claves_compactas_desde_un_iterable():
    c = ClavesCompactas.desde(iter([5, 3, 5, 2**64 - 1]))
    assert len(c) == 3 and 3 in c and 2**64 - 1 in c and 4 not in c
    assert c and not ClavesCompactas() and not ClavesCompactas.desde([])
    assert c.anadir(4) and not c.anadir(3)


# ---------------------------------------------------------------------------
# El rastreo real con la cola en disco
# ---------------------------------------------------------------------------
def _rastrear(tmp_path, nombre: str, settings: dict, **extra):
    entrada = {
        "seeds": ["http://localhost:{port}/"],
        "config": {"max_depth": 2, "exclude_patterns": ["*/tag/*"]},
        "settings": settings,
        **extra,
    }
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1].split("\n", 1)[0])
    cierre = json.loads(proc.stdout.split("@@CIERRE@@", 1)[1].split("\n", 1)[0])
    paginas = sorted((i["url"], i["status_code"], i["crawl_depth"])
                     for i in items if i["_tipo"] == "PageItem")
    # Los hashes llevan el puerto, que cambia en cada ejecucion: se comparan
    # por la URL de su pagina.
    url_de = {i["url_hash"]: i["url"] for i in items if i["_tipo"] == "PageItem"}
    enlaces = sorted((url_de.get(i.get("from_url_hash")), i["to_url"])
                     for i in items if i["_tipo"] == "LinkItem")
    return paginas, enlaces, cierre


@pytest.mark.parametrize("navegador", [False, True], ids=["descarga", "render"])
def test_con_jobdir_la_cola_va_a_disco_y_se_rastrea_lo_mismo(tmp_path, navegador):
    """Las peticiones del rastreo se pueden serializar (callbacks del spider,
    meta sin objetos vivos), asi que van TODAS a disco; lo rastreado es igual
    que con la cola en memoria y el dupefilter de Scrapy."""
    antes = _rastrear(tmp_path, "antes", {}, simular_navegador=navegador)
    jobdir = tmp_path / "frontera"
    ahora = _rastrear(tmp_path, "ahora", {
        "JOBDIR": str(jobdir),
        "SCHEDULER_DISK_QUEUE": "scrapy.squeues.PickleFifoDiskQueue",
        "DUPEFILTER_CLASS": "seo_crawler.cola_disco.DupefilterCompacto",
        "SCHEDULER": "seo_crawler.cola_disco.SchedulerFrontera",
        "EXTENSIONS": {"scrapy.extensions.spiderstate.SpiderState": None},
    }, simular_navegador=navegador)
    assert ahora[0] == antes[0] and ahora[1] == antes[1]
    stats = ahora[2]["stats"]
    assert stats.get("scheduler/enqueued/disk", 0) > 0
    assert stats.get("scheduler/enqueued/memory", 0) == 0, stats
    assert stats.get("scheduler/unserializable", 0) == 0, stats
    assert stats.get("dupefilter/filtered") == antes[2]["stats"].get("dupefilter/filtered")
    assert not (jobdir / "requests.seen").exists()


def test_el_worker_le_da_a_cada_rastreo_su_jobdir_y_lo_borra(tmp_path, monkeypatch):
    pytest.importorskip("redis")
    import worker

    monkeypatch.setattr(worker, "FRONTERA_DIR", str(tmp_path / "f"))
    a, b = worker._directorio_de_frontera("j1"), worker._directorio_de_frontera("j1")
    assert a != b and os.path.isdir(a) and os.path.basename(a).startswith("j1-")
    # La recuperacion periodica corre con rastreos en marcha: no toca sus
    # directorios (lo encontro la revision: se borraban cada 5 minutos).
    for nombre in ("_recover_stale_jobs", "_recuperar_enriquecidos", "_recuperar_logs"):
        monkeypatch.setattr(worker, nombre, lambda *a, **k: None)
    worker._quizas_recuperar(None, 0.0, 10**9)
    assert os.path.isdir(a) and os.path.isdir(b)
    # Al arrancar el worker si: no corre ningun rastreo suyo.
    worker._limpiar_fronteras_huerfanas()
    assert os.listdir(tmp_path / "f") == []
    monkeypatch.setattr(worker, "FRONTERA_DIR", "/proc/no-se-puede")
    assert worker._directorio_de_frontera("j1") is None


# ---------------------------------------------------------------------------
# Sin disco: a memoria, sin perder nada
# ---------------------------------------------------------------------------
def _planificador(tmp_path):
    from scrapy import Spider
    from scrapy.utils.test import get_crawler

    from seo_crawler.cola_disco import SchedulerFrontera

    crawler = get_crawler(Spider, settings_dict={
        "JOBDIR": str(tmp_path / "jobdir"),
        "SCHEDULER_DISK_QUEUE": "scrapy.squeues.PickleFifoDiskQueue",
        "DUPEFILTER_CLASS": "seo_crawler.cola_disco.DupefilterCompacto",
        # La de serie pide el motor en marcha; lo que se prueba es `_dqpush`.
        "SCHEDULER_PRIORITY_QUEUE": "scrapy.pqueues.ScrapyPriorityQueue",
    })
    crawler.spider = crawler._create_spider("s")
    planificador = SchedulerFrontera.from_crawler(crawler)
    planificador.open(crawler.spider)
    return planificador, crawler


def _sacar_todo(planificador):
    salida = []
    while (r := planificador.next_request()) is not None:
        salida.append(r.url)
    return salida


def test_con_espacio_va_a_disco(tmp_path):
    planificador, crawler = _planificador(tmp_path)
    for i in range(5):
        assert planificador.enqueue_request(Request(f"https://x.com/{i}"))
    assert crawler.stats.get_value("scheduler/enqueued/disk") == 5
    assert sorted(_sacar_todo(planificador)) == [f"https://x.com/{i}" for i in range(5)]


def test_sin_espacio_libre_deja_el_disco(tmp_path, monkeypatch):
    import seo_crawler.cola_disco as cola_disco

    monkeypatch.setattr(cola_disco, "FRONTERA_DISCO_MIN_MB", 10**9)
    planificador, crawler = _planificador(tmp_path)
    for i in range(5):
        planificador.enqueue_request(Request(f"https://x.com/{i}"))
    assert crawler.stats.get_value("scheduler/enqueued/memory") == 5
    assert "MB libres" in crawler.stats.get_value("scheduler/sin_disco")
    assert len(_sacar_todo(planificador)) == 5


def test_un_error_al_escribir_no_pierde_la_peticion(tmp_path):
    """Con el disco lleno, el `OSError` del planificador de Scrapy perdia la
    peticion y el rastreo acababa `finished` con la frontera recortada."""
    planificador, crawler = _planificador(tmp_path)
    planificador.enqueue_request(Request("https://x.com/en-disco"))
    original = planificador.dqs.push
    llamadas = []

    def push_lleno(request):
        llamadas.append(request.url)
        raise OSError(28, "No space left on device")

    planificador.dqs.push = push_lleno
    for i in range(3):
        planificador.enqueue_request(Request(f"https://x.com/{i}"))
    assert llamadas == ["https://x.com/0"]  # despues ya ni lo intenta
    planificador.dqs.push = original
    assert sorted(_sacar_todo(planificador)) == sorted(
        ["https://x.com/en-disco"] + [f"https://x.com/{i}" for i in range(3)])
    assert "OSError" in crawler.stats.get_value("scheduler/sin_disco")
    # Esa escritura pudo dejar algo a medias: cuenta como posible perdida.
    assert crawler.stats.get_value("scheduler/perdidas") == 1


def test_un_error_al_sacar_cuenta_como_perdida_y_el_cierre_no_revienta(tmp_path):
    """Con el disco lleno, cerrar una cola vaciada (guarda su estado) lanza
    `OSError` dentro de `next_request`, y una entrada escrita a medias no se
    deserializa: se pierde la peticion, pero se cuenta."""
    planificador, crawler = _planificador(tmp_path)
    for i in range(3):
        planificador.enqueue_request(Request(f"https://x.com/{i}"))
    original = planificador.dqs.pop
    planificador.dqs.pop = lambda: (_ for _ in ()).throw(OSError(28, "No space left on device"))
    assert planificador.next_request() is None
    assert crawler.stats.get_value("scheduler/perdidas") == 1
    planificador.dqs.pop = original
    planificador.enqueue_request(Request("https://x.com/nueva"))
    assert crawler.stats.get_value("scheduler/enqueued/memory") == 1  # ya no usa el disco
    planificador.dqs.close = lambda: (_ for _ in ()).throw(OSError(28, "No space left on device"))
    planificador.close("finished")


def test_el_worker_cierra_como_truncado_si_la_cola_perdio_peticiones(monkeypatch):
    pytest.importorskip("redis")
    import worker
    from shared.comparacion import MOTIVOS_TRUNCADO

    marcas = {"job:j:frontera_perdida": "3"}

    class RedisFalso:
        def get(self, clave):
            return marcas.get(clave)

        def delete(self, clave):
            marcas.pop(clave, None)

    monkeypatch.setattr(worker.redis_lib.Redis, "from_url", classmethod(lambda cls, *a, **k: RedisFalso()))
    kw = dict(razon_cero="", sin_guardar=False, stalled=False, timed_out=False)
    assert worker._motivo_de_fin("j", consumir=False, **kw) == "frontera_sin_disco"
    assert "frontera_sin_disco" in MOTIVOS_TRUNCADO
    # Lo que ya decia otra cosa no se pisa (el tiempo agotado manda).
    assert worker._motivo_de_fin("j", consumir=False, **{**kw, "timed_out": True}) == "max_runtime_reached"
    worker._motivo_de_fin("j", consumir=True, **kw)
    assert worker._motivo_de_fin("j", consumir=False, **kw) == "finished"


def test_claves_compactas_desde_sin_set_quita_repetidas():
    c = ClavesCompactas.desde(x % 1000 for x in range(10_000))
    assert len(c) == 1000 and list(c._orden) == list(range(1000))
