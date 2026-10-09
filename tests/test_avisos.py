"""El aviso que sale fuera cuando un rastreo encuentra algo grave.

Una alerta que espera en la interfaz solo sirve si alguien entra. El fallo de
Lopesan —2.367 páginas canonicalizadas a un servidor de pruebas— lo pillamos
porque re-rastreamos ese día por otro motivo.
"""

from __future__ import annotations

import json

import pytest

import shared.avisos as avisos

CRITICA = {
    "regla": "canonical_a_otro_host",
    "severidad": "critical",
    "paginas": 2319,
    "de": 4193,
    "pct": 55.3,
    "que_decide_google": (
        "Google consolida estas paginas en una URL de OTRO host: dejan de "
        "aparecer en resultados."),
}


class _Respuesta:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def capturado(monkeypatch):
    """Intercepta la peticion para leer lo que se manda de verdad."""
    enviado = {}

    def falso_urlopen(peticion, timeout=None):
        enviado["url"] = peticion.full_url
        enviado["cuerpo"] = json.loads(peticion.data.decode("utf-8"))
        enviado["timeout"] = timeout
        return _Respuesta()

    monkeypatch.setattr(avisos.urllib.request, "urlopen", falso_urlopen)
    monkeypatch.setattr(avisos, "WEBHOOK_URL", "https://ejemplo/hook")
    monkeypatch.setattr(avisos, "BASE_PUBLICA", "https://crawler.ejemplo")
    return enviado


def test_el_aviso_dice_QUE_pasa_no_cuantas_alertas_hay(capturado):
    """Un aviso que obliga a abrir otra cosa para entenderlo avisa a medias,
    que es justo el problema que esto viene a resolver."""
    assert avisos.avisar_de_alertas("abc-123", "lopesan.com (semanal)", [CRITICA])

    texto = capturado["cuerpo"]["text"]
    assert "lopesan.com (semanal)" in texto
    assert "2319" in texto and "4193" in texto and "55.3" in texto
    assert "OTRO host" in texto, "que decide Google viaja en el aviso"
    assert capturado["cuerpo"]["url"] == "https://crawler.ejemplo/#/jobs/abc-123"
    assert capturado["cuerpo"]["alertas"][0]["regla"] == "canonical_a_otro_host"


def test_sin_webhook_configurado_no_se_manda_nada(monkeypatch):
    """Lo normal en local y en una instalacion recien montada."""
    monkeypatch.setattr(avisos, "WEBHOOK_URL", "")
    llamadas = []
    monkeypatch.setattr(avisos.urllib.request, "urlopen",
                        lambda *a, **k: llamadas.append(1))
    assert avisos.avisar_de_alertas("abc", "x", [CRITICA]) is False
    assert llamadas == []


def test_sin_alertas_no_se_manda_nada(capturado):
    assert avisos.avisar_de_alertas("abc", "x", []) is False
    assert capturado == {}


def test_un_webhook_caido_no_puede_tumbar_nada(monkeypatch, caplog):
    """Esto corre al cerrar un rastreo. Que un webhook caido dejara un censo
    en `failed` seria cambiar un problema por otro peor."""
    import logging

    monkeypatch.setattr(avisos, "WEBHOOK_URL", "https://ejemplo/hook")

    def explota(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(avisos.urllib.request, "urlopen", explota)
    with caplog.at_level(logging.ERROR):
        assert avisos.avisar_de_alertas("abc", "x", [CRITICA]) is False
    assert any("No se pudo enviar el aviso" in r.message for r in caplog.records)


def test_hay_un_tope_de_espera(capturado):
    """Un aviso que tarda en irse no puede retrasar el cierre de un rastreo."""
    avisos.avisar_de_alertas("abc", "x", [CRITICA])
    assert capturado["timeout"] == avisos.TIMEOUT
    assert 0 < avisos.TIMEOUT <= 30


def test_el_worker_solo_avisa_de_las_CRITICAS():
    """Un aviso que llega cada semana con cosas que no hay que mirar se deja
    de leer, y entonces no avisa de nada. El worker filtra antes de llamar.
    """
    import inspect

    import worker

    fuente = inspect.getsource(worker._comparar_con_el_censo_anterior)
    pos_filtro = fuente.index('severidad") == "critical"')
    pos_aviso = fuente.index("avisar_de_alertas(")
    assert pos_filtro < pos_aviso
    assert "avisar_de_alertas(job_id, job.name, criticas)" in fuente
