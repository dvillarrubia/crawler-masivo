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


def test_tls_marca_renovar_a_30_dias():
    filas = _con_job({"config": {}, "tls": {"hosts": {
        "a.x": {"verificado": True, "dias_restantes": 14.5},
        "b.x": {"verificado": True, "dias_restantes": 31},
        "c.x": {"verificado": False, "motivo": "caducado", "dias_restantes": -3}}}},
        "return filasTls(job).map(f => [f.host, f.nivel, f.renovar, f.estado]);")
    # A 30 dias o menos ya no es «Valido» en verde (revision de UX del 25-oct,
    # 3.6; V19): naranja y con los dias.
    assert filas == [["a.x", "orange", True, "Válido, caduca en 14 días"], ["b.x", "green", False, "Válido"],
                     ["c.x", "red", False, "No válido: caducado"]]


ROBOTS = {"bots": [{"bot": "googlebot", "token": "Googlebot", "tipo": "buscador"},
                   {"bot": "gptbot", "token": "GPTBot", "tipo": "entrenamiento"},
                   {"bot": "oai", "token": "OAI-SearchBot", "tipo": "busqueda_ia"}]}


def test_robots_en_una_frase():
    nada = _evaluar("() => resumenRobots(" + json.dumps({**ROBOTS, "hosts": [
        {"origen": "https://a.x", "bots": {"googlebot": {"alcance": "nada"}, "gptbot": {"alcance": "nada"}}},
        {"origen": "https://www.a.x", "bots": {"googlebot": {"alcance": "nada"}}}]}) + ")")
    assert nada["texto"].startswith("Ningún bot bloqueado") and "2 hosts" in nada["texto"]
    assert nada["nivel"] == "" and nada["abrir"] is False
    algo = _evaluar("() => resumenRobots(" + json.dumps({**ROBOTS, "hosts": [
        {"origen": "https://a.x", "bots": {"gptbot": {"alcance": "todo"}, "oai": {"alcance": "parte"}}}]}) + ")")
    assert algo["texto"] == "Bloqueados: GPTBot (todo en https://a.x), OAI-SearchBot (una parte en https://a.x)."
    assert algo["nivel"] == "orange" and algo["abrir"] is True
    grave = _evaluar("() => resumenRobots(" + json.dumps({**ROBOTS, "hosts": [
        {"origen": "https://a.x", "bots": {"oai": {"alcance": "todo"}}}, {"origen": "https://b.x", "bots": None}]}) + ")")
    assert grave["nivel"] == "red" and grave["sin_evaluar"] == 1
    # Un host sin evaluar (robots.txt inaccesible) no deja decir «ninguno»
    # sin mas: se dice, en naranja, y el detalle se abre.
    mezcla = _evaluar("() => resumenRobots(" + json.dumps({**ROBOTS, "hosts": [
        {"origen": "https://a.x", "bots": {"googlebot": {"alcance": "nada"}}},
        {"origen": "https://b.x", "bots": None}, {"origen": "https://c.x", "bots": None}]}) + ")")
    assert mezcla["texto"] == ("Ningún bot bloqueado en la home ni en la muestra del host evaluado. "
                               "2 hosts sin evaluar: mira su robots.txt en el detalle.")
    assert mezcla["nivel"] == "orange" and mezcla["abrir"] is True
    assert _evaluar("() => resumenRobots({hosts: []})") is None


def test_tls_usa_el_umbral_del_job():
    filas = _con_job({"config": {"analysis_thresholds": {"tls_dias_aviso": 2}}, "tls": TLS},
                     "return filasTls(job).find(f => f.host === 'pronto.x.com').estado;")
    # Con el aviso a 2 dias no es «Caduca pronto», pero a 3 dias tampoco es
    # «Valido» sin mas (3.6).
    assert filas == "Válido, caduca en 3 días"


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
# Las sondas en el formulario de configuracion (decision 196): lo heredado se
# ve con su procedencia y no se manda; lo cambiado en este nivel, si.
SERIE = ("{crawl_behavior: {comprobar_enlaces_externos: false, comprobar_recursos: false,"
         " enlaces_externos_max: 5000, recursos_max: 20000, sondas_por_host: 50},"
         " resource_types: {check_external_resources: false}}")


def _sondas(base: str, origen: str, cuerpo: str = ""):
    return _evaluar("() => { const i = cfgNuevo('rastreo', {base: " + base + ", origen: " + origen
                    + "}); " + cuerpo + " return {i, ve: ['crawl_behavior.recursos_max',"
                    " 'resource_types.check_external_resources'].map(r => cfgEfectivo(i, r))}; }")


def test_sin_tocar_las_sondas_no_se_mandan():
    assert _sondas(SERIE, "{}")["i"]["propia"] == {}


def test_lo_que_se_toca_se_manda():
    p = _sondas(SERIE, "{}", "cfgCambiar(i, 'crawl_behavior.comprobar_enlaces_externos', true);"
                " cfgCambiar(i, 'crawl_behavior.enlaces_externos_max', '800');"
                " cfgCambiar(i, 'resource_types.check_external_resources', true);")["i"]["propia"]
    assert p == {"crawl_behavior": {"comprobar_enlaces_externos": True, "enlaces_externos_max": 800},
                 "resource_types": {"check_external_resources": True}}


def test_lo_heredado_del_perfil_se_ve_y_no_se_repite():
    base = ("{crawl_behavior: {comprobar_recursos: true, recursos_max: 900},"
            " resource_types: {check_external_resources: true}}")
    origen = ("{'crawl_behavior.comprobar_recursos': 'perfil', 'crawl_behavior.recursos_max': 'perfil',"
              " 'resource_types.check_external_resources': 'perfil'}")
    r = _sondas(base, origen)
    assert r["ve"] == [900, True] and r["i"]["propia"] == {}
    # Apagar lo heredado SI se manda: es un cambio frente al perfil.
    apagado = _sondas(base, origen, "cfgCambiar(i, 'crawl_behavior.comprobar_recursos', false);")
    assert apagado["i"]["propia"] == {"crawl_behavior": {"comprobar_recursos": False}}
