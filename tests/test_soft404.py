"""Soft 404 determinista (decision 105): reglas puras y analisis contra SQLite.

Cada test falla sin el cambio: o importa algo que no existia, o comprueba una
incidencia (`soft_404`, `soft_404_candidato`) que nadie emitia.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared import soft404 as s4
from shared.models import (Base, Heading, HtmlMeta, Issue, Job, PageContent, StructuredData,
                           Url)


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


# ---------------------------------------------------------------------------
# Sonda
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,esperada", [
    ("https://x.com/", "https://x.com/TOK"),
    ("https://x.com", "https://x.com/TOK"),
    ("https://x.com/es/hoteles/gran-canaria/", "https://x.com/es/hoteles/TOK/"),
    ("https://x.com/p/libro-123.html?utm=1#a", "https://x.com/p/TOK.html"),
    ("https://x.com/guia/v1.2-guia", "https://x.com/guia/TOK"),
])
def test_mangle_path_conserva_la_plantilla(url, esperada):
    assert s4.mangle_path(url, "TOK") == esperada


def test_token_determinista_por_job_y_clave():
    assert s4.token_sonda("j", "x.com|raiz") == s4.token_sonda("j", "x.com|raiz")
    assert s4.token_sonda("j", "x.com|raiz") != s4.token_sonda("k", "x.com|raiz")
    assert s4.token_sonda("j", "a").isalnum()


def test_la_huella_quita_el_eco_de_la_url():
    a = s4.huella("La página /es/zz123abc no existe. Volver al inicio.", "https://x.com/es/zz123abc")
    b = s4.huella("La página /es/hotel-viejo no existe. Volver al inicio.", "https://x.com/es/hotel-viejo")
    assert a.hash == b.hash


def test_misma_huella_exige_titulo_con_poco_texto():
    corta = s4.huella("No existe", "https://x.com/a", "Error | X")
    otra = s4.huella("No existe", "https://x.com/b", "Ficha | X")
    misma = s4.huella("No existe", "https://x.com/c", "Error | X")
    assert s4.misma_huella(corta, otra, 5) is None
    assert s4.misma_huella(corta, misma, 5) == "contenido_y_titulo_identicos"
    vacia = s4.huella("", "https://x.com/a", "Error | X")
    assert s4.misma_huella(vacia, vacia, 5) is None


# ---------------------------------------------------------------------------
# Titulares de error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("titulo,h1", [
    ("Página no encontrada | Lopesan", None),
    ("404 - Not Found", None),
    ("Error 404: página no encontrada", None),
    (None, "¡Ups! Página no encontrada"),
    ("Pàgina no trobada - UOC", None),
    ("Orria ez da aurkitu", None),
    ("Page introuvable – Boutique", None),
    ("Seite nicht gefunden", None),
    ("Página não encontrada", None),
    ("Pagina non trovata | Negozio", None),
    ("404", None),
    ("Not Found", None),
    ("Error 404 - No encontrado", None),
    ("404: NotFound", None),
    (None, "404 – Página no encontrada"),
])
def test_titulos_de_error_seguros(titulo, h1):
    r = s4.senal_titulo(titulo, h1)
    assert r is not None and r[0] == "seguro", r


@pytest.mark.parametrize("titulo", [
    "Cómo arreglar el error 404 en WordPress paso a paso",
    "Los 404 mejores libros del año",
    "Producto no encontrado | Tienda",
    "Paraíso no encontrado (Novela)",
    "Inauguración JJOO 2012: ERROR 404 - Tecnología++",
])
def test_un_articulo_sobre_errores_es_dudoso(titulo):
    assert s4.senal_titulo(titulo, None)[0] == "dudoso"


@pytest.mark.parametrize("titulo", [
    "Hotel Lopesan Costa Meloneras", "Lost and Found: objetos perdidos", "Error de cálculo",
    "Errores comunes al reservar", None,
])
def test_titulos_normales_no_dan_senal(titulo):
    assert s4.senal_titulo(titulo, None) is None


def test_busqueda_y_frase_sin_resultados():
    assert s4.es_busqueda("https://x.com/?s=zapatillas")
    assert s4.es_busqueda("https://x.com/es/buscar/zapatillas")
    assert s4.es_busqueda("https://x.com/catalogsearch/result/?q=a")
    assert not s4.es_busqueda("https://x.com/es/hoteles/?page=2")
    assert s4.frase_sin_resultados("Lo sentimos, no se han encontrado resultados para «xyz».")
    assert s4.frase_sin_resultados("Aucun résultat")
    assert s4.frase_sin_resultados("Nessun risultato trovato")
    assert not s4.frase_sin_resultados("Resultados de la búsqueda: 24 hoteles")


def test_disponibilidad_de_los_datos_estructurados():
    raw = {"@type": "Product", "offers": [{"availability": "https://schema.org/Discontinued"},
                                          {"availability": "http://schema.org/OutOfStock"}]}
    assert s4.disponibilidades(raw) == {"discontinued", "outofstock"}
    assert s4.disponibilidades({"@graph": [{"offers": {"availability": "InStock"}}]}) == {"instock"}


def test_portadas():
    assert s4.es_portada("/") and s4.es_portada("/es/") and s4.es_portada("/pt-br")
    assert not s4.es_portada("/hoteles") and not s4.es_portada("/es/hoteles")


# ---------------------------------------------------------------------------
# Analisis contra SQLite
# ---------------------------------------------------------------------------

ERROR = ("Lo sentimos, la página que buscas ya no está disponible en nuestra web. "
         "Puede que la dirección esté mal escrita o que el contenido se haya movido "
         "a otra sección. Usa el buscador o vuelve a la portada para seguir "
         "navegando por nuestros hoteles y ofertas especiales de temporada.")
NORMAL = " ".join(f"contenido{i} propio de la ficha" for i in range(80))


@pytest.fixture()
def sesion():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, Issue.__table__, PageContent.__table__,
        HtmlMeta.__table__, Heading.__table__, StructuredData.__table__])
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _pagina(s, job_id, uid, path, texto, titulo="Ficha | X", *, indexable=True,
            in_sitemap=False, h1=None):
    s.add(Url(id=uid, job_id=job_id, url=f"https://x.com{path}", url_hash=f"h{uid}",
              host="x.com", path=path.split("?")[0], is_html=True, is_internal=True,
              status_code=200, indexable=indexable, in_sitemap=in_sitemap,
              content_word_count=len(texto.split())))
    s.add(PageContent(url_id=uid, content_text=texto))
    s.add(HtmlMeta(url_id=uid, title=titulo))
    if h1:
        s.add(Heading(url_id=uid, tag="h1", position=0, text=h1))


def _sonda(path="/zz00", status=200, texto=ERROR, titulo="Oops | X", plantilla=None, **extra):
    return {"clave": f"x.com|{plantilla or 'raiz'}", "host": "x.com", "plantilla": plantilla,
            "url": f"https://x.com{path}", "status": status, "redirige_a": None,
            "titulo": titulo, "texto": texto, "origen": "rastreo", **extra}


def _analizar(s, job_id):
    from analysis.analyzer import SEOAnalyzer

    an = SEOAnalyzer(s, job_id)
    an.analyze_content()
    an.analyze_soft_404()
    s.commit()
    return {(i.url_id, i.issue_type): i for i in s.query(Issue).all()}


def test_pagina_con_la_huella_de_la_sonda_es_soft_404(sesion):
    job_id = uuid.uuid4()
    sesion.add(Job(id=job_id, name="t", seeds=[], config={},
                   soft404={"sondas": [_sonda()]}))
    _pagina(sesion, job_id, 1, "/hoteles/viejo", ERROR, in_sitemap=True)
    _pagina(sesion, job_id, 2, "/ofertas/caducada", ERROR.replace("temporada", "verano"))
    _pagina(sesion, job_id, 3, "/hoteles/bueno", NORMAL)
    _pagina(sesion, job_id, 4, "/hoteles/otro", NORMAL.replace("propio", "suyo"))
    _pagina(sesion, job_id, 5, "/hoteles/mas", NORMAL + " extra")
    sesion.commit()

    issues = _analizar(sesion, job_id)
    assert issues[(1, "soft_404")].severity == "error"           # en el sitemap
    assert issues[(1, "soft_404")].details["senales"][0]["metodo"] == "contenido_identico"
    assert issues[(2, "soft_404")].severity == "warning"
    assert issues[(2, "soft_404")].details["senales"][0]["metodo"] == "minhash"
    assert not any(k[0] in (3, 4, 5) for k in issues)
    # Un aviso por hallazgo: la pagina de error no es ademas contenido escaso
    assert (1, "low_word_count") not in issues
    resumen = sesion.get(Job, job_id).soft404["resumen"]
    assert resumen["soft_404"] == 2 and resumen["sondas_con_huella"] == 1


def test_sonda_404_tambien_deja_huella_y_la_redireccion_no(sesion):
    job_id = uuid.uuid4()
    sesion.add(Job(id=job_id, name="t", seeds=[], config={}, soft404={"sondas": [
        _sonda(status=404),
        _sonda(path="/zz11", texto=NORMAL, redirige_a="https://x.com/"),
    ]}))
    _pagina(sesion, job_id, 1, "/a/b", ERROR)
    _pagina(sesion, job_id, 2, "/a/c", NORMAL)
    _pagina(sesion, job_id, 3, "/a/d", NORMAL + " uno")
    _pagina(sesion, job_id, 4, "/a/e", NORMAL + " dos")
    sesion.commit()
    issues = _analizar(sesion, job_id)
    assert (1, "soft_404") in issues
    assert not any(k[0] in (2, 3, 4) for k in issues)


def test_la_sonda_que_da_un_muro_de_waf_no_deja_huella(sesion):
    job_id = uuid.uuid4()
    muro = "Checking if the site connection is secure. " * 10
    sesion.add(Job(id=job_id, name="t", seeds=[], config={},
                   soft404={"sondas": [_sonda(texto=muro, titulo="Just a moment...")]}))
    _pagina(sesion, job_id, 1, "/a/b", muro, titulo="Just a moment...")
    sesion.commit()
    assert not [k for k in _analizar(sesion, job_id) if k[1].startswith("soft_404")]


def test_huella_que_casa_con_media_plantilla_no_se_afirma(sesion):
    """Un shell de SPA o una plantilla vacia: no es una pagina de error."""
    job_id = uuid.uuid4()
    sesion.add(Job(id=job_id, name="t", seeds=[], config={},
                   soft404={"sondas": [_sonda(texto=NORMAL, titulo="App")]}))
    for uid in range(1, 6):
        _pagina(sesion, job_id, uid, f"/p/{uid}", NORMAL, titulo="App")
    _pagina(sesion, job_id, 6, "/p/otra", ERROR)
    sesion.commit()
    issues = _analizar(sesion, job_id)
    assert not [k for k in issues if k[1] == "soft_404"]
    agrupada = [i for k, i in issues.items() if k[1] == "soft_404_candidato"]
    assert len(agrupada) == 1
    assert agrupada[0].details["paginas_afectadas"] == 5
    assert agrupada[0].details["motivos"][0]["senal"] == "huella_de_plantilla"
    assert agrupada[0].group_key


def test_la_portada_nunca_es_soft_404_por_huella(sesion):
    job_id = uuid.uuid4()
    sesion.add(Job(id=job_id, name="t", seeds=[], config={},
                   soft404={"sondas": [_sonda(texto=NORMAL, titulo="Inicio")]}))
    _pagina(sesion, job_id, 1, "/", NORMAL, titulo="Inicio")
    _pagina(sesion, job_id, 2, "/index-viejo", NORMAL, titulo="Inicio")
    for uid in range(3, 10):
        _pagina(sesion, job_id, uid, f"/h/{uid}", ERROR + f" {uid}" * 40)
    sesion.commit()
    issues = _analizar(sesion, job_id)
    assert (1, "soft_404") not in issues and (2, "soft_404") not in issues
    cand = issues[(2, "soft_404_candidato")]
    assert cand.details["motivos"][0]["senal"] == "sonda_devuelve_portada"


def test_titulos_busquedas_y_descatalogados_sin_sonda(sesion):
    job_id = uuid.uuid4()
    sesion.add(Job(id=job_id, name="t", seeds=[], config={}))  # censo sin sondas
    _pagina(sesion, job_id, 1, "/a", "Vuelve al inicio", titulo="Página no encontrada | X")
    _pagina(sesion, job_id, 2, "/blog/b", NORMAL, titulo="Cómo arreglar el error 404 en tu web y no perder visitas")
    _pagina(sesion, job_id, 3, "/buscar?q=zzz", "No se han encontrado resultados para zzz",
            titulo="Buscar | X")
    _pagina(sesion, job_id, 4, "/categoria/vacia", "No hay productos que coincidan con la selección",
            titulo="Vacía | X")
    _pagina(sesion, job_id, 5, "/libro/viejo", NORMAL)
    sesion.add(StructuredData(url_id=5, format="jsonld", schema_type="Product",
                              raw={"@type": "Product", "offers": {"availability": "https://schema.org/Discontinued"}}))
    _pagina(sesion, job_id, 6, "/libro/agotado", NORMAL + " x")
    sesion.add(StructuredData(url_id=6, format="jsonld", schema_type="Product",
                              raw={"@type": "Product", "offers": {"availability": "https://schema.org/OutOfStock"}}))
    _pagina(sesion, job_id, 7, "/noindex", "x", titulo="Page not found", indexable=False)
    _pagina(sesion, job_id, 8, "/noindex-dudosa", "x", titulo="Los 404 libros", indexable=False)
    # Titular de error sobre un articulo de verdad: no es un error
    _pagina(sesion, job_id, 9, "/blog/post", NORMAL, titulo="Error 404 | Blog")
    sesion.commit()

    issues = _analizar(sesion, job_id)
    assert issues[(1, "soft_404")].details["senales"][0]["senal"] == "titulo_de_error"
    assert issues[(2, "soft_404_candidato")].details["candidato_llm"] is True
    assert issues[(3, "soft_404")].details["senales"][0]["senal"] == "busqueda_sin_resultados"
    assert issues[(4, "soft_404_candidato")].details["motivos"][0]["senal"] == "listado_vacio"
    assert (8, "soft_404_candidato") not in issues  # lo dudoso, solo en indexables
    assert (9, "soft_404") not in issues
    assert issues[(9, "soft_404_candidato")].details["motivos"][0]["content_word_count"] == 400
    assert issues[(5, "soft_404_candidato")].details["motivos"][0]["senal"] == "producto_descatalogado"
    assert not any(k[0] == 6 for k in issues if k[1].startswith("soft_404"))
    # Fuera del indice: el enlace roto disfrazado sigue ahi, con info.
    assert issues[(7, "soft_404")].severity == "info"
    assert sesion.get(Job, job_id).soft404["resumen"]["productos_agotados_no_emitidos"] == 1


def test_soft404_corre_en_run_all():
    import inspect

    from analysis.analyzer import SEOAnalyzer

    fuente = inspect.getsource(SEOAnalyzer.run_all)
    assert fuente.index("analyze_content()") < fuente.index("analyze_soft_404()")


def test_umbrales_declarados_en_el_schema():
    """Decision 33: una clave sin declarar se tira en silencio."""
    import ast
    import pathlib

    arbol = ast.parse((pathlib.Path(__file__).resolve().parent.parent / "api" / "schemas.py")
                      .read_text(encoding="utf-8"))
    campos = {}
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ClassDef):
            campos[nodo.name] = {h.target.id for h in nodo.body
                                 if isinstance(h, ast.AnnAssign) and isinstance(h.target, ast.Name)}
    assert {"soft404_similitud", "soft404_min_palabras_igualdad",
            "soft404_max_proporcion_huella"} <= campos["AnalysisThresholdsConfig"]
    assert "soft404_sondas" in campos["CrawlBehaviorConfig"]
