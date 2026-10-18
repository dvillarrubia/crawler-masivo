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
    assert capturado["cuerpo"]["url"] == "https://crawler.ejemplo/#/rastreos/abc-123"
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
    de leer, y entonces no avisa de nada. Sin reglas del cliente, el filtro es
    el de siempre; con reglas, lo que el cliente marque (decision 135). Lo
    ejecuta `tests/test_reglas.py::test_el_worker_*` contra SQLite; aqui, la
    funcion que decide."""
    from shared.reglas import a_avisar

    aviso = {**CRITICA, "regla": "pierden_contenido", "severidad": "warning"}
    assert a_avisar(None, [CRITICA, aviso], []) == [CRITICA]


# ---------------------------------------------------------------------------
# El enlace del aviso tiene que llevar a la ficha del rastreo
# ---------------------------------------------------------------------------

from pathlib import Path
import re

APP_JS = Path(__file__).resolve().parent.parent / "frontend" / "app.js"


def _cuerpo_de_irARuta() -> str:
    fuente = APP_JS.read_text(encoding="utf-8")
    inicio = fuente.index("async _irARuta(hash)")
    fin = fuente.index("\n    },\n", inicio)
    return fuente[inicio:fin]


def _resolver_como_irARuta(hash_: str) -> tuple[str, list[str]]:
    """Lo que hace `_irARuta` con el hash: a qué rama va y con qué trozos.

    Reproduce el troceado del frontend (quitar `#`, cortar la query, partir por
    `/` sin vacíos) y las ramas que reconoce, sacadas del fuente de
    `_irARuta`: si alguien renombra la ruta, el test lo nota.
    """
    cuerpo = _cuerpo_de_irARuta()
    prefijos = set(re.findall(r"p\[0\] === '([a-z-]+)'", cuerpo))
    assert "rastreos" in prefijos, "no se reconocen las ramas de _irARuta"
    ruta = hash_.lstrip("#").split("?")[0]
    p = [x for x in ruta.split("/") if x]
    if not p or p[0] not in prefijos:
        return "desconocida", p   # el frontend la manda a la lista de rastreos
    if p[0] == "rastreos" and len(p) == 1:
        return "lista", p
    return p[0], p


def test_el_enlace_del_aviso_abre_la_ficha_del_rastreo(monkeypatch):
    """`#/jobs/<id>` no lo conoce el frontend y acababa en la lista: el aviso
    llevaba a todas partes menos al rastreo del que avisaba."""
    monkeypatch.setattr(avisos, "BASE_PUBLICA", "https://crawler.ejemplo")
    enlace = avisos.enlace_al_rastreo("abc-123")
    assert enlace.startswith("https://crawler.ejemplo/#/")
    rama, p = _resolver_como_irARuta(enlace.split("/", 3)[3])
    assert rama == "rastreos", f"{enlace} no abre una ficha de rastreo ({rama})"
    assert p[1] == "abc-123"
    assert len(p) == 2, "sin pestaña: abre el resumen, donde va la alerta"


def test_la_ruta_vieja_no_la_resuelve_el_frontend():
    """Control del comprobador: si la ruta vieja tambien «resolviera», el test
    de arriba no estaria comprobando nada."""
    assert _resolver_como_irARuta("#/jobs/abc-123")[0] == "desconocida"


def test_sin_url_publica_no_hay_enlace(monkeypatch):
    monkeypatch.setattr(avisos, "BASE_PUBLICA", "")
    assert avisos.enlace_al_rastreo("abc") is None
