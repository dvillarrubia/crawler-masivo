"""Los logs del servidor cruzados con un censo, contra Postgres de verdad
(#35, decision 213). Reutiliza el blog de `test_pagerank_db.py`.

Se salta sin `PAGERANK_TEST_DATABASE_URL`. NUNCA apuntarlo a la BD de trabajo.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL")

pytest.importorskip("numpy")

from sqlalchemy import select  # noqa: E402

from test_pagerank_db import BASE, job, sesion  # noqa: E402,F401

from shared.models import Client, Issue, Job, LogImport, LogUrl, Url  # noqa: E402

FIN = datetime(2026, 10, 25, tzinfo=timezone.utc)


def _con_logs(sesion, j, dias=14):
    c = Client(id=uuid.uuid4(), nombre="X", slug=f"x-{uuid.uuid4().hex[:8]}", dominios=["x.com"],
               config_defecto={}, integraciones=[])
    sesion.add(c)
    sesion.flush()
    sesion.get(Job, j.id).cliente_id = c.id
    imp = LogImport(cliente_id=c.id, host="x.com", estado="hecho", desde=FIN - timedelta(days=dias), hasta=FIN,
                    bots={"gptbot": {"visitas": 3, "verificadas": 3, "falsas": 0, "sin_comprobar": 0}})
    sesion.add(imp)
    sesion.flush()

    def fila(ruta, visitas, verificado=True, codigos=None, bot="googlebot"):
        sesion.add(LogUrl(import_id=imp.id, ruta=ruta, clave="x.com" + (ruta.rstrip("/") or "/"), bot=bot,
                          verificado=verificado, visitas=visitas, codigos=codigos or {"200": visitas},
                          movil=visitas, primero=FIN - timedelta(days=3), ultimo=FIN))

    fila("/", 400)
    fila("/blog/a1", 5)
    fila("/blog/a3", 4, codigos={"503": 3, "200": 1})   # 200 en el rastreo, errores a Googlebot
    fila("/roto", 2, codigos={"404": 2})
    fila("/", 9, verificado=False)                       # un falso Googlebot
    fila("/no-rastreada", 6, codigos={"404": 6})
    fila("/img/a.jpg", 3, bot="googlebot-imagen")
    sesion.commit()
    return c


@pytest.fixture()
def limpiar(sesion):  # noqa: F811
    creados = []
    yield creados
    for c in creados:
        sesion.delete(sesion.get(Client, c.id))
    sesion.commit()


def _analizar(sesion, j):  # noqa: F811
    from analysis import avisos_de_logs
    from analysis.analyzer import SEOAnalyzer

    an = SEOAnalyzer(sesion, str(j.id))
    an.compute_pagerank()
    avisos_de_logs.avisar(an)
    an._flush_issues()
    sesion.commit()


def test_visitas_resumen_y_avisos(sesion, job, limpiar):  # noqa: F811
    j, filas = job
    limpiar.append(_con_logs(sesion, j))
    _analizar(sesion, j)

    r = sesion.get(Job, j.id).logs_resumen
    assert (r["visitas"], r["falsas"], r["dias"], r["afirmable_ausencia"]) == (417, 9, 14.0, True)
    assert r["reparto"]["no_en_censo"] == 6 and r["reparto"]["errores"] == 2
    assert r["no_en_censo"][0]["ruta"] == "/no-rastreada"
    assert r["otros_bots"]["gptbot"]["visitas"] == 3

    v = dict(sesion.execute(select(Url.id, Url.googlebot_visitas).where(Url.job_id == j.id)).all())
    assert v[filas["/"].id] == 400, "el falso no cuenta"
    assert v[filas["/contacto"].id] == 0, "0 = el log no la vio; NULL seria sin logs"

    avisos = list(sesion.execute(
        select(Issue.issue_type, Issue.url_id, Issue.details, Issue.group_key).where(
            Issue.job_id == j.id, Issue.issue_type.in_(("indexable_sin_visitas_googlebot", "googlebot_recibe_error")))))
    err = [a for a in avisos if a[0] == "googlebot_recibe_error"]
    assert len(err) == 1 and err[0][1] == filas["/blog/a3"].id and err[0][2]["paginas_afectadas"] == 1
    sin = [a for a in avisos if a[0] == "indexable_sin_visitas_googlebot"]
    assert sin and all(a[3] for a in sin), "agrupados por plantilla, con su clave"
    assert not any(filas["/"].id == a[1] for a in sin)


def test_con_pocos_dias_no_se_afirma_que_no_la_visite(sesion, job, limpiar):  # noqa: F811
    j, _ = job
    limpiar.append(_con_logs(sesion, j, dias=3))
    _analizar(sesion, j)
    assert sesion.get(Job, j.id).logs_resumen["afirmable_ausencia"] is False
    n = sesion.execute(select(Issue.id).where(Issue.job_id == j.id,
                                              Issue.issue_type == "indexable_sin_visitas_googlebot")).all()
    assert n == []


def test_un_log_que_apenas_ve_a_googlebot_no_afirma_ausencias(sesion, job, limpiar):  # noqa: F811
    """Detras de una CDN todo sale como falso: el problema es el log, no el sitio."""
    j, _ = job
    c = _con_logs(sesion, j)
    limpiar.append(c)
    imp = sesion.query(LogImport).filter(LogImport.cliente_id == c.id).one()
    sesion.query(LogUrl).filter(LogUrl.import_id == imp.id).update({LogUrl.verificado: False})
    sesion.commit()
    _analizar(sesion, j)
    r = sesion.get(Job, j.id).logs_resumen
    assert r["afirmable_ausencia"] is False and "CDN" in r["no_se_afirma"]
    assert sesion.execute(select(Issue.id).where(Issue.job_id == j.id,
                                                 Issue.issue_type == "indexable_sin_visitas_googlebot")).all() == []


def test_un_log_de_otro_host_o_de_otra_epoca_no_cuenta(sesion, job, limpiar):  # noqa: F811
    j, _ = job
    c = _con_logs(sesion, j)
    limpiar.append(c)
    imp = sesion.query(LogImport).filter(LogImport.cliente_id == c.id).one()
    imp.host = "blog.x.com"
    sesion.commit()
    _analizar(sesion, j)
    assert sesion.get(Job, j.id).logs_resumen is None, "un log del blog no habla de la web"
    imp.host = "www.x.com"
    imp.desde, imp.hasta = FIN - timedelta(days=200), FIN - timedelta(days=186)
    sesion.commit()
    _analizar(sesion, j)
    assert sesion.get(Job, j.id).logs_resumen is None, "un log de otra época tampoco"
    assert sesion.execute(select(Url.googlebot_visitas).where(Url.job_id == j.id,
                                                               Url.googlebot_visitas.isnot(None))).all() == []
    assert sesion.execute(select(Issue.id).where(Issue.job_id == j.id, Issue.issue_type.in_(
        ("indexable_sin_visitas_googlebot", "googlebot_recibe_error")))).all() == []


def test_sin_logs_las_columnas_quedan_a_null(sesion, job, limpiar):  # noqa: F811
    j, filas = job
    c = _con_logs(sesion, j)
    limpiar.append(c)
    _analizar(sesion, j)
    sesion.query(LogImport).filter(LogImport.cliente_id == c.id).delete()
    sesion.commit()
    _analizar(sesion, j)
    sesion.expire_all()
    assert sesion.get(Url, filas["/"].id).googlebot_visitas is None
    assert sesion.get(Job, j.id).logs_resumen is None


def test_el_presupuesto_por_plantilla(sesion, job, limpiar):  # noqa: F811
    """En que tipo de pagina se va Googlebot (#35 con #12): por las plantillas
    del cliente y, si no casa ninguna, por la forma de la ruta."""
    from test_pagerank_db import N_ARTICULOS

    j, filas = job
    limpiar.append(_con_logs(sesion, j))
    sesion.get(Job, j.id).config = {"templates": [{"nombre": "articulo", "patron": "^/blog/"}]}
    sesion.commit()
    _analizar(sesion, j)

    filas_r = {p["plantilla"]: p for p in sesion.get(Job, j.id).logs_resumen["por_plantilla"]}
    art = filas_r["articulo"]
    # /blog/a1 (5) y /blog/a3 (4); la de /blog/a1?orden=precio no es indexable.
    assert (art["visitas"], art["urls"], art["urls_visitadas"]) == (9, N_ARTICULOS + 1, 2)
    assert (art["indexables"], art["indexables_visitadas"]) == (N_ARTICULOS, 2)
    primera = sesion.get(Job, j.id).logs_resumen["por_plantilla"][0]
    assert primera["visitas"] == 400 and primera["plantilla"] != "articulo"
    # Lo que el rastreo no encontro no entra en ninguna plantilla del censo.
    total = sum(p["visitas"] for p in filas_r.values())
    assert total == 417 - 6
    assert abs(sum(p["parte"] for p in filas_r.values()) - 1) < 0.01


def test_reparto_por_plantilla_saca_las_que_google_no_visita():
    """Pasado el tope de filas, las plantillas sin visitas pero con indexables
    siguen saliendo: que Google no pase por una plantilla es lo que importa."""
    import types

    from analysis import avisos_de_logs
    from shared.plantillas import plantilla_de

    urls = [types.SimpleNamespace(id=i, url=f"https://x.com/s{i}/p", path=f"/s{i}/p")
            for i in range(avisos_de_logs.MAX_PLANTILLAS + 5)]
    reglas = [(f"s{i}", f"^/s{i}/") for i in range(len(urls))]
    visitas = {i: 10 + i for i in range(avisos_de_logs.MAX_PLANTILLAS + 2)}
    filas = avisos_de_logs.reparto_por_plantilla(urls, visitas, urls, reglas, plantilla_de)
    nombres = [f["plantilla"] for f in filas]
    assert len(nombres) == avisos_de_logs.MAX_PLANTILLAS + 3
    assert {"s32", "s33", "s34"} <= set(nombres)
    assert all(f["visitas"] == 0 for f in filas[avisos_de_logs.MAX_PLANTILLAS:])
