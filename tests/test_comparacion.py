"""Comparar dos censos del mismo sitio (#32).

Las cuatro decisiones de criterio, cada una evitando una forma de mentir:
emparejar por URL normalizada, no afirmar ausencias si un censo esta truncado,
no comparar dos sitios distintos, y avisar si el codigo no era el mismo.
"""

from __future__ import annotations

from shared.comparacion import comparar_censos

JOB_OK = {"semilla": "https://x.com/", "status": "completed",
          "finish_reason": "finished", "crawler_version": "abc1234",
          "fecha": "2026-01-01T00:00:00"}
# El de despues. Las dos fechas tienen que ser distintas porque "antes" y
# "ahora" los pone la FECHA, no el orden de los parametros: sin eso no se
# afirma ninguna direccion y no se emite ninguna alerta.
JOB_LUEGO = {**JOB_OK, "fecha": "2026-02-01T00:00:00"}


def _pag(ruta, **kw):
    base = {"url": f"https://x.com{ruta}", "status_code": 200,
            "indexability_status": "Indexable", "canonical_href": None,
            "title": "Titulo", "h1": "Titular", "word_count": 500}
    base.update(kw)
    return base


def test_una_pagina_que_cambia_de_titulo_sale_como_cambio():
    r = comparar_censos([_pag("/a")], [_pag("/a", title="Otro")],
                        job_a=JOB_OK, job_b=JOB_LUEGO)
    assert r["comparable"] and r["concluyente"]
    assert r["cambios"]["title"]["total"] == 1
    assert r["cambios"]["title"]["ejemplos"][0]["antes"] == "Titulo"
    assert r["cambios"]["title"]["ejemplos"][0]["ahora"] == "Otro"
    assert r["cambios"]["h1"]["total"] == 0


def test_el_orden_de_los_parametros_no_hace_dos_urls():
    a = [_pag("/b?x=1&y=2")]
    b = [_pag("/b?y=2&x=1")]
    r = comparar_censos(a, b, job_a=JOB_OK, job_b=JOB_LUEGO)
    assert r["urls"]["en_ambos"] == 1
    assert r["urls"]["nuevas"] == 0 and r["urls"]["desaparecidas"] == 0


def test_la_barra_final_SI_hace_dos_urls():
    """`/a` y `/a/` pueden servir cosas distintas y normalmente una redirige a
    la otra: emparejarlas taparia que un sitio ha cambiado de convencion."""
    r = comparar_censos([_pag("/a/")], [_pag("/a")], job_a=JOB_OK, job_b=JOB_LUEGO)
    assert r["urls"]["en_ambos"] == 0
    assert r["urls"]["nuevas"] == 1 and r["urls"]["desaparecidas"] == 1


def test_un_censo_truncado_no_permite_afirmar_que_falte_nada():
    """Si paro por `max_urls`, las URLs que no estan es que no se llegaron."""
    truncado = {**JOB_OK, "finish_reason": "max_urls_reached"}
    r = comparar_censos([_pag("/a"), _pag("/b")], [_pag("/a")],
                        job_a=JOB_OK, job_b=truncado)
    assert r["concluyente"] is False
    assert r["urls"]["desaparecidas"] is None
    assert r["urls"]["desaparecidas_no_afirmables"] == 1
    assert "NO CONCLUYENTE" in r["avisos"][0]


def test_un_censo_cancelado_tampoco():
    cancelado = {**JOB_OK, "status": "cancelled", "finish_reason": None}
    r = comparar_censos([_pag("/a")], [], job_a=cancelado, job_b=JOB_LUEGO)
    assert r["concluyente"] is False


def test_dos_sitios_distintos_se_rechazan():
    otro = {**JOB_OK, "semilla": "https://competidor.com/"}
    r = comparar_censos([_pag("/a")], [_pag("/a")], job_a=JOB_OK, job_b=otro)
    assert r["comparable"] is False
    assert "dos origenes distintos" in r["motivo"]


