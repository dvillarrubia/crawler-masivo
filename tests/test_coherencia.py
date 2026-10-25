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
from pathlib import Path

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


def test_marcado_por_objeto():
    """El precio y la disponibilidad de cada variante van en una linea: aplanados,
    Jev no sabia que la talla agotada era la de 13,99 y daba contradiccion
    (decision 185). La oferta repetida en los microdatos sale una vez; un valor
    suelto sigue como «Tipo.campo: valor»."""
    items = [
        {"@type": "ProductGroup", "aggregateRating": {"ratingValue": 4.4, "reviewCount": 14},
         "offers": {"priceCurrency": "EUR", "availability": "https://schema.org/InStock"},
         "hasVariant": [
             {"@type": "Product", "offers": {"price": "14.99", "availability": "InStock"}},
             {"@type": "Product", "offers": {"price": "13.89", "availability": "InStock"}},
             {"@type": "Product", "offers": {"price": "13.99", "availability": "OutOfStock"}}]},
        {"@type": "Offer", "price": "13.99", "availability": "https://schema.org/OutOfStock"},
        {"@type": "Event", "startDate": "2026-10-21"},
    ]
    h = coh.hechos_schema(items)
    assert coh.lineas_marcado(h) == [
        "ProductGroup.aggregateRating: ratingValue 4.4, reviewCount 14",
        "ProductGroup.offers: priceCurrency EUR, availability InStock",
        "ProductGroup.hasVariant.offers: price 14.99, availability InStock",
        "ProductGroup.hasVariant.offers: price 13.89, availability InStock",
        "ProductGroup.hasVariant.offers: price 13.99, availability OutOfStock",
        "Event.startDate: 2026-10-21",
    ]
    # Dos variantes en stock no pierden su segundo InStock, como al aplanar.
    assert sum(x["valor"] == "InStock" for x in h) == 3
    # El tope cuenta objetos, no valores, y no parte uno.
    muchas = [{"@type": "Offer", "price": str(n), "availability": "InStock"} for n in range(10, 40)]
    h2 = coh.hechos_schema(muchas)
    assert len(coh.lineas_marcado(h2)) == coh.MAX_HECHOS and len(h2) == 2 * coh.MAX_HECHOS


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


def test_fragmentos_no_pierden_el_valor_declarado_por_el_corte():
    """El fragmento del valor declarado entra aunque salga tarde en el texto,
    detras de otros precios y disponibilidades: sin el, Jev ve «178» en el
    marcado y solo «99 / 120 / 135 / 150» en lo visible, y contesta contradice
    (revision de #242)."""
    relleno = "texto de relleno sin cifras " * 12
    texto = ""
    for p in ["99€", "120€", "135€", "150€"]:
        texto += relleno + f"Habitacion desde {p} por noche. "
    texto += relleno + " Disponible. " + relleno + " Disponible. " + relleno
    texto += "Precio de esta habitacion: 178€ por noche." + relleno
    h = coh.hechos_schema([{"@type": "Offer", "price": "178", "availability": "https://schema.org/InStock"}])
    f = coh.fragmentos_visibles(texto, h)
    assert "178€" in f and len(f) <= coh.MAX_TEXTO_VISIBLE
    # Y lo generico rellena lo que queda, sin pasarse.
    assert "99€" in f
    # Muchos valores: nunca pasa del tope, y cada trozo entra entero o no entra.
    largo = " ".join(f"{relleno} precio {n}€ disponible" for n in range(10, 60))
    h2 = coh.hechos_schema([{"@type": "Offer", "price": str(n)} for n in (10, 11, 12)])
    f2 = coh.fragmentos_visibles(largo, h2)
    assert len(f2) <= coh.MAX_TEXTO_VISIBLE and "10€" in f2


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
    # La ruta va en el estado: «SAT» en /sats/a-coruna y en /sat/ no es el
    # mismo caso, y sin ella la definicion no los separa (revision de #242).
    assert estados[0].datos == {"titulo": "Detalle | Acme", "sitio": "acme.com",
                                "ruta": s.get(Url, ids[0]).path}
    assert len({e.datos["ruta"] for e in estados[:3]}) == 3
    # Muestra por plantilla de titles DISTINTOS, y por title hasta
    # GENERICO_URLS_POR_TITLE de sus URLs.
    # (los tres «Detalle» estan en dos plantillas: /tienda/ y /blog/).
    monkeypatch.setattr(coh, "GENERICO_URLS_POR_TITLE", 1)
    elegidas = coh.candidatos_title_generico(s, jid, ids)
    assert len([u for u in elegidas if u in ids[:3]]) == 2
    monkeypatch.setattr(coh, "GENERICO_POR_PLANTILLA", 1)
    assert len(coh.candidatos_title_generico(s, jid, ids)) <= 4
    s.close()


