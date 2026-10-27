"""Revision humana de Jev (#8, decision 211), EJECUTADA: la API con TestClient
sobre SQLite y la exportacion pasada por `evaluar` de verdad (decision 74).

- a ciegas: lo pendiente no trae la respuesta de Jev; se ve al etiquetar;
- un estado, una etiqueta, aunque salga en dos URLs o dos censos;
- la etiqueta se valida contra el vocabulario de la pregunta;
- `solo_si`: lo que no aplica no se ofrece;
- la etiqueta sobrevive a borrar el censo;
- lo exportado lo lee `evaluar` y cuenta la etiqueta humana.
"""

from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine, event  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.database import get_session  # noqa: E402
from shared.models import Base, Decision, DecisionLabel, Job, Url  # noqa: E402
from shared.preguntas import CATALOGO  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint(tipo, compilador, **kw):
    return "INTEGER"


@pytest.fixture()
def entorno(tmp_path):
    from api.routers import revision

    engine = create_engine(f"sqlite:///{tmp_path}/r.db")

    @event.listens_for(engine, "connect")
    def _fk(conexion, _):
        conexion.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(revision.router)

    def _sesion():
        s = S()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    return TestClient(app), S


def _job(S):
    with S() as s:
        j = Job(id=uuid.uuid4(), name="censo", seeds=["https://x.com/"], status="completed", config={})
        s.add(j)
        s.commit()
        return j.id


