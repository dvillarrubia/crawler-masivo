"""Un rastreo con cero URLs no es un sitio limpio.

Con `robots_mode=respect` y un `Disallow: /`, Scrapy descarta la semilla con
IgnoreRequest, `handle_error` no registraba nada y Scrapy terminaba con codigo
0: el job quedaba `completed` con 0 URLs, que se lee igual que un sitio vacio y
en orden. Con la comparacion entre censos (#32) eso declararia desaparecido el
sitio entero.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")

ARNES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")


def _rastrear(**extra):
    entrada = {"seeds": ["http://localhost:{port}/"], "config": {"max_depth": 1}, **extra}
    proc = subprocess.run(
        [sys.executable, ARNES, json.dumps(entrada)],
        capture_output=True, text=True, timeout=120,
    )
    items = []
    if "@@ITEMS@@" in proc.stdout:
        items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    return items, proc.stdout + proc.stderr


def test_con_disallow_total_solo_queda_la_semilla_bloqueada():
    """La semilla bloqueada SI se guarda —es el hallazgo— pero sin respuesta:
    `status_code` NULL. El worker mira eso y no las filas a secas, asi que el
    job sigue sin pasar por un rastreo correcto (#37)."""
    items, salida = _rastrear(robots_prohibe_todo=True, config={"max_depth": 1,
                                                                "robots_mode": "respect"})
    paginas = [i for i in items if i["_tipo"] == "PageItem"]
    assert [p["indexability_status"] for p in paginas] == ["Blocked by robots.txt"]
    assert paginas[0]["status_code"] is None
    assert paginas[0]["blocked_by_robots"] is True
    # Y el spider lo dice, que es lo que permite al worker distinguir
    # "robots lo bloquea todo" de "no se sabe por que".
    assert "robots.txt prohibe la semilla" in salida


def test_sin_disallow_el_mismo_sitio_si_se_rastrea():
    """Control: que el test anterior no pase por otra razon."""
    items, _ = _rastrear(config={"max_depth": 1, "robots_mode": "respect"})
    paginas = [i for i in items if i["_tipo"] == "PageItem"]
    assert paginas, "sin Disallow deberia rastrear"


def test_el_motivo_de_cero_urls_distingue_robots():
    """`_por_que_cero_urls` traduce la marca del spider a un finish_reason.

    Sin la marca es `sin_urls` (hay que mirarlo); con ella,
    `robots_bloquea_todo`, que es un hallazgo que se cuenta en una frase.
    """
    pytest.importorskip("redis")
    from crawler import worker

    class RedisDeMentira:
        def __init__(self, bloqueado):
            self.bloqueado = bloqueado

        def get(self, clave):
            return "1" if (self.bloqueado and "robots_bloquea_semillas" in clave) else None

    original = worker.redis_lib.Redis.from_url
    try:
        worker.redis_lib.Redis.from_url = lambda *a, **k: RedisDeMentira(True)
        assert worker._por_que_cero_urls("x") == "robots_bloquea_todo"
        worker.redis_lib.Redis.from_url = lambda *a, **k: RedisDeMentira(False)
        assert worker._por_que_cero_urls("x") == "sin_urls"
    finally:
        worker.redis_lib.Redis.from_url = original


@pytest.mark.parametrize("marca, esperado", [
    ("custom_extraction.r: regex no valida", ("failed", "config_invalida")),
    (None, ("completed", "finished")),
])
def test_reglas_invalidas_en_una_reanudacion_dejan_el_job_fallido(monkeypatch, marca, esperado):
    """Ejecutando `_run_job` de verdad (con Scrapy y el analisis simulados).

    El caso que importa es la REANUDACION: el job ya tiene filas con
    respuesta, asi que el guardia de cero URLs no salta. Si las reglas dejaron
    de compilar entre medias, el spider no pide nada y el job saldria
    `completed` con la frontera sin explorar —y la comparacion daria por
    desaparecido lo que no se llego a pedir (decision 67)—. El control (sin
    marca) comprueba que el resto del flujo no se rompe."""
    pytest.importorskip("redis")
    # Postgres y no SQLite: el worker pasa el id del job como TEXTO (sale de la
    # cola), que Postgres convierte a UUID y SQLite no.
    url_bd = os.environ.get("PAGERANK_TEST_DATABASE_URL")
    if not url_bd:
        pytest.skip("sin PAGERANK_TEST_DATABASE_URL")
    import uuid

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    import shared.database
    import shared.models as modelos
    from crawler import worker
    from shared.extraccion_a_medida import CLAVE_CONFIG_INVALIDA
    from shared.models import Base, Job, Url

    engine = create_engine(url_bd)
    Base.metadata.create_all(engine, tables=[
        v.__table__ for v in vars(modelos).values()
        if isinstance(v, type) and getattr(v, "__module__", "") == modelos.__name__
        and hasattr(v, "__table__")])
    Sesion = sessionmaker(bind=engine)
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="reanudado", status="pending", seeds=["https://x.com/"],
                  config={"render_js": True}))
        s.add(Url(job_id=jid, url="https://x.com/", url_hash=uuid.uuid4().hex, status_code=200))
        s.commit()

    datos = {CLAVE_CONFIG_INVALIDA.format(job_id=str(jid)): marca} if marca else {}

    class RedisDeMentira:
        def get(self, clave):
            return datos.get(clave)

        def delete(self, *claves):
            for c in claves:
                datos.pop(c, None)

        def __getattr__(self, nombre):
            return lambda *a, **k: None

    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    monkeypatch.setattr(worker, "SessionLocal", Sesion, raising=False)
    monkeypatch.setattr(worker.redis_lib.Redis, "from_url",
                        classmethod(lambda cls, *a, **k: RedisDeMentira()))
    monkeypatch.setattr(worker, "_ejecutar_con_vigilancia",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(worker, "_reencolar_si_pedido", lambda j: False)
    monkeypatch.setattr(worker, "_trigger_analysis", lambda j: True)
    monkeypatch.setattr(worker, "_comparar_con_el_censo_anterior", lambda j: None)
    monkeypatch.setattr(worker, "_archivar_enlaces_superados", lambda j: None)

    try:
        worker._run_job(str(jid))
        with Sesion() as s:
            job = s.get(Job, jid)
            assert (job.status, job.finish_reason) == esperado
        assert not datos, "la marca se consume"
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM urls WHERE job_id = :j"), {"j": str(jid)})
            c.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": str(jid)})
        engine.dispose()
