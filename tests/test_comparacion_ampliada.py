"""Comparacion ampliada (decision 131, ola 5.4 de #32).

Tres cosas, ejecutando la comparacion de verdad sobre censos en memoria:

(a) los campos nuevos por pagina —enlaces entrantes, profundidad, PageRank,
    description, tipos de datos estructurados e idiomas de hreflang— con
    umbrales que no disparan con el ruido de un rastreo a otro;
(b) las incidencias nuevas separadas entre URL que ya existia (regresion) y
    URL nueva (publicada mal);
(c) el mapeo de rutas por regex para migraciones, con los mismos guardias
    (decisiones 67, 70 y 91).
"""

from __future__ import annotations

import pytest

from shared.comparacion import (
    MINIMO_PAGINAS, compilar_mapa_rutas, comparar_censos, comparar_incidencias,
)

ANTES = {"semilla": "https://x.com/", "status": "completed",
         "finish_reason": "finished", "crawler_version": "abc1234",
         "analisis_version": "abc1234", "fecha": "2026-01-01T00:00:00"}
LUEGO = {**ANTES, "fecha": "2026-02-01T00:00:00"}


def _pag(ruta, **kw):
    base = {"url": f"https://x.com{ruta}", "status_code": 200,
            "indexability_status": "Indexable", "canonical_href": None,
            "title": "Titulo", "h1": "Titular", "word_count": 500,
            "meta_description": "Una description", "inlinks": 40,
            "click_depth": 2, "pagerank_score": 60,
            "sd_tipos": ["BreadcrumbList", "Product"], "hreflang": ["en", "es"]}
    base.update(kw)
    return base


def _sitio(n=100, **kw):
    return [_pag(f"/p{i}", **kw) for i in range(n)]


def _reglas(r):
    return {a["regla"]: a for a in r["alertas"]}


# --- (a) campos nuevos ------------------------------------------------------

def test_un_censo_identico_no_dispara_nada_ni_cuenta_cambios():
    r = comparar_censos(_sitio(), _sitio(), job_a=ANTES, job_b=LUEGO)
    assert r["alertas"] == []
    for campo in ("meta_description", "sd_tipos", "hreflang", "inlinks",
                  "click_depth", "pagerank_score"):
        assert r["cambios"][campo]["total"] == 0, campo


def test_el_ruido_de_un_rastreo_a_otro_no_es_un_cambio():
    """Lo que se mueve solo entre dos rastreos sanos: un clic arriba o abajo
    porque un listado pagina distinto, unos pocos entrantes de un bloque de
    «relacionados» que rota, 10-20 puntos de PageRank. Ni cambio ni alerta."""
    antes = _sitio()
    ahora = [_pag(f"/p{i}", click_depth=3, inlinks=31, pagerank_score=41)
             for i in range(100)]
    # Una pagina con pocos entrantes que pierde dos de tres: 66%, y ruido.
    antes.append(_pag("/poca", inlinks=3))
    ahora.append(_pag("/poca", inlinks=1))
    r = comparar_censos(antes, ahora, job_a=ANTES, job_b=LUEGO)
    assert r["alertas"] == []
    assert r["cambios"]["click_depth"]["total"] == 0
    assert r["cambios"]["inlinks"]["total"] == 0
    assert r["cambios"]["pagerank_score"]["total"] == 0


def test_una_plantilla_que_pierde_su_marcado_de_producto_alerta():
    antes = _sitio()
    ahora = _sitio(sd_tipos=["BreadcrumbList"])
    r = comparar_censos(antes, ahora, job_a=ANTES, job_b=LUEGO)
    al = _reglas(r)["pierden_datos_estructurados"]
    assert al["paginas"] == 100 and al["de"] == 100
    assert al["ejemplos"][0]["pierde"] == ["Product"]
    fila = r["cambios"]["sd_tipos"]["ejemplos"][0]
    assert fila["pierde"] == ["Product"] and fila["gana"] == []


