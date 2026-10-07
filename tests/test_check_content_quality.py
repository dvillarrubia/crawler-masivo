"""El comprobador de contenido no puede dar la alarma cuando el bloqueado es el.

Este script existe para avisar de que un rastreo se ha dejado contenido fuera.
Devolvia `r.text` sin mirar el codigo, asi que un 403 o un muro de WAF —5-6 kB
de HTML sin una palabra de la pagina— se comparaba como si fuera la pagina y
salia "aqui se ha perdido todo". Y si el sitio bloqueaba TODAS las muestras, el
grupo desaparecia de la tabla: una tabla corta se lee como "no hay problemas".
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)


def _cargar():
    """Igual que en `test_check_js_templates`: sin dejar rastro en sys.path."""
    ruta = os.path.join(RAIZ, "scripts", "check_content_quality.py")
    camino_previo = list(sys.path)
    modulos_previos = set(sys.modules)
    spec = importlib.util.spec_from_file_location("ccq", ruta)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ccq"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = camino_previo
        for nombre in set(sys.modules) - modulos_previos - {"ccq"}:
            fichero = getattr(sys.modules[nombre], "__file__", None) or ""
            if fichero and not fichero.startswith(RAIZ):
                del sys.modules[nombre]
    return mod


ccq = _cargar()


class _Respuesta:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


@pytest.fixture
def finge(monkeypatch):
    """Sustituye la descarga por una respuesta dada."""
    from curl_cffi import requests as cr

    def poner(status_code: int, text: str):
        monkeypatch.setattr(cr, "get", lambda *a, **k: _Respuesta(status_code, text))

    return poner


PAGINA = "<html><head><title>Zapatillas de trail</title></head><body>texto</body></html>"


def test_una_pagina_de_verdad_se_devuelve(finge):
    finge(200, PAGINA)
    assert ccq.descargar_crudo("https://x.test/a") == PAGINA


def test_un_403_no_es_la_pagina(finge):
    finge(403, "<html><head><title>403 Forbidden</title></head><body></body></html>")
    with pytest.raises(ccq.NoEsLaPagina) as exc:
        ccq.descargar_crudo("https://x.test/a")
    assert "403" in str(exc.value)


def test_el_muro_de_cloudflare_responde_200_y_tampoco_es_la_pagina(finge):
    """Es el caso peligroso: 200, HTML valido y cero palabras de la pagina."""
    finge(200, "<html><head><title>Just a moment...</title></head><body></body></html>")
    with pytest.raises(ccq.NoEsLaPagina) as exc:
        ccq.descargar_crudo("https://x.test/a")
    assert "WAF" in str(exc.value)


@pytest.mark.parametrize("titulo", [
    "Access Denied",
    "Pardon Our Interruption",
    "Attention Required! | Cloudflare",
    "Request unsuccessful. Incapsula incident ID: 123",
    "Checking your browser before accessing",
])
def test_los_muros_conocidos_de_los_WAF(finge, titulo):
    finge(200, f"<html><head><title>{titulo}</title></head><body></body></html>")
    with pytest.raises(ccq.NoEsLaPagina):
        ccq.descargar_crudo("https://x.test/a")


def test_un_titulo_que_solo_se_parece_no_dispara(finge):
    """"Security check" bloquea; "Checklist de seguridad web" es un articulo."""
    finge(200, "<html><head><title>Checklist de seguridad web</title></head>"
               "<body>texto</body></html>")
    assert "Checklist" in ccq.descargar_crudo("https://x.test/a")


def test_sin_titulo_se_da_por_buena(finge):
    """Muchas paginas no tienen title (y eso ya lo reporta el analizador); lo
    que no se puede es tirarlas aqui por eso."""
    finge(200, "<html><body>texto de la pagina</body></html>")
    assert "texto" in ccq.descargar_crudo("https://x.test/a")
