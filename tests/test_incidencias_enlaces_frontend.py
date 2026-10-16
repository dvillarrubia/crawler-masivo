"""Los avisos de enlaces (decision 106) tienen nombre en la interfaz.

Sin etiqueta, `fmt.issue` cae a `t.replaceAll('_', ' ')` y Problemas enseñaba
«enlace interno a redireccion» (sin tilde) donde todos los demas tipos llevan
su nombre en castellano; y fuera del ambito «Enlazado» no salen en esa pestaña.
"""

from __future__ import annotations

import os
import re

from analysis.avisos_de_enlaces import TIPOS

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _leer(nombre: str) -> str:
    with open(os.path.join(RAIZ, "frontend", nombre), encoding="utf-8") as f:
        return f.read()


def _bloque(texto: str, inicio: str) -> str:
    i = texto.index(inicio)
    return texto[i:texto.index("\n};", i)]


def test_cada_tipo_tiene_ficha_y_nombre():
    inc = _leer("incidencias.js")
    app = _leer("app.js")
    fichas = _bloque(inc, "const ISSUE_INFO = {")
    nombres = _bloque(inc, "const ISSUE_LABEL_EXTRA = {") + _bloque(app, "const ISSUE_LABEL = {")
    for t in TIPOS:
        assert f"'{t}':" in fichas, f"{t} sin ficha en ISSUE_INFO"
        assert re.search(rf"['\s]{t}'?\s*:", nombres), f"{t} sin nombre"


def test_salen_en_el_ambito_enlazado():
    app = _leer("app.js")
    linea = next(l for l in app.splitlines() if "key: 'a_enlazado'" in l)
    for t in TIPOS:
        assert f"'{t}'" in linea, f"{t} no esta en el ambito Enlazado"


def test_los_detalles_tienen_nombre():
    inc = _leer("incidencias.js")
    etiquetas = _bloque(inc, "const DETALLE_LABEL = {")
    for clave in ("paginas_que_enlazan", "con_destino_interno", "destinos_fuera_del_censo",
                  "destinos_internos_distintos", "apariciones"):
        assert re.search(rf"\b{clave}\s*:", etiquetas), clave
