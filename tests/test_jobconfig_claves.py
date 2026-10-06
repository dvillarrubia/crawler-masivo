"""Toda clave que el spider lee de `job.config` tiene que sobrevivir a JobConfig.

Pydantic v2 descarta lo que no esta declarado, en silencio. Paso con
`use_sitemap`: el formulario lo enviaba, `JobConfig` no lo declaraba, y el
spider leia `job_config.get("use_sitemap", True)` — asi que desmarcar la
casilla no tenia NINGUN efecto y nadie se enteraba. Le pasara a cualquier clave
nueva (`taxonomia` de #12, `decisions` de #7, `external_links` de #31) si se
anade al spider y no al schema.

Este test se mantiene solo: lee las claves del codigo del spider.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# Necesita los DOS arboles a la vez, y ninguna de las imagenes los tiene: la
# del crawler no lleva `api/`, la de la API no lleva `crawler/`. En la CI y en
# local estan los dos (es un checkout completo), que es donde tiene que correr.
_ESQUEMA = RAIZ / "api" / "schemas.py"
_SPIDER = RAIZ / "crawler"
pytestmark = pytest.mark.skipif(
    not (_ESQUEMA.is_file() and _SPIDER.is_dir()),
    reason="hacen falta api/ y crawler/ juntos (imagen parcial)",
)

# Se lee el FUENTE en vez de importar `api.schemas` a proposito: este test
# necesita los dos arboles (el schema y el spider) y ninguna de las dos
# imagenes los tiene juntos —la del crawler no lleva `api/`, la de la API no
# lleva `crawler/`—. Leyendo el fuente corre en las dos y en local.

# Claves que el spider resuelve por su cuenta y no vienen del config del job.
NO_SON_DEL_CONFIG = {"crawl_behavior", "extraction", "http", "resource_types"}


def _claves_declaradas_en_jobconfig() -> set[str]:
    arbol = ast.parse((RAIZ / "api" / "schemas.py").read_text(encoding="utf-8"))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ClassDef) and nodo.name == "JobConfig":
            return {
                hijo.target.id
                for hijo in nodo.body
                if isinstance(hijo, ast.AnnAssign) and isinstance(hijo.target, ast.Name)
            }
    raise AssertionError("no se encontro la clase JobConfig en api/schemas.py")


def _claves_que_lee_el_spider() -> set[str]:
    claves: set[str] = set()
    for ruta in (RAIZ / "crawler").rglob("*.py"):
        texto = ruta.read_text(encoding="utf-8")
        # job_config.get("x"), self.job_config.get('x'), cfg.get("x", ...)
        for m in re.finditer(r'job_config\.get\(\s*["\']([a-z_]+)["\']', texto):
            claves.add(m.group(1))
    return claves - NO_SON_DEL_CONFIG


def test_el_spider_no_lee_claves_que_pydantic_tira():
    declaradas = _claves_declaradas_en_jobconfig()
    leidas = _claves_que_lee_el_spider()
    assert leidas, "el test no encontro ninguna clave: ha cambiado la forma de leerlas"
    sin_declarar = sorted(leidas - declaradas)
    assert sin_declarar == [], (
        "el spider lee estas claves y JobConfig las descarta en silencio, "
        f"asi que no tienen efecto: {sin_declarar}"
    )


def test_use_sitemap_esta_declarado():
    """El caso concreto de #37: el formulario lo enviaba y el schema lo tiraba."""
    assert "use_sitemap" in _claves_declaradas_en_jobconfig()


def test_el_test_encuentra_de_verdad_las_claves_del_spider():
    """Control: si cambia la forma de leer el config, este test tiene que
    enterarse en vez de pasar en vacio."""
    leidas = _claves_que_lee_el_spider()
    assert "use_sitemap" in leidas
    assert len(leidas) >= 5, leidas
