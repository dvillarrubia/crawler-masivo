"""Canibalizacion juzgada (#18, #169 punto 3, decision 175), EJECUTADA contra
SQLite y el FakeLLM: nada sale a la red (decisiones 84 y 85).

Lo que hay que sujetar:

- la unidad es el grupo (busqueda + URLs), no la URL: una URL en dos grupos
  son dos estados, dos llamadas y dos juegos de filas, y reanudar reconoce lo
  hecho por el estado;
- el filtro coge los avisos de la 157 con alguna URL en el alcance, por clics
  y con tope; el estado lleva rutas, cifras redondeadas y contenido recortado;
- las dos preguntas van en UNA llamada, y la estimacion lo cuenta igual;
- la recomendacion solo se cuenta donde las paginas son sustitutas (`solo_si`);
- el efecto en el aviso esta apagado mientras no haya umbral.
"""

from __future__ import annotations

import dataclasses
import json
import uuid

import pytest

pytest.importorskip("httpx")

import shared.enriquecido as enr  # noqa: E402
import shared.enriquecido_canibalizacion as can  # noqa: E402
from shared.models import Decision, EnrichmentRun, Issue, Job, Url  # noqa: E402
from test_enriquecido import _decisiones, _leer, _motor, _run, capa_activa, sembrar  # noqa: E402,F401

PREGUNTAS = ["paginas_sustitutas", "recomendacion_canibalizacion"]


def _aviso(s, jid, consulta, clics, urls, imp=None):
    """Un `canibalizacion_por_consulta` como lo escribe la 157: cuelga de la
    URL menor del grupo y lleva las URLs ordenadas por parte."""
    ids = {u: i for i, u in s.query(Url.id, Url.url).filter(Url.job_id == jid)}
    ancla = min(u for u, *_ in urls)
    total = sum(c for _, c, *_ in urls) or 1
    i = Issue(job_id=jid, url_id=ids.get(ancla) or min(ids.values()), issue_type="canibalizacion_por_consulta",
              severity="warning" if clics >= 100 else "info", details={
                  "consulta": consulta, "clics": clics, "impresiones": imp or clics * 10,
                  "reparto_por": "clics",
                  "urls": [{"url": u, "clics": c, "impresiones": im, "parte": round(c / total, 3),
                            "posicion": pos, "en_censo": True, "indexable": True, "estado": "Indexable",
                            "plantilla": "/tienda/:slug" if "tienda" in u else "/blog/:slug"}
                           for u, c, im, pos in urls]})
    s.add(i)
    s.flush()
    return i.id


def _sembrar_grupos(Sesion):
    jid, _ = sembrar(Sesion, n=6)
    s = Sesion()
    a = "https://acme.com"
    g1 = _aviso(s, jid, "zapatos rojos", 300, [(f"{a}/tienda/p-0", 180, 2049, 1.4),
                                               (f"{a}/blog/post-1", 120, 1449, 2.6)])
    # La misma URL ancla en otro grupo: es otro estado, no la misma unidad.
    g2 = _aviso(s, jid, "zapatos", 50, [(f"{a}/tienda/p-2", 30, 874, 3.2), (f"{a}/blog/post-1", 20, 9299, 7.7)])
    # Ninguna de sus URLs esta en el alcance (no indexable y fuera del censo).
    g3 = _aviso(s, jid, "canon", 900, [(f"{a}/canon", 500, 5000, 1.0), (f"{a}/fuera", 400, 4000, 2.0)])
    s.commit()
    s.close()
    return jid, g1, g2, g3


def test_grupos_por_clics_con_tope_y_solo_del_alcance(tmp_path, monkeypatch):
    Sesion = _motor(tmp_path)
    jid, g1, g2, g3 = _sembrar_grupos(Sesion)
    s = Sesion()
    alcance = enr.urls_del_alcance(s, jid, {})
    assert can.grupos_canibalizacion(s, jid, alcance) == [g1, g2]       # g3: nada en el alcance
    monkeypatch.setattr(can, "MAX_GRUPOS", 1)
    assert can.grupos_canibalizacion(s, jid, alcance) == [g1]           # el de mas clics
    # Con una carpeta, entra el grupo con ALGUNA pagina dentro.
    blog = enr.urls_del_alcance(s, jid, {"carpeta": "/tienda/p-2"})
    monkeypatch.setattr(can, "MAX_GRUPOS", 300)
    assert can.grupos_canibalizacion(s, jid, blog) == [g2]
    assert can.grupos_canibalizacion(s, jid, []) == []
    s.close()