def test_title_solo_marca_no_se_pregunta(tmp_path):
    """Un title que, quitada la marca, se queda sin palabras es generico (y no
    dice nada del contenido) por construccion: lo resuelve una regla, no Jev."""
    Sesion = _motor(tmp_path)
    jid, _ = sembrar(Sesion, n=6)
    s = Sesion()
    ids = _ids(s, jid)
    titulos = ["Ofertas | Acme", "Contacto | Acme", "Blog | Acme", "Tienda | Acme", "Acme", "| Acme"]
    for u, t in zip(ids, titulos):
        s.get(HtmlMeta, u).title = t
    s.commit()
    assert coh.solo_marca("Acme", frozenset({"acme"})) and coh.solo_marca("| Acme", frozenset({"acme"}))
    # Dos segmentos que pasan los dos por marca: no se sabe cual es, se pregunta.
    assert not coh.solo_marca("Detalle | Acme", frozenset({"acme", "detalle"}))
    assert not set(ids[4:]) & set(coh.candidatos_title_generico(s, jid, ids))
    assert not set(ids[4:]) & set(coh.candidatos_title_contenido(s, jid, ids))
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
    assert e.datos["marcado"] == ["Offer: price 354USD$, priceCurrency EUR, availability InStock"]
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
    assert res["etiquetas"]["3"] == "nombra la página" and res["etiquetas"]["0"] == "otro asunto o engaña"
    # Las opciones de un score son sus niveles: la pantalla los pinta en orden
    # con el mismo `respuestasEnriq` que un choice (decision 176).
    assert res["opciones"] == ["0", "1", "2", "3"] and res["ambito"] == "url"
    d = api.get(f"/api/jobs/{jid}/enrichment/{rid}/decisions",
                params={"pregunta": "title_describe_contenido", "respuesta": "3"}).json()
    assert d["total"] == 3 and {x["nivel"] for x in d["items"]} == {"3"}
    assert all(x["busqueda"] is None and x["rutas"] is None for x in d["items"])
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
    # La misma pagina con www: mismo title, mismo sitio y misma ruta.
    con_www = s.get(Url, ids[3])
    con_www.host, con_www.path = "www.acme.com", s.get(Url, ids[1]).path
    con_www.url = "https://www.acme.com" + con_www.path
    s.commit()
    informe: dict = {}
    filas = S.extraer(s, "title_es_generico", [str(jid)], 10, 0, informe)
    assert len(filas) == 7 and informe["estados_repetidos"] == 1
    assert sum(f["estado"]["ruta"] == s.get(Url, ids[1]).path for f in filas) == 1
    assert {f["pregunta"] for f in filas} == {"title_es_generico"} and all(f["etiqueta"] is None for f in filas)
    s.close()


def test_title_v2_el_nivel_alto_es_nombrar_la_pagina_aunque_sea_corto():
    """Decision 180: la v1 puntuaba 1 o 2 el title que nombra el libro, el
    destino o la seccion porque el fragmento hablaba del autor o el H1 era un
    eslogan. En la v2 el nivel alto es «nombra lo que la pagina es», los bajos
    son otro asunto, generico o engaño, y los ejemplos son genericos (no salen
    del juego con el que se midio la v1, que lo inflarian)."""
    from shared.preguntas import CATALOGO

    p = CATALOGO["title_describe_contenido"]
    assert p.version == 2 and len(p.opciones) == len(p.etiquetas) == 4
    assert "even briefly" in p.opciones[3] and "different subject" in p.opciones[0]
    assert "generic" in p.opciones[1] and "parent item" in p.opciones[2]
    assert "biography" in p.instrucciones and "slogan" in p.instrucciones
    # Revision: los metadatos de un producto son parte de la pagina, pero una
    # lista de documentos SOBRE el item que nombra el title es nivel 2, y un
    # title de evento u oferta que nada de lo visible menciona es nivel 0: sin
    # esas dos excepciones la v2 no separaba los pocos negativos del juego.
    assert "ABOUT the item the title names" in p.instrucciones and "(level 2)" in p.instrucciones
    assert "event, offer or promotion" in p.instrucciones and "(level 0)" in p.instrucciones
    assert "date" not in p.instrucciones.split("Two exceptions")[0]
    for de_un_cliente in ("Penguin", "La Salle", "Fuerteventura", "Lopesan", "tigres", "Carnaval",
                          "Maspalomas", "Memòria", "Meloneras"):
        assert de_un_cliente not in p.instrucciones + " ".join(p.opciones)


