"""Capa de decisiones (`shared/decisiones.py`, #7) con un `FakeLLM`.

Agrupacion, cache (memoria, Redis simulado y BD), presupuesto en sus tres
niveles, degradacion ante 429/503/402 y confianza normalizada. Ningun test
llama a la red.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import json
import math

import pytest

pytest.importorskip("httpx")

from sqlalchemy import BigInteger, create_engine  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from fake_llm import FakeLLM  # noqa: E402
from shared import decisiones as dec  # noqa: E402
from shared.decisiones import (  # noqa: E402
    CacheMemoria, CapaDecisiones, ContadoresMemoria, Estado, Observabilidad, Presupuesto,
    clave_cache, clave_estado, fusionar_resumen, interpretar,
)
from shared.llm import ClienteOpenRouter, ConfigLLM  # noqa: E402
from shared.preguntas import CATALOGO, Pregunta  # noqa: E402


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def P(nombre, tipo="noul", version=1, umbral=None, opciones=None, modelo="typesafe/jev-1.13"):
    if tipo == "choice" and opciones is None:
        opciones = {"a": "Opcion A", "b": "Opcion B", "c": None}
    if tipo == "score" and opciones is None:
        opciones = ["bajo", "medio", "alto"]
    return Pregunta(nombre=nombre, tipo=tipo, version=version, instrucciones=f"¿{nombre}?",
                    opciones=opciones, umbral=umbral, modelo=modelo)


def _capa(fake, **kw) -> CapaDecisiones:
    n = kw.pop("concurrencia", 1)
    cfg = ConfigLLM(api_key="sk-test", backoff_inicial_s=0.001, backoff_max_s=0.002,
                    concurrencia=n)
    return CapaDecisiones(lambda: ClienteOpenRouter(cfg, transport=fake.transport),
                          concurrencia=n, **kw)


def _preguntar(capa, estados, preguntas):
    return capa.preguntar_sync(estados, preguntas)


def E(texto, url_id=None):
    return Estado({"title": texto, "h1": texto}, url_id=url_id)


# --- agrupacion y cache ---------------------------------------------------------

def test_tres_preguntas_de_un_estado_son_una_llamada():
    fake = FakeLLM()
    capa = _capa(fake)
    ps = [P("a"), P("b", "choice"), P("c", "score")]
    res = _preguntar(capa, [E("Aviso legal")], ps)
    assert fake.llamadas == 1
    assert set(fake.peticiones[0]["questions"]) == {"a", "b", "c"}
    assert [d.pregunta for d in res[0]] == ["a", "b", "c"]
    assert all(d.origen == "modelo" for d in res[0])
    # La misma pregunta otra vez: de la cache de la ejecucion, sin llamar.
    res2 = _preguntar(capa, [E("Aviso legal")], ps)
    assert fake.llamadas == 1
    assert all(d.origen == "cache" and d.coste_usd == 0 for d in res2[0])
    assert capa.obs.aciertos_cache == 3


def test_con_dos_en_cache_la_peticion_lleva_solo_la_que_falta():
    cache = CacheMemoria()
    ps = [P("a"), P("b"), P("c")]
    _preguntar(_capa(FakeLLM(), cache=cache), [E("x")], ps[:2])
    fake = FakeLLM()
    capa = _capa(fake, cache=cache)  # otra ejecucion, misma cache (Redis)
    _preguntar(capa, [E("x")], ps)
    assert fake.llamadas == 1
    assert list(fake.peticiones[0]["questions"]) == ["c"]
    assert capa.obs.aciertos_cache == 2


def test_estados_identicos_se_preguntan_una_vez():
    """Bloques de plantilla: el mismo estado en 50 URLs es UNA llamada, pero
    cada URL tiene su decision (y su fila en `decisions`)."""
    fake = FakeLLM()
    capa = _capa(fake, concurrencia=4)
    estados = [E("Pie de pagina", url_id=i) for i in range(50)] + [E("Otro", url_id=99)]
    res = _preguntar(capa, estados, [P("a")])
    assert fake.llamadas == 2
    assert len(res) == 51 and res[0][0] is not res[1][0]
    assert {r[0].respuesta for r in res} == {"true"}


def test_cambiar_version_o_modelo_cambia_la_clave():
    ce = clave_estado({"title": "x"})
    assert clave_cache(ce, P("a")) != clave_cache(ce, P("a", version=2))
    assert clave_cache(ce, P("a")) != clave_cache(ce, P("a", modelo="typesafe/jev-1.14"))


# --- normalizacion ------------------------------------------------------------

def test_tokens_volatiles_no_cambian_la_clave():
    a = {"title": "Zapatillas", "body": "csrf=a8f7d6e5c4b3a2918f7e6d5c4b3a2918 hola"}
    b = {"title": "zapatillas ", "body": "csrf=ffffd6e5c4b3a2918f7e6d5c4b3a2918   hola"}
    assert clave_estado(a) == clave_estado(b)
    assert clave_estado({"t": "a.css?v=12345 x"}) == clave_estado({"t": "a.css?v=99 x"})
    assert clave_estado({"t": "visto 2026-10-13T10:00:00Z"}) == \
        clave_estado({"t": "visto 2026-10-14T11:22:33+02:00"})


def test_numeros_de_negocio_si_cambian_la_clave():
    assert clave_estado({"precio": "19,99 €"}) != clave_estado({"precio": "24,99 €"})
    # Una fecha sin hora puede ser la de una oferta: no se toca.
    assert clave_estado({"t": "Oferta hasta 2026-10-31"}) != \
        clave_estado({"t": "Oferta hasta 2026-11-30"})
    assert clave_estado({"sku": "AB12"}) != clave_estado({"sku": "AB13"})


def test_lo_enviado_conserva_caja_y_colapsa_espacios():
    fake = FakeLLM()
    _preguntar(_capa(fake), [Estado({"title": "  Aviso\n\n  LEGAL  "})], [P("a")])
    assert fake.peticiones[0]["state"] == {"title": "Aviso LEGAL"}


# --- confianza --------------------------------------------------------------------

def test_noul_confianza_simetrica():
    p = P("a")
    assert interpretar(p, {"type": "noul", "noul": 0.2}) == \
        ("false", {"true": 0.2, "false": pytest.approx(0.8)}, pytest.approx(0.8))


def test_choice_sin_confianza_ni_probabilidades_queda_incierto():
    fake = FakeLLM(respuestas={"c": {"type": "choice", "choice": "a"}})
    p = P("c", "choice", umbral=0.5)
    d = _preguntar(_capa(fake), [E("x")], [p])[0][0]
    assert d.respuesta == "a" and d.confianza is None and d.estado == "incierto"


def test_choice_sin_confidence_usa_la_probabilidad_maxima():
    r, probs, conf = interpretar(P("c", "choice"), {
        "type": "choice", "choice": "b", "probabilities": {"a": 0.1, "b": 0.7, "c": 0.2}})
    assert r == "b" and conf == pytest.approx(0.7)


def test_sin_umbral_calibrado_nada_es_decidido():
    fake = FakeLLM(p_noul=0.99)
    sin = _preguntar(_capa(fake), [E("x")], [P("a")])[0][0]
    con = _preguntar(_capa(fake), [E("y")], [P("b", umbral=0.9)])[0][0]
    assert sin.estado == "incierto" and con.estado == "decidido"


def test_respuesta_de_otro_tipo_o_ausente_queda_sin_decidir_y_no_se_cachea():
    fake = FakeLLM(respuestas={"b": {"type": "choice", "choice": "z"}})
    capa = _capa(fake)
    res = _preguntar(capa, [E("x")], [P("a"), P("b")])[0]
    assert res[0].respuesta == "true"
    assert res[1].estado == "sin_decidir" and res[1].motivo == "respuesta_incompleta"
    _preguntar(capa, [E("x")], [P("a"), P("b")])
    assert fake.llamadas == 2 and list(fake.peticiones[1]["questions"]) == ["b"]


def test_score():
    d = _preguntar(_capa(FakeLLM()), [E("x")], [P("s", "score", umbral=0.5)])[0][0]
    assert float(d.respuesta) == pytest.approx(1.9) and d.estado == "decidido"


# --- degradacion ------------------------------------------------------------------

def test_503_cuatro_veces_queda_sin_decidir_sin_excepcion():
    fake = FakeLLM(guion=[503] * 4)
    capa = _capa(fake)
    d = _preguntar(capa, [E("x")], [P("a")])[0][0]
    assert d.estado == "sin_decidir" and d.motivo == "servidor"
    assert capa.obs.estado == "degradado" and capa.obs.n_errores == 1


def test_429_y_luego_bien():
    fake = FakeLLM(guion=[429], retry_after_ms=1)
    capa = _capa(fake)
    d = _preguntar(capa, [E("x")], [P("a")])[0][0]
    assert d.respuesta == "true" and capa.obs.n_429 == 1 and capa.obs.reintentos == 1
    assert capa.obs.estado == "ok"


def test_402_corta_el_resto_de_la_ejecucion():
    fake = FakeLLM(guion=[402])
    capa = _capa(fake, concurrencia=1)
    res = _preguntar(capa, [E(f"pagina {i}") for i in range(5)], [P("a")])
    assert fake.llamadas == 1
    assert {r[0].estado for r in res} == {"sin_decidir"}
    assert {r[0].motivo for r in res} == {"sin_credito"}
    assert capa.obs.estado == "sin_credito"
    # Y la siguiente tanda ni lo intenta.
    _preguntar(capa, [E("otra")], [P("a")])
    assert fake.llamadas == 1


def test_400_solo_afecta_a_su_estado():
    fake = FakeLLM(guion=[400])
    res = _preguntar(_capa(fake), [E("uno"), E("dos")], [P("a")])
    assert res[0][0].motivo == "peticion_rechazada" and res[1][0].respuesta == "true"


def test_capa_desactivada_no_toca_la_red(monkeypatch):
    fake = FakeLLM(prohibido=True)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    capa = dec.capa_desde_entorno(transport=fake.transport)  # DECISIONS_ENABLED sin poner
    res = capa.preguntar_sync([E("x")], [P("a")])
    assert res[0][0].estado == "sin_decidir" and res[0][0].motivo == "desactivado"
    assert capa.obs.estado == "desactivado" and fake.intentos_prohibidos == 0


def test_activada_sin_clave_tambien_queda_desactivada(monkeypatch):
    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    capa = dec.capa_desde_entorno()
    assert capa.preguntar_sync([E("x")], [P("a")])[0][0].motivo == "desactivado"


def test_activada_con_clave_llama(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    capa = dec.capa_desde_entorno(transport=fake.transport, usuario="acme", job_id="j1")
    assert capa.preguntar_sync([E("x")], [P("a")])[0][0].respuesta == "true"
    assert fake.peticiones[0]["user"] == "acme" and fake.peticiones[0]["session_id"] == "j1"


# --- presupuesto ------------------------------------------------------------------

def test_tope_de_ejecucion_la_tercera_llamada_no_sale():
    """0,0004 $ por llamada y 0,001 de tope: la tercera ya no cabe. La reserva
    aprende del coste real: con solo el precio publicado (~1e-5 por llamada)
    habria salido y el tope se pasaria."""
    fake = FakeLLM(coste=0.0004)
    capa = _capa(fake, presupuesto=Presupuesto(ejecucion_usd=0.001))
    res = _preguntar(capa, [E(f"p{i}") for i in range(5)], [P("a")])
    assert fake.llamadas == 2
    assert [r[0].estado for r in res].count("sin_decidir") == 3
    assert {r[0].motivo for r in res[2:]} == {"presupuesto_ejecucion"}
    assert capa.obs.estado == "presupuesto_agotado"
    assert capa.presupuesto.gastado_ejecucion == pytest.approx(0.0008)


def test_max_llamadas():
    fake = FakeLLM()
    capa = _capa(fake, presupuesto=Presupuesto(max_llamadas=2))
    _preguntar(capa, [E(f"p{i}") for i in range(4)], [P("a")])
    assert fake.llamadas == 2


def test_tope_mensual_del_cliente_lo_comparten_las_ejecuciones():
    contadores = ContadoresMemoria()
    cid = str(uuid.uuid4())

    def presupuesto():
        return Presupuesto(cliente_mensual_usd=0.0005, cliente_id=cid, contadores=contadores)

    f1 = FakeLLM(coste=0.0004)
    _preguntar(_capa(f1, presupuesto=presupuesto()), [E("a")], [P("a")])
    f2 = FakeLLM(coste=0.0004)
    capa2 = _capa(f2, presupuesto=presupuesto())
    # Primera llamada de la segunda ejecucion: la reserva (estimada por
    # precio) cabe; al ajustar se pasa a 0,0008 y la siguiente ya no.
    res = _preguntar(capa2, [E("b"), E("c")], [P("a")])
    assert f1.llamadas == 1 and f2.llamadas == 1
    assert res[1][0].motivo == "presupuesto_cliente"
    assert contadores.valores[f"llm:coste:cliente:{cid}:{dec._mes()}"] == pytest.approx(0.0008)


def test_tope_global_mensual(monkeypatch):
    contadores = ContadoresMemoria()
    contadores.sembrar(f"llm:coste:global:{dec._mes()}", 5.0)
    fake = FakeLLM()
    capa = _capa(fake, presupuesto=Presupuesto(global_mensual_usd=5.0, contadores=contadores))
    d = _preguntar(capa, [E("x")], [P("a")])[0][0]
    assert fake.llamadas == 0 and d.motivo == "presupuesto_global"


def test_sin_usage_cost_se_estima_y_se_marca():
    fake = FakeLLM(coste=None, tokens_entrada=1_000_000)
    capa = _capa(fake, precio_usd_mtok=0.042)
    d = _preguntar(capa, [E("x")], [P("a")])[0][0]
    assert d.coste_estimado is True and d.coste_usd == pytest.approx(0.042)
    assert capa.obs.llamadas_coste_estimado == 1


def test_el_coste_de_una_llamada_se_reparte_entre_sus_preguntas():
    fake = FakeLLM(coste=0.0003)
    res = _preguntar(_capa(fake), [E("x")], [P("a"), P("b"), P("c")])[0]
    assert sum(d.coste_usd for d in res) == pytest.approx(0.0003)


def test_estimar_no_llama():
    fake = FakeLLM(prohibido=True)
    capa = _capa(fake)
    est = capa.estimar([E("x"), E("x"), E("y")], [P("a"), P("b")])
    assert est["estados_distintos"] == 2 and est["llamadas"] == 2
    assert est["tokens_estimados"] > 0 and est["coste_estimado_usd"] > 0
    assert fake.intentos_prohibidos == 0


# --- BD: cache duradera, filas y resumen --------------------------------------

@pytest.fixture()
def Sesion(tmp_path):
    from shared.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/d.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _job(Sesion, **kw):
    from shared.models import Job

    s = Sesion()
    j = Job(id=uuid.uuid4(), name="t", status="completed", seeds=["https://x.com/"],
            config={}, **kw)
    s.add(j)
    s.commit()
    s.close()
    return j.id


def test_un_recrawl_no_vuelve_a_pagar(Sesion):
    from shared.models import Decision as Fila, Job

    j1, j2 = _job(Sesion), _job(Sesion)
    f1 = FakeLLM()
    capa1 = _capa(f1, sesion_factory=Sesion, job_id=j1)
    estados = [E("Aviso legal", url_id=1), E("Producto", url_id=2)]
    res = _preguntar(capa1, estados, [P("a")])
    s = Sesion()
    assert capa1.guardar(s, estados, res) == 2
    s.commit()

    # Otro job, otra ejecucion, cache de Redis vacia: lo encuentra en la BD.
    f2 = FakeLLM(prohibido=True)
    capa2 = _capa(f2, sesion_factory=Sesion, job_id=j2)
    res2 = _preguntar(capa2, [E("Aviso  LEGAL", url_id=7)], [P("a")])
    assert f2.intentos_prohibidos == 0
    d = res2[0][0]
    assert d.origen == "cache" and d.respuesta == "true" and d.coste_usd == 0
    assert capa2.obs.aciertos_cache_bd == 1

    filas = s.query(Fila).filter(Fila.job_id == j1).all()
    assert {f.url_id for f in filas} == {1, 2}
    f = filas[0]
    assert f.clave == clave_estado(estados[0].datos) or f.clave == clave_estado(estados[1].datos)
    assert f.modelo == "typesafe/jev-1.13" and f.modelo_respuesta.startswith("typesafe/jev-1.13")
    assert f.estado_enviado in ({"title": "Aviso legal", "h1": "Aviso legal"},
                                {"title": "Producto", "h1": "Producto"})
    assert f.run_id is None and f.ambito == "url"
    resumen = s.get(Job, j1).decisiones_resumen
    assert resumen["llamadas"] == 2 and resumen["coste_usd"] == pytest.approx(0.0002)
    assert resumen["por_pregunta"]["a"]["distribucion"] == {"true": 2}
    s.close()


def test_otra_version_de_la_pregunta_no_sale_de_la_bd(Sesion):
    j = _job(Sesion)
    capa1 = _capa(FakeLLM(), sesion_factory=Sesion, job_id=j)
    res = _preguntar(capa1, [E("x")], [P("a")])
    s = Sesion()
    capa1.guardar(s, [E("x")], res)
    s.commit()
    fake = FakeLLM()
    _preguntar(_capa(fake, sesion_factory=Sesion), [E("x")], [P("a", version=2)])
    assert fake.llamadas == 1


def test_sin_decidir_no_es_cache(Sesion):
    j = _job(Sesion)
    capa1 = _capa(FakeLLM(guion=[503] * 4), sesion_factory=Sesion, job_id=j)
    res = _preguntar(capa1, [E("x")], [P("a")])
    s = Sesion()
    capa1.guardar(s, [E("x")], res)
    s.commit()
    fake = FakeLLM()
    _preguntar(_capa(fake, sesion_factory=Sesion), [E("x")], [P("a")])
    assert fake.llamadas == 1


def test_sin_tabla_la_cache_de_bd_falla_en_silencio(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/vacia.db")  # sin create_all
    fake = FakeLLM()
    d = _preguntar(_capa(fake, sesion_factory=sessionmaker(bind=engine)), [E("x")], [P("a")])
    assert d[0][0].respuesta == "true"


def test_el_resumen_se_acumula_entre_ejecuciones(Sesion):
    from shared.models import Job

    j = _job(Sesion)
    for texto in ("a", "b"):
        capa = _capa(FakeLLM(), job_id=j)
        res = _preguntar(capa, [E(texto)], [P("q")])
        s = Sesion()
        capa.guardar(s, [E(texto)], res)
        s.commit()
        s.close()
    s = Sesion()
    r = s.get(Job, j).decisiones_resumen
    assert r["llamadas"] == 2 and r["tokens_entrada"] == 550
    assert r["latencia_p50_ms"] is not None and r["estado"] == "ok"
    s.close()


def test_presupuesto_del_cliente_desde_su_ficha_y_sembrado_desde_bd(Sesion, monkeypatch):
    from shared.models import Client, Decision as Fila

    s = Sesion()
    cliente = Client(nombre="Acme", slug="acme", dominios=[], config_defecto={},
                     integraciones=[], decisiones={"presupuesto_mensual_usd": 0.01})
    s.add(cliente)
    s.commit()
    j = _job(Sesion, cliente_id=cliente.id)
    s.add(Fila(job_id=j, clave="0" * 64, pregunta="q", version_pregunta=1, estado="incierto",
               origen="modelo", modelo="typesafe/jev-1.13", coste_usd=0.0099))
    s.commit()
    s.close()

    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MAX_CONCURRENCIA", "1")
    fake = FakeLLM(coste=0.0004)
    capa = dec.capa_desde_entorno(transport=fake.transport, cliente_id=cliente.id,
                                  sesion_factory=Sesion, job_id=j)
    assert capa.presupuesto.cliente_mensual_usd == pytest.approx(0.01)
    res = capa.preguntar_sync([E("a"), E("b"), E("c")], [P("q2")])
    # Quedaba 0,0001: entra como mucho una llamada (la reserva por precio
    # cabe) y despues ninguna.
    assert fake.llamadas <= 1
    assert "presupuesto_cliente" in {r[0].motivo for r in res}


# --- observabilidad y catalogo -------------------------------------------------

def test_fusionar_resumen_suma_y_se_queda_con_el_peor_estado():
    a = Observabilidad(llamadas=2, coste_usd=0.1, n_429=1)
    a.latencia(80)
    b = Observabilidad(llamadas=1, coste_usd=0.05, estado="sin_credito")
    b.latencia(900)
    f = fusionar_resumen(a.a_dict(), b.a_dict())
    assert f["llamadas"] == 3 and f["coste_usd"] == pytest.approx(0.15)
    assert f["n_429"] == 1 and f["estado"] == "sin_credito"
    assert f["latencia_hist"] == {"100": 1, "1600": 1}


# (nombre, version) -> huella de lo que se envia. Una pregunta nueva, o una
# version nueva, se apunta aqui. Si cambia el texto y NO la version, la cache
# seguiria sirviendo respuestas a la pregunta vieja como si fueran de la
# nueva: este test es el que lo impide.
HUELLAS = {("es_pagina_legal", 1): "22ada5c93222ca3d", ("es_soft_404", 1): "3550ca4cd93c9626",
           ("es_pagina_legal", 2): "dd0345f0f28408ad", ("es_soft_404", 2): "e3d75320bde45267",
           ("es_pagina_de_utilidad", 1): "3928932eba01e723", ("es_pagina_de_utilidad", 2): "43adee04faca5150",
           # Intencion y embudo (decision 176)
           ("intencion_pagina", 1): "a4589e2b81d4a4fb",
           ("etapa_embudo", 1): "22ab1379c09d763a",
           ("intencion_busqueda", 1): "a436ff6f5e5ff168",
           }


def test_cambiar_el_texto_de_una_pregunta_obliga_a_subir_su_version():
    for p in CATALOGO.values():
        assert HUELLAS.get((p.nombre, p.version)) == p.huella, (
            f"{p.nombre} v{p.version}: el texto ha cambiado. Sube `version` y "
            f"apunta su huella ({p.huella}) en HUELLAS")


def test_catalogo_de_ejemplo():
    p = CATALOGO["es_pagina_legal"]
    assert p.ejemplo and p.umbral is None and p.modelo == "typesafe/jev-1.13"


@pytest.mark.parametrize("kw,mensaje", [
    (dict(modelo="typesafe/jev-latest"), "fijado"),
    (dict(instrucciones=" "), "obligatorias"),
    (dict(tipo="choice", opciones={"a": None}), "opciones"),
    (dict(tipo="score", opciones=["solo"]), "niveles"),
    (dict(opciones={"true": "si"}), "true y false"),
])
def test_pregunta_invalida(kw, mensaje):
    base = dict(nombre="x", tipo="noul", version=1, instrucciones="¿x?")
    base.update(kw)
    with pytest.raises(ValueError, match=mensaje):
        Pregunta(**base)


def test_preguntar_dentro_de_un_bucle_pide_await():
    capa = _capa(FakeLLM())

    async def dentro():
        with pytest.raises(RuntimeError, match="await"):
            capa.preguntar_sync([E("x")], [P("a")])
        return await capa.preguntar([E("x")], [P("a")])

    async def todo():
        try:
            return await dentro()
        finally:
            await capa.cerrar()

    assert asyncio.run(todo())[0][0].respuesta == "true"


# --- Redis de verdad (solo con REDIS_TEST_URL, como test_cola) ------------------

@pytest.mark.skipif(not os.environ.get("REDIS_TEST_URL"), reason="sin REDIS_TEST_URL")
def test_cache_y_contadores_en_redis():
    """El indice de pruebas (9), nunca el 0 del stack."""
    import redis as redis_lib

    from shared.decisiones import CacheRedis, ContadoresRedis

    r = redis_lib.Redis.from_url(os.environ["REDIS_TEST_URL"], decode_responses=True)
    sufijo = uuid.uuid4().hex
    usadas: list[str] = []
    try:
        cache = CacheRedis(r, ttl_dias=1)
        cache.guardar(f"t{sufijo}", {"respuesta": "true", "confianza": 0.9})
        usadas.append(f"llm:dec:t{sufijo}")
        assert cache.obtener([f"t{sufijo}", f"no{sufijo}"]) == {
            f"t{sufijo}": {"respuesta": "true", "confianza": 0.9}}
        assert 0 < r.ttl(f"llm:dec:t{sufijo}") <= 86400

        c = ContadoresRedis(r)
        clave = f"llm:coste:test:{sufijo}"
        usadas.append(clave)
        c.sembrar(clave, 1.5)
        c.sembrar(clave, 99)  # NX: no pisa lo que otro proceso ya ha contado
        assert c.sumar(clave, 0.25) == pytest.approx(1.75)
        assert r.ttl(clave) > 0

        # Tres ejecuciones que comparten el tope mensual a traves de Redis.
        cid = f"cli-{sufijo}"
        usadas.append(f"llm:coste:cliente:{cid}:{dec._mes()}")
        fake = FakeLLM(coste=0.0004)
        motivos = []
        for i in range(3):
            capa = _capa(fake, presupuesto=Presupuesto(
                cliente_mensual_usd=0.0005, cliente_id=cid, contadores=ContadoresRedis(r)))
            motivos.append(_preguntar(capa, [E(f"{sufijo}-{i}")], [P("a")])[0][0].motivo)
        # La segunda reserva (por precio) aun cabe y al ajustar se pasa a
        # 0,0008; la tercera ya no sale.
        assert fake.llamadas == 2 and motivos == [None, None, "presupuesto_cliente"]
    finally:
        for k in usadas:
            r.delete(k)


def test_dos_urls_con_el_mismo_estado_pagan_una_vez():
    """Una llamada, dos URLs: la respuesta va a las dos, el coste solo a la
    primera. Con el coste en cada copia, la suma de `decisions` contaba el mismo
    pago dos veces (decision 162)."""
    fake = FakeLLM(coste=0.0004)
    res = _preguntar(_capa(fake), [E("igual"), E("igual"), E("otro")], [P("a")])
    assert fake.llamadas == 2
    assert [r[0].respuesta for r in res] == ["true", "true", "true"]
    assert [r[0].coste_usd for r in res] == [0.0004, 0.0, 0.0004]
    assert sum(r[0].coste_usd for r in res) == pytest.approx(0.0008)


def test_la_estimacion_cuenta_el_coste_fijo_de_cada_llamada():
    """Medido en la primera llamada real: tokens = 0,247 x caracteres + 246."""
    capa = _capa(FakeLLM())
    cuerpo = {"state": "x" * 1000}
    assert capa._tokens_estimados(cuerpo) == math.ceil(len(json.dumps(cuerpo)) / 4) + dec.TOKENS_FIJOS_POR_LLAMADA
