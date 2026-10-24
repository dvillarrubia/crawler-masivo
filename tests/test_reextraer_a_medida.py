"""`scripts/reextraer_a_medida.py` (#34, decision 166), EJECUTADO: contra
SQLite para lo que hace con las filas y contra Postgres de verdad (con
`PAGERANK_TEST_DATABASE_URL` o `A_MEDIDA_TEST_DATABASE_URL`) para el candado
del job, que en SQLite no existe.

Lo que se sujeta:

- una pagina con `raw_html` se re-extrae entera con las reglas fusionadas
  (sustituir por nombre, `null` quita) y las reglas quedan en `jobs.config`;
- un muro sale `pagina_bloqueada`; una 404 y una externa no tienen fila;
- una pagina SIN `raw_html` pierde las filas de las reglas que cambian o se
  quitan y conserva las de las que no cambian (sin fila = no evaluada);
- `fuente=contenido` lee `page_content.content_text`;
- `--dry-run` no escribe; un job en marcha, reglas invalidas o el candado
  tomado por otro: no se re-extrae.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from contextlib import contextmanager

import pytest

pytest.importorskip("lxml")
pytest.importorskip("regex")

from sqlalchemy import BigInteger, create_engine, text  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.models import Base, CustomExtraction, Job, PageContent, Url  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))
import reextraer_a_medida as rx  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


CONFIG = {
    "custom_extraction": {
        "precio": {"tipo": "css", "expresion": ".precio", "valor": "numero"},
        "sku": {"tipo": "css", "expresion": ".sku"},
    },
    "custom_search": {"gtm": {"patron": "GTM-"}},
}

FICHA = ("<html><head><title>Ficha</title></head><body><span class='precio'>19,95 €</span>"
         "<div class='sku'>A1</div><div class='ean'>8410</div></body></html>")


@contextmanager
def _siempre(job_id):
    yield True


@contextmanager
def _nunca(job_id):
    yield False


def _poblar(s, status="completed"):
    job = Job(id=uuid.uuid4(), name="tienda", status=status, seeds=["https://x.com/"],
              config=json.loads(json.dumps(CONFIG)))
    s.add(job)
    s.flush()

    def url(ruta, codigo=200, html=True, interna=True, clase=None):
        u = Url(job_id=job.id, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com",
                path=ruta, is_internal=interna, is_html=html, status_code=codigo,
                status_group=f"{codigo // 100}xx", resource_type="html", response_class=clase)
        s.add(u)
        s.flush()
        return u

    u = {"con_html": url("/p/1"), "sin_html": url("/p/2"), "muro": url("/muro", 403,
                                                                         clase="challenge_waf"),
         "no_esta": url("/no", 404), "externa": url("/ext", interna=False)}
    s.add(PageContent(url_id=u["con_html"].id, raw_html=FICHA, content_text="Ficha precio 19,95"))
    s.add(PageContent(url_id=u["sin_html"].id, content_text="otra"))
    for nombre, clase in (("precio", "extractor"), ("sku", "extractor"), ("gtm", "busqueda")):
        for k in ("con_html", "sin_html"):
            s.add(CustomExtraction(job_id=job.id, url_id=u[k].id, nombre=nombre, clase=clase,
                                   estado="ok", n=1, valor="viejo", valor_clave="viejo",
                                   truncado=False, render=False))
    s.commit()
    return job.id, {k: v.id for k, v in u.items()}


@pytest.fixture()
def sesion(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


def _filas(s, job_id):
    return {(f.url_id, f.nombre): f for f in s.query(CustomExtraction)
            .filter(CustomExtraction.job_id == job_id)}


NUEVAS = {"custom_extraction": {
    "sku": {"tipo": "css", "expresion": ".ean"},                       # cambia
    "texto": {"tipo": "regex", "expresion": r"precio (\S+)", "fuente": "contenido"},  # nueva
}, "custom_search": {"gtm": None}}                                      # se quita


def test_reextrae_con_las_reglas_fusionadas_y_las_guarda(sesion):
    jid, u = _poblar(sesion)
    r = rx.reextraer(sesion, jid, NUEVAS, candado=_siempre)
    f = _filas(sesion, jid)
    # Con raw_html: todas las filas nuevas, ninguna vieja.
    assert f[(u["con_html"], "precio")].valor_num == 19.95
    assert f[(u["con_html"], "sku")].valor == "8410"
    assert f[(u["con_html"], "texto")].valor == "19,95"
    assert (u["con_html"], "gtm") not in f
    # Sin raw_html: se conserva la que no cambia y caen la cambiada y la quitada.
    assert f[(u["sin_html"], "precio")].valor == "viejo"
    assert (u["sin_html"], "sku") not in f and (u["sin_html"], "gtm") not in f
    assert (u["sin_html"], "texto") not in f
    # El muro, bloqueado; la 404 y la externa, sin fila.
    assert {f[(u["muro"], n)].estado for n in ("precio", "sku", "texto")} == {"pagina_bloqueada"}
    assert not [k for k in f if k[0] in (u["no_esta"], u["externa"])]
    assert r["evaluadas"] == 1 and r["bloqueadas"] == 1 and r["sin_html"] == 1
    assert r["caducas"] == ["gtm", "sku"]      # `texto` es nueva: no tenia filas
    sesion.expire_all()
    cfg = sesion.get(Job, jid).config
    assert set(cfg["custom_extraction"]) == {"precio", "sku", "texto"}
    assert cfg["custom_search"] == {}


def test_sin_reglas_nuevas_repite_la_extraccion_y_no_toca_las_que_no_puede(sesion):
    jid, u = _poblar(sesion)
    r = rx.reextraer(sesion, jid, None, candado=_siempre)
    f = _filas(sesion, jid)
    assert f[(u["con_html"], "sku")].valor == "A1"
    assert f[(u["con_html"], "gtm")].estado == "sin_coincidencia"
    assert {f[(u["sin_html"], n)].valor for n in ("precio", "sku", "gtm")} == {"viejo"}
    assert r["caducas"] == []


def test_dry_run_no_escribe(sesion):
    jid, u = _poblar(sesion)
    r = rx.reextraer(sesion, jid, NUEVAS, dry_run=True, candado=_siempre)
    assert r["filas"] == 6 and r["dry_run"]
    f = _filas(sesion, jid)
    assert f[(u["con_html"], "sku")].valor == "viejo" and (u["muro"], "precio") not in f
    sesion.expire_all()
    assert "texto" not in sesion.get(Job, jid).config["custom_extraction"]


def test_lo_que_impide_reextraer(sesion):
    jid, _ = _poblar(sesion, status="running")
    with pytest.raises(rx.NoSePuede, match="running"):
        rx.reextraer(sesion, jid, None, candado=_siempre)
    jid, _ = _poblar(sesion)
    with pytest.raises(rx.NoSePuede, match="no validas"):
        rx.reextraer(sesion, jid, {"custom_extraction": {
            "x": {"tipo": "xpath", "expresion": "//a[re:test(@h,'x')]"}}}, candado=_siempre)
    with pytest.raises(rx.NoSePuede, match="candado"):
        rx.reextraer(sesion, jid, None, candado=_nunca)
    with pytest.raises(rx.NoSePuede, match="desconocidas"):
        rx.reextraer(sesion, jid, {"custom_extractions": {}}, candado=_siempre)
    assert _filas(sesion, jid)[next(iter(_filas(sesion, jid)))].valor == "viejo"


def test_si_muere_a_medias_no_queda_nada_cambiado(sesion, monkeypatch):
    """Revision de #233: con un commit por lote, un fallo en el tercer lote
    dejaba las filas del primero con la regla NUEVA y `jobs.config` con la
    VIEJA, y la comparacion entre censos (127) lo tomaba por cambio del sitio."""
    from shared import extraccion_a_medida as xm

    jid, u = _poblar(sesion)
    monkeypatch.setattr(rx, "LOTE", 1)        # un lote por URL: con_html, sin_html, muro...
    original = xm.aplicar

    def aplicar(*a, **kw):
        if kw.get("bloqueada"):               # el muro, en un lote posterior
            raise RuntimeError("muere a medias")
        return original(*a, **kw)

    monkeypatch.setattr(xm, "aplicar", aplicar)
    with pytest.raises(RuntimeError, match="a medias"):
        rx.reextraer(sesion, jid, NUEVAS, candado=_siempre)
    sesion.expire_all()
    f = _filas(sesion, jid)
    # Ni las filas del primer lote ni las borradas del segundo han cambiado.
    assert f[(u["con_html"], "sku")].valor == "viejo"
    assert (u["con_html"], "texto") not in f
    assert f[(u["sin_html"], "gtm")].valor == "viejo"
    cfg = sesion.get(Job, jid).config
    assert cfg["custom_extraction"]["sku"]["expresion"] == ".sku" and "gtm" in cfg["custom_search"]


def test_main_lee_el_fichero_y_sale_con_2_si_no_se_puede(sesion, tmp_path, monkeypatch, capsys):
    import shared.database

    jid, _ = _poblar(sesion, status="analyzing")
    monkeypatch.setattr(shared.database, "SessionLocal", lambda: sesion)
    reglas = tmp_path / "r.json"
    reglas.write_text(json.dumps(NUEVAS))
    assert rx.main([str(jid), "--reglas", str(reglas)]) == 2
    assert "analyzing" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# El candado de verdad (Postgres)
# ---------------------------------------------------------------------------
URL_BD = os.environ.get("A_MEDIDA_TEST_DATABASE_URL") or os.environ.get("PAGERANK_TEST_DATABASE_URL")


@pytest.mark.skipif(not URL_BD, reason="sin base de pruebas de Postgres")
def test_el_candado_del_job_lo_respeta_y_lo_suelta(monkeypatch):
    pytest.importorskip("psycopg2")
    import shared.database
    from shared.candados import clave_de_job

    engine = create_engine(URL_BD)
    import shared.models as modelos

    # Solo las de `shared.models` (como test_a_medida_db.py): sin las de pgvector.
    tablas = [v.__table__ for v in vars(modelos).values()
              if isinstance(v, type) and getattr(v, "__module__", "") == modelos.__name__
              and hasattr(v, "__table__")]
    Base.metadata.create_all(engine, tables=tablas)
    monkeypatch.setattr(shared.database, "engine", engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        jid, u = _poblar(s)
        otra = engine.connect()
        try:
            assert otra.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                {"k": clave_de_job(jid)}).scalar()
            with pytest.raises(rx.NoSePuede, match="candado"):
                rx.reextraer(s, jid, NUEVAS)
            otra.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": clave_de_job(jid)})
        finally:
            otra.close()
        r = rx.reextraer(s, jid, NUEVAS)
        assert r["evaluadas"] == 1
        assert _filas(s, jid)[(u["con_html"], "sku")].valor == "8410"
        # Soltado: otro lo puede tomar.
        with engine.connect() as c:
            assert c.execute(text("SELECT pg_try_advisory_lock(:k)"),
                             {"k": clave_de_job(jid)}).scalar()
            c.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": clave_de_job(jid)})
    finally:
        s.rollback()
        s.query(Job).filter(Job.name == "tienda").delete(synchronize_session=False)
        s.commit()
        s.close()
        engine.dispose()
