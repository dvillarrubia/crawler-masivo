"""Tipo de pagina y categoria del cliente por plantilla (#12, decision 178),
EJECUTADO contra SQLite y el FakeLLM: nada sale a la red.

Lo que hay que sujetar:

- se pregunta por una MUESTRA de cada plantilla (3 a 8 URLs), igual en la
  estimacion que al ejecutar, y la misma pagina con y sin www es una muestra;
- el estado lleva ruta, palabras, og:type, paginacion y los tipos de schema
  que no estan en todas las paginas, y nada mas que lo minimo;
- la estimacion cuenta los tokens como la decision 162 (caracteres / 4 + 250);
- sin umbral todo sale `incierto` y ninguna plantilla es `propagable`; una
  plantilla cuyas muestras no coinciden es `mixta`;
- `categoria_cliente` no pregunta nada sin taxonomia, manda las categorias del
  job como opciones, y otra taxonomia es otra clave de cache (tambien en BD);
- `extraer` saca el juego de la muestra y sin estados repetidos.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

pytest.importorskip("httpx")

import shared.enriquecido as enr  # noqa: E402
from shared import tipo_pagina as tp  # noqa: E402
from shared.models import (  # noqa: E402
    Client, Decision, EnrichmentRun, Heading, HtmlMeta, Job, PageContent, StructuredData, Url,
)
from test_enriquecido import _motor, capa_activa  # noqa: E402,F401

from fake_llm import FakeLLM  # noqa: E402


def sembrar(Sesion, *, taxonomia=None, templates=None):
    """Un censo con tres plantillas: 40 entradas de blog (la mitad repetidas
    con www), 12 fichas `/tienda/:slug` y una portada."""
    s = Sesion()
    cliente = Client(nombre="Acme", slug="acme", dominios=["acme.com"])
    s.add(cliente)
    s.flush()
    config = {}
    if taxonomia is not None:
        config["taxonomia"] = taxonomia
    if templates is not None:
        config["templates"] = templates
    job = Job(name="censo", status="completed", seeds=["https://acme.com/"], config=config,
              cliente_id=cliente.id)
    s.add(job)
    s.flush()
    n = [0]

    def url(host, ruta, titulo, texto, schema=(), og="", rel_next=None):
        n[0] += 1
        u = Url(job_id=job.id, url=f"https://{host}{ruta}", url_hash=f"{(n[0] * 7919) % 10007:064x}",
                host=host, path=ruta, scheme="https", is_internal=True, status_code=200,
                status_group="2xx", is_html=True, resource_type="html", content_type="text/html",
                indexable=True, content_word_count=len(texto.split()))
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title=titulo, og_type=og or None, rel_next=rel_next))
        s.add(Heading(url_id=u.id, tag="h1", position=0, text=titulo))
        s.add(PageContent(url_id=u.id, content_text=texto))
        for t in schema:
            s.add(StructuredData(url_id=u.id, schema_type=t, format="json-ld"))
        return u

    url("acme.com", "/", "Acme", "Bienvenido a Acme", schema=("WebSite", "Organization"))
    for i in range(20):
        texto = f"Entrada numero {i} del blog con su texto. " * 20
        url("acme.com", f"/blog/entrada-{i}", f"Entrada {i}", texto, schema=("BlogPosting", "WebSite"),
            og="article")
        # La misma entrada con www: el mismo estado.
        url("www.acme.com", f"/blog/entrada-{i}", f"Entrada {i}", texto,
            schema=("BlogPosting", "WebSite"), og="article")
    for i in range(12):
        url("acme.com", f"/tienda/producto-{chr(97 + i)}", f"Producto {i}", f"Ficha del producto {i}. " * 10,
            schema=("Product", "Offer", "BreadcrumbList"), og="product",
            rel_next="https://acme.com/tienda/2" if i == 0 else None)
    s.commit()
    jid = job.id
    s.close()
    return jid


def _ids(s, jid):
    return [u for (u,) in s.query(Url.id).filter(Url.job_id == jid).order_by(Url.id)]


# --- muestra ------------------------------------------------------------------

def test_cuantas_muestras_por_plantilla():
    assert [tp.muestras_por_plantilla(n) for n in (0, 1, 2, 3, 9, 10, 99, 100, 8718, 10**9)] == [
        0, 1, 2, 3, 3, 4, 4, 5, 6, 8]


def test_la_plantilla_lleva_el_host_sin_www():
    a = tp.clave_de_plantilla("https://www.acme.com/blog/x", "/blog/x")
    assert a == tp.clave_de_plantilla("https://acme.com/blog/y", "/blog/y") == "acme.com|/blog/:slug (2n)"
    assert tp.clave_de_plantilla("https://otro.com/blog/x", "/blog/x") != a
    reglas = [("fichas", r"^/tienda/")]
    assert tp.clave_de_plantilla("https://acme.com/tienda/a", "/tienda/a", reglas) == "acme.com|fichas"


def test_la_muestra_es_por_plantilla_y_sin_la_misma_pagina_dos_veces(tmp_path):
    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion)
    s = Sesion()
    ids = _ids(s, jid)
    elegidas = tp.muestra_por_plantilla(s, jid, ids)
    rutas = {u: (s.get(Url, u).host, s.get(Url, u).path) for u in elegidas}
    blog = [r for r in rutas.values() if r[1].startswith("/blog/")]
    tienda = [r for r in rutas.values() if r[1].startswith("/tienda/")]
    # 20 rutas de blog distintas (40 URLs con y sin www) -> 4 muestras; 12 fichas -> 4; la home, 1.
    assert len(blog) == 4 and len({p for _, p in blog}) == 4
    assert len(tienda) == 4 and len(elegidas) == 9
    # Determinista (por url_hash) y en el orden de entrada.
    assert elegidas == tp.muestra_por_plantilla(s, jid, ids)
    assert elegidas == [u for u in ids if u in set(elegidas)]
    assert tp.muestra_por_plantilla(s, jid, []) == []
    s.close()


def test_las_reglas_del_cliente_mandan_en_la_plantilla(tmp_path):
    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion, templates=[{"nombre": "todo", "patron": "^/"}])
    s = Sesion()
    # Una sola plantilla de 33 rutas distintas -> 4 muestras.
    assert len(tp.muestra_por_plantilla(s, jid, _ids(s, jid))) == 4
    s.close()


# --- estado y estimacion --------------------------------------------------------

def test_el_estado_es_minimo_y_quita_el_schema_de_todas_las_paginas(tmp_path):
    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion)
    s = Sesion()
    ficha = s.query(Url.id).filter(Url.job_id == jid, Url.path == "/tienda/producto-a").scalar()
    (e,) = tp.estados_tipo_pagina(s, [ficha])
    assert set(e.datos) == {"ruta", "palabras", "og_type", "paginada", "schema", "titulo", "h1", "contenido"}
    assert e.datos["schema"] == ["Offer", "Product"]          # sin BreadcrumbList
    assert e.datos["og_type"] == "product" and e.datos["paginada"] is True
    assert e.datos["ruta"] == "/tienda/producto-a" and e.datos["palabras"] == 40
    assert len(e.datos["contenido"]) <= enr.CARACTERES_CONTENIDO
    s.close()


def test_la_estimacion_es_por_muestra_y_con_los_tokens_de_la_decision_162(tmp_path, capa_activa):
    from shared.decisiones import TOKENS_FIJOS_POR_LLAMADA, normalizar_estado
    from shared.preguntas import obtener

    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion)
    s = Sesion()
    e = enr.estimar(s, s.get(Job, jid), ["tipo_pagina"], {})
    pm = e["por_modulo"]["tipo_pagina"]
    assert e["urls"] == 53 and pm["urls"] == 9 and e["llamadas"] == 9
    p = obtener("tipo_pagina")
    estados = tp.estados_tipo_pagina(s, tp.muestra_por_plantilla(s, jid, _ids(s, jid)))
    tokens = sum(math.ceil(len(json.dumps({"model": p.modelo, "state": normalizar_estado(x.datos),
                                           "questions": {p.nombre: p.a_api()}},
                                          ensure_ascii=False)) / 4) + TOKENS_FIJOS_POR_LLAMADA
                 for x in estados)
    assert e["tokens_estimados"] == tokens
    assert e["coste_estimado_usd"] == pytest.approx(tokens * 0.042 / 1e6, abs=1e-8)  # redondeado a 8 decimales
    s.close()


# --- de punta a punta -------------------------------------------------------------

class FakePorRuta(FakeLLM):
    """Responde el tipo segun la ruta del estado: la home, «articulo» en el
    blog y, en la tienda, «ficha_producto» salvo en una ficha (plantilla mixta)."""

    def __init__(self, raro: str | None = None, **kw):
        super().__init__(**kw)
        self.raro = raro
        self._ruta = "/"

    def __call__(self, request):
        cuerpo = json.loads(request.content or b"{}")
        self._ruta = (cuerpo.get("state") or {}).get("ruta", "/")
        return super().__call__(request)

    def _responder(self, nombre, q):
        if nombre != "tipo_pagina":
            return super()._responder(nombre, q)
        r = self._ruta
        eleccion = ("home" if r == "/" else "articulo" if r.startswith("/blog/")
                    else "otro" if r == self.raro else "ficha_producto")
        probs = {o: (0.85 if o == eleccion else 0.15 / (len(q["criteria"]) - 1)) for o in q["criteria"]}
        return {"type": "choice", "choice": eleccion, "confidence": 0.85, "probabilities": probs}


def _run(Sesion, jid, modulos):
    s = Sesion()
    r = EnrichmentRun(job_id=jid, modulos=list(modulos), alcance={}, presupuesto_usd=1.0,
                      estado=enr.PENDIENTE)
    s.add(r)
    s.commit()
    rid = r.id
    s.close()
    return rid


def test_tipo_de_pagina_de_punta_a_punta_y_su_propuesta_por_plantilla(tmp_path, capa_activa):
    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion)
    s = Sesion()
    muestra_tienda = [u for u in tp.muestra_por_plantilla(s, jid, _ids(s, jid))
                      if s.get(Url, u).path.startswith("/tienda/")]
    raro = s.get(Url, muestra_tienda[0]).path
    s.close()
    fake = FakePorRuta(raro=raro)
    rid = _run(Sesion, jid, ["tipo_pagina"])
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport) == enr.COMPLETADA
    assert fake.llamadas == 9
    enviado = fake.peticiones[0]["questions"]["tipo_pagina"]
    assert enviado["type"] == "choice" and "ficha_producto" in enviado["criteria"]
    s = Sesion()
    filas = s.query(Decision).filter(Decision.run_id == rid).all()
    assert len(filas) == 9 and {f.estado for f in filas} == {"incierto"}   # sin umbral
    prop = tp.propuesta_por_plantilla(s, rid)
    por = {f["plantilla"]: f for f in prop["plantillas"]}
    blog = por["acme.com|/blog/:slug (2n)"]
    # La plantilla del blog son 40 URLs (con y sin www), preguntadas por 4.
    assert blog["urls"] == 40 and blog["muestras"] == 4 and blog["respuesta"] == "articulo"
    assert blog["acuerdo"] == 1.0 and blog["estado"] == tp.ORIENTATIVA and blog["confianza_media"] == 0.85
    tienda = por["acme.com|/tienda/:slug (2n)"]
    assert tienda["estado"] == tp.MIXTA and tienda["respuesta"] == "ficha_producto"
    assert tienda["acuerdo"] == 0.75 and tienda["respuestas"] == {"ficha_producto": 3, "otro": 1}
    assert prop["umbral"] is None and tp.PROPAGABLE not in prop["por_estado"]
    assert prop["urls_cubiertas"] == 41               # blog y home; la tienda es mixta
    s.close()


def test_con_umbral_y_todo_decidido_la_plantilla_es_propagable(tmp_path, capa_activa, monkeypatch):
    import dataclasses

    from shared import preguntas

    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion)
    con_umbral = dataclasses.replace(preguntas.CATALOGO["tipo_pagina"], umbral=0.8)
    monkeypatch.setitem(preguntas.CATALOGO, "tipo_pagina", con_umbral)
    rid = _run(Sesion, jid, ["tipo_pagina"])
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=FakePorRuta().transport) == enr.COMPLETADA
    s = Sesion()
    prop = tp.propuesta_por_plantilla(s, rid)
    assert {f["estado"] for f in prop["plantillas"]} == {tp.PROPAGABLE}
    s.close()


# --- categoria del cliente ------------------------------------------------------------

def test_sin_taxonomia_la_categoria_no_pregunta_nada(tmp_path, capa_activa):
    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion)
    s = Sesion()
    e = enr.estimar(s, s.get(Job, jid), ["categoria_cliente"], {})
    assert e["por_modulo"]["categoria_cliente"]["urls"] == 0 and e["llamadas"] == 0
    s.close()


def test_la_categoria_pregunta_por_la_taxonomia_del_job_y_otra_es_otra_clave(tmp_path, capa_activa):
    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion, taxonomia=["Calderas", "Aire acondicionado"])
    fake = FakeLLM()
    rid = _run(Sesion, jid, ["categoria_cliente"])
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport) == enr.COMPLETADA
    assert fake.llamadas == 9
    criterios = fake.peticiones[0]["questions"]["categoria_cliente"]["criteria"]
    assert list(criterios) == ["Calderas", "Aire acondicionado", "ninguna"]
    # La misma taxonomia: todo de la cache de la tabla `decisions`, 0 llamadas.
    fake2 = FakeLLM()
    rid2 = _run(Sesion, jid, ["categoria_cliente"])
    assert enr.ejecutar(rid2, sesion_factory=Sesion, transport=fake2.transport) == enr.COMPLETADA
    assert fake2.llamadas == 0
    # Otra taxonomia: las respuestas de antes se eligieron entre otras opciones.
    s = Sesion()
    s.get(Job, jid).config = {"taxonomia": ["Calderas", "Aerotermia", "Aire acondicionado"]}
    s.commit()
    e = enr.estimar(s, s.get(Job, jid), ["categoria_cliente"], {})
    assert e["llamadas"] == 9 and e["aciertos_cache"] == 0
    s.close()
    fake3 = FakeLLM()
    rid3 = _run(Sesion, jid, ["categoria_cliente"])
    assert enr.ejecutar(rid3, sesion_factory=Sesion, transport=fake3.transport) == enr.COMPLETADA
    assert fake3.llamadas == 9
    assert "Aerotermia" in fake3.peticiones[0]["questions"]["categoria_cliente"]["criteria"]


def test_las_opciones_del_cliente_cambian_la_clave_y_no_las_del_catalogo():
    from shared.decisiones import clave_cache
    from shared.preguntas import obtener
    from shared.preguntas.tipo_pagina import opciones_de_taxonomia

    p = obtener("categoria_cliente")
    a = p.con_opciones(opciones_de_taxonomia(["A", "B"]))
    b = p.con_opciones(opciones_de_taxonomia(["A", "C"]))
    assert a.variante and a.variante != b.variante
    assert clave_cache("x", a) != clave_cache("x", b) != clave_cache("x", p)
    assert a.huella != p.huella and p.variante == ""
    # Las preguntas de siempre no cambian de clave (la cache de produccion sigue valiendo).
    legal = obtener("es_pagina_legal")
    import hashlib
    assert clave_cache("x", legal) == hashlib.sha256(
        f"x|{legal.nombre}|{legal.version}|{legal.modelo}".encode()).hexdigest()
    with pytest.raises(ValueError):
        legal.con_opciones({"a": None, "b": None})
    assert opciones_de_taxonomia(["Solo una"]) is None
    assert opciones_de_taxonomia(["A", "a", " ", "ninguna"]) is None


@pytest.mark.parametrize("taxonomia,error", [
    (["Calderas", "calderas"], "repetida"),
    (["Ninguna", "Calderas"], "ninguna"),
    (["x" * 81, "Calderas"], "80"),
    ([f"c{i}" for i in range(21)], "20"),
])
def test_la_taxonomia_se_valida_en_jobconfig(taxonomia, error):
    pytest.importorskip("pydantic")
    from pydantic import ValidationError

    from api.schemas import JobConfig

    with pytest.raises(ValidationError, match=error):
        JobConfig(taxonomia=taxonomia)


def test_la_taxonomia_sobrevive_al_config_del_job():
    pytest.importorskip("pydantic")
    from api.schemas import JobConfig

    c = JobConfig(taxonomia=["  Calderas ", "", "Aire  acondicionado"])
    assert c.model_dump()["taxonomia"] == ["Calderas", "Aire acondicionado"]


# --- el juego de evaluacion -----------------------------------------------------------------

def _script():
    ruta = Path(__file__).resolve().parent.parent / "scripts" / "evaluar_decisiones.py"
    spec = importlib.util.spec_from_file_location("evaluar_decisiones_script_tp", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extraer_saca_el_juego_de_la_muestra_y_sin_estados_repetidos(tmp_path):
    S = _script()
    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion)
    s = Sesion()
    filas = S.extraer(s, "tipo_pagina", [str(jid)], 50, 0)
    s.close()
    assert len(filas) == 9                                    # lo que pregunta el modulo
    estados = [json.dumps(f["estado"], sort_keys=True) for f in filas]
    assert len(set(estados)) == len(estados)
    assert {f["pregunta"] for f in filas} == {"tipo_pagina"} and "opciones" not in filas[0]


def test_extraer_dedup_rellena_hasta_n_con_el_siguiente(tmp_path, monkeypatch):
    """Con la misma pagina con y sin www en el juego, se salta la copia y se
    coge la siguiente: el juego sigue teniendo `n` ejemplos distintos."""
    S = _script()
    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion)
    # Sin el filtro del modulo: todas las URLs entran (y las de www repiten estado).
    modulo = enr.Modulo(nombre="t", titulo="t", descripcion="", preguntas=("tipo_pagina",),
                        construir_estados=tp.estados_tipo_pagina)
    monkeypatch.setattr(S, "modulo_de", lambda pregunta: modulo)
    s = Sesion()
    filas = S.extraer(s, "tipo_pagina", [str(jid)], 30, 0)
    s.close()
    estados = [json.dumps(f["estado"], sort_keys=True) for f in filas]
    assert len(filas) == 30 and len(set(estados)) == 30


def test_extraer_y_responder_la_categoria_llevan_las_opciones_del_cliente(tmp_path, monkeypatch):
    from shared import decisiones as dec

    S = _script()
    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion, taxonomia=["Calderas", "Aire acondicionado"])
    s = Sesion()
    filas = S.extraer(s, "categoria_cliente", [str(jid)], 50, 0)
    s.close()
    assert filas and filas[0]["opciones"] == {"Calderas": None, "Aire acondicionado": None,
                                              "ninguna": filas[0]["opciones"]["ninguna"]}
    fake = FakeLLM()
    monkeypatch.setenv("DECISIONS_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("ENRICHMENT_TOKEN", "x" * 16)
    for f in filas:
        f["etiqueta"] = "calderas"                             # se normaliza a «Calderas»
    _, respuestas = S.responder(filas, confirmar=True, capa=dec.capa_desde_entorno(transport=fake.transport))
    assert fake.llamadas == len(filas)
    assert list(fake.peticiones[0]["questions"]["categoria_cliente"]["criteria"])[0] == "Calderas"
    informe = S.evaluar(filas, respuestas, objetivo=0.9)
    assert informe["etiquetas_invalidas"] == 0 and informe["acierto"] == 1.0


def test_extraer_la_categoria_sin_taxonomia_lo_dice(tmp_path):
    S = _script()
    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion)
    s = Sesion()
    with pytest.raises(SystemExit, match="taxonomia"):
        S.extraer(s, "categoria_cliente", [str(jid)], 50, 0)
    s.close()


# --- la pantalla del enriquecido ------------------------------------------------------------

def test_la_api_da_la_propuesta_por_plantilla_y_el_tipo_de_la_pregunta(tmp_path, capa_activa):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import enriquecido
    from shared.database import get_session

    Sesion = _motor(tmp_path)
    jid = sembrar(Sesion)
    rid = _run(Sesion, jid, ["tipo_pagina"])
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=FakePorRuta().transport) == enr.COMPLETADA
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
    r = api.get(f"/api/jobs/{jid}/enrichment/{rid}").json()
    tp_res = r["resultados"]["tipo_pagina"]
    assert tp_res["tipo"] == "choice" and tp_res["umbral"] is None
    assert tp_res["por_respuesta"] == {"articulo": 4, "ficha_producto": 4, "home": 1}
    prop = api.get(f"/api/jobs/{jid}/enrichment/{rid}/plantillas", params={"pregunta": "tipo_pagina"}).json()
    assert prop["total_plantillas"] == 3 and prop["plantillas"][0]["urls"] == 40
    assert api.get(f"/api/jobs/{jid}/enrichment/{rid}/plantillas").json()["pregunta"] == "tipo_pagina"
    import uuid
    assert api.get(f"/api/jobs/{jid}/enrichment/{uuid.uuid4()}/plantillas").status_code == 404
