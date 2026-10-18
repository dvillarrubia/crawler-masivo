"""Lo que LLEGA al servidor: UA, `sec-ch-ua` y plataforma del mismo navegador
(decision 132).

El SeoSpider real con la descarga de produccion (curl_cffi) contra un sitio
local que anota las cabeceras de cada peticion. La huella va por `-s IMPERSONATE`,
como la pone el worker; el par (UA, huella) es el que deja `JobConfig`, que se
comprueba aparte en `test_huella_y_ua.py` (esta imagen no lleva pydantic). Antes del cambio
llegaba un Chrome 124 de Windows con `sec-ch-ua-platform: "macOS"`.

Y las herramientas de control (`descargar_crudo`): sin UA, curl_cffi manda el
de un Chrome de macOS, que el Cloudflare de progym bloquea con cualquier
version de la huella.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("scrapy")
pytest.importorskip("scrapy_impersonate")
pytest.importorskip("curl_cffi")



HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Literales y no `perfiles.UA_POR_DEFECTO`: contra el codigo anterior el test
# tiene que fallar por lo que llega al servidor, no por un AttributeError.
UA_150 = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
UA_124 = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _html(cuerpo: str) -> str:
    return f"<html><head><title>t</title></head><body>{cuerpo}</body></html>"


RUTAS = {
    "/h/inicio": [200, {}, _html('<a href="/h/dos">dos</a>')],
    "/h/dos": [200, {}, _html("<p>dos</p>")],
}


def _rastrear(user_agent: str | None = None, impersonate: str | None = None
              ) -> tuple[list[dict], str]:
    """Sin argumentos, lo de serie: el UA de `shared.config` y la huella de
    `settings.py`, que es lo que sale de un `JobConfig()` sin tocar."""
    from shared.config import DEFAULT_USER_AGENT

    config = {"user_agent": user_agent or DEFAULT_USER_AGENT}
    if impersonate:
        config["impersonate"] = impersonate
    entrada = {
        "seeds": ["http://localhost:{port}/h/inicio"],
        "config": {"max_depth": 1, "use_sitemap": False,
                   **config,
                   "crawl_behavior": {"soft404_sondas": 0}},
        "rutas": RUTAS,
        "descarga_real": True,
        "ua_real": True,
        "ver_cabeceras": True,
        # Como el worker: `-s IMPERSONATE=<job.config.impersonate>`
        "settings": {"IMPERSONATE": impersonate} if impersonate else {},
    }
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=180, cwd=RAIZ)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-6000:]
    cabeceras = json.loads(proc.stdout.split("@@CABECERAS@@", 1)[1].split("\n", 1)[0])
    paginas = [h for (_host, ruta, h) in cabeceras if ruta.startswith("/h/")]
    assert paginas, cabeceras
    return paginas, proc.stderr


def test_el_rastreo_de_serie_llega_como_un_chrome_150_de_windows():
    paginas, stderr = _rastrear()
    for h in paginas:
        assert h["user-agent"] == UA_150
        assert 'v="150"' in h["sec-ch-ua"], h
        assert h["sec-ch-ua-platform"] == '"Windows"', h
    assert "no son el mismo navegador" not in stderr


def test_un_config_con_el_ua_de_124_llega_entero_como_chrome_124():
    paginas, stderr = _rastrear(UA_124, "chrome124")
    for h in paginas:
        assert h["user-agent"] == UA_124
        assert 'v="124"' in h["sec-ch-ua"], h
        assert h["sec-ch-ua-platform"] == '"Windows"', h
    assert "no son el mismo navegador" not in stderr


def test_una_mezcla_fijada_a_mano_se_respeta_y_se_avisa():
    paginas, stderr = _rastrear(UA_124, "chrome150")
    assert all(h["user-agent"] == UA_124 for h in paginas)
    assert "no son el mismo navegador" in stderr and "chrome150" in stderr


@pytest.fixture
def servidor():
    vistas: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            vistas.append({k.lower(): v for k, v in self.headers.items()})
            cuerpo = _html("<p>ficha</p>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/", vistas
    srv.shutdown()


def test_las_herramientas_de_control_bajan_con_el_ua_de_la_huella(servidor, monkeypatch):
    from shared.descarga_cruda import descargar_crudo

    monkeypatch.delenv("IMPERSONATE", raising=False)
    monkeypatch.delenv("USER_AGENT", raising=False)
    url, vistas = servidor
    descargar_crudo(url)
    assert vistas[-1]["user-agent"] == UA_150
    assert 'v="150"' in vistas[-1]["sec-ch-ua"]
    assert vistas[-1]["sec-ch-ua-platform"] == '"Windows"'
    # con otra huella (la del entorno), el UA de esa huella y no el de macOS
    monkeypatch.setenv("IMPERSONATE", "chrome124")
    descargar_crudo(url)
    assert vistas[-1]["user-agent"] == UA_124 and 'v="124"' in vistas[-1]["sec-ch-ua"]
    assert vistas[-1]["sec-ch-ua-platform"] == '"Windows"'
