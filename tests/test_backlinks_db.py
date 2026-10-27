"""El PageRank con backlinks y sus avisos, contra Postgres de verdad (#31,
decision 212). Reutiliza el blog de `test_pagerank_db.py` (301, 404,
canonicalizada, noindex) con un cliente y un CSV de backlinks.

Se salta sin `PAGERANK_TEST_DATABASE_URL`. NUNCA apuntarlo a la BD de trabajo.
"""

from __future__ import annotations

import os
import uuid

import pytest

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL")

pytest.importorskip("numpy")

from sqlalchemy import select  # noqa: E402

from test_pagerank_db import BASE, job, sesion  # noqa: E402,F401

from shared.models import BacklinkImport, Client, ExternalLink, Issue, Job, Url  # noqa: E402


@pytest.fixture()
def con_backlinks(sesion, job):  # noqa: F811
    j, filas = job
    c = Client(id=uuid.uuid4(), nombre="X", slug=f"x-{uuid.uuid4().hex[:8]}", dominios=["x.com"],
               config_defecto={}, integraciones=[])
    sesion.add(c)
    sesion.flush()
    sesion.get(Job, j.id).cliente_id = c.id
    imp = BacklinkImport(cliente_id=c.id, fuente="ahrefs", filas=4)
    sesion.add(imp)
    sesion.flush()
    for destino, aporte in (("https://www.x.com/roto", 8.0), ("https://x.com/viejo", 4.0),
                            ("https://x.com/tag/x", 2.0), ("https://x.com/ya-no-existe", 1.0)):
        sesion.add(ExternalLink(import_id=imp.id, url_to=destino, url_from="https://otro.com/p",
                                dominio_from="otro.com", aportacion=aporte))
    sesion.commit()
    yield j, filas, c
    sesion.delete(sesion.get(Client, c.id))
    sesion.commit()


def test_pagerank_externo_y_avisos(sesion, con_backlinks):
    from analysis import avisos_de_backlinks
    from analysis.analyzer import SEOAnalyzer

    j, filas, _ = con_backlinks
    interno_antes = None
    an = SEOAnalyzer(sesion, str(j.id))
    an.compute_pagerank()
    avisos_de_backlinks.avisar(an)
    an._flush_issues()
    sesion.commit()

    resumen = sesion.get(Job, j.id).pagerank_resumen
    ext = resumen["externo"]
    assert (ext["backlinks"], ext["casados"], ext["parte_casada"]) == (4, 3, 0.75)
    assert ext["no_rastreadas"][0]["url"] == "https://x.com/ya-no-existe"
    assert ext["entra_en"]["error"] == pytest.approx(8 / 14, abs=1e-3)
    # Lo que llega al 404 se pierde: el desperdiciado del externo pasa al del interno
    assert ext["desperdiciado"] > resumen["desperdiciado"]

    def fila(ruta):
        return sesion.execute(select(Url.pagerank_score, Url.pagerank_externo_score, Url.backlinks_externos,
                                     Url.autoridad_externa).where(Url.id == filas[ruta].id)).one()

    # La 301 lleva su autoridad a /blog/a2
    assert fila("/blog/a2")[1] >= fila("/blog/a2")[0]
    assert fila("/roto")[2:] == (1, 8.0)
    assert fila("/blog/a3")[2] == 0

    tipos = {(t, u) for t, u in sesion.execute(
        select(Issue.issue_type, Issue.url_id).where(Issue.job_id == j.id, Issue.issue_type.like("%backlinks%")))}
    assert ("backlinks_a_error", filas["/roto"].id) in tipos
    assert ("backlinks_a_noindex", filas["/tag/x"].id) in tipos
    assert not any(u == filas["/viejo"].id for _, u in tipos), "una 301 con backlinks no se avisa"
    del interno_antes


def test_sin_importacion_las_columnas_quedan_a_null(sesion, con_backlinks):
    from analysis.analyzer import SEOAnalyzer

    j, filas, c = con_backlinks
    SEOAnalyzer(sesion, str(j.id)).compute_pagerank()
    sesion.commit()
    assert sesion.get(Url, filas["/roto"].id).backlinks_externos == 1
    sesion.query(BacklinkImport).filter(BacklinkImport.cliente_id == c.id).delete()
    sesion.commit()
    SEOAnalyzer(sesion, str(j.id)).compute_pagerank()
    sesion.commit()
    sesion.expire_all()
    assert sesion.get(Url, filas["/roto"].id).backlinks_externos is None
    assert "externo" not in (sesion.get(Job, j.id).pagerank_resumen or {})
