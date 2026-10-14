"""scripts/compactar_enlaces.py contra Postgres de verdad (decision 94).

Lo que tiene que garantizar: que un censo viejo compactado da EXACTAMENTE los
mismos conteos que sin compactar, que sin `--aplicar` no escribe nada y que no
toca un job en marcha. Necesita Postgres (`SET LOCAL`, temporal `ON COMMIT
DROP`); se salta sin PAGERANK_TEST_DATABASE_URL, como test_pagerank_db.py.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL")

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from analysis.analyzer import SEOAnalyzer  # noqa: E402
from shared.models import Base, Job, Link, Url  # noqa: E402

_RUTA = Path(__file__).resolve().parent.parent / "scripts" / "compactar_enlaces.py"
_spec = importlib.util.spec_from_file_location("compactar_enlaces", _RUTA)
compactar_enlaces = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compactar_enlaces)


@pytest.fixture()
def sesion():
    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


def _censo_viejo(s, estado="completed"):
    """Una fila por aparicion, como guardaba el spider antes de la decision 94:
    el menu (escritorio + movil) en las 3 paginas, y un enlace de cuerpo."""
    j = Job(id=uuid.uuid4(), name="test compactar", status=estado,
            seeds=["https://x.com/inicio"], config={})
    s.add(j)
    s.flush()
    urls = []
    for ruta in ("/inicio", "/a", "/b"):
        u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=f"{j.id}{ruta}",
                is_internal=True, is_html=True, status_code=200)
        s.add(u)
        urls.append(u)
    s.flush()
    for origen in urls:
        for destino in urls:
            for _ in range(2):  # menu de escritorio y de movil
                s.add(Link(job_id=j.id, from_url_id=origen.id, to_url=destino.url,
                           to_url_hash=destino.url_hash, anchor_text=destino.url[-2:],
                           is_internal=True, link_position="nav", follow=True))
    s.add(Link(job_id=j.id, from_url_id=urls[0].id, to_url=urls[1].url,
               to_url_hash=urls[1].url_hash, anchor_text="leer mas",
               is_internal=True, link_position="content", follow=True))
    s.commit()
    return j, urls


def _filas(s, j):
    return s.execute(select(func.count()).select_from(Link).where(Link.job_id == j.id)).scalar()


def _conteos(s, j, urls):
    SEOAnalyzer(s, j.id).compute_link_counts()
    s.commit()
    for u in urls:
        s.refresh(u)
    return [(u.inlinks_count, u.unique_inlinks_count, u.outlinks_count) for u in urls]


def test_sin_aplicar_no_escribe(sesion):
    j, _ = _censo_viejo(sesion)
    assert compactar_enlaces.compactar(sesion, str(j.id), aplicar=False) == (19, 10)
    assert _filas(sesion, j) == 19


def test_compactar_no_cambia_ningun_conteo(sesion):
    j, urls = _censo_viejo(sesion)
    antes = _conteos(sesion, j, urls)
    primer_id = sesion.execute(
        select(func.min(Link.id)).where(Link.job_id == j.id)
    ).scalar()

    assert compactar_enlaces.compactar(sesion, str(j.id), aplicar=True) == (19, 10)
    assert _filas(sesion, j) == 10
    assert sesion.execute(
        select(func.sum(Link.repeticiones)).where(Link.job_id == j.id)
    ).scalar() == 19
    # El orden de las listas (por id) se conserva: cada grupo guarda su id mas bajo
    assert sesion.execute(
        select(func.min(Link.id)).where(Link.job_id == j.id)
    ).scalar() == primer_id
    assert _conteos(sesion, j, urls) == antes

    # Pasarlo otra vez no cambia nada
    assert compactar_enlaces.compactar(sesion, str(j.id), aplicar=True) == (10, 10)


def test_no_toca_un_job_en_marcha(sesion):
    j, _ = _censo_viejo(sesion, estado="analyzing")
    with pytest.raises(RuntimeError, match="analyzing"):
        compactar_enlaces.compactar(sesion, str(j.id), aplicar=True)
    sesion.rollback()
    assert _filas(sesion, j) == 19
