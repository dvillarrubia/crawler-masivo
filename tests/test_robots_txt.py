"""Reglas de `shared/robots_txt.py` (decisiones 112 y 113): funciones puras.

Que hace Google con cada respuesta de robots.txt, y que puede pedir Googlebot
segun un cuerpo guardado. Las precedencias son los ejemplos de la documentacion
de Google («How Google interprets the robots.txt specification»), evaluados con
Protego, que es el parser de Scrapy y el que usa el analisis.
"""

from __future__ import annotations

import pytest

from shared.robots_txt import (
    INACCESIBLE,
    LIMITE_BYTES,
    NO_EVALUADO,
    PROCESADO,
    SIN_RESTRICCIONES,
    EvaluadorRobots,
    es_url_de_robots,
    estado,
    fila_de_respuesta,
    limitado,
    origen,
)

pytest.importorskip("protego")

CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


@pytest.mark.parametrize("status,error,clase,esperado", [
    (200, None, "contenido", PROCESADO),
    (204, None, "contenido", PROCESADO),
    (404, None, "contenido", SIN_RESTRICCIONES),
    (410, None, "contenido", SIN_RESTRICCIONES),
    # 401 y 403 son 4xx: para Google, todo permitido.
    (401, None, "login_requerido", SIN_RESTRICCIONES),
    (403, None, "contenido", SIN_RESTRICCIONES),
    (301, None, "contenido", SIN_RESTRICCIONES),
    (500, None, "contenido", INACCESIBLE),
    (503, None, "contenido", INACCESIBLE),
    # 429 = 5xx para Google, aunque sea un rate limit.
    (429, None, "rate_limited", INACCESIBLE),
    # Un muro delante no es el robots.txt del sitio.
    (403, None, "challenge_waf", NO_EVALUADO),
    (200, None, "captcha", NO_EVALUADO),
    (None, "TimeoutError: took longer than 30.0 seconds", None, INACCESIBLE),
    (None, "DNSLookupError: no existe", None, INACCESIBLE),
    (None, "ConnectionRefusedError: x", None, INACCESIBLE),
    (None, "IgnoreRequest: max redirections", None, NO_EVALUADO),
    (None, None, None, NO_EVALUADO),
])
def test_estado_con_el_criterio_de_google(status, error, clase, esperado):
    assert estado(status, error, clase) == esperado


def test_limitado():
    assert limitado(429, "rate_limited") and limitado(200, "rate_limited")
    assert not limitado(503, "contenido")


def test_origen_y_url_de_robots():
    assert origen("HTTPS://WWW.X.com:443/a?b") == "https://www.x.com"
    assert origen("http://x.com:8080/a") == "http://x.com:8080"
    assert origen("mailto:a@x.com") is None
    assert es_url_de_robots("https://x.com/robots.txt")
    assert not es_url_de_robots("https://x.com/robots.txt?x=1")
    assert not es_url_de_robots("https://x.com/es/robots.txt")


def _ev(cuerpo: str) -> EvaluadorRobots:
    return EvaluadorRobots(PROCESADO, cuerpo)


def test_la_tabla_de_la_guia_googlebot_frente_a_chrome():
    """El token, no el UA: con el de Chrome se aplica `Mozilla` y no `Googlebot`."""
    from protego import Protego

    moz = "User-agent: Mozilla\nDisallow: /moz/\n"
    assert not Protego.parse(moz).can_fetch("https://e.com/moz/a", CHROME)
    assert _ev(moz).permitido("https://e.com/moz/a", "googlebot") is True

    solo = "User-agent: Googlebot\nDisallow: /solo-google/\n"
    assert Protego.parse(solo).can_fetch("https://e.com/solo-google/a", CHROME)
    assert _ev(solo).permitido("https://e.com/solo-google/a", "googlebot") is False

    todo = "User-agent: *\nDisallow: /\n\nUser-agent: Googlebot\nAllow: /\n"
    assert not Protego.parse(todo).can_fetch("https://e.com/x", CHROME)
    assert _ev(todo).permitido("https://e.com/x", "googlebot") is True