def test_la_base_de_los_datos_estructurados_son_las_que_los_tenian():
    """«30 de 1.000» no dice nada si solo 30 tenian marcado: es el 100%."""
    antes = _sitio(70, sd_tipos=[]) + [_pag(f"/f{i}") for i in range(30)]
    ahora = _sitio(70, sd_tipos=[]) + [_pag(f"/f{i}", sd_tipos=[]) for i in range(30)]
    al = _reglas(comparar_censos(antes, ahora, job_a=ANTES, job_b=LUEGO))[
        "pierden_datos_estructurados"]
    assert (al["paginas"], al["de"], al["pct"]) == (30, 30, 100.0)


def test_ganar_un_tipo_no_es_perderlo():
    ahora = _sitio(sd_tipos=["BreadcrumbList", "Product", "FAQPage"])
    r = comparar_censos(_sitio(), ahora, job_a=ANTES, job_b=LUEGO)
    assert "pierden_datos_estructurados" not in _reglas(r)
    assert r["cambios"]["sd_tipos"]["total"] == 100


def test_perder_un_idioma_del_hreflang_alerta():
    r = comparar_censos(_sitio(), _sitio(hreflang=["es"]), job_a=ANTES, job_b=LUEGO)
    al = _reglas(r)["pierden_hreflang"]
    assert al["paginas"] == 100 and al["ejemplos"][0]["pierde"] == ["en"]


def test_un_cambio_masivo_de_description_es_info():
    r = comparar_censos(_sitio(), _sitio(meta_description="Otra"),
                        job_a=ANTES, job_b=LUEGO)
    al = _reglas(r)["cambia_la_description"]
    assert al["severidad"] == "info" and al["paginas"] == 100
    assert r["cambios"]["meta_description"]["total"] == 100


def test_dos_clics_mas_lejos_o_sin_camino_es_alejarse_de_la_home():
    antes = _sitio()
    ahora = ([_pag(f"/p{i}", click_depth=4) for i in range(60)]
             + [_pag(f"/p{i}", click_depth=None) for i in range(60, 100)])
    al = _reglas(comparar_censos(antes, ahora, job_a=ANTES, job_b=LUEGO))[
        "se_alejan_de_la_home"]
    assert al["paginas"] == 100
    assert {e["ahora"] for e in al["ejemplos"]} <= {4, None}


def test_perder_la_mitad_de_los_entrantes_alerta():
    r = comparar_censos(_sitio(), _sitio(inlinks=12), job_a=ANTES, job_b=LUEGO)
    assert _reglas(r)["pierden_enlaces_internos"]["paginas"] == 100
    assert r["cambios"]["inlinks"]["total"] == 100


def test_bajar_una_decada_de_pagerank_alerta():
    r = comparar_censos(_sitio(), _sitio(pagerank_score=34), job_a=ANTES, job_b=LUEGO)
    assert _reglas(r)["pierden_pagerank"]["paginas"] == 100


def test_el_enlazado_es_un_hallazgo_por_pagina():
    """Un menu que deja de enlazar una seccion aleja sus paginas, les quita
    entrantes y PageRank a la vez: es UN hallazgo (decision 36), el primero."""
    r = comparar_censos(_sitio(), _sitio(click_depth=5, inlinks=2, pagerank_score=10),
                        job_a=ANTES, job_b=LUEGO)
    reglas = _reglas(r)
    assert "se_alejan_de_la_home" in reglas
    assert "pierden_enlaces_internos" not in reglas
    assert "pierden_pagerank" not in reglas


def test_con_un_censo_truncado_el_grafo_no_se_compara():
    """Si el anterior paro por `max_urls`, las paginas que enlazaban pueden no
    haberse rastreado: «pierden enlaces» seria falso (decision 67)."""
    truncado = {**ANTES, "finish_reason": "max_urls_reached"}
    r = comparar_censos(_sitio(), _sitio(click_depth=None, inlinks=1, pagerank_score=5),
                        job_a=truncado, job_b=LUEGO)
    reglas = _reglas(r)
    for regla in ("se_alejan_de_la_home", "pierden_enlaces_internos", "pierden_pagerank"):
        assert regla not in reglas
    for campo in ("inlinks", "click_depth", "pagerank_score"):
        assert r["cambios"][campo]["total"] is None
        assert "grafo" in r["cambios"][campo]["motivo"]


