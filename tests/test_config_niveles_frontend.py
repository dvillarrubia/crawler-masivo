"""El formulario de configuracion en tres niveles (decision 196; revision de
UX del 25-oct, 2.4), EJECUTANDO `frontend/app.js` en Chromium.

Lo que se sujeta:

- los limites y las opciones del formulario son los de `JobConfig`: validar
  en el navegador con otros numeros que la API seria decir «vale» a lo que
  luego da un 422 (o al reves);
- lo heredado se ve con su procedencia y no se fija en el nivel; escribir lo
  mismo que lo heredado tampoco lo fija («volver al heredado» lo quita);
- lo que se manda al lanzar es solo lo de este rastreo (y el tope del
  canario), y cambiar de cliente o de perfil no pierde lo ya cambiado;
- una cabecera heredada no se puede quitar desde abajo sin decirlo.

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal, get_args, get_origin

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


# --- los mismos limites que JobConfig ---------------------------------------

def _campo_pydantic(ruta: str):
    """El FieldInfo de `JobConfig` para una ruta con puntos."""
    from pydantic import BaseModel

    from api.schemas import JobConfig

    modelo = JobConfig
    partes = ruta.split(".")
    for p in partes[:-1]:
        anotacion = modelo.model_fields[p].annotation
        assert isinstance(anotacion, type) and issubclass(anotacion, BaseModel), ruta
        modelo = anotacion
    return modelo.model_fields[partes[-1]]


def _limites(info) -> tuple:
    ge = le = None
    for m in info.metadata:
        ge = getattr(m, "ge", ge) if getattr(m, "ge", None) is not None else ge
        le = getattr(m, "le", le) if getattr(m, "le", None) is not None else le
    return ge, le


def _literales(anotacion) -> set | None:
    if get_origin(anotacion) is Literal:
        return set(get_args(anotacion))
    for a in get_args(anotacion):
        r = _literales(a)
        if r:
            return r
    return None


def test_los_limites_y_las_opciones_son_los_de_jobconfig():
    pytest.importorskip("pydantic")
    from api.schemas import JobConfig

    campos = _evaluar("() => CAMPOS_CONFIG")
    serie = JobConfig().model_dump()
    assert len(campos) > 60
    for c in campos:
        info = _campo_pydantic(c["ruta"])
        valor = serie
        for p in c["ruta"].split("."):
            valor = valor[p]
        if c["tipo"] in ("entero", "decimal"):
            ge, le = _limites(info)
            assert (c.get("min"), c.get("max")) == (ge, le), c["ruta"]
            # «nulo» (vacio = None) solo donde JobConfig admite None
            assert bool(c.get("nulo")) == (valor is None or "None" in str(info.annotation)), c["ruta"]
        if c["tipo"] == "opciones":
            assert {o[0] for o in c["opciones"]} == _literales(info.annotation), c["ruta"]
        if c["tipo"] == "si_no":
            assert isinstance(valor, bool), c["ruta"]


def test_la_validacion_del_navegador():
    r = _evaluar("""() => {
        const c = CAMPO_CONFIG;
        return [cfgParsear(c.concurrent_requests_per_domain, '65'),
                cfgParsear(c.concurrent_requests_per_domain, '4,5'),
                cfgParsear(c.concurrent_requests_per_domain, ''),
                cfgParsear(c.max_urls, ''),
                cfgParsear(c.max_urls, '5.000'),
                cfgParsear(c['crawl_behavior.request_delay'], '0,5'),
                cfgParsear(c.exclude_patterns, ' /a/ \\n\\n/b/ '),
                cfgParsear(c['http.custom_headers'], 'X-Uno: 1\\nmal'),
                cfgParsear(c.topes_por_carpeta, '/blog/ = 500')];
    }""")
    assert r[0] == {"error": "Entre 1 y 64"}
    assert r[1] == {"error": "Tiene que ser un número entero"}
    assert r[2] == {"error": "Hace falta un número"}
    assert r[3] == {"valor": None}, "max_urls vacío = sin tope"
    assert r[4] == {"valor": 5000}, "el punto de los miles no es un decimal"
    assert r[5] == {"valor": 0.5}
    assert r[6] == {"valor": ["/a/", "/b/"]}
    assert r[7] == {"error": "Falta «:» en «mal»"}
    assert r[8] == {"valor": {"/blog/": 500}}


# --- heredado, cambiado y volver al heredado --------------------------------

BASE = "{concurrent_requests_per_domain: 6, crawl_behavior: {request_delay: 0, retry_count: 2}, render_js: false}"
ORIGEN = "{concurrent_requests_per_domain: 'cliente'}"


def _inst(cuerpo: str, nivel: str = "perfil", propia: str = "{}"):
    return _evaluar("() => { const i = cfgNuevo('" + nivel + "', {base: " + BASE + ", origen: " + ORIGEN
                    + ", serie: {concurrent_requests_per_domain: 8, render_js: false}, propia: " + propia
                    + "}); " + cuerpo + " }")


def test_lo_heredado_se_ve_con_su_procedencia_y_no_se_fija():
    r = _inst("return [cfgEfectivo(i, 'concurrent_requests_per_domain'), cfgEtiquetaOrigen(i, 'concurrent_requests_per_domain'),"
              " cfgEtiquetaOrigen(i, 'render_js'), i.propia];")
    assert r == [6, "del cliente", "de serie", {}]


def test_cambiar_fija_en_este_nivel_y_escribir_lo_heredado_no():
    r = _inst("cfgCambiar(i, 'concurrent_requests_per_domain', '2');"
              " const a = [JSON.parse(JSON.stringify(i.propia)), cfgEtiquetaOrigen(i, 'concurrent_requests_per_domain')];"
              " cfgCambiar(i, 'concurrent_requests_per_domain', '6'); return [a, i.propia];")
    (propia, etiqueta), despues = r
    assert propia == {"concurrent_requests_per_domain": 2}
    assert etiqueta == "cambiado aquí · del cliente: 6"
    assert despues == {}, "igual a lo heredado = sigue al cliente"


def test_un_error_no_toca_lo_guardado_y_volver_lo_limpia():
    r = _inst("cfgCambiar(i, 'crawl_behavior.request_delay', '99');"
              " const a = [cfgHayErrores(i), JSON.parse(JSON.stringify(i.propia)), cfgTextoCampo(i, 'crawl_behavior.request_delay')];"
              " cfgHeredar(i, 'crawl_behavior.request_delay'); return [a, cfgHayErrores(i)];",
              propia="{crawl_behavior: {request_delay: 1.5}}")
    (hay, propia, texto), despues = r
    assert hay is True and propia == {"crawl_behavior": {"request_delay": 1.5}} and texto == "99"
    assert despues is False


def test_volver_al_heredado_quita_la_clave_y_los_padres_vacios():
    r = _inst("cfgHeredar(i, 'crawl_behavior.request_delay'); return [i.propia, JSON.parse(i.json)];",
              propia="{crawl_behavior: {request_delay: 1.5}, max_urls: 10}")
    assert r == [{"max_urls": 10}, {"max_urls": 10}]


def test_una_cabecera_heredada_no_se_quita_en_silencio():
    r = _evaluar("""() => {
        const i = cfgNuevo('perfil', {base: {http: {custom_headers: {'X-Cliente': '1'}}},
                                      origen: {'http.custom_headers': 'cliente'}});
        const quitar = cfgCambiar(i, 'http.custom_headers', 'X-Otra: 2');
        cfgHeredar(i, 'http.custom_headers');
        cfgCambiar(i, 'http.custom_headers', 'X-Cliente: 1\\nX-Otra: 2');
        return [quitar, i.propia, cfgEfectivo(i, 'http.custom_headers')];
    }""")
    assert "X-Cliente" in r[0] and "del cliente" in r[0]
    assert r[1] == {"http": {"custom_headers": {"X-Otra": "2"}}}, "solo lo nuevo: se fusiona por clave"
    assert r[2] == {"X-Cliente": "1", "X-Otra": "2"}


def test_el_json_de_avanzado_sustituye_el_nivel_y_avisa_si_no_vale():
    r = _inst("i.json = '{\"marcas\": [\"acme\"]}'; const ok = cfgAplicarJson(i);"
              " const a = [ok, i.propia, cfgClavesFuera(i)]; i.json = '[1]'; return [a, cfgAplicarJson(i), i.jsonError];")
    (ok, propia, fuera), mal, error = r
    assert ok is True and propia == {"marcas": ["acme"]} and fuera == ["marcas"]
    assert mal is False and "objeto" in error


def test_la_contrasena_del_basic_solo_en_un_rastreo():
    r = _evaluar("() => [cfgCamposDe(cfgNuevo('cliente', {}), 'http').map(c => c.ruta),"
                 " cfgCamposDe(cfgNuevo('rastreo', {}), 'http').map(c => c.ruta)]")
    assert "http.basic_auth_password" not in r[0]
    assert "http.basic_auth_password" in r[1]


def test_la_huella_automatica_no_se_fija():
    r = _evaluar("""() => {
        const i = cfgNuevo('rastreo', {base: {impersonate: 'firefox147'}});
        const auto = cfgTextoCampo(i, 'impersonate');
        cfgCambiar(i, 'impersonate', 'safari260');
        const fijada = JSON.parse(JSON.stringify(i.propia));
        cfgCambiar(i, 'impersonate', '');
        return [auto, fijada, i.propia];
    }""")
    assert r == ["", {"impersonate": "safari260"}, {}]


# --- el modal de Nuevo rastreo ----------------------------------------------

def test_al_lanzar_se_manda_solo_lo_de_este_rastreo():
    r = _evaluar("""async () => {
        const enviados = [];
        api = async (ruta, op) => { if (op && op.method === 'POST' && ruta === '/jobs') enviados.push(JSON.parse(op.body)); return {items: []}; };
        const a = app();
        a.createForm = Object.assign(_freshForm(), {name: 'n', seeds: 'https://x.com/', cliente_id: 'c1', profile_id: 'p1'});
        a.cfgRastreo = cfgNuevo('rastreo', {base: {max_urls: null, concurrent_requests_per_domain: 3},
                                            origen: {concurrent_requests_per_domain: 'perfil'}});
        cfgCambiar(a.cfgRastreo, 'render_js', true);
        a.loadJobs = async () => {};
        await a.submitCreate();
        a.showCreate = true;
        await a.lanzarCanario();
        return enviados;
    }""")
    assert r[0] == {"name": "n", "seeds": ["https://x.com/"], "config": {"render_js": True},
                    "cliente_id": "c1", "profile_id": "p1"}
    assert r[1]["config"] == {"render_js": True, "max_urls": 250}
    assert r[1]["name"].startswith("CANARY - ")


def test_con_un_campo_con_error_no_se_lanza():
    r = _evaluar("""async () => {
        let llamado = false;
        api = async () => { llamado = true; return {}; };
        const a = app();
        a.createForm = Object.assign(_freshForm(), {name: 'n', seeds: 'https://x.com/'});
        a.cfgRastreo = cfgNuevo('rastreo', {base: {}});
        cfgCambiar(a.cfgRastreo, 'max_depth', '-1');
        await a.submitCreate();
        return [llamado, a.createError];
    }""")
    assert r[0] is False and "error" in r[1]


def test_cambiar_de_cliente_o_quitarlo_conserva_lo_cambiado():
    r = _evaluar("""async () => {
        const pedidas = [];
        api = async (ruta, op) => {
            pedidas.push((op && op.method || 'GET') + ' ' + ruta);
            if (ruta.startsWith('/clients/c1/config-resuelta'))
                return {config: {max_depth: 9, concurrent_requests_per_domain: 3}, origen: {max_depth: 'cliente'},
                        serie: {max_depth: 3}, config_heredada: {}, seeds: ['https://c1.com/']};
            if (ruta === '/clients/c1/profiles') return [];
            return {config: {max_depth: 3}, serie: {max_depth: 3}, origen: {}, avisos: []};
        };
        const a = app();
        a.clientes = [{id: 'c1', nombre: 'C1'}];
        a.createForm = Object.assign(_freshForm(), {cliente_id: 'c1'});
        await a.cambiarClienteModal('');
        cfgCambiar(a.cfgRastreo, 'render_js', true);
        const con = [cfgEfectivo(a.cfgRastreo, 'max_depth'), cfgEtiquetaOrigen(a.cfgRastreo, 'max_depth'), a.createForm.seeds];
        a.createForm.cliente_id = '';
        await a.cambiarClienteModal('');
        return {con, sin: [cfgEfectivo(a.cfgRastreo, 'max_depth'), a.cfgRastreo.propia], pedidas};
    }""")
    assert r["con"] == [9, "del cliente", "https://c1.com/"]
    assert r["sin"] == [3, {"render_js": True}], "sin cliente: lo de serie con lo cambiado aquí"
    assert "POST /clients/config-resuelta" in r["pedidas"]


def test_nuevo_rastreo_desde_un_cliente_trae_su_perfil_principal():
    r = _evaluar("""() => {
        const a = app();
        a.view = 'client'; a.cliente = {id: 'c1'};
        a.clientePerfiles = [{id: 'p0', nombre: 'Competidor', rol: 'competidor', activo: true},
                             {id: 'p1', nombre: 'Sitio principal', rol: 'propio', activo: true}];
        return a._presetDelContexto();
    }""")
    assert r == {"cliente_id": "c1", "profile_id": "p1"}


def test_asi_rastreara_enseña_lo_principal_y_lo_que_no_es_de_serie():
    r = _evaluar("""() => {
        const i = cfgNuevo('perfil', {base: {}});
        i.resuelta = {config: {max_urls: null, max_depth: 3, concurrent_requests_per_domain: 3,
                               concurrent_requests: 32, render_js: false, robots_mode: 'respect',
                               user_agent: UA_PRESETS.chrome_win, exclude_patterns: [],
                               crawl_behavior: {request_delay: 1.5}},
                      origen: {concurrent_requests_per_domain: 'cliente', 'crawl_behavior.request_delay': 'perfil'}};
        return cfgAsiLineas(i).map(l => [l.ruta, l.valor, l.origen, cfgOrigenAsi(l.origen, 'perfil')]);
    }""")
    filas = {x[0]: x[1:] for x in r}
    assert filas["max_urls"] == ["sin tope", "serie", "de serie"]
    assert filas["concurrent_requests_per_domain"] == ["3", "cliente", "del cliente"]
    assert filas["crawl_behavior.request_delay"] == ["1,5", "perfil", "cambiado aquí"]
    assert filas["user_agent"][0] == "Chrome - Windows"
    assert "crawl_behavior.retry_count" not in filas, "de serie y no principal: no sale"


def test_la_plantilla_del_formulario_esta_una_vez_y_se_usa_en_los_tres_sitios():
    html = (RAIZ / "frontend" / "index.html").read_text(encoding="utf-8")
    assert html.count('id="tpl-config-niveles"') == 1
    for nivel in ("cfgCliente", "cfgPerfil", "cfgRastreo"):
        assert f'x-data="{{ get i() {{ return {nivel} }} }}" x-html="plantillaCfg()"' in html, nivel
    # El JSON a mano solo queda en «Avanzado»
    assert "Configuracion de rastreo por defecto (JSON)" not in html
    assert "Configuracion del perfil (JSON)" not in html
