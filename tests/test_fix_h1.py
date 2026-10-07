"""El script que repone el titular en el contenido no puede corromperlo.

Es el unico script que ESCRIBE en `page_content`, asi que un fallo suyo no da
un dato raro: corrompe el contenido guardado de un censo entero. Tres bugs
reproducidos sobre SQLite:

1. Con `--todos`, un solo job sin h1 abortaba la pasada entera (`reparar_job`
   devolvia `0` donde el llamador hace `len(...)`).
2. `_falta_titular` exigia una linea identica, asi que "Hola mundo." frente al
   h1 "Hola mundo" —o el titular partido por un `<br>`— se daba por ausente y
   se anteponia DUPLICADO.
3. Al revertir se quitaba la primera linea de `content_text_original` aunque no
   se hubiera parcheado, borrando un titular legitimo.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.models import Base, Heading, Job, PageContent, Url  # noqa: E402


def _cargar_script():
    """Igual que en `test_check_js_templates.py`: sin dejar rastro en sys.path.

    Los scripts anaden rutas candidatas al arrancar (entre ellas `/app`, que en
    la imagen del crawler tiene una copia del codigo) y el resto de la suite se
    quedaba importando esa copia.
    """
    ruta = os.path.join(RAIZ, "scripts", "fix_h1_en_contenido.py")
    camino_previo = list(sys.path)
    modulos_previos = set(sys.modules)
    spec = importlib.util.spec_from_file_location("fix_h1", ruta)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fix_h1"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = camino_previo
        for nombre in set(sys.modules) - modulos_previos - {"fix_h1"}:
            fichero = getattr(sys.modules[nombre], "__file__", None) or ""
            if fichero and not fichero.startswith(RAIZ):
                del sys.modules[nombre]
    return mod


fix_h1 = _cargar_script()


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture
def sesion():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Heading.__table__, PageContent.__table__])
    return sessionmaker(bind=engine)()


def _job(s, nombre="t"):
    j = Job(id=uuid.uuid4(), name=nombre, status="completed",
            seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()
    return j


def _pagina(s, j, ruta, *, h1=None, texto=None, md=None, texto_orig=None, md_orig=None):
    u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, is_internal=True,
            is_html=True, status_code=200)
    s.add(u)
    s.flush()
    if h1:
        s.add(Heading(url_id=u.id, tag="h1", position=0, text=h1))
    s.add(PageContent(url_id=u.id, content_text=texto,
                      content_length=len(texto or "") or None,
                      content_markdown=md,
                      content_text_original=texto_orig,
                      content_markdown_original=md_orig))
    s.flush()
    return u


def test_un_job_sin_h1_no_tumba_la_pasada(sesion):
    j = _job(sesion)
    _pagina(sesion, j, "/a", texto="Contenido sin titular")
    examinadas, tocadas = fix_h1.reparar_job(sesion, j.id, dry_run=True)
    assert examinadas == 0
    assert len(tocadas) == 0  # era un 0 y revent25aba con TypeError


def test_el_titular_con_punto_final_ya_esta_puesto(sesion):
    """"Hola mundo." frente al h1 "Hola mundo": el mismo titular."""
    assert fix_h1._falta_titular("Hola mundo.\nCuerpo", "Hola mundo") is False
    assert fix_h1._falta_titular("Hola mundo ·\nCuerpo", "Hola mundo") is False
    # Y uno que de verdad falta sigue faltando.
    assert fix_h1._falta_titular("Otro texto\nCuerpo", "Hola mundo") is True


def test_el_titular_partido_por_un_br_ya_esta_puesto(sesion):
    assert fix_h1._falta_titular("Hola\nmundo\nCuerpo", "Hola mundo") is False


def test_el_titular_a_media_frase_no_cuenta(sesion):
    """Decision 10b: como subcadena da falsos positivos."""
    assert fix_h1._falta_titular(
        "Este parrafo menciona Hola mundo a media frase", "Hola mundo") is True


def test_no_se_antepone_un_duplicado(sesion):
    j = _job(sesion)
    u = _pagina(sesion, j, "/a", h1="Centro de XPERIENCIA",
                texto="Centro de XPERIENCIA.\nCuerpo del reportaje")
    _, tocadas = fix_h1.reparar_job(sesion, j.id, dry_run=False)
    assert tocadas == {}
    pc = sesion.query(PageContent).filter(PageContent.url_id == u.id).one()
    assert pc.content_text.count("Centro de XPERIENCIA") == 1


def test_se_repone_el_titular_que_falta(sesion):
    j = _job(sesion)
    u = _pagina(sesion, j, "/a", h1="Centro de XPERIENCIA",
                texto="Cuerpo del reportaje", md="Cuerpo del reportaje")
    _, tocadas = fix_h1.reparar_job(sesion, j.id, dry_run=False)
    assert list(tocadas) == [u.id]
    pc = sesion.query(PageContent).filter(PageContent.url_id == u.id).one()
    assert pc.content_text.splitlines()[0] == "Centro de XPERIENCIA"
    assert pc.content_markdown.startswith("# Centro de XPERIENCIA")


def test_el_markdown_original_no_se_duplica(sesion):
    """La comprobacion estaba solo en content_text_original."""
    j = _job(sesion)
    u = _pagina(sesion, j, "/a", h1="Titular", texto="Cuerpo",
                md="Cuerpo", md_orig="# Titular\n\nCuerpo original")
    fix_h1.reparar_job(sesion, j.id, dry_run=False)
    pc = sesion.query(PageContent).filter(PageContent.url_id == u.id).one()
    assert pc.content_markdown_original.count("Titular") == 1


def test_revertir_no_toca_lo_que_no_parcheamos(sesion):
    """`content_text_original` ya llevaba el titular: revertir lo borraba."""
    j = _job(sesion)
    u = _pagina(sesion, j, "/a", h1="Titular", texto="Cuerpo",
                md="Cuerpo", texto_orig="Titular\nCuerpo original")
    _, tocadas = fix_h1.reparar_job(sesion, j.id, dry_run=False)
    diario = {"job_id": j.id, "paginas": {str(k): v for k, v in tocadas.items()}}
    examinadas, revertidas = fix_h1.revertir_diario(sesion, diario, dry_run=False)
    assert revertidas == 1
    pc = sesion.query(PageContent).filter(PageContent.url_id == u.id).one()
    assert pc.content_text == "Cuerpo"
    # Lo que no se parcheo se queda como estaba.
    assert pc.content_text_original == "Titular\nCuerpo original"