def test_con_alcances_distintos_tampoco():
    a = {**ANTES, "semillas": ["https://x.com/es/"]}
    b = {**LUEGO, "semillas": ["https://x.com/en/"]}
    r = comparar_censos(_sitio(), _sitio(inlinks=1), job_a=a, job_b=b)
    assert "pierden_enlaces_internos" not in _reglas(r)
    assert r["cambios"]["inlinks"]["total"] is None


def test_con_el_analisis_distinto_el_grafo_no_se_compara():
    """Entrantes, profundidad y PageRank los calcula el ANALISIS, no el
    rastreo: con dos versiones del analizador una caida puede ser nuestra (la
    decision 38 movio el PageRank de un censo entero al re-analizarlo). Es la
    regla de la 91 para las incidencias, y desconocido tampoco vale."""
    for va, vb in (("a1", "a2"), (None, "a2"), ("a1", None)):
        a = {**ANTES, "analisis_version": va}
        b = {**LUEGO, "analisis_version": vb}
        r = comparar_censos(_sitio(), _sitio(click_depth=6, inlinks=1, pagerank_score=5),
                            job_a=a, job_b=b)
        reglas = _reglas(r)
        for regla in ("se_alejan_de_la_home", "pierden_enlaces_internos", "pierden_pagerank"):
            assert regla not in reglas, (va, vb, regla)
        for campo in ("inlinks", "click_depth", "pagerank_score"):
            assert r["cambios"][campo]["total"] is None
            assert "analiz" in r["cambios"][campo]["motivo"]
        # Lo que sale del rastreo se sigue comparando.
        assert r["cambios"]["sd_tipos"]["total"] == 0


def test_una_lista_no_compara_el_grafo_con_un_censo_completo():
    """Una lista (`max_depth` 0, decision 128) no sigue enlaces: sus entrantes
    solo cuentan los de las otras URLs de la lista, y frente al censo completo
    TODAS «perderian» sus enlaces. Ni con dos profundidades distintas: lo que
    no se rastreo no enlaza."""
    for pa, pb in ((3, 0), (0, 0), (3, 5)):
        a = {**ANTES, "max_depth": pa}
        b = {**LUEGO, "max_depth": pb}
        r = comparar_censos(_sitio(), _sitio(inlinks=1, pagerank_score=5),
                            job_a=a, job_b=b)
        assert "pierden_enlaces_internos" not in _reglas(r), (pa, pb)
        assert r["cambios"]["inlinks"]["total"] is None
        assert "profundidad" in r["cambios"]["inlinks"]["motivo"]
    # La misma profundidad, o una sin dato, si se compara.
    for pa, pb in ((3, 3), (None, 3)):
        r = comparar_censos(_sitio(), _sitio(inlinks=1),
                            job_a={**ANTES, "max_depth": pa}, job_b={**LUEGO, "max_depth": pb})
        assert "pierden_enlaces_internos" in _reglas(r)


def test_resumen_de_job_lleva_la_profundidad():
    from types import SimpleNamespace

    from shared.comparacion import resumen_de_job
    job = SimpleNamespace(seeds=["https://x.com/"], config={"max_depth": 0},
                          status="completed", finish_reason="finished",
                          crawler_version="v", analisis_version="v", health=None,
                          name="lista", started_at=None)
    assert resumen_de_job(job)["max_depth"] == 0


def test_un_censo_sin_profundidad_no_esta_a_profundidad_desconocida():
    """Un censo anterior a `click_depth` lo lleva a NULL en todas: eso es «no
    se sabe», no «ninguna pagina llega desde la home»."""
    r = comparar_censos(_sitio(click_depth=None), _sitio(), job_a=ANTES, job_b=LUEGO)
    assert "se_alejan_de_la_home" not in _reglas(r)
    assert r["cambios"]["click_depth"]["total"] is None
    # Ni al reves: si el NUEVO no la tiene en ninguna pagina es que no se
    # rastreo la home (decision 90), no que el sitio entero sea una isla.
    r = comparar_censos(_sitio(), _sitio(click_depth=None), job_a=ANTES, job_b=LUEGO)
    assert "se_alejan_de_la_home" not in _reglas(r)
    # Con la home medida, las que se quedan sin camino si cuentan.
    ahora = [_pag("/p0", click_depth=0)] + [_pag(f"/p{i}", click_depth=None)
                                           for i in range(1, 100)]
    r = comparar_censos(_sitio(), ahora, job_a=ANTES, job_b=LUEGO)
    assert _reglas(r)["se_alejan_de_la_home"]["paginas"] == 99


