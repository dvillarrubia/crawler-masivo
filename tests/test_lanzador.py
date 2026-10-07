"""El lanzador avisa de lo que la API ha tirado de la configuracion.

Pydantic v2 descarta en silencio lo que `JobConfig` no declara (decision 33):
el formulario mandaba `use_sitemap`, el schema no lo declaraba, y desmarcar la
casilla no tenia ningun efecto. El rastreo sale con otra configuracion y el
informe se lee como si fuera la pedida. `test_jobconfig_claves.py` cubre las
claves que lee el spider; esto cubre las que escribe una persona.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent
_ESQUEMA = RAIZ / "api" / "schemas.py"


def _cargar():
    ruta = str(RAIZ / "scripts" / "lanzar_job.py")
    spec = importlib.util.spec_from_file_location("lanzador", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lz = _cargar()


def test_claves_anidadas():
    assert lz._claves({"a": 1, "b": {"c": 2, "d": {"e": 3}}}) == {"a", "b.c", "b.d.e"}


def test_un_dict_vacio_es_una_clave_hoja():
    """`http.cookies: {}` se envia y se guarda; no debe contarse como ausente."""
    assert lz._claves({"http": {"cookies": {}}}) == {"http.cookies"}


def test_lo_que_se_guarda_no_se_avisa():
    cfg = {"max_urls": 100, "crawl_behavior": {"request_delay": 0.5}}
    assert lz._lo_que_la_api_no_guardo(cfg, cfg) == []


def test_una_clave_de_primer_nivel_descartada():
    enviado = {"max_urls": 100, "use_sitemap": False}
    guardado = {"max_urls": 100}
    assert lz._lo_que_la_api_no_guardo(enviado, guardado) == ["use_sitemap"]


def test_una_clave_anidada_descartada():
    enviado = {"crawl_behavior": {"request_delay": 0.5, "render_wait_mss": 3000}}
    guardado = {"crawl_behavior": {"request_delay": 0.5}}
    assert lz._lo_que_la_api_no_guardo(enviado, guardado) == [
        "crawl_behavior.render_wait_mss"
    ]


def test_lo_que_la_api_anade_por_defecto_no_es_un_aviso():
    """La API rellena sus defaults: eso sobra en el guardado, no falta."""
    enviado = {"max_urls": 100}
    guardado = {"max_urls": 100, "max_depth": 3, "render_js": False}
    assert lz._lo_que_la_api_no_guardo(enviado, guardado) == []


def _modelos_de_schemas() -> dict[str, dict[str, str]]:
    """{clase: {campo: anotacion}} leyendo `api/schemas.py` con el AST.

    Se lee el fuente en vez de importar, por lo mismo que
    `test_jobconfig_claves.py`: ninguna de las dos imagenes tiene `api/` y
    `crawler/` juntos, y asi el test corre en las dos y en local.
    """
    arbol = ast.parse(_ESQUEMA.read_text(encoding="utf-8"))
    modelos: dict[str, dict[str, str]] = {}
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.ClassDef):
            continue
        campos: dict[str, str] = {}
        for hijo in nodo.body:
            if isinstance(hijo, ast.AnnAssign) and isinstance(hijo.target, ast.Name):
                campos[hijo.target.id] = ast.unparse(hijo.annotation)
        modelos[nodo.name] = campos
    return modelos


def _rutas_declaradas() -> tuple[set[str], set[str]]:
    """Rutas de clave que `JobConfig` admite, y las que son opacas.

    Opaca = su contenido es libre (`dict[str, str]`, `Any`), asi que cualquier
    clave por debajo vale: `http.custom_headers.X-Mi-Cabecera`.
    """
    modelos = _modelos_de_schemas()
    rutas: set[str] = set()
    opacas: set[str] = set()

    def expandir(clase: str, prefijo: str = "") -> None:
        for campo, anotacion in modelos.get(clase, {}).items():
            ruta = f"{prefijo}{campo}"
            rutas.add(ruta)
            nombres = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", anotacion)
            anidada = next((n for n in nombres if n in modelos and n != clase), None)
            if "dict" in anotacion or "Any" in anotacion:
                opacas.add(ruta)
            if anidada and "list" not in anotacion:
                expandir(anidada, ruta + ".")

    expandir("JobConfig")
    assert rutas, "no se pudo leer JobConfig"
    return rutas, opacas


def test_los_configs_de_cliente_solo_usan_claves_que_la_api_declara():
    """El aviso del lanzador tiene que ser un cable trampa, no un lobo que
    grita: con los configs de hoy no salta ninguno. Y si manana alguien escribe
    `render_wait_mss`, lo canta esto en el PR y no un informe raro semanas
    despues."""
    rutas, opacas = _rutas_declaradas()
    directorio = RAIZ / "projects"
    clientes = [c.name for c in sorted(directorio.iterdir())
                if (c / "config.json").is_file()]
    assert clientes, "no hay configs de cliente que comprobar"
    sospechosas: list[str] = []
    for cliente in clientes:
        cfg = json.loads((directorio / cliente / "config.json").read_text(encoding="utf-8"))
        for k in [k for k in cfg if k.startswith("_")]:
            cfg.pop(k)
        for ruta in sorted(lz._claves(cfg)):
            if ruta in rutas:
                continue
            if any(ruta.startswith(o + ".") for o in opacas):
                continue
            sospechosas.append(f"{cliente}: {ruta}")
    assert not sospechosas, (
        "claves que la API tirara en silencio (declararlas en JobConfig o "
        f"corregir el config): {sospechosas}"
    )
