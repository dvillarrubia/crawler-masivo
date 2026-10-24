"""Perfil de dispositivo y capturas de pantalla por muestra (decision 173).

Sin navegador: el almacen de capturas en el volumen de archivo, la ficha de la
URL y la imagen por la API (TestClient y SQLite), que la referencia de la
comparacion automatica es del MISMO perfil, que el hash del contenido no se
compara entre perfiles, y que el preset de Chrome Android del formulario es un
movil coherente (UA, huella TLS y contexto de render). El spider real con
Chromium va en `test_capturas_pantalla_spider.py`.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared import capturas_pantalla, perfiles

RAIZ = Path(__file__).resolve().parent.parent
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 600
H = "ab" * 32


@pytest.fixture()
def archivo(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    return tmp_path / "archivo"


# ---------------------------------------------------------------------------
# El almacen
# ---------------------------------------------------------------------------
def test_guardar_listar_y_borrar(archivo):
    job = uuid.uuid4()
    capturas_pantalla.guardar(job, H, "movil", JPEG, {"plantilla": "/p/*", "ancho": 412})
    capturas_pantalla.guardar(job, H, "escritorio", JPEG, {"plantilla": "/p/*", "ancho": 1280})
    capturas_pantalla.guardar(job, "cd" * 32, "movil", JPEG, {"plantilla": "/q/*"})
    ruta = capturas_pantalla.ruta_imagen(job, H, "movil")
    assert ruta == archivo / "capturas" / str(job) / f"{H}.movil.jpg" and ruta.read_bytes() == JPEG

    de_url = capturas_pantalla.de_url(job, H)
    assert [c["perfil"] for c in de_url] == ["escritorio", "movil"]
    assert de_url[1]["ancho"] == 412 and de_url[1]["bytes"] == len(JPEG)
    assert capturas_pantalla.cuantas_por_plantilla(job) == {("/p/*", "movil"): 1,
                                                          ("/p/*", "escritorio"): 1,
                                                          ("/q/*", "movil"): 1}
    assert capturas_pantalla.bytes_en_disco(job) > 3 * len(JPEG)
    assert capturas_pantalla.resumen_del_directorio()["capturas_jobs"] == 1
    # Reemplaza: una por URL y perfil
    capturas_pantalla.guardar(job, H, "movil", JPEG + b"\x01", {"plantilla": "/p/*"})
    assert len(capturas_pantalla.listar(job)) == 3

    capturas_pantalla.borrar(job)
    assert not capturas_pantalla.directorio(job).exists()
    assert capturas_pantalla.listar(job) == [] and capturas_pantalla.bytes_en_disco(job) == 0
    capturas_pantalla.borrar(job)  # dos veces no lanza


def test_lo_que_no_es_una_captura_no_se_guarda(archivo):
    job = uuid.uuid4()
    with pytest.raises(ValueError):
        capturas_pantalla.guardar(job, H, "movil", b"\x89PNG....", {})
    with pytest.raises(ValueError):
        capturas_pantalla.guardar(job, H, "movil", JPEG + b"x" * capturas_pantalla.MAX_BYTES, {})
    # Ni un nombre que se salga de la carpeta, ni un perfil desconocido, ni un job raro
    for url_hash, perfil in (("../../etc/passwd", "movil"), (H, "tablet"), (H, "../movil")):
        with pytest.raises(ValueError):
            capturas_pantalla.guardar(job, url_hash, perfil, JPEG, {})
    with pytest.raises(ValueError):
        capturas_pantalla.directorio("../otro")
    assert not (archivo / "capturas" / str(job)).exists() or capturas_pantalla.listar(job) == []


def test_un_metadato_sin_imagen_no_es_una_captura(archivo):
    job = uuid.uuid4()
    capturas_pantalla.guardar(job, H, "movil", JPEG, {})
    capturas_pantalla.ruta_imagen(job, H, "movil").unlink()
    assert capturas_pantalla.listar(job) == [] and capturas_pantalla.de_url(job, H) == []


# ---------------------------------------------------------------------------
# La API: ficha, imagen y lista
# ---------------------------------------------------------------------------
@pytest.fixture()
def api(archivo, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    from api.routers import results
    from shared.database import get_session
    from shared.models import Base, Job, Url

    @compiles(BigInteger, "sqlite")
    def _bigint_sqlite(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine(f"sqlite:///{tmp_path}/c.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    with Sesion() as s:
        job = Job(id=uuid.uuid4(), name="c", status="completed", seeds=["https://x.com/"],
                  config={"crawl_behavior": {"capturas_muestra": 5}})
        otro = Job(id=uuid.uuid4(), name="otro", status="completed", seeds=["https://x.com/"],
                   config={})
        s.add_all([job, otro])
        s.flush()
        con = Url(job_id=job.id, url="https://x.com/a", url_hash=H, host="x.com", path="/a",
                  is_internal=True, is_html=True, status_code=200)
        sin = Url(job_id=job.id, url="https://x.com/b", url_hash="ef" * 32, host="x.com",
                  path="/b", is_internal=True, is_html=True, status_code=200)
        ajena = Url(job_id=otro.id, url="https://x.com/a", url_hash=H, host="x.com", path="/a",
                    is_internal=True, is_html=True, status_code=200)
        s.add_all([con, sin, ajena])
        s.commit()
        ids = {"job": job.id, "otro": otro.id, "con": con.id, "sin": sin.id, "ajena": ajena.id}
    capturas_pantalla.guardar(ids["job"], H, "movil", JPEG, {"plantilla": "/a", "ancho": 412,
                                                             "alto": 915})

    app = FastAPI()
    app.include_router(results.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), ids


def test_la_ficha_trae_la_captura_y_la_imagen_se_sirve(api):
    cliente, ids = api
    base = f"/api/jobs/{ids['job']}"
    d = cliente.get(f"{base}/urls/{ids['con']}").json()
    assert len(d["capturas_pantalla"]) == 1
    c = d["capturas_pantalla"][0]
    assert c["perfil"] == "movil" and c["ancho"] == 412 and c["plantilla"] == "/a"
    assert c["src"] == f"{base}/urls/{ids['con']}/captura/movil"
    r = cliente.get(c["src"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == JPEG and "private" in r.headers["cache-control"]
    # Sin captura: lista vacia en la ficha y 404 en la imagen
    assert cliente.get(f"{base}/urls/{ids['sin']}").json()["capturas_pantalla"] == []
    assert cliente.get(f"{base}/urls/{ids['sin']}/captura/movil").status_code == 404
    assert cliente.get(f"{base}/urls/{ids['con']}/captura/escritorio").status_code == 404
    assert cliente.get(f"{base}/urls/{ids['con']}/captura/tablet").status_code == 404


def test_la_captura_de_un_censo_no_se_sirve_desde_otro(api):
    """Misma URL (mismo url_hash) en otro censo: ni la ficha ni la imagen."""
    cliente, ids = api
    otro = f"/api/jobs/{ids['otro']}"
    assert cliente.get(f"{otro}/urls/{ids['ajena']}").json()["capturas_pantalla"] == []
    assert cliente.get(f"{otro}/urls/{ids['ajena']}/captura/movil").status_code == 404
    # Ni pidiendo la URL de un censo con el id del otro
    assert cliente.get(f"{otro}/urls/{ids['con']}/captura/movil").status_code == 404


def test_la_lista_del_censo(api):
    cliente, ids = api
    d = cliente.get(f"/api/jobs/{ids['job']}/capturas").json()
    assert d["pedidas"] == 5 and [c["perfil"] for c in d["capturas"]] == ["movil"]
    d = cliente.get(f"/api/jobs/{ids['otro']}/capturas").json()
    assert d == {"job_id": str(ids["otro"]), "pedidas": 0, "capturas": []}


def test_la_config_valida_los_topes():
    pytest.importorskip("pydantic")
    from pydantic import ValidationError

    from api.schemas import CrawlBehaviorConfig, JobConfig

    cb = JobConfig().crawl_behavior
    assert (cb.capturas_muestra, cb.capturas_por_plantilla, cb.capturas_ambos_perfiles) == (0, 1, False)
    ok = JobConfig(crawl_behavior={"capturas_muestra": 50, "capturas_por_plantilla": 5,
                                   "capturas_ambos_perfiles": True})
    assert ok.model_dump()["crawl_behavior"]["capturas_ambos_perfiles"] is True
    for malo in ({"capturas_muestra": 51}, {"capturas_por_plantilla": 0},
                 {"capturas_por_plantilla": 6}):
        with pytest.raises(ValidationError):
            CrawlBehaviorConfig(**malo)


# ---------------------------------------------------------------------------
# Comparacion entre censos: el perfil cuenta
# ---------------------------------------------------------------------------
def _sesion_jobs():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from shared.models import Base, Job

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Job.__table__])
    return sessionmaker(bind=engine)()


def _job(s, nombre, dia, ua=None, **kw):
    from shared.models import Job

    j = Job(id=uuid.uuid4(), name=nombre, status="completed", seeds=["https://x.com/"],
            config={"user_agent": ua} if ua else {}, started_at=datetime(2026, 1, dia), **kw)
    s.add(j)
    s.flush()
    return j


def test_la_referencia_automatica_es_del_mismo_perfil():
    """Un censo movil no se compara con el de escritorio de ayer: la diferencia
    seria del dispositivo, y las alertas saltarian por eso."""
    from shared.comparacion import censo_anterior

    s = _sesion_jobs()
    esc_1 = _job(s, "escritorio 1", 1)
    movil_1 = _job(s, "movil 1", 2, perfiles.UA_CHROME_ANDROID)
    esc_2 = _job(s, "escritorio 2", 3)
    movil_2 = _job(s, "movil 2", 4, perfiles.UA_GOOGLEBOT_SMARTPHONE)
    assert censo_anterior(s, esc_2).id == esc_1.id
    assert censo_anterior(s, movil_2).id == movil_1.id
    assert censo_anterior(s, movil_1) is None, "sin otro movil no hay con que"


def test_en_una_serie_tambien_cuenta_el_perfil():
    from shared.comparacion import censo_anterior
    from shared.models import Programacion

    s = _sesion_jobs()
    Programacion.__table__.create(s.get_bind(), checkfirst=True)
    prog = uuid.uuid4()
    viejo = _job(s, "semanal 1", 1, programacion_id=prog)
    _job(s, "semanal 2 (alguien puso UA movil)", 2, perfiles.UA_CHROME_ANDROID,
         programacion_id=prog)
    actual = _job(s, "semanal 3", 3, programacion_id=prog)
    assert censo_anterior(s, actual).id == viejo.id


def test_el_hash_del_contenido_no_se_compara_entre_perfiles():
    from shared.trafico_entre_censos import hash_comparable

    def job(ua=None):
        return SimpleNamespace(crawler_version="v1", config={"user_agent": ua} if ua else {})

    assert hash_comparable(job(), job())
    assert hash_comparable(job(perfiles.UA_CHROME_ANDROID), job(perfiles.UA_GOOGLEBOT_SMARTPHONE))
    assert not hash_comparable(job(), job(perfiles.UA_CHROME_ANDROID))


# ---------------------------------------------------------------------------
# El perfil movil por nombre: preset, huella y contexto coherentes
# ---------------------------------------------------------------------------
def test_el_preset_chrome_android_es_un_movil_coherente():
    app = (RAIZ / "frontend" / "app.js").read_text(encoding="utf-8")
    ua = re.search(r"^\s*chrome_android:\s*'([^']+)'", app, re.M).group(1)
    assert ua == perfiles.UA_CHROME_ANDROID
    # Sin render: la huella automatica es la de Android y casa con el UA
    assert perfiles.huella_para_ua(ua) == perfiles.IMPERSONATE_MOVIL
    assert perfiles.incoherencia_ua_huella(ua, perfiles.IMPERSONATE_MOVIL) is None
    assert perfiles.cabeceras_de_huella(perfiles.IMPERSONATE_MOVIL, ua)["Sec-Ch-Ua-Platform"] == '"Android"'
    # Con render: el contexto del censo pasa a ser de movil
    assert perfiles.perfil_de_ua(ua) == "movil"
    ctx = perfiles.contexto_playwright(ua, perfiles.es_ua_movil(ua), base={"locale": "es-ES"})
    assert ctx["viewport"] == {"width": 412, "height": 915} and ctx["is_mobile"]
    assert ctx["device_scale_factor"] == 2.625 and ctx["user_agent"] == ua
    assert ctx["locale"] == "es-ES"
    index = (RAIZ / "frontend" / "index.html").read_text(encoding="utf-8")
    assert '<option value="chrome_android"' in index


def test_la_api_deduce_la_huella_movil_del_preset():
    pytest.importorskip("pydantic")
    from api.schemas import JobConfig

    c = JobConfig(user_agent=perfiles.UA_CHROME_ANDROID)
    assert c.impersonate == perfiles.IMPERSONATE_MOVIL


def test_paridad_y_capturas_construyen_igual_el_contexto_del_otro_perfil():
    """Un contexto de Playwright con nombre se crea con los argumentos de la
    PRIMERA peticion que lo nombra: la paridad y las capturas comparten
    `perfil_movil`, asi que su UA sale de la misma funcion."""
    assert perfiles.ua_de_perfil_alternativo("movil") == perfiles.UA_CHROME_ANDROID
    assert perfiles.ua_de_perfil_alternativo("movil", "googlebot_smartphone") == \
        perfiles.UA_GOOGLEBOT_SMARTPHONE
    assert perfiles.ua_de_perfil_alternativo("escritorio") == perfiles.UA_CHROME_ESCRITORIO