def test_una_pagina_que_se_rompe_no_pierde_ademas_su_marcado():
    """Si pasa a 404 ya es `rotas` y `status_code`; que «pierda» sus datos
    estructurados es el mismo hallazgo (decision 36)."""
    r = comparar_censos(_sitio(), _sitio(status_code=404, sd_tipos=[], hreflang=[]),
                        job_a=ANTES, job_b=LUEGO)
    reglas = _reglas(r)
    assert "rotas" in reglas
    assert "pierden_datos_estructurados" not in reglas
    assert r["cambios"]["sd_tipos"]["total"] == 0


def test_por_debajo_del_umbral_no_salta():
    n = MINIMO_PAGINAS - 1
    ahora = [_pag(f"/p{i}", sd_tipos=[]) for i in range(n)] + [
        _pag(f"/p{i}") for i in range(n, 1000)]
    r = comparar_censos(_sitio(1000), ahora, job_a=ANTES, job_b=LUEGO)
    assert "pierden_datos_estructurados" not in _reglas(r)
    assert r["cambios"]["sd_tipos"]["total"] == n


# --- (c) mapeo de rutas ------------------------------------------------------

MIGRACION = [[r"^/es/hoteles/([^/]+)\.html$", r"/es/hotel/\1/"],
             [r"^/blog/(\d{4})/\d{2}/([^/]+)/$", r"/blog/\2/"]]