def test_con_mapeo_de_host_si_se_comparan():
    """Produccion contra preproduccion es legitimo, pero hay que pedirlo."""
    pre = {**JOB_OK, "semilla": "https://pre.x.com/"}
    paginas_pre = [{**_pag("/a"), "url": "https://pre.x.com/a"}]
    r = comparar_censos(paginas_pre, [_pag("/a")], job_a=pre, job_b=JOB_OK,
                        mapa_hosts={"pre.x.com": "x.com"})
    assert r["comparable"] is True
    assert r["urls"]["en_ambos"] == 1, "con el mapeo son la misma pagina"


def test_sin_mapeo_el_mismo_caso_se_rechaza():
    pre = {**JOB_OK, "semilla": "https://pre.x.com/"}
    r = comparar_censos([{**_pag("/a"), "url": "https://pre.x.com/a"}],
                        [_pag("/a")], job_a=pre, job_b=JOB_LUEGO)
    assert r["comparable"] is False


def test_dos_versiones_distintas_del_crawler_se_avisan():
    """Una diferencia puede ser nuestra y no del sitio (decision 66)."""
    viejo = {**JOB_OK, "crawler_version": "aaa1111"}
    r = comparar_censos([_pag("/a")], [_pag("/a", title="Otro")],
                        job_a=viejo, job_b=JOB_LUEGO)
    assert any("versiones distintas" in a for a in r["avisos"])


def test_la_misma_version_no_genera_aviso():
    r = comparar_censos([_pag("/a")], [_pag("/a")], job_a=JOB_OK, job_b=JOB_LUEGO)
    assert r["avisos"] == []


def test_si_no_se_sabe_la_version_tambien_se_avisa():
    """Desconocido no es igual. Callarse equivale a afirmar que se hicieron
    con el mismo codigo, que es justo lo que no se sabe.

    Comprobado con dos censos reales de progym anteriores al sello: 439
    paginas cambian su recuento de palabras y el cambio es NUESTRO.
    """
    sin_sello = {**JOB_OK, "crawler_version": None}
    r = comparar_censos([_pag("/a")], [_pag("/a")],
                        job_a=sin_sello, job_b={**sin_sello, "fecha": JOB_LUEGO["fecha"]})
    assert any("No se sabe con que version" in a for a in r["avisos"])
    r2 = comparar_censos([_pag("/a")], [_pag("/a")],
                         job_a=sin_sello, job_b=JOB_LUEGO)
    assert any("el primer censo" in a for a in r2["avisos"])


def test_el_recuento_de_palabras_tiene_umbral():
    """Una fecha o un contador mueven el texto sin que cambie la pagina."""
    r = comparar_censos([_pag("/a", word_count=500)],
                        [_pag("/a", word_count=520)],
                        job_a=JOB_OK, job_b=JOB_LUEGO)
    assert r["cambios"]["word_count"]["total"] == 0, "4% es ruido"
    r2 = comparar_censos([_pag("/a", word_count=500)],
                         [_pag("/a", word_count=200)],
                         job_a=JOB_OK, job_b=JOB_LUEGO)
    assert r2["cambios"]["word_count"]["total"] == 1, "-60% no lo es"


def test_el_caso_lopesan_sale_como_cambio_de_canonical():
    """2.367 paginas que pasan a canonicalizarse a otro host: el caso real que
    motivo todo esto. Aqui en pequeno."""
    antes = [_pag(f"/hotel{i}") for i in range(5)]
    despues = [_pag(f"/hotel{i}", canonical_href=f"https://origen.lfr.cloud/hotel{i}",
                    indexability_status="Canonicalised") for i in range(5)]
    r = comparar_censos(antes, despues, job_a=JOB_OK, job_b=JOB_LUEGO)
    assert r["cambios"]["canonical_href"]["total"] == 5
    assert r["cambios"]["indexability_status"]["total"] == 5


