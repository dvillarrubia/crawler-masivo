"""Que limita la velocidad de un rastreo (#275, decision 227)."""

from __future__ import annotations

import time
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from shared.ritmo import resumen

BASE = dict(autothrottle=False, objetivo_autothrottle=None)


def test_lopesan_lo_marca_la_pausa():
    # Medido el 28-oct: 0,74 URL/s con 4 a la vez, 0,75 s de pausa y 1,4 s.
    r = resumen(respuestas=5621, segundos=5621 / 0.74, latencia_s=1.4, pausa_s=0.75,
                concurrencia_dominio=4, **BASE)
    assert r["techo_pausa"] == pytest.approx(1.333, abs=0.01)
    assert r["techo_servidor"] == pytest.approx(2.857, abs=0.01)
    assert (r["techo"], r["limita"]) == (r["techo_pausa"], "pausa")
    assert r["alcanzado"] is False  # 0,74 de 1,33: entre la mitad y el 80 %


def test_penguin_lo_marca_el_servidor():
    # 3 a la vez, 3,3 s de respuesta: techo 0,9; fue a 0,68 (con 0,3 s de pausa).
    r = resumen(respuestas=87570, segundos=36 * 3600, latencia_s=3.3, pausa_s=0.3,
                concurrencia_dominio=3, **BASE)
    assert r["limita"] == "servidor" and r["techo"] == pytest.approx(0.909, abs=0.01)


def test_autothrottle_con_objetivo_menor_manda_el_objetivo():
    r = resumen(respuestas=1000, segundos=1000, latencia_s=1.0, pausa_s=0,
                concurrencia_dominio=8, autothrottle=True, objetivo_autothrottle=2.0)
    assert r["concurrencia"] == 2.0 and r["techo"] == pytest.approx(2.0)
    assert r["techo_pausa"] is None


def test_muy_por_debajo_del_techo_lo_frena_el_proceso():
    r = resumen(respuestas=600, segundos=600, latencia_s=0.2, pausa_s=0,
                concurrencia_dominio=8, **BASE)
    assert r["urls_s"] == 1.0 and r["techo"] == 40.0 and r["limita"] == "proceso"


def test_alcanzado():
    r = resumen(respuestas=1200, segundos=1000, latencia_s=1.0, pausa_s=0.75,
                concurrencia_dominio=4, **BASE)
    assert r["limita"] == "pausa" and r["alcanzado"] is True


def test_sin_datos_suficientes_no_se_dice_nada():
    assert resumen(respuestas=10, segundos=600, latencia_s=1, pausa_s=0,
                   concurrencia_dominio=4, **BASE) is None
    assert resumen(respuestas=500, segundos=30, latencia_s=1, pausa_s=0,
                   concurrencia_dominio=4, **BASE) is None
    r = resumen(respuestas=500, segundos=600, latencia_s=None, pausa_s=0,
                concurrencia_dominio=4, **BASE)
    assert r["techo"] is None and r["limita"] is None


@pytest.fixture
def bd(monkeypatch, tmp_path):
    pytest.importorskip("scrapy")
    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    import shared.database
    from shared.models import Base

    @compiles(BigInteger, "sqlite")
    def _bigint(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion, raising=False)
    return Sesion


def test_el_spider_guarda_el_ritmo_de_su_tramo(bd):
    """Cuenta lo pedido en ESTE tramo, no lo arrastrado ni lo de antes."""
    from scrapy.settings import Settings

    from seo_crawler.spiders.seo_spider import SeoSpider
    from shared.models import Job, Url

    jid = uuid.uuid4()
    ahora = datetime.now(timezone.utc)
    s = bd()
    s.add(Job(id=jid, name="r", status="running", seeds=[], config={}))
    for i in range(100):
        s.add(Url(job_id=jid, url=f"https://x.com/{i}", url_hash=f"h{i}", response_time_ms=1000,
                  last_crawled_at=ahora))
    s.add(Url(job_id=jid, url="https://x.com/c", url_hash="c", response_time_ms=50,
              last_crawled_at=ahora, fetch_mode="carried"))
    s.add(Url(job_id=jid, url="https://x.com/v", url_hash="v", response_time_ms=50,
              last_crawled_at=ahora - timedelta(days=1)))
    s.commit()
    s.close()
    sp = SeoSpider(job_id=str(jid))
    sp.crawler = types.SimpleNamespace(settings=Settings({
        "DOWNLOAD_DELAY": 0.5, "CONCURRENT_REQUESTS_PER_DOMAIN": 4, "AUTOTHROTTLE_ENABLED": False}))
    sp._ritmo_desde = (ahora - timedelta(seconds=1), time.monotonic() - 80)
    sp._persist_ritmo()
    s = bd()
    r = s.get(Job, jid).ritmo
    assert r["respuestas"] == 100 and r["latencia_s"] == 1.0
    assert r["urls_s"] == pytest.approx(1.25, abs=0.02) and r["limita"] == "pausa"
    assert r["techo_pausa"] == 2.0 and r["techo_servidor"] == 4.0