@pytest.mark.parametrize("allow,disallow,ruta,permitido", [
    # Ejemplos de la documentacion de Google: gana la regla mas larga, y con
    # la misma longitud, la menos restrictiva (Allow).
    ("/p", "/", "/page", True),
    ("/folder", "/folder", "/folder/page", True),
    ("/page", "/*.htm", "/page.htm", False),
    ("/$", "/", "/", True),
    ("/$", "/", "/page.htm", False),
    ("/", "/", "/x", True),
])
def test_precedencia_como_google(allow, disallow, ruta, permitido):
    ev = _ev(f"User-agent: *\nAllow: {allow}\nDisallow: {disallow}\n")
    assert ev.permitido("https://example.com" + ruta, "googlebot") is permitido


def test_un_grupo_de_otro_producto_de_google_no_aplica_a_googlebot():
    assert _ev("User-agent: Googlebot-News\nDisallow: /\n").permitido(
        "https://e.com/x", "googlebot") is True


def test_lo_que_pasa_de_500_kib_no_cuenta():
    relleno = "# " + "x" * 100 + "\n"
    cuerpo = ("User-agent: *\n" + relleno * (LIMITE_BYTES // len(relleno) + 10)
              + "Disallow: /tarde/\n")
    fila = fila_de_respuesta("https://e.com/robots.txt", status_code=200,
                             cabeceras={"content-type": "text/plain"},
                             cuerpo=cuerpo.encode())
    assert fila["recortado"] is True and fila["bytes"] == len(cuerpo)
    assert len(fila["cuerpo"].encode()) <= LIMITE_BYTES
    assert _ev(fila["cuerpo"]).permitido("https://e.com/tarde/a", "googlebot") is True


def test_sin_restricciones_e_inaccesible():
    assert EvaluadorRobots(SIN_RESTRICCIONES).permitido("https://e.com/x", "googlebot") is True
    assert EvaluadorRobots(INACCESIBLE).permitido("https://e.com/x", "googlebot") is None
    assert EvaluadorRobots(NO_EVALUADO).permitido("https://e.com/x", "googlebot") is None


def test_fila_solo_guarda_el_cuerpo_de_un_2xx_y_sin_nul():
    f503 = fila_de_respuesta("https://e.com/robots.txt", status_code=503,
                             cuerpo=b"<html>caido</html>", intentos=3)
    assert f503["cuerpo"] is None and f503["status_code"] == 503 and f503["intentos"] == 3
    f200 = fila_de_respuesta("https://e.com/robots.txt", status_code=200,
                             cabeceras={"content-type": "text/plain"},
                             cuerpo=b"User-agent: *\x00\nDisallow: /a\n")
    assert "\x00" not in f200["cuerpo"] and len(f200["sha256"]) == 64
    assert f200["host"] == "e.com" and f200["recortado"] is False
    f429 = fila_de_respuesta("https://e.com/robots.txt", status_code=429, cuerpo=b"x")
    assert f429["response_class"] == "rate_limited"
    ferr = fila_de_respuesta("https://e.com/robots.txt", error="TimeoutError: " + "x" * 400)
    assert len(ferr["error"]) == 200 and ferr["status_code"] is None


@pytest.mark.parametrize("grupo,permitido", [
    # Google extrae el token del grupo ([A-Za-z_-]) y lo compara ENTERO con el
    # del bot (robots.cc). Protego busca el del grupo dentro del nombre del bot,
    # asi que `Google` le aplicaba a Googlebot y `Googlebot/2.1` no
    # (revision de la decision 113).
    ("Google", True),
    ("Googlebot/2.1", False),
    ("googlebot ", False),
    ("Googlebot # el de busqueda", False),
    ("bot", True),
    ("Googlebot-Image", True),
])
def test_el_grupo_se_elige_por_token_entero_como_google(grupo, permitido):
    cuerpo = f"User-agent: {grupo}\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    assert _ev(cuerpo).permitido("https://e.com/x", "googlebot") is permitido


def test_varios_tokens_del_mismo_grupo_y_variantes_de_la_directiva():
    cuerpo = ("User-agent: Bingbot\nuser agent: Googlebot/2.1\nDisallow: /a/\n\n"
              "useragent: Google\nDisallow: /b/\n\nUser-agent: *\nDisallow: /c/\n")
    ev = _ev(cuerpo)
    assert ev.permitido("https://e.com/a/x", "googlebot") is False
    assert ev.permitido("https://e.com/b/x", "googlebot") is True
    # El grupo de Googlebot sustituye al de `*`
    assert ev.permitido("https://e.com/c/x", "googlebot") is True