def test_el_estado_es_del_grupo_minimo_y_redondeado(tmp_path):
    Sesion = _motor(tmp_path)
    jid, g1, g2, _ = _sembrar_grupos(Sesion)
    s = Sesion()
    # La 157 cuelga el aviso de la URL menor del grupo: aqui, la del blog en los dos.
    ancla = s.query(Url.id).filter(Url.job_id == jid, Url.path == "/blog/post-1").scalar()
    e1, e2 = can.estados_canibalizacion(s, [g1, g2])
    assert e1.ambito == "consulta" and e1.url_id == ancla and e2.url_id == ancla
    assert e1.datos["busqueda"] == "zapatos rojos" and e1.datos["reparto_por"] == "clics"
    primera, segunda = e1.datos["paginas"]
    assert set(primera) == {"ruta", "parte_pct", "clics", "impresiones", "posicion", "indexable",
                            "titulo", "h1", "contenido"}
    # La ruta, no la URL: con y sin www es el mismo estado (decision 169).
    assert primera["ruta"] == "/tienda/p-0" and segunda["ruta"] == "/blog/post-1"
    assert (primera["parte_pct"], primera["clics"], primera["impresiones"], primera["posicion"]) == (60, 180, 2000, 1)
    assert e2.datos["paginas"][1]["impresiones"] == 9300 and e2.datos["paginas"][0]["impresiones"] == 870
    # El h1 que se pinta y el contenido recortado.
    assert primera["h1"] == "H1 0" and len(primera["contenido"]) == can.CARACTERES_CONTENIDO
    # Un aviso que ya no existe no da estado.
    assert can.estados_canibalizacion(s, [g1, 999999]) and len(can.estados_canibalizacion(s, [g1, 999999])) == 1
    s.close()


@pytest.mark.parametrize("n,esperado", [(None, None), (0, 0), (16, 16), (99.6, 100), (874, 870),
                                        (1678, 1700), (9299, 9300), (59558, 60000)])
def test_redondear(n, esperado):
    assert can.redondear(n) == esperado


def test_estimar_una_llamada_por_grupo_con_las_dos_preguntas(tmp_path, monkeypatch, capa_activa):
    from shared.decisiones import CapaDecisiones
    from shared.preguntas import obtener

    Sesion = _motor(tmp_path)
    jid, g1, g2, _ = _sembrar_grupos(Sesion)
    s = Sesion()
    e = enr.estimar(s, s.get(Job, jid), ["canibalizacion"], {})
    pm = e["por_modulo"]["canibalizacion"]
    assert pm["urls"] == 2 and pm["unidad"] == "grupos" and pm["llamadas"] == 2
    assert pm["decisiones"] == 4
    # Decision 162: tokens = caracteres / 4 + 250 por llamada, con las DOS preguntas.
    capa = CapaDecisiones(None)
    preguntas = [obtener(n) for n in PREGUNTAS]
    tokens = sum(capa._tokens_estimados(capa._cuerpo(x, preguntas))
                 for x in can.estados_canibalizacion(s, [g1, g2]))
    assert pm["tokens_estimados"] == tokens
    assert e["coste_estimado_usd"] == pytest.approx(tokens * 0.042 / 1_000_000, abs=1e-8)
    s.close()


