"""El worker no da por bueno un rastreo detras de un muro (decision 103).

Se ejecuta `_run_job` de verdad (decision 74), con el Scrapy sustituido por una
funcion que deja en la base de datos lo que habria guardado el spider. Redis es
un diccionario.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "crawler"))
try:
    import redis  # noqa: F401
except ImportError:  # solo existe en la imagen del crawler
    sys.modules["redis"] = types.ModuleType("redis")


class _Redis:
    def __init__(self, datos):
        self.datos = datos

    def get(self, k):
        return self.datos.get(k)

    def set(self, k, v, *a, **kw):
        self.datos[k] = v

    def delete(self, *ks):
        for k in ks:
            self.datos.pop(k, None)

    def incr(self, k):
        self.datos[k] = int(self.datos.get(k) or 0) + 1
        return self.datos[k]


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    import worker
    from shared.models import Base, Job

    @compiles(BigInteger, "sqlite")
    def _bigint(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr("shared.database.SessionLocal", Sesion, raising=False)

    datos: dict = {}
    monkeypatch.setattr(worker.redis_lib, "Redis",
                        types.SimpleNamespace(from_url=lambda *a, **k: _Redis(datos)),
                        raising=False)
    llamadas = {"analisis": 0, "comparar": 0, "reencolar": 0}
    monkeypatch.setattr(worker, "_trigger_analysis",
                        lambda jid: llamadas.__setitem__("analisis", llamadas["analisis"] + 1) or True)
    monkeypatch.setattr(worker, "_comprobar_render_js", lambda jid: None)
    monkeypatch.setattr(worker, "_comparar_con_el_censo_anterior",
                        lambda jid: llamadas.__setitem__("comparar", llamadas["comparar"] + 1))
    monkeypatch.setattr(worker, "_archivar_enlaces_superados", lambda jid: None)

    def _reencolar(jid, motivo):
        llamadas["reencolar"] += 1
        return True

    monkeypatch.setattr(worker, "_reencolar_tras_estancamiento", _reencolar)

    s = Sesion()
    j = Job(id=uuid.uuid4(), name="t", status="pending", seeds=["https://x.com/"], config={})
    s.add(j)
    s.commit()
    jid = j.id
    s.close()
    return worker, Sesion, datos, llamadas, jid


def _guardar(Sesion, jid, clases):
    from shared.models import Url

    s = Sesion()
    for i, clase in enumerate(clases):
        s.add(Url(job_id=jid, url=f"https://x.com/p{i}", url_hash=f"h{i}",
                  is_internal=True, is_html=True,
                  status_code=403 if clase == "challenge_waf" else 200,
                  response_class=clase))
    s.commit()
    s.close()


def _scrapy_que_guarda(worker, Sesion, jid, clases, datos, marca=None, estanca=False):
    def falso(cmd, **kw):
        _guardar(Sesion, jid, clases)
        datos[f"job:{jid}:crawled_count"] = str(len(clases))
        if marca:
            datos[f"job:{jid}:bloqueo"] = json.dumps(marca)
        if estanca:
            raise worker.EstancadoError("40 min sin avanzar")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return falso


def _job(Sesion, jid):
    from shared.models import Job

    s = Sesion()
    try:
        j = s.query(Job).filter(Job.id == jid).one()
        return j.status, j.finish_reason, j.health
    finally:
        s.close()


def test_progym_la_home_y_todo_lo_demas_un_desafio(entorno, monkeypatch):
    """Lo medido el 2026-10-16: la home sale de la cache de Cloudflare con 200 y
    el resto, 403 con el desafio. Rastreo corto, ventana sin llenar: lo decide
    el total al cerrar."""
    worker, Sesion, datos, llamadas, jid = entorno
    monkeypatch.setattr(worker, "_ejecutar_con_vigilancia", _scrapy_que_guarda(
        worker, Sesion, jid, ["contenido"] + ["challenge_waf"] * 9, datos))
    worker._run_job(jid)

    status, motivo, salud = _job(Sesion, jid)
    assert (status, motivo) == ("failed", "bloqueado_por_waf")
    assert salud["por_clase"] == {"contenido": 1, "challenge_waf": 9}
    assert salud["causa"] == "bloqueado_por_waf"
    # Ni se analiza ni se compara: saldrian incidencias y alertas de un censo
    # que no se ha visto.
    assert llamadas["analisis"] == 0 and llamadas["comparar"] == 0


def test_la_marca_del_spider_manda_y_viaja_en_la_salud(entorno, monkeypatch):
    worker, Sesion, datos, llamadas, jid = entorno
    marca = {"causa": "rate_limited", "ventana": 50, "recuentos": {"rate_limited": 45}}
    monkeypatch.setattr(worker, "_ejecutar_con_vigilancia", _scrapy_que_guarda(
        worker, Sesion, jid, ["contenido"] * 200 + ["rate_limited"] * 45, datos, marca=marca))
    worker._run_job(jid)

    status, motivo, salud = _job(Sesion, jid)
    assert (status, motivo) == ("failed", "rate_limited")
    assert salud["ventana"]["recuentos"] == {"rate_limited": 45}
    assert f"job:{jid}:bloqueo" not in datos  # se limpia al cerrar


def test_un_estancamiento_de_un_job_bloqueado_no_se_reencola(entorno, monkeypatch):
    """Decision 15: un estancamiento reencola el job. Detras de un muro seria
    pedir otras tres veces lo mismo."""
    worker, Sesion, datos, llamadas, jid = entorno
    monkeypatch.setattr(worker, "_ejecutar_con_vigilancia", _scrapy_que_guarda(
        worker, Sesion, jid, ["challenge_waf"] * 20, datos, estanca=True))
    worker._run_job(jid)

    assert llamadas["reencolar"] == 0
    assert _job(Sesion, jid)[:2] == ("failed", "bloqueado_por_waf")


def test_un_estancamiento_normal_si_se_reencola(entorno, monkeypatch):
    """Control: sin muro, la decision 15 sigue igual."""
    worker, Sesion, datos, llamadas, jid = entorno
    monkeypatch.setattr(worker, "_ejecutar_con_vigilancia", _scrapy_que_guarda(
        worker, Sesion, jid, ["contenido"] * 20, datos, estanca=True))
    worker._run_job(jid)
    assert llamadas["reencolar"] == 1


def test_un_rastreo_sano_sigue_completed_con_su_salud(entorno, monkeypatch):
    worker, Sesion, datos, llamadas, jid = entorno
    monkeypatch.setattr(worker, "_ejecutar_con_vigilancia", _scrapy_que_guarda(
        worker, Sesion, jid, ["contenido"] * 95 + ["challenge_waf"] * 5, datos))
    worker._run_job(jid)

    status, motivo, salud = _job(Sesion, jid)
    assert (status, motivo) == ("completed", "finished")
    assert salud["no_validas"] == 5 and salud["causa"] is None
    assert llamadas["analisis"] == 1 and llamadas["comparar"] == 1


def test_la_marca_de_una_ejecucion_anterior_no_tumba_la_siguiente(entorno, monkeypatch):
    """La ejecucion anterior paro por muro y se reencolo antes de la
    finalizacion (reanudacion pedida, despliegue, estancamiento): su marca
    seguia en Redis y esta, que sale limpia, acababa `failed`."""
    worker, Sesion, datos, llamadas, jid = entorno
    datos[f"job:{jid}:bloqueo"] = json.dumps(
        {"causa": "bloqueado_por_waf", "ventana": 50, "recuentos": {"challenge_waf": 50}})
    monkeypatch.setattr(worker, "_ejecutar_con_vigilancia", _scrapy_que_guarda(
        worker, Sesion, jid, ["contenido"] * 100, datos))
    worker._run_job(jid)

    status, motivo, salud = _job(Sesion, jid)
    assert (status, motivo) == ("completed", "finished")
    assert "ventana" not in salud
    assert llamadas["analisis"] == 1


def test_los_muros_de_hosts_externos_no_bloquean_el_job(entorno, monkeypatch):
    """Con follow_external, los desafios de redes sociales y medios enlazados
    no dicen nada del sitio que se audita."""
    from shared.models import Url

    worker, Sesion, datos, llamadas, jid = entorno

    def falso(cmd, **kw):
        s = Sesion()
        for i in range(10):
            s.add(Url(job_id=jid, url=f"https://x.com/p{i}", url_hash=f"i{i}",
                      is_internal=True, is_html=True, status_code=200,
                      response_class="contenido"))
        for i in range(90):
            s.add(Url(job_id=jid, url=f"https://twitter.com/p{i}", url_hash=f"e{i}",
                      is_internal=False, is_html=True, status_code=403,
                      response_class="challenge_waf"))
        s.commit()
        s.close()
        datos[f"job:{jid}:crawled_count"] = "100"
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(worker, "_ejecutar_con_vigilancia", falso)
    worker._run_job(jid)
    assert _job(Sesion, jid)[:2] == ("completed", "finished")
