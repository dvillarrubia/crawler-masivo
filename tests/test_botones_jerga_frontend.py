"""Un boton principal por vista, cambios sin guardar y menos jerga (revision
de UX del 25-oct, 3.5 y 3.9; V42 y UX-14), EJECUTANDO `frontend/app.js` en
Chromium.

- Los helpers de formato dan coma decimal, punto de miles y «32,1 %»:
  salian «32.1%» y «841.2 KB» junto a cifras ya en castellano.
- «1 rastreo» y no «1 rastreos» (fmt.n).
- Las reglas de alertas se leen en frase, sin «<=» ni claves internas.
- Los cambios sin guardar de la ficha del cliente se detectan por seccion,
  volver al valor de antes no cuenta como cambio y salir de la ficha
  pregunta; cambiar de pestaña dentro del mismo cliente no.
- El boton de aceptar de las preguntas no sale desactivado: su `:disabled`
  daba '' y Alpine pone el atributo con cualquier valor que no sea false,
  null o undefined. Se evalua la expresion tal cual esta en index.html.

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from test_evolucion_frontend import _evaluar  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent


def test_formato_de_numeros_en_castellano():
    r = _evaluar("""() => [fmt.pct(32.1), fmt.pct(5), fmt.pct(null), fmt.pctDe(0.321), fmt.pctDe(0.5, 0),
        fmt.dec(1234.56), fmt.dec(0.1234, 3), fmt.dec('x'), fmt.bytes(861388.8), fmt.bytes(3221225472),
        fmt.bytes(512), fmt.duracion(5400), fmt.num(4319), fmt.n(1, 'rastreo', 'rastreos'),
        fmt.n(3, 'rastreo', 'rastreos'), fmt.n(1500, 'página', 'páginas'), fmt.n(0, 'regla', 'reglas')]""")
    assert r == ["32,1 %", "5,0 %", "—", "32,1 %", "50 %",
                 "1.234,6", "0,123", "—", "841,2 KB", "3,0 GB",
                 "512 B", "1,5 h", "4.319", "1 rastreo",
                 "3 rastreos", "1.500 páginas", "0 reglas"]


def test_las_reglas_se_leen_en_frase():
    r = _evaluar("""() => [
        fraseReglaPropia({medida: 'indexables', modo: 'cambio_pct', operador: '<=', umbral: -10},
                         {medidas: {indexables: {etiqueta: 'Páginas indexables'}}}),
        fraseReglaPropia({medida: 'incidencia:soft_404_candidato', segmento: 'fichas', modo: 'valor', operador: '>', umbral: '1500'}, null),
        fmt.reglaDiff('rotas'), fmt.reglaDiff('salen_del_indice')]""")
    assert r[0] == "Páginas indexables: el cambio en % frente al anterior es menor o igual que −10 %"
    assert r[1] == "Páginas con «Posible soft 404» en «fichas»: el valor del rastreo es mayor que 1.500"
    assert r[2] == "Páginas que ahora responden con error"
    assert "_" not in r[3]


CLIENTE = {"id": "c1", "nombre": "Lopesan", "slug": "lopesan", "dominios": ["www.lopesan.com"],
           "zona_horaria": "Europe/Madrid", "notas": "", "integraciones": [], "config_defecto": {}}
REGLAS = {"propias": [{"id": "r1", "nombre": "Caen", "medida": "indexables", "segmento": "", "modo": "cambio_pct",
                       "operador": "<=", "umbral": -10}], "comparacion": {"rotas": {"pct_sitio": 5}},
          "resumen_semanal": {"activo": True, "dia": 1, "hora": 8}}


def test_cambios_sin_guardar_por_seccion():
    r = _evaluar("""() => { const a = app(); const out = {};
        a.view = 'client'; a.cliente = %s; a.clienteForm = _formCliente(a.cliente);
        a._reglasDe = 'c1'; a.reglasCliente = { efectivas: %s };
        a.reglasForm = JSON.parse(JSON.stringify(a.reglasCliente.efectivas)); a._reglasFoto = _fotoReglas(a.reglasForm);
        out.limpio = a.cambiosSinGuardar().length;
        // un umbral escrito igual que estaba (texto) no es un cambio
        a.reglasForm.propias[0].umbral = '-10'; a.reglasForm.comparacion.rotas.pct_sitio = '5';
        out.mismoValor = a.cambiosSinGuardar().length;
        a.reglasForm.propias[0].umbral = '-15'; a.clienteForm.notas = 'algo';
        out.ids = a.cambiosSinGuardar().map(x => x.id);
        out.texto = a.textoCambiosSinGuardar();
        out.saleOtraPestana = a._saleDeLosCambios('#/clientes/c1/alertas');
        out.saleOtroCliente = a._saleDeLosCambios('#/clientes/c2');
        out.saleProduccion = a._saleDeLosCambios('#/produccion');
        a.descartarCambiosPendientes();
        out.trasDescartar = a.cambiosSinGuardar().length;
        a.view = 'ajustes'; a.ajustes = { slack: { tokenTexto: 'xoxb-1' } };
        out.ajustes = a.cambiosSinGuardar().map(x => x.id);
        out.principal = ['clients', 'programados', 'jobs', 'produccion'].map(v => { a.view = v; return a.principalPropia(); });
        return out; }""" % (json.dumps(CLIENTE), json.dumps(REGLAS)))
    assert r["limpio"] == 0
    assert r["mismoValor"] == 0
    assert r["ids"] == ["datos", "reglas"]
    assert r["texto"] == "Cambios sin guardar en los datos del cliente y las reglas de alertas"
    assert r["saleOtraPestana"] is False
    assert r["saleOtroCliente"] is True and r["saleProduccion"] is True
    assert r["trasDescartar"] == 0
    assert r["ajustes"] == ["slack"]
    assert r["principal"] == [True, True, False, False]


def test_salir_con_cambios_pregunta_y_vuelve_si_no_se_acepta():
    r = _evaluar("""async () => { const a = app(); const fueA = []; let preguntas = 0;
        a.view = 'client'; a.cliente = %s; a.clienteTab = 'config'; a.clienteForm = _formCliente(a.cliente);
        a._irARuta = async h => { fueA.push(h); };
        a.clienteForm.notas = 'sin guardar';
        history.replaceState(null, '', '#/produccion');
        a.preguntar = async () => { preguntas++; return false; };
        await a._alCambiarRuta();
        const tras1 = [location.hash, fueA.length, a.clienteForm.notas];
        history.replaceState(null, '', '#/produccion');
        a.preguntar = async () => { preguntas++; return true; };
        await a._alCambiarRuta();
        const tras2 = [fueA.slice(), a.clienteForm.notas];
        // otra pestaña del mismo cliente: no pregunta
        a.clienteForm.notas = 'otra vez';
        history.replaceState(null, '', '#/clientes/c1/alertas');
        await a._alCambiarRuta();
        return { tras1, tras2, preguntas, ultima: fueA[fueA.length - 1] }; }""" % json.dumps(CLIENTE))
    assert r["tras1"] == ["#/clientes/c1/configuracion", 0, "sin guardar"]
    assert r["tras2"] == [["#/produccion"], ""]
    assert r["preguntas"] == 2
    assert r["ultima"] == "#/clientes/c1/alertas"


def test_el_perfil_con_cambios_pregunta_al_cerrar():
    r = _evaluar("""async () => { const a = app(); let preguntas = 0;
        a.preguntar = async () => { preguntas++; return false; };
        a.perfilForm = { id: 'p', nombre: 'Blog', rol: 'propio', seeds: 'https://x/', notas: '', activo: true };
        a._perfilFoto = JSON.stringify(a.perfilForm); a._perfilCfgBase = {};
        await a.cerrarPerfil(); const sinCambios = a.perfilForm === null;
        a.perfilForm = JSON.parse(a._perfilFoto); a.perfilForm.nombre = 'Blog 2';
        await a.cerrarPerfil(); const conCambios = a.perfilForm !== null;
        return [sinCambios, conCambios, preguntas]; }""")
    assert r == [True, True, 1]


def test_ia_lleva_a_problemas_y_nombra_las_reglas():
    r = _evaluar("""() => { const a = app();
        a.reglasForm = { propias: [{ id: 'r1', nombre: 'Fichas rotas' }] };
        return [a.problemaDePreguntaIA('es_soft_404'), a.problemaDePreguntaIA('paginas_sustitutas'),
                a.problemaDePreguntaIA('tipo_pagina'), a.nombreRegla('r1'), a.nombreRegla('rotas')]; }""")
    assert r == ["soft_404_candidato", "canibalizacion_por_consulta", None,
                 "Fichas rotas", "Páginas que ahora responden con error"]


def test_el_aceptar_de_las_preguntas_no_sale_desactivado():
    html = (RAIZ / "frontend" / "index.html").read_text(encoding="utf-8")
    bloque = html.split('<template x-if="dialogo">', 1)[1]
    expr = re.search(r'@click="cerrarDialogo\(true\)"\s*:disabled="([^"]+)"', bloque).group(1)
    r = _evaluar("""() => { const e = new Function('dialogo', 'return (' + %s + ')');
        return [e({ escribir: '', pedir: '', entrada: '' }),
                e({ escribir: 'eliminar', pedir: '', entrada: 'elim' }),
                e({ escribir: 'eliminar', pedir: '', entrada: 'eliminar' }),
                e({ escribir: '', pedir: 'Nombre', entrada: ' ' })]; }""" % json.dumps(expr))
    # Alpine solo quita `disabled` con false, null o undefined: '' lo pondria.
    assert r == [False, True, False, True]


def test_recargar_la_ficha_conserva_lo_no_guardado_de_otras_secciones():
    # Guardar o eliminar un perfil, o lanzar un rastreo desde la ficha,
    # recargan el cliente: lo editado y sin guardar en los datos o en las
    # integraciones no se pierde sin preguntar (revision de UX del 25-oct, 3.5).
    r = _evaluar("""async () => { const a = app(); const servidor = %s;
        window.api = async (ruta) => {
          if (ruta === '/clients/c1') return JSON.parse(JSON.stringify(servidor));
          if (ruta.endsWith('/profiles')) return [];
          if (ruta.startsWith('/jobs')) return { items: [] };
          return [];
        };
        for (const f of ['cargarAlertasAbiertas', 'cargarEstadoCliente', 'cargarProgramacionesCliente', '_cargarTabCliente']) a[f] = () => {};
        a.view = 'client'; a.cliente = JSON.parse(JSON.stringify(servidor)); a.clienteForm = _formCliente(a.cliente);
        a.clienteForm.notas = 'NOTA PENDIENTE';
        a.clienteForm.integraciones.push({ tipo: 'gsc', etiqueta: '', parametros: '', credencial_env: '', activa: true });
        await a.abrirCliente('c1', 'config');
        const tras = [a.clienteForm.notas, a.clienteForm.integraciones.length, a.cambiosSinGuardar().map(x => x.id)];
        // lo que se acaba de guardar se toma del servidor
        servidor.notas = 'guardada';
        await a.abrirCliente('c1', 'config', { guardado: ['datos'] });
        const trasGuardar = [a.clienteForm.notas, a.cambiosSinGuardar().map(x => x.id)];
        // otro cliente: nada se arrastra
        a.cliente = { ...a.cliente, id: 'c0' };
        await a.abrirCliente('c1', 'estado');
        return { tras, trasGuardar, otro: [a.clienteForm.notas, a.clienteForm.integraciones.length] }; }"""
                 % json.dumps(CLIENTE))
    assert r["tras"] == ["NOTA PENDIENTE", 1, ["datos", "integraciones"]]
    assert r["trasGuardar"] == ["guardada", ["integraciones"]]
    assert r["otro"] == ["guardada", 0]


def test_el_error_de_las_reglas_no_usa_claves_internas():
    r = _evaluar("""() => { const a = app(); const out = [];
        a.reglasCliente = { de_serie: { comparacion: { rotas: { activa: true, avisar: true, pct_sitio: 5, min_paginas: 1, pct_plantilla: 5 } },
                                        propias: [], resumen_semanal: { activo: true, dia: 1, hora: 8 } },
                            catalogo: { medidas: { indexables: { etiqueta: 'Páginas indexables' } } } };
        a.reglasForm = { comparacion: { rotas: { activa: true, avisar: true, pct_sitio: '', min_paginas: 1, pct_plantilla: 5 } },
                         propias: [], resumen_semanal: { activo: true, dia: 1, hora: 8 } };
        try { a._cuerpoReglas(); } catch (e) { out.push(e.message); }
        a.reglasForm.comparacion.rotas.pct_sitio = 5;
        a.reglasForm.propias = [{ id: 'regla-3', nombre: '', medida: 'indexables', modo: 'valor', operador: '<', umbral: '' }];
        try { a._cuerpoReglas(); } catch (e) { out.push(e.message); }
        return out; }""")
    assert r == ["«Páginas que ahora responden con error»: falta el número",
                 "«Páginas indexables: el valor del rastreo es menor que …»: falta el número"]
