"""Borrado en masa de rastreos (decision 96), EJECUTADO con TestClient y SQLite.

Lo que se sujeta, que es lo que no se puede deshacer si falla:

- que se borra TODO lo del censo y nada de los demas;
- que el censo de referencia de una programacion activa NO se borra sin
  insistir (decisiones 78 y 86), que insistiendo si, y que un rastreo en
  marcha no se borra ni insistiendo;
- que un borrado cortado a medias deja el job en `deleting`, fuera de las
  referencias de la comparacion y del archivado;
- que la simulacion recorre lo mismo que el borrado y no toca nada;
- que el presupuesto de tiempo devuelve `pendientes` y que repetir termina —el
  caso que de verdad importa: un lote de censos grandes partido en varias
  peticiones no puede dejar filas huerfanas por el camino.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine, event  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import (  # noqa: E402
    Base, CustomExtraction, Decision, HtmlMeta, Issue, Job, Link, PageContent, Programacion, Url,
)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _job(s, nombre, dias, *, status="completed", paginas=3, enlaces=2, **kw):
    j = Job(id=uuid.uuid4(), name=nombre, status=status, seeds=["https://x.com/"],
            config={}, created_at=T0 + timedelta(days=dias),
            started_at=T0 + timedelta(days=dias), total_urls_crawled=paginas, **kw)
    s.add(j)
    s.flush()
    for n in range(paginas):
        u = Url(job_id=j.id, url=f"https://x.com/{n}", url_hash=f"{j.id}-{n}",
                is_internal=True, is_html=True, status_code=200)
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title=f"t{n}"))
        s.add(PageContent(url_id=u.id, content_text="texto " * 50))
        s.add(Issue(job_id=j.id, url_id=u.id, issue_type="missing_title", severity="warning"))
        # Extraccion a medida (decision 104): una fila por URL y regla.
        s.add(CustomExtraction(job_id=j.id, url_id=u.id, nombre="precio", clase="extractor",
                               estado="ok", n=1, valor="9,99", valor_num=9.99))
        for k in range(enlaces):
            # Agrupadas (decision 94): una fila con sus repeticiones. Lo que se
            # cuenta y se borra son FILAS, no apariciones.
            s.add(Link(job_id=j.id, from_url_id=u.id, to_url=f"https://x.com/{k}",
                       to_url_hash=f"h{k}", repeticiones=3))
    s.flush()
    return j


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    from api.routers import jobs

    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path / "archivo"))
    engine = create_engine(f"sqlite:///{tmp_path}/b.db")

    # SQLite no aplica las FK si no se le pide, y el borrado de `urls` se
    # apoya en el ON DELETE CASCADE para arrastrar html_meta y page_content.
    # Sin esto el test diria que las hijas se quedan colgando cuando en
    # Postgres se van, o al contrario.
    @event.listens_for(engine, "connect")
    def _fk(conexion, _r):
        conexion.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine, expire_on_commit=False)
    s = Sesion()

    prog = Programacion(id=uuid.uuid4(), nombre="Semanal de x.com", seeds=["https://x.com/"],
                        cron="0 3 * * 1", activa=True)
    apagada = Programacion(id=uuid.uuid4(), nombre="Vieja", seeds=["https://x.com/"],
                           cron="0 3 * * 1", activa=False)
    s.add_all([prog, apagada])
    s.flush()

    ids = {
        "prueba": _job(s, "prueba de techo", 0).id,
        "canario": _job(s, "CANARY - x", 1).id,
        "en_marcha": _job(s, "en curso", 2, status="running").id,
        "serie_vieja": _job(s, "semanal 1", 3, programacion_id=prog.id).id,
        "serie_nueva": _job(s, "semanal 2", 4, programacion_id=prog.id).id,
        "serie_apagada": _job(s, "vieja 1", 5, programacion_id=apagada.id).id,
    }
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(jobs.router)

    def _sesion():
        ses = Sesion()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_session] = _sesion
    monkeypatch.setattr(jobs, "get_redis", lambda: None)
    return TestClient(app), Sesion, ids


def _filas(Sesion, job_id):
    s = Sesion()
    try:
        return {
            "jobs": s.query(Job).filter(Job.id == job_id).count(),
            "urls": s.query(Url).filter(Url.job_id == job_id).count(),
            "links": s.query(Link).filter(Link.job_id == job_id).count(),
            "issues": s.query(Issue).filter(Issue.job_id == job_id).count(),
            "custom_extraction": s.query(CustomExtraction).filter(
                CustomExtraction.job_id == job_id).count(),
        }
    finally:
        s.close()


def _borrar(api, ids, **extra):
    cuerpo = {"ids": [str(i) for i in ids], **extra}
    r = api.post("/api/jobs/eliminar", json=cuerpo)
    assert r.status_code == 200, r.text
    return r.json()


def test_borra_varios_y_no_deja_nada_detras(entorno):
    api, Sesion, ids = entorno
    d = _borrar(api, [ids["prueba"], ids["canario"]])
    assert {e["id"] for e in d["eliminados"]} == {str(ids["prueba"]), str(ids["canario"])}
    assert d["omitidos"] == [] and d["pendientes"] == []
    for clave in ("prueba", "canario"):
        assert _filas(Sesion, ids[clave]) == {"jobs": 0, "urls": 0, "links": 0, "issues": 0,
                                                "custom_extraction": 0}
    # Las hijas que cuelgan de url_id se van con la URL (ON DELETE CASCADE).
    s = Sesion()
    assert s.query(HtmlMeta).count() == s.query(PageContent).count() == 4 * 3
    # Y lo que no se pidio sigue intacto.
    assert _filas(Sesion, ids["serie_nueva"])["urls"] == 3
    assert _filas(Sesion, ids["serie_nueva"])["custom_extraction"] == 3
    s.close()
    # Las filas se cuentan por tabla, que es lo que se ensena al terminar.
    assert d["filas"]["urls"] == 6 and d["filas"]["links"] == 12 and d["filas"]["issues"] == 6
    assert d["filas"]["custom_extraction"] == 6


@pytest.mark.parametrize("estado", ["pending", "running", "analyzing"])
def test_un_rastreo_en_marcha_no_se_borra_ni_forzando(entorno, estado):
    """Con `analyzing`, el DELETE de la fila de `jobs` esperaria al FOR UPDATE
    del analisis toda la pasada; con `running`, el pipeline seguiria metiendo
    enlaces mientras se borran. `forzar` solo salta el guardia de la serie."""
    api, Sesion, ids = entorno
    s = Sesion()
    s.query(Job).filter(Job.id == ids["en_marcha"]).update({Job.status: estado})
    s.commit()
    s.close()
    for forzar in (False, True):
        d = _borrar(api, [ids["en_marcha"]], forzar=forzar)
        assert d["eliminados"] == []
        assert len(d["omitidos"]) == 1 and "en marcha" in d["omitidos"][0]["motivo"]
        assert d["omitidos"][0]["forzable"] is False
        assert _filas(Sesion, ids["en_marcha"])["jobs"] == 1


def test_el_censo_de_referencia_de_una_serie_viva_se_protege(entorno):
    """Decisiones 78 y 86: sin referencia, la serie deja de avisar en silencio."""
    api, Sesion, ids = entorno
    # El ultimo completado de la programacion activa: protegido.
    d = _borrar(api, [ids["serie_nueva"]])
    assert d["eliminados"] == []
    assert "referencia" in d["omitidos"][0]["motivo"]
    assert d["omitidos"][0]["forzable"] is True
    assert "Semanal de x.com" in d["omitidos"][0]["motivo"]
    # El anterior de la misma serie NO lo esta: ya no es con lo que se compara.
    d = _borrar(api, [ids["serie_vieja"]])
    assert len(d["eliminados"]) == 1 and d["omitidos"] == []
    # Y el de una programacion apagada tampoco: esa serie no va a volver a correr.
    d = _borrar(api, [ids["serie_apagada"]])
    assert len(d["eliminados"]) == 1 and d["omitidos"] == []
    # Pidiendo los dos de una serie viva se va el viejo y se queda el nuevo,
    # que es justo lo que tiene que pasar: la serie conserva su referencia.
    assert _filas(Sesion, ids["serie_nueva"])["jobs"] == 1


def test_pidiendo_los_dos_de_la_serie_se_conserva_el_de_referencia(entorno):
    api, Sesion, ids = entorno
    d = _borrar(api, [ids["serie_vieja"], ids["serie_nueva"]])
    assert [e["id"] for e in d["eliminados"]] == [str(ids["serie_vieja"])]
    assert [o["id"] for o in d["omitidos"]] == [str(ids["serie_nueva"])]
    assert _filas(Sesion, ids["serie_nueva"])["urls"] == 3


def test_forzar_tambien_se_lleva_la_referencia(entorno):
    api, Sesion, ids = entorno
    d = _borrar(api, [ids["serie_nueva"]], forzar=True)
    assert len(d["eliminados"]) == 1 and d["omitidos"] == []
    assert _filas(Sesion, ids["serie_nueva"])["jobs"] == 0


def test_simular_no_borra_nada_y_pasa_por_los_mismos_guardias(entorno):
    api, Sesion, ids = entorno
    d = _borrar(api, [ids["prueba"], ids["en_marcha"], ids["serie_nueva"]], simular=True)
    assert d["simulado"] is True
    assert [e["id"] for e in d["eliminados"]] == [str(ids["prueba"])]
    assert {o["id"] for o in d["omitidos"]} == {str(ids["en_marcha"]), str(ids["serie_nueva"])}
    assert d["filas"] == {}
    # Nada se ha tocado.
    for clave in ("prueba", "en_marcha", "serie_nueva"):
        assert _filas(Sesion, ids[clave])["jobs"] == 1
    # Las filas contadas por censo viajan en la vista previa: son la unica
    # pista de cuanto pesa cada uno antes de darle.
    assert d["eliminados"][0]["filas"] == {"urls": 3, "links": 6, "issues": 3,
                                                       "custom_extraction": 3}
    # Fuera de Postgres no hay bytes por fila de donde sacar un tamano, y eso
    # se dice en vez de inventar un cero (shared/almacenamiento.py).
    assert d["eliminados"][0]["bytes"] is None
    assert d["bytes_aprox"] is None and d["bytes_incompletos"] is True


def test_simular_avisa_cuando_el_sitio_se_queda_sin_censo(entorno):
    api, _, ids = entorno
    # Todos los censos del fixture son del mismo host; pidiendo los cinco
    # completados, x.com se queda sin ninguno. Es un aviso, no un guardia:
    # siguen en `eliminados`.
    completados = [ids[k] for k in
                   ("prueba", "canario", "serie_vieja", "serie_nueva", "serie_apagada")]
    d = _borrar(api, completados, simular=True, forzar=True)
    assert len(d["eliminados"]) == 5
    assert all("sin ningún censo completado" in e["aviso"] for e in d["eliminados"])
    # Dejando uno, no hay aviso.
    d = _borrar(api, completados[:-1], simular=True, forzar=True)
    assert all(e["aviso"] is None for e in d["eliminados"])


def test_un_id_que_ya_no_existe_se_omite_sin_romper_el_lote(entorno):
    api, Sesion, ids = entorno
    fantasma = uuid.uuid4()
    d = _borrar(api, [fantasma, ids["prueba"]])
    assert [o["motivo"] for o in d["omitidos"]] == ["ya no existe"]
    assert len(d["eliminados"]) == 1
    assert _filas(Sesion, ids["prueba"])["jobs"] == 0


def test_los_ids_repetidos_se_cuentan_una_vez(entorno):
    api, _, ids = entorno
    d = _borrar(api, [ids["prueba"], ids["prueba"]])
    assert len(d["eliminados"]) == 1 and d["omitidos"] == []


def test_el_presupuesto_de_tiempo_devuelve_pendientes_y_repetir_termina(entorno, monkeypatch):
    """Lo borrado a medias es consistente, y la siguiente pasada lo acaba."""
    from api.routers import jobs as router_jobs

    api, Sesion, ids = entorno
    # Trozos de una fila y un presupuesto que se agota a la segunda mirada del
    # reloj: asi el primer job se queda a medias de verdad.
    monkeypatch.setattr(router_jobs, "TROZO_DE_BORRADO", 1)
    # Se parchea `_reloj` del modulo y NO `time.monotonic`: lo segundo cambia
    # el reloj de todo el proceso (httpx, SQLite, pytest) y la suite se cuelga.
    vueltas = {"n": 0}

    def _reloj_falso():
        vueltas["n"] += 1
        return 0.0 if vueltas["n"] <= 2 else 1e9

    monkeypatch.setattr(router_jobs, "_reloj", _reloj_falso)
    # Cada censo se mide UNA vez en toda la operacion: medir cuenta los enlaces
    # (segundos en un censo grande) y repetirlo en cada pasada es gastar el
    # presupuesto en contar filas que se van a borrar justo despues.
    from shared import almacenamiento

    medidos: list = []
    medir = almacenamiento.tamano_de_jobs

    def _medir(db, job_ids, **kw):
        medidos.extend(job_ids)
        return medir(db, job_ids, **kw)

    monkeypatch.setattr(almacenamiento, "tamano_de_jobs", _medir)

    lote = [ids["prueba"], ids["canario"]]
    d = _borrar(api, lote, segundos=5)
    assert d["eliminados"] == []
    # El que no se llego a empezar no se midio.
    assert medidos == [ids["prueba"]]
    assert set(d["pendientes"]) == {str(i) for i in lote}
    # Una pasada sin terminar nada SI avanza: trae filas borradas, que es lo
    # que la interfaz mira para no darla por atascada.
    assert sum(d["filas"].values()) > 0
    # Lo que se llego a borrar esta borrado; la fila de `jobs` sigue, asi que
    # el censo es «uno con menos enlaces», no un huerfano.
    assert _filas(Sesion, ids["prueba"])["jobs"] == 1
    assert _filas(Sesion, ids["prueba"])["links"] < 6
    s = Sesion()
    assert s.get(Job, ids["prueba"]).status == "deleting"
    # El segundo no se llego a empezar: sigue como estaba.
    assert s.get(Job, ids["canario"]).status == "completed"
    s.close()

    # Repitiendo con los pendientes y sin reloj trucado, se acaba. El que ya
    # estaba a medias no se vuelve a medir: lo que queda no es lo que ocupaba.
    monkeypatch.setattr(router_jobs, "_reloj", time.monotonic)
    d = _borrar(api, lote)
    assert d["pendientes"] == [] and len(d["eliminados"]) == 2
    por_id = {e["id"]: e for e in d["eliminados"]}
    assert por_id[str(ids["prueba"])]["filas"] is None
    assert por_id[str(ids["prueba"])]["status"] == "deleting"
    assert por_id[str(ids["canario"])]["filas"] == {"urls": 3, "links": 6, "issues": 3,
                                                       "custom_extraction": 3}
    assert medidos == [ids["prueba"], ids["canario"]]
    for clave in ("prueba", "canario"):
        assert _filas(Sesion, ids[clave]) == {"jobs": 0, "urls": 0, "links": 0, "issues": 0,
                                                "custom_extraction": 0}


def test_se_rechaza_un_lote_desmedido_y_uno_vacio(entorno):
    api, _, _ = entorno
    muchos = [str(uuid.uuid4()) for _ in range(501)]
    r = api.post("/api/jobs/eliminar", json={"ids": muchos})
    assert r.status_code == 422 and "500" in r.text
    assert api.post("/api/jobs/eliminar", json={"ids": []}).status_code == 422


def test_las_decisiones_del_enriquecido_se_van_con_el_job(entorno):
    api, Sesion, ids = entorno
    s = Sesion()
    s.add(Decision(job_id=ids["prueba"], clave="k", pregunta="pagina_legal",
                   version_pregunta=1, estado="decidido", origen="modelo",
                   modelo="typesafe/jev-1.13"))
    s.commit()
    s.close()
    _borrar(api, [ids["prueba"]])
    s = Sesion()
    assert s.query(Decision).filter(Decision.job_id == ids["prueba"]).count() == 0
    s.close()


def test_el_historial_de_limpiezas_tambien(entorno):
    """`cleaning_rulesets` lleva `job_id` y no se borraba: quedaba huerfano."""
    from shared.models import CleaningRuleset

    api, Sesion, ids = entorno
    s = Sesion()
    s.add(CleaningRuleset(job_id=ids["prueba"], rules={"quitar": ["x"]}, targets="both"))
    s.commit()
    s.close()
    d = _borrar(api, [ids["prueba"]])
    assert d["filas"]["cleaning_rulesets"] == 1
    s = Sesion()
    assert s.query(CleaningRuleset).count() == 0
    s.close()


# --------------------------------------------------------------------------
# Censos con los enlaces archivados en Parquet (decision 95)
# --------------------------------------------------------------------------
def _archivar(Sesion, job_id):
    pytest.importorskip("pyarrow")
    from shared import archivo_enlaces

    s = Sesion()
    try:
        archivo_enlaces.archivar(s, job_id)
        return archivo_enlaces.ruta(job_id)
    finally:
        s.close()


def test_un_censo_archivado_se_mide_por_su_parquet_y_se_borra_con_el(entorno):
    """Sin filas en `links` pero ocupando disco: ni «sin medir» ni su tamano de
    antes. Y al borrarlo se va tambien el fichero, que es el unico espacio que
    un borrado devuelve al disco en el acto."""
    api, Sesion, ids = entorno
    fichero = _archivar(Sesion, ids["prueba"])
    assert fichero.exists() and _filas(Sesion, ids["prueba"])["links"] == 0

    d = _borrar(api, [ids["prueba"], ids["canario"]], simular=True)
    por_id = {e["id"]: e for e in d["eliminados"]}
    archivado, vivo = por_id[str(ids["prueba"])], por_id[str(ids["canario"])]
    assert archivado["archivado"] is True
    assert archivado["archivo_bytes"] == fichero.stat().st_size > 0
    # Sus enlaces no estan en la base: 0 filas, contadas sin consultar.
    assert archivado["filas"]["links"] == 0
    assert vivo["archivado"] is False and vivo["archivo_bytes"] == 0
    assert vivo["filas"]["links"] == 6
    assert d["archivo_bytes"] == fichero.stat().st_size
    assert fichero.exists(), "la simulacion no puede tocar el fichero"

    d = _borrar(api, [ids["prueba"]])
    assert len(d["eliminados"]) == 1 and d["archivo_bytes"] > 0
    assert not fichero.exists()
    assert _filas(Sesion, ids["prueba"]) == {"jobs": 0, "urls": 0, "links": 0, "issues": 0,
                                                "custom_extraction": 0}


def test_borrar_uno_a_uno_y_en_masa_quitan_el_parquet_igual(entorno):
    """Una sola funcion (`borrar_job`) para los dos caminos: el DELETE de la
    ficha ya quitaba el fichero, y el borrado en masa no puede olvidarlo."""
    api, Sesion, ids = entorno
    fichero = _archivar(Sesion, ids["canario"])
    assert api.delete(f"/api/jobs/{ids['canario']}").status_code in (200, 204)
    assert not fichero.exists()


def test_a_medias_el_parquet_se_queda_con_su_job(entorno, monkeypatch):
    """Si el presupuesto corta, la fila de `jobs` sigue y sigue apuntando a su
    fichero: borrarlo antes dejaria un censo «archivado» sin archivo."""
    from api.routers import jobs as router_jobs

    api, Sesion, ids = entorno
    fichero = _archivar(Sesion, ids["prueba"])
    monkeypatch.setattr(router_jobs, "TROZO_DE_BORRADO", 1)
    vueltas = {"n": 0}

    def _reloj_falso():
        vueltas["n"] += 1
        return 0.0 if vueltas["n"] <= 2 else 1e9

    monkeypatch.setattr(router_jobs, "_reloj", _reloj_falso)
    d = _borrar(api, [ids["prueba"]], segundos=5)
    assert d["pendientes"] == [str(ids["prueba"])]
    assert fichero.exists() and _filas(Sesion, ids["prueba"])["jobs"] == 1

    monkeypatch.setattr(router_jobs, "_reloj", time.monotonic)
    d = _borrar(api, [ids["prueba"]])
    assert d["pendientes"] == [] and not fichero.exists()


def test_un_censo_a_medio_borrar_no_es_referencia_ni_se_archiva(entorno, monkeypatch):
    """Si nadie vuelve a terminar el borrado, lo que queda no puede pasar por
    un censo `completed`: la comparacion lo elegiria como referencia y sus
    alertas callarian (decisiones 78 y 86), y el archivado lo guardaria en un
    Parquet vacio (decision 95)."""
    from api.routers import jobs as router_jobs
    from shared import archivo_enlaces
    from shared.comparacion import censo_anterior

    api, Sesion, ids = entorno
    monkeypatch.setattr(router_jobs, "TROZO_DE_BORRADO", 1)
    vueltas = {"n": 0}

    def _reloj_falso():
        vueltas["n"] += 1
        return 0.0 if vueltas["n"] <= 2 else 1e9

    monkeypatch.setattr(router_jobs, "_reloj", _reloj_falso)
    # `canario` es el completado mas reciente de x.com sin serie.
    d = _borrar(api, [ids["canario"]], segundos=5)
    assert d["pendientes"] == [str(ids["canario"])]

    s = Sesion()
    nuevo = _job(s, "manual", 10)
    s.commit()
    ref = censo_anterior(s, nuevo)
    assert ref is None or ref.id != ids["canario"]
    # Con un completado mas nuevo que abarca sus semillas y mucho tiempo
    # despues, un `completed` seria archivable; a medio borrar, no.
    lejos = datetime(2030, 1, 1, tzinfo=timezone.utc)
    archivables = {c.id for c in archivo_enlaces.candidatos(s, ahora=lejos)}
    assert ids["prueba"] in archivables, "el control: el mismo caso con un completado si"
    assert ids["canario"] not in archivables
    s.close()


def test_un_parquet_sin_medir_no_suma_como_cero(entorno):
    """Archivado, sin fichero visible y sin `bytes` anotados: el total lo dice."""
    api, Sesion, ids = entorno
    fichero = _archivar(Sesion, ids["prueba"])
    fichero.unlink()
    s = Sesion()
    j = s.get(Job, ids["prueba"])
    j.enlaces_archivados = {k: v for k, v in j.enlaces_archivados.items() if k != "bytes"}
    s.commit()
    s.close()
    d = _borrar(api, [ids["prueba"]], simular=True)
    assert d["eliminados"][0]["archivo_bytes"] is None
    assert d["archivo_bytes"] == 0 and d["archivo_incompleto"] is True


def test_un_censo_con_el_candado_tomado_no_se_borra_ni_forzando(entorno, monkeypatch):
    """El re-analisis de la referencia (decision 93) y el archivado (95) toman
    el candado del censo, y un `completed` puede estar re-analizandose sin que
    su `status` lo diga. Borrarlo a la vez dejaria un analisis escribiendo
    sobre filas que desaparecen."""
    import api.routers.jobs as jobs_router

    api, Sesion, ids = entorno
    monkeypatch.setattr(jobs_router, "_candado_ocupado", lambda db, job_id: job_id == ids["prueba"])
    for forzar in (False, True):
        d = _borrar(api, [ids["prueba"], ids["canario"]], forzar=forzar, simular=True)
        omitidos = {o["id"]: o for o in d["omitidos"]}
        assert set(omitidos) == {str(ids["prueba"])}
        assert "analizando o archivando" in omitidos[str(ids["prueba"])]["motivo"]
        assert omitidos[str(ids["prueba"])]["forzable"] is False
    d = _borrar(api, [ids["prueba"], ids["canario"]])
    assert [e["id"] for e in d["eliminados"]] == [str(ids["canario"])]
    assert _filas(Sesion, ids["prueba"])["jobs"] == 1


def test_candado_ocupado_contra_postgres():
    """La comprobacion de verdad: otra conexion tiene el candado del censo."""
    import os

    from sqlalchemy import text

    from api.routers.jobs import _candado_ocupado
    from shared.candados import clave_de_job

    url = os.environ.get("ALMACENAMIENTO_TEST_DATABASE_URL") or os.environ.get(
        "PAGERANK_TEST_DATABASE_URL")
    if not url:
        pytest.skip("sin ALMACENAMIENTO_TEST_DATABASE_URL ni PAGERANK_TEST_DATABASE_URL")
    motor = create_engine(url)
    job_id = uuid.uuid4()
    Sesion = sessionmaker(bind=motor)
    s = Sesion()
    try:
        assert _candado_ocupado(s, job_id) is False
        # Probarlo no lo deja tomado: una segunda prueba sigue viendolo libre.
        assert _candado_ocupado(s, job_id) is False
        with motor.connect() as otra:
            assert otra.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                {"k": clave_de_job(job_id)}).scalar()
            assert _candado_ocupado(s, job_id) is True
            otra.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": clave_de_job(job_id)})
        assert _candado_ocupado(s, job_id) is False
    finally:
        s.close()
        motor.dispose()
