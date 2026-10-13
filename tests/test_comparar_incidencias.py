"""Qué se arregló, qué apareció y qué sigue entre dos censos (#32).

Comparar páginas no contesta «qué arregló el cliente desde la auditoría
anterior», que es justo para lo que se mira un seguimiento mensual.

«El mismo hallazgo» es `(issue_type, url, group_key)`. Con tipo y URL no basta:
un hreflang mal escrito emite una incidencia por código de idioma, y un título
duplicado pertenece a un grupo.
"""

from __future__ import annotations

from shared.comparacion import comparar_incidencias


# Dos censos solo se comparan por incidencias si se analizaron con el MISMO
# codigo: un hallazgo puede salir "resuelto" solo porque la regla que lo
# emitia ha cambiado. Los tests que no van de eso usan la misma version.
VERSION = "abc1234"


def _inc(tipo, url, *, sev="warning", grupo=None):
    return {"issue_type": tipo, "url": url, "severity": sev, "group_key": grupo}


def comparar(antes, ahora, **kw):
    kw.setdefault("analisis_a", VERSION)
    kw.setdefault("analisis_b", VERSION)
    return comparar_incidencias(antes, ahora, **kw)


def test_resuelta_nueva_y_que_sigue():
    antes = [
        _inc("title_too_long", "https://x.com/a"),
        _inc("missing_h1", "https://x.com/b"),
    ]
    ahora = [
        _inc("missing_h1", "https://x.com/b"),          # sigue
        _inc("status_4xx", "https://x.com/c", sev="error"),  # nueva
    ]
    r = comparar(antes, ahora)
    assert r["resueltas"]["total"] == 1
    assert r["resueltas"]["por_tipo"][0]["tipo"] == "title_too_long"
    assert r["nuevas"]["total"] == 1
    assert r["nuevas"]["por_tipo"][0]["tipo"] == "status_4xx"
    assert r["siguen"]["total"] == 1


def test_dos_hallazgos_del_mismo_tipo_en_la_misma_URL_no_se_confunden():
    """Es la razon de ser de `group_key`: un hreflang mal escrito emite una
    incidencia POR CODIGO DE IDIOMA. Sin la clave, arreglar uno de los dos
    pareceria arreglar los dos."""
    antes = [
        _inc("hreflang_invalid_lang", "https://x.com/a", grupo="en-UK"),
        _inc("hreflang_invalid_lang", "https://x.com/a", grupo="es-SP"),
    ]
    ahora = [_inc("hreflang_invalid_lang", "https://x.com/a", grupo="es-SP")]

    r = comparar(antes, ahora)
    assert r["resueltas"]["total"] == 1, "se arreglo en-UK, no los dos"
    assert r["siguen"]["total"] == 1
    assert r["nuevas"]["total"] == 0


def test_un_duplicado_que_cambia_de_grupo_es_otro_hallazgo():
    """Si el titulo cambia, el duplicado anterior SE RESOLVIO y hay uno nuevo.
    Es el comportamiento correcto: ya no compite con las mismas paginas."""
    antes = [_inc("title_duplicate", "https://x.com/a", grupo="hash-viejo")]
    ahora = [_inc("title_duplicate", "https://x.com/a", grupo="hash-nuevo")]

    r = comparar(antes, ahora)
    assert r["resueltas"]["total"] == 1
    assert r["nuevas"]["total"] == 1
    assert r["siguen"]["total"] == 0


def test_con_el_censo_anterior_truncado_no_se_afirma_ninguna_resuelta():
    """Que un hallazgo no aparezca ahora puede ser que no se llegara a su
    pagina. Y no se enumeran: una lista de "quizas resueltas" se lee como una
    lista de resueltas en cuanto alguien la copia a un informe.
    """
    antes = [_inc("missing_h1", f"https://x.com/p{i}") for i in range(40)]
    ahora = [_inc("missing_h1", "https://x.com/p0")]

    r = comparar(antes, ahora, puede_afirmar_resueltas=False)
    assert r["resueltas"] is None
    assert r["resueltas_no_afirmables"] == 39
    assert r["siguen"]["total"] == 1


def test_las_URLs_se_emparejan_por_su_texto_normalizado():
    """El orden de los parametros no hace dos URLs; la barra final si."""
    antes = [_inc("missing_h1", "https://x.com/a?b=2&a=1")]
    ahora = [_inc("missing_h1", "https://x.com/a?a=1&b=2")]
    assert comparar(antes, ahora)["siguen"]["total"] == 1

    r = comparar([_inc("missing_h1", "https://x.com/a/")],
                             [_inc("missing_h1", "https://x.com/a")])
    assert r["siguen"]["total"] == 0


def test_con_mapeo_de_host_las_de_preproduccion_casan():
    antes = [_inc("missing_h1", "https://pre.x.com/a")]
    ahora = [_inc("missing_h1", "https://x.com/a")]
    r = comparar(antes, ahora, mapa_hosts={"pre.x.com": "x.com"})
    assert r["siguen"]["total"] == 1


