"""Los avisos de las decisiones 153 a 156 tienen ficha, nombre y ambito en la
interfaz.

`test_catalogo_incidencias.py` solo ve las llamadas a `_add_issue` con un tipo
literal, y `avisos_de_medios.py` emite por `_Grupos.emitir` (a veces con el
tipo en una variable). Lee el fuente a proposito: comprueba que dos ficheros
dicen lo mismo (decision 74).
"""

from __future__ import annotations

import ast
import os
import re

from analysis.avisos_de_medios import TIPOS

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODOS = TIPOS + ("encabezado_salta_nivel",)


def _leer(*ruta: str) -> str:
    with open(os.path.join(RAIZ, *ruta), encoding="utf-8") as f:
        return f.read()


def _bloque(texto: str, inicio: str) -> str:
    i = texto.index(inicio)
    return texto[i:texto.index("\n};", i)]


def test_tipos_es_lo_que_se_emite():
    arbol = ast.parse(_leer("analysis", "avisos_de_medios.py"))
    literales = {n.value for n in ast.walk(arbol)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    emitidos = set()
    for nodo in ast.walk(arbol):
        if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)
                and nodo.func.attr in ("_add_issue", "emitir") and len(nodo.args) > 1
                and isinstance(nodo.args[1], ast.Constant)):
            emitidos.add(nodo.args[1].value)
    assert emitidos <= set(TIPOS)
    # Los que van por variable (AMP y movil) tambien estan escritos en el modulo.
    assert set(TIPOS) <= literales


def test_cada_tipo_tiene_ficha_nombre_y_ambito():
    inc = _leer("frontend", "incidencias.js")
    fichas = _bloque(inc, "const ISSUE_INFO = {")
    nombres = _bloque(inc, "const ISSUE_LABEL_EXTRA = {")
    ambitos = " ".join(l for l in _leer("frontend", "app.js").splitlines() if "ambito: [" in l)
    for t in TODOS:
        assert f"'{t}': {{" in fichas, f"{t} sin ficha en ISSUE_INFO"
        assert re.search(rf"\b{t}\s*:", nombres), f"{t} sin nombre"
        assert f"'{t}'" in ambitos, f"{t} no esta en ningun ambito del Explorador"


def test_los_detalles_tienen_nombre():
    etiquetas = _bloque(_leer("frontend", "incidencias.js"), "const DETALLE_LABEL = {")
    for clave in ("plantilla", "motivo", "salto", "saltos_distintos", "imagen", "ancho",
                  "alto", "valor", "videos", "destino", "destino_status",
                  "destino_canonical", "veredicto"):
        assert re.search(rf"\b{clave}\s*:", etiquetas), clave
