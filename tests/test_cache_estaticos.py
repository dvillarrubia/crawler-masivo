"""Que el navegador no mezcle un `app.js` viejo con un `index.html` nuevo.

Sin `Cache-Control`, el navegador decide por su cuenta cuánto se fía de su
copia. El síntoma, visto hoy tras un despliegue:

    Uncaught ReferenceError: verClientes is not defined
    Uncaught ReferenceError: enriqAbierto is not defined

…para funciones que **sí estaban** en el `app.js` que servía el servidor. No
hay forma de distinguir eso de un fallo de código sin ir a mirar lo que se
sirve de verdad, y la primera reacción siempre es sospechar del despliegue.

`no-cache` no es «no guardes»: es «guárdalo, pero pregunta antes de usarlo».
Como ya se manda `etag`, la pregunta se contesta con un 304.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

cliente = TestClient(app)


def test_el_javascript_se_revalida():
    r = cliente.get("/static/app.js")
    assert r.status_code == 200
    assert "no-cache" in r.headers.get("cache-control", ""), (
        "sin esto el navegador puede servir un app.js de hace dias junto a un "
        "index.html recien desplegado")


def test_el_html_se_revalida():
    """Es quien referencia al resto: si se sirve uno viejo, da igual lo demas."""
    r = cliente.get("/alguna/ruta/de/la/spa")
    assert r.status_code == 200
    assert "no-cache" in r.headers.get("cache-control", "")


def test_sigue_mandando_etag_para_que_el_304_sea_barato():
    """`no-cache` obliga a preguntar; el `etag` hace que la respuesta sean
    unos cientos de bytes en vez de los 100 kB del fichero."""
    r = cliente.get("/static/app.js")
    assert r.headers.get("etag")

    r2 = cliente.get("/static/app.js",
                     headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304, "una recarga no se baja el fichero entero"
