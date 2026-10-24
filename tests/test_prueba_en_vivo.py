"""Prueba de reglas a medida contra una URL en vivo: el lado del worker
(#34, decision 166), EJECUTADO contra un servidor HTTP local con curl_cffi de
verdad.

Lo que se sujeta:

- la forma de la URL (esquema, credenciales, host privado en cualquier forma);
- que un nombre se resuelve, que TODAS sus IP tienen que ser de Internet y que
  la IP comprobada se FIJA (`CURLOPT_RESOLVE`): `sitio.example` no existe en el
  DNS y la descarga llega igual al servidor local, con su `Host`;
- que una redireccion a una IP privada, o a un nombre que resuelve a una, se
  corta ANTES de pedirla (el servidor nunca ve `/secreto`);
- que las reglas salen con sus valores y sus errores por regla, que un 404 o un
  no-HTML no se evaluan y que el render se avisa;
- el hilo real del worker contra Redis de verdad (con `REDIS_TEST_URL`): con la
  politica de serie, `127.0.0.1` es privada y la respuesta lo dice; un mensaje
  vencido no se atiende.

El servidor escucha en 127.0.0.1 y los tests pasan una politica que solo da
por «de Internet» a 127.0.0.1: 127.0.0.2 (el mismo servidor, tambien loopback
en Linux) hace de red interna.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("curl_cffi")
pytest.importorskip("lxml")
pytest.importorskip("regex")

from shared import prueba_en_vivo as pv  # noqa: E402

FICHA = ("<html><head><title>Zapatilla</title></head><body>"
         "<span class='precio'>19,95 €</span><div class='sku'>SKU-1</div>"
         "<ul><li>38</li><li>39</li></ul></body></html>")


class _Manejador(BaseHTTPRequestHandler):
    vistas: list[tuple[str, str]] = []

    def log_message(self, *a):  # silencio
        pass

    def do_GET(self):  # noqa: N802
        type(self).vistas.append((self.path, self.headers.get("Host", "")))
        puerto = self.server.server_address[1]
        rutas = {
            "/ficha": (200, "text/html; charset=utf-8", FICHA),
            "/pdf": (200, "application/pdf", "%PDF-1.4"),
            "/no-esta": (404, "text/html", "<title>404</title>"),
            "/secreto": (200, "text/html", "<title>interno</title>"),
            "/grande": (200, "text/html", "<p>" + "x" * 200_000 + "</p>"),
        }
        saltos = {
            "/salto-ok": "/ficha",
            "/salto-ip-privada": f"http://127.0.0.2:{puerto}/secreto",
            "/salto-metadatos": "http://169.254.169.254/latest/meta-data/",
            "/salto-rebind": f"http://rebind.example:{puerto}/secreto",
            "/salto-localhost": f"http://localhost:{puerto}/secreto",
            "/salto-ftp": "ftp://sitio.example/x",
            "/bucle": "/bucle",
        }
        if self.path in saltos:
            self.send_response(302)
            self.send_header("Location", saltos[self.path])
            self.end_headers()
            return
        codigo, ct, cuerpo = rutas.get(self.path, (404, "text/html", "no"))
        datos = cuerpo.encode()
        self.send_response(codigo)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)


@pytest.fixture()
def servidor():
    _Manejador.vistas = []
    srv = ThreadingHTTPServer(("0.0.0.0", 0), _Manejador)
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()


DNS = {"sitio.example": ["127.0.0.1"], "rebind.example": ["127.0.0.2"],
       "mixto.example": ["127.0.0.1", "10.0.0.7"]}


def _resolver(host, puerto):
    if host in DNS:
        return DNS[host]
    raise OSError("no resuelve")


def _permitida(ip):
    return ip == "127.0.0.1"


KW = {"resolver": _resolver, "permitida": _permitida, "timeout_s": 10}


def _vistas():
    return [p for p, _h in _Manejador.vistas]


# ---------------------------------------------------------------------------
# Forma de la URL y politica de serie
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "ftp://x.com/", "file:///etc/passwd", "http:///sin-host", "http://u:p@x.com/",
    "http://localhost/", "http://127.0.0.1/", "http://0x7f000001/", "http://2130706433/",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://10.1.2.3/", "http://169.254.169.254/",
    "http://redis:6379/", "http://api.internal/", "http://x.com:99999/",
])
def test_la_forma_rechaza_lo_que_no_es_internet(url):
    with pytest.raises(pv.URLNoPermitida):
        pv.comprobar_forma(url)


def test_con_la_politica_de_serie_el_servidor_local_no_se_pide(servidor):
    with pytest.raises(pv.URLNoPermitida):
        pv.descargar_segura(f"http://127.0.0.1:{servidor}/ficha", timeout_s=5)
    assert _vistas() == []


def test_un_nombre_que_resuelve_a_una_ip_privada_no_se_pide(servidor):
    """Rebinding barato: el nombre tiene forma publica y su A es interna."""
    with pytest.raises(pv.URLNoPermitida, match="127.0.0.2"):
        pv.descargar_segura(f"http://rebind.example:{servidor}/secreto", **KW)
    # Con una sola IP privada entre varias tambien: curl podria elegir esa.
    with pytest.raises(pv.URLNoPermitida, match="10.0.0.7"):
        pv.descargar_segura(f"http://mixto.example:{servidor}/ficha", **KW)
    assert _vistas() == []


# ---------------------------------------------------------------------------
# IP fijada y redirecciones
# ---------------------------------------------------------------------------
def test_la_ip_comprobada_se_fija_y_el_host_se_conserva(servidor):
    """`sitio.example` no existe en el DNS: si llega al servidor es porque curl
    conecto a la IP que se comprobo (CURLOPT_RESOLVE), sin preguntar al DNS."""
    d = pv.descargar_segura(f"http://sitio.example:{servidor}/ficha", **KW)
    assert d.status == 200 and b"19,95" in d.cuerpo and d.ip == "127.0.0.1"
    assert _Manejador.vistas[-1] == ("/ficha", f"sitio.example:{servidor}")


def test_una_redireccion_se_sigue_comprobando_cada_salto(servidor):
    d = pv.descargar_segura(f"http://sitio.example:{servidor}/salto-ok", **KW)
    assert d.status == 200 and d.url_final.endswith("/ficha")
    assert [s["status"] for s in d.saltos] == [302]


@pytest.mark.parametrize("ruta", ["/salto-ip-privada", "/salto-metadatos", "/salto-rebind",
                                  "/salto-localhost", "/salto-ftp"])
def test_una_redireccion_a_la_red_interna_se_corta_antes_de_pedirla(servidor, ruta):
    with pytest.raises(pv.URLNoPermitida):
        pv.descargar_segura(f"http://sitio.example:{servidor}{ruta}", **KW)
    assert "/secreto" not in _vistas()
    assert _vistas() == [ruta]


def test_un_bucle_de_redirecciones_tiene_tope(servidor):
    with pytest.raises(pv.DescargaFallida, match="redirecciones"):
        pv.descargar_segura(f"http://sitio.example:{servidor}/bucle", max_saltos=3, **KW)
    assert len(_vistas()) == 4


def test_una_pagina_demasiado_grande_se_corta(servidor):
    with pytest.raises(pv.DescargaFallida, match="MB"):
        pv.descargar_segura(f"http://sitio.example:{servidor}/grande", max_bytes=1024 * 1024 // 8,
                            **KW)


def test_un_nombre_que_no_resuelve_es_un_fallo_de_descarga():
    with pytest.raises(pv.DescargaFallida, match="resolver"):
        pv.descargar_segura("http://no-existe.example/", **KW)


# ---------------------------------------------------------------------------
# atender: reglas, errores por regla, lo que no se evalua
# ---------------------------------------------------------------------------
REGLAS = {
    "custom_extraction": {
        "precio": {"tipo": "css", "expresion": ".precio", "valor": "numero"},
        "tallas": {"tipo": "css", "expresion": "li", "cuantos": "todos"},
        "ean": {"tipo": "css", "expresion": ".ean"},
        "lenta": {"tipo": "regex", "expresion": r"(a|aa)+$", "fuente": "texto"},
    },
    "custom_search": {"gtm": {"patron": "GTM-"}},
}


def test_atender_devuelve_valores_y_estado_por_regla(servidor):
    r = pv.atender({"url": f"http://sitio.example:{servidor}/salto-ok",
                    "config": {**REGLAS, "render_js": True}}, **KW)
    assert r["ok"], r
    por = {f["nombre"]: f for f in r["reglas"]}
    assert por["precio"]["estado"] == "ok" and por["precio"]["valor_num"] == 19.95
    assert por["tallas"]["valores"] == ["38", "39"]
    assert por["ean"]["estado"] == "sin_coincidencia"
    assert por["gtm"]["estado"] == "sin_coincidencia"
    assert por["lenta"]["estado"] in ("ok", "sin_coincidencia", "tiempo_agotado")
    assert r["descarga"]["status"] == 200 and r["descarga"]["url_final"].endswith("/ficha")
    assert any("NO" in a and "JavaScript" in a for a in r["avisos"])


def test_atender_no_evalua_un_404_ni_un_pdf(servidor):
    for ruta, texto in (("/no-esta", "404"), ("/pdf", "application/pdf")):
        r = pv.atender({"url": f"http://sitio.example:{servidor}{ruta}", "config": REGLAS}, **KW)
        assert r["ok"] and r["reglas"] == []
        assert set(r["no_evaluadas"]) == {"precio", "tallas", "ean", "lenta", "gtm"}
        assert any(texto in a for a in r["avisos"])


def test_atender_dice_por_que_no_pudo(servidor):
    r = pv.atender({"url": f"http://sitio.example:{servidor}/salto-ip-privada",
                    "config": REGLAS}, **KW)
    assert r == {"ok": False, "motivo": "url_no_permitida", "error": r["error"]}
    assert "127.0.0.2" in r["error"]
    r = pv.atender({"url": "http://no-existe.example/", "config": REGLAS}, **KW)
    assert r["ok"] is False and r["motivo"] == "descarga"
    r = pv.atender({"url": f"http://sitio.example:{servidor}/ficha", "config": {
        "custom_extraction": {"x": {"tipo": "xpath", "expresion": "//a[re:test(@h,'x')]"}}}}, **KW)
    assert r["ok"] is False and r["motivo"] == "reglas"


def test_atender_nunca_lanza(monkeypatch):
    def revienta(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(pv, "descargar_segura", revienta)
    r = pv.atender({"url": "http://x.com/", "config": REGLAS})
    assert r["ok"] is False and r["motivo"] == "interno" and "boom" in r["error"]


# ---------------------------------------------------------------------------
# El hilo del worker contra Redis de verdad
# ---------------------------------------------------------------------------
@pytest.fixture()
def redis_real():
    if not os.environ.get("REDIS_TEST_URL"):
        pytest.skip("sin REDIS_TEST_URL")
    import redis as redis_lib

    r = redis_lib.Redis.from_url(os.environ["REDIS_TEST_URL"], decode_responses=True)
    r.delete(pv.COLA_PRUEBAS)
    yield r
    r.delete(pv.COLA_PRUEBAS)


def test_el_hilo_del_worker_atiende_la_cola_con_la_politica_de_serie(servidor, redis_real,
                                                                    monkeypatch):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                    "crawler"))
    import worker

    monkeypatch.setattr(worker, "REDIS_URL", os.environ["REDIS_TEST_URL"])
    monkeypatch.setattr(worker, "BRPOP_TIMEOUT", 1)
    parada = threading.Event()
    hilo = threading.Thread(target=worker._atender_pruebas_en_vivo, args=(parada,), daemon=True)
    hilo.start()
    try:
        # Vencida: no se atiende y nadie contesta.
        redis_real.rpush(pv.COLA_PRUEBAS, json.dumps({
            "id": "vencida", "url": f"http://127.0.0.1:{servidor}/ficha",
            "config": REGLAS, "vence": time.time() - 1}))
        pid = pv.encolar(redis_real, {"url": f"http://127.0.0.1:{servidor}/ficha",
                                      "config": REGLAS}, espera_s=10)
        r = pv.esperar(redis_real, pid, espera_s=10)
        assert r is not None and r["ok"] is False and r["motivo"] == "url_no_permitida"
        assert pv.esperar(redis_real, "vencida", espera_s=1) is None
        assert _vistas() == []
    finally:
        parada.set()
        hilo.join(timeout=5)
