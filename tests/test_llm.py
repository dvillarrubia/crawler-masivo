"""Transporte de OpenRouter (`shared/llm.py`, #7) contra un `FakeLLM`.

Sin red: el transporte es un `httpx.MockTransport`, y `conftest.py` hace
fallar cualquier transporte real que apunte a OpenRouter o TypeSafe.
"""

from __future__ import annotations

import asyncio

import pytest

httpx = pytest.importorskip("httpx")

from fake_llm import FakeLLM  # noqa: E402
from shared.llm import (  # noqa: E402
    ClienteOpenRouter, ConfigLLM, ErrorLLM, _retry_after_s, _Ritmo,
)

PREGUNTAS = {"legal": {"type": "noul", "instructions": "Is this a legal page?"}}


def _cliente(fake, **cfg) -> ClienteOpenRouter:
    base = dict(api_key="sk-test", backoff_inicial_s=0.001, backoff_max_s=0.002)
    base.update(cfg)
    return ClienteOpenRouter(ConfigLLM(**base), transport=fake.transport)


def _correr(coro):
    return asyncio.run(coro)


async def _una(cliente, **kw):
    try:
        return await cliente.systemone({"title": "Aviso legal"}, PREGUNTAS,
                                       "typesafe/jev-1.13", **kw)
    finally:
        await cliente.cerrar()


def test_cuerpo_y_cabeceras_de_systemone():
    fake = FakeLLM()
    r = _correr(_una(_cliente(fake), session_id="job-1", user="acme"))
    assert fake.llamadas == 1
    cuerpo = fake.peticiones[0]
    assert cuerpo["model"] == "typesafe/jev-1.13"
    assert cuerpo["state"] == {"title": "Aviso legal"}
    assert cuerpo["questions"] == PREGUNTAS
    # Que el proveedor no entrene con lo enviado, por defecto.
    assert cuerpo["provider"] == {"data_collection": "deny", "allow_fallbacks": True}
    assert cuerpo["session_id"] == "job-1" and cuerpo["user"] == "acme"
    cab = fake.cabeceras[0]
    assert cab["authorization"] == "Bearer sk-test"
    assert cab["x-openrouter-title"] == "SEO Crawler"
    assert r.uso.coste_usd == pytest.approx(0.0001)
    assert r.uso.tokens_entrada == 275 and r.uso.modelo == "typesafe/jev-1.13-20260917"
    assert r.uso.proveedor == "TypeSafe"


def test_url_de_la_ruta_estable():
    """`/api/v1/systemone`, no la alfa `/api/alpha/decisions`."""
    vistas = []

    def handler(request):
        vistas.append(str(request.url))
        return FakeLLM()(request)

    c = ClienteOpenRouter(ConfigLLM(api_key="k"), transport=httpx.MockTransport(handler))
    _correr(_una(c))
    assert vistas == ["https://openrouter.ai/api/v1/systemone"]


def test_zdr_se_pide_solo_si_se_configura():
    fake = FakeLLM()
    _correr(_una(_cliente(fake, zdr=True)))
    assert fake.peticiones[0]["provider"]["zdr"] is True


def test_sin_usage_cost_el_coste_queda_none():
    fake = FakeLLM(coste=None)
    r = _correr(_una(_cliente(fake)))
    assert r.uso.coste_usd is None and r.uso.tokens_entrada == 275


def test_429_con_retry_after_ms_se_reintenta_y_frena_el_ritmo():
    fake = FakeLLM(guion=[429], retry_after_ms=50)
    esperas = []

    async def dormir(s):
        esperas.append(s)

    c = ClienteOpenRouter(ConfigLLM(api_key="k", rpm=100), transport=fake.transport,
                          dormir=dormir)
    r = _correr(_una(c))
    assert fake.llamadas == 2 and r.n_429 == 1 and r.intentos == 2
    assert esperas == [pytest.approx(0.05)]
    # AIMD: tras un 429 se va a la mitad durante 60 s.
    assert c.rpm_efectivo == 50


def test_503_cuatro_veces_agota_los_reintentos():
    fake = FakeLLM(guion=[503, 503, 503, 503])
    with pytest.raises(ErrorLLM) as e:
        _correr(_una(_cliente(fake)))
    assert fake.llamadas == 4  # 1 + 3 reintentos
    assert e.value.motivo == "servidor" and e.value.intentos == 4
    assert not e.value.corta_capa


