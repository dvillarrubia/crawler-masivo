"""Credenciales y cookies del job: solo a los hosts del sitio, y sin guardar
las del servidor (decision 114).

Con el SeoSpider real, sus middlewares y la descarga de produccion (curl_cffi
y, si hay Chromium, Playwright con la preparacion de pagina de settings.py)
contra un sitio local en dos hosts: `localhost` es el del cliente (la semilla)
y `127.0.0.1` hace de tercero. El servidor anota las cabeceras de cada
peticion, asi que se mira lo que LLEGA, no lo que el codigo dice poner.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")
pytest.importorskip("scrapy_impersonate")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FUERA = "127.0.0.1"
SITIO = "localhost"
BASIC = "Basic dXN1OmNsYXZl"  # usu:clave


def _html(cuerpo: str) -> str:
    return f"<html><head><title>t</title></head><body>{cuerpo}</body></html>"


RUTAS = {
    # Pone una cookie por cabecera y otra por JS (esta solo cuenta con render)
    "/c/inicio": [200, {"Set-Cookie": "servidor=1; Path=/"}, _html(
        '<a href="/c/lee">lee</a>'
        '<a href="/c/redir-fuera">fuera</a>'
        '<a href="/c/redir-dentro">dentro</a>'
        '<a href="http://127.0.0.1:{port}/c/externa">externa</a>'
        "<script>document.cookie='js=1; path=/'</script>")],
    "/c/lee": [200, {}, _html('<a href="/c/lee2">lee2</a>')],
    "/c/lee2": [200, {}, _html("<p>dos</p>")],
    # Un staging que manda a un SSO de otro dominio
    "/c/redir-fuera": [302, {"Location": "http://127.0.0.1:{port}/c/fuera"}, ""],
    "/c/fuera": [200, {}, _html("<p>sso</p>")],
    "/c/redir-dentro": [301, {"Location": "/c/dentro"}, ""],
    "/c/dentro": [200, {}, _html("<p>dentro</p>")],
    "/c/externa": [200, {}, _html("<p>externa</p>")],
}

HTTP_STAGING = {
    "cookies": {"configurada": "abc"},
    "custom_headers": {"X-Secreto": "s3"},
    "basic_auth_user": "usu",
    "basic_auth_password": "clave",
    "accept_language": "eu-ES",
}


def _rastrear(http: dict, render: bool = False, chromium: str | None = None,
              **config) -> tuple[list[tuple[str, str, dict]], dict]:
    entrada = {
        "seeds": ["http://localhost:{port}/c/inicio"],
        "config": {"max_depth": 3, "follow_external": True, "use_sitemap": False,
                   "render_js": render, "http": http, **config},
        "rutas": RUTAS,
        # robots.txt de los dos hosts, por el RobotsTxtMiddleware de Scrapy
        "robots_bloquea_privado": True,
        "http_real": True,
        "descarga_real": True,
        "ver_cabeceras": True,
    }
    if chromium:
        entrada["chromium"] = chromium
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=240, cwd=RAIZ)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-6000:]
    cabeceras = json.loads(proc.stdout.split("@@CABECERAS@@", 1)[1].split("\n", 1)[0])
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    paginas = {i["url"]: i for i in items if i["_tipo"] == "PageItem"}
    return [tuple(c) for c in cabeceras], paginas


def _de(cabeceras, host: str, ruta: str | None = None) -> list[dict]:
    return [h for (ho, r, h) in cabeceras if ho == host and (ruta is None or r == ruta)]


def _cookies(h: dict) -> set[str]:
    return {c.strip() for c in (h.get("cookie") or "").split(";") if c.strip()}


def _sin_nada_del_job(h: dict) -> bool:
    return ("authorization" not in h and "x-secreto" not in h
            and "configurada=abc" not in _cookies(h))


def _comprobar_solo_al_sitio(cabeceras, rutas_sitio: list[str]) -> None:
    for ruta in rutas_sitio:
        recibidas = _de(cabeceras, SITIO, ruta)
        assert recibidas, (ruta, cabeceras)
        for h in recibidas:
            assert h.get("authorization") == BASIC, (ruta, h)
            assert h.get("x-secreto") == "s3", (ruta, h)
            assert "configurada=abc" in _cookies(h), (ruta, h)
    fuera = _de(cabeceras, FUERA)
    # El tercero recibe peticiones (su robots.txt, el destino del SSO y la
    # pagina externa) pero nada del job
    assert {"/robots.txt", "/c/fuera", "/c/externa"} <= {r for (ho, r, _) in cabeceras if ho == FUERA}
    for h in fuera:
        assert _sin_nada_del_job(h), h


# ---------------------------------------------------------------------------
# Sin render: curl_cffi
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def staging():
    return _rastrear(HTTP_STAGING)


def test_las_credenciales_solo_llegan_a_los_hosts_del_sitio(staging):
    cabeceras, _ = staging
    _comprobar_solo_al_sitio(
        cabeceras, ["/c/inicio", "/c/lee", "/c/redir-fuera", "/c/redir-dentro",
                    "/c/dentro", "/robots.txt"])


def test_las_sondas_de_soft404_llevan_las_credenciales(staging):
    cabeceras, _ = staging
    sondas = [h for (ho, r, h) in cabeceras if ho == SITIO and "/zz" in r]
    assert sondas, cabeceras
    assert all(h.get("authorization") == BASIC and "configurada=abc" in _cookies(h)
               for h in sondas)


def test_accept_language_va_a_todos(staging):
    cabeceras, _ = staging
    assert all(h.get("accept-language") == "eu-ES" for (_, _, h) in cabeceras)


def test_por_defecto_no_se_guardan_las_cookies_del_servidor(staging):
    cabeceras, _ = staging
    for (_, ruta, h) in cabeceras:
        assert "servidor=1" not in _cookies(h), (ruta, h)


def test_la_redireccion_a_otro_host_queda_registrada(staging):
    _, paginas = staging
    origen = paginas["http://SEMILLA/c/redir-fuera"]
    assert origen["status_code"] == 302
    assert origen["redirect_url"] == "http://OTRO/c/fuera"


def test_sin_cookies_configuradas_no_va_ninguna():
    cabeceras, _ = _rastrear({})
    lee = _de(cabeceras, SITIO, "/c/lee")
    assert lee and all(not h.get("cookie") for h in lee), lee


def test_conservar_cookies_guarda_las_del_servidor():
    cabeceras, _ = _rastrear({"conservar_cookies": True})
    lee = _de(cabeceras, SITIO, "/c/lee")
    assert lee and all("servidor=1" in _cookies(h) for h in lee), lee
    assert all("servidor=1" not in _cookies(h) for h in _de(cabeceras, FUERA))


def test_conservar_cookies_con_configuradas_manda_las_dos():
    """Con jar y cookies configuradas, curl_cffi tiraba las del jar."""
    cabeceras, _ = _rastrear({**HTTP_STAGING, "conservar_cookies": True})
    lee = _de(cabeceras, SITIO, "/c/lee")
    assert lee and all({"servidor=1", "configurada=abc"} <= _cookies(h) for h in lee), lee
    for h in _de(cabeceras, FUERA):
        assert _sin_nada_del_job(h) and "servidor=1" not in _cookies(h), h


# ---------------------------------------------------------------------------
# Con render: Playwright
# ---------------------------------------------------------------------------
_PRUEBA_NAVEGADOR = """
import asyncio, sys
from playwright.async_api import async_playwright
async def main(ruta):
    async with async_playwright() as p:
        kw = {"executable_path": ruta} if ruta else {}
        b = await p.chromium.launch(args=["--no-sandbox"], **kw)
        await b.close()
