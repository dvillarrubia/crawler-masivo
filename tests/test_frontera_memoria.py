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
    worker._limpiar_fronteras_huerfanas()
    assert os.listdir(tmp_path / "f") == []
    monkeypatch.setattr(worker, "FRONTERA_DIR", "/proc/no-se-puede")
    assert worker._directorio_de_frontera("j1") is None
