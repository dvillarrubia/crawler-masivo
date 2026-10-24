"""La pantalla del enriquecido lee la intencion (decision 176): resultados de un
choice con sus opciones y su unidad, la busqueda en la tabla de respuestas y el
desajuste pagina-busquedas. EJECUTADO con TestClient, SQLite y el FakeLLM.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import shared.enriquecido as enr  # noqa: E402
from shared.database import get_session  # noqa: E402
from test_enriquecido import _motor  # noqa: E402
from test_intencion import FakeIntencion, _run, _transporte, capa_activa, sembrar  # noqa: E402,F401


@pytest.fixture()
def entorno(tmp_path, monkeypatch, capa_activa, fake_llm):  # noqa: F811
    from api.routers import enriquecido

    Sesion = _motor(tmp_path)
    app = FastAPI()
    app.include_router(enriquecido.router)

    def _sesion():
        s = Sesion()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    jid, ids = sembrar(Sesion)
    rid = _run(Sesion, jid, ["intencion_pagina", "intencion_busquedas"])
    fake = FakeIntencion(fake_llm, coste=0.0001)
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=_transporte(fake)) == "completada"
    return TestClient(app), jid, rid


def test_los_resultados_de_un_choice_traen_opciones_y_unidad(entorno):
    api, jid, rid = entorno
    res = api.get(f"/api/jobs/{jid}/enrichment/{rid}").json()["resultados"]
    busq, pag, emb = res["intencion_busqueda"], res["intencion_pagina"], res["etapa_embudo"]
    assert busq["tipo"] == "choice" and busq["ambito"] == "busqueda" and busq["total"] == 6
    assert busq["opciones"][0] == "informational" and "unclear" in busq["opciones"]
    assert pag["ambito"] == "url" and pag["por_respuesta"] == {"transactional": 1, "informational": 2}
    assert emb["por_respuesta"] == {"bofu": 1, "tofu": 2} and emb["umbral"] is None


def test_la_tabla_de_respuestas_ensena_la_busqueda_y_nada_de_la_pagina(entorno):
    api, jid, rid = entorno
    d = api.get(f"/api/jobs/{jid}/enrichment/{rid}/decisions?pregunta=intencion_busqueda").json()
    assert d["total"] == 6
    assert {x["busqueda"] for x in d["items"]} >= {"comprar zapato rojo", "acme"}
    p = api.get(f"/api/jobs/{jid}/enrichment/{rid}/decisions?pregunta=intencion_pagina").json()
    assert {x["busqueda"] for x in p["items"]} == {None}
    assert all("estado_enviado" not in x for x in p["items"])


def test_el_desajuste_se_lee_por_api(entorno):
    api, jid, rid = entorno
    r = api.get(f"/api/jobs/{jid}/enrichment/{rid}/intencion?limite=2").json()
    assert r["resumen"]["paginas_comparadas"] == 3 and r["resumen"]["desalineadas"] == 1
    assert r["resumen"]["orientativo"] is True and len(r["paginas"]) == 2
    assert r["embudo"]["por_etapa"]["bofu"]["clics"] == 160
    otro = api.get(f"/api/jobs/00000000-0000-0000-0000-000000000000/enrichment/{rid}/intencion")
    assert otro.status_code == 404
