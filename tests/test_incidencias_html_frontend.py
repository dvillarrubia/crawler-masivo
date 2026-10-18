"""Los avisos de la decision 108 tienen ficha, nombre y ambito en la interfaz.

`test_catalogo_incidencias.py` solo lee `analysis/analyzer.py`, y estos se
emiten desde `analysis/avisos_de_html.py`: sin este test, un tipo nuevo sin
ficha saldria con su nombre tecnico en la reunion con el cliente. Lee el
fuente a proposito: comprueba que dos ficheros dicen lo mismo (decision 74).
"""

from __future__ import annotations

import ast
import os
import re

from analysis.avisos_de_html import TIPOS

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _leer(*ruta: str) -> str:
    with open(os.path.join(RAIZ, *ruta), encoding="utf-8") as f:
        return f.read()


def _bloque(texto: str, inicio: str) -> str:
    i = texto.index(inicio)
    return texto[i:texto.index("\n};", i)]


def _tipos_emitidos() -> set[str]:
    """Los literales que llegan a `_add_issue` o a `_Grupos.emitir`."""
    arbol = ast.parse(_leer("analysis", "avisos_de_html.py"))
    tipos = set()
    for nodo in ast.walk(arbol):
        if (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)
                and nodo.func.attr in ("_add_issue", "emitir") and len(nodo.args) > 1
                and isinstance(nodo.args[1], ast.Constant)):
            tipos.add(nodo.args[1].value)
    return tipos


def test_tipos_es_lo_que_se_emite():
    assert _tipos_emitidos() == set(TIPOS)


def test_cada_tipo_tiene_ficha_y_nombre():
    inc = _leer("frontend", "incidencias.js")
    fichas = _bloque(inc, "const ISSUE_INFO = {")
    nombres = _bloque(inc, "const ISSUE_LABEL_EXTRA = {")
    for t in TIPOS:
        assert f"'{t}': {{" in fichas, f"{t} sin ficha en ISSUE_INFO"
        assert re.search(rf"\b{t}\s*:", nombres), f"{t} sin nombre"


def test_cada_tipo_esta_en_un_ambito():
    app = _leer("frontend", "app.js")
    ambitos = " ".join(l for l in app.splitlines() if "ambito: [" in l)
    for t in TIPOS:
        assert f"'{t}'" in ambitos, f"{t} no esta en ningun ambito del Explorador"


def test_los_detalles_tienen_nombre():
    etiquetas = _bloque(_leer("frontend", "incidencias.js"), "const DETALLE_LABEL = {")
    for clave in ("plantilla", "motivo", "forma", "charset_cabecera", "charset_meta",
                  "charset_bom", "manda", "apunta_a_si_misma", "html_lang", "hreflang",
                  "destino_status", "destino_no_comprobado", "con_varios_viewports",
                  "atributo_vacio", "rastreo_con_render"):
        assert re.search(rf"\b{clave}\s*:", etiquetas), clave
