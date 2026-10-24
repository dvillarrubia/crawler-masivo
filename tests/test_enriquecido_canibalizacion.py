"""Canibalizacion juzgada (#18, #169 punto 3, decision 175), EJECUTADA contra
SQLite y el FakeLLM: nada sale a la red (decisiones 84 y 85).

Lo que hay que sujetar:

- la unidad es el PAR (busqueda + dos de sus URLs), no la URL ni el grupo: un
  aviso de tres paginas son tres pares, una URL en dos avisos son dos estados,
  y reanudar reconoce lo hecho por el estado;
- el filtro coge los pares de los avisos de la 157 con alguna URL en el
  alcance, por clics, con tope y SOLO del mismo idioma; el estado lleva rutas
  ordenadas, cifras redondeadas, por que no es indexable y su canonica;
- la recomendacion (con `solo_si`) va en SU llamada, en produccion como en la
  evaluacion, y la estimacion lo cuenta igual;
- la recomendacion solo se cuenta donde las paginas son sustitutas;
- el efecto en el aviso esta apagado mientras no haya umbral.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid

import pytest

pytest.importorskip("httpx")

import shared.enriquecido as enr  # noqa: E402
import shared.enriquecido_canibalizacion as can  # noqa: E402
from shared.models import Decision, EnrichmentRun, Hreflang, HtmlMeta, Issue, Job, Url  # noqa: E402
from test_enriquecido import _decisiones, _leer, _motor, _run, capa_activa, sembrar  # noqa: E402,F401

PREGUNTAS = ["paginas_sustitutas", "recomendacion_canibalizacion"]


def _aviso(s, jid, consulta, clics, urls, imp=None, reparto_por="clics"):
    """Un `canibalizacion_por_consulta` como lo escribe la 157: cuelga de la
    URL menor del grupo y lleva las URLs ordenadas por parte."""
    ids = {u: i for i, u in s.query(Url.id, Url.url).filter(Url.job_id == jid)}
    ancla = min(u for u, *_ in urls)
    total = sum(c for _, c, *_ in urls) or 1
    i = Issue(job_id=jid, url_id=ids.get(ancla) or min(ids.values()), issue_type="canibalizacion_por_consulta",
              severity="warning" if clics >= 100 else "info", details={
                  "consulta": consulta, "clics": clics, "impresiones": imp or clics * 10,
                  "reparto_por": reparto_por,
                  "urls": [{"url": u, "clics": c, "impresiones": im, "parte": round(c / total, 3),
                            "posicion": pos, "en_censo": True, "indexable": True, "estado": "Indexable",
                            "plantilla": "/tienda/:slug" if "tienda" in u else "/blog/:slug"}
                           for u, c, im, pos in urls]})
    s.add(i)
    s.flush()
    return i.id


A = "https://acme.com"


def _sembrar_grupos(Sesion):
    jid, _ = sembrar(Sesion, n=6)
    s = Sesion()
    por_ruta = {u.path: u for u in s.query(Url).filter(Url.job_id == jid)}
    # Idiomas: p-4 y post-3 en castellano (`<html lang>`), post-5 en aleman
    # solo por su hreflang a si misma (censo de antes de `html_lang`).
    s.get(HtmlMeta, por_ruta["/tienda/p-4"].id).html_lang = "es-ES"
    s.get(HtmlMeta, por_ruta["/blog/post-3"].id).html_lang = "es"
    s.add(Hreflang(url_id=por_ruta["/blog/post-5"].id, lang="de-DE", href=f"{A}/blog/post-5"))
    s.add(Hreflang(url_id=por_ruta["/blog/post-5"].id, lang="es-ES", href=f"{A}/blog/post-3"))
    # /canon declara como canonica una URL que no esta en el aviso.
    por_ruta["/canon"].indexability_status = "Canonicalised"
    s.add(HtmlMeta(url_id=por_ruta["/canon"].id, title="Canon", canonical_href=f"{A}/tienda/p-0-nueva"))
    g1 = _aviso(s, jid, "zapatos rojos", 300, [(f"{A}/tienda/p-0", 180, 2049, 1.4),
                                               (f"{A}/blog/post-1", 120, 1449, 2.6)])
    # La misma URL ancla en otro grupo: es otro estado, no la misma unidad.
    g2 = _aviso(s, jid, "zapatos", 50, [(f"{A}/tienda/p-2", 30, 874, 3.2), (f"{A}/blog/post-1", 20, 9299, 7.7)])
    # Ninguna de sus URLs esta en el alcance (no indexable y fuera del censo).
    g3 = _aviso(s, jid, "canon", 900, [(f"{A}/canon", 500, 5000, 1.0), (f"{A}/fuera", 400, 4000, 2.0)])
    # Tres paginas: tres pares, y los dos con la de aleman no se preguntan.
    g4 = _aviso(s, jid, "botas", 200, [(f"{A}/tienda/p-4", 80, 900, 2.0), (f"{A}/blog/post-3", 70, 800, 2.5),
                                       (f"{A}/blog/post-5", 50, 700, 3.0)])
    # Una canonicalizada contra una indexable.
    g5 = _aviso(s, jid, "zapato canon", 40, [(f"{A}/blog/post-1", 25, 300, 4.0), (f"{A}/canon", 15, 200, 6.0)])
    s.commit()
    s.close()
    return jid, g1, g2, g3, g4, g5


def test_pares_por_clics_con_tope_del_alcance_y_del_mismo_idioma(tmp_path, monkeypatch):
    Sesion = _motor(tmp_path)
    jid, g1, g2, g3, g4, g5 = _sembrar_grupos(Sesion)
    par = can.id_de_par
    s = Sesion()
    alcance = enr.urls_del_alcance(s, jid, {})
    # g3: nada en el alcance; g4: de sus tres pares solo el que no lleva la de aleman.
    assert can.pares_canibalizacion(s, jid, alcance) == [par(g1, 0), par(g4, 0), par(g2, 0), par(g5, 0)]
    assert can.deshacer_par(par(g4, 2)) == (g4, 2)
    monkeypatch.setattr(can, "MAX_GRUPOS", 2)
    assert can.pares_canibalizacion(s, jid, alcance) == [par(g1, 0), par(g4, 0)]   # las de mas clics
    # Con una carpeta, entra el par con ALGUNA pagina dentro.
    tienda = enr.urls_del_alcance(s, jid, {"carpeta": "/tienda/p-2"})
    monkeypatch.setattr(can, "MAX_GRUPOS", 300)
    assert can.pares_canibalizacion(s, jid, tienda) == [par(g2, 0)]
    assert can.pares_canibalizacion(s, jid, []) == []
    s.close()


def test_idioma_de_la_ruta_y_sin_saberlo_se_pregunta():
    assert can._idioma_de_ruta("https://a.com/blog/de/fehmarn") == "de"
    assert can._idioma_de_ruta("https://a.com/tv/x") == ""
    assert can.mismo_idioma("es", "") and can.mismo_idioma("es", "es") and not can.mismo_idioma("en", "de")


def test_las_urls_se_buscan_por_hash_con_el_indice_unico(tmp_path):
    """`urls.url` no tiene indice (#244): se busca por (job_id, url_hash) y solo
    lo que no case por hash, por URL."""
    from sqlalchemy import event

    Sesion = _motor(tmp_path)
    jid, *_ = _sembrar_grupos(Sesion)
    s = Sesion()
    buena = s.query(Url).filter(Url.job_id == jid, Url.path == "/tienda/p-0").one()
    buena.url_hash = hashlib.sha256(buena.url.encode()).hexdigest()
    s.commit()
    sql = []
    event.listen(s.get_bind(), "before_cursor_execute", lambda *a: sql.append(a[2]))
    assert can._ids_por_url(s, jid, {buena.url}) == {buena.url: buena.id}
    assert len(sql) == 1 and "url_hash IN" in sql[0]
    sql.clear()
    otra = f"{A}/blog/post-1"                    # hash de prueba: cae a la URL
    assert otra in can._ids_por_url(s, jid, {buena.url, otra})
    assert len(sql) == 2 and "url_hash IN" in sql[0] and "urls.url IN" in sql[1]
    s.close()


def test_el_estado_es_del_par_minimo_y_redondeado(tmp_path):
    Sesion = _motor(tmp_path)
    jid, g1, g2, g3, g4, g5 = _sembrar_grupos(Sesion)
    s = Sesion()
    par = can.id_de_par
    # El par cuelga de su URL menor: aqui, la del blog en los dos.
    ancla = s.query(Url.id).filter(Url.job_id == jid, Url.path == "/blog/post-1").scalar()
    e1, e2 = can.estados_canibalizacion(s, [par(g1, 0), par(g2, 0)])
    assert e1.ambito == "consulta" and e1.url_id == ancla and e2.url_id == ancla
    assert e1.datos["busqueda"] == "zapatos rojos" and e1.datos["reparto_por"] == "clics"
    assert e1.datos["otras_paginas"] == 0
    primera, segunda = e1.datos["paginas"]
    assert set(primera) == {"ruta", "parte_pct", "clics", "impresiones", "posicion", "no_indexable",
                            "canonica", "titulo", "h1", "contenido"}
    # Ordenadas por RUTA (no por parte) y la ruta, no la URL (decision 169).
    assert primera["ruta"] == "/blog/post-1" and segunda["ruta"] == "/tienda/p-0"
    assert (segunda["parte_pct"], segunda["clics"], segunda["impresiones"], segunda["posicion"]) == (60, 180, 2000, 1)
    assert segunda["no_indexable"] is None and segunda["canonica"] is None
    assert e2.datos["paginas"][0]["impresiones"] == 9300 and e2.datos["paginas"][1]["impresiones"] == 870
    # El h1 que se pinta y el contenido recortado.
    assert segunda["h1"] == "H1 0" and len(segunda["contenido"]) == can.CARACTERES_CONTENIDO
    # Un grupo de tres: el par sabe que hay una pagina mas.
    (e4,) = can.estados_canibalizacion(s, [par(g4, 0)])
    assert e4.datos["otras_paginas"] == 1 and [p["ruta"] for p in e4.datos["paginas"]] == ["/blog/post-3", "/tienda/p-4"]
    # La canonicalizada dice por que y a donde apunta (#244: sin eso, Jev
    # recomendaria canonicalizar lo que ya lo esta).
    (e5,) = can.estados_canibalizacion(s, [par(g5, 0)])
    canon = next(p for p in e5.datos["paginas"] if p["ruta"] == "/canon")
    assert canon["no_indexable"] == "Canonicalised" and canon["canonica"] == "/tienda/p-0-nueva"
    # Un aviso que ya no existe, o un par que no tiene, no da estado.
    assert len(can.estados_canibalizacion(s, [par(g1, 0), par(999999, 0), par(g1, 5)])) == 1
    s.close()


def test_el_orden_del_aviso_no_cambia_la_clave(tmp_path):
    """Un reparto casi al 50 % se invierte de un recrawl a otro con las mismas
    cifras redondeadas: la clave del estado no puede cambiar (#244)."""
    from shared.decisiones import clave_estado

    Sesion = _motor(tmp_path)
    jid, *_ = _sembrar_grupos(Sesion)
    s = Sesion()
    a = _aviso(s, jid, "kumara", 100, [(f"{A}/tienda/p-0", 51, 500, 2.0), (f"{A}/blog/post-1", 49, 500, 2.0)])
    b = _aviso(s, jid, "kumara", 100, [(f"{A}/blog/post-1", 51, 500, 2.0), (f"{A}/tienda/p-0", 49, 500, 2.0)])
    for i in (a, b):            # mismas cifras redondeadas: 50 % las dos
        d = dict(s.get(Issue, i).details)
        d["urls"] = [dict(u, parte=0.5, clics=50) for u in d["urls"]]
        s.get(Issue, i).details = d
    s.commit()
    ea, eb = can.estados_canibalizacion(s, [can.id_de_par(a, 0), can.id_de_par(b, 0)])
    assert clave_estado(ea.datos) == clave_estado(eb.datos)
    s.close()


def test_reparto_por_impresiones_viaja_en_el_estado(tmp_path):
    Sesion = _motor(tmp_path)
    jid, *_ = _sembrar_grupos(Sesion)
    s = Sesion()
    i = _aviso(s, jid, "sin clics", 0, [(f"{A}/tienda/p-0", 0, 600, 5.0), (f"{A}/blog/post-1", 0, 400, 6.0)],
               imp=1000, reparto_por="impresiones")
    d = dict(s.get(Issue, i).details)
    d["urls"] = [dict(u, parte=round(u["impresiones"] / 1000, 3)) for u in d["urls"]]
    s.get(Issue, i).details = d
    s.commit()
    (e,) = can.estados_canibalizacion(s, [can.id_de_par(i, 0)])
    assert e.datos["reparto_por"] == "impresiones"
    assert sorted(p["parte_pct"] for p in e.datos["paginas"]) == [40, 60]
    s.close()


@pytest.mark.parametrize("n,esperado", [(None, None), (0, 0), (16, 16), (99.6, 100), (874, 870),
                                        (1678, 1700), (9299, 9300), (59558, 60000)])
def test_redondear(n, esperado):
    assert can.redondear(n) == esperado


def test_estimar_una_llamada_por_pregunta_condicionada(tmp_path, monkeypatch, capa_activa):
    from shared.decisiones import CapaDecisiones
    from shared.preguntas import obtener

    Sesion = _motor(tmp_path)
    jid, g1, g2, g3, g4, g5 = _sembrar_grupos(Sesion)
    s = Sesion()
    e = enr.estimar(s, s.get(Job, jid), ["canibalizacion"], {})
    pm = e["por_modulo"]["canibalizacion"]
    # Cuatro pares; la recomendacion (solo_si) va en su propia llamada: 8.
    assert pm["urls"] == 4 and pm["unidad"] == "grupos" and pm["llamadas"] == 8
    assert pm["decisiones"] == 8
    # Decision 162: tokens = caracteres / 4 + 250 POR LLAMADA, cada pregunta con su estado.
    capa = CapaDecisiones(None)
    tokens = sum(capa._tokens_estimados(capa._cuerpo(x, [obtener(n)]))
                 for x in can.estados_canibalizacion(s, can.pares_canibalizacion(
                     s, jid, enr.urls_del_alcance(s, jid, {})))
                 for n in PREGUNTAS)
    assert pm["tokens_estimados"] == tokens
    assert e["coste_estimado_usd"] == pytest.approx(tokens * 0.042 / 1_000_000, abs=1e-8)
    s.close()


def test_la_pregunta_condicionada_va_sola_y_las_demas_juntas():
    """#244: leida en la misma llamada, la recomendacion («han sido juzgadas
    sustitutas») empujaria a la primera; y la evaluacion pregunta una a una."""
    from shared.decisiones import _partir
    from shared.preguntas import CATALOGO, obtener

    sust, rec = obtener("paginas_sustitutas"), obtener("recomendacion_canibalizacion")
    assert _partir([sust, rec]) == [[sust], [rec]]
    otras = [p for p in CATALOGO.values() if p.solo_si is None][:2]
    assert _partir(otras) == [otras]
    otro_modelo = dataclasses.replace(otras[1], modelo="typesafe/otro-1.0")
    assert _partir([otras[0], otro_modelo]) == [[otras[0]], [otro_modelo]]


def test_de_punta_a_punta_y_reanudar_por_estado(tmp_path, capa_activa, fake_llm, monkeypatch):
    import importlib.util
    from pathlib import Path

    from shared.decisiones import clave_estado

    Sesion = _motor(tmp_path)
    jid, g1, g2, g3, g4, g5 = _sembrar_grupos(Sesion)
    fake = fake_llm(coste=0.0001)
    rid = _run(Sesion, jid, modulos=("canibalizacion",))
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport) == enr.COMPLETADA
    filas = _decisiones(Sesion, rid)
    # Cuatro pares x dos preguntas, cada una en SU llamada.
    assert fake.llamadas == 8
    assert sorted(tuple(p["questions"]) for p in fake.peticiones) == sorted(
        [("paginas_sustitutas",)] * 4 + [("recomendacion_canibalizacion",)] * 4)
    assert {p["state"]["busqueda"] for p in fake.peticiones} == {"zapatos rojos", "zapatos", "botas",
                                                                 "zapato canon"}
    assert len(filas) == 8 and {f.ambito for f in filas} == {"consulta"}
    assert len({f.clave for f in filas}) == 4
    assert {f.estado for f in filas} == {"incierto"}                    # sin umbral (84)
    r = _leer(Sesion, rid)
    assert r.progreso["procesadas"] == 4 and r.progreso["total"] == 4

    # Lo calibrado es lo que corre: el cuerpo de `responder` (evaluacion) para
    # la primera pregunta es el mismo que el de la ejecucion.
    ruta = Path(__file__).resolve().parent.parent / "scripts" / "evaluar_decisiones.py"
    spec = importlib.util.spec_from_file_location("evaluar_decisiones_script", ruta)
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    s = Sesion()
    juego = S.extraer(s, "paginas_sustitutas", [str(jid)], n=10, semilla=0)
    s.close()
    fake_eval = fake_llm(coste=0.0001)
    from shared.decisiones import CapaDecisiones
    from shared.llm import ClienteOpenRouter, ConfigLLM

    capa = CapaDecisiones(lambda: ClienteOpenRouter(ConfigLLM(api_key="sk-de-prueba"),
                                                    transport=fake_eval.transport))
    monkeypatch.setenv("ENRICHMENT_TOKEN", "x" * 16)
    S.responder(juego, confirmar=True, capa=capa)
    def prompt(p):          # lo que lee el modelo (sin session_id ni user)
        return json.dumps({k: p.get(k) for k in ("model", "state", "questions")}, sort_keys=True)

    en_prod = {prompt(p) for p in fake.peticiones if "paginas_sustitutas" in p["questions"]}
    en_eval = {prompt(p) for p in fake_eval.peticiones}
    assert en_eval and en_eval <= en_prod

    # Reanudar tras un corte: un par ya hecho en ESTA ejecucion (por la clave
    # de su estado; la URL no basta porque varios cuelgan de la misma).
    fake2 = fake_llm(coste=0.0001)
    rid2 = _run(Sesion, jid, modulos=("canibalizacion",))
    s = Sesion()
    (e1,) = can.estados_canibalizacion(s, [can.id_de_par(g1, 0)])
    for p in PREGUNTAS:
        s.add(Decision(job_id=jid, url_id=e1.url_id, run_id=rid2, ambito="consulta",
                       clave=clave_estado(e1.datos), pregunta=p, version_pregunta=1, estado="incierto",
                       origen="modelo", modelo="typesafe/jev-1.13", coste_usd=0.0))
    s.commit()
    s.close()
    assert enr.ejecutar(rid2, sesion_factory=Sesion, transport=fake2.transport) == enr.COMPLETADA
    # Los otros salen de la cache de la primera ejecucion: cero llamadas, y
    # el hecho no se repite.
    assert fake2.llamadas == 0
    assert len(_decisiones(Sesion, rid2)) == 8
    assert _leer(Sesion, rid2).progreso["procesadas"] == 4


def test_la_recomendacion_solo_cuenta_donde_son_sustitutas(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import enriquecido
    from shared.database import get_session

    Sesion = _motor(tmp_path)
    jid, g1, g2, *_ = _sembrar_grupos(Sesion)
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
    e1, e2 = can.estados_canibalizacion(s, [can.id_de_par(g1, 0), can.id_de_par(g2, 0)])
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
    # Las dos rutas del par: la URL de la fila es solo donde cuelga.
    assert d["items"][0]["rutas"] == ["/blog/post-1", "/tienda/p-0"]
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
    # #244: las busquedas de marca no pueden ser a la vez «no sustitutas» en una
    # y el caso de «dejar» en la otra (la rama no se leeria nunca).
    assert "brand" in sust.instrucciones and "brand" not in rec.instrucciones
    assert "brand" not in rec.opciones["dejar"]
    # Las dos explican `reparto_por`, `no_indexable` y `canonica`.
    for p in (sust, rec):
        assert all(c in p.instrucciones for c in ("reparto_por", "no_indexable", "canonica"))
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
    jid, *_ = _sembrar_grupos(Sesion)
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
    # «zapatos rojos» con www tiene las mismas rutas: un ejemplo. «botas» del
    # segundo censo es OTRO par (otras rutas): otro ejemplo.
    assert sorted(f["estado"]["busqueda"] for f in filas) == ["botas", "botas", "zapato canon", "zapatos",
                                                              "zapatos rojos"]
    assert all(f["ambito"] == "consulta" and f["grupo"] and f["etiqueta"] is None for f in filas)
    assert all("+" in f["plantilla"] or "|" in f["plantilla"] for f in filas)
    rec = S.extraer(s, "recomendacion_canibalizacion", [str(jid)], n=10, semilla=0)
    assert len(rec) == 4 and "paginas_sustitutas" in rec[0]["nota"]
    # Determinista con la semilla.
    assert [f["id"] for f in S.extraer(s, "paginas_sustitutas", [str(jid), str(j2)], n=2, semilla=3)] == \
        [f["id"] for f in S.extraer(s, "paginas_sustitutas", [str(jid), str(j2)], n=2, semilla=3)]
    s.close()
