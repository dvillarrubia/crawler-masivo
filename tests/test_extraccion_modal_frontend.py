"""La pestaña «Extracción» del modal de Nuevo rastreo (#34, decision 166),
EJECUTANDO `frontend/app.js` en Chromium: lo que se ve es lo que se lanza.

Revision de #233:

- las reglas heredadas «sin tocar no se mandan» porque las hereda el servidor;
  al volver a «Sin cliente» ya no hay herencia, y el rastreo se lanzaba SIN
  las reglas que la pestaña seguia ensenando como heredadas;
- cambiar de cliente o de perfil rehacia el formulario y borraba sin aviso las
  reglas propias.

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("playwright")

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = [(RAIZ / "frontend" / n).read_text(encoding="utf-8") for n in ("incidencias.js", "app.js")]


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


HEREDADA = "{custom_extraction: {precio: {tipo: 'css', expresion: '.precio', valor: 'numero'}}}"


def test_heredadas_sin_tocar_no_se_mandan_con_cliente():
    r = _evaluar("() => { const f = _freshForm(); f.cliente_id = 'c1'; "
                 "_extraccionDesdeConfig(f, " + HEREDADA + "); return (_extraccionAConfig(f) ? {custom_extraction: _extraccionAConfig(f)} : {}); }")
    assert "custom_extraction" not in r


def test_al_volver_a_sin_cliente_las_heredadas_se_lanzan():
    r = _evaluar("""async () => {
        const a = app();
        const f = _freshForm(); f.cliente_id = 'c1';
        _extraccionDesdeConfig(f, """ + HEREDADA + """);
        f.extraccion.push(_xmFila('sku', {tipo: 'css', expresion: '.sku'}, true));
        f.extraccion[1].quitada = true;      // quitada con cliente: sin cliente, fuera
        a.createForm = f;
        f.cliente_id = '';
        await a.cambiarClienteModal();
        return {cfg: (_extraccionAConfig(a.createForm) ? {custom_extraction: _extraccionAConfig(a.createForm)} : {}),
                filas: a.createForm.extraccion.map(x => [x.nombre, x.heredada])};
    }""")
    assert r["cfg"]["custom_extraction"]["precio"]["expresion"] == ".precio"
    assert "sku" not in r["cfg"]["custom_extraction"]
    assert r["filas"] == [["precio", False]]      # ya no se ensena como heredada


def test_sin_cliente_una_fila_heredada_se_manda_aunque_no_se_haya_convertido():
    r = _evaluar("() => { const f = _freshForm(); f.cliente_id = 'c1'; "
                 "_extraccionDesdeConfig(f, " + HEREDADA + "); f.cliente_id = ''; "
                 "return _extraccionAConfig(f); }")
    assert r["precio"]["tipo"] == "css"


def test_cambiar_de_perfil_conserva_las_reglas_propias():
    r = _evaluar("""async () => {
        api = async () => ({config_heredada: {custom_extraction: {
            precio: {tipo: 'css', expresion: '.precio'},
            sku: {tipo: 'css', expresion: '.sku'}}}, seeds: []});
        const a = app();
        a.clientes = [];
        const f = _freshForm(); f.cliente_id = 'c1';
        f.extraccion = [_xmFila('mio', {tipo: 'css', expresion: '.mio'}, false),
                        _xmFila('sku', {tipo: 'css', expresion: '.ean'}, false)];
        a.createForm = f;
        await a.aplicarHerencia();
        return {cfg: (_extraccionAConfig(a.createForm) ? {custom_extraction: _extraccionAConfig(a.createForm)} : {}),
                filas: a.createForm.extraccion.map(x => [x.nombre, x.heredada])};
    }""")
    assert sorted(r["filas"]) == [["mio", False], ["precio", True], ["sku", False]]
    ce = r["cfg"]["custom_extraction"]
    # La propia con el nombre de una heredada la sustituye; la heredada sin
    # tocar no se manda.
    assert ce == {"mio": {"tipo": "css", "expresion": ".mio", "cuantos": "primero",
                          "devuelve": "texto", "valor": "texto"},
                  "sku": {"tipo": "css", "expresion": ".ean", "cuantos": "primero",
                          "devuelve": "texto", "valor": "texto"}}
