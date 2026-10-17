"""Las sondas de respuesta con el SeoSpider real (decisiones 123 y 124).

El sitio vive en `localhost`; los «terceros», en servidores propios en
127.0.0.2 (responde) y 127.0.0.3 (nada escucha: conexion rechazada), y un
`x.invalid` que no resuelve. Con la descarga de produccion (curl_cffi) y los
middlewares del bloque `http`, para mirar lo que LLEGA a cada servidor: el
metodo, y que no viaja ninguna credencial del job a un tercero (decision 114).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("scrapy")
pytest.importorskip("scrapy_impersonate")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXT = "http://127.0.0.2:{port}"
CAIDO = "http://127.0.0.3:{port}"


def _html(cuerpo: str) -> str:
    return f"<html><head><title>t</title></head><body>{cuerpo}</body></html>"


ENLACES = [
    f"{EXT}/ok", f"{EXT}/404", f"{EXT}/410", f"{EXT}/waf", f"{EXT}/429",
    f"{EXT}/head405", f"{EXT}/head404", f"{EXT}/redir", f"{EXT}/prohibida/x",
    f"{EXT}/cortar", f"{EXT}/500",
    f"{CAIDO}/a", f"{CAIDO}/b",
    "http://x.invalid/a", "http://x.invalid/b",
    "mailto:a@b.c", "whatsapp://send?text=x",
]

RUTAS = {
    "/s/inicio": [200, {}, _html(
        "".join(f'<a href="{u}">e</a>' for u in ENLACES)
        + '<a href="/s/dos">dos</a>'
        + '<img src="/img/rota.png" alt="x"><img src="/img/ok.png" alt="x">'
        + '<link rel="stylesheet" href="/css/x.css">'
        + f'<img src="{EXT}/ext-rota.png" alt="x">')],
    "/s/dos": [200, {}, _html(f'<a href="{EXT}/404">otra vez</a><a href="/s/inicio">i</a>')],
    "/img/ok.png": [200, {"Content-Type": "image/png", "Content-Length": "5000"}, ""],
    "/css/x.css": [200, {"Content-Type": "text/css"}, "body{}"],
}

EXTERNOS = {
    "127.0.0.2": {
        "/robots.txt": [200, {"Content-Type": "text/plain"},
                        "User-agent: *\nDisallow: /prohibida/\n"],
        "/ok": [200, {}, _html("ok")],
        "/404": [404, {}, _html("no")],
        "/410": [410, {}, _html("no")],
        # Un 403 de Cloudflare no es un enlace roto
        "/waf": [403, {"cf-mitigated": "challenge", "Server": "cloudflare"}, _html("x")],
        "/429": [429, {"Retry-After": "30"}, _html("despacio")],
        "/500": [500, {}, _html("error")],
        # Solo enrutan GET
        "/head405": {"HEAD": [405, {}, ""], "GET": [200, {}, _html("ok")]},
        "/head404": {"HEAD": [404, {}, ""], "GET": [200, {}, _html("ok")]},
        "/cortar": {"HEAD": ["cortar", {}, ""], "GET": [200, {}, _html("ok")]},
        "/redir": [301, {"Location": "/fin-roto"}, ""],
        "/fin-roto": [404, {}, _html("no")],
        "/ext-rota.png": [404, {"Content-Type": "image/png"}, ""],
    },
}

HTTP_STAGING = {
    "cookies": {"configurada": "abc"},
    "custom_headers": {"X-Secreto": "s3"},
    "basic_auth_user": "usu",
    "basic_auth_password": "clave",
}


def _rastrear(crawl_behavior: dict, resource_types: dict | None = None, *,
              max_urls: int | None = None, http: dict | None = None,
              descarga_real: bool = True):
    config = {"max_depth": 3, "use_sitemap": False, "http": http or {},
              "crawl_behavior": {"soft404_sondas": 0, **crawl_behavior},
              "resource_types": resource_types or {}}
    if max_urls is not None:
        config["max_urls"] = max_urls
    entrada = {
        "seeds": ["http://localhost:{port}/s/inicio"],
        "config": config, "rutas": RUTAS, "externos": EXTERNOS,
        "http_real": True, "descarga_real": descarga_real, "log_level": "INFO",
    }
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=240, cwd=RAIZ)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-6000:]
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    externas = json.loads(proc.stdout.split("@@EXTERNAS@@", 1)[1].split("\n", 1)[0])
    paginas = [i for i in items if i["_tipo"] == "PageItem"]
    filas = {}
    for i in items:
        if i["_tipo"] == "RespuestaSondeadaItem":
            port = i["url"].split(":")[2].split("/")[0] if i["url"].count(":") > 1 else ""
            filas[i["url"].replace(f":{port}", ":P") if port else i["url"]] = i
    return paginas, filas, externas, proc.stderr


@pytest.fixture(scope="module")
def con_sondas():
    return _rastrear({"comprobar_enlaces_externos": True, "comprobar_recursos": True},
                     {"check_external_resources": True}, http=HTTP_STAGING)


def _f(filas, ruta: str, host: str = "127.0.0.2:P"):
    return filas.get(f"http://{host}{ruta}")


def test_veredictos(con_sondas):
    _, filas, _, log = con_sondas
    esperado = {
        "/ok": "ok", "/404": "roto", "/410": "roto", "/waf": "no_verificable",
        "/429": "no_verificable", "/head405": "ok", "/head404": "ok",
        "/redir": "redirige_a_error", "/prohibida/x": "prohibido_por_robots",
        "/cortar": "ok", "/500": "error_servidor",
    }
    obtenido = {r: (_f(filas, r) or {}).get("veredicto") for r in esperado}
    assert obtenido == esperado, (obtenido, log[-4000:])


def test_un_roto_con_head_se_confirma_con_get(con_sondas):
    _, filas, externas, _ = con_sondas
    metodos = [(m, r) for ip, m, r, _ in externas if r == "/404"]
    assert ("HEAD", "/404") in metodos and ("GET", "/404") in metodos, metodos
    assert _f(filas, "/404")["metodo"] == "GET"
    # el 405 al HEAD se repite con GET, y el 200 del GET manda
    assert _f(filas, "/head405")["status_code"] == 200
    assert _f(filas, "/head405")["metodo"] == "GET"
    # Range en el GET de repuesto
    rangos = [h.get("range") for ip, m, r, h in externas if m == "GET" and r == "/head405"]
    assert rangos == ["bytes=0-1023"], rangos


def test_un_200_al_head_no_pide_get(con_sondas):
    _, filas, externas, _ = con_sondas
    assert [m for ip, m, r, _ in externas if r == "/ok"] == ["HEAD"]
    assert _f(filas, "/ok")["metodo"] == "HEAD"


def test_la_redireccion_se_sigue_y_se_anota(con_sondas):
    _, filas, _, _ = con_sondas
    f = _f(filas, "/redir")
    assert f["status_code"] == 301 and f["final_status"] == 404 and f["saltos"] == 1
    assert f["final_url"].endswith("/fin-roto")


def test_robots_del_tercero_se_respeta_sin_pedir_la_url(con_sondas):
    _, filas, externas, _ = con_sondas
    assert not [r for _, _, r, _ in externas if r.startswith("/prohibida")]
    assert _f(filas, "/prohibida/x")["metodo"] is None
    # una sola peticion de robots.txt por origen
    assert [r for _, _, r, _ in externas].count("/robots.txt") == 1


def test_host_que_rechaza_conexiones_se_da_por_caido(con_sondas):
    _, filas, _, _ = con_sondas
    a, b = _f(filas, "/a", "127.0.0.3:P"), _f(filas, "/b", "127.0.0.3:P")
    assert {a["veredicto"], b["veredicto"]} == {"sin_conexion"}, (a, b)
    assert a["host_caido"] and b["host_caido"]
    # el robots fallo, se probo UNA URL, y la otra ya no se pidio
    assert sorted([a["metodo"], b["metodo"]], key=str) == ["HEAD", None]


def test_dominio_que_no_existe(con_sondas):
    _, filas, _, _ = con_sondas
    a, b = filas.get("http://x.invalid/a"), filas.get("http://x.invalid/b")
    assert a["veredicto"] == b["veredicto"] == "dominio_inexistente", (a, b)
    assert a["host_caido"] and b["host_caido"]
    # con el DNS confirmado no se pide ninguna: basta el robots.txt
    assert a["metodo"] is None and b["metodo"] is None


def test_esquemas_que_no_son_web_no_se_piden(con_sondas):
    _, filas, _, _ = con_sondas
    assert not [u for u in filas if u.startswith(("mailto:", "whatsapp:"))]


def test_un_destino_enlazado_dos_veces_es_una_sola_sonda(con_sondas):
    _, _, externas, _ = con_sondas
    assert [m for _, m, r, _ in externas if r == "/404"].count("HEAD") == 1


def test_ninguna_credencial_del_job_llega_a_un_tercero(con_sondas):
    _, _, externas, _ = con_sondas
    assert externas
    for _, _, ruta, h in externas:
        assert "authorization" not in h, (ruta, h)
        assert "x-secreto" not in h, (ruta, h)
        assert "configurada=abc" not in (h.get("cookie") or ""), (ruta, h)


def test_las_sondas_no_son_urls_del_censo(con_sondas):
    paginas, _, _, _ = con_sondas
    assert not [p for p in paginas if "127.0.0.2" in p["url"] or "x.invalid" in p["url"]
                or "/img/" in p["url"] or "/css/" in p["url"]]


def test_recursos_internos_y_externos(con_sondas):
    _, filas, _, _ = con_sondas
    rota = _f(filas, "/img/rota.png", "SEMILLA")
    ok = _f(filas, "/img/ok.png", "SEMILLA")
    css = _f(filas, "/css/x.css", "SEMILLA")
    ext = _f(filas, "/ext-rota.png")
    assert rota["veredicto"] == "roto" and rota["uso"] == "recurso" and rota["interno"]
    assert ok["veredicto"] == "ok" and ok["content_length"] == 5000 and ok["metodo"] == "HEAD"
    assert css["veredicto"] == "ok"
    assert ext["veredicto"] == "roto" and not ext["interno"]


def test_no_gastan_max_urls():
    sin, filas_sin, ext_sin, _ = _rastrear({}, max_urls=2)
    con, filas_con, ext_con, _ = _rastrear(
        {"comprobar_enlaces_externos": True, "comprobar_recursos": True}, max_urls=2)
    assert not filas_sin and not ext_sin
    assert filas_con and ext_con
    assert len(con) == len(sin) == 2


def test_tope_y_tope_por_host():
    _, filas, externas, _ = _rastrear(
        {"comprobar_enlaces_externos": True, "enlaces_externos_max": 3})
    assert len(filas) == 3
    _, filas, externas, _ = _rastrear(
        {"comprobar_enlaces_externos": True, "sondas_por_host": 2})
    assert len([u for u in filas if "127.0.0.2" in u]) == 2


def test_solo_recursos_externos_con_check_external_resources():
    _, filas, _, _ = _rastrear({}, {"check_external_resources": True})
    assert set(filas) == {"http://127.0.0.2:P/ext-rota.png"}, filas


def test_con_la_descarga_de_twisted_los_fallos_se_clasifican_igual():
    """Sin curl_cffi (el handler de Scrapy) las excepciones son otras."""
    _, filas, _, _ = _rastrear({"comprobar_enlaces_externos": True}, descarga_real=False)
    assert filas["http://x.invalid/a"]["veredicto"] == "dominio_inexistente"
    assert _f(filas, "/a", "127.0.0.3:P")["veredicto"] == "sin_conexion"
    assert _f(filas, "/404")["veredicto"] == "roto"
