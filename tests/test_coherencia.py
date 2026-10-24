"""Coherencia del title y del marcado con el contenido (#16, #17; decision 177).

Lo que hay que sujetar:

- las funciones puras: que valores del marcado entran (y cuales no: autor,
  fecha de publicacion, listas de un listado) y que fragmentos visibles se
  sacan; sin nada visible con lo que comparar, la pagina no se pregunta;
- los filtros contra SQLite: lo que ya resuelve una regla no se pregunta (title
  vacio, placeholder, duplicado exacto para la alineacion), la portada no entra
  en el generico, y la muestra por plantilla;
- de punta a punta con el `FakeLLM`: los tres modulos escriben sus decisiones,
  todas `incierto` (sin umbral), y la pantalla agrupa el score por nivel.

Nada sale a la red: `conftest.py` hace fallar cualquier transporte real.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("httpx")

import shared.coherencia as coh  # noqa: E402
import shared.enriquecido as enr  # noqa: E402
from shared.models import (  # noqa: E402
    Decision, Heading, HtmlMeta, Issue, Job, PageContent, StructuredData, Url,
)
from test_enriquecido import _decisiones, _motor, _run, capa_activa, sembrar  # noqa: E402,F401


# --- puras ---------------------------------------------------------------------

def test_hechos_del_marcado():
    items = [
        {"@type": "Product", "name": "Libro", "offers": {
            "@type": "Offer", "price": "6.64", "priceCurrency": "EUR",
            "availability": "https://schema.org/InStock"}},
        {"@type": "BlogPosting", "author": {"name": "admin"}, "datePublished": "2026-06-29"},
        {"@type": "WebPage", "datePublished": ["1 oct", "2 oct", "3 oct", "4 oct"]},
        {"@type": "Event", "startDate": "2026-10-21"},
        {"@type": "Hotel", "starRating": {"@type": "Rating", "ratingValue": "3"}},
        {"@context": "https://schema.org", "@graph": [
            {"@type": "Course", "offers": {"@type": "Offer", "price": "1970"}}]},
    ]
    h = coh.hechos_schema(items)
    rutas = {(x["ruta"], x["valor"]) for x in h}
    assert ("Product.offers.price", "6.64") in rutas
    assert ("Product.offers.availability", "InStock") in rutas
    assert ("Event.startDate", "2026-10-21") in rutas
    assert ("Hotel.starRating.ratingValue", "3") in rutas
    assert ("Course.offers.price", "1970") in rutas
    # Ni el autor ni la fecha de publicacion (la firma no esta en el contenido
    # guardado), ni la lista de fechas de un listado.
    assert not any("author" in r or "datePublished" in r for r, _ in rutas)


def test_fragmentos_visibles():
    precio = [{"campo": "price", "ruta": "Offer.price", "valor": "6.64"}]
    texto = ("Una novela larga sobre el mar. " * 10 + "Formatos disponibles 6,64 € 6,99 € "
             + "Leer fragmento. " * 10)
    f = coh.fragmentos_visibles(texto, precio)
    assert "6,64 €" in f and len(f) <= coh.MAX_TEXTO_VISIBLE
    # Sin nada de la clase del valor en el texto: vacio, y no se pregunta.
    assert coh.fragmentos_visibles("Texto sin cifras de ninguna clase.", precio) == ""
    assert coh.fragmentos_visibles("", precio) == ""
    assert coh.fragmentos_visibles(texto, []) == ""
    # Una fecha de evento casa con cualquier fecha escrita; una valoracion con «4,5/5».
    evento = [{"campo": "startDate", "ruta": "Event.startDate", "valor": "2026-10-21"}]
    assert "21 de octubre" in coh.fragmentos_visibles("El concierto es el 21 de octubre de 2026.", evento)
    nota = [{"campo": "ratingValue", "ruta": "AggregateRating.ratingValue", "valor": "4.5"}]
    assert "4,5/5" in coh.fragmentos_visibles("Valoracion media 4,5/5 de 120 clientes.", nota)


def test_muestra_portada_y_palabras():
    filas = [("a", "3", 3), ("a", "1", 1), ("a", "2", 2), ("b", "9", 9)]
    assert coh.muestra_por_plantilla(filas, 2) == [1, 2, 9]
    assert coh.es_portada("/") and coh.es_portada("/es/") and coh.es_portada("/pt-br")
    assert not coh.es_portada("/blog/") and not coh.es_portada("/tv/")
    assert coh.palabras_sin_marca("Detalle noticia | Grupo Lopesan", frozenset({"grupo lopesan"})) == 2
    assert coh.palabras_sin_marca("| Penguin Libros ES", frozenset({"penguin libros es"})) == 0
    assert coh.sitio("WWW.Acme.com") == "acme.com"


# --- filtros contra SQLite -------------------------------------------------------

def _ids(s, jid):
    return sorted(enr.urls_del_alcance(s, jid, {}))


def test_title_contenido_salta_lo_que_resuelve_una_regla(tmp_path, monkeypatch):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=8)
    s = Sesion()
    ids = _ids(s, jid)
    s.get(HtmlMeta, ids[0]).title = ""                                # title_missing
    s.get(HtmlMeta, ids[1]).title = "Sin título"                        # placeholder (decision 121)
    s.add(Issue(job_id=jid, url_id=ids[2], issue_type="title_duplicate", severity="warning", details={}))
    s.get(Url, ids[3]).content_word_count = 12                          # sin contenido que comparar
    s.query(PageContent).filter(PageContent.url_id == ids[4]).delete()
    s.commit()
    assert coh.candidatos_title_contenido(s, jid, ids) == ids[5:]
    # Muestra por plantilla: /blog/:slug y /tienda/:slug, una de cada.
    monkeypatch.setattr(coh, "TITLE_POR_PLANTILLA", 1)
    elegidas = coh.candidatos_title_contenido(s, jid, ids)
    assert len(elegidas) == 2
    assert {s.get(Url, u).path.split("/")[1] for u in elegidas} == {"blog", "tienda"}
    s.close()


def test_title_generico_corto_sin_portada_y_un_estado_por_title(tmp_path, monkeypatch):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=8)
    s = Sesion()
    ids = _ids(s, jid)
    titulos = ["Detalle | Acme", "Detalle | Acme", "Detalle | Acme",
               "Guia completa para elegir una caldera de condensacion | Acme",
               "Sin título | Acme", "Ofertas | Acme", "Acme", "Contacto | Acme"]
    for u, t in zip(ids, titulos):
        s.get(HtmlMeta, u).title = t
    s.get(Url, ids[6]).path = "/"                                        # la portada no entra
    s.commit()
    elegidas = coh.candidatos_title_generico(s, jid, ids)
    # Los tres «Detalle» (duplicados exactos: entran), «Ofertas» y «Contacto».
    assert elegidas == [ids[0], ids[1], ids[2], ids[5], ids[7]]
    estados = coh.estados_title_generico(s, elegidas)
    assert estados[0].datos == {"titulo": "Detalle | Acme", "sitio": "acme.com"}
    assert estados[0].datos == estados[1].datos == estados[2].datos   # una llamada
    # Muestra por plantilla de titles DISTINTOS, con todas sus URLs.
    monkeypatch.setattr(coh, "GENERICO_POR_PLANTILLA", 1)
    assert len(coh.candidatos_title_generico(s, jid, ids)) <= 4
    s.close()


def _con_marcado(s, uid, raw, texto=None):
    s.add(StructuredData(url_id=uid, raw=raw, format="jsonld", schema_type=raw.get("@type")))
    if texto is not None:
        s.get(PageContent, uid).content_text = texto


def test_schema_solo_con_algo_visible_que_comparar(tmp_path, monkeypatch):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=6)
    s = Sesion()
    ids = _ids(s, jid)
    oferta = {"@type": "Offer", "price": "354USD$", "priceCurrency": "EUR", "availability": "InStock"}
    _con_marcado(s, ids[0], oferta, "Habitacion con vistas. Price and availability From 354USD$ Room/Night.")
    _con_marcado(s, ids[1], oferta, "Una habitacion sin cifras en el texto.")               # nada que comparar
    _con_marcado(s, ids[2], {"@type": "BlogPosting", "author": {"name": "admin"},
                             "datePublished": "2026-06-29"}, "Por Carlos el 29 de junio.")  # sin campos que valgan
    s.add(StructuredData(url_id=ids[3], raw=oferta, format="rdfa"))                      # RDFa: fuera
    s.commit()
    assert coh.candidatos_schema(s, jid, ids) == [ids[0]]
    (e,) = coh.estados_schema(s, [ids[0]])
    assert set(e.datos) == {"titulo", "h1", "marcado", "texto_visible"}
    assert "Offer.price: 354USD$" in e.datos["marcado"] and "Offer.priceCurrency: EUR" in e.datos["marcado"]
    assert "354USD$" in e.datos["texto_visible"]
    # Muestra por plantilla.
    _con_marcado(s, ids[4], oferta, "From 354USD$ por noche.")
    _con_marcado(s, ids[5], oferta, "From 354USD$ por noche.")
    s.commit()
    monkeypatch.setattr(coh, "SCHEMA_POR_PLANTILLA", 1)
    assert len(coh.candidatos_schema(s, jid, ids)) == 2                  # una por plantilla
    s.close()


# --- de punta a punta ------------------------------------------------------------

def test_los_tres_modulos_de_punta_a_punta(tmp_path, capa_activa, fake_llm):
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=6)
    s = Sesion()
    ids = _ids(s, jid)
    s.get(HtmlMeta, ids[0]).title = "Detalle | Acme"
    _con_marcado(s, ids[1], {"@type": "Offer", "price": "116€"}, "Reserva ya. Desde 99 € por noche.")
    s.commit()
    job = s.get(Job, jid)
    modulos = ["title_contenido", "title_generico", "schema_visible"]
    e = enr.estimar(s, job, modulos, {})
    assert e["por_modulo"]["title_generico"]["urls"] >= 1
    assert e["por_modulo"]["schema_visible"]["urls"] == 1
    s.close()
    fake = fake_llm(respuestas={"title_describe_contenido": {
        "type": "score", "score": 1.2, "confidence": 0.8,
        "probabilities": {"0": 0.1, "1": 0.8, "2": 0.05, "3": 0.05}}})
    rid = _run(Sesion, jid, modulos=modulos)
    assert enr.ejecutar(rid, sesion_factory=Sesion, transport=fake.transport) == "completada"
    filas = _decisiones(Sesion, rid)
    por = {}
    for f in filas:
        por.setdefault(f.pregunta, []).append(f)
    assert set(por) == {"title_describe_contenido", "title_es_generico", "schema_contradice_contenido"}
    # Sin umbral nada sale decidido (decision 84).
    assert {f.estado for f in filas} == {"incierto"}
    assert {f.respuesta for f in por["title_describe_contenido"]} == {"1.2"}
    # El estado que vio el modelo es el minimo de cada modulo: nunca el HTML.
    enviados = [p["state"] for p in fake.peticiones if "schema_contradice_contenido" in p["questions"]]
    assert enviados and "marcado" in str(enviados[0]) and "<" not in str(enviados[0])


def test_la_pantalla_agrupa_el_score_por_nivel(tmp_path, monkeypatch):
    """Un score se pinta por nivel con su nombre corto, no un decimal por
    entrada, y pinchar un nivel filtra las filas de ese nivel."""
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import enriquecido as router
    from shared.database import get_session

    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=5)
    rid = _run(Sesion, jid, modulos=["title_contenido"])
    s = Sesion()
    ids = _ids(s, jid)
    for n, (u, r) in enumerate(zip(ids, ["2.62", "2.9", "0.3", "1.49", "3.0"])):
        s.add(Decision(job_id=jid, url_id=u, run_id=rid, clave=f"{n:064d}", pregunta="title_describe_contenido",
                       version_pregunta=1, estado="incierto", respuesta=r, origen="modelo",
                       modelo="typesafe/jev-1.13", coste_usd=0.0))
    s.commit()
    s.close()
    app = FastAPI()
    app.include_router(router.router)

    def _sesion():
        x = Sesion()
        try:
            yield x
        finally:
            x.close()

    app.dependency_overrides[get_session] = _sesion
    api = TestClient(app)
    res = api.get(f"/api/jobs/{jid}/enrichment/{rid}").json()["resultados"]["title_describe_contenido"]
    assert res["tipo"] == "score" and res["por_respuesta"] == {"3": 3, "0": 1, "1": 1}
    assert res["etiquetas"]["3"] == "describe el contenido" and res["etiquetas"]["0"] == "otro asunto"
    d = api.get(f"/api/jobs/{jid}/enrichment/{rid}/decisions",
                params={"pregunta": "title_describe_contenido", "respuesta": "3"}).json()
    assert d["total"] == 3 and {x["nivel"] for x in d["items"]} == {"3"}
    d = api.get(f"/api/jobs/{jid}/enrichment/{rid}/decisions",
                params={"pregunta": "title_describe_contenido", "respuesta": "1"}).json()
    assert [x["respuesta"] for x in d["items"]] == ["1.49"]
    assert uuid.UUID(str(rid))


def test_extraer_saca_el_juego_sin_estados_repetidos(tmp_path):
    """La 169 vio que la misma pagina con y sin www contaba dos veces: el juego
    de `extraer` tiene cada estado una vez, y dice cuantos salto."""
    from test_evaluacion_decisiones import _script

    S = _script()
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=8)
    s = Sesion()
    ids = _ids(s, jid)
    for u in ids:
        s.get(HtmlMeta, u).title = "Detalle | Acme"
    s.get(HtmlMeta, ids[0]).title = "Ofertas | Acme"
    s.commit()
    informe: dict = {}
    filas = S.extraer(s, "title_es_generico", [str(jid)], 10, 0, informe)
    assert sorted(f["estado"]["titulo"] for f in filas) == ["Detalle | Acme", "Ofertas | Acme"]
    assert informe["estados_repetidos"] == 6
    assert {f["pregunta"] for f in filas} == {"title_es_generico"} and all(f["etiqueta"] is None for f in filas)
    s.close()