def test_title_generico_v2_el_nombre_propio_no_es_generico():
    """Decision 182: la v1 decia «generico» a titles cortos con nombre propio
    (el cliente de un caso, «<marca> <pais>», un proyecto) y «no generico» a
    la etiqueta que tienen todos los items de una seccion. La v2 lo escribe a
    los dos lados, con una prueba explicita para la frontera de (5)."""
    from shared.preguntas import CATALOGO

    p = CATALOGO["title_es_generico"]
    assert p.version == 2 and set(p.opciones) == {"true", "false"}
    i = p.instrucciones
    assert i.index("Answer false") < i.index("Answer true") < i.index("When unsure")
    assert "read it as a name" in i and "'<Brand> <country>'" in i
    assert "decide with the test for (5) instead" in i[i.index("When unsure"):]
    for de_un_cliente in ("Penguin", "La Salle", "Salle", "Lopesan", "Workoholics", "Iberdrola",
                          "ICOBEN", "CAF", "Saunier", "SAT", "SafiraAqua", "Culpa", "Chile",
                          "Fotovoltaica", "Normativa", "Work |", "headerpage", "Re_",
                          "Scholarships", "Accessible rooms", "Solar plant", "Downloads"):
        assert de_un_cliente not in i + " ".join(p.opciones.values()), de_un_cliente


def test_ningun_ejemplo_del_title_generico_sale_de_los_juegos_con_que_se_mide():
    """Decision 182: un ejemplo copiado de un title de los juegos (como
    «Scholarships and grants» en el primer borrador de la v2) infla la medida
    (169, 180). Se compara cada ejemplo citado con las huellas de los titles
    del juego de 200, el de precision y el conjunto de 2.000, no con una lista
    a mano. Si se re-extraen los juegos, regenerar el fichero con
    `scripts/evaluar_decisiones.py huellas`."""
    from shared.evaluacion_decisiones import fugas
    from shared.preguntas.coherencia import TITLE_ES_GENERICO as p

    fichero = Path(__file__).parent / "fixtures" / "titles_juegos_title_es_generico.txt"
    huellas = {x.strip() for x in fichero.read_text(encoding="utf-8").splitlines()
               if x.strip() and not x.startswith("#")}
    assert len(huellas) > 3000
    texto = p.instrucciones + " " + " ".join(p.opciones.values())
    # Sin lista blanca: «Home», «Contact» y «Projects», que se admitian como
    # vocabulario de la definicion, son titles de los juegos (y «Projects»,
    # sinonimo de un falso positivo medido): se cambiaron por otros sin huella.
    assert fugas(texto, huellas) == []
    # Y la comprobacion de verdad pilla las fugas que tuvieron los borradores.
    assert fugas("a specific topic ('Scholarships and grants')", huellas)
    assert fugas("its own page ('Projects' on /projects/)", huellas)


def _casos_title_generico():
    from shared.preguntas.coherencia import CASOS_FRONTERA

    return CASOS_FRONTERA


@pytest.mark.parametrize("caso,respuesta,frase", _casos_title_generico())
def test_la_frase_de_cada_caso_frontera_sigue_en_su_parte(caso, respuesta, frase):
    """Cada caso real que caia entre las dos respuestas (falsos positivos y
    negativos de la v1, los pares contradictorios de la regla (5) y los
    positivos que hay que conservar) tiene la frase que lo decide en la parte
    de su respuesta y no en la otra. Vigila que nadie la mueva o la borre; no
    prueba que el significado de la otra parte no lo cubra tambien."""
    from shared.preguntas.coherencia import TITLE_ES_GENERICO

    i = TITLE_ES_GENERICO.instrucciones
    partes = {"false": i[i.index("Answer false"):i.index("Answer true")],
              "true": i[i.index("Answer true"):i.index("When unsure")]}
    reclaman = [r for r, texto in partes.items() if frase in texto]
    assert reclaman == [respuesta], (caso, reclaman)
