"""Intencion de la pagina y de sus busquedas de Search Console (#39, #169;
decision 176), EJECUTADO contra SQLite y el FakeLLM.

Lo que hay que sujetar:

- las preguntas: un criterio cada una, `unclear` como salida, las mismas
  opciones en la pagina y en la busqueda, sin umbral y los modulos marcados;
- las busquedas que se preguntan: las de mas clics de cada pagina en la
  ventana del censo y de las propiedades activas, nada fuera;
- la cache por cliente: la misma busqueda en dos paginas es una llamada, y
  relanzar no paga nada;
- sin Search Console el modulo no tiene trabajo y lo dice;
- el desajuste ponderado por clics no cuenta lo navegacional ni lo que no esta
  claro, y la incidencia esta apagada sin umbral;
- `extraer` saca el juego sin estados repetidos.

Nada sale a la red: el `FakeLLM` va como transporte de httpx.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import date, datetime, timezone

import pytest

pytest.importorskip("httpx")

import shared.enriquecido as enr  # noqa: E402
from shared import intencion as I  # noqa: E402
from shared.gsc import clave_de_url  # noqa: E402
from shared.models import (  # noqa: E402
    Client, Decision, EnrichmentRun, GscConsulta, GscEstado, Heading, HtmlMeta, Job, PageContent, Url,
)
from shared.preguntas import CATALOGO  # noqa: E402
from test_enriquecido import _motor  # noqa: E402

PROPIEDAD = "https://www.acme.com/"
EN_VENTANA = date(2026, 10, 1)
FUERA = date(2026, 6, 1)


def sembrar(Sesion, *, con_gsc=True, integracion_activa=True, cliente=True):
    """Un censo de acme.com (semilla con www) con:
    - /tienda/zapato-rojo (transaccional) con 3 busquedas, una de marca;
    - /blog/como-limpiar (informacional) con 2 busquedas, una comercial;
    - /blog/otra con la MISMA busqueda que la tienda (una llamada para las dos);
    - /vacia sin titulo, H1 ni contenido;
    y busquedas que no deben entrar: sin clics, fuera de la ventana y de una
    propiedad inactiva."""
    s = Sesion()
    c = None
    if cliente:
        c = Client(nombre="Acme", slug="acme", dominios=["acme.com"],
                   integraciones=[{"tipo": "gsc", "parametros": {"propiedad": PROPIEDAD},
                                   "activa": integracion_activa},
                                  {"tipo": "gsc", "parametros": {"propiedad": "sc-domain:vieja.com"},
                                   "activa": False}])
        s.add(c)
        s.flush()
    job = Job(name="censo", status="completed", seeds=["https://www.acme.com/"],
              config={"marcas": ["Acme", "acme shoes"]}, cliente_id=c.id if c else None,
              started_at=datetime(2026, 10, 21, 1, tzinfo=timezone.utc))
    s.add(job)
    s.flush()
    if c is not None and con_gsc:
        s.add(GscEstado(cliente_id=c.id, propiedad=PROPIEDAD, estado="ok"))
        s.add(GscEstado(cliente_id=c.id, propiedad="sc-domain:vieja.com", estado="ok"))

    ids = {}

    def url(ruta, i, titulo=None, h1=None, texto=None, clics=None):
        u = Url(job_id=job.id, url=f"https://www.acme.com{ruta}", url_hash=f"{i:064x}",
                host="www.acme.com", path=ruta, scheme="https", is_internal=True, status_code=200,
                status_group="2xx", is_html=True, resource_type="html", content_type="text/html",
                indexable=True, gsc_clics=clics)
        s.add(u)
        s.flush()
        if titulo is not None:
            s.add(HtmlMeta(url_id=u.id, title=titulo))
        if h1 is not None:
            s.add(Heading(url_id=u.id, tag="h1", position=0, text=h1))
        if texto is not None:
            s.add(PageContent(url_id=u.id, content_text=texto))
        ids[ruta] = u.id
        return u

    url("/tienda/zapato-rojo", 1, "Zapato rojo | Acme", "Zapato rojo", "Compra el zapato rojo. 49 EUR.", 160)
    url("/blog/como-limpiar", 2, "Como limpiar zapatos", "Como limpiar zapatos", "Guia paso a paso.", 60)
    url("/blog/otra", 3, "Otra guia", "Otra guia", "Mas consejos.", 5)
    url("/vacia", 4, None, None, None)

    def consulta(ruta, q, clics, imp=100, dia=EN_VENTANA, propiedad=PROPIEDAD):
        if c is None or not con_gsc:
            return
        s.add(GscConsulta(cliente_id=c.id, propiedad=propiedad, fecha=dia,
                          url_clave=clave_de_url(f"https://www.acme.com{ruta}"), consulta=q,
                          clics=clics, impresiones=imp, posicion=3.0))

    consulta("/tienda/zapato-rojo", "comprar zapato rojo", 60)
    consulta("/tienda/zapato-rojo", "comprar zapato rojo", 40, dia=date(2026, 10, 2))  # suma 100
    consulta("/tienda/zapato-rojo", "como limpiar zapatos rojos", 40)
    consulta("/tienda/zapato-rojo", "acme", 20)
    consulta("/tienda/zapato-rojo", "sin clics", 0, imp=900)
    consulta("/tienda/zapato-rojo", "fuera de la ventana", 500, dia=FUERA)
    consulta("/tienda/zapato-rojo", "propiedad vieja", 500, propiedad="sc-domain:vieja.com")
    consulta("/blog/como-limpiar", "como limpiar zapatos", 50)
    consulta("/blog/como-limpiar", "mejores limpiadores de zapatos", 10)
    consulta("/blog/otra", "comprar zapato rojo", 5)
    s.commit()
    jid = job.id
    s.close()
    return jid, ids


class FakeIntencion:
    """Contesta segun el estado, como lo haria un juez coherente: la tienda y
    «comprar» son transaccionales, «mejores» comercial, la marca navegacional y
    el resto informacional. El embudo, BOFU en la tienda y TOFU en el blog."""

    def __new__(cls, fake_llm, **kw):
        base = fake_llm(**kw)
        original = base._responder

        def responder_con_estado(cuerpo):
            st = cuerpo["state"]

            def eleccion(o):
                return {"type": "choice", "choice": o, "confidence": 0.8, "probabilities": {o: 0.8}}

            respuestas = {}
            for nombre, q in cuerpo["questions"].items():
                if nombre == "intencion_busqueda":
                    b = st["busqueda"]
                    o = ("transactional" if "comprar" in b else "commercial" if "mejores" in b
                         else "navigational" if b in st["marcas"] else "informational")
                elif nombre == "intencion_pagina":
                    o = "transactional" if "/tienda/" in st["ruta"] else "informational"
                elif nombre == "etapa_embudo":
                    o = "bofu" if "/tienda/" in st["ruta"] else "tofu"
                else:
                    respuestas[nombre] = original(nombre, q)
                    continue
                respuestas[nombre] = eleccion(o)
            return respuestas

        llamar = base.__call__

        def __call__(request):
            import httpx

            r = llamar(request)
            if r.status_code != 200:
                return r
            cuerpo = json.loads(request.content)
            datos = r.json()
            datos["answers"] = responder_con_estado(cuerpo)
            return httpx.Response(200, json=datos)

        base.__call__ = __call__  # noqa: B010 -- el transporte llama a la instancia
        return base


def _transporte(fake):
    import httpx

    return httpx.MockTransport(fake.__call__)


@pytest.fixture()
def capa_activa(monkeypatch):
    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-de-prueba")
    monkeypatch.setenv("LLM_MAX_CONCURRENCIA", "1")
    monkeypatch.delenv("ENRICHMENT_MAX_USD_MES", raising=False)


def _run(Sesion, jid, modulos):
    s = Sesion()
    r = EnrichmentRun(job_id=jid, modulos=list(modulos), alcance={}, presupuesto_usd=1.0,
                      estado=enr.PENDIENTE)
    s.add(r)
    s.commit()
    rid = r.id
    s.close()
    return rid


# --- preguntas y modulos ---------------------------------------------------------

def test_las_preguntas_tienen_un_criterio_salida_y_ninguna_umbral():
    pagina, embudo, busqueda = (CATALOGO[n] for n in ("intencion_pagina", "etapa_embudo",
                                                      "intencion_busqueda"))
    for p in (pagina, embudo, busqueda):
        assert p.tipo == "choice" and p.umbral is None and "unclear" in p.opciones
        # Cada opcion dice lo que NO es (leccion de la decision 169).
        assert all("NOT" in d for o, d in p.opciones.items() if o != "unclear"), p.nombre
    # Las mismas opciones en la pagina y en la busqueda: se comparan.
    from shared.preguntas.intencion import ETAPAS, INTENCIONES

    assert list(pagina.opciones) == list(busqueda.opciones) == list(INTENCIONES)
    assert list(embudo.opciones) == list(ETAPAS)
    # Intencion y embudo son dos preguntas, no una mezclada.
    assert set(embudo.opciones) & set(pagina.opciones) == {"unclear"}


def test_los_modulos_van_marcados_y_el_de_busquedas_trae_el_de_la_pagina():
    pag, bus = enr.REGISTRO["intencion_pagina"], enr.REGISTRO["intencion_busquedas"]
    assert pag.ejemplo and bus.ejemplo
    assert pag.preguntas == ("intencion_pagina", "etapa_embudo")
    assert bus.preguntas == ("intencion_busqueda",) and bus.filtrar_urls and bus.motivo_sin_urls
    orden, anadidos = enr.resolver_modulos(["intencion_busquedas"])
    assert orden == ["intencion_pagina", "intencion_busquedas"] and anadidos == ["intencion_pagina"]


# --- estados y filtros -----------------------------------------------------------

def test_busquedas_de_la_ventana_y_de_la_propiedad_activa_por_clics(tmp_path):
    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion)
    s = Sesion()
    job = s.get(Job, jid)
    por_url = I.busquedas_por_url(s, job, list(ids.values()))
    assert por_url[ids["/tienda/zapato-rojo"]] == [
        ("comprar zapato rojo", 100), ("como limpiar zapatos rojos", 40), ("acme", 20)]
    assert ids["/vacia"] not in por_url
    assert I.sitio_del_job(job) == "acme.com" and I.marcas_del_job(job) == ["acme", "acme shoes"]
    s.close()


def test_con_muchas_urls_se_lee_la_ventana_una_vez_y_sale_lo_mismo(tmp_path, monkeypatch):
    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion)
    s = Sesion()
    job = s.get(Job, jid)
    con_clave = I.busquedas_por_url(s, job, list(ids.values()))
    monkeypatch.setattr(I, "CLAVES_POR_CONSULTA", 1)
    assert I.busquedas_por_url(s, job, list(ids.values())) == con_clave
    s.close()


def test_el_estado_de_una_busqueda_no_lleva_nada_de_la_pagina_ni_cifras(tmp_path):
    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion)
    s = Sesion()
    estados = I.estados_intencion_busquedas(s, [ids["/tienda/zapato-rojo"], ids["/blog/otra"]])
    assert [e.datos["busqueda"] for e in estados] == [
        "comprar zapato rojo", "como limpiar zapatos rojos", "acme", "comprar zapato rojo"]
    assert all(e.ambito == "busqueda" for e in estados)
    assert all(set(e.datos) == {"sitio", "marcas", "busqueda"} for e in estados)
    # La misma busqueda en dos paginas es el mismo estado: una llamada.
    assert estados[0].datos == estados[3].datos
    s.close()


def test_la_pagina_lleva_ruta_titulo_h1_y_contenido_y_sin_nada_no_entra(tmp_path):
    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion)
    s = Sesion()
    todas = list(ids.values())
    assert I.paginas_con_algo_que_juzgar(s, jid, todas) == [ids[r] for r in (
        "/tienda/zapato-rojo", "/blog/como-limpiar", "/blog/otra")]
    (e,) = I.estados_intencion_pagina(s, [ids["/tienda/zapato-rojo"]])
    assert e.datos == {"ruta": "/tienda/zapato-rojo", "titulo": "Zapato rojo | Acme",
                       "h1": "Zapato rojo", "contenido": "Compra el zapato rojo. 49 EUR."}
    s.close()


@pytest.mark.parametrize("kw,texto", [
    (dict(cliente=False), "ningun cliente"),
    (dict(con_gsc=False), "no tiene Search Console"),
    (dict(integracion_activa=False), "no tiene Search Console"),
])
def test_sin_search_console_no_hay_trabajo_y_se_dice(tmp_path, capa_activa, kw, texto):
    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion, **kw)
    s = Sesion()
    assert I.paginas_con_busquedas(s, jid, list(ids.values())) == []
    e = enr.estimar(s, s.get(Job, jid), ["intencion_busquedas"], {})
    s.close()
    assert e["por_modulo"]["intencion_busquedas"]["urls"] == 0
    assert texto in e["por_modulo"]["intencion_busquedas"]["aviso"]
    # La de la pagina si tiene trabajo: no depende de Search Console.
    assert e["por_modulo"]["intencion_pagina"]["urls"] == 3


def test_con_search_console_pero_sin_clics_en_la_ventana_tambien_se_dice(tmp_path):
    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion)
    s = Sesion()
    s.query(GscConsulta).filter(GscConsulta.fecha == EN_VENTANA).delete()
    s.query(GscConsulta).filter(GscConsulta.fecha == date(2026, 10, 2)).delete()
    s.commit()
    assert I.paginas_con_busquedas(s, jid, list(ids.values())) == []
    assert "entre el 2026-07-24 y el 2026-10-21" in I.motivo_sin_busquedas(s, jid)
    s.close()


# --- estimacion y ejecucion de punta a punta ----------------------------------------

def test_estimar_cuenta_cada_busqueda_distinta_una_vez(tmp_path, capa_activa, fake_llm):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion)
    trampa = fake_llm(prohibido=True)
    s = Sesion()
    e = enr.estimar(s, s.get(Job, jid), ["intencion_busquedas"], {})
    s.close()
    assert trampa.llamadas == 0
    b = e["por_modulo"]["intencion_busquedas"]
    # 3 paginas con busquedas, 6 pares y 5 busquedas distintas.
    assert (b["urls"], b["estados"], b["estados_distintos"], b["llamadas"]) == (3, 6, 5, 5)
    # Decision 162: tokens = caracteres / 4 + 250 por llamada.
    assert b["tokens_estimados"] > 5 * 250
    assert e["modulos"] == ["intencion_pagina", "intencion_busquedas"]
    assert e["por_modulo"]["intencion_pagina"]["llamadas"] == 3


def test_de_punta_a_punta_con_el_fake_y_relanzar_no_paga(tmp_path, capa_activa, fake_llm):
    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion)
    fake = FakeIntencion(fake_llm, coste=0.0001)
    rid = _run(Sesion, jid, ["intencion_pagina", "intencion_busquedas"])
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=_transporte(fake), lote=2) == "completada"
    # 3 paginas (una llamada con las dos preguntas) + 5 busquedas distintas.
    assert fake.llamadas == 8
    estados_enviados = [p["state"] for p in fake.peticiones]
    assert sum(1 for st in estados_enviados if "busqueda" in st) == 5
    assert all("contenido" not in st for st in estados_enviados if "busqueda" in st)
    s = Sesion()
    filas = s.query(Decision).filter(Decision.run_id == rid).all()
    assert {f.estado for f in filas} == {"incierto"}  # sin umbral nada es decidido
    assert sum(1 for f in filas if f.pregunta == "intencion_busqueda") == 6
    assert {f.ambito for f in filas if f.pregunta == "intencion_busqueda"} == {"busqueda"}

    res = enr.intencion_frente_a_busquedas(s, rid)
    s.close()
    por_url = {p["url"].rsplit("/", 1)[-1]: p for p in res["paginas"]}
    tienda = por_url["zapato-rojo"]
    # La marca (20 clics) no cuenta: 100 transaccionales de 140 que cuentan.
    assert tienda["intencion_pagina"] == "transactional"
    assert tienda["clics_por_intencion"] == {"transactional": 100, "informational": 40, "navigational": 20}
    assert (tienda["clics_que_cuentan"], tienda["clics_desalineados"], tienda["desalineada"]) == (140, 40, False)
    # La otra guia solo recibe «comprar zapato rojo»: desalineada. El orden es
    # por clics con otra intencion: la tienda primero (40 frente a 5).
    otra = por_url["otra"]
    assert otra["desalineada"] and otra["intencion_dominante"] == "transactional"
    assert [p["url"].rsplit("/", 1)[-1] for p in res["paginas"]][0] == "zapato-rojo"
    assert res["resumen"]["desalineadas"] == 1 and res["resumen"]["orientativo"] is True
    assert res["resumen"]["incidencia_activa"] is False
    # Embudo con trafico: clics de la pagina por etapa.
    assert res["embudo"]["por_etapa"] == {"bofu": {"paginas": 1, "clics": 160},
                                          "tofu": {"paginas": 2, "clics": 65}}

    # Relanzar: todo sale de la cache (tabla `decisions`), cero llamadas.
    fake2 = FakeIntencion(fake_llm, coste=0.0001)
    rid2 = _run(Sesion, jid, ["intencion_pagina", "intencion_busquedas"])
    assert enr.ejecutar(rid2, sesion_factory=Sesion, transport=_transporte(fake2)) == "completada"
    assert fake2.llamadas == 0
    s = Sesion()
    assert s.get(EnrichmentRun, rid2).gastado_usd == 0
    s.close()


def test_la_cache_de_una_busqueda_es_por_cliente(tmp_path, capa_activa, fake_llm):
    """Otro censo del mismo cliente reutiliza la busqueda; otro sitio no."""
    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion)
    s = Sesion()
    a = I.estados_intencion_busquedas(s, [ids["/blog/otra"]])[0]
    job = s.get(Job, jid)
    job.seeds = ["https://otro-sitio.com/"]
    s.commit()
    b = I.estados_intencion_busquedas(s, [ids["/blog/otra"]])[0]
    s.close()
    from shared.decisiones import clave_estado

    assert a.datos["busqueda"] == b.datos["busqueda"]
    assert clave_estado(a.datos) != clave_estado(b.datos)


# --- desajuste e incidencia ---------------------------------------------------------

def test_desajuste_no_cuenta_marca_ni_dudosas():
    d = I.desajuste("informational", [
        {"intencion": "transactional", "clics": 30}, {"intencion": "informational", "clics": 10},
        {"intencion": "navigational", "clics": 500}, {"intencion": "unclear", "clics": 50},
        {"intencion": None, "clics": 7}])
    assert d["clics_que_cuentan"] == 40 and d["clics_sin_contar"] == 557
    assert d["intencion_dominante"] == "transactional" and d["desalineada"]
    assert d["parte_alineada"] == 0.25 and d["clics_desalineados"] == 30
    # Solo marca: nada que afirmar.
    solo_marca = I.desajuste("transactional", [{"intencion": "navigational", "clics": 99}])
    assert not solo_marca["desalineada"] and solo_marca["parte_alineada"] is None
    # Pagina sin intencion clara: no se afirma desajuste.
    assert not I.desajuste("unclear", [{"intencion": "transactional", "clics": 9}])["desalineada"]
    # Empate a la mitad: no es mayoria de otra intencion.
    assert not I.desajuste("informational", [{"intencion": "informational", "clics": 5},
                                             {"intencion": "commercial", "clics": 5}])["desalineada"]


def test_la_incidencia_esta_apagada_sin_umbral_y_con_umbral_solo_usa_lo_decidido(monkeypatch):
    paginas = [{"url_id": 7, "intencion_pagina": "informational", "estado_pagina": "decidido",
                "busquedas": [{"busqueda": "comprar x", "clics": 30, "intencion": "transactional",
                               "estado": "decidido"},
                              {"busqueda": "que es x", "clics": 10, "intencion": "informational",
                               "estado": "decidido"}]}]
    assert I.incidencias_intencion_desalineada("j", paginas) == []
    for n in ("intencion_pagina", "intencion_busqueda"):
        monkeypatch.setitem(CATALOGO, n, dataclasses.replace(CATALOGO[n], umbral=0.8))
    (inc,) = I.incidencias_intencion_desalineada("j", paginas)
    assert inc["issue_type"] == "intencion_desalineada" and inc["url_id"] == 7
    assert inc["details"]["busquedas"] == [{"busqueda": "comprar x", "clics": 30}]
    # Lo incierto no cuenta, aunque haya umbral.
    paginas[0]["busquedas"][0]["estado"] = "incierto"
    assert I.incidencias_intencion_desalineada("j", paginas) == []
    paginas[0]["busquedas"][0]["estado"] = "decidido"
    paginas[0]["estado_pagina"] = "incierto"
    assert I.incidencias_intencion_desalineada("j", paginas) == []


# --- juego de evaluacion -------------------------------------------------------------

def _script():
    import importlib.util
    from pathlib import Path

    ruta = Path(__file__).resolve().parent.parent / "scripts" / "evaluar_decisiones.py"
    spec = importlib.util.spec_from_file_location("evaluar_decisiones_script", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extraer_el_juego_de_busquedas_sin_estados_repetidos(tmp_path, monkeypatch):
    ev = _script()

    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion)
    s = Sesion()
    filas = ev.extraer(s, "intencion_busqueda", [str(jid)], n=50, semilla=0)
    pagina = ev.extraer(s, "intencion_pagina", [str(jid)], n=50, semilla=0)
    s.close()
    # 6 pares, 5 busquedas distintas: «comprar zapato rojo» sale una vez.
    assert sorted(f["estado"]["busqueda"] for f in filas) == sorted(
        ["comprar zapato rojo", "como limpiar zapatos rojos", "acme", "como limpiar zapatos",
         "mejores limpiadores de zapatos"])
    assert len({f["id"] for f in filas}) == 5 and {f["ambito"] for f in filas} == {"busqueda"}
    assert all(f["etiqueta"] is None and f["version"] == 1 for f in filas)
    # La de la pagina: las tres con algo que juzgar, la vacia no.
    assert len(pagina) == 3 and {f["ambito"] for f in pagina} == {"url"}


def test_extraer_quita_la_misma_pagina_con_y_sin_www(tmp_path):
    ev = _script()

    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion)
    s = Sesion()
    u = Url(job_id=jid, url="https://acme.com/blog/otra", url_hash=f"{99:064x}", host="acme.com",
            path="/blog/otra", scheme="https", is_internal=True, status_code=200, status_group="2xx",
            is_html=True, resource_type="html", content_type="text/html", indexable=True)
    s.add(u)
    s.flush()
    s.add(HtmlMeta(url_id=u.id, title="Otra guia"))
    s.add(Heading(url_id=u.id, tag="h1", position=0, text="Otra guia"))
    s.add(PageContent(url_id=u.id, content_text="Mas consejos."))
    s.commit()
    pagina = ev.extraer(s, "intencion_pagina", [str(jid)], n=50, semilla=0)
    s.close()
    assert len(pagina) == 3


def test_responder_respeta_el_ambito_del_juego(tmp_path, capa_activa, fake_llm):
    ev = _script()
    from shared.decisiones import CapaDecisiones

    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion)
    s = Sesion()
    juego = ev.extraer(s, "intencion_busqueda", [str(jid)], n=50, semilla=0)
    s.close()
    capa = CapaDecisiones(None)  # sin cliente: estima y no llama
    estimacion, respuestas = ev.responder(juego, confirmar=False, capa=capa)
    assert respuestas is None and estimacion["estados_distintos"] == 5


def test_el_modulo_de_busquedas_no_tumba_nada_si_falta_la_tabla(tmp_path):
    """Best-effort: una base sin `gsc_consultas` es «sin busquedas», no un error."""
    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion)
    s = Sesion()
    s.execute(GscConsulta.__table__.delete())
    s.commit()
    GscConsulta.__table__.drop(s.get_bind())
    assert I.busquedas_por_url(s, s.get(Job, jid), list(ids.values())) == {}
    s.close()


def test_uuid_de_job_en_texto(tmp_path):
    Sesion = _motor(tmp_path)
    jid, ids = sembrar(Sesion)
    s = Sesion()
    assert I.paginas_con_busquedas(s, str(jid), list(ids.values())) == [
        ids["/tienda/zapato-rojo"], ids["/blog/como-limpiar"], ids["/blog/otra"]]
    assert isinstance(uuid.UUID(str(jid)), uuid.UUID)
    s.close()