def test_comparar_en_un_sentido_y_en_el_otro_da_lo_mismo():
    """Si no, el informe depende de cual pongas primero.

    Lo vi usando la vista: dos censos reales de progym daban 1.639 paginas
    con el texto cambiado en un sentido y 439 en el otro, con los mismos
    datos. El umbral se medi­a sobre el "antes"; ahora sobre el mayor de los
    dos, que es simetrico.
    """
    a = [_pag("/a", word_count=100)]
    b = [_pag("/a", word_count=130)]
    ida = comparar_censos(a, b, job_a=JOB_OK, job_b=JOB_LUEGO)
    vuelta = comparar_censos(b, a, job_a=JOB_OK, job_b=JOB_LUEGO)
    assert (ida["cambios"]["word_count"]["total"]
            == vuelta["cambios"]["word_count"]["total"] == 1)

    # Y justo por debajo del umbral, tampoco en ninguno de los dos sentidos.
    c = [_pag("/a", word_count=100)]
    d = [_pag("/a", word_count=115)]
    assert comparar_censos(c, d, job_a=JOB_OK, job_b=JOB_LUEGO)["cambios"]["word_count"]["total"] == 0
    assert comparar_censos(d, c, job_a=JOB_OK, job_b=JOB_LUEGO)["cambios"]["word_count"]["total"] == 0


# --- Alertas: cuando un cambio deja de ser trabajo de una pagina ------------
#
# La pregunta que contestan es la que no contestaba nada: en Lopesan, 2.367
# paginas canonicalizadas a un host de preproduccion se entregaron como un
# aviso `info` entre 315.119 incidencias, el 91% de ellas `image_missing_alt`.
# Un cambio que afecta a una parte grande de las indexables no es trabajo
# editorial: es una plantilla, una configuracion o un despliegue.

def _alertas_de(r, regla=None):
    als = {a["regla"]: a for a in r["alertas"]}
    return als if regla is None else als.get(regla)


def test_el_caso_lopesan_dispara_una_alerta_critica():
    """El fallo real, en pequeno: 40 de 40 fichas de hotel a un host ajeno."""
    antes = [_pag(f"/hotel{i}") for i in range(40)]
    despues = [_pag(f"/hotel{i}", canonical_href=f"https://origen.lfr.cloud/hotel{i}",
                    indexability_status="Canonicalised") for i in range(40)]
    al = _alertas_de(comparar_censos(antes, despues, job_a=JOB_OK, job_b=JOB_LUEGO),
                     "canonical_a_otro_host")
    assert al is not None, "esto es exactamente lo que tenia que avisar y no avisaba"
    assert al["severidad"] == "critical"
    assert (al["paginas"], al["de"], al["pct"]) == (40, 40, 100.0)
    assert "otro host" in al["que_decide_google"].lower() or "OTRO host" in al["que_decide_google"]


def test_el_canonical_que_se_va_fuera_cuenta_aunque_la_pagina_ya_no_fuera_indexable():
    """Lo que abandona el sitio es el DESTINO de la consolidacion.

    Medido en Lopesan: de las 2.367 paginas que acabaron apuntando a
    `webserver-lopesan-prd.lfr.cloud`, **1.832 ya estaban canonicalizadas** a
    una URL legitima del sitio. Con la regla de "era indexable y deja de
    serlo" se quedaban fuera las 1.832, y la alerta decia 476 de 2.271 (21%)
    en vez de 2.319 de 4.193 (55,3%).
    """
    antes = [_pag(f"/p{i}", canonical_href="https://x.com/canonica",
                  indexability_status="Canonicalised") for i in range(30)]
    despues = [_pag(f"/p{i}", canonical_href="https://origen.lfr.cloud/canonica",
                    indexability_status="Canonicalised") for i in range(30)]
    al = _alertas_de(comparar_censos(antes, despues, job_a=JOB_OK, job_b=JOB_LUEGO),
                     "canonical_a_otro_host")
    assert al is not None and al["paginas"] == 30


def test_un_cambio_de_dos_paginas_no_es_una_alerta():
    """El piso en paginas: en un censo de 30 URLs "el 10%" son tres."""
    antes = [_pag(f"/p{i}") for i in range(40)]
    despues = [_pag(f"/p{i}") for i in range(40)]
    despues[0] = _pag("/p0", status_code=404, indexability_status="Client Error (404)")
    despues[1] = _pag("/p1", status_code=404, indexability_status="Client Error (404)")
    r = comparar_censos(antes, despues, job_a=JOB_OK, job_b=JOB_LUEGO)
    assert r["alertas"] == []
    assert r["cambios"]["status_code"]["total"] == 2, "sigue estando en el diff"


