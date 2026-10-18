"""Esquemas que no son web y credenciales en la URL (#149, secciones 9 y 10).

`links` solo guarda aristas del grafo (decision 106): un `sms:`, un `ftp://` o
un `JavaScript:` en mayusculas no lo son. Y una URL con `usuario:clave@` no
puede llegar a `urls.url`, a los enlaces ni a los CSV, que lee cualquiera
(decision 83).
"""

from __future__ import annotations

import pytest
from parsel import Selector

from seo_crawler import extractors as ex

BASE = "https://x.com/pagina"
HOSTS = {"x.com"}


def _enlaces(cuerpo: str) -> list[dict]:
    return ex.extract_links(Selector(text=f"<html><body>{cuerpo}</body></html>"), BASE, HOSTS)


NO_WEB = [
    "sms:+34600000000",
    "whatsapp://send?text=x",
    "intent://scan/#Intent;end",
    "ftp://x.com/f.zip",
    "ws://x.com/s",
    "wss://x.com/s",
    "file:///etc/passwd",
    "skype:u?call",
    "itms-apps://itunes.apple.com/app/id1",
    "JavaScript:void(0)",
    "JAVASCRIPT:alert(1)",
    "Mailto:a@x.com",
    "TEL:+34",
    "Data:text/html,hola",
]


@pytest.mark.parametrize("href", NO_WEB)
def test_un_esquema_que_no_es_web_no_es_un_enlace(href):
    assert _enlaces(f'<a href="{href}">x</a>') == []


def test_lo_web_sigue_entrando_con_cualquier_caja():
    urls = [e["url"] for e in _enlaces(
        '<a href="HTTPS://X.COM/a">1</a><a href="//x.com/b">2</a>'
        '<a href="/c">3</a><a href="Http://otro.com/d">4</a>')]
    assert urls == ["https://x.com/a", "https://x.com/b", "https://x.com/c",
                    "http://otro.com/d"]


def test_javascript_en_mayusculas_va_solo_a_no_rastreables():
    """La disyuncion de la decision 106: una etiqueta, una tabla."""
    html = '<a href="JavaScript:void(0)">Menu</a>'
    sel = Selector(text=f"<html><body>{html}</body></html>")
    assert ex.extract_links(sel, BASE, HOSTS) == []
    filas = ex.extract_enlaces_no_rastreables(sel, BASE, HOSTS)
    assert [f["crawlability"] for f in filas] == ["javascript_href"]


@pytest.mark.parametrize("href", ["sms:+34", "whatsapp://send?text=x", "intent://scan/",
                                  "ftp://x.com/f.zip", "Mailto:a@x.com", "TEL:+34"])
def test_los_esquemas_nuevos_no_van_a_no_rastreables(href):
    """Como `mailto:` y `tel:` en la decision 106: no son paginas."""
    sel = Selector(text=f'<html><body><a href="{href}">x</a></body></html>')
    assert ex.extract_enlaces_no_rastreables(sel, BASE, HOSTS) == []


@pytest.mark.parametrize("url,esperado", [
    ("https://x.com/a", True),
    ("HTTP://x.com/a", True),
    ("ftp://x.com/a", False),
    ("ws://x.com/socket", False),
    ("intent://x.com/", False),
    ("//x.com/a", False),  # relativa al esquema: sin resolver no es interna
    ("x.com/a", False),
])
def test_is_internal_url_solo_con_esquema_web(url, esperado):
    assert ex.is_internal_url(url, HOSTS) is esperado


def test_es_url_web():
    assert ex.es_url_web("https://x.com/")
    assert ex.es_url_web(" HTTPS://x.com/ ")
    assert not ex.es_url_web("ftp://x.com/")
    assert not ex.es_url_web("")
    assert not ex.es_url_web(None)
    assert not ex.es_url_web("http://[::1")  # urlsplit lanza ValueError


# ---------------------------------------------------------------------------
# Credenciales
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url,esperado", [
    ("https://u:p@x.com/a", "https://x.com/a"),
    ("https://u@x.com", "https://x.com/"),
    ("https://u:p@x.com:443/a", "https://x.com/a"),
    ("http://u:p@x.com:8080/a", "http://x.com:8080/a"),
    ("https://us%40er:p%3A@x.com/", "https://x.com/"),
    ("https://u:p@X.COM./a?b=1#f", "https://x.com/a?b=1"),
    # El host es evil.com: quitar el `userinfo` no la vuelve interna.
    ("https://x.com@evil.com/", "https://evil.com/"),
    ("https://u:p@españa.com/", "https://xn--espaa-rta.com/"),
    ("https://u:p@[::1]:8443/a", "https://[::1]:8443/a"),
])
def test_normalize_url_quita_las_credenciales(url, esperado):
    assert ex.normalize_url(url) == esperado


@pytest.mark.parametrize("url,esperado", [
    ("https://x.com/a@b", "https://x.com/a@b"),
    # w3lib escapa la arroba de la query, como antes; el host no cambia.
    ("https://x.com/a?email=a@b.com", "https://x.com/a?email=a%40b.com"),
])
def test_una_arroba_fuera_del_host_no_se_toca(url, esperado):
    assert ex.normalize_url(url) == esperado


def test_el_hash_no_depende_de_las_credenciales():
    assert ex.compute_url_hash("https://u:p@x.com/a") == ex.compute_url_hash("https://x.com/a")


def test_un_enlace_con_credenciales_se_guarda_sin_ellas():
    enlaces = _enlaces('<a href="https://u:p@x.com/privado">x</a>'
                       '<a href="https://x.com@evil.com/">y</a>')
    assert [(e["url"], e["is_internal"]) for e in enlaces] == [
        ("https://x.com/privado", True), ("https://evil.com/", False)]


def test_una_semilla_con_credenciales_se_rechaza():
    pytest.importorskip("pydantic")
    from api.schemas import _limpiar_semillas

    with pytest.raises(ValueError, match="basic_auth_user"):
        _limpiar_semillas(["https://u:p@x.com/"])
    with pytest.raises(ValueError, match="basic_auth_user"):
        _limpiar_semillas(["https://x.com/", "https://u@x.com/"])
    assert _limpiar_semillas(["https://x.com/a@b?c=d@e"]) == ["https://x.com/a@b?c=d@e"]
