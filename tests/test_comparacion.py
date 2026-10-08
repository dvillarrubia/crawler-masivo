"""Comparar dos censos del mismo sitio (#32).

Las cuatro decisiones de criterio, cada una evitando una forma de mentir:
emparejar por URL normalizada, no afirmar ausencias si un censo esta truncado,
no comparar dos sitios distintos, y avisar si el codigo no era el mismo.
"""

from __future__ import annotations

from shared.comparacion import comparar_censos

JOB_OK = {"semilla": "https://x.com/", "status": "completed",
          "finish_reason": "finished", "crawler_version": "abc1234"}


def _pag(ruta, **kw):
    base = {"url": f"https://x.com{ruta}", "status_code": 200,
            "indexability_status": "Indexable", "canonical_href": None,
            "title": "Titulo", "h1": "Titular", "word_count": 500}
    base.update(kw)
    return base


def test_una_pagina_que_cambia_de_titulo_sale_como_cambio():
    r = comparar_censos([_pag("/a")], [_pag("/a", title="Otro")],
                        job_a=JOB_OK, job_b=JOB_OK)
    assert r["comparable"] and r["concluyente"]
    assert r["cambios"]["title"]["total"] == 1
    assert r["cambios"]["title"]["ejemplos"][0]["antes"] == "Titulo"
    assert r["cambios"]["title"]["ejemplos"][0]["ahora"] == "Otro"
    assert r["cambios"]["h1"]["total"] == 0


def test_el_orden_de_los_parametros_no_hace_dos_urls():
    a = [_pag("/b?x=1&y=2")]
    b = [_pag("/b?y=2&x=1")]
    r = comparar_censos(a, b, job_a=JOB_OK, job_b=JOB_OK)
    assert r["urls"]["en_ambos"] == 1
    assert r["urls"]["nuevas"] == 0 and r["urls"]["desaparecidas"] == 0


def test_la_barra_final_SI_hace_dos_urls():
    """`/a` y `/a/` pueden servir cosas distintas y normalmente una redirige a
    la otra: emparejarlas taparia que un sitio ha cambiado de convencion."""
    r = comparar_censos([_pag("/a/")], [_pag("/a")], job_a=JOB_OK, job_b=JOB_OK)
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
    r = comparar_censos([_pag("/a")], [], job_a=cancelado, job_b=JOB_OK)
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
                        [_pag("/a")], job_a=pre, job_b=JOB_OK)
    assert r["comparable"] is False


def test_dos_versiones_distintas_del_crawler_se_avisan():
    """Una diferencia puede ser nuestra y no del sitio (decision 66)."""
    viejo = {**JOB_OK, "crawler_version": "aaa1111"}
    r = comparar_censos([_pag("/a")], [_pag("/a", title="Otro")],
                        job_a=viejo, job_b=JOB_OK)
    assert any("versiones distintas" in a for a in r["avisos"])


def test_la_misma_version_no_genera_aviso():
    r = comparar_censos([_pag("/a")], [_pag("/a")], job_a=JOB_OK, job_b=JOB_OK)
    assert r["avisos"] == []


def test_si_no_se_sabe_la_version_tambien_se_avisa():
    """Desconocido no es igual. Callarse equivale a afirmar que se hicieron
    con el mismo codigo, que es justo lo que no se sabe.

    Comprobado con dos censos reales de progym anteriores al sello: 439
    paginas cambian su recuento de palabras y el cambio es NUESTRO.
    """
    sin_sello = {**JOB_OK, "crawler_version": None}
    r = comparar_censos([_pag("/a")], [_pag("/a")],
                        job_a=sin_sello, job_b=sin_sello)
    assert any("No se sabe con que version" in a for a in r["avisos"])
    r2 = comparar_censos([_pag("/a")], [_pag("/a")],
                         job_a=sin_sello, job_b=JOB_OK)
    assert any("el primer censo" in a for a in r2["avisos"])


def test_el_recuento_de_palabras_tiene_umbral():
    """Una fecha o un contador mueven el texto sin que cambie la pagina."""
    r = comparar_censos([_pag("/a", word_count=500)],
                        [_pag("/a", word_count=520)],
                        job_a=JOB_OK, job_b=JOB_OK)
    assert r["cambios"]["word_count"]["total"] == 0, "4% es ruido"
    r2 = comparar_censos([_pag("/a", word_count=500)],
                         [_pag("/a", word_count=200)],
                         job_a=JOB_OK, job_b=JOB_OK)
    assert r2["cambios"]["word_count"]["total"] == 1, "-60% no lo es"


def test_el_caso_lopesan_sale_como_cambio_de_canonical():
    """2.367 paginas que pasan a canonicalizarse a otro host: el caso real que
    motivo todo esto. Aqui en pequeno."""
    antes = [_pag(f"/hotel{i}") for i in range(5)]
    despues = [_pag(f"/hotel{i}", canonical_href=f"https://origen.lfr.cloud/hotel{i}",
                    indexability_status="Canonicalised") for i in range(5)]
    r = comparar_censos(antes, despues, job_a=JOB_OK, job_b=JOB_OK)
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
    ida = comparar_censos(a, b, job_a=JOB_OK, job_b=JOB_OK)
    vuelta = comparar_censos(b, a, job_a=JOB_OK, job_b=JOB_OK)
    assert (ida["cambios"]["word_count"]["total"]
            == vuelta["cambios"]["word_count"]["total"] == 1)

    # Y justo por debajo del umbral, tampoco en ninguno de los dos sentidos.
    c = [_pag("/a", word_count=100)]
    d = [_pag("/a", word_count=115)]
    assert comparar_censos(c, d, job_a=JOB_OK, job_b=JOB_OK)["cambios"]["word_count"]["total"] == 0
    assert comparar_censos(d, c, job_a=JOB_OK, job_b=JOB_OK)["cambios"]["word_count"]["total"] == 0