def _viejo(n=200):
    return ([_pag(f"/es/hoteles/h{i}.html") for i in range(n // 2)]
            + [_pag(f"/blog/2025/0{1 + i % 9}/post-{i}/") for i in range(n // 2)])


def _nuevo(n=200, sin=0):
    """Las mismas paginas con las rutas nuevas, menos `sin` que no migraron."""
    return ([_pag(f"/es/hotel/h{i}/") for i in range(n // 2)]
            + [_pag(f"/blog/post-{i}/") for i in range(sin, n // 2)])


def test_una_migracion_simulada_empareja_mas_del_95_por_ciento():
    sin = _nuevo(sin=4)
    r = comparar_censos(_viejo(), sin, job_a=ANTES, job_b=LUEGO)
    assert r["urls"]["en_ambos"] == 0          # sin mapeo: nada casa
    assert r["urls"]["pct_emparejadas_del_anterior"] == 0.0

    r = comparar_censos(_viejo(), sin, job_a=ANTES, job_b=LUEGO, mapa_rutas=MIGRACION)
    m = r["mapeo_rutas"]
    assert m["pct_emparejadas"] > 95
    assert (m["emparejadas"], m["urls_del_anterior"], m["reescritas"]) == (196, 200, 200)
    assert [x["reescritas"] for x in m["reglas"]] == [100, 100]
    assert m["colisiones"] == 0
    # Lo que no tiene pareja se afirma como desaparecido: los dos censos estan
    # enteros y el alcance es el mismo.
    assert r["urls"]["desaparecidas"] == 4 and r["urls"]["nuevas"] == 0
    assert r["alertas"] == []


def test_el_mapeo_va_sobre_el_ANTERIOR_aunque_se_pida_al_reves():
    """«Antes» lo pone la fecha (decision 67): el desplegable no puede decidir
    a que censo se le aplican las rutas viejas."""
    r = comparar_censos(_nuevo(), _viejo(), job_a=LUEGO, job_b=ANTES, mapa_rutas=MIGRACION)
    assert r["mapeo_rutas"]["pct_emparejadas"] == 100.0


def test_con_el_mapeo_los_cambios_se_ven_entre_la_url_vieja_y_la_nueva():
    nuevo = _nuevo()
    nuevo[0] = {**nuevo[0], "title": "Otro titulo"}
    r = comparar_censos(_viejo(), nuevo, job_a=ANTES, job_b=LUEGO, mapa_rutas=MIGRACION)
    assert r["cambios"]["title"]["total"] == 1
    assert r["cambios"]["title"]["ejemplos"][0]["url"] == "https://x.com/es/hotel/h0/"


def test_gana_la_primera_regla_que_casa():
    reglas = [[r"^/a/(.*)$", r"/b/\1"], [r"^/a/x$", r"/nunca"]]
    r = comparar_censos([_pag("/a/x")], [_pag("/b/x")], job_a=ANTES, job_b=LUEGO,
                        mapa_rutas=reglas)
    assert r["urls"]["en_ambos"] == 1
    assert [x["reescritas"] for x in r["mapeo_rutas"]["reglas"]] == [1, 0]


def test_el_mapeo_reescribe_la_ruta_y_no_el_host():
    reglas = [[r"x\.com", "otro.com"]]
    r = comparar_censos([_pag("/x.com")], [_pag("/otro.com")], job_a=ANTES,
                        job_b=LUEGO, mapa_rutas=reglas)
    assert r["comparable"] and r["urls"]["en_ambos"] == 1


def test_la_query_tambien_se_puede_mapear():
    reglas = [[r"^/ficha\?id=(\d+)$", r"/producto/\1"]]
    r = comparar_censos([_pag("/ficha?id=7")], [_pag("/producto/7")], job_a=ANTES,
                        job_b=LUEGO, mapa_rutas=reglas)
    assert r["urls"]["en_ambos"] == 1


def test_dos_urls_viejas_que_caen_en_la_misma_se_cuentan():
    reglas = [[r"^/(es|es-es)/(.*)$", r"/es/\2"]]
    r = comparar_censos([_pag("/es/a"), _pag("/es-es/a")], [_pag("/es/a")],
                        job_a=ANTES, job_b=LUEGO, mapa_rutas=reglas)
    assert r["mapeo_rutas"]["colisiones"] == 1


def test_las_semillas_viejas_tambien_se_mapean():
    """Tras una migracion la semilla vieja es la nueva con otro nombre: sin
    mapearla, el guardia de alcance (decision 70) declararia otro alcance."""
    a = {**ANTES, "semillas": ["https://x.com/es/hoteles/h0.html"]}
    b = {**LUEGO, "semillas": ["https://x.com/es/hotel/h0/"]}
    r = comparar_censos(_viejo(), _nuevo(), job_a=a, job_b=b)
    assert r["concluyente"] is False
    r = comparar_censos(_viejo(), _nuevo(), job_a=a, job_b=b, mapa_rutas=MIGRACION)
    assert r["concluyente"] is True


def test_el_mapeo_no_se_salta_el_guardia_del_censo_truncado():
    truncado = {**LUEGO, "finish_reason": "max_urls_reached"}
    r = comparar_censos(_viejo(), _nuevo(sin=10), job_a=ANTES, job_b=truncado,
                        mapa_rutas=MIGRACION)
    assert r["concluyente"] is False
    assert r["urls"]["desaparecidas"] is None
    assert r["urls"]["desaparecidas_no_afirmables"] == 10


def test_ni_el_guardia_de_origen():
    otro = {**LUEGO, "semilla": "https://competidor.com/"}
    r = comparar_censos(_viejo(), _nuevo(), job_a=ANTES, job_b=otro, mapa_rutas=MIGRACION)
    assert r["comparable"] is False


@pytest.mark.parametrize("reglas, texto", [
    ([["^/a/(", "/b"]], "no compila"),
    ([["^/a/(.*)$", r"/b/\2"]], "reemplazo"),
    ([["^/a/(.*)$", r"/b/\g<nombre>"]], "reemplazo"),
    ([["", "/b"]], "vacio"),
    ([["^/a"]], "[patron, reemplazo]"),
    ("^/a", "lista"),
    ([["^/a", "/b"]] * 51, "como mucho"),
])
def test_una_regla_mal_escrita_se_rechaza_diciendo_cual(reglas, texto):
    with pytest.raises(ValueError, match=None) as e:
        compilar_mapa_rutas(reglas)
    assert texto in str(e.value)
    with pytest.raises(ValueError):
        comparar_censos([_pag("/a")], [_pag("/b")], job_a=ANTES, job_b=LUEGO,
                        mapa_rutas=reglas)


def test_las_reglas_pueden_venir_como_dicts():
    assert len(compilar_mapa_rutas([{"patron": "^/a", "reemplazo": "/b"}])) == 1


# --- (b) e incidencias con el mapeo -------------------------------------------

def _inc(tipo, url, *, sev="warning", grupo=None):
    return {"issue_type": tipo, "severity": sev, "group_key": grupo, "url": url}


def test_las_nuevas_se_separan_entre_url_existente_y_url_nueva():
    antes = [_inc("missing_title", "https://x.com/a")]
    ahora = [_inc("missing_title", "https://x.com/a"),
             _inc("missing_description", "https://x.com/a"),   # regresion
             _inc("missing_description", "https://x.com/nueva"),  # publicada mal
             _inc("missing_h1", "https://x.com/nueva")]
    r = comparar_incidencias(antes, ahora, analisis_a="v1", analisis_b="v1",
                             urls_del_anterior={"https://x.com/a"})
    n = r["nuevas"]
    assert n["total"] == 3
    assert n["en_url_existente"]["total"] == 1
    assert n["en_url_existente"]["por_tipo"][0]["tipo"] == "missing_description"
    assert n["en_url_nueva"]["total"] == 2
    assert n["en_url_nueva"]["ejemplos"] == ["https://x.com/nueva"] * 2
    assert n["url_nueva_afirmable"] is True


def test_con_el_anterior_truncado_la_url_nueva_no_es_afirmable():
    r = comparar_incidencias([], [_inc("missing_h1", "https://x.com/b")],
                             analisis_a="v1", analisis_b="v1",
                             puede_afirmar_resueltas=False,
                             urls_del_anterior={"https://x.com/a"})
    assert r["nuevas"]["en_url_nueva"]["total"] == 1
    assert r["nuevas"]["url_nueva_afirmable"] is False


def test_sin_las_urls_del_anterior_no_se_inventa_el_reparto():
    r = comparar_incidencias([], [_inc("missing_h1", "https://x.com/b")],
                             analisis_a="v1", analisis_b="v1")
    assert "en_url_nueva" not in r["nuevas"]


def test_con_el_mapeo_una_incidencia_migrada_sigue_y_no_es_nueva():
    antes = [_inc("duplicate_title", "https://x.com/es/hoteles/h1.html", grupo="g1")]
    ahora = [_inc("duplicate_title", "https://x.com/es/hotel/h1/", grupo="g1")]
    sin = comparar_incidencias(antes, ahora, analisis_a="v1", analisis_b="v1")
    assert sin["nuevas"]["total"] == 1 and sin["resueltas"]["total"] == 1
    con = comparar_incidencias(antes, ahora, analisis_a="v1", analisis_b="v1",
                               mapa_rutas=MIGRACION,
                               urls_del_nuevo={"https://x.com/es/hotel/h1/"},
                               urls_del_anterior={"https://x.com/es/hotel/h1/"})
    assert con["siguen"]["total"] == 1
    assert con["nuevas"]["total"] == 0 and con["resueltas"]["total"] == 0


def test_el_mapeo_no_se_salta_el_guardia_del_analisis():
    """Decision 91: con codigo de analisis distinto no se comparan las
    incidencias, haya mapeo o no."""
    r = comparar_incidencias([], [], analisis_a="v1", analisis_b="v2",
                             mapa_rutas=MIGRACION)
    assert r["comparable"] is False


def test_tras_la_migracion_el_canonical_propio_no_es_un_cambio():
    """`/x.html` con canonical a si misma y `/x/` con canonical a si misma no
    han cambiado de canonical: el mapeo se aplica tambien al canonical de
    antes. Visto en la interfaz con progym migrado: 3.205 «cambios» falsos."""
    viejo = [_pag(f"/es/hoteles/h{i}.html", canonical_href=f"https://x.com/es/hoteles/h{i}.html")
             for i in range(30)]
    nuevo = [_pag(f"/es/hotel/h{i}/", canonical_href=f"https://x.com/es/hotel/h{i}/")
             for i in range(30)]
    nuevo[0]["canonical_href"] = "https://x.com/es/hotel/h1/"
    r = comparar_censos(viejo, nuevo, job_a=ANTES, job_b=LUEGO, mapa_rutas=MIGRACION)
    assert r["cambios"]["canonical_href"]["total"] == 1
    assert "canonical_a_otra_url" not in _reglas(r)
