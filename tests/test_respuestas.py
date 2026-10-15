"""La pagina o un muro delante: `shared/respuestas.py` (decision 103).

El criterio es conservador: un falso positivo esconde una pagina real del
informe. Por eso la mitad de estos casos son paginas de verdad que se PARECEN a
un muro y tienen que salir `contenido`.
"""

from __future__ import annotations

import os

import pytest

from shared.indexabilidad import estado_indexabilidad
from shared.respuestas import (
    BLOQUEADO_POR_WAF, NO_VALIDAS, VentanaDeSalud, causa_de_bloqueo,
    clasificar_respuesta, es_titulo_de_muro, resumen_de_salud,
)

HTML = {"content-type": "text/html; charset=utf-8"}
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "respuestas",
                       "cloudflare_desafio_progym.html")


def _pagina(titulo: str, cuerpo: str = "") -> bytes:
    return f"<html><head><title>{titulo}</title></head><body>{cuerpo}</body></html>".encode()


ARTICULO = "<p>" + "Texto de un articulo con contenido de verdad. " * 60 + "</p>"


@pytest.fixture(scope="module")
def desafio() -> bytes:
    with open(FIXTURE, "rb") as f:
        return f.read()


# --- Lo que es un muro --------------------------------------------------------

def test_el_desafio_real_de_progym_con_su_cabecera(desafio):
    clase, motivo = clasificar_respuesta(
        403, {**HTML, "cf-mitigated": "challenge", "server": "cloudflare"}, desafio)
    assert clase == "challenge_waf"
    assert "cf-mitigated" in motivo


@pytest.mark.parametrize("status", [200, 403, 503])
def test_el_desafio_real_sin_cabecera_lo_delata_el_cuerpo(desafio, status):
    clase, motivo = clasificar_respuesta(status, HTML, desafio)
    assert clase == "challenge_waf"
    assert "_cf_chl_opt" in motivo


@pytest.mark.parametrize("titulo", [
    "Just a moment...",
    "Attention Required! | Cloudflare",
    "Pardon Our Interruption",
    "Request unsuccessful. Incapsula incident ID: 123-456",
    "Checking your browser before accessing example.com",
])
def test_titulos_propios_de_un_waf(titulo):
    assert clasificar_respuesta(200, HTML, _pagina(titulo))[0] == "challenge_waf"
    assert clasificar_respuesta(403, HTML, _pagina(titulo))[0] == "challenge_waf"


def test_403_forbidden_servido_con_200_y_sin_contenido():
    """El wp-login de blogs.uoc.edu: 200, «403 Forbidden», 3 palabras e
    indexable en el censo guardado."""
    clase, _ = clasificar_respuesta(200, HTML, _pagina("403 Forbidden", "<h1>Forbidden</h1>"))
    assert clase == "challenge_waf"


def test_la_pagina_de_rechazo_de_f5():
    cuerpo = _pagina("Request Rejected",
                     "The requested URL was rejected. Please consult with your "
                     "administrator.<br><br>Your support ID is: 1234567890")
    assert clasificar_respuesta(200, HTML, cuerpo)[0] == "challenge_waf"


def test_el_bloqueo_de_akamai():
    cuerpo = _pagina("Access Denied", "You don't have permission to access this "
                     "server. Reference #18.abc https://errors.edgesuite.net/18.abc")
    assert clasificar_respuesta(403, HTML, cuerpo)[0] == "challenge_waf"


def test_captcha_de_datadome():
    cuerpo = _pagina("example.com", '<script src="https://ct.captcha-delivery.com/c.js"></script>')
    assert clasificar_respuesta(403, HTML, cuerpo)[0] == "captcha"


def test_titulo_de_captcha():
    assert clasificar_respuesta(200, HTML, _pagina("Are you a robot?"))[0] == "captcha"


def test_429_y_503_con_retry_after_son_rate_limited():
    assert clasificar_respuesta(429, HTML, b"")[0] == "rate_limited"
    assert clasificar_respuesta(503, {**HTML, "Retry-After": "120"}, b"x")[0] == "rate_limited"