def test_de_punta_a_punta_y_reanudar_por_estado(tmp_path, capa_activa, fake_llm):
    from shared.decisiones import clave_estado

    Sesion = _motor(tmp_path)
    jid, g1, g2, _ = _sembrar_grupos(Sesion)
    fake = fake_llm(coste=0.0001)
    rid = _run(Sesion, jid, modulos=("canibalizacion",))
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport) == enr.COMPLETADA
    filas = _decisiones(Sesion, rid)
    # Dos grupos con la misma ancla: dos llamadas, cada una con las dos preguntas.
    assert fake.llamadas == 2
    assert all(set(p["questions"]) == set(PREGUNTAS) for p in fake.peticiones)
    assert {p["state"]["busqueda"] for p in fake.peticiones} == {"zapatos rojos", "zapatos"}
    assert len(filas) == 4 and {f.ambito for f in filas} == {"consulta"}
    assert len({f.url_id for f in filas}) == 1 and len({f.clave for f in filas}) == 2
    assert {f.estado for f in filas} == {"incierto"}                    # sin umbral (84)
    r = _leer(Sesion, rid)
    assert r.progreso["procesadas"] == 2 and r.progreso["total"] == 2

    # Reanudar tras un corte: un grupo ya hecho en ESTA ejecucion (por la clave
    # de su estado; la URL no basta porque los dos cuelgan de la misma).
    fake2 = fake_llm(coste=0.0001)
    rid2 = _run(Sesion, jid, modulos=("canibalizacion",))
    s = Sesion()
    (e1,) = can.estados_canibalizacion(s, [g1])
    for p in PREGUNTAS:
        s.add(Decision(job_id=jid, url_id=e1.url_id, run_id=rid2, ambito="consulta",
                       clave=clave_estado(e1.datos), pregunta=p, version_pregunta=1, estado="incierto",
                       origen="modelo", modelo="typesafe/jev-1.13", coste_usd=0.0))
    s.commit()
    s.close()
    assert enr.ejecutar(rid2, sesion_factory=Sesion, transport=fake2.transport) == enr.COMPLETADA
    # El otro grupo sale de la cache de la primera ejecucion: cero llamadas, y
    # el hecho no se repite.
    assert fake2.llamadas == 0
    assert len(_decisiones(Sesion, rid2)) == 4
    assert _leer(Sesion, rid2).progreso["procesadas"] == 2


