"""Las tarjetas de comprobaciones de la ficha del rastreo y las sondas del
formulario, ejecutando `frontend/app.js` en Chromium.

- Ficha: `filasTls`, `resumenSoft404`, `resumenSondas` y `resumenSitemaps`
  leen `jobs.tls` (decision 110), `jobs.soft404` (105), `jobs.sondas_resumen`
  (123/124) y `jobs.sitemaps` (126). Los datos son los de un rastreo de verdad
  (spider + analisis contra un sitio local con un soft 404, el 2026-10-18) y
  la ficha TLS de `sondear_tls` contra hosts reales. Sin el dato, null: un
  rastreo viejo o con la sonda apagada no es «0 problemas».
- Formulario: las sondas de enlaces externos y recursos solo se mandan si se
  TOCAN; lo heredado de un perfil se precarga y no se repite en el job.

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("playwright")

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = [(RAIZ / "frontend" / n).read_text(encoding="utf-8") for n in ("incidencias.js", "app.js")]

TLS = {"hosts": {
    "www.lin3s.com": {"host": "www.lin3s.com", "puerto": 443, "fuente": "socket",
                      "verificado": True, "motivo": None, "detalle": None, "version": "TLSv1.3",
                      "not_after": "2027-03-06T23:59:59+00:00", "dias_restantes": 139.63,
                      "emisor": "Sectigo Public Server Authentication CA DV R36",
                      "version_antigua": False},
    "expired.badssl.com": {"host": "expired.badssl.com", "verificado": False, "motivo": "caducado",
                           "detalle": "certificate has expired", "version": "TLSv1.2",
                           "not_after": "2015-04-12T23:59:59+00:00", "dias_restantes": -4206.37,
                           "emisor": "COMODO RSA Domain Validation Secure Server CA",
                           "version_antigua": False},
    "pronto.x.com": {"host": "pronto.x.com", "verificado": True, "dias_restantes": 3.2,
                     "version": "TLSv1.3"},
    "proxy.x.com": {"host": "proxy.x.com", "omitida": "proxy"},
}}
SOFT404 = {
    "sondas": [
        {"clave": "sitio|raiz", "host": "sitio", "plantilla": None,
         "url": "http://sitio:8000/zz728e6f6ecaac5c", "status": 404, "redirige_a": None},
        {"clave": "sitio|/productos/:slug (2n)", "host": "sitio",
         "plantilla": "/productos/:slug (2n)",
         "url": "http://sitio:8000/productos/zza2037f2196907a", "status": 200, "redirige_a": None},
        {"clave": "sitio|/blog/:slug (2n)", "host": "sitio", "plantilla": "/blog/:slug (2n)",
         "url": "http://sitio:8000/blog/zz5", "status": None, "error": "bloqueada_por_robots"},
    ],
    "resumen": {"paginas_juzgadas": 8, "soft_404": 2, "soft_404_indexables": 2, "candidatos": 0,
                "sondas": 3, "sondas_con_huella": 2, "detalle_sondas": [
                    {"url": "http://sitio:8000/zz728e6f6ecaac5c", "status": 404, "coincidencias": 2,
                     "alcance": 8, "ambigua": False, "devuelve_portada": False},
                    {"url": "http://sitio:8000/productos/zza2037f2196907a", "status": 200,
                     "coincidencias": 2, "alcance": 4, "ambigua": False,
                     "devuelve_portada": False}]},
}
SONDAS = {"enlaces_externos": {"distintos": 451, "comprobados": 212, "rotos": 1,
                               "dominios_muertos": 0, "por_veredicto": {"roto": 1, "ok": 211}},
          "recursos": {"comprobados": 3, "rotos": 1, "por_veredicto": {"ok": 2, "roto": 1}}}
SITEMAPS = {"ficheros": [
    {"url": "http://sitio:8000/sitemap.xml", "status": 200, "formato": "urlset", "declaradas": 7,
     "devueltas": 7, "hijos": 0, "internas": 7, "bytes": 615, "cortado": False,
     "entradas_guardadas": 7},
    {"url": "http://sitio:8000/sitemap-2.xml", "status": 404}],
    "pedidos": 2, "leidos": 1, "parcial": True, "tope_entradas": 250000,
    "entradas_guardadas": 7, "entradas_recortadas": False}


def _evaluar(expr: str):
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await nav.new_page()
            await pagina.set_content("<html><body></body></html>")
            await pagina.add_script_tag(content="window.lucide = { createIcons() {} };")
            for f in FUENTES:
                await pagina.add_script_tag(content=f)
            salida = await pagina.evaluate(expr)
            await nav.close()
            return salida

    return asyncio.run(run())


def _con_job(job: dict, cuerpo: str):
    return _evaluar("() => { const job = " + json.dumps(job) + "; " + cuerpo + " }")


# ---------------------------------------------------------------- ficha ----
def test_sin_datos_no_hay_tarjetas():
    """Un job sin ninguno de los cuatro (anterior o con sondas apagadas)."""
    r = _con_job({"config": {}, "tls": None, "soft404": None, "sondas_resumen": None},
                 "return [filasTls(job), resumenSoft404(job), resumenSondas(job), "
                 "resumenSitemaps(job), filasTls({}), resumenSondas({sondas_resumen: {}})];")
    assert r == [None] * 6


def test_tls_lee_el_estado_de_cada_host():
    filas = _con_job({"config": {}, "tls": TLS}, "return filasTls(job);")
    por_host = {f["host"]: f for f in filas}
    assert [f["host"] for f in filas] == sorted(por_host)
    assert por_host["www.lin3s.com"]["nivel"] == "green"
    assert por_host["www.lin3s.com"]["dias"] == 139
    assert por_host["www.lin3s.com"]["emisor"].startswith("Sectigo")
    assert por_host["expired.badssl.com"]["nivel"] == "red"
    assert "caducado" in por_host["expired.badssl.com"]["estado"]
    assert por_host["pronto.x.com"]["estado"] == "Caduca pronto"
    assert por_host["proxy.x.com"]["nivel"] == "dim"


def test_tls_usa_el_umbral_del_job():
    filas = _con_job({"config": {"analysis_thresholds": {"tls_dias_aviso": 2}}, "tls": TLS},
                     "return filasTls(job).find(f => f.host === 'pronto.x.com').estado;")
    assert filas == "Válido"


def test_soft404_cruza_las_sondas_con_el_resumen():
    r = _con_job({"soft404": SOFT404}, "return resumenSoft404(job);")
    assert r["analizado"] and r["soft_404"] == 2 and r["indexables"] == 2
    raiz, productos, blog = r["filas"]
    assert raiz["nivel"] == "green" and raiz["coincidencias"] == 2 and raiz["alcance"] == 8
    assert productos["nivel"] == "red" and productos["plantilla"] == "/productos/:slug (2n)"
    assert blog["nivel"] == "dim" and "robots" in blog["lectura"]


def test_soft404_solo_con_sondas_y_sin_analizar():
    r = _con_job({"soft404": {"sondas": SOFT404["sondas"][:1]}}, "return resumenSoft404(job);")
    assert r["analizado"] is False and r["sondas"] == 1 and r["filas"][0]["coincidencias"] is None


def test_soft404_con_forma_inesperada_no_rompe():
    """`sondas` como dict (lo que usa un test de la API) no es la forma real."""
    r = _con_job({"soft404": {"sondas": {"x.com": {"status_code": 404}}}},
                 "return resumenSoft404(job);")
    assert r is None


def test_sondas_dice_cuando_no_se_comprobo_todo():
    r = _con_job({"sondas_resumen": SONDAS}, "return resumenSondas(job);")
    enlaces, recursos = r
    assert enlaces["parcial"] is True and enlaces["rotos"] == 1
    assert enlaces["por_veredicto"][0] == ["ok", 211]
    assert recursos["parcial"] is False and recursos["distintos"] is None


def test_sitemaps_resume_los_ficheros():
    r = _con_job({"sitemaps": SITEMAPS}, "return resumenSitemaps(job);")
    assert r["leidos"] == 1 and r["pedidos"] == 2 and r["parcial"] is True
    assert r["fallidos"] == 1 and r["devueltas"] == 7 and r["formatos"] == [["urlset", 1]]


def test_modo_lista_solo_con_profundidad_cero():
    assert _evaluar("() => [esModoLista({config: {max_depth: 0}}), esModoLista({config: {max_depth: 3}}),"
                    " esModoLista({config: {}}), esModoLista(null)]") == [True, False, False, False]


# ----------------------------------------------------------- formulario ----
def _lanzado(cuerpo: str):
    return _evaluar("() => { const f = _freshForm(); " + cuerpo + " return _configDesdeForm(f); }")


CLAVES = ("comprobar_enlaces_externos", "comprobar_recursos", "enlaces_externos_max",
          "recursos_max", "sondas_por_host")


def test_sin_tocar_las_sondas_no_se_mandan():
    c = _lanzado("")
    assert not any(k in c["crawl_behavior"] for k in CLAVES)
    assert "check_external_resources" not in c["resource_types"]


def test_lo_que_se_toca_se_manda():
    c = _lanzado("f.comprobar_enlaces_externos = true; f.enlaces_externos_max = '800';"
                 " f.check_external_resources = true;")
    assert c["crawl_behavior"]["comprobar_enlaces_externos"] is True
    assert c["crawl_behavior"]["enlaces_externos_max"] == 800
    assert c["resource_types"]["check_external_resources"] is True
    assert "recursos_max" not in c["crawl_behavior"]


def test_lo_heredado_del_perfil_se_precarga_y_no_se_repite():
    r = _evaluar(
        "() => { const f = _freshForm(); f.cliente_id = 'c1'; _formDesdeConfig(f, {crawl_behavior: "
        "{comprobar_recursos: true, recursos_max: 900}, resource_types: {check_external_resources: true}});"
        " const sin = _configDesdeForm(f); f.comprobar_recursos = false;"
        " return [f.recursos_max, f.check_external_resources, sin, _configDesdeForm(f)]; }")
    tope, externos, sin_tocar, apagado = r
    assert tope == 900 and externos is True
    assert not any(k in sin_tocar["crawl_behavior"] for k in CLAVES)
    assert "check_external_resources" not in sin_tocar["resource_types"]
    # Apagar lo heredado SI se manda: es un cambio frente al perfil.
    assert apagado["crawl_behavior"]["comprobar_recursos"] is False


def test_lo_tocado_antes_de_elegir_perfil_se_mantiene():
    c = _lanzado("f.comprobar_enlaces_externos = true; _formDesdeConfig(f, {max_depth: 2});")
    assert c["crawl_behavior"]["comprobar_enlaces_externos"] is True


def test_al_quitar_el_cliente_lo_que_se_ve_es_lo_que_se_lanza():
    """Con cliente, lo heredado se pinta y no se manda (el servidor lo hereda).
    Si despues se vuelve a «Sin cliente», el modal no rehace el formulario:
    las casillas siguen marcadas y, sin cliente, nadie hereda nada en el
    servidor. Sin mandarlas, el job salia SIN la sonda que la pantalla decia."""
    c = _evaluar(
        "() => { const f = _freshForm(); f.cliente_id = 'c1';"
        " _formDesdeConfig(f, {crawl_behavior: {comprobar_recursos: true, recursos_max: 900}});"
        " f.cliente_id = ''; f.profile_id = ''; return _configDesdeForm(f); }")
    assert c["crawl_behavior"]["comprobar_recursos"] is True
    assert c["crawl_behavior"]["recursos_max"] == 900


# ---------------------------------------------------- ficha en la pagina ----
# Las funciones puras no ven las plantillas: un `:key` repetido en un x-for
# hace que Alpine no pinte NINGUNA fila y lance un error. Se monta la pagina
# de verdad (index.html + Alpine) con la API simulada.
JID = "11111111-1111-1111-1111-111111111111"
STATS = {"issues_by_type": [], "urls_by_status_group": [], "top_hosts": [],
         "urls_by_resource_type": [], "total_urls": 3, "paginas_html": 3, "indexables": 3,
         "no_indexables": 0, "internal_count": 3, "external_count": 0,
         "click_depth_calculada": False}


def _ficha(job: dict, veredicto: dict | None = None):
    from playwright.async_api import async_playwright

    fe = RAIZ / "frontend"
    base = {"id": JID, "name": "t", "status": "completed", "seeds": ["https://sitio/"],
            "config": {}, "client_id": None, "owner_id": None, "total_urls_crawled": 3,
            "created_at": "2026-10-18T10:00:00", "started_at": None, "completed_at": None}
    base.update(job)

    async def run():
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await nav.new_page()
            avisos = []
            pagina.on("console", lambda m: avisos.append(m.text) if m.type in ("warning", "error")
                      and "Failed to load resource" not in m.text else None)
            pagina.on("pageerror", lambda e: avisos.append(f"pageerror: {e}"))

            async def servir(route):
                ruta = route.request.url.replace("http://t.local", "").split("?")[0]
                if ruta.startswith("/static/"):
                    f = fe / ruta[len("/static/"):]
                    tipo = "text/css" if f.suffix == ".css" else "application/javascript"
                    return await route.fulfill(body=f.read_text(encoding="utf-8"), content_type=tipo)
                if ruta == f"/api/jobs/{JID}":
                    return await route.fulfill(json=base)
                if ruta == f"/api/jobs/{JID}/stats":
                    return await route.fulfill(json=STATS)
                if ruta == f"/api/jobs/{JID}/veredicto" and veredicto:
                    return await route.fulfill(json=veredicto)
                if ruta.startswith("/api/"):
                    return await route.fulfill(status=404, json={"detail": "no"})
                return await route.fulfill(body=(fe / "index.html").read_text(encoding="utf-8"),
                                           content_type="text/html")

            async def vacio(route):
                await route.fulfill(body="", content_type="application/javascript")

            await pagina.route("http://t.local/**", servir)
            await pagina.route("https://cdn.plot.ly/**", vacio)
            await pagina.route("https://fonts.googleapis.com/**", vacio)
            try:
                await pagina.goto(f"http://t.local/#/rastreos/{JID}")
                await pagina.wait_for_function("window.Alpine !== undefined", timeout=15000)
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Alpine (CDN): {exc}")
            await pagina.wait_for_selector('[data-testid="tarjeta-soft404"], [data-testid="veredicto-lista"]',
                                           timeout=10000)
            await pagina.wait_for_timeout(300)
            filas = {
                "soft404": await pagina.locator('[data-testid="tarjeta-soft404"] tbody tr').count(),
                "ejemplos": await pagina.locator('[data-testid="veredicto-lista"] .text-dim[style*="word-break"]').count(),
            }
            await nav.close()
            return filas, avisos

    return asyncio.run(run())


def test_una_sonda_que_redirige_en_el_mismo_host_pinta_sus_dos_filas():
    """http -> https en el mismo host: el spider repite la sonda en el destino
    con la MISMA clave (`sonda_soft404._repetir_sonda_en_destino`). Con la
    clave como `:key`, Alpine no pintaba la tabla."""
    sondas = [
        {"clave": "sitio|raiz", "host": "sitio", "plantilla": None,
         "url": "http://sitio/zz1", "status": 301, "redirige_a": "https://sitio/zz1"},
        {"clave": "sitio|raiz", "host": "sitio", "plantilla": None,
         "url": "https://sitio/zz1", "status": 404, "redirige_a": None},
        {"clave": "sitio|/p/:s", "host": "sitio", "plantilla": "/p/:s",
         "url": "https://sitio/p/zz", "status": 200, "redirige_a": None},
    ]
    filas, avisos = _ficha({"soft404": {"sondas": sondas}})
    assert filas["soft404"] == 3
    assert not avisos, avisos


def test_el_veredicto_pinta_ejemplos_repetidos():
    """`rastreo_bloqueado_por_robots` lista las semillas tal cual, y la API no
    quita repetidas: dos ejemplos con la misma URL."""
    v = {"veredicto": "falla", "urls_de_la_lista": 2, "no_concluyente_por": [],
         "motivos": [{"tipo": "rastreo_bloqueado_por_robots", "mensaje": "robots.txt bloquea",
                      "paginas": 2, "ejemplos": [{"url": "https://sitio/a"}, {"url": "https://sitio/a"}]}]}
    filas, avisos = _ficha({"config": {"max_depth": 0}}, v)
    assert filas["ejemplos"] == 2
    assert not avisos, avisos