def test_cuerpo_binario_declarado_html():
    """El caso de la decision 11: un F5 que devuelve basura binaria."""
    assert clasificar_respuesta(200, HTML, bytes(range(256)) * 8)[0] == "respuesta_binaria"
    assert clasificar_respuesta(200, HTML, b"\x1f\x8b\x08\x00" + b"a" * 500)[0] == "respuesta_binaria"


# --- Lo que NO es un muro -----------------------------------------------------

def test_un_403_de_apache_es_la_respuesta_del_sitio():
    """Los 12 403 del censo de www.uoc.edu son directorios sin indice: un
    enlace interno a algo prohibido es un hallazgo, no un muro."""
    cuerpo = _pagina("403 Forbidden", "<h1>Forbidden</h1><p>You don't have "
                     "permission to access this resource.</p>")
    assert clasificar_respuesta(403, HTML, cuerpo)[0] == "contenido"


def test_un_503_sin_retry_after_es_un_error_del_servidor():
    assert clasificar_respuesta(503, HTML, _pagina("Service Unavailable"))[0] == "contenido"


@pytest.mark.parametrize("titulo", [
    "Security checklist for WordPress",
    "Checklist de seguridad web",
    "Access denied: la serie que arrasa",
    "Captchas: qué son y cómo funcionan",
])
def test_titulos_que_solo_se_parecen(titulo):
    assert clasificar_respuesta(200, HTML, _pagina(titulo, ARTICULO))[0] == "contenido"


def test_un_articulo_que_habla_de_captchas_y_lleva_recaptcha():
    cuerpo = _pagina("Formulario de contacto",
                     ARTICULO + '<div class="g-recaptcha" data-sitekey="x"></div>'
                     '<script src="https://www.google.com/recaptcha/api.js"></script>'
                     "<p>Este formulario esta protegido por un captcha.</p>")
    assert clasificar_respuesta(200, HTML, cuerpo)[0] == "contenido"


def test_una_pagina_normal_de_cloudflare_lleva_su_script_de_deteccion():
    """Cloudflare inyecta `challenge-platform/scripts/jsd` en paginas NORMALES."""
    cuerpo = _pagina("Máquinas de gimnasio | Progym", ARTICULO +
                     "<script>a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'</script>")
    assert clasificar_respuesta(200, {**HTML, "server": "cloudflare", "cf-ray": "x"},
                                cuerpo)[0] == "contenido"


def test_un_marcador_de_waf_en_una_pagina_con_contenido_no_basta():
    cuerpo = _pagina("Guía", ARTICULO + "<p>window._cf_chl_opt es la variable "
                     "del desafio de Cloudflare.</p>")
    assert clasificar_respuesta(200, HTML, cuerpo)[0] == "contenido"


def test_un_pdf_binario_es_contenido():
    assert clasificar_respuesta(200, {"content-type": "application/pdf"},
                                b"%PDF-1.4\x00\x01\x02")[0] == "contenido"


def test_html_con_algun_byte_raro_sigue_siendo_html():
    cuerpo = _pagina("Ficha", ARTICULO + "\x00\x01")
    assert clasificar_respuesta(200, HTML, cuerpo)[0] == "contenido"


# --- Login y desconocido ------------------------------------------------------

def test_login_solo_si_es_inequivoco():
    assert clasificar_respuesta(401, HTML, b"")[0] == "login_requerido"
    assert clasificar_respuesta(
        302, {}, b"", url="https://x.com/intranet",
        destino="https://login.microsoftonline.com/abc")[0] == "login_requerido"
    assert clasificar_respuesta(
        302, {}, b"", url="https://x.com/a",
        destino="https://sso.x.com/cas/login?service=a")[0] == "login_requerido"
    # Al login del MISMO host, o a otro host que no es un login: contenido.
    assert clasificar_respuesta(
        302, {}, b"", url="https://x.com/a", destino="https://x.com/login")[0] == "contenido"
    assert clasificar_respuesta(
        301, {}, b"", url="https://x.com/a", destino="https://www.x.com/a")[0] == "contenido"