def test_el_recuento_va_por_tipo_y_los_errores_primero():
    """Un informe se lee de arriba abajo: lo que saca paginas del indice va
    antes que un aviso de estilo."""
    ahora = ([_inc("status_5xx", f"https://x.com/e{i}", sev="error") for i in range(2)]
             + [_inc("title_too_long", f"https://x.com/t{i}") for i in range(9)]
             + [_inc("url_underscores", "https://x.com/u", sev="info")])
    r = comparar([], ahora)
    assert [f["tipo"] for f in r["nuevas"]["por_tipo"]] == [
        "status_5xx", "title_too_long", "url_underscores"]
    assert r["nuevas"]["total"] == 12


def test_una_incidencia_cuya_pagina_ya_no_esta_NO_esta_resuelta():
    """Es lo que mas cerca estuvo de acabar en un informe de cliente.

    Medido con dos censos reales de progym en los que desaparecieron 1.205
    URLs: sin esto el diff decia **4.794 resueltas**, y las tres primeras
    clases —1.152 `noindex_page`, 1.146 `canonical_missing`, 938
    `hreflang_missing_return`— eran paginas que sencillamente no se volvieron a
    visitar. Con el arreglo: **125 resueltas** y 4.669 "no se sabe".

    Decirle a un cliente que arreglo cuatro mil cosas que no toco es peor que
    no darle ninguna cifra.
    """
    antes = [
        _inc("missing_h1", "https://x.com/sigue"),
        _inc("missing_h1", "https://x.com/ya-no-se-rastrea"),
    ]
    ahora = []     # la primera se arreglo, la segunda no se visito
    urls = {"https://x.com/sigue"}

    r = comparar(antes, ahora, urls_del_nuevo=urls)
    assert r["resueltas"]["total"] == 1
    assert r["resueltas"]["ejemplos"] == ["https://x.com/sigue"]
    assert r["sin_pagina_en_el_nuevo"] == 1


def test_sin_saber_que_URLs_hay_no_se_descarta_ninguna():
    """Quien llama puede no tener la lista; entonces se comporta como antes."""
    antes = [_inc("missing_h1", "https://x.com/a")]
    r = comparar(antes, [])
    assert r["resueltas"]["total"] == 1
    assert r["sin_pagina_en_el_nuevo"] == 0


def test_con_el_anterior_truncado_las_dos_clases_van_juntas():
    """Ni resueltas ni "sin pagina": todo es "no se sabe", y una sola cifra."""
    antes = [_inc("missing_h1", "https://x.com/a"),
             _inc("missing_h1", "https://x.com/b")]
    r = comparar(antes, [], puede_afirmar_resueltas=False,
                             urls_del_nuevo={"https://x.com/a"})
    assert r["resueltas"] is None
    assert r["resueltas_no_afirmables"] == 2
    assert r["sin_pagina_en_el_nuevo"] == 0


# --- Dos censos analizados con codigo distinto no se comparan --------------

def _par():
    antes = [_inc("title_duplicate", "https://x.com/a", grupo="g1")]
    ahora = [_inc("title_duplicate", "https://x.com/a", grupo="g1")]
    return antes, ahora


def test_con_el_mismo_analisis_si_se_comparan():
    antes, ahora = _par()
    r = comparar_incidencias(antes, ahora, analisis_a="abc1234", analisis_b="abc1234")
    assert r["comparable"] is True
    assert r["siguen"]["total"] == 1


def test_con_analisis_distinto_NO_se_comparan():
    """Un aviso no basta: no impide que la cifra sea falsa ni que alguien la
    copie a un informe.

    Medido el 2026-10-13, el dia que se anadio `group_key`: el censo de
    referencia de Lopesan tenia 11.414 incidencias y CERO claves, y el
    siguiente las tendria todas. Ningun hallazgo agrupado habria casado y los
    miles afectados habrian salido como "resuelto" y "nuevo" a la vez.
    """
    antes, ahora = _par()
    r = comparar_incidencias(antes, ahora, analisis_a="abc1234", analisis_b="def5678")
    assert r["comparable"] is False
    assert "versiones distintas" in r["motivo"]
    assert "vuelve a analizar el mas antiguo" in r["motivo"], (
        "el motivo tiene que decir como arreglarlo, no solo que no se puede")
    assert "nuevas" not in r and "resueltas" not in r, (
        "ninguna cifra: una que no se puede afirmar se copia igual")


def test_sin_saber_la_version_tampoco():
    """Un censo anterior al sello pudo analizarse con cualquier cosa, y
    callarse equivale a afirmar que fue con el mismo codigo (decision 72)."""
    antes, ahora = _par()
    for a, b in (("abc1234", None), (None, "abc1234"), (None, None)):
        r = comparar_incidencias(antes, ahora, analisis_a=a, analisis_b=b)
        assert r["comparable"] is False, f"{a!r} vs {b!r}"
        assert "desconocida" in r["motivo"] or "versiones distintas" in r["motivo"]