def test_una_plantilla_entera_rota_dispara_aunque_se_diluya_en_el_sitio():
    """Las 2.367 de Lopesan son el 99,5% de su plantilla y el 11% del sitio."""
    antes = ([_pag(f"/fichas/{i}-producto") for i in range(25)]
             + [_pag(f"/blog/articulo-{i}") for i in range(600)])
    despues = ([_pag(f"/fichas/{i}-producto", status_code=404,
                     indexability_status="Client Error (404)") for i in range(25)]
               + [_pag(f"/blog/articulo-{i}") for i in range(600)])
    al = _alertas_de(comparar_censos(antes, despues, job_a=JOB_OK, job_b=JOB_LUEGO),
                     "rotas")
    assert al is not None, "25 de 625 es el 4% del sitio: solo salta por plantilla"
    assert al["por"] == "plantilla"
    assert al["plantillas"][0]["pct"] == 100.0


def test_un_aviso_por_hallazgo_tambien_en_las_alertas():
    """Una pagina que sale del indice PORQUE le pusieron un canonical fuera no
    se cuenta ademas en `salen_del_indice`: diria lo mismo sin decir que
    arreglar (decision 36)."""
    antes = [_pag(f"/p{i}") for i in range(30)]
    despues = [_pag(f"/p{i}", canonical_href="https://otro.com/p",
                    indexability_status="Canonicalised") for i in range(30)]
    r = comparar_censos(antes, despues, job_a=JOB_OK, job_b=JOB_LUEGO)
    assert _alertas_de(r, "canonical_a_otro_host") is not None
    assert _alertas_de(r, "salen_del_indice") is None
    assert _alertas_de(r, "canonical_a_otra_url") is None


def test_el_denominador_son_las_paginas_que_podian_tenerlo():
    """"2.367 paginas" no dice nada sin "de 2.379". Y una pagina que ya estaba
    en noindex no entra en el denominador de "sale del indice"."""
    antes = ([_pag(f"/ok{i}") for i in range(30)]
             + [_pag(f"/no{i}", indexability_status="Noindex") for i in range(970)])
    despues = ([_pag(f"/ok{i}", indexability_status="Noindex") for i in range(30)]
               + [_pag(f"/no{i}", indexability_status="Noindex") for i in range(970)])
    al = _alertas_de(comparar_censos(antes, despues, job_a=JOB_OK, job_b=JOB_LUEGO),
                     "salen_del_indice")
    assert al is not None, "30 de 30 indexables es el 100%, no el 3% de las 1.000"
    assert (al["paginas"], al["de"], al["pct"]) == (30, 30, 100.0)


def test_la_direccion_la_pone_la_fecha_no_el_orden_de_los_parametros():
    """Una pagina que sale del indice y una que entra son hallazgos opuestos:
    no pueden depender de cual se elija primero en el desplegable."""
    antes = [_pag(f"/p{i}") for i in range(30)]
    despues = [_pag(f"/p{i}", status_code=404,
                    indexability_status="Client Error (404)") for i in range(30)]
    ida = comparar_censos(antes, despues, job_a=JOB_OK, job_b=JOB_LUEGO)
    vuelta = comparar_censos(despues, antes, job_a=JOB_LUEGO, job_b=JOB_OK)
    assert _alertas_de(ida, "rotas") == _alertas_de(vuelta, "rotas")
    assert ida["antes"]["fecha"] == vuelta["antes"]["fecha"] == JOB_OK["fecha"]


def test_sin_fecha_no_se_afirma_ninguna_direccion_y_no_hay_alertas():
    sin_fecha = {**JOB_OK, "fecha": None}
    antes = [_pag(f"/p{i}") for i in range(30)]
    despues = [_pag(f"/p{i}", status_code=404,
                    indexability_status="Client Error (404)") for i in range(30)]
    r = comparar_censos(antes, despues, job_a=sin_fecha, job_b=JOB_LUEGO)
    assert r["direccion_sabida"] is False
    assert r["alertas"] == []
    assert any("no se emiten alertas" in a.lower() for a in r["avisos"])
    assert r["cambios"]["status_code"]["total"] == 30, "el diff si se calcula"


