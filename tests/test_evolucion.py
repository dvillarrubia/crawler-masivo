"""Evolucion de un perfil: `profile_runs` y la serie (decision 130),
EJECUTANDO contra una base de verdad (SQLite; tambien Postgres con
PAGERANK_TEST_DATABASE_URL).

Lo que se sujeta:

- el agregado cuenta lo que hay en `urls` e `issues` del job y **no lee
  `links`** (decision 37), comprobado capturando el SQL que se ejecuta;
- lo escriben el analisis (`run_analysis`) y el cierre del worker, y si
  revienta no se lleva nada por delante (decisiones 74 y 77);
- un censo truncado, fallido, un canario o una lista no son un punto
  comparable, y entre dos puntos con distinta version del analisis la
  tendencia no dice «sube» ni «baja» (decisiones 72 y 91);
- el relleno hacia atras escribe solo lo que falta y con `aplicar=False`
  dentro de una transaccion que se deshace no deja nada.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import BigInteger, create_engine, event, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared.models import Base, Issue, Job, Link, ProfileRun, Url

URL_PG = os.environ.get("PAGERANK_TEST_DATABASE_URL")
MOTORES = ["sqlite"] + (["postgres"] if URL_PG else [])
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture(params=MOTORES)
def base(request, tmp_path):
    engine = (create_engine(URL_PG) if request.param == "postgres"
              else create_engine(f"sqlite:///{tmp_path}/e.db"))
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    creados: list = []
    yield Sesion, creados, engine
    with Sesion() as s:
        for jid in creados:
            s.query(ProfileRun).filter(ProfileRun.job_id == jid).delete()
            s.query(Issue).filter(Issue.job_id == jid).delete()
            s.query(Link).filter(Link.job_id == jid).delete()
            s.query(Url).filter(Url.job_id == jid).delete()
            s.query(Job).filter(Job.id == jid).delete()
        s.commit()
    engine.dispose()


def _censo(s, creados, *, dias=0, status="completed", nombre="acme censo", errores=2,
           analisis="v1", crawler="c1", finish="finished", config=None, pagerank=None):
    """Un censo pequeno: 4 HTML 2xx (3 indexables, profundidades 0, 1 y sin
    camino), un 301, un 404, un 500, una bloqueada y una URL externa."""
    j = Job(id=uuid.uuid4(), name=nombre, status=status, seeds=["https://x.com/"],
            config=config if config is not None else {"max_depth": 3},
            created_at=T0 + timedelta(days=dias), analisis_version=analisis,
            crawler_version=crawler, finish_reason=finish,
            pagerank_resumen=pagerank if pagerank is not None else {
                "reparto": {"indexable": 0.8, "error": 0.05}, "desperdiciado": 0.0625})
    s.add(j)
    s.flush()
    creados.append(j.id)
    filas = [
        ("/", "2xx", 200, True, True, 0),
        ("/a", "2xx", 200, True, True, 1),
        ("/b", "2xx", 200, True, True, None),
        ("/noindex", "2xx", 200, True, False, 2),
        ("/viejo", "3xx", 301, False, None, None),
        ("/roto", "4xx", 404, True, False, None),
        ("/caido", "5xx", 500, True, False, None),
        ("/privado", "blocked", None, False, None, None),
    ]
    urls = []
    for ruta, grupo, codigo, html, idx, prof in filas:
        u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=f"{j.id}{ruta}", host="x.com",
                path=ruta, is_internal=True, is_html=html, status_group=grupo,
                status_code=codigo, indexable=idx, click_depth=prof)
        s.add(u)
        urls.append(u)
    s.add(Url(job_id=j.id, url="https://otro.com/", url_hash=f"{j.id}ext", host="otro.com",
              is_internal=False, is_html=True, status_group="2xx", status_code=200, indexable=True))
    s.flush()
    for n in range(errores):
        s.add(Issue(job_id=j.id, url_id=urls[5].id, issue_type="status_4xx", severity="error",
                    group_key=str(n)))
    s.add(Issue(job_id=j.id, url_id=urls[6].id, issue_type="status_5xx", severity="error"))
    s.add(Issue(job_id=j.id, url_id=urls[1].id, issue_type="title_too_long", severity="warning"))
    s.add(Issue(job_id=j.id, url_id=urls[2].id, issue_type="title_too_long", severity="warning"))
    s.add(Issue(job_id=j.id, url_id=urls[2].id, issue_type="url_has_parameters", severity="info"))
    s.add(Link(job_id=j.id, from_url_id=urls[0].id, to_url="https://x.com/a",
               to_url_hash="h", is_internal=True))
    s.commit()
    return j.id


# --------------------------------------------------------------- agregado ---
def test_el_agregado_cuenta_urls_incidencias_y_pagerank(base):
    from shared.evolucion import agregar

    Sesion, creados, _ = base
    with Sesion() as s:
        jid = _censo(s, creados)
        a = agregar(s, jid)
    assert (a["urls"], a["urls_2xx"], a["urls_3xx"], a["urls_4xx"], a["urls_5xx"],
            a["urls_bloqueadas"]) == (8, 4, 1, 1, 1, 1)   # la externa no cuenta
    assert (a["paginas_html"], a["indexables"]) == (4, 3)
    # Media de 0 y 1 (la indexable sin camino no entra en la media, se cuenta)
    assert a["click_depth_medio"] == 0.5 and a["indexables_sin_camino"] == 1
    assert (a["incidencias"], a["errores"], a["avisos"], a["info"]) == (6, 3, 2, 1)
    assert a["urls_con_error"] == 2
    assert a["top_tipos"][0] == {"tipo": "status_4xx", "severidad": "error", "n": 2}
    assert a["pagerank_desperdiciado"] == 0.0625 and a["pagerank_indexable"] == 0.8
    assert a["analisis_version"] == "v1" and a["crawler_version"] == "c1"


def test_el_agregado_no_lee_links(base):
    """Decision 37: la evolucion no puede costar lo que cuesta leer enlaces."""
    from shared.evolucion import registrar

    Sesion, creados, engine = base
    sentencias: list[str] = []

    def anotar(conn, cursor, sql, params, context, executemany):
        sentencias.append(sql.lower())

    with Sesion() as s:
        jid = _censo(s, creados)
        event.listen(engine, "before_cursor_execute", anotar)
        try:
            registrar(s, jid)
            s.commit()
        finally:
            event.remove(engine, "before_cursor_execute", anotar)
    assert sentencias, "no se ejecuto nada"
    assert not [q for q in sentencias if " links" in q or "links." in q], sentencias


def test_registrar_rehace_la_misma_fila_y_solo_si_falta_no_recuenta(base):
    from shared.evolucion import registrar

    Sesion, creados, _ = base
    with Sesion() as s:
        jid = _censo(s, creados, errores=2)
        registrar(s, jid)
        s.commit()
        s.add(Issue(job_id=jid, url_id=s.execute(select(Url.id).where(Url.job_id == jid)).scalars().first(),
                    issue_type="status_4xx", severity="error", group_key="otra"))
        s.get(Job, jid).status = "completed"
        s.commit()
        # Misma version del analisis: con solo_si_falta no se recuenta...
        assert registrar(s, jid, solo_si_falta=True) is None
        s.commit()
        assert s.execute(select(ProfileRun.errores).where(ProfileRun.job_id == jid)).scalar() == 3
        # ...pero si se re-analiza (otra version), si.
        s.get(Job, jid).analisis_version = "v2"
        s.commit()
        assert registrar(s, jid, solo_si_falta=True)["errores"] == 4
        s.commit()
        filas = s.execute(select(ProfileRun).where(ProfileRun.job_id == jid)).scalars().all()
    assert len(filas) == 1 and filas[0].errores == 4 and filas[0].analisis_version == "v2"


# ------------------------------------------------------------ quien escribe ---
def _sin_enlaces_en_sqlite(monkeypatch, engine):
    # En el trabajo de la API no esta el analizador (numpy, trafilatura...):
    # estos dos corren en el del crawler.
    pytest.importorskip("numpy")
    from analysis.analyzer import SEOAnalyzer

    if engine.dialect.name == "sqlite":
        for paso in ("compute_link_counts", "compute_pagerank", "analyze_links"):
            monkeypatch.setattr(SEOAnalyzer, paso, lambda self: None)


def test_el_analisis_deja_la_fila_con_su_version(base, monkeypatch):
    az = pytest.importorskip("analysis.analyzer")
    from shared.version import VERSION

    Sesion, creados, engine = base
    _sin_enlaces_en_sqlite(monkeypatch, engine)
    monkeypatch.setattr(az, "SessionLocal", Sesion)
    with Sesion() as s:
        jid = _censo(s, creados, status="analyzing", analisis=None)
    az.run_analysis(jid)
    with Sesion() as s:
        fila = s.execute(select(ProfileRun).where(ProfileRun.job_id == jid)).scalar_one()
        incidencias = s.query(Issue).filter(Issue.job_id == jid).count()
    assert fila.analisis_version == VERSION
    assert fila.incidencias == incidencias and fila.urls == 8


def test_si_la_evolucion_revienta_el_analisis_termina_igual(base, monkeypatch):
    az = pytest.importorskip("analysis.analyzer")
    import shared.evolucion as ev

    Sesion, creados, engine = base
    _sin_enlaces_en_sqlite(monkeypatch, engine)
    monkeypatch.setattr(az, "SessionLocal", Sesion)

    def revienta(*a, **k):
        raise RuntimeError("profile_runs no existe")

    monkeypatch.setattr(ev, "registrar", revienta)
    with Sesion() as s:
        jid = _censo(s, creados, status="analyzing", analisis=None)
    az.run_analysis(jid)   # no lanza
    with Sesion() as s:
        assert s.get(Job, jid).analisis_version is not None   # el analisis si se sello
        assert s.query(ProfileRun).filter(ProfileRun.job_id == jid).count() == 0


def test_el_worker_registra_el_rastreo_fallido_y_no_lanza(base, monkeypatch):
    import shared.database
    import shared.evolucion as ev

    pytest.importorskip("scrapy")
    worker = pytest.importorskip("worker")
    Sesion, creados, _ = base
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    with Sesion() as s:
        jid = _censo(s, creados, status="failed", finish="bloqueado_waf", errores=0)
    worker._registrar_evolucion(str(jid))
    with Sesion() as s:
        fila = s.execute(select(ProfileRun).where(ProfileRun.job_id == jid)).scalar_one()
    assert fila.status == "failed" and fila.urls == 8

    monkeypatch.setattr(ev, "agregar", lambda *a, **k: 1 / 0)
    with Sesion() as s:
        s.query(ProfileRun).delete()
        s.commit()
    worker._registrar_evolucion(str(jid))   # no lanza


# ------------------------------------------------------------------ serie ---
def _par(n, *, status="completed", errores=10, analisis="v1", crawler="c1", nombre=None,
         finish="finished", config=None, perfil="p1", fila=True):
    job = {"id": uuid.UUID(int=n), "name": nombre or f"censo {n}", "tipo": None, "status": status,
           "finish_reason": finish, "config": config or {"max_depth": 3},
           "seeds": ["https://x.com/"], "created_at": T0 + timedelta(days=7 * n),
           "profile_id": perfil, "programacion_id": None,
           "analisis_version": analisis, "crawler_version": crawler}
    agregado = None if not fila else {
        "criterio": 1, "analisis_version": analisis, "crawler_version": crawler,
        "errores": errores, "urls": 100, "indexables": 50, "pagerank_desperdiciado": 0.012}
    return job, agregado


def test_lo_que_no_es_un_censo_entero_no_entra_en_la_linea():
    from shared.evolucion import series

    filas = [_par(1), _par(2, status="failed"), _par(3, finish="max_urls_reached"),
             _par(4, nombre="CANARY - acme"), _par(5, config={"max_depth": 0}),
             _par(6, fila=False), _par(7)]
    (serie,) = series(filas)
    motivos = [p["motivo"] for p in serie["puntos"]]
    assert motivos == [None, "failed", "truncado", "canario", "lista", "sin_agregado", None]
    assert serie["comparables"] == 2
    assert serie["puntos"][0]["pagerank_desperdiciado"] == 1.2   # en porcentaje


def test_el_cambio_de_version_del_analisis_corta_la_tendencia():
    from shared.evolucion import series

    filas = [_par(1, errores=10), _par(2, errores=12), _par(3, errores=40, analisis="v2"),
             _par(4, status="failed")]
    (serie,) = series(filas)
    p1, p2, p3, _ = serie["puntos"]
    assert p2["cambio_version"] is None
    assert p3["cambio_version"]["analisis"] and not p3["cambio_version"]["rastreo"]
    t = serie["tendencia_errores"]
    assert t["direccion"] == "no_comparable" and t["delta"] is None and t["mejora"] is None
    assert t["valores"] == [10, 12, 40] and t["cortes"] == [2]
    # Sin el corte, la misma subida es «sube» y empeora.
    (serie,) = series([_par(1, errores=10), _par(2, errores=12), _par(3, errores=40)])
    t = serie["tendencia_errores"]
    assert (t["direccion"], t["delta"], t["mejora"]) == ("sube", 28, False)
    assert t["valores"] == [10, 12, 40]


def test_version_desconocida_cuenta_como_cambio_y_el_rastreo_no_corta():
    from shared.evolucion import series

    (serie,) = series([_par(1, analisis=None), _par(2)])
    assert serie["puntos"][1]["cambio_version"]["analisis"] is True
    (serie,) = series([_par(1, errores=9, crawler="c1"), _par(2, errores=5, crawler="c2")])
    cambio = serie["puntos"][1]["cambio_version"]
    assert cambio["rastreo"] and not cambio["analisis"]
    assert serie["tendencia_errores"]["direccion"] == "baja"
    assert serie["tendencia_errores"]["mejora"] is True


def test_un_agregado_de_otra_version_que_la_del_job_esta_desfasado():
    from shared.evolucion import series

    job, fila = _par(1)
    fila["analisis_version"] = "v0"
    (serie,) = series([(job, fila)])
    assert serie["puntos"][0]["motivo"] == "desfasado"


def test_dos_perfiles_son_dos_series_y_sin_perfil_va_por_host():
    from shared.evolucion import series

    otro = _par(2, perfil="p2")
    sin = _par(3, perfil=None)
    claves = {s["clave"] for s in series([_par(1), otro, sin], {("perfil", "p1"): "Sitio"})}
    assert claves == {"perfil:p1", "perfil:p2", "host:x.com"}


# ----------------------------------------------------------------- relleno ---
def test_el_relleno_escribe_lo_que_falta_y_simulado_no_deja_nada(base):
    import importlib.util
    from pathlib import Path

    ruta = Path(__file__).resolve().parent.parent / "scripts" / "rellenar_evolucion.py"
    spec = importlib.util.spec_from_file_location("rellenar_evolucion", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    Sesion, creados, engine = base
    with Sesion() as s:
        a = _censo(s, creados, dias=0)
        b = _censo(s, creados, dias=7, status="failed")
        c = _censo(s, creados, dias=14, status="cancelled")
        mios = {a, b, c}

    # Simulado: una transaccion que se deshace.
    from sqlalchemy.orm import Session

    with engine.connect() as conexion:
        tx = conexion.begin()
        s = Session(bind=conexion, join_transaction_mode="create_savepoint")
        r = mod.rellenar(s, ids=mios, aplicar=False, salida=lambda *_: None)
        assert r["escritos"] == 2 and not r["fallos"]   # el cancelado no
        s.close()
        tx.rollback()
    with Sesion() as s:
        assert s.query(ProfileRun).filter(ProfileRun.job_id.in_(mios)).count() == 0

    with Sesion() as s:
        r = mod.rellenar(s, ids=mios, salida=lambda *_: None)
        assert r["escritos"] == 2
        r = mod.rellenar(s, ids=mios, salida=lambda *_: None)
        assert r["pendientes"] == 0                      # ya estan
        r = mod.rellenar(s, ids=mios, todos=True, salida=lambda *_: None)
        assert r["escritos"] == 2
        assert s.query(ProfileRun).filter(ProfileRun.job_id.in_(mios)).count() == 2


# ----------------------------------------------------- lectura de la serie ---
def test_leer_la_evolucion_no_carga_las_semillas_de_cada_job(base):
    """La ficha y el panel leen TODOS los rastreos terminados del cliente, y
    `jobs.seeds` de un censo sembrado desde el sitemap pesa cientos de kB
    (Lopesan semanal: 3.040 semillas, 233 kB por job). Para nombrar la serie
    por host basta el host, que se guarda en la fila al agregar: leer las
    semillas haria crecer la portada con cada semana de cada cliente."""
    from shared.evolucion import leer, registrar, tendencias_de_clientes
    from shared.models import Client

    Sesion, creados, engine = base
    with Sesion() as s:
        c = Client(nombre="Acme", slug=f"acme-{uuid.uuid4().hex[:8]}")
        s.add(c)
        s.flush()
        ids = [_censo(s, creados, dias=d, errores=e) for d, e in ((0, 1), (7, 3))]
        sin_fila = _censo(s, creados, dias=14)
        for jid in ids + [sin_fila]:
            j = s.get(Job, jid)
            j.cliente_id = c.id
            j.seeds = ["https://x.com/"] + [f"https://x.com/p/{n}" for n in range(500)]
        s.commit()
        for jid in ids:
            registrar(s, jid)
        s.commit()
        cid = c.id

    sentencias: list[str] = []

    def anotar(conn, cursor, sql, params, context, executemany):
        sentencias.append(sql.lower())

    try:
        with Sesion() as s:
            event.listen(engine, "before_cursor_execute", anotar)
            r = leer(s, cid)
            t = tendencias_de_clientes(s, [cid])
    finally:
        event.remove(engine, "before_cursor_execute", anotar)
        with Sesion() as s:
            for jid in ids + [sin_fila]:
                s.get(Job, jid).cliente_id = None
            s.query(Client).filter(Client.id == cid).delete()
            s.commit()

    # Una sola serie por host, con los dos agregados y el que no lo tiene.
    assert [x["clave"] for x in r["series"]] == ["host:x.com"]
    assert len(r["series"][0]["puntos"]) == 3 and r["sin_agregado"] == 1
    assert t[cid]["clave"] == "host:x.com"
    con_semillas = [q for q in sentencias if "jobs.seeds" in q]
    # Solo se piden las del job SIN fila, y solo las de ese.
    assert len(con_semillas) <= 1, con_semillas
