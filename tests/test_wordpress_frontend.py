"""La interfaz del adaptador WordPress (decision 168), ejecutando
`frontend/app.js` en Chromium:

- `resumenWordPress(job)`: la tarjeta del Resumen sale solo si el job trae el
  resumen del adaptador y hubo rastro de WordPress; cruza lo que el sitio
  publica (`X-WP-Total`), lo leido y lo que esta en el censo por tipo, dice si
  la lectura se corto y por que no se afirma lo no rastreado. El resumen es el
  que deja `sonda_wordpress.py` + `avisos_de_wordpress.py` (forma de d161).
- La casilla «Analizar WordPress (REST API)» del modal: solo se manda si se
  CAMBIA respecto a lo que cargo el modal (lo de serie o lo heredado), como
  las sondas, para que el job siga heredando del cliente y el perfil.
- `textoEntidadWp`: la entidad en la ficha de la URL.

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

WP = {
    "modo": "auto", "detectado": True, "rest": "abierta", "codigo": 200,
    "raiz": "https://blog.x.com/wp-json/", "nombre": "Blog X", "version": "6.8.3",
    "evidencia": ["meta_generator", "rest_api"], "plugin_seo": "Yoast SEO",
    "namespaces": ["oembed/1.0", "wp/v2", "wp-site-health/v1", "yoast/v1", "wc/v3",
                   "wc/store/v1", "contact-form-7/v1"],
    "tipos": {
        "post": {"clase": "entrada", "leidas": 120, "total": 120, "paginas": 2, "recortado": False},
        "page": {"clase": "entrada", "leidas": 8, "total": 8, "paginas": 1, "recortado": False},
        "category": {"clase": "termino", "leidas": 14, "total": 14, "paginas": 1},
        "attachment": {"clase": "medio", "leidas": 1000, "total": 2400, "paginas": 24,
                       "recortado": True},
        "guest-author": {"clase": "entrada", "status": 401},
    },
    "peticiones": 63, "cortada": None, "sondas": {"paginacion": 5, "adjuntos": 20},
    "analisis": {
        "entidades": 1142,
        "por_tipo": {"post": {"leidas": 120, "en_el_censo": 95},
                     "page": {"leidas": 8, "en_el_censo": 8},
                     "category": {"leidas": 14, "en_el_censo": 10},
                     "attachment": {"leidas": 1000, "en_el_censo": 3}},
        "wp_publicado_no_rastreado": {"emitidos": 0, "no_rastreadas": 25, "fuera_de_alcance": 2,
                                      "tipo_sin_pagina_publica": 0,
                                      "no_afirmable": "profundidad (max_depth 3 alcanzado)",
                                      "sin_afirmar": 23},
    },
}


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


def _resumen(adapters):
    return _evaluar("() => resumenWordPress(" + json.dumps({"adapters": adapters}) + ")")


# ------------------------------------------------------------- tarjeta ----
def test_sin_resumen_o_sin_rastro_no_hay_tarjeta():
    r = _evaluar(
        "() => [resumenWordPress({}), resumenWordPress({adapters: null}),"
        " resumenWordPress({adapters: {wordpress: {modo: 'auto', detectado: false, evidencia: [],"
        " rest: null}}}), resumenWordPress(null)]")
    assert r == [None, None, None, None]


def test_cruza_publicadas_leidas_y_censo_por_tipo():
    r = _resumen({"wordpress": WP})
    assert r["version"] == "6.8.3" and r["plugin_seo"] == "Yoast SEO"
    assert r["abierta"] is True and r["rest_texto"] == "abierta"
    filas = {f["tipo"]: f for f in r["filas"]}
    # Contenido primero (lo mas publicado arriba), luego taxonomias y medios.
    assert [f["tipo"] for f in r["filas"]] == ["post", "page", "guest-author", "category",
                                               "attachment"]
    assert filas["post"]["nombre"] == "Entrada"
    assert (filas["post"]["publicadas"], filas["post"]["en_el_censo"], filas["post"]["solo_wp"]) \
        == (120, 95, 25)
    assert filas["attachment"]["recortado"] is True and filas["attachment"]["publicadas"] == 2400
    # Un tipo que la REST API no deja leer: sin cifras, con el codigo.
    assert filas["guest-author"]["status"] == 401 and filas["guest-author"]["solo_wp"] is None


def test_dice_por_que_no_se_afirma():
    r = _resumen({"wordpress": WP})
    assert r["no_afirmable"].startswith("profundidad") and r["sin_afirmar"] == 23
    assert r["emitidos"] == 0


def test_plugins_por_namespace_sin_los_del_nucleo():
    r = _resumen({"wordpress": WP})
    assert r["namespaces"] == ["yoast", "wc", "contact-form-7"]


def test_sin_analizar_todavia():
    wp = {k: v for k, v in WP.items() if k != "analisis"}
    r = _resumen({"wordpress": wp})
    assert r["analizado"] is False
    post = next(f for f in r["filas"] if f["tipo"] == "post")
    assert post["leidas"] == 120 and post["en_el_censo"] is None and post["solo_wp"] is None


def test_rest_cerrada_con_rastro_de_wordpress():
    r = _resumen({"wordpress": {"modo": "auto", "detectado": False, "rest": "cerrada",
                                "codigo": 403, "evidencia": ["meta_generator"],
                                "version": "6.5", "tipos": {}}})
    assert r is not None and r["abierta"] is False
    assert r["rest_texto"] == "cerrada (403)" and r["filas"] == []


def test_forma_inesperada_no_rompe():
    r = _resumen({"wordpress": {"detectado": True, "tipos": [], "namespaces": "x",
                                "analisis": {"por_tipo": None}}})
    assert r["filas"] == [] and r["namespaces"] == []


# ------------------------------------------------------- ficha de URL ----
def test_texto_de_la_entidad():
    r = _evaluar(
        "() => [textoEntidadWp({tipo: 'post', id: '11', estado: 'publish', modificado: "
        "'2026-09-30T10:00:00'}), textoEntidadWp({tipo: 'category', id: '3', cuenta: 1}),"
        " textoEntidadWp({tipo: 'evento', id: '9', estado: 'draft'}), textoEntidadWp(null)]")
    assert r == ["Entrada · ID 11 · modificada 2026-09-30", "Categoría · ID 3 · 1 entrada",
                 "evento · ID 9 · draft", ""]


# ---------------------------------------------------------- formulario ----
def _lanzado(cuerpo: str):
    return _evaluar("() => { const f = _freshForm(); " + cuerpo + " return _configDesdeForm(f); }")


def test_sin_tocar_la_casilla_no_se_manda():
    assert "adapters" not in _lanzado("")


def test_desmarcarla_manda_off_y_volver_a_marcarla_no_manda_nada():
    assert _lanzado("f.wp_analizar = false;")["adapters"] == {"wordpress": {"modo": "off"}}
    assert "adapters" not in _lanzado("f.wp_analizar = false; f.wp_analizar = true;")


def test_lo_heredado_se_pinta_y_no_se_repite():
    r = _evaluar(
        "() => { const f = _freshForm(); f.cliente_id = 'c1';"
        " _formDesdeConfig(f, {adapters: {wordpress: {modo: 'off', max_peticiones: 200}}});"
        " const sin = _configDesdeForm(f); const marcada = f.wp_analizar; f.wp_analizar = true;"
        " return [marcada, sin, _configDesdeForm(f)]; }")
    marcada, sin_tocar, encendida = r
    assert marcada is False
    assert "adapters" not in sin_tocar
    # Encenderlo frente al perfil SI se manda; solo `modo`: la fusion del
    # servidor es profunda y los topes del perfil siguen.
    assert encendida["adapters"] == {"wordpress": {"modo": "auto"}}


def test_on_heredado_y_marcada_no_se_manda():
    c = _evaluar(
        "() => { const f = _freshForm(); f.cliente_id = 'c1';"
        " _formDesdeConfig(f, {adapters: {wordpress: {modo: 'on'}}}); return [f.wp_analizar,"
        " _configDesdeForm(f)]; }")
    assert c[0] is True and "adapters" not in c[1]


def test_al_quitar_el_cliente_lo_que_se_ve_es_lo_que_se_lanza():
    """Heredado `off` y luego «Sin cliente»: la casilla sigue sin marcar y,
    sin cliente, el servidor no hereda nada: hay que mandar el `off`."""
    c = _evaluar(
        "() => { const f = _freshForm(); f.cliente_id = 'c1';"
        " _formDesdeConfig(f, {adapters: {wordpress: {modo: 'off'}}});"
        " f.cliente_id = ''; return _configDesdeForm(f); }")
    assert c["adapters"] == {"wordpress": {"modo": "off"}}