def test_la_recomendacion_solo_cuenta_donde_son_sustitutas(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import enriquecido
    from shared.database import get_session

    Sesion = _motor(tmp_path)
    jid, g1, g2, _ = _sembrar_grupos(Sesion)
    app = FastAPI()
    app.include_router(enriquecido.router)

    def _sesion():
        s = Sesion()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _sesion
    api = TestClient(app)
    rid = _run(Sesion, jid, modulos=("canibalizacion",))
    s = Sesion()
    e1, e2 = can.estados_canibalizacion(s, [g1, g2])
    for e, (sust, rec) in ((e1, ("true", "fusionar")), (e2, ("false", "dejar"))):
        for p, r in zip(PREGUNTAS, (sust, rec)):
            s.add(Decision(job_id=jid, url_id=e.url_id, run_id=rid, ambito="consulta",
                           clave=("a" if e is e1 else "b") * 64, pregunta=p, version_pregunta=1,
                           estado="incierto", respuesta=r, origen="modelo", modelo="typesafe/jev-1.13",
                           coste_usd=0.0001, estado_enviado=e.datos))
    run = s.get(EnrichmentRun, rid)
    run.estado = enr.COMPLETADA
    s.commit()
    s.close()
    res = api.get(f"/api/jobs/{jid}/enrichment/{rid}").json()["resultados"]
    rec, sust = res["recomendacion_canibalizacion"], res["paginas_sustitutas"]
    assert sust["por_respuesta"] == {"true": 1, "false": 1} and "solo_si" not in sust
    assert rec["por_respuesta"] == {"fusionar": 1} and rec["total"] == 1 and rec["no_aplica"] == 1
    assert rec["solo_si"] == ["paginas_sustitutas", "true"]
    assert rec["coste_usd"] == pytest.approx(0.0002)                  # se pagaron las dos
    d = api.get(f"/api/jobs/{jid}/enrichment/{rid}/decisions",
                params={"pregunta": "recomendacion_canibalizacion"}).json()
    assert d["total"] == 1 and d["items"][0]["busqueda"] == "zapatos rojos"
    todas = api.get(f"/api/jobs/{jid}/enrichment/{rid}/decisions").json()
    assert todas["total"] == 4 and {x["busqueda"] for x in todas["items"]} == {"zapatos rojos", "zapatos"}


def test_el_efecto_en_el_aviso_esta_apagado_sin_umbral():
    from shared.decisiones import Decision as D

    def d(p, r, estado):
        return D(pregunta=p, version_pregunta=1, estado=estado, origen="modelo", modelo="m", respuesta=r)

    # Sin umbral todo es incierto (84): no rebaja ni confirma nada.
    assert can.efecto_en_el_aviso(d("paginas_sustitutas", "false", "incierto"), None) is None
    assert can.efecto_en_el_aviso(d("paginas_sustitutas", "true", "incierto"),
                                  d("recomendacion_canibalizacion", "fusionar", "decidido")) is None
    # Preparado para cuando lo haya.
    assert can.efecto_en_el_aviso(d("paginas_sustitutas", "false", "decidido"), None)["efecto"] == "rebajar"
    assert can.efecto_en_el_aviso(d("paginas_sustitutas", "true", "decidido"),
                                  d("recomendacion_canibalizacion", "fusionar", "incierto")) == {
        "efecto": "confirmar", "recomendacion": None}
    assert can.efecto_en_el_aviso(d("paginas_sustitutas", "true", "decidido"),
                                  d("recomendacion_canibalizacion", "fusionar", "decidido"))["recomendacion"] == "fusionar"


def test_las_preguntas_sin_umbral_y_la_condicion_bien_puesta():
    from shared.preguntas import CATALOGO, Pregunta, _comprobar_condiciones

    sust, rec = CATALOGO["paginas_sustitutas"], CATALOGO["recomendacion_canibalizacion"]
    assert sust.umbral is None and rec.umbral is None and enr.REGISTRO["canibalizacion"].ejemplo
    assert rec.tipo == "choice" and set(rec.opciones) == {"fusionar", "diferenciar", "canonicalizar", "dejar"}
    assert rec.solo_si == ("paginas_sustitutas", "true")
    # La condicion no viaja: no cambia lo enviado ni la huella.
    assert "solo_si" not in json.dumps(rec.a_api())
    base = dict(nombre="x", tipo="noul", version=1, instrucciones="¿x?")
    with pytest.raises(ValueError, match="no existe"):
        _comprobar_condiciones({"x": Pregunta(**base, solo_si=("nada", "true"))})
    with pytest.raises(ValueError, match="no puede responder"):
        _comprobar_condiciones({"x": Pregunta(**base, solo_si=("paginas_sustitutas", "fusionar")),
                                "paginas_sustitutas": sust})


def test_extraer_el_juego_de_grupos_sin_repetidos(tmp_path):
    """La 169 vio que la misma pagina con y sin www inflaba la muestra: la misma
    busqueda con las mismas rutas en otro censo (u otro host) es un ejemplo."""
    import importlib.util
    from pathlib import Path

    ruta = Path(__file__).resolve().parent.parent / "scripts" / "evaluar_decisiones.py"
    spec = importlib.util.spec_from_file_location("evaluar_decisiones_script", ruta)
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)

    Sesion = _motor(tmp_path)
    jid, g1, g2, _ = _sembrar_grupos(Sesion)
    # Un segundo censo del sitio con www: las mismas busquedas y rutas.
    s = Sesion()
    viejo = s.get(Job, jid)
    job2 = Job(name="censo www", status="completed", seeds=["https://www.acme.com/"], config={},
               cliente_id=viejo.cliente_id)
    s.add(job2)
    s.flush()
    for i, u in enumerate(s.query(Url).filter(Url.job_id == jid).all()):
        s.add(Url(job_id=job2.id, url=u.url.replace("://acme.com", "://www.acme.com"),
                  url_hash=f"{5000 + i:064x}", host="www.acme.com", path=u.path, scheme="https",
                  is_internal=u.is_internal, status_code=u.status_code, status_group=u.status_group,
                  is_html=u.is_html, resource_type=u.resource_type, content_type=u.content_type,
                  indexable=u.indexable))
    s.flush()
    w = "https://www.acme.com"
    _aviso(s, job2.id, "zapatos rojos", 310, [(f"{w}/tienda/p-0", 190, 2100, 1.4), (f"{w}/blog/post-1", 120, 1449, 2.6)])
    _aviso(s, job2.id, "botas", 40, [(f"{w}/tienda/p-2", 20, 500, 2.0), (f"{w}/blog/post-5", 20, 400, 3.0)])
    s.commit()
    j2 = job2.id
    filas = S.extraer(s, "paginas_sustitutas", [str(jid), str(j2)], n=10, semilla=0)
    assert sorted(f["estado"]["busqueda"] for f in filas) == ["botas", "zapatos", "zapatos rojos"]
    assert all(f["ambito"] == "consulta" and f["grupo"] and f["etiqueta"] is None for f in filas)
    assert all("+" in f["plantilla"] or "|" in f["plantilla"] for f in filas)
    rec = S.extraer(s, "recomendacion_canibalizacion", [str(jid)], n=10, semilla=0)
    assert len(rec) == 2 and "paginas_sustitutas" in rec[0]["nota"]
    # Determinista con la semilla.
    assert [f["id"] for f in S.extraer(s, "paginas_sustitutas", [str(jid), str(j2)], n=2, semilla=3)] == \
        [f["id"] for f in S.extraer(s, "paginas_sustitutas", [str(jid), str(j2)], n=2, semilla=3)]
    s.close()