def test_un_censo_truncado_no_puede_alertar_de_paginas_que_desaparecen():
    """Las que no estan es que no se llegaron a ellas, no que falten."""
    truncado = {**JOB_LUEGO, "finish_reason": "max_urls_reached"}
    antes = [_pag(f"/p{i}") for i in range(100)]
    r = comparar_censos(antes, [_pag("/p0")], job_a=JOB_OK, job_b=truncado)
    assert _alertas_de(r, "desaparecen") is None


def test_las_paginas_que_desaparecen_si_alertan_cuando_el_censo_esta_entero():
    antes = [_pag(f"/p{i}") for i in range(100)]
    r = comparar_censos(antes, [_pag(f"/p{i}") for i in range(50)],
                        job_a=JOB_OK, job_b=JOB_LUEGO)
    al = _alertas_de(r, "desaparecen")
    assert al is not None and (al["paginas"], al["de"]) == (50, 100)


def test_w3lib_esta_disponible_donde_corre_la_comparacion():
    """Este test corre en las DOS imagenes, y ahi esta su razon de ser.

    `_norm` tenia un `except` que caia a `url.strip().rstrip("/")`, y la imagen
    de la API no llevaba w3lib: el endpoint de comparacion emparejaba las URLs
    con el criterio CONTRARIO al documentado en los dos casos que importan
    —`?a=1&b=2` no casaba con `?b=2&a=1`, y `/a/` si casaba con `/a`— sin que
    nada lo dijera. Lo que pinta de detalle de empaquetado era el criterio de
    comparacion cambiando segun donde corriera el codigo.
    """
    from w3lib.url import canonicalize_url

    assert canonicalize_url("https://x.com/a?b=2&a=1") == "https://x.com/a?a=1&b=2"


# --- Alcance: dos rastreos del mismo host no son el mismo censo -------------

def test_dos_alcances_distintos_no_permiten_afirmar_ausencias():
    """Medido con los dos censos de cst.gov.sa del mismo dia: uno sembrado en
    el arbol castellano y otro en el ingles, **0 semillas en comun de 4.780**.
    La comparacion afirmaba que habian desaparecido 834 paginas que nunca
    estuvieron en el alcance, y 3.839 que "entraban en el indice".
    """
    es = {**JOB_OK, "semillas": ["https://x.com/es/a", "https://x.com/es/b"]}
    en = {**JOB_LUEGO, "semillas": ["https://x.com/en/a", "https://x.com/en/b"]}
    r = comparar_censos([_pag("/es/a"), _pag("/es/b")], [_pag("/en/a")],
                        job_a=es, job_b=en)
    assert r["comparable"] is True, "mismo host: las URLs comunes si se comparan"
    assert r["concluyente"] is False
    assert r["urls"]["desaparecidas"] is None
    assert any("ALCANCES DISTINTOS" in a for a in r["avisos"])


def test_un_censo_que_amplia_el_alcance_si_permite_afirmar_ausencias():
    """Lo normal al re-rastrear: el sitemap ha crecido. Medido en Lopesan,
    2.935 de 2.935 semillas del censo viejo siguen en el nuevo; y el canario de
    penguin, 1.950 de 1.950 en su censo completo. Con Jaccard ese segundo caso
    daria 0,022 y se rechazaria: lo que importa es la CONTENCION."""
    antes = {**JOB_OK, "semillas": ["https://x.com/a"]}
    despues = {**JOB_LUEGO, "semillas": ["https://x.com/a", "https://x.com/b"]}
    r = comparar_censos([_pag("/a"), _pag("/viejo")], [_pag("/a")],
                        job_a=antes, job_b=despues)
    assert r["concluyente"] is True
    assert r["urls"]["desaparecidas"] == 1
    assert not any("ALCANCES" in a for a in r["avisos"])


