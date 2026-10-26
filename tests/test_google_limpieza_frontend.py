"""El bloque «Google», los matices del Resumen y la Limpieza (revision de UX
del 25-oct, 3.3 y 3.6), ejecutando `frontend/app.js` en Chromium.

- `bloqueGoogle`: tres cifras con la ventana de los DATOS al lado, una frase
  que solo dice lo que se sabe (sube, baja, se mantiene, espera a los datos o
  no hay comparacion) y las paginas que mas cambian una vez cada una (V46).
- `resumenSitemaps` y `dudaSitemaps`: los ficheros vacios se dicen y matizan
  «No incluida en el sitemap» (V13). `hostDominante` (V19) y `baseAlerta` (V20).
- Limpieza: el texto de la celda y del orden (V45, decision 198).

Los datos son los del censo de Lopesan del 21-oct (y el del 26-oct, que
esperaba a Search Console). Necesita Chromium: se salta si no esta.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright")

from test_resumenes_frontend import _evaluar  # noqa: E402

ES = "https://www.lopesan.com/es"
TRAFICO = {
    "dias": 7, "ventana_anterior": ["2026-10-06", "2026-10-12"], "ventana_nueva": ["2026-10-13", "2026-10-19"],
    "comparable": True, "pendiente": False, "clics_anterior": 42443, "clics_nuevo": 41481,
    "variacion": -0.023, "general": False, "estacional": None, "rupturas": [],
    "caidas": [{"cambio": "sin_cambio", "texto": "sin cambio técnico en el rastreo", "paginas": 4,
                "ejemplos": [{"url": "https://www.lopesan.com/blog/a/", "clics_antes": 46, "clics_despues": 21}]}],
    "subidas": [
        {"cambio": "noindex", "texto": "cambia la indexabilidad", "paginas": 1,
         "ejemplos": [{"url": ES, "clics_antes": 29, "clics_despues": 138}]},
        {"cambio": "canonical", "texto": "cambia el canonical", "paginas": 1,
         "ejemplos": [{"url": ES, "clics_antes": 29, "clics_despues": 138}]}],
    "textos": ["Clics de Google: 42.443 del 06/10 al 12/10 y 41.481 del 13/10 al 19/10 (-2 %).",
               "1 página ganó 109 clics; coincide con que en ella cambia la indexabilidad y cambia el canonical."],
}
GSC = {"desde": "2026-07-29", "hasta": "2026-10-26", "datos_desde": "2026-07-29", "datos_hasta": "2026-10-23",
       "clics": 563710, "impresiones": 25465708,
       "inspeccion": {"inspeccionadas": 3723, "indexadas": 3190, "no_indexadas": 46}}


def _bloque(job):
    return _evaluar("() => bloqueGoogle(" + json.dumps(job) + ")")


def test_tres_cifras_con_la_ventana_de_los_datos():
    b = _bloque({"gsc_resumen": GSC, "comparacion": {"trafico": TRAFICO}})
    assert [(c["etiqueta"], c["valor"]) for c in b["cifras"]] == [
        ("Clics", "563.710"), ("Impresiones", "25.465.708"), ("CTR medio", "2,2 %")]
    # La ventana llegaba al 26 y los datos al 23: la fecha es la de los datos.
    assert b["cifras"][0]["ventana"] == "del 29 jul al 23 oct"
    assert b["inspeccion"] == "De las 3.723 URLs inspeccionadas, Google dice que 3.190 están indexadas y 46 no."


def test_la_frase_dice_cuanto_y_la_fecha_de_cada_cifra():
    b = _bloque({"comparacion": {"trafico": TRAFICO}})
    assert b["cifras"] == []
    assert b["conclusiones"][0]["texto"] == (
        "Los clics bajan un 2 % frente a los 7 días anteriores: 41.481 del 13 al 19 oct "
        "frente a 42.443 clics del 6 al 12 oct.")
    # La primera frase del servidor (los totales) no se repite debajo.
    assert b["textos"] == TRAFICO["textos"][1:]


def test_sube_se_mantiene_y_caida_general():
    r = _evaluar("() => [%s].map(t => conclusionTrafico(t).map(c => c.texto))" % ", ".join(json.dumps(t) for t in (
        {**TRAFICO, "variacion": 0.12},
        {**TRAFICO, "variacion": -0.004},
        {**TRAFICO, "variacion": -0.4, "general": True, "estacional": {"igual": True}},
        {**TRAFICO, "variacion": None, "clics_anterior": 0})))
    sube, igual, general, sin_antes = r
    assert sube[0].startswith("Los clics suben un 12 % frente a los 7 días anteriores")
    assert igual[0].startswith("Los clics se mantienen frente a")
    assert general[1] == "La caída es de todo el sitio, no de unas páginas concretas."
    assert "parece estacional" in general[2]
    assert sin_antes[0].startswith("Sin clics en el periodo anterior (del 6 al 12 oct): no hay porcentaje")


def test_esperando_a_los_datos_o_sin_comparacion_no_concluye():
    pendiente = {"dias": 7, "ventana_anterior": ["2026-10-11", "2026-10-17"], "ventana_nueva": ["2026-10-18", "2026-10-24"],
                 "comparable": False, "pendiente": True, "motivo": "Search Console aún no tiene datos hasta el 2026-10-24."}
    b = _bloque({"gsc_resumen": GSC, "comparacion": {"trafico": pendiente}})
    (c,) = b["conclusiones"]
    assert c["nivel"] == "dim" and c["texto"].startswith("Todavía no se puede decir si los clics suben o bajan")
    assert "del 18 al 24 oct frente a del 11 al 17 oct" in c["texto"] and c["texto"].endswith("hasta el 24 oct.")
    assert b["textos"] == [] and b["paginas"] == []
    solo = _bloque({"gsc_resumen": GSC})
    assert solo["conclusiones"][0]["texto"].startswith("Sin comparación con el censo anterior")
    assert _bloque({}) is None and _bloque({"comparacion": {}}) is None


def test_la_misma_pagina_una_vez_con_todo_lo_que_cambio():
    """V46: /es estaba en «cambia la indexabilidad» y en «cambia el canonical»."""
    b = _bloque({"comparacion": {"trafico": TRAFICO}})
    urls = [p["url"] for p in b["paginas"]]
    assert urls == [ES, "https://www.lopesan.com/blog/a/"]
    assert b["paginas"][0]["cambios"] == ["cambia la indexabilidad", "cambia el canonical"]
    # El grupo «sin cambio» tiene 4 paginas y un ejemplo: hay mas de las que se ven.
    assert b["cortadas"] is True


SITEMAPS_LOPESAN = {"pedidos": 4, "leidos": 4, "parcial": False, "ficheros": [
    {"url": "https://x.com/sitemap.xml", "status": 200, "formato": "sitemapindex", "declaradas": 3, "devueltas": 0, "hijos": 3, "bytes": 600},
    {"url": "https://x.com/s1", "status": 200, "formato": "desconocido", "declaradas": 0, "devueltas": 0, "bytes": 0},
    {"url": "https://x.com/s2", "status": 200, "formato": "desconocido", "declaradas": 0, "devueltas": 0, "bytes": 0},
    {"url": "https://x.com/s3", "status": 200, "formato": "urlset", "declaradas": 64, "devueltas": 64, "bytes": 9000}]}


def test_sitemaps_vacios_se_dicen_y_matizan_no_incluida():
    sm, duda, limpio = _evaluar("() => { const j = %s; return [resumenSitemaps(j), dudaSitemaps(j),"
                                " dudaSitemaps({sitemaps: {ficheros: [j.sitemaps.ficheros[3]]}})]; }"
                                % json.dumps({"sitemaps": SITEMAPS_LOPESAN}))
    # Las URLs son las de los urlset, no los hijos del indice sumados.
    assert (sm["urls"], sm["conUrls"], sm["indices"], sm["hijos"]) == (64, 1, 1, 3)
    assert (sm["ilegibles"], sm["vacios"], sm["dudoso"]) == (2, 2, True)
    assert duda == ("2 de los 4 ficheros de sitemap respondieron 200 vacíos: una página «no incluida» "
                    "puede estar en ellos, así que no se puede afirmar que falte.")
    assert limpio is None


def test_host_dominante_y_base_de_cada_alerta():
    r = _evaluar("""() => [
      hostDominante([{host: 'www.lopesan.com', count: 5571}, {host: 'lopesan.com', count: 7}]),
      hostDominante([{host: 'a', count: 60}, {host: 'b', count: 40}]),
      hostDominante([{host: 'a', count: 10}]),
      baseAlerta({regla: 'se_alejan_de_la_home', de: 3153}),
      baseAlerta({regla: 'desaparecen', de: 1}),
      baseAlerta({regla: 'extraido_cambia', de: 30})]""")
    assert r[0] == "Casi todo en www.lopesan.com: 5.571 de 5.578 URLs. El resto, en lopesan.com (7)."
    assert r[1] is None and r[2] is None
    assert r[3] == "Sobre las 3.153 páginas que eran indexables en el censo anterior y están en los dos."
    assert r[4] == "Sobre la única página que era indexable en el censo anterior."
    # Una regla sin base conocida no se inventa una.
    assert r[5] == ""


def test_limpieza_celda_y_orden():
    r = _evaluar("""() => [
      textoRepetidoGrupo({repetido_parte: 0.461, repetido_chars_medio: 4084}),
      textoRepetidoGrupo({repetido_parte: 0.004, repetido_chars_medio: 21}),
      textoRepetidoGrupo({repetido_parte: 0}),
      textoRepetidoGrupo({repetido_parte: null}),
      textoOrdenLimpieza([{repetido_parte: 0.4}]).slice(0, 40),
      textoOrdenLimpieza([{pages: 3}]),
      limpiezaConLimpiadas([{cleaned_pages: 0}, {cleaned_pages: 0}]),
      limpiezaConLimpiadas([{cleaned_pages: 0}, {cleaned_pages: 2}])]""")
    assert r[:4] == ["46 % · 4.084 caracteres por página", "menos del 1 % · 21 caracteres por página", "nada", "sin medir"]
    assert r[4] == "Primero los que más texto repetido tiene"
    # Con una API anterior (sin cifra) se dice que van por paginas.
    assert r[5].startswith("Por número de páginas")
    assert r[6:] == [False, True]


def test_quitar_una_linea_rellena_el_hueco_y_no_repite():
    r = _evaluar("""() => { const a = app(); a.cleaningRules = [{ type: 'line_exact', value: '' }];
        a.anadirLineaLimpieza('Menú'); a.anadirLineaLimpieza('Menú'); a.anadirLineaLimpieza('Pie');
        const antes = a.cleaningRules.map(r => r.value);
        a.cleaningSelectedGroup = '/a'; a.$nextTick = () => {};
        a.selectCleaningGroup({ group_key: '/b' });
        const otro = [a.cleaningSelectedGroup, a.cleaningRules.length, a.cleaningRules[0].value];
        a.selectCleaningGroup({ group_key: '/b' });
        return { antes, otro, cerrado: a.cleaningSelectedGroup }; }""")
    assert r["antes"] == ["Menú", "Pie"]
    # Otro grupo: reglas de cero. El mismo otra vez: se cierra.
    assert r["otro"] == ["/b", 1, ""]
    assert r["cerrado"] is None
