"""Pestana IA (revision de UX del 25-oct, 2.9), ejecutando `frontend/app.js`
en Chromium con la API simulada (ninguna llamada sale de la pagina).

- La pestana tiene su tramo (`#/rastreos/<id>/ia`) y el viejo `enriquecido`
  sigue abriendola.
- El lanzador avisa si ya se pregunto lo mismo en este censo, y con que
  alcance; una ejecucion fallida no cuenta.
- La linea «Revisado con IA» de Problemas: en la canibalizacion cuenta los
  pares de la ejecucion; en el soft 404 cuenta SOLO las paginas del problema
  (el modulo pregunta tambien a paginas con poco texto que no lo tienen), y si
  una de las dos listas no se ha leido entera no dice nada.
- La clave de autorizacion y el gasto «de todos los clientes» en los mensajes.
- Las parejas pregunta -> problema existen de verdad en el catalogo y en el
  analizador: una errata dejaria la linea muda sin que nadie lo notase.
- Cada modulo del catalogo tiene su explicacion en llano en el lanzador, y la
  etiqueta ya no dice «solo a las sospechosas» por el titulo del catalogo
  (10 de los 11 modulos eligen sus paginas, no solo los «candidatos»).

Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def test_las_parejas_pregunta_problema_existen():
    fuente = (RAIZ / "frontend" / "app.js").read_text(encoding="utf-8")
    bloque = fuente.split("const PROBLEMA_PREGUNTA_IA = {", 1)[1].split("\n};", 1)[0]
    parejas = re.findall(r"^\s*(\w+): \{ pregunta: '(\w+)'", bloque, re.M)
    assert dict(parejas) == {"canibalizacion_por_consulta": "paginas_sustitutas", "soft_404_candidato": "es_soft_404"}
    from shared.preguntas import CATALOGO

    analizador = "\n".join(p.read_text(encoding="utf-8") for p in (RAIZ / "analysis").glob("*.py"))
    for tipo, pregunta in parejas:
        assert pregunta in CATALOGO, pregunta
        assert f'"{tipo}"' in analizador, tipo


def test_cada_modulo_tiene_su_explicacion():
    fuente = (RAIZ / "frontend" / "app.js").read_text(encoding="utf-8")
    bloque = fuente.split("const EXPLICACION_MODULO_IA = {", 1)[1].split("\n};", 1)[0]
    claves = set(re.findall(r"^\s*(\w+): '", bloque, re.M))
    from shared.enriquecido import describir_modulos

    assert claves == {m["nombre"] for m in describir_modulos()}


pytest.importorskip("playwright")

from test_evolucion_frontend import _evaluar  # noqa: E402

RUNS = [
    {"id": "r3", "estado": "fallida", "modulos": ["soft_404"], "alcance": {}, "created_at": "2026-10-25T10:00:00Z"},
    {"id": "r2", "estado": "completada", "modulos": ["canibalizacion"], "alcance": {},
     "created_at": "2026-10-24T10:00:00Z"},
    {"id": "r1", "estado": "completada", "modulos": ["soft_404"], "alcance": {"carpeta": "/es/"},
     "created_at": "2026-10-23T10:00:00Z"},
]
CATALOGO = [
    {"nombre": "soft_404", "titulo": "Soft 404 (candidatos)", "preguntas": ["es_soft_404"], "depende_de": []},
    {"nombre": "canibalizacion", "titulo": "Canibalizacion por busqueda (juicio)",
     "preguntas": ["paginas_sustitutas", "recomendacion_canibalizacion"], "depende_de": []},
    {"nombre": "title_generico", "titulo": "Title generico", "preguntas": ["title_es_generico"], "depende_de": []},
]
# La API simulada: el soft 404 pregunto a 3 URLs, de las que solo /a y /b
# tienen el aviso soft_404_candidato (/c es de poco texto).
API = {
    "/jobs/j/enrichment/r2": {"id": "r2", "resultados": {"paginas_sustitutas": {
        "total": 10, "por_respuesta": {"true": 4, "false": 5}, "por_estado": {"incierto": 10}}}},
    "/jobs/j/issues?issue_type=soft_404_candidato&page=1&page_size=200": {
        "total": 2, "items": [{"url": "https://x/a"}, {"url": "https://x/b"}]},
    "/jobs/j/enrichment/r1/decisions": {"total": 3, "items": [
        {"url": "https://x/a", "respuesta": "true", "estado": "incierto"},
        {"url": "https://x/b", "respuesta": "false", "estado": "incierto"},
        {"url": "https://x/c", "respuesta": "true", "estado": "incierto"}]},
}


def _app(extra: str = "") -> str:
    return """async () => {
        const datos = %s;
        window.api = async (p) => {
            const k = p.startsWith('/jobs/j/enrichment/r1/decisions') ? '/jobs/j/enrichment/r1/decisions' : p;
            if (!(k in datos)) throw new Error('no simulado: ' + p);
            return datos[k];
        };
        const a = app();
        a.showToast = () => {};
        a.job = { id: 'j', status: 'completed' };
        a.enriqRuns = %s;
        a.enriqCatalogo = %s;
        a.issuesResumen = [
            { issue_type: 'canibalizacion_por_consulta', severity: 'warning' },
            { issue_type: 'canibalizacion_por_consulta', severity: 'info' },
            { issue_type: 'soft_404_candidato', severity: 'info' },
            { issue_type: '4xx_error', severity: 'error' }];
        %s
    }""" % (json.dumps(API), json.dumps(RUNS), json.dumps(CATALOGO), extra)


def test_rutas_de_la_pestana_ia():
    r = _evaluar("() => [TAB_SLUG.enriquecido, SLUG_TAB.ia, SLUG_TAB.enriquecido]")
    assert r == ["ia", "enriquecido", "enriquecido"]


def test_linea_revisado_con_ia_en_problemas():
    r = _evaluar(_app("""
        await a.cargarIAProblemas();
        return a.issuesResumen.map(f => (a.lineaIAProblema(f) || {}).texto || null);"""))
    canib_w, canib_i, soft, otro = r
    # Pares de la canibalizacion: los de la ejecucion, en la primera fila del tipo.
    assert canib_w.startswith("Revisado con IA el ") and "10 pares" in canib_w
    assert "compiten por la misma búsqueda en 4" in canib_w and "no compiten en 5" in canib_w
    assert "sin respuesta clara en 1" in canib_w and canib_w.endswith("· orientativo")
    assert canib_i is None
    # Soft 404: solo /a y /b, las del problema; /c no cuenta.
    assert "2 páginas de este problema" in soft
    assert "sí es soft 404 en 1" in soft and "no lo es en 1" in soft
    # La fallida mas reciente no cuenta: la linea sale de r1, del 23 oct.
    assert "23 oct" in soft
    assert otro is None


def test_sin_lista_completa_no_se_afirma_nada():
    r = _evaluar(_app("""
        window.api = async (p) => {
            if (p.startsWith('/jobs/j/issues')) return { total: 500, items: [{ url: 'https://x/a' }] };
            if (p.includes('/decisions')) return { total: 1, items: [{ url: 'https://x/a', respuesta: 'true', estado: 'incierto' }] };
            if (p === '/jobs/j/enrichment/r2') throw new Error('caido');
            throw new Error(p);
        };
        await a.cargarIAProblemas();
        a.issuesSegmento = '';
        return a.issuesResumen.map(f => a.lineaIAProblema(f));"""))
    assert r == [None, None, None, None]


def test_con_segmento_la_linea_no_se_mezcla():
    r = _evaluar(_app("""
        await a.cargarIAProblemas();
        a.issuesSegmento = '/es/';
        return a.lineaIAProblema(a.issuesResumen[0]);"""))
    assert r is None


def test_aviso_de_consulta_repetida():
    r = _evaluar(_app("""
        const out = {};
        a.enriqForm.modulos = ['soft_404']; a.enriqForm.tipo = 'todas';
        const x = a.ejecucionRepetidaEnriq(); out.soft = [x.run.id, x.mismoAlcance];
        a.enriqForm.tipo = 'carpeta'; a.enriqForm.carpeta = '/es/';
        out.carpeta = a.ejecucionRepetidaEnriq().mismoAlcance;
        a.enriqForm.modulos = ['title_generico']; a.enriqForm.tipo = 'todas';
        out.nueva = a.ejecucionRepetidaEnriq();
        return out;"""))
    # r3 (fallida) no cuenta: el aviso apunta a r1, con otro alcance.
    assert r["soft"] == ["r1", False]
    assert r["carpeta"] is True
    assert r["nueva"] is None


def test_textos_de_clave_y_gasto():
    r = _evaluar(_app("""
        return [a.textoErrorEnriq('Token de analisis enriquecido incorrecto o ausente.'),
                a.textoErrorEnriq('El lanzamiento esta desactivado: falta ENRICHMENT_TOKEN en el entorno de la API.'),
                a.textoErrorEnriq('Tope mensual de la instancia agotado: 5 $ de 5 $.'),
                fmt.usd(0.0573), fmt.usd(0.00012), fmt.alcanceEnriq({}), a.tituloModuloEnriq('canibalizacion')];"""))
    clave, sin_clave, tope, usd, usd_peq, alcance, titulo = r
    assert "clave de autorización no es correcta" in clave and "Token" not in clave
    assert "ENRICHMENT_TOKEN" not in sin_clave and "clave de autorización" in sin_clave
    assert "de todos los clientes" in tope and "instancia" not in tope
    assert usd == "0,06 $" and usd_peq == "0,00012 $"
    assert alcance == "las candidatas de cada módulo"
    assert titulo == "Canibalización por búsqueda"


def test_explicacion_y_etiqueta_del_lanzador():
    r = _evaluar(_app("""
        return [a.etiquetaModuloEnriq(a.enriqCatalogo[0]), a.explicacionModuloEnriq(a.enriqCatalogo[2]),
                a.explicacionModuloEnriq({ nombre: 'nuevo', descripcion: 'Algo (decision 200). Sin evaluar (#8).' })];"""))
    etiqueta, explicacion, nuevo = r
    assert etiqueta == "Soft 404"
    assert "Solo pregunta por titles muy cortos" in explicacion
    assert nuevo == "Algo. Sin calibrar."
