"""Capturas de pantalla con el SeoSpider real y Chromium (decision 173).

Contra un sitio local con dos paginas por plantilla y una imagen: se mira lo
que queda en el volumen de archivo (una captura por plantilla y perfil, JPEG
del tamano del dispositivo) y lo que le llega al servidor (la imagen la pide
la pestana de la captura, con el UA de cada dispositivo, y no la del rastreo
con render). Se salta sin un Chromium que lance.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import uuid

import pytest

pytest.importorskip("scrapy")
pytest.importorskip("scrapy_playwright")

from test_credenciales_y_cookies import _chromium_disponible  # noqa: E402

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200">'
       '<rect width="400" height="200" fill="#c00"/></svg>')


def _pagina(titulo: str) -> str:
    return (f"<html><head><title>{titulo}</title>"
            '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
            f'<body><main><h1>{titulo}</h1><img src="/img/banner.svg" alt="b">'
            "<p>" + " ".join(f"palabra{i}" for i in range(80)) + "</p></main></body></html>")


RUTAS = {
    "/p/home": [200, {}, "<html><head><title>Home</title></head><body>"
                + "".join(f'<a href="{r}">{r}</a>' for r in (
                    "/ficha/n-1", "/ficha/n-2", "/cat/c-1", "/cat/c-2"))
                + "</body></html>"],
    "/ficha/n-1": [200, {}, _pagina("Ficha 1")],
    "/ficha/n-2": [200, {}, _pagina("Ficha 2")],
    "/cat/c-1": [200, {}, _pagina("Cat 1")],
    "/cat/c-2": [200, {}, _pagina("Cat 2")],
    "/img/banner.svg": [200, {"Content-Type": "image/svg+xml"}, SVG],
}


@pytest.fixture(scope="module")
def chromium():
    ruta = _chromium_disponible()
    if ruta is None:
        pytest.skip("sin Chromium para Playwright")
    return ruta


def _rastrear(tmp_path, chromium: str, *, render: bool, **cb) -> dict:
    job_id = str(uuid.uuid4())
    crawl_behavior = {"soft404_sondas": 0, "render_crudo_muestra": 0, "sonda_tls": False, **cb}
    entrada = {
        "seeds": ["http://localhost:{port}/p/home"], "job_id": job_id,
        "config": {"max_depth": 2, "use_sitemap": False, "render_js": render,
                   "crawl_behavior": crawl_behavior},
        "rutas": RUTAS, "descarga_real": True, "ua_real": True, "ver_cabeceras": True,
        "chromium": chromium,
    }
    archivo = tmp_path / "archivo"
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=300, cwd=RAIZ,
                          env={**os.environ, "ARCHIVO_DIR": str(archivo)})
    assert "@@ITEMS@@" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-6000:]
    cabeceras = json.loads(proc.stdout.split("@@CABECERAS@@", 1)[1].split("\n", 1)[0])
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    carpeta = archivo / "capturas" / job_id
    metas = [json.loads(f.read_text()) for f in sorted(carpeta.glob("*.json"))] if carpeta.exists() else []
    return {"job_id": job_id, "carpeta": carpeta, "metas": metas, "cabeceras": cabeceras,
            "items": items, "log": proc.stderr}


def _tamano_jpeg(datos: bytes) -> tuple[int, int]:
    """(ancho, alto) del marcador SOF de un JPEG."""
    i = 2
    while i < len(datos):
        assert datos[i] == 0xFF, "JPEG mal formado"
        marca = datos[i + 1]
        largo = struct.unpack(">H", datos[i + 2:i + 4])[0]
        if marca in (0xC0, 0xC1, 0xC2):
            alto, ancho = struct.unpack(">HH", datos[i + 5:i + 9])
            return ancho, alto
        i += 2 + largo
    raise AssertionError("JPEG sin SOF")


def _imagenes(cabeceras) -> list[str]:
    return [h.get("user-agent", "") for _host, ruta, h in cabeceras if ruta == "/img/banner.svg"]


def test_una_por_plantilla_y_perfil_con_el_tamano_del_dispositivo(tmp_path, chromium):
    """Censo de escritorio SIN render: las capturas renderizan igual, una por
    plantilla (cupo 1) en los dos perfiles, y la imagen la piden ellas."""
    r = _rastrear(tmp_path, chromium, render=False, capturas_muestra=10,
                  capturas_por_plantilla=1, capturas_ambos_perfiles=True)
    por = {(m["plantilla"], m["perfil"]): m for m in r["metas"]}
    plantillas = {p for p, _ in por}
    # La home es su propia plantilla; /ficha/* y /cat/* tienen 2 paginas y cupo 1
    assert len(plantillas) == 3, (sorted(por), r["log"][-3000:])
    assert len(r["metas"]) == 6
    for (plantilla, perfil), m in por.items():
        img = (r["carpeta"] / f"{m['url_hash']}.{perfil}.jpg").read_bytes()
        assert img[:3] == b"\xff\xd8\xff"
        esperado = (412, 915) if perfil == "movil" else (1280, 720)
        assert _tamano_jpeg(img) == esperado, (plantilla, perfil)
        assert (m["ancho"], m["alto"]) == esperado
        assert ("Mobile" in m["user_agent"]) == (perfil == "movil"), m
    # La imagen la pidio la pestana de cada captura, con el UA de cada perfil
    uas = _imagenes(r["cabeceras"])
    assert any("Mobile" in ua for ua in uas) and any("Mobile" not in ua for ua in uas), uas
    # Sondas, no URLs del censo: las paginas salen una vez cada una
    paginas = [i["url"] for i in r["items"] if i["_tipo"] == "PageItem"]
    assert len(paginas) == len(set(paginas))


def test_con_render_y_paridad_a_la_vez(tmp_path, chromium):
    """Censo CON render y la paridad movil encendida: comparten el contexto
    `perfil_movil` sin pisarse, el rastreo no pide la imagen (la aborta) y la
    captura si; el tope total corta en 2 paginas."""
    r = _rastrear(tmp_path, chromium, render=True, capturas_muestra=2,
                  capturas_por_plantilla=1, capturas_ambos_perfiles=True,
                  paridad_movil_muestra=5, paridad_movil_por_plantilla=1)
    assert len({m["url_hash"] for m in r["metas"]}) == 2, r["log"][-3000:]
    assert {m["perfil"] for m in r["metas"]} == {"movil", "escritorio"}
    for m in r["metas"]:
        img = (r["carpeta"] / f"{m['url_hash']}.{m['perfil']}.jpg").read_bytes()
        assert _tamano_jpeg(img) == ((412, 915) if m["perfil"] == "movil" else (1280, 720))
    # La piden las capturas (una vez por perfil, o dos si Chromium no la tiene
    # en cache de la primera); las 5 paginas renderizadas del censo y las
    # sondas de paridad no (PLAYWRIGHT_ABORT_REQUEST): si las dejara pasar,
    # serian 9 o mas.
    uas = _imagenes(r["cabeceras"])
    assert 2 <= len(uas) <= 4, uas
    assert any("Mobile" in ua for ua in uas) and any("Mobile" not in ua for ua in uas), uas
    paridad = [i for i in r["items"] if i["_tipo"] == "CapturaAlternativaItem"]
    assert paridad and all(i["perfil"] == "movil" and i["modo"] == "render" for i in paridad)
    assert all(i["estado"] == "comparada" for i in paridad), paridad


def test_apagado_no_deja_nada(tmp_path, chromium):
    r = _rastrear(tmp_path, chromium, render=False)
    assert r["metas"] == [] and not r["carpeta"].exists()
    assert _imagenes(r["cabeceras"]) == []


# ---------------------------------------------------------------------------
# Reanudar: el cupo se rehace por pagina, de cualquier perfil (sin navegador)
# ---------------------------------------------------------------------------
def _spider_de_capturas(job_id: str, **cb):
    from scrapy.settings import Settings
    from seo_crawler.sonda_capturas import CapturasPantallaMixin

    class _Spider(CapturasPantallaMixin):
        pass

    s = _Spider()
    s.job_id = job_id
    s.job_config = {"crawl_behavior": cb}  # sin UA: el de serie, de escritorio
    s.settings = Settings()
    return s


def test_al_reanudar_la_captura_del_otro_perfil_gasta_el_cupo(tmp_path, monkeypatch):
    """La captura de escritorio (perfil del censo) de /ficha/n-1 fallo y la
    movil se guardo. Al reanudar, /ficha/* no vuelve a tener cupo: si lo
    tuviera, se pedirian las dos de /ficha/n-2 y el perfil movil acabaria con
    dos capturas de una plantilla con tope 1, y una mas en cada reanudacion."""
    from shared import capturas_pantalla

    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    job = str(uuid.uuid4())
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    capturas_pantalla.guardar(job, "ab" * 32, "movil", jpeg, {"plantilla": "/ficha/*"})
    s = _spider_de_capturas(job, capturas_muestra=1, capturas_por_plantilla=1,
                            capturas_ambos_perfiles=True)
    s._iniciar_capturas()
    assert s._capt_perfil_censo == "escritorio" and s._capt_perfiles == ["escritorio", "movil"]
    assert s._capt_cupos == {"/ficha/*": 1} and s._capt_pedidas == 1
    # Con el tope total gastado, ninguna pagina pide capturas
    assert list(s._capturas_de_pagina(None, "http://x.test/ficha/n-2")) == []


def test_al_reanudar_los_dos_perfiles_de_una_pagina_cuentan_una_vez(tmp_path, monkeypatch):
    from shared import capturas_pantalla

    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    job = str(uuid.uuid4())
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    for perfil in ("movil", "escritorio"):
        capturas_pantalla.guardar(job, "ab" * 32, perfil, jpeg, {"plantilla": "/ficha/*"})
    s = _spider_de_capturas(job, capturas_muestra=3, capturas_por_plantilla=2,
                            capturas_ambos_perfiles=True)
    s._iniciar_capturas()
    assert s._capt_cupos == {"/ficha/*": 1} and s._capt_pedidas == 1
