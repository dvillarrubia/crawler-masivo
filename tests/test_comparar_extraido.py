"""La extraccion a medida y los segmentos en la comparacion de censos (decision 127).

Dos cosas: (a) lo que extraen las reglas a medida (precio, disponibilidad, el
tag de GTM) se compara entre dos censos, con una alerta cuando cambia en bloque
y con su denominador; y (b) las alertas de pagina de la decision 68 saltan
tambien por segmento de negocio (decision 125), sin repetir el aviso si el
segmento y la plantilla son las mismas paginas.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared.comparacion import (
    comparar_censos, comparar_con_el_anterior, paginas_de_censo, reglas_a_medida,
)

REGLA_PRECIO = {"tipo": "css", "expresion": ".price", "valor": "numero"}
REGLA_STOCK = {"tipo": "regex", "expresion": r'"availability":\s*"(\w+)"'}
BUSQUEDA_GTM = {"modo": "texto", "patron": "GTM-", "ignorar_mayusculas": False}
CONFIG = {"custom_extraction": {"precio": REGLA_PRECIO, "stock": REGLA_STOCK},
          "custom_search": {"gtm": BUSQUEDA_GTM}}

JOB_A = {"semilla": "https://x.com/", "status": "completed",
         "finish_reason": "finished", "crawler_version": "abc1234",
         "fecha": "2026-01-01T00:00:00", "reglas_a_medida": reglas_a_medida(CONFIG)}
JOB_B = {**JOB_A, "fecha": "2026-02-01T00:00:00"}


def _x(estado="ok", valor=None, num=None, clase="extractor"):
    return {"clase": clase, "estado": estado, "valor": valor, "num": num}


def _pag(ruta, extraido=None, **kw):
    base = {"url": f"https://x.com{ruta}", "status_code": 200,
            "indexability_status": "Indexable", "canonical_href": None,
            "title": "Titulo", "h1": "Titular", "word_count": 500,
            "extraido": extraido or {}}
    base.update(kw)
    return base


def _fichas(n, precio, stock="InStock", prefijo="/p/"):
    return [_pag(f"{prefijo}{i}", {"precio": _x(valor=str(precio), num=float(precio)),
                                   "stock": _x(valor=stock)})
            for i in range(n)]


def _alerta(r, regla, extraccion=None):
    for al in r["alertas"]:
        if al["regla"] == regla and (extraccion is None or al.get("extraccion") == extraccion):
            return al
    return None


# --- (a) extraccion a medida -------------------------------------------------

def test_un_cambio_masivo_de_precio_es_una_alerta_con_su_denominador():
    a = _fichas(40, 10)
    b = _fichas(30, 11) + _fichas(40, 10)[30:]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    al = _alerta(r, "extraido_cambia", "precio")
    assert al is not None, r["alertas"]
    assert (al["paginas"], al["de"]) == (30, 40), "30 de las 40 que tenian precio en los dos"
    assert al["variacion"] == {"suben": 30, "bajan": 0, "mediana_pct": 10.0}
    assert al["severidad"] == "warning", "no es una decision de Google: no va al webhook"
    regla = next(x for x in r["extraido"]["reglas"] if x["nombre"] == "precio")
    assert regla["cambios"]["extraido_cambia"]["total"] == 30
    assert regla["cambios"]["extraido_cambia"]["ejemplos"][0]["antes"] == "10"


def test_pocos_cambios_de_precio_no_son_una_alerta():
    """El piso de 20 paginas y el 5% de la decision 68: tres precios no son
    una plantilla."""
    a = _fichas(200, 10)
    b = _fichas(3, 12) + _fichas(200, 10)[3:]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    assert _alerta(r, "extraido_cambia") is None
    precio = next(x for x in r["extraido"]["reglas"] if x["nombre"] == "precio")
    assert precio["cambios"]["extraido_cambia"]["total"] == 3


def test_disponibilidad_que_pasa_a_sin_stock_dice_la_transicion():
    a = _fichas(30, 10, "InStock")
    b = _fichas(30, 10, "OutOfStock")
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    al = _alerta(r, "extraido_cambia", "stock")
    assert al and al["paginas"] == 30
    assert al["transiciones"] == [{"antes": "InStock", "ahora": "OutOfStock", "paginas": 30}]
    assert _alerta(r, "extraido_cambia", "precio") is None


def test_sin_fila_en_uno_de_los_dos_no_es_un_cambio():
    """Decision 104: sin fila es «no evaluada», no «vacio». Una ficha que en el
    censo nuevo no tiene fila (paso a 404, cayo fuera de `solo_urls`) no ha
    «perdido el precio»."""
    a = _fichas(30, 10)
    b = [_pag(p["url"][len("https://x.com"):]) for p in _fichas(30, 10)]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    precio = next(x for x in r["extraido"]["reglas"] if x["nombre"] == "precio")
    assert precio["sin_dato"] == 30 and precio["comparadas"] == 0
    assert precio["cambios"]["extraido_desaparece"]["total"] == 0
    assert not [al for al in r["alertas"] if al.get("extraccion")]


def test_un_estado_sin_dato_es_no_se_sabe_no_un_cambio():
    """`tiempo_agotado` o `pagina_bloqueada` no dicen nada de la pagina."""
    a = _fichas(30, 10)
    b = [_pag(f"/p/{i}", {"precio": _x("tiempo_agotado"), "stock": _x("pagina_bloqueada")})
         for i in range(30)]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    for x in r["extraido"]["reglas"]:
        if x["nombre"] in ("precio", "stock"):
            assert x["sin_saber"] == 30 and x["comparadas"] == 0
    assert not [al for al in r["alertas"] if al.get("extraccion")]


def test_el_precio_que_desaparece_si_es_una_alerta():
    """`ok` -> `sin_coincidencia` en la MISMA URL: la regla miro y no esta."""
    a = _fichas(30, 10)
    b = [_pag(f"/p/{i}", {"precio": _x("sin_coincidencia"), "stock": _x(valor="InStock")})
         for i in range(30)]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    al = _alerta(r, "extraido_desaparece", "precio")
    assert al and (al["paginas"], al["de"]) == (30, 30)


def test_una_busqueda_que_deja_de_contener_el_tag():
    a = [_pag(f"/a/{i}", {"gtm": _x(valor="GTM-ABCD", clase="busqueda")}) for i in range(25)]
    b = [_pag(f"/a/{i}", {"gtm": _x("sin_coincidencia", clase="busqueda")}) for i in range(25)]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    al = _alerta(r, "busqueda_deja_de_contener", "gtm")
    assert al and (al["paginas"], al["de"]) == (25, 25)


def test_solo_se_comparan_reglas_que_existen_en_los_dos():
    a = _fichas(30, 10)
    b = _fichas(30, 20)
    job_b = {**JOB_B, "reglas_a_medida": reglas_a_medida(
        {"custom_extraction": {"stock": REGLA_STOCK}})}
    r = comparar_censos(a, b, job_a=JOB_A, job_b=job_b)
    assert _alerta(r, "extraido_cambia", "precio") is None
    motivos = {x["nombre"]: x["motivo"] for x in r["extraido"]["no_comparadas"]}
    assert "solo existe en el censo anterior" in motivos["precio"]
    assert [x["nombre"] for x in r["extraido"]["reglas"]] == ["stock"]


def test_una_regla_que_cambio_de_expresion_no_se_compara():
    """Si la expresion cambio, una diferencia puede ser de la regla."""
    a = _fichas(30, 10)
    b = _fichas(30, 20)
    otra = {**CONFIG, "custom_extraction": {
        "precio": {**REGLA_PRECIO, "expresion": ".final-price"}, "stock": REGLA_STOCK}}
    job_b = {**JOB_B, "reglas_a_medida": reglas_a_medida(otra)}
    r = comparar_censos(a, b, job_a=JOB_A, job_b=job_b)
    assert _alerta(r, "extraido_cambia", "precio") is None
    motivo = next(x["motivo"] for x in r["extraido"]["no_comparadas"] if x["nombre"] == "precio")
    assert "expresion" in motivo


def test_la_etiqueta_y_el_alcance_no_cambian_la_regla():
    otra = {**CONFIG, "custom_extraction": {
        "precio": {**REGLA_PRECIO, "etiqueta": "Precio final", "solo_urls": "^/p/"},
        "stock": REGLA_STOCK}}
    r = comparar_censos(_fichas(30, 10), _fichas(30, 20), job_a=JOB_A,
                        job_b={**JOB_B, "reglas_a_medida": reglas_a_medida(otra)})
    assert _alerta(r, "extraido_cambia", "precio") is not None


def test_un_censo_bloqueado_no_emite_alertas_de_extraccion():
    """Decision 103: lo que «cambio» es lo que el WAF nos dejo ver."""
    from shared.respuestas import BLOQUEADO_POR_WAF
    job_b = {**JOB_B, "finish_reason": BLOQUEADO_POR_WAF}
    r = comparar_censos(_fichas(30, 10), _fichas(30, 20), job_a=JOB_A, job_b=job_b)
    assert r["alertas"] == []


def test_una_pagina_que_es_un_muro_no_se_compara():
    from shared.respuestas import NO_VALIDAS

    a = _fichas(30, 10)
    b = [{**p, "response_class": next(iter(NO_VALIDAS))} for p in _fichas(30, 20)]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    assert _alerta(r, "extraido_cambia") is None
    assert next(x for x in r["extraido"]["reglas"] if x["nombre"] == "precio")["comparadas"] == 0


def test_el_orden_lo_pone_la_fecha_tambien_en_la_extraccion():
    a = _fichas(30, 10)
    b = _fichas(30, 11)
    r = comparar_censos(b, a, job_a=JOB_B, job_b=JOB_A)
    al = _alerta(r, "extraido_cambia", "precio")
    assert al["variacion"]["suben"] == 30, "de 10 a 11 aunque se pasen al reves"


# --- (b) alertas por segmento -------------------------------------------------

def _sitio_con_fichas_repartidas(titulo_fichas="Titulo"):
    """1.000 paginas; 40 fichas repartidas en 40 formas de ruta distintas, de
    modo que ninguna plantilla llega al piso de 20 y el sitio no llega al 5%."""
    otras = [_pag(f"/blog/{i}", segmento="blog") for i in range(960)]
    fichas = [_pag(f"/c{i}/x/y{'/z' * (i % 6)}" + ("-a" * (i // 6)), segmento="fichas",
                   title=titulo_fichas) for i in range(40)]
    return otras + fichas


def test_un_segmento_entero_que_cambia_salta_aunque_ninguna_plantilla_llegue():
    a = _sitio_con_fichas_repartidas()
    b = _sitio_con_fichas_repartidas(titulo_fichas="Otro titulo")
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    al = _alerta(r, "cambia_el_titulo")
    assert al is not None, "40 de 40 fichas: es el segmento entero"
    assert al["por"] == "segmento"
    assert al["segmentos"][0] == {"segmento": "fichas", "paginas": 40, "de": 40,
                                  "pct": 100.0, "plantilla": None}


def test_sin_segmentos_el_mismo_cambio_no_salta():
    """El contrafactual: sin `segmento`, las 40 fichas repartidas no llegan a
    ningun umbral. Es lo que la decision 127 viene a arreglar."""
    def sin(p):
        return {k: v for k, v in p.items() if k != "segmento"}
    a = [sin(p) for p in _sitio_con_fichas_repartidas()]
    b = [sin(p) for p in _sitio_con_fichas_repartidas(titulo_fichas="Otro titulo")]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    assert _alerta(r, "cambia_el_titulo") is None


def test_segmento_y_plantilla_iguales_son_un_solo_aviso():
    """La regla `^/blog/` y la forma `/blog/:slug` son las mismas paginas: el
    hallazgo sale como segmento, con la plantilla al lado, y no se repite en
    la lista de plantillas."""
    a = [_pag(f"/blog/{i}", segmento="blog") for i in range(30)] + \
        [_pag(f"/tienda/{i}", segmento="tienda") for i in range(1000)]
    b = [_pag(f"/blog/{i}", segmento="blog", title="Nuevo") for i in range(30)] + \
        [_pag(f"/tienda/{i}", segmento="tienda") for i in range(1000)]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    al = _alerta(r, "cambia_el_titulo")
    assert al and al["por"] == "segmento"
    assert len(al["segmentos"]) == 1 and al["segmentos"][0]["plantilla"]
    assert al["segmentos"][0]["plantilla"] not in {p["plantilla"] for p in al["plantillas"]}
    assert al["plantillas"] == []


def test_el_segmento_de_repuesto_no_es_un_segmento():
    """Sin regla que case, el segmento es la forma de la ruta (decision 125):
    ya esta en la lista de plantillas y no se repite."""
    a = [_pag(f"/blog/{i}", segmento="/blog/:slug") for i in range(30)] + \
        [_pag(f"/t/{i}", segmento="/t/:slug") for i in range(1000)]
    b = [_pag(f"/blog/{i}", segmento="/blog/:slug", title="Nuevo") for i in range(30)] + \
        [_pag(f"/t/{i}", segmento="/t/:slug") for i in range(1000)]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    al = _alerta(r, "cambia_el_titulo")
    assert al and al["por"] == "plantilla" and al["segmentos"] == []


def test_la_extraccion_tambien_salta_por_segmento():
    """Un cambio de precio en las fichas, diluido en un sitio grande con
    precio en todas partes."""
    def censo(p_fichas):
        otras = [_pag(f"/o/{i}", {"precio": _x(valor="5", num=5.0)}, segmento="otros")
                 for i in range(2000)]
        fichas = [_pag(f"/f{i}/a{'/b' * (i % 5)}", {"precio": _x(valor=str(p_fichas), num=float(p_fichas))},
                       segmento="fichas") for i in range(40)]
        return otras + fichas
    r = comparar_censos(censo(10), censo(12), job_a=JOB_A, job_b=JOB_B)
    al = _alerta(r, "extraido_cambia", "precio")
    assert al and al["por"] == "segmento" and al["segmentos"][0]["segmento"] == "fichas"
    assert al["de"] == 2040, "el denominador del sitio es el de la regla, no el del segmento"


# --- de la base de datos al veredicto ----------------------------------------

@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


def _sesion():
    from shared.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _censo(s, dia, precio, *, config=CONFIG):
    from shared.models import CustomExtraction, HtmlMeta, Job, Url

    j = Job(id=uuid.uuid4(), name=f"censo {dia}", status="completed",
            finish_reason="finished", seeds=["https://x.com/"], config=config,
            crawler_version="abc1234", analisis_version="abc1234",
            started_at=datetime(2026, 9, dia, tzinfo=timezone.utc))
    s.add(j)
    s.flush()
    for i in range(25):
        u = Url(job_id=j.id, url=f"https://x.com/p/{i}", url_hash=f"{dia}-{i}",
                is_internal=True, is_html=True, status_code=200, indexable=True,
                indexability_status="Indexable", word_count=500, segmento="fichas")
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title="Ficha"))
        s.add(CustomExtraction(job_id=j.id, url_id=u.id, nombre="precio",
                               clase="extractor", estado="ok", valor=f"{precio} €",
                               valor_clave=f"{precio} €", valor_num=float(precio)))
    s.commit()
    return j


def test_de_la_base_de_datos_a_la_alerta_que_guarda_el_worker():
    """Se ejecuta `comparar_con_el_anterior` de verdad contra SQLite y se lee
    lo que devuelve, que es lo que el worker guarda en `jobs.comparacion`
    (decision 74: mirar el fuente no sustituye a ejecutar)."""
    s = _sesion()
    viejo = _censo(s, 1, 10)
    nuevo = _censo(s, 8, 15)

    paginas = paginas_de_censo(s, nuevo.id)
    assert paginas[0]["segmento"] == "fichas"
    assert paginas[0]["extraido"]["precio"]["num"] == 15.0

    r = comparar_con_el_anterior(s, nuevo)
    assert r["comparado_con"] == str(viejo.id)
    al = _alerta(r, "extraido_cambia", "precio")
    assert al and (al["paginas"], al["de"]) == (25, 25)
    assert al["variacion"]["mediana_pct"] == 50.0
    assert "precio" in {x["nombre"] for x in r["extraido"]["reglas"]}
    s.close()


def test_sin_filas_de_extraccion_la_comparacion_sigue_igual():
    s = _sesion()
    from shared.models import CustomExtraction

    _censo(s, 1, 10)
    nuevo = _censo(s, 8, 15)
    s.query(CustomExtraction).filter(CustomExtraction.job_id == nuevo.id).delete()
    s.commit()
    r = comparar_con_el_anterior(s, nuevo)
    assert r["comparable"] and not [al for al in r["alertas"] if al.get("extraccion")]
    precio = next(x for x in r["extraido"]["reglas"] if x["nombre"] == "precio")
    assert precio["sin_dato"] == 25
    s.close()


def test_un_valor_renderizado_y_otro_crudo_no_se_comparan():
    """Lo que monta el JavaScript solo esta en el DOM renderizado: un precio
    que «aparece» al pasar a render no es un cambio del sitio."""
    a = [_pag(f"/p/{i}", {"precio": {**_x("sin_coincidencia"), "render": False}}) for i in range(30)]
    b = [_pag(f"/p/{i}", {"precio": {**_x(valor="10", num=10.0), "render": True}}) for i in range(30)]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    precio = next(x for x in r["extraido"]["reglas"] if x["nombre"] == "precio")
    assert precio["render_distinto"] == 30 and precio["comparadas"] == 0
    assert _alerta(r, "extraido_aparece") is None


# --- el segmento que define el resultado no puede ser el denominador ----------

def test_un_segmento_definido_por_el_resultado_no_infla_la_alerta():
    """Los segmentos pueden depender de `indexable`, `status_code` o de un
    valor extraido (decision 125). Si las paginas se reparten por el segmento
    del censo NUEVO, uno definido por el propio resultado («no indexables»)
    contiene justo las que cambiaron y la alerta sale al 100%: 25 de 1.000
    fichas que salen del indice no son un cambio masivo. El reparto va por el
    segmento de ANTES, que es la poblacion de la que se cuenta el porcentaje."""
    a = [_pag(f"/p/{i}", segmento="fichas") for i in range(1000)]
    b = ([_pag(f"/p/{i}", segmento="no indexables", indexability_status="Noindex")
          for i in range(25)]
         + [_pag(f"/p/{i}", segmento="fichas") for i in range(25, 1000)])
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    assert _alerta(r, "salen_del_indice") is None, r["alertas"]


def test_un_segmento_definido_por_el_valor_extraido_no_infla_la_alerta():
    """Lo mismo con la extraccion: el segmento «agotados» (`x.stock` igual
    OutOfStock) del censo nuevo es exactamente las fichas que se agotaron."""
    a = [_pag(f"/p/{i}", {"stock": _x(valor="InStock")}, segmento="disponibles")
         for i in range(2000)]
    b = ([_pag(f"/p/{i}", {"stock": _x(valor="OutOfStock")}, segmento="agotados")
          for i in range(30)]
         + [_pag(f"/p/{i}", {"stock": _x(valor="InStock")}, segmento="disponibles")
            for i in range(30, 2000)])
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    assert _alerta(r, "extraido_cambia", "stock") is None, r["alertas"]


def test_sin_segmentos_en_el_anterior_no_se_usan_los_del_nuevo_y_se_dice():
    """El mismo caso que las 40 fichas repartidas, pero el censo anterior es
    previo a la columna: sin segmento de antes no hay poblacion de la que
    contar el porcentaje, y el aviso dice como recuperarla."""
    a = [_pag(f"/c{i}/x{'/z' * (i % 6)}" + ("-a" * (i // 6))) for i in range(40)]
    b = [_pag(f"/c{i}/x{'/z' * (i % 6)}" + ("-a" * (i // 6)), segmento="fichas",
              title="Otro") for i in range(40)]
    a += [_pag(f"/blog/{i}") for i in range(960)]
    b += [_pag(f"/blog/{i}", segmento="blog") for i in range(960)]
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)
    assert _alerta(r, "cambia_el_titulo") is None
    assert any("no tiene segmentos" in av for av in r["avisos"])
