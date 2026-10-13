"""Contrato del formato de `/systemone` contra los SDK OFICIALES (#7).

El cliente de `shared/llm.py` es propio (ver su docstring: por que no los
SDK), asi que el formato lo fija esto: el cuerpo que construye la capa valida
contra el `SystemOneRequest` de TypeSafe (generado desde su OpenAPI) y contra
el `DecisionsRequest` de OpenRouter, y la respuesta del `FakeLLM` —con la que
se prueba todo lo demas— valida contra las dos respuestas. Si el `FakeLLM`
mintiera sobre la API, el resto de la suite probaria contra un mundo que no
existe.

Los SDK se instalan SOLO para esto (`tests/requirements-contrato.txt`, su
propio trabajo de CI): el de OpenRouter fija `pydantic<2.13`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")
ts = pytest.importorskip("typesafe_sdk._schemas.models")
orc = pytest.importorskip("openrouter.components")

from fake_llm import FakeLLM  # noqa: E402
from shared.decisiones import CapaDecisiones, Estado  # noqa: E402
from shared.llm import ClienteOpenRouter, ConfigLLM  # noqa: E402
from shared.preguntas import CATALOGO, Pregunta  # noqa: E402

PREGUNTAS = [
    CATALOGO["es_pagina_legal"],
    Pregunta(nombre="tipo", tipo="choice", version=1, instrucciones="What type of page?",
             opciones={"producto": "A product page", "articulo": None}),
    Pregunta(nombre="calidad", tipo="score", version=1, instrucciones="How complete?",
             opciones=["vacia", "escasa", "completa"]),
    Pregunta(nombre="noul_sin_criterios", tipo="noul", version=1,
             instrucciones="Is there a price?"),
]


def _cuerpo_y_respuesta():
    fake = FakeLLM()
    capa = CapaDecisiones(
        lambda: ClienteOpenRouter(ConfigLLM(api_key="k"), transport=fake.transport),
        job_id="job-1", usuario="acme")
    capa.preguntar_sync([Estado({"title": "Aviso legal", "h1": "Aviso legal"})], PREGUNTAS)
    assert fake.llamadas == 1
    cuerpo = fake.peticiones[0]
    respuesta = fake(_Peticion(cuerpo)).json()
    return cuerpo, respuesta


class _Peticion:
    """Lo minimo para volver a pedirle al FakeLLM la respuesta a un cuerpo."""

    def __init__(self, cuerpo):
        import json

        self.content = json.dumps(cuerpo).encode()
        self.headers = {}
        self.url = "https://openrouter.ai/api/v1/systemone"


def test_el_cuerpo_valida_contra_typesafe():
    cuerpo, _ = _cuerpo_y_respuesta()
    # TypeSafe no conoce `provider`, `session_id` ni `user` (son de
    # OpenRouter) y su modelo no los prohibe: se valida lo que si define.
    ts.SystemOneRequest.model_validate(
        {k: cuerpo[k] for k in ("state", "model", "questions")})


def test_el_cuerpo_valida_contra_openrouter():
    cuerpo, _ = _cuerpo_y_respuesta()
    req = orc.DecisionsRequest.model_validate(cuerpo)
    assert req.session_id == "job-1" and req.user == "acme"
    assert req.provider.data_collection == "deny"


def test_la_respuesta_del_fake_valida_contra_los_dos():
    _, respuesta = _cuerpo_y_respuesta()
    r = orc.DecisionsResponse.model_validate(respuesta)
    assert r.usage.cost is not None and r.provider == "TypeSafe"
    # TypeSafe exige `confidence` y `probabilities` en choice y score (en
    # OpenRouter son opcionales): el fake las trae para valer en los dos.
    ts.SystemOneResponse.model_validate(
        {k: respuesta[k] for k in ("model", "answers", "usage")})


def test_el_contrato_muerde():
    """Que la validacion de arriba no sea de goma: lo que la capa se niega a
    mandar (sin instrucciones, un noul con un solo criterio) lo rechaza el
    esquema de OpenRouter, aunque el de TypeSafe lo dejaria pasar."""
    import pydantic

    sin_instrucciones = {"model": "typesafe/jev-1.13", "state": "x",
                         "questions": {"q": {"type": "noul"}}}
    ts.SystemOneRequest.model_validate(sin_instrucciones)
    with pytest.raises(pydantic.ValidationError):
        orc.DecisionsRequest.model_validate(sin_instrucciones)
    medio_noul = {"model": "typesafe/jev-1.13", "state": "x", "questions": {
        "q": {"type": "noul", "instructions": "?", "criteria": {"true": "si"}}}}
    with pytest.raises(pydantic.ValidationError):
        orc.DecisionsRequest.model_validate(medio_noul)