def test_las_semillas_no_viajan_en_la_respuesta():
    """Son 87.429 en el censo de penguin. Queda el recuento."""
    muchas = {**JOB_OK, "semillas": [f"https://x.com/s{i}" for i in range(500)]}
    r = comparar_censos([_pag("/a")], [_pag("/a")],
                        job_a=muchas, job_b={**muchas, "fecha": JOB_LUEGO["fecha"]})
    assert "semillas" not in r["antes"] and r["antes"]["n_semillas"] == 500


def test_un_censo_con_render_y_otro_sin_el_se_avisa():
    """El que renderiza ve enlaces que el otro no llega a ver: es lo que mide
    `js_check` (decision 35)."""
    r = comparar_censos([_pag("/a")], [_pag("/a")],
                        job_a={**JOB_OK, "render_js": False},
                        job_b={**JOB_LUEGO, "render_js": True})
    assert any("renderiza JavaScript" in a for a in r["avisos"])
    r2 = comparar_censos([_pag("/a")], [_pag("/a")],
                         job_a={**JOB_OK, "render_js": True},
                         job_b={**JOB_LUEGO, "render_js": True})
    assert not any("renderiza JavaScript" in a for a in r2["avisos"])


def test_el_resumen_del_job_lleva_las_semillas_y_el_render():
    """El guardia de alcance vive en la funcion pura, pero los datos los pone
    quien la llama: si dejan de enviarse, la comprobacion no salta y nada lo
    dice. Es el mismo agujero que el de `w3lib` (decision 69), en el otro
    extremo. Ahora el resumen es UNO —lo usan el endpoint y el worker— porque
    dos copias es como se llega a dos cifras que dicen medir lo mismo.
    """
    from types import SimpleNamespace

    from shared.comparacion import resumen_de_job as _resumen_de_job

    job = SimpleNamespace(
        seeds=["https://x.com/a", "https://x.com/b"], status="completed",
        finish_reason="finished", crawler_version="abc1234", name="censo",
        started_at=None, config={"render_js": True})
    r = _resumen_de_job(job)
    assert r["semillas"] == ["https://x.com/a", "https://x.com/b"]
    assert r["render_js"] is True


# --- La comparacion automatica al cerrar un rastreo (#36) -------------------

def test_el_censo_anterior_es_el_mas_reciente_del_mismo_sitio():
    """Un rastreo de otro sitio no sirve, y uno posterior tampoco."""
    import uuid
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from shared.comparacion import censo_anterior
    from shared.models import Base, Job

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Job.__table__])
    s = sessionmaker(bind=engine)()

    def job(nombre, semilla, dia, estado="completed"):
        j = Job(id=uuid.uuid4(), name=nombre, status=estado, seeds=[semilla],
                config={}, started_at=datetime(2026, 1, dia))
        s.add(j)
        s.flush()
        return j

    job("otro sitio", "https://otro.com/", 5)
    viejo = job("el viejo", "https://x.com/", 1)
    medio = job("el de en medio", "https://x.com/", 5)
    actual = job("el actual", "https://x.com/", 10)
    job("posterior", "https://x.com/", 20)

    assert censo_anterior(s, actual).id == medio.id
    assert censo_anterior(s, medio).id == viejo.id
    assert censo_anterior(s, viejo) is None, "el primero de un sitio no tiene con que"


def test_un_rastreo_sin_terminar_no_sirve_de_referencia():
    """Comparar contra uno truncado o fallido no permitiria afirmar nada."""
    import uuid
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from shared.comparacion import censo_anterior
    from shared.models import Base, Job

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Job.__table__])
    s = sessionmaker(bind=engine)()
    for nombre, dia, estado in [("fallido", 1, "failed"), ("cancelado", 2, "cancelled"),
                                ("corriendo", 3, "running")]:
        s.add(Job(id=uuid.uuid4(), name=nombre, status=estado,
                  seeds=["https://x.com/"], config={}, started_at=datetime(2026, 1, dia)))
    actual = Job(id=uuid.uuid4(), name="actual", status="completed",
                 seeds=["https://x.com/"], config={}, started_at=datetime(2026, 1, 9))
    s.add(actual)
    s.flush()
    assert censo_anterior(s, actual) is None