@pytest.mark.parametrize("codigo", [400, 413, 422])
def test_peticion_rechazada_no_se_reintenta(codigo):
    fake = FakeLLM(guion=[codigo])
    with pytest.raises(ErrorLLM) as e:
        _correr(_una(_cliente(fake)))
    assert fake.llamadas == 1 and e.value.motivo == "peticion_rechazada"
    assert not e.value.corta_capa


@pytest.mark.parametrize("codigo,motivo", [(402, "sin_credito"), (401, "clave_invalida"),
                                           (403, "clave_invalida"), (404, "modelo_inexistente")])
def test_errores_que_cortan_la_capa(codigo, motivo):
    fake = FakeLLM(guion=[codigo])
    c = _cliente(fake)

    async def dos():
        try:
            with pytest.raises(ErrorLLM) as e1:
                await c.systemone("x", PREGUNTAS, "typesafe/jev-1.13")
            # La siguiente ni sale: un 402 vale para todo lo que queda.
            with pytest.raises(ErrorLLM) as e2:
                await c.systemone("y", PREGUNTAS, "typesafe/jev-1.13")
            return e1.value, e2.value
        finally:
            await c.cerrar()

    e1, e2 = _correr(dos())
    assert fake.llamadas == 1
    assert e1.motivo == motivo and e1.corta_capa
    assert e2.corta_capa and e2.intentos == 0


def test_error_de_red_se_reintenta():
    n = {"v": 0}

    def handler(request):
        n["v"] += 1
        if n["v"] == 1:
            raise httpx.ConnectError("caida", request=request)
        return FakeLLM()(request)

    c = ClienteOpenRouter(ConfigLLM(api_key="k", backoff_inicial_s=0.001),
                          transport=httpx.MockTransport(handler))
    r = _correr(_una(c))
    assert n["v"] == 2 and r.intentos == 2


def test_circuito_abierto_tras_fallos_seguidos():
    fake = FakeLLM(guion=[503] * 100)
    c = _cliente(fake, max_reintentos=0, fallos_para_abrir_circuito=3)

    async def muchas():
        motivos = []
        for _ in range(5):
            try:
                await c.systemone("x", PREGUNTAS, "typesafe/jev-1.13")
            except ErrorLLM as exc:
                motivos.append((exc.motivo, exc.corta_capa))
        await c.cerrar()
        return motivos

    motivos = _correr(muchas())
    assert fake.llamadas == 3  # la cuarta y la quinta no salen
    assert c.circuito_abierto
    assert motivos[2][1] is True and motivos[3][0] in ("circuito_abierto", "servidor")


def test_retry_after():
    assert _retry_after_s({"retry-after-ms": "250"}) == pytest.approx(0.25)
    assert _retry_after_s({"retry-after": "2"}) == 2.0
    # retry-after-ms manda, como en el SDK de TypeSafe
    assert _retry_after_s({"retry-after-ms": "100", "retry-after": "9"}) == pytest.approx(0.1)
    assert _retry_after_s({}) is None
    assert _retry_after_s({"retry-after": "basura"}) is None


def test_ritmo_por_minuto_espera_al_hueco():
    t = {"ahora": 0.0}
    dormidas = []

    async def dormir(s):
        dormidas.append(s)
        t["ahora"] += s

    ritmo = _Ritmo(2, reloj=lambda: t["ahora"], dormir=dormir)

    async def tres():
        for _ in range(3):
            await ritmo.esperar_turno()

    _correr(tres())
    assert dormidas and sum(dormidas) == pytest.approx(60.0)


def test_sin_clave_no_hay_config(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert ConfigLLM.desde_entorno() is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "  ")
    assert ConfigLLM.desde_entorno() is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_RPM", "120")
    cfg = ConfigLLM.desde_entorno()
    assert cfg.rpm == 120 and cfg.base_url == "https://openrouter.ai/api/v1"


def test_un_transporte_real_hacia_openrouter_falla_en_los_tests():
    """La red de seguridad de `conftest.py`: aunque alguien construya un
    cliente sin MockTransport, la llamada no sale."""
    c = ClienteOpenRouter(ConfigLLM(api_key="sk-test", max_reintentos=0))
    with pytest.raises(AssertionError, match="intento de llamar"):
        _correr(_una(c))