def _decision(S, job_id, ruta, *, pregunta="es_soft_404", respuesta="false", confianza=0.7,
              estado="incierto", clave=None, origen="modelo", url_id=None, run_id=None):
    p = CATALOGO[pregunta]
    with S() as s:
        if url_id is None:
            u = Url(job_id=job_id, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com", path=ruta)
            s.add(u)
            s.flush()
            url_id = u.id
        d = Decision(job_id=job_id, url_id=url_id, run_id=run_id, ambito="url",
                     clave=clave or f"clave{ruta}", pregunta=pregunta,
                     version_pregunta=p.version, estado=estado, origen=origen, respuesta=respuesta,
                     confianza=confianza, modelo=p.modelo,
                     estado_enviado={"ruta": ruta, "titulo": f"Título de {ruta}"})
        s.add(d)
        s.commit()
        return d.id, url_id


def test_lo_pendiente_va_a_ciegas_y_la_respuesta_se_ve_al_etiquetar(entorno):
    api, S = entorno
    jid = _job(S)
    did, _ = _decision(S, jid, "/agotado/", respuesta="true", confianza=0.62)
    r = api.get("/api/decisiones/revision", params={"pregunta": "es_soft_404"})
    assert r.status_code == 200
    (fila,) = r.json()
    assert fila["decision_id"] == did and fila["estado"]["titulo"] == "Título de /agotado/"
    assert [o["valor"] for o in fila["opciones"]] == ["true", "false"]
    assert not {"respuesta", "confianza", "jev"} & set(fila), "a ciegas"

    r = api.put(f"/api/decisiones/{did}/etiqueta", json={"etiqueta": "sí", "nota": "ficha sin stock"})
    assert r.status_code == 200
    assert r.json() == {"etiqueta": "true", "jev": "true", "confianza": 0.62, "coincide": True}
    assert api.get("/api/decisiones/revision", params={"pregunta": "es_soft_404"}).json() == []


def test_un_estado_en_dos_urls_se_revisa_una_vez(entorno):
    api, S = entorno
    jid, otro = _job(S), _job(S)
    _decision(S, jid, "/a/", clave="misma")
    _decision(S, otro, "/a/?www", clave="misma")
    _decision(S, jid, "/b/")
    filas = api.get("/api/decisiones/revision").json()
    assert len(filas) == 2
    api.put(f"/api/decisiones/{filas[0]['decision_id']}/etiqueta", json={"etiqueta": False})
    assert len(api.get("/api/decisiones/revision").json()) == 1, "la etiqueta vale para sus copias"


def test_una_etiqueta_fuera_del_vocabulario_se_rechaza(entorno):
    api, S = entorno
    did, _ = _decision(S, _job(S), "/a/")
    assert api.put(f"/api/decisiones/{did}/etiqueta", json={"etiqueta": "quizás"}).status_code == 422
    assert api.put("/api/decisiones/999/etiqueta", json={"etiqueta": "true"}).status_code == 404
    did2, _ = _decision(S, _job(S), "/t/", pregunta="title_describe_contenido", respuesta="2.7")
    assert api.put(f"/api/decisiones/{did2}/etiqueta", json={"etiqueta": 9}).status_code == 422
    r = api.put(f"/api/decisiones/{did2}/etiqueta", json={"etiqueta": 3})
    assert r.json()["jev"] == "3" and r.json()["coincide"] is True, "un score se compara por nivel"


def test_corregir_una_etiqueta_no_duplica(entorno):
    api, S = entorno
    did, _ = _decision(S, _job(S), "/a/")
    api.put(f"/api/decisiones/{did}/etiqueta", json={"etiqueta": "true"})
    api.put(f"/api/decisiones/{did}/etiqueta", json={"etiqueta": "false", "autor": "dvillarrubia"})
    with S() as s:
        (e,) = s.query(DecisionLabel).all()
        assert (e.etiqueta, e.autor) == ("false", "dvillarrubia")


def test_lo_decidido_lo_de_regla_y_las_versiones_viejas_no_se_ofrecen(entorno):
    api, S = entorno
    jid = _job(S)
    _decision(S, jid, "/dec/", estado="decidido")
    _decision(S, jid, "/regla/", origen="regla")
    did_viejo, _ = _decision(S, jid, "/vieja/")
    with S() as s:
        s.get(Decision, did_viejo).version_pregunta = CATALOGO["es_soft_404"].version - 1
        s.commit()
    assert api.get("/api/decisiones/revision", params={"pregunta": "es_soft_404"}).json() == []
    todas = api.get("/api/decisiones/revision", params={"pregunta": "es_soft_404", "estado": "todas"}).json()
    assert [f["estado"]["ruta"] for f in todas] == ["/dec/"], "con «todas», también lo decidido"


def test_solo_si_lo_que_no_aplica_no_se_revisa(entorno):
    """La recomendacion de una canibalizacion solo significa algo si las
    paginas son sustitutas (decision 175)."""
    api, S = entorno
    jid = _job(S)
    run = uuid.uuid4()
    from shared.models import EnrichmentRun

    with S() as s:
        s.add(EnrichmentRun(id=run, job_id=jid, modulos=["canibalizacion"], alcance={}, estado="completada"))
        s.commit()
    _, u1 = _decision(S, jid, "/p1/", pregunta="paginas_sustitutas", respuesta="true", clave="par1", run_id=run)
    _decision(S, jid, "/p1/", pregunta="recomendacion_canibalizacion", respuesta=next(iter(
        CATALOGO["recomendacion_canibalizacion"].opciones)), clave="par1", url_id=u1, run_id=run)
    _, u2 = _decision(S, jid, "/p2/", pregunta="paginas_sustitutas", respuesta="false", clave="par2", run_id=run)
    _decision(S, jid, "/p2/", pregunta="recomendacion_canibalizacion", respuesta=next(iter(
        CATALOGO["recomendacion_canibalizacion"].opciones)), clave="par2", url_id=u2, run_id=run)
    filas = api.get("/api/decisiones/revision", params={"pregunta": "recomendacion_canibalizacion"}).json()
    assert [f["estado"]["ruta"] for f in filas] == ["/p1/"]


def test_la_etiqueta_sobrevive_a_borrar_el_censo(entorno):
    api, S = entorno
    jid = _job(S)
    did, _ = _decision(S, jid, "/a/", respuesta="true", confianza=0.9)
    api.put(f"/api/decisiones/{did}/etiqueta", json={"etiqueta": "true"})
    with S() as s:
        s.query(Decision).delete()
        s.query(Url).delete()
        s.query(Job).delete()
        s.commit()
        (e,) = s.query(DecisionLabel).all()
        assert e.decision_id is None and e.estado_enviado["ruta"] == "/a/" and e.respuesta_jev == "true"


def test_resumen_por_pregunta(entorno):
    api, S = entorno
    jid = _job(S)
    a, _ = _decision(S, jid, "/a/", respuesta="true")
    b, _ = _decision(S, jid, "/b/", respuesta="true")
    _decision(S, jid, "/c/")
    api.put(f"/api/decisiones/{a}/etiqueta", json={"etiqueta": "true"})
    api.put(f"/api/decisiones/{b}/etiqueta", json={"etiqueta": "false"})
    (fila,) = [f for f in api.get("/api/decisiones/revision/resumen").json() if f["pregunta"] == "es_soft_404"]
    assert (fila["por_revisar"], fila["etiquetadas"], fila["coinciden_con_jev"]) == (1, 2, 1)


def test_lo_exportado_lo_lee_evaluar_con_la_etiqueta_humana(entorno):
    """El criterio de #8: las etiquetas de la vista amplian el juego."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import evaluar_decisiones as E

    api, S = entorno
    jid = _job(S)
    for n in range(4):
        did, _ = _decision(S, jid, f"/p{n}/", respuesta="true", confianza=0.6 + n / 10)
        api.put(f"/api/decisiones/{did}/etiqueta", json={"etiqueta": "true" if n else "false"})
    juego = [json.loads(x) for x in api.get("/api/decisiones/etiquetas/es_soft_404/juego.jsonl").text.splitlines()]
    resp = [json.loads(x) for x in api.get("/api/decisiones/etiquetas/es_soft_404/respuestas.jsonl").text.splitlines()]
    assert len(juego) == len(resp) == 4 and {f["id"] for f in juego} == {r["id"] for r in resp}
    informe = E.evaluar(juego, resp, 0.95)
    assert informe["pregunta"] == "es_soft_404" and informe["etiquetas_invalidas"] == 0
    assert (informe["ejemplos"], informe["evaluables"], informe["sin_etiqueta"]) == (4, 4, 0)
    assert informe["acierto"] == 0.75, "tres de cuatro: cuenta la etiqueta humana"
    assert api.get("/api/decisiones/etiquetas/no_existe/juego.jsonl").status_code == 404


def test_la_orden_etiquetas_del_script(entorno, tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import evaluar_decisiones as E

    api, S = entorno
    monkeypatch.setattr("shared.database.SessionLocal", S, raising=False)
    did, _ = _decision(S, _job(S), "/a/", respuesta="true", confianza=0.8)
    api.put(f"/api/decisiones/{did}/etiqueta", json={"etiqueta": "false"})
    juego, resp = tmp_path / "j.jsonl", tmp_path / "r.jsonl"
    assert E.main(["etiquetas", "es_soft_404", "--juego", str(juego), "--respuestas", str(resp)]) in (0, None)
    (fila,) = [json.loads(x) for x in juego.read_text().splitlines()]
    assert fila["etiqueta"] == "false" and fila["estado"]["ruta"] == "/a/"
    (r,) = [json.loads(x) for x in resp.read_text().splitlines()]
    assert (r["respuesta"], r["id"]) == ("true", fila["id"])
