"""«Revisar a Jev» (#8, decision 211), ejecutando `frontend/app.js` en Chromium
con la API simulada (ninguna llamada sale de la pagina).

- La ruta `#/revision?pregunta=` abre la pregunta y carga lo pendiente.
- Se etiqueta con un clic o con el numero de la respuesta, y lo que dijo Jev
  solo se ensena DESPUES (a ciegas).
- Saltar no guarda nada; al acabarse la tanda se pide la siguiente.
- El estado que vio Jev se pinta campo a campo.

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright")

from test_evolucion_frontend import _evaluar  # noqa: E402

ITEMS = [
    {"decision_id": 1, "pregunta": "es_soft_404", "version": 2, "tipo": "noul", "instrucciones": "¿Error?",
     "descripcion": "", "url": "https://x.com/a", "job_id": "j", "ambito": "url",
     "opciones": [{"valor": "true", "texto": "Sí", "detalle": ""}, {"valor": "false", "texto": "No", "detalle": ""}],
     "estado": {"ruta": "/a", "titulo": "Agotado"}},
    {"decision_id": 2, "pregunta": "es_soft_404", "version": 2, "tipo": "noul", "instrucciones": "¿Error?",
     "descripcion": "", "url": "https://x.com/b", "job_id": "j", "ambito": "url",
     "opciones": [{"valor": "true", "texto": "Sí", "detalle": ""}, {"valor": "false", "texto": "No", "detalle": ""}],
     "estado": {"ruta": "/b", "palabras": ["hola"]}},
]


def _app(cuerpo: str) -> str:
    return """async () => {
        const items = %s;
        const llamadas = [];
        window.api = async (p, o = {}) => {
            llamadas.push([p, o.method || 'GET', o.body ? JSON.parse(o.body) : null]);
            if (p === '/decisiones/revision/resumen') return [{ pregunta: 'es_soft_404', por_revisar: 2, etiquetadas: 0 }];
            if (p.startsWith('/decisiones/revision?')) return llamadas.filter(l => l[0].startsWith('/decisiones/revision?')).length === 1 ? items : [];
            if (p.endsWith('/etiqueta')) {
                const b = JSON.parse(o.body);
                return { etiqueta: b.etiqueta, jev: 'true', confianza: 0.6, coincide: b.etiqueta === 'true' };
            }
            throw new Error('no simulado: ' + p);
        };
        const a = app();
        a.showToast = () => {};
        a._salirDeJob = () => {};
        %s
    }""" % (json.dumps(ITEMS), cuerpo)


def test_etiquetar_a_ciegas_y_pasar_a_la_siguiente():
    r = _evaluar(_app("""
        await a.verRevision('es_soft_404');
        const antes = { n: a.revision.items.length, resultado: a.revision.resultado, actual: a.revisionActual.decision_id };
        await a.etiquetarRevision('false');
        const tras = JSON.parse(JSON.stringify(a.revision.resultado));
        await a.siguienteRevision();
        const luego = a.revisionActual.decision_id;
        return { antes, tras, luego, migas: a.migas(), llamadas };"""))
    assert r["antes"] == {"n": 2, "resultado": None, "actual": 1}
    assert r["tras"]["coincide"] is False and r["tras"]["jev"] == "true" and r["tras"]["item"]["decision_id"] == 1
    assert r["luego"] == 2
    assert [m["texto"] for m in r["migas"]] == ["Revisar a Jev", "es_soft_404"]
    put = [x for x in r["llamadas"] if x[1] == "PUT"]
    assert put == [["/decisiones/1/etiqueta", "PUT", {"etiqueta": "false", "nota": None, "autor": None}]]


def test_el_numero_etiqueta_y_saltar_no_guarda():
    r = _evaluar(_app("""
        await a.verRevision('es_soft_404');
        a.saltarRevision();
        const tras_saltar = a.revisionActual.decision_id;
        a.teclaRevision({ key: '1', target: { tagName: 'BODY' }, preventDefault() {} });
        await new Promise(r => setTimeout(r, 0));
        a.teclaRevision({ key: '2', target: { tagName: 'INPUT' }, preventDefault() {} });
        await new Promise(r => setTimeout(r, 0));
        return { tras_saltar, puts: llamadas.filter(l => l[1] === 'PUT'), resultado: a.revision.resultado && a.revision.resultado.etiqueta };"""))
    assert r["tras_saltar"] == 2, "saltar manda la fila al final"
    assert r["puts"] == [["/decisiones/2/etiqueta", "PUT", {"etiqueta": "true", "nota": None, "autor": None}]]
    assert r["resultado"] == "true", "escribiendo en la nota, el número no etiqueta"


def test_al_acabar_la_tanda_pide_otra_y_dice_que_no_queda():
    r = _evaluar(_app("""
        await a.verRevision('es_soft_404');
        for (const _ of [1, 2]) { await a.etiquetarRevision('true'); await a.siguienteRevision(); }
        return { actual: a.revisionActual, pedidas: llamadas.filter(l => l[0].startsWith('/decisiones/revision?')).length };"""))
    assert r["actual"] is None and r["pedidas"] == 2


def test_los_campos_del_estado():
    r = _evaluar("() => camposDeEstado({ ruta: '/a', palabras: ['x', 'y'], texto: 'z'.repeat(200) })")
    assert [c["clave"] for c in r] == ["ruta", "palabras", "texto"]
    assert r[0]["largo"] is False and r[1]["largo"] is True and r[2]["largo"] is True
    assert json.loads(r[1]["valor"]) == ["x", "y"]
    assert _evaluar("() => camposDeEstado(null)") == []


def test_dos_pulsaciones_seguidas_mandan_un_solo_put_y_los_modificadores_no_etiquetan():
    r = _evaluar(_app("""
        await a.verRevision('es_soft_404');
        a.teclaRevision({ key: '1', metaKey: true, target: { tagName: 'BODY' }, preventDefault() {} });
        const uno = a.etiquetarRevision('true'), dos = a.etiquetarRevision('false');
        await Promise.all([uno, dos]);
        a.dialogo = { titulo: 'x' };
        a.revision.resultado = null;
        a.revision.items = a.revision.items.slice(1);
        a.teclaRevision({ key: '1', target: { tagName: 'BODY' }, preventDefault() {} });
        await new Promise(r => setTimeout(r, 0));
        return llamadas.filter(l => l[1] === 'PUT').map(l => l[2].etiqueta);"""))
    assert r == ["true"], "Cmd+1 no etiqueta, la segunda pulsación no sale y con un diálogo abierto tampoco"