def test_un_200_html_vacio_es_desconocido():
    assert clasificar_respuesta(200, HTML, b"   ")[0] == "desconocido"
    assert "desconocido" not in NO_VALIDAS


def test_es_titulo_de_muro():
    assert es_titulo_de_muro("Just a moment...")
    assert es_titulo_de_muro("Access Denied")
    assert not es_titulo_de_muro("Security checklist")
    assert not es_titulo_de_muro(None)


# --- Indexabilidad ------------------------------------------------------------

@pytest.mark.parametrize("clase, estado", [
    ("challenge_waf", "Bloqueada (WAF)"),
    ("captcha", "Bloqueada (captcha)"),
    ("rate_limited", "Limitada (rate limit)"),
    ("respuesta_binaria", "Respuesta no valida (binaria)"),
])
def test_un_muro_no_es_indexable_aunque_responda_200(clase, estado):
    assert estado_indexabilidad(200, response_class=clase, page_url="https://x.com/") == (False, estado)
    assert estado_indexabilidad(403, response_class=clase) == (False, estado)


def test_contenido_y_login_no_cambian_la_indexabilidad():
    assert estado_indexabilidad(200, response_class="contenido", page_url="https://x.com/")[0]
    assert estado_indexabilidad(401, response_class="login_requerido") == (False, "Client Error (401)")


# --- Salud del rastreo --------------------------------------------------------

def test_la_ventana_solo_juzga_llena():
    v = VentanaDeSalud(ventana=10, umbral=0.8)
    for _ in range(9):
        v.anotar("challenge_waf")
    assert v.veredicto() is None
    v.anotar("challenge_waf")
    assert v.veredicto() == BLOQUEADO_POR_WAF


def test_la_ventana_olvida_lo_viejo():
    """Un sitio que bloquea a partir de la URL 2.000: el total no llega nunca al
    umbral, la ventana si."""
    v = VentanaDeSalud(ventana=10, umbral=0.8)
    for _ in range(2000):
        v.anotar("contenido")
    for _ in range(8):
        v.anotar("challenge_waf")
    assert v.veredicto() == BLOQUEADO_POR_WAF
    assert v.totales["contenido"] == 2000


def test_por_debajo_del_umbral_sigue():
    v = VentanaDeSalud(ventana=10, umbral=0.8)
    for c in ["contenido"] * 3 + ["challenge_waf"] * 7:
        v.anotar(c)
    assert v.veredicto() is None
    v.anotar(None)  # sin clase: no cuenta
    assert v.veredicto() is None


def test_la_causa_es_rate_limited_si_pesa_el_429():
    assert causa_de_bloqueo({"rate_limited": 9, "challenge_waf": 1}, 0.8) == "rate_limited"
    assert causa_de_bloqueo({"rate_limited": 4, "challenge_waf": 5}, 0.8) == BLOQUEADO_POR_WAF
    # login y contenido no son muros
    assert causa_de_bloqueo({"login_requerido": 10}, 0.8) is None
    assert causa_de_bloqueo({}, 0.8) is None


def test_el_rastreo_de_una_url_detras_de_un_muro():
    """La decision 11: un job que acabo con 1 URL. Sobre el total se juzga."""
    assert causa_de_bloqueo({"respuesta_binaria": 1}, 0.8) == BLOQUEADO_POR_WAF


def test_resumen_de_salud():
    r = resumen_de_salud({"contenido": 1, "challenge_waf": 49, None: 3}, 0.8, BLOQUEADO_POR_WAF)
    assert r["respuestas"] == 50 and r["no_validas"] == 49
    assert r["proporcion_no_validas"] == 0.98
    assert r["causa"] == BLOQUEADO_POR_WAF
    assert None not in r["por_clase"]
