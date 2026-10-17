"""Enlaces archivados en Parquet (decision 95), EJECUTADO con TestClient y SQLite.

Lo que se sujeta: que archivar un censo no cambie NADA de lo que la API
devuelve de el (ficha de URL, lista de enlaces, sus dos CSV, los origenes del
explorador, las cifras de /insights y el backup); que restaurar deje `links`
como estaba; que el analisis se niegue a correr sobre un censo archivado; y la
regla de que se archiva, que es la que impide llevarse la referencia de una
comparacion.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("pyarrow")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine, func, select  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared import archivo_enlaces  # noqa: E402
from shared.database import get_session  # noqa: E402
from shared.models import Base, HtmlMeta, Job, Link, RespuestaSondeada, Url  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


_HACE_UN_MES = datetime.now(timezone.utc) - timedelta(days=30)


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    from api.routers import results

    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    engine = create_engine(f"sqlite:///{tmp_path}/e.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    # El CSV de enlaces sin archivar abre sus propias sesiones
    import shared.database
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    s = Sesion()
    job = Job(id=uuid.uuid4(), name="censo", status="completed", seeds=["https://x.com/"],
              config={}, started_at=_HACE_UN_MES, completed_at=_HACE_UN_MES)
    s.add(job)
    s.flush()
    urls = {}
    for ruta in ("/", "/a", "/b", "/c"):
        u = Url(job_id=job.id, url=f"https://x.com{ruta}", url_hash=f"h{ruta}", host="x.com",
                is_internal=True, is_html=True, status_code=200, status_group="2xx")
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title=f"Titulo {ruta}"))
        urls[ruta] = u
    # Menu en todas (con repeticiones), un enlace de cuerpo, un nofollow, uno
    # externo y una redireccion, que no cuenta como enlace en los origenes.
    for origen in urls.values():
        for ruta, destino in urls.items():
            s.add(Link(job_id=job.id, from_url_id=origen.id, to_url=destino.url,
                       to_url_hash=destino.url_hash, anchor_text=f"menu {ruta}",
                       is_internal=True, link_position="nav", follow=True,
                       link_type="hyperlink", repeticiones=2))
    s.add_all([
        Link(job_id=job.id, from_url_id=urls["/"].id, to_url=urls["/a"].url,
             to_url_hash="h/a", anchor_text="leer mas", is_internal=True,
             link_position="content", follow=True, link_type="hyperlink"),
        Link(job_id=job.id, from_url_id=urls["/a"].id, to_url=urls["/b"].url,
             to_url_hash="h/b", anchor_text="patrocinado", rel="nofollow", is_internal=True,
             link_position="content", follow=False, link_type="hyperlink"),
        Link(job_id=job.id, from_url_id=urls["/b"].id, to_url="https://otro.com/",
             to_url_hash="hext", anchor_text="fuera", is_internal=False,
             link_position="footer", follow=True, target="_blank", link_type="hyperlink"),
        Link(job_id=job.id, from_url_id=urls["/c"].id, to_url=urls["/a"].url,
             to_url_hash="h/a", is_internal=True, link_type="redirect", follow=True),
    ])
    # La sonda del enlace externo (decision 123): sale en /links y en su CSV,
    # archivado o no.
    s.add(RespuestaSondeada(job_id=job.id, url_hash="hext", url="https://otro.com/",
                            host="otro.com", uso="enlace", interno=False, metodo="GET",
                            status_code=404, veredicto="roto"))
    s.commit()
    ids = {k: v.id for k, v in urls.items()}
    s.close()

    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), job.id, ids, Sesion


def _todo(api, jid, ids):
    """Todo lo que la API dice de los enlaces del censo."""
    salida = {}
    for ruta, uid in ids.items():
        d = api.get(f"/api/jobs/{jid}/urls/{uid}").json()
        salida[f"ficha{ruta}"] = (d["outlinks"], d["inlinks"])
    salida["links"] = [api.get(f"/api/jobs/{jid}/links?page={p}&page_size=7").json()
                       for p in (1, 2, 3, 4)]
    # Mismas filas; el orden NO: el CSV de `links` va por id y el del archivo
    # por destino, que es el orden del fichero (decision 95).
    cabecera, *filas = api.get(f"/api/jobs/{jid}/links/export").text.splitlines()
    salida["csv"] = (cabecera, sorted(filas))
    salida["origenes"] = [
        (i["url"], i["enlazada_desde"], i["origenes"])
        for i in api.get(f"/api/jobs/{jid}/urls?origenes=true").json()["items"]
    ]
    salida["entrantes"] = api.get(f"/api/jobs/{jid}/urls/enlaces-entrantes").text
    links = api.get(f"/api/jobs/{jid}/insights").json()
    salida["insights"] = [c for c in links["categories"] if c["key"] == "links"]
    return salida


def _filas(Sesion, jid):
    with Sesion() as s:
        return s.execute(select(func.count()).select_from(Link).where(Link.job_id == jid)).scalar()


def test_archivar_no_cambia_nada_de_lo_que_devuelve_la_api(entorno):
    api, jid, ids, Sesion = entorno
    antes = _todo(api, jid, ids)
    assert antes["ficha/a"][1], "la prueba necesita enlaces entrantes"
    filas = _filas(Sesion, jid)

    with Sesion() as s:
        r = archivo_enlaces.archivar(s, jid)
    assert r["filas"] == filas
    assert r["apariciones"] == 16 * 2 + 4
    assert r["internos"] == 16 * 2 + 3 and r["nofollow_internos"] == 1
    assert _filas(Sesion, jid) == 0
    assert archivo_enlaces.ruta(jid).exists()

    assert _todo(api, jid, ids) == antes


def test_el_veredicto_de_la_sonda_sale_en_links_y_en_el_csv(entorno):
    api, jid, _, Sesion = entorno
    for archivado in (False, True):
        if archivado:
            with Sesion() as s:
                archivo_enlaces.archivar(s, jid)
        items = [i for p in (1, 2, 3, 4) for i in
                 api.get(f"/api/jobs/{jid}/links?page={p}&page_size=7").json()["items"]]
        externos = [(i["to_url"], i["estado_externo"], i["status_externo"]) for i in items
                    if not i["is_internal"]]
        assert externos == [("https://otro.com/", "roto", 404)], archivado
        assert all(i["estado_externo"] is None for i in items if i["is_internal"])
        cabecera, *filas = api.get(f"/api/jobs/{jid}/links/export").text.splitlines()
        assert cabecera.lstrip("\ufeff").endswith("estado_externo,status_externo")
        assert [f for f in filas if "otro.com" in f][0].endswith(",roto,404"), archivado


def test_restaurar_deja_links_como_estaba(entorno):
    api, jid, ids, Sesion = entorno
    with Sesion() as s:
        original = [tuple(f) for f in s.execute(
            select(*[Link.__table__.c[c] for c in archivo_enlaces.COLUMNAS])
            .where(Link.job_id == jid).order_by(Link.id)).all()]
        archivo_enlaces.archivar(s, jid)
        assert archivo_enlaces.restaurar(s, jid) == len(original)
        vuelta = [tuple(f) for f in s.execute(
            select(*[Link.__table__.c[c] for c in archivo_enlaces.COLUMNAS])
            .where(Link.job_id == jid).order_by(Link.id)).all()]
        assert s.get(Job, jid).enlaces_archivados is None
    assert vuelta == original
    assert not archivo_enlaces.ruta(jid).exists()


def test_no_archiva_un_job_en_marcha(entorno):
    _, jid, _, Sesion = entorno
    with Sesion() as s:
        s.get(Job, jid).status = "analyzing"
        s.commit()
        with pytest.raises(RuntimeError, match="analyzing"):
            archivo_enlaces.archivar(s, jid)
    assert _filas(Sesion, jid) > 0


def test_el_backup_lleva_los_enlaces_y_el_job_importado_no_esta_archivado(entorno, monkeypatch):
    from api import backup

    _, jid, _, Sesion = entorno
    monkeypatch.setattr(backup, "SessionLocal", Sesion)

    def zip_de():
        datos = b"".join(backup.stream_backup_zip(jid))
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            enlaces = sorted(json.loads(x)["id"] for x in z.read("links.jsonl").splitlines())
            return json.loads(z.read("job.json"))["enlaces_archivados"], enlaces

    _, antes = zip_de()
    with Sesion() as s:
        archivo_enlaces.archivar(s, jid)
    marca, despues = zip_de()
    assert despues == antes and marca is None


# ---------------------------------------------------------------------------
# Que se archiva
# ---------------------------------------------------------------------------
def _job(s, semillas, hace_dias, estado="completed", programacion=None, host="x.com"):
    t = datetime.now(timezone.utc) - timedelta(days=hace_dias)
    j = Job(id=uuid.uuid4(), name=f"{hace_dias}", status=estado, config={},
            seeds=[f"https://{host}{r}" for r in semillas], started_at=t, completed_at=t,
            programacion_id=programacion)
    s.add(j)
    s.flush()
    return j


@pytest.fixture()
def sesion(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/c.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_solo_se_archiva_lo_que_otro_censo_mas_nuevo_abarca(sesion):
    viejo = _job(sesion, ["/", "/blog"], 60)
    canario = _job(sesion, ["/"], 40)           # abarca menos que `viejo`
    completo = _job(sesion, ["/", "/blog", "/tienda"], 30)
    reciente = _job(sesion, ["/", "/blog", "/tienda"], 3)
    otro_host = _job(sesion, ["/"], 90, host="y.com")
    sesion.commit()

    elegidos = {j.id for j in archivo_enlaces.candidatos(sesion)}
    # `viejo` y `canario` los abarca `completo`; `completo` lo abarca
    # `reciente`; `reciente` es el ultimo; `otro_host` no tiene a nadie.
    assert elegidos == {viejo.id, canario.id, completo.id}
    assert reciente.id not in elegidos and otro_host.id not in elegidos


def test_un_censo_que_nadie_abarca_se_queda(sesion):
    completo = _job(sesion, ["/", "/blog"], 60)
    _job(sesion, ["/"], 30)                      # mas nuevo pero con menos alcance
    sesion.commit()
    assert completo.id not in {j.id for j in archivo_enlaces.candidatos(sesion)}


def test_nada_de_los_ultimos_dias_ni_el_ultimo_de_una_programacion(sesion, monkeypatch):
    serie = uuid.uuid4()
    de_serie = _job(sesion, ["/"], 60, programacion=serie)
    reciente = _job(sesion, ["/"], 10)           # menos de 14 dias
    _job(sesion, ["/"], 1)
    sesion.commit()
    elegidos = {j.id for j in archivo_enlaces.candidatos(sesion)}
    assert de_serie.id not in elegidos           # ultimo de su programacion
    assert reciente.id not in elegidos
    monkeypatch.setenv("ARCHIVAR_DIAS_MINIMOS", "5")
    assert reciente.id in {j.id for j in archivo_enlaces.candidatos(sesion)}


def test_un_rastreo_fallido_se_archiva_si_hay_un_completado_mas_nuevo(sesion):
    fallido = _job(sesion, ["/", "/todo"], 60, estado="failed")
    solo_fallido = _job(sesion, ["/"], 60, estado="failed", host="y.com")
    _job(sesion, ["/"], 30)
    en_marcha = _job(sesion, ["/"], 40, estado="running")
    sesion.commit()
    elegidos = {j.id for j in archivo_enlaces.candidatos(sesion)}
    assert fallido.id in elegidos
    assert solo_fallido.id not in elegidos and en_marcha.id not in elegidos