def test_la_comparacion_automatica_de_extremo_a_extremo():
    """Del rastreo cerrado a la alerta guardada, con filas de verdad.

    El gancho del worker es fino a proposito, asi que lo que hay que probar es
    este camino: dos censos en base de datos -> `comparar_con_el_anterior` ->
    una alerta critica con su denominador. Es el caso de Lopesan en pequeno.
    """
    import uuid
    from datetime import datetime

    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    from shared.comparacion import comparar_con_el_anterior
    from shared.models import Base, Heading, HtmlMeta, Job, Url

    @compiles(BigInteger, "sqlite")
    def _bigint(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Heading.__table__])
    s = sessionmaker(bind=engine)()

    def censo(nombre, dia, canonical_a_staging):
        j = Job(id=uuid.uuid4(), name=nombre, status="completed",
                finish_reason="finished", seeds=["https://x.com/"], config={},
                started_at=datetime(2026, 1, dia), crawler_version="abc1234")
        s.add(j)
        s.flush()
        for i in range(30):
            ruta = f"/hotel{i}"
            u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=f"{nombre}{ruta}",
                    is_internal=True, is_html=True, status_code=200,
                    indexability_status="Indexable", word_count=500)
            s.add(u)
            s.flush()
            can = (f"https://origen.lfr.cloud{ruta}" if canonical_a_staging
                   else f"https://x.com{ruta}")
            s.add(HtmlMeta(url_id=u.id, title="Titulo", canonical_href=can))
        s.flush()
        return j

    censo("el anterior", 1, False)
    actual = censo("el nuevo", 5, True)

    r = comparar_con_el_anterior(s, actual)
    assert r is not None and r["comparable"] is True
    assert r["comparado_con"] and r["nombre_anterior"] == "el anterior"
    criticas = [a for a in r["alertas"] if a["severidad"] == "critical"]
    assert [a["regla"] for a in criticas] == ["canonical_a_otro_host"]
    assert (criticas[0]["paginas"], criticas[0]["de"]) == (30, 30)


def test_el_primer_censo_de_un_sitio_no_inventa_una_comparacion():
    import uuid
    from datetime import datetime

    from sqlalchemy import BigInteger, create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    from shared.comparacion import comparar_con_el_anterior
    from shared.models import Base, Heading, HtmlMeta, Job, Url

    @compiles(BigInteger, "sqlite")
    def _bigint2(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        Job.__table__, Url.__table__, HtmlMeta.__table__, Heading.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="el unico", status="completed",
            seeds=["https://x.com/"], config={}, started_at=datetime(2026, 1, 1))
    s.add(j)
    s.flush()
    assert comparar_con_el_anterior(s, j) is None


def test_un_censo_en_analisis_no_puede_afirmar_ausencias():
    """Por que la comparacion va DESPUES de escribir el estado final.

    Para la comparacion, cualquier estado que no sea `completed` es un censo
    truncado (decision 67). Llamandola junto a la comprobacion de render, la
    fila del job todavia dice `analyzing`: el censo de ahora se daria por
    incompleto SIEMPRE y nunca se podria afirmar que una pagina ha
    desaparecido. Este test fija el motivo, para que mover la llamada "a un
    sitio mas logico" no lo rompa en silencio.
    """
    en_analisis = {**JOB_LUEGO, "status": "analyzing"}
    r = comparar_censos([_pag("/a"), _pag("/b")], [_pag("/a")],
                        job_a=JOB_OK, job_b=en_analisis)
    assert r["concluyente"] is False
    assert r["urls"]["desaparecidas"] is None

    ya_cerrado = {**JOB_LUEGO, "status": "completed"}
    r2 = comparar_censos([_pag("/a"), _pag("/b")], [_pag("/a")],
                         job_a=JOB_OK, job_b=ya_cerrado)
    assert r2["concluyente"] is True and r2["urls"]["desaparecidas"] == 1