asyncio.run(main(sys.argv[1]))
"""


def _chromium_disponible() -> str | None:
    """'' si Playwright lanza su Chromium, la ruta de otro que lance, o None.

    La version del paquete puede pedir un build que no es el instalado (aqui,
    1.64 pide el 1248 y en /opt/pw-browsers esta el 1194): se prueba con el
    que haya.
    """
    try:
        import playwright  # noqa: F401
    except ImportError:
        return None
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "/opt/pw-browsers"
    candidatos = [""] + sorted(glob.glob(os.path.join(base, "chromium-*", "chrome-linux*", "chrome")))
    for ruta in candidatos:
        try:
            proc = subprocess.run([sys.executable, "-c", _PRUEBA_NAVEGADOR, ruta],
                                  capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            continue
        if proc.returncode == 0:
            return ruta
    return None


@pytest.fixture(scope="module")
def chromium():
    ruta = _chromium_disponible()
    if ruta is None:
        pytest.skip("sin Chromium para Playwright")
    return ruta


@pytest.fixture(scope="module")
def staging_render(chromium):
    return _rastrear(HTTP_STAGING, render=True, chromium=chromium)


def test_render_las_cookies_configuradas_llegan_a_la_pagina(staging_render):
    """Antes no llegaban: Chromium ignora la cabecera Cookie de scrapy-playwright."""
    cabeceras, _ = staging_render
    for ruta in ("/c/inicio", "/c/lee", "/c/lee2"):
        recibidas = _de(cabeceras, SITIO, ruta)
        assert recibidas and all("configurada=abc" in _cookies(h) for h in recibidas), (ruta, recibidas)


def test_render_las_credenciales_solo_llegan_al_sitio(staging_render):
    """Chromium conserva las cabeceras de `route.continue_` al seguir una
    redireccion a otro host: el Basic del staging llegaba al SSO."""
    cabeceras, _ = staging_render
    _comprobar_solo_al_sitio(cabeceras, ["/c/inicio", "/c/lee", "/c/redir-fuera",
                                         "/c/redir-dentro", "/c/dentro"])


def test_render_la_redireccion_la_sigue_el_spider(staging_render):
    _, paginas = staging_render
    origen = paginas["http://SEMILLA/c/redir-fuera"]
    assert origen["status_code"] == 302
    assert origen["redirect_url"] == "http://OTRO/c/fuera"
    dentro = paginas["http://SEMILLA/c/redir-dentro"]
    assert dentro["status_code"] == 301
    assert paginas["http://SEMILLA/c/dentro"]["status_code"] == 200


def test_render_no_se_acumulan_las_cookies_del_sitio(staging_render):
    cabeceras, _ = staging_render
    for ruta in ("/c/lee", "/c/lee2"):
        for h in _de(cabeceras, SITIO, ruta):
            assert not ({"servidor=1", "js=1"} & _cookies(h)), (ruta, h)


def test_render_sin_credenciales_va_por_el_camino_de_siempre(chromium):
    """Sin cabeceras propias ni Basic no se toca la descarga del documento, y
    con `conservar_cookies` las del sitio se quedan en el contexto."""
    cabeceras, paginas = _rastrear({"conservar_cookies": True}, render=True, chromium=chromium)
    lee = _de(cabeceras, SITIO, "/c/lee2")
    assert lee and all({"servidor=1", "js=1"} <= _cookies(h) for h in lee), lee
    assert all(not h.get("cookie") for h in _de(cabeceras, FUERA))
    assert paginas["http://SEMILLA/c/lee"]["status_code"] == 200


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------
def test_conservar_cookies_esta_declarada_en_jobconfig():
    """Pydantic v2 tira en silencio una clave anidada sin declarar (decision 33)."""
    pytest.importorskip("pydantic")
    sys.path.insert(0, RAIZ)
    from api.schemas import JobCreate

    job = JobCreate(name="x", seeds=["https://x.com/"], config={"http": {"conservar_cookies": True}})
    assert job.config.http.conservar_cookies is True
    assert JobCreate(name="x", seeds=["https://x.com/"]).config.http.conservar_cookies is False


def test_el_worker_enciende_el_jar_solo_si_se_pide():
    pytest.importorskip("redis")
    from crawler import worker

    assert worker._ajustes_de_cookies({}) == []
    assert worker._ajustes_de_cookies({"http": {"conservar_cookies": False}}) == []
    assert worker._ajustes_de_cookies({"http": {"conservar_cookies": True}}) == [
        "-s", "COOKIES_ENABLED=True"]


def test_settings_sin_jar_por_defecto():
    sys.path.insert(0, os.path.join(RAIZ, "crawler"))
    import seo_crawler.settings as ajustes

    assert ajustes.COOKIES_ENABLED is False
    assert "seo_crawler.middlewares.CookiesTrasElJarMiddleware" in ajustes.DOWNLOADER_MIDDLEWARES


def test_el_ejemplo_declara_conservar_cookies():
    with open(os.path.join(RAIZ, "projects", "_ejemplo", "config.json"), encoding="utf-8") as f:
        assert json.load(f)["http"]["conservar_cookies"] is False


def test_el_host_al_que_salta_la_semilla_no_recibe_credenciales():
    """El sitio por defecto del arnes: `localhost/` redirige a `127.0.0.1`, que
    pasa a ser interno para el rastreo (se sigue y se enlaza) pero no recibe
    las credenciales: es el salto de un staging a su SSO."""
    entrada = {
        "seeds": ["http://localhost:{port}/"],
        "config": {"max_depth": 1, "use_sitemap": False, "http": HTTP_STAGING},
        "http_real": True, "descarga_real": True, "ver_cabeceras": True,
    }
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=240, cwd=RAIZ)
    assert "@@ITEMS@@" in proc.stdout, proc.stderr[-4000:]
    cabeceras = json.loads(proc.stdout.split("@@CABECERAS@@", 1)[1].split("\n", 1)[0])
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    paginas = {i["url"]: i for i in items if i["_tipo"] == "PageItem"}
    assert paginas["http://OTRO/home"]["is_internal"] is True
    assert "http://OTRO/a" in paginas
    raiz = _de(cabeceras, SITIO, "/")
    assert raiz and raiz[0].get("authorization") == BASIC
    otro = _de(cabeceras, FUERA)
    assert otro and all(_sin_nada_del_job(h) for h in otro), otro


# ---------------------------------------------------------------------------
# Piezas, en proceso
# ---------------------------------------------------------------------------
def _piezas():
    sys.path.insert(0, os.path.join(RAIZ, "crawler"))
    sys.path.insert(0, RAIZ)
    from seo_crawler import middlewares
    from seo_crawler.extractors import normalize_host, url_en_hosts
    return middlewares, normalize_host, url_en_hosts


def _spider(semillas: list[str], http: dict, subdominios: bool = False):
    import types

    from shared.dominios import dominio_registrable

    middlewares, normalize_host, url_en_hosts = _piezas()
    hosts = set()
    for s in semillas:
        from urllib.parse import urlparse

        h = normalize_host(urlparse(s).hostname)
        hosts |= {h, h.removeprefix("www.")}
    raices = frozenset(dominio_registrable(h) for h in hosts) if subdominios else frozenset()
    spider = types.SimpleNamespace(_http_config=http, _hosts_credenciales=frozenset(hosts),
                                   _raices_credenciales=raices)
    spider._lleva_credenciales = lambda u: url_en_hosts(u, spider._hosts_credenciales,
                                                        spider._raices_credenciales)
    return spider


def test_url_en_hosts_www_subdominios_e_idn():
    _, normalize_host, url_en_hosts = _piezas()
    hosts = {"ejemplo.com", "www.ejemplo.com"}
    assert url_en_hosts("https://www.ejemplo.com/a", hosts)
    assert url_en_hosts("https://EJEMPLO.com./a", hosts)
    assert not url_en_hosts("https://blog.ejemplo.com/", hosts)
    assert url_en_hosts("https://blog.ejemplo.com/", hosts, {"ejemplo.com"})
    assert not url_en_hosts("https://notejemplo.com/", hosts, {"ejemplo.com"})
    assert not url_en_hosts("https://ejemplo.com.evil.net/", hosts, {"ejemplo.com"})
    idn = {normalize_host("españa.com")}
    assert url_en_hosts("https://españa.com/x", idn)
    assert url_en_hosts("https://www.xn--espaa-rta.com/", idn)
    assert not url_en_hosts("https://xn--espaa-rta.com.evil.net/", idn)


def _peticion(url: str, **meta):
    from scrapy import Request

    return Request(url, meta=meta)


def test_middleware_solo_pone_credenciales_en_los_hosts_del_sitio():
    middlewares, _, _ = _piezas()
    spider = _spider(["https://www.ejemplo.com/"], HTTP_STAGING, subdominios=True)
    mw = middlewares.HttpConfigMiddleware(con_jar=False)
    for url in ("https://ejemplo.com/a", "https://www.ejemplo.com/robots.txt",
                "https://tienda.ejemplo.com/"):
        r = _peticion(url)
        mw.process_request(r, spider)
        assert r.headers.get("Authorization") == BASIC.encode(), url
        assert r.headers.get("X-Secreto") == b"s3"
        assert r.headers.get("Cookie") == b"configurada=abc"
        assert not r.cookies
    for url in ("https://sso.otro.com/login", "https://ejemplo.com.evil.net/"):
        r = _peticion(url)
        # Lo que llega copiado de otra peticion (una redireccion de Scrapy
        # copia cabeceras) tambien se quita
        r.headers["Authorization"] = BASIC
        r.headers["Cookie"] = "configurada=abc"
        r.headers["X-Secreto"] = "s3"
        mw.process_request(r, spider)
        assert b"Authorization" not in r.headers and b"Cookie" not in r.headers, url
        assert b"X-Secreto" not in r.headers
        assert r.headers.get("Accept-Language") == b"eu-ES"


def test_middleware_con_jar_usa_request_cookies():
    middlewares, _, _ = _piezas()
    spider = _spider(["https://ejemplo.com/"], {"cookies": {"a": "1"}})
    r = _peticion("https://ejemplo.com/")
    middlewares.HttpConfigMiddleware(con_jar=True).process_request(r, spider)
    assert r.cookies == {"a": "1"} and b"Cookie" not in r.headers
    fuera = _peticion("https://otro.com/")
    fuera.cookies["a"] = "1"
    middlewares.HttpConfigMiddleware(con_jar=True).process_request(fuera, spider)
    assert not fuera.cookies


def test_middleware_con_render_no_pone_nada_en_la_peticion():
    """scrapy-playwright la aplicaria con route.continue_, y Chromium la
    conserva al seguir una redireccion a otro host."""
    middlewares, _, _ = _piezas()
    spider = _spider(["https://ejemplo.com/"], HTTP_STAGING)
    mw = middlewares.HttpConfigMiddleware()
    r = _peticion("https://ejemplo.com/a", playwright=True)
    mw.process_request(r, spider)
    assert b"Authorization" not in r.headers and b"Cookie" not in r.headers
    datos = r.meta["_http_render"]
    assert datos["cabeceras"] == {"X-Secreto": "s3", "Authorization": BASIC}
    assert datos["cookies"] == {"configurada": "abc"}
    assert datos["conservar"] is False
    assert datos["hosts"] == ["ejemplo.com"]
    fuera = _peticion("https://otro.com/", playwright=True)
    mw.process_request(fuera, spider)
    assert fuera.meta["_http_render"]["cabeceras"] == {}
    # Sin bloque http tambien: hay que limpiar las cookies del contexto
    sin = _peticion("https://ejemplo.com/", playwright=True)
    mw.process_request(sin, _spider(["https://ejemplo.com/"], {}))
    assert sin.meta["_http_render"]["conservar"] is False


def test_dominios_de_cookie():
    sys.path.insert(0, os.path.join(RAIZ, "crawler"))
    from seo_crawler.render import dominios_de_cookie

    assert dominios_de_cookie(["ejemplo.com", "www.ejemplo.com"], []) == [
        "ejemplo.com", "www.ejemplo.com"]
    assert dominios_de_cookie(["ejemplo.com"], ["ejemplo.com"]) == [".ejemplo.com", "ejemplo.com"]


def test_cookies_tras_el_jar():
    from scrapy.exceptions import NotConfigured
    from scrapy.utils.test import get_crawler

    middlewares, _, _ = _piezas()
    with pytest.raises(NotConfigured):
        middlewares.CookiesTrasElJarMiddleware.from_crawler(
            get_crawler(settings_dict={"COOKIES_ENABLED": False}))
    mw = middlewares.CookiesTrasElJarMiddleware.from_crawler(
        get_crawler(settings_dict={"COOKIES_ENABLED": True}))
    r = _peticion("https://ejemplo.com/")
    r.cookies["configurada"] = "abc"
    r.headers["Cookie"] = "configurada=abc; servidor=1"
    mw.process_request(r, None)
    assert not r.cookies and r.headers.get("Cookie") == b"configurada=abc; servidor=1"
