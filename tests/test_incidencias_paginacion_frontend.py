"""Los avisos de paginacion (decision 122) tienen ficha, nombre y ambito.

Sin nombre, Problemas enseña «paginacion canonical a la primera»; fuera del
ambito «Paginación» no salen en esa pestaña del explorador. Lee el fuente a
proposito: lo que se comprueba es que dos ficheros digan lo mismo (74).
"""

from __future__ import annotations

import os
import re

from analysis.avisos_de_paginacion import TIPOS

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _leer(nombre: str) -> str:
    with open(os.path.join(RAIZ, "frontend", nombre), encoding="utf-8") as f:
        return f.read()


def _bloque(texto: str, inicio: str) -> str:
    i = texto.index(inicio)
    return texto[i:texto.index("\n};", i)]


def test_cada_tipo_tiene_ficha_y_nombre():
    inc = _leer("incidencias.js")
    fichas = _bloque(inc, "const ISSUE_INFO = {")
    nombres = _bloque(inc, "const ISSUE_LABEL_EXTRA = {")
    for t in TIPOS:
        assert f"'{t}':" in fichas, f"{t} sin ficha en ISSUE_INFO"
        assert re.search(rf"\b{t}\s*:", nombres), f"{t} sin nombre"


def test_salen_en_el_ambito_paginacion_con_sus_columnas():
    app = _leer("app.js")
    lineas = app.splitlines()
    i = next(n for n, l in enumerate(lineas) if "key: 'a_paginacion'" in l)
    for t in TIPOS:
        assert f"'{t}'" in lineas[i], f"{t} no esta en el ambito Paginación"
    for col in ("rel_next", "rel_prev"):
        assert f"'{col}'" in lineas[i + 1]
        assert re.search(rf"^\s+{col}:\s+\{{ label:", app, re.M), f"columna {col} sin definir"
    # La que sustituye a `orphan_page` sale tambien donde se busca la huerfana.
    enlazado = next(l for l in lineas if "key: 'a_enlazado'" in l)
    assert "'solo_enlazada_desde_paginacion_excluida'" in enlazado


def test_los_detalles_tienen_nombre():
    etiquetas = _bloque(_leer("incidencias.js"), "const DETALLE_LABEL = {")
    for clave in ("plantilla", "series", "motivos", "ejemplos_origen", "siguiente"):
        assert re.search(rf"\b{clave}\s*:", etiquetas), clave
