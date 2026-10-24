"""Endpoints del analisis enriquecido (#40, decision 85), EJECUTADOS con
TestClient y SQLite.

Lo que de verdad hay que sujetar es la seguridad, porque la API no tiene
autenticacion: sin `ENRICHMENT_TOKEN` lanzar esta DESACTIVADO (403), con un
token incorrecto tambien, y el token no aparece en ninguna respuesta. Y que
estimar no gaste: la capa se monta sin cliente.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import EnrichmentRun, Job  # noqa: E402
from test_enriquecido import _motor, sembrar  # noqa: E402

TOKEN = "token-de-prueba-largo-0123456789"


class RedisFalso:
    def __init__(self):
        self.listas: dict[str, list] = {}

    def rpush(self, clave, valor):
        self.listas.setdefault(clave, []).append(valor)

    def lpop(self, clave):
        lista = self.listas.get(clave) or []
        return lista.pop(0) if lista else None

    def mget(self, claves):
        return [None] * len(claves)


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    from api.routers import enriquecido, jobs

    Sesion = _motor(tmp_path)
    app = FastAPI()
    app.include_router(jobs.router)
    app.include_router(enriquecido.router)

    def _sesion():
        s = Sesion()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    redis = RedisFalso()
    monkeypatch.setattr(enriquecido, "get_redis", lambda: redis)
    monkeypatch.setattr(jobs, "get_redis", lambda: redis)
    monkeypatch.delenv("ENRICHMENT_TOKEN", raising=False)
    jid, _ = sembrar(Sesion, n=5)
    return TestClient(app), Sesion, redis, jid


CUERPO = {"modulos": ["pagina_legal"], "alcance": {}, "presupuesto_usd": 0.05}


def _lanzar(api, jid, token=None, cuerpo=CUERPO):
    cab = {"X-Enrichment-Token": token} if token is not None else {}
    return api.post(f"/api/jobs/{jid}/enrichment", json=cuerpo, headers=cab)


def test_sin_token_configurado_el_lanzamiento_esta_desactivado(entorno):
    api, Sesion, redis, jid = entorno
    r = _lanzar(api, jid, token="lo-que-sea-aunque-sea-largo")
    assert r.status_code == 403
    assert "ENRICHMENT_TOKEN" in r.json()["detail"] and "desactivado" in r.json()["detail"]
    assert api.get("/api/enrichment/modulos").json()["lanzamiento_activado"] is False
    s = Sesion()
    assert s.query(EnrichmentRun).count() == 0
    s.close()
    assert redis.listas.get("enrichment:pending") is None


def test_un_token_corto_cuenta_como_no_configurado(entorno, monkeypatch):
    api, *_, jid = entorno
    monkeypatch.setenv("ENRICHMENT_TOKEN", "corto")
    r = _lanzar(api, jid, token="corto")
    assert r.status_code == 403 and "demasiado corto" in r.json()["detail"]


@pytest.mark.parametrize("token", [None, "", "token-de-prueba-largo-012345678X", TOKEN + "x"])
def test_con_token_incorrecto_o_ausente_tambien_403(entorno, monkeypatch, token):
    api, Sesion, redis, jid = entorno
    monkeypatch.setenv("ENRICHMENT_TOKEN", TOKEN)
    r = _lanzar(api, jid, token=token)
    assert r.status_code == 403
    assert TOKEN not in r.text
    assert redis.listas.get("enrichment:pending") is None


def test_con_token_se_crea_y_se_encola_y_el_token_no_sale_nunca(entorno, monkeypatch):
    api, Sesion, redis, jid = entorno
    monkeypatch.setenv("ENRICHMENT_TOKEN", TOKEN)
    respuestas = []
    r = _lanzar(api, jid, token=TOKEN)
    respuestas.append(r)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["estado"] == "pendiente" and run["presupuesto_usd"] == 0.05
    assert run["estimacion"]["llamadas"] == 5
    assert redis.listas["enrichment:pending"] == [run["id"]]
    # La cola de rastreos no se toca: es otra lista.
    assert "jobs:pending" not in redis.listas
    respuestas += [
        api.get(f"/api/jobs/{jid}/enrichment/{run['id']}"),
        api.get(f"/api/jobs/{jid}/enrichment"),
        api.get("/api/enrichment/modulos"),
        api.get(f"/api/jobs/{jid}"),
        api.post(f"/api/jobs/{jid}/enrichment/estimate", json={"modulos": ["pagina_legal"]}),
        _lanzar(api, jid, token=TOKEN),  # 409: ya hay una en marcha
        api.patch(f"/api/jobs/{jid}/enrichment/{run['id']}/cancel"),
    ]
    assert respuestas[-2].status_code == 409
    for resp in respuestas:
        assert TOKEN not in resp.text
        assert TOKEN not in json.dumps(dict(resp.headers))
    # Ni en la fila guardada.
    s = Sesion()
    fila = s.get(EnrichmentRun, uuid.UUID(run["id"]))
    assert TOKEN not in json.dumps([fila.estimacion, fila.alcance, fila.modulos, fila.errores])
    s.close()


def test_estimar_no_pide_token_y_no_llama_a_nadie(entorno, monkeypatch):
    import shared.llm

    construidos = []
    monkeypatch.setattr(shared.llm.ClienteOpenRouter, "__init__",
                        lambda self, *a, **k: construidos.append(1))
    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-trampa")
    api, *_, jid = entorno
    r = api.post(f"/api/jobs/{jid}/enrichment/estimate",
                 json={"modulos": ["pagina_legal"], "alcance": {"muestra": True, "max_urls": 3}})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["urls"] == 3 and e["llamadas"] == 3 and e["coste_estimado_usd"] > 0
    assert e["alcance"] == {"carpeta": None, "max_urls": 3, "muestra": True}
    assert "cliente" in e and "global" in e and e["segundos_estimados"] >= 1
    assert construidos == []


@pytest.mark.parametrize("cuerpo,codigo", [
    ({"modulos": ["no_existe"]}, 422),
    ({"modulos": []}, 422),
    # Una clave mal escrita del alcance es un 422, no pagar por todo el sitio.
    ({"modulos": ["pagina_legal"], "alcance": {"max_url": 5}}, 422),
])
def test_estimar_valida(entorno, cuerpo, codigo):
    api, *_, jid = entorno
    assert api.post(f"/api/jobs/{jid}/enrichment/estimate", json=cuerpo).status_code == codigo


def test_solo_sobre_un_job_completado(entorno, monkeypatch):
    api, Sesion, _, jid = entorno
    monkeypatch.setenv("ENRICHMENT_TOKEN", TOKEN)
    s = Sesion()
    s.get(Job, jid).status = "analyzing"
    s.commit()
    s.close()
    assert api.post(f"/api/jobs/{jid}/enrichment/estimate",
                    json={"modulos": ["pagina_legal"]}).status_code == 409
    assert _lanzar(api, jid, token=TOKEN).status_code == 409
    assert _lanzar(api, uuid.uuid4(), token=TOKEN).status_code == 404


def test_el_presupuesto_es_obligatorio(entorno, monkeypatch):
    api, *_, jid = entorno
    monkeypatch.setenv("ENRICHMENT_TOKEN", TOKEN)
    r = _lanzar(api, jid, token=TOKEN, cuerpo={"modulos": ["pagina_legal"]})
    assert r.status_code == 422


def test_tope_mensual_agotado_no_se_encola(entorno, monkeypatch):
    from shared.models import Client, Decision

    api, Sesion, redis, jid = entorno
    monkeypatch.setenv("ENRICHMENT_TOKEN", TOKEN)
    s = Sesion()
    job = s.get(Job, jid)
    s.get(Client, job.cliente_id).decisiones = {"presupuesto_mensual_usd": 0.001}
    s.add(Decision(job_id=jid, clave="y" * 64, pregunta="otra", version_pregunta=1,
                   estado="incierto", origen="modelo", modelo="m", coste_usd=0.002))
    s.commit()
    s.close()
    r = _lanzar(api, jid, token=TOKEN)
    assert r.status_code == 409 and "Tope mensual del cliente" in r.json()["detail"]
    assert redis.listas.get("enrichment:pending") is None


def test_cancelar_una_pendiente_y_leer_resultados(entorno, monkeypatch):
    api, Sesion, _, jid = entorno
    monkeypatch.setenv("ENRICHMENT_TOKEN", TOKEN)
    run = _lanzar(api, jid, token=TOKEN).json()
    r = api.patch(f"/api/jobs/{jid}/enrichment/{run['id']}/cancel")
    assert r.status_code == 200 and r.json()["estado"] == "cancelada"
    assert api.patch(f"/api/jobs/{jid}/enrichment/{run['id']}/cancel").status_code == 409
    # Otra ejecucion ya se puede lanzar.
    assert _lanzar(api, jid, token=TOKEN).status_code == 201
    v = api.get(f"/api/jobs/{jid}/enrichment/{run['id']}").json()
    assert v["resultados"] == {}
    d = api.get(f"/api/jobs/{jid}/enrichment/{run['id']}/decisions").json()
    assert d["total"] == 0
    assert api.get(f"/api/jobs/{uuid.uuid4()}/enrichment/{run['id']}").status_code == 404


def test_borrar_el_job_se_lleva_sus_ejecuciones(entorno, monkeypatch):
    api, Sesion, _, jid = entorno
    monkeypatch.setenv("ENRICHMENT_TOKEN", TOKEN)
    assert _lanzar(api, jid, token=TOKEN).status_code == 201
    assert api.delete(f"/api/jobs/{jid}").status_code == 204
    s = Sesion()
    assert s.query(EnrichmentRun).count() == 0
    s.close()


def test_resultados_separan_lo_decidido_y_dicen_el_umbral_de_hoy(entorno, monkeypatch):
    """Decision 169: el aviso de «orientativas» depende de si la pregunta tiene
    umbral hoy, y por respuesta se cuenta cuantas estan decididas."""
    from shared.models import Decision, Url

    api, Sesion, _, jid = entorno
    monkeypatch.setenv("ENRICHMENT_TOKEN", TOKEN)
    run = _lanzar(api, jid, token=TOKEN).json()
    s = Sesion()
    ids = [u for (u,) in s.query(Url.id).filter(Url.job_id == jid).limit(3)]
    filas = [("es_pagina_de_utilidad", "decidido", "true"), ("es_pagina_de_utilidad", "incierto", "true"),
             ("es_soft_404", "incierto", "true")]
    for n, (preg, estado, resp) in enumerate(filas):
        s.add(Decision(job_id=jid, url_id=ids[n], run_id=uuid.UUID(run["id"]), clave=f"{n:064d}",
                       pregunta=preg, version_pregunta=2, estado=estado, respuesta=resp,
                       origen="modelo", modelo="typesafe/jev-1.13", coste_usd=0.0))
    s.commit()
    s.close()
    res = api.get(f"/api/jobs/{jid}/enrichment/{run['id']}").json()["resultados"]
    util, soft = res["es_pagina_de_utilidad"], res["es_soft_404"]
    assert util["umbral"] == 0.65 and util["por_respuesta"] == {"true": 2}
    assert util["decididas_por_respuesta"] == {"true": 1}
    assert soft["umbral"] is None and soft["decididas_por_respuesta"] == {}
    d = api.get(f"/api/jobs/{jid}/enrichment/{run['id']}/decisions").json()
    assert {x["estado"] for x in d["items"]} == {"decidido", "incierto"}
