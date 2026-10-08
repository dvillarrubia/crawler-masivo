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
