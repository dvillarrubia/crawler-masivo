"""Modo lista y veredicto pasa/falla para QA de despliegues (decision 128).

El caso que lo justifica: una lista de 50 URLs clave en la que 10 pasan a
noindex tras un despliegue. Con el piso de 20 paginas de la decision 68 no
alertaba, y no habia forma de que un CI lo leyera.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared.comparacion import comparar_censos, comparar_con_el_anterior
from shared.modo_lista import es_lista, umbral_absoluto, veredicto


@compiles(BigInteger, "sqlite")
def _bigint(tipo, compilador, **kw):
    return "INTEGER"


LISTA = {"max_depth": 0, "use_sitemap": False}
N = 50
SEMILLAS = [f"https://x.com/clave{i}" for i in range(N)]


# --- JobConfig ----------------------------------------------------------------

def test_jobconfig_admite_profundidad_cero_y_entonces_no_lee_el_sitemap():
    pytest.importorskip("pydantic")  # en el trabajo de la API
    from api.schemas import JobConfig

    c = JobConfig.model_validate({"max_depth": 0})
    assert c.max_depth == 0 and c.use_sitemap is False
    # Pedido explicitamente, se respeta
    assert JobConfig.model_validate({"max_depth": 0, "use_sitemap": True}).use_sitemap
    # Un rastreo normal no cambia
    assert JobConfig.model_validate({"max_depth": 3}).use_sitemap is True
    assert JobConfig.model_validate({}).use_sitemap is True


def test_el_override_de_una_lista_no_pisa_el_sitemap_que_fija_el_perfil():
    """Con `exclude_unset` el override del job solo lleva lo que se escribio:
    si el validador marcase `use_sitemap` como fijado, `max_depth: 0` a secas
    pisaria el `use_sitemap: true` de su perfil."""
    pytest.importorskip("pydantic")
    from api.schemas import JobConfig
    from shared.clientes import fusionar_config

    override = JobConfig.model_validate({"max_depth": 0}).model_dump(exclude_unset=True)
    assert "use_sitemap" not in override
    fusion = fusionar_config({}, {"use_sitemap": True}, override)
    assert JobConfig.model_validate(fusion).use_sitemap is True


def test_el_umbral_es_configurable_y_solo_vale_en_listas():
    cfg = {"max_depth": 0, "analysis_thresholds": {"alerta_lista_min_paginas": 5}}
    assert umbral_absoluto({"max_depth": 3}) is None
    assert umbral_absoluto({"max_depth": 0}) == 1
    assert umbral_absoluto(cfg) == 5
    assert es_lista(cfg) and not es_lista({}) and not es_lista(None)


def test_el_umbral_lo_declara_jobconfig():
    pytest.importorskip("pydantic")
    from api.schemas import JobConfig

    assert umbral_absoluto(JobConfig.model_validate({"max_depth": 0}).model_dump()) == 1
    cfg = {"max_depth": 0, "analysis_thresholds": {"alerta_lista_min_paginas": 5}}
    assert JobConfig.model_validate(cfg).model_dump()[
        "analysis_thresholds"]["alerta_lista_min_paginas"] == 5


# --- El umbral absoluto en la comparacion -------------------------------------

def _pag(i, **kw):
    p = {"url": SEMILLAS[i], "status_code": 200, "indexability_status": "Indexable",
         "canonical_href": None, "title": "T", "h1": "H", "word_count": 500}
    p.update(kw)
    return p


JOB_A = {"semilla": SEMILLAS[0], "status": "completed", "finish_reason": "finished",
         "crawler_version": "abc", "fecha": "2026-01-01T00:00:00"}
JOB_B = {**JOB_A, "fecha": "2026-01-02T00:00:00"}


def _diez_a_noindex():
    a = [_pag(i) for i in range(N)]
    b = [_pag(i, indexability_status="Noindex" if i < 10 else "Indexable")
         for i in range(N)]
    return a, b


def test_sin_umbral_absoluto_diez_de_cincuenta_no_alertan():
    """El piso de 20 de la decision 68: el motivo de este cambio."""
    a, b = _diez_a_noindex()
    assert comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B)["alertas"] == []


def test_con_umbral_absoluto_diez_de_cincuenta_alertan():
    a, b = _diez_a_noindex()
    r = comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B, umbral_absoluto=1)
    (alerta,) = r["alertas"]
    assert alerta["regla"] == "salen_del_indice" and alerta["severidad"] == "critical"
    assert (alerta["paginas"], alerta["de"], alerta["por"]) == (10, 50, "lista")


def test_el_umbral_absoluto_es_un_minimo():
    a, b = _diez_a_noindex()
    assert comparar_censos(a, b, job_a=JOB_A, job_b=JOB_B, umbral_absoluto=11)["alertas"] == []


# --- De extremo a extremo: BD -> comparacion automatica -> veredicto ----------

@pytest.fixture
def s():
    from shared.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _censo(s, nombre, dia, *, config=LISTA, seeds=SEMILLAS, paginas=None,
           status="completed", finish_reason="finished", extra=0, **kw_job):
    """Un censo con una fila por URL. `paginas`: {indice: campos que cambian}."""
    from shared.models import HtmlMeta, Job, Url

    j = Job(id=uuid.uuid4(), name=nombre, status=status, finish_reason=finish_reason,
            seeds=list(seeds), config=dict(config), started_at=datetime(2026, 1, dia),
            crawler_version="abc", **kw_job)
    s.add(j)
    s.flush()
    urls = list(SEMILLAS) + [f"https://x.com/otra{i}" for i in range(extra)]
    for i, url in enumerate(urls):
        campos = {"status_code": 200, "indexability_status": "Indexable",
                  "canonical": url, **((paginas or {}).get(i, {}))}
        if campos.pop("falta", False):
            continue
        canonical = campos.pop("canonical")
        u = Url(job_id=j.id, url=url, url_hash=f"{nombre}-{i}", is_internal=True,
                is_html=True, word_count=500, **campos)
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, title="T", canonical_href=canonical))
    s.flush()
    return j


def _comparar_y_juzgar(s, job):
    job.comparacion = comparar_con_el_anterior(s, job)
    s.flush()
    return veredicto(s, job)


def test_diez_urls_clave_a_noindex_alertan_y_el_veredicto_es_falla(s):
    _censo(s, "antes", 1)
    noindex = {i: {"indexability_status": "Noindex"} for i in range(10)}
    ahora = _censo(s, "ahora", 2, paginas=noindex)

    v = _comparar_y_juzgar(s, ahora)
    criticas = [a for a in ahora.comparacion["alertas"] if a["severidad"] == "critical"]
    assert [(a["regla"], a["paginas"], a["de"]) for a in criticas] == [
        ("salen_del_indice", 10, 50)]

    assert v["veredicto"] == "falla" and v["es_lista"] is True
    tipos = {m["tipo"]: m for m in v["motivos"]}
    assert tipos["deja_de_ser_indexable"]["paginas"] == 10
    assert tipos["deja_de_ser_indexable"]["ejemplos"][0]["ahora"] == "Noindex"
    # La alerta dice lo mismo de las mismas 10: viaja con el grupo (decision 36)
    assert tipos["deja_de_ser_indexable"]["regla"] == "salen_del_indice"


def test_la_misma_lista_sin_cambios_pasa(s):
    _censo(s, "antes", 1)
    ahora = _censo(s, "ahora", 2)
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "pasa", v
    assert v["motivos"] == [] and v["no_concluyente_por"] == []


def test_contra_un_censo_completo_lo_que_no_esta_en_la_lista_no_desaparece(s):
    """La referencia de una lista puede ser el censo entero del sitio: sus
    otras 200 paginas no estan en la lista porque no se pidieron."""
    _censo(s, "completo", 1, config={"max_depth": 3}, seeds=["https://x.com/clave0"],
           extra=200)
    ahora = _censo(s, "lista", 2)
    v = _comparar_y_juzgar(s, ahora)
    assert ahora.comparacion["concluyente"] is True
    assert ahora.comparacion["urls"]["desaparecidas"] == 0
    assert ahora.comparacion["alertas"] == []
    assert v["veredicto"] == "pasa", v


def test_cambiar_de_canonical_es_falla(s):
    _censo(s, "antes", 1)
    ahora = _censo(s, "ahora", 2, paginas={3: {"canonical": "https://x.com/otra-url",
                                               "indexability_status": "Canonicalised"}})
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "falla"
    (m,) = [m for m in v["motivos"] if m["tipo"] == "cambia_el_canonical"]
    assert m["paginas"] == 1 and m["ejemplos"][0]["ahora"] == "https://x.com/otra-url"
    # Un aviso por hallazgo: la causa es el canonical, no «deja de ser indexable»
    assert not [m for m in v["motivos"] if m["tipo"] == "deja_de_ser_indexable"]


def test_una_url_que_no_responde_200_falla_aunque_no_haya_referencia(s):
    ahora = _censo(s, "unico", 1, paginas={7: {"status_code": 500,
                                               "indexability_status": "Server Error (500)"}})
    v = _comparar_y_juzgar(s, ahora)
    assert ahora.comparacion is None
    assert v["veredicto"] == "falla"
    (m,) = [m for m in v["motivos"] if m["tipo"] == "no_responde_200"]
    assert m["ejemplos"] == [{"url": SEMILLAS[7], "ahora": 500}]


def test_sin_referencia_no_es_pasa(s):
    v = _comparar_y_juzgar(s, _censo(s, "unico", 1))
    assert v["veredicto"] == "no_concluyente"
    assert [d["tipo"] for d in v["no_concluyente_por"]] == ["sin_referencia"]


def test_truncado_no_es_pasa(s):
    _censo(s, "antes", 1)
    ahora = _censo(s, "ahora", 2, finish_reason="max_urls_reached",
                   paginas={49: {"falta": True}})
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "no_concluyente"
    tipos = {d["tipo"] for d in v["no_concluyente_por"]}
    assert {"truncado", "no_rastreadas"} <= tipos


def test_bloqueado_no_es_pasa_aunque_todo_de_403(s):
    """Los codigos de un censo bloqueado son los del muro, no los del sitio."""
    _censo(s, "antes", 1)
    muro = {i: {"status_code": 403, "response_class": "challenge_waf"} for i in range(N)}
    ahora = _censo(s, "ahora", 2, paginas=muro, finish_reason="bloqueado_por_waf")
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "no_concluyente"
    assert v["no_concluyente_por"][0]["tipo"] == "bloqueado"


def test_un_rastreo_sin_terminar_no_es_pasa(s):
    v = veredicto(s, _censo(s, "corriendo", 1, status="running", finish_reason=None))
    assert v["veredicto"] == "no_concluyente"


def test_robots_prohibiendo_las_semillas_es_falla(s):
    """El `Disallow: /` de preproduccion llegando a produccion: el rastreo
    acaba `failed` (decision 32), pero para QA es un veredicto, no una duda."""
    j = _censo(s, "robots", 1, status="failed", finish_reason="robots_bloquea_todo",
               paginas={i: {"falta": True} for i in range(N)})
    v = veredicto(s, j)
    assert v["veredicto"] == "falla"
    assert v["motivos"][0]["tipo"] == "rastreo_bloqueado_por_robots"


def test_una_url_de_la_lista_bloqueada_por_robots_es_falla(s):
    _censo(s, "antes", 1)
    ahora = _censo(s, "ahora", 2, paginas={0: {
        "status_code": None, "indexability_status": "Blocked by robots.txt"}})
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "falla"
    assert "bloqueada_por_robots" in {m["tipo"] for m in v["motivos"]}


def test_una_referencia_truncada_que_tiene_toda_la_lista_si_permite_pasar(s):
    """El canario truncado como referencia (decision 86): en una lista lo que
    protege el «no concluyente» de la comparacion se mira URL a URL. Medido con
    los canarios de blogs.uoc.edu y www.uoc.edu: sin esto, `no_concluyente` con
    las 50 URLs presentes y sin cambios."""
    _censo(s, "canario", 1, finish_reason="max_urls_reached")
    ahora = _censo(s, "ahora", 2)
    v = _comparar_y_juzgar(s, ahora)
    assert ahora.comparacion["concluyente"] is False
    assert v["veredicto"] == "pasa", v


def test_si_a_la_referencia_le_falta_una_url_de_la_lista_no_es_pasa(s):
    _censo(s, "canario", 1, finish_reason="max_urls_reached", paginas={5: {"falta": True}})
    v = _comparar_y_juzgar(s, _censo(s, "ahora", 2))
    assert v["veredicto"] == "no_concluyente"
    (d,) = v["no_concluyente_por"]
    assert d["tipo"] == "sin_referencia_url" and d["ejemplos"] == [{"url": SEMILLAS[5]}]


# --- Revision adversarial ------------------------------------------------------

def test_un_rastreo_completo_no_se_compara_con_una_lista(s):
    """Una lista de QA tras cada despliegue suele llevar la home, asi que sus
    semillas «abarcan» las de un rastreo completo sembrado en la home (decision
    86) y, al ser la mas reciente, la elegia como referencia: las alertas del
    censo completo se median sobre 50 paginas en comun (el silencio de las
    decisiones 78 y 86)."""
    from shared.comparacion import censo_anterior

    home = "https://x.com/"
    completo = _censo(s, "completo", 1, config={"max_depth": 3}, seeds=[home])
    _censo(s, "lista", 2, seeds=[home] + SEMILLAS)
    nuevo = _censo(s, "nuevo", 3, config={"max_depth": 3}, seeds=[home])
    assert censo_anterior(s, nuevo).id == completo.id


def test_una_lista_si_puede_compararse_con_su_lista_anterior(s):
    from shared.comparacion import censo_anterior

    antes = _censo(s, "antes", 1)
    ahora = _censo(s, "ahora", 2)
    assert censo_anterior(s, ahora).id == antes.id


def test_un_hallazgo_es_un_motivo_aunque_lo_diga_tambien_la_alerta(s):
    """Decision 36: en una lista la alerta `salen_del_indice` y la comprobacion
    URL a URL `deja_de_ser_indexable` son las MISMAS 10 paginas. Contarlas dos
    veces hace que el CI informe de 20 problemas donde hay 10."""
    _censo(s, "antes", 1)
    noindex = {i: {"indexability_status": "Noindex"} for i in range(10)}
    v = _comparar_y_juzgar(s, _censo(s, "ahora", 2, paginas=noindex))
    assert v["veredicto"] == "falla"
    assert sum(m["paginas"] for m in v["motivos"]) == 10, v["motivos"]
    (m,) = v["motivos"]
    # Lo que dice la alerta no se pierde: viaja con el motivo URL a URL
    assert m["tipo"] == "deja_de_ser_indexable" and m["regla"] == "salen_del_indice"
    assert m["que_decide_google"]


def test_con_mas_paginas_que_la_muestra_tampoco_se_cuentan_dos_veces(s):
    """Los ejemplos de la alerta son 10 (`MUESTRA`): con 25 no se puede
    comprobar pagina a pagina, y seguian saliendo 50 problemas donde hay 25."""
    _censo(s, "antes", 1)
    noindex = {i: {"indexability_status": "Noindex"} for i in range(25)}
    v = _comparar_y_juzgar(s, _censo(s, "ahora", 2, paginas=noindex))
    assert [(m["tipo"], m["paginas"]) for m in v["motivos"]] == [("deja_de_ser_indexable", 25)]


def test_una_alerta_que_no_cubre_el_url_a_url_sigue_siendo_un_motivo():
    """Sin el grupo equivalente (p. ej. la referencia no respondia 200 y la
    pagina queda en `sin_referencia_url`), la alerta no se pierde."""
    from types import SimpleNamespace

    job = SimpleNamespace(
        id=uuid.uuid4(), name="j", status="completed", finish_reason="finished",
        config=LISTA, seeds=[], health=None,
        comparacion={"comparable": True, "concluyente": True, "comparado_con": None,
                     "alertas": [{"severidad": "critical", "regla": "canonical_a_otro_host",
                                  "paginas": 1, "de": 1, "pct": 100.0,
                                  "que_decide_google": "x",
                                  "ejemplos": [{"url": SEMILLAS[0]}]}]})
    v = veredicto(None, job)
    assert v["veredicto"] == "falla"
    assert [m["tipo"] for m in v["motivos"]] == ["alerta"]


def test_relanzar_la_lista_no_pone_en_verde_lo_que_sigue_roto(s):
    """El CI falla, alguien pulsa «re-run» y la lista se compara con el censo
    ROTO: las 10 seguian en noindex y, como no cambiaban, salia `pasa`. La
    referencia de cada URL es el ultimo estado que paso, no el ultimo censo."""
    _censo(s, "antes", 1)
    noindex = {i: {"indexability_status": "Noindex"} for i in range(10)}
    assert _comparar_y_juzgar(s, _censo(s, "roto", 2, paginas=noindex))["veredicto"] == "falla"
    v = _comparar_y_juzgar(s, _censo(s, "reintento", 3, paginas=noindex))
    assert v["veredicto"] == "falla", v
    (m,) = v["motivos"]
    assert (m["tipo"], m["paginas"]) == ("deja_de_ser_indexable", 10)
    # Arreglado, vuelve a pasar
    assert _comparar_y_juzgar(s, _censo(s, "arreglado", 4))["veredicto"] == "pasa"


def test_relanzar_tampoco_pone_en_verde_un_canonical_cambiado(s):
    _censo(s, "antes", 1)
    otro = {3: {"canonical": "https://x.com/otra-url", "indexability_status": "Canonicalised"}}
    assert _comparar_y_juzgar(s, _censo(s, "roto", 2, paginas=otro))["veredicto"] == "falla"
    v = _comparar_y_juzgar(s, _censo(s, "reintento", 3, paginas=otro))
    assert [m["tipo"] for m in v["motivos"]] == ["cambia_el_canonical"], v


# --- Una URL de la lista que redirige (decision 133) --------------------------

def _destino(s, job, url, **campos):
    from shared.models import HtmlMeta, Url

    campos = {"status_code": 200, "indexability_status": "Indexable", **campos}
    u = Url(job_id=job.id, url=url, url_hash=f"{job.name}-{url}", is_internal=True,
            is_html=True, word_count=500, **campos)
    s.add(u)
    s.flush()
    s.add(HtmlMeta(url_id=u.id, title="T", canonical_href=url))
    s.flush()


def _redirige(i, destino, codigo=301):
    return {i: {"status_code": codigo, "indexability_status": f"Redirect ({codigo})",
                "redirect_url": destino}}


def test_redirigir_a_una_pagina_sana_es_aviso_con_el_destino(s):
    """Una URL clave que ahora redirige a otra que responde 200 indexable."""
    _censo(s, "antes", 1)
    ahora = _censo(s, "ahora", 2, paginas=_redirige(4, "https://x.com/nueva"))
    _destino(s, ahora, "https://x.com/nueva")
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "aviso", v
    assert v["motivos"] == [] and v["no_concluyente_por"] == []
    (a,) = v["avisos"]
    assert a["tipo"] == "redirige" and a["paginas"] == 1
    assert a["ejemplos"] == [{"url": SEMILLAS[4], "ahora": 301, "antes": 200,
                              "destino": "https://x.com/nueva"}]


def test_se_sigue_la_cadena_hasta_el_final(s):
    _censo(s, "antes", 1)
    ahora = _censo(s, "ahora", 2, paginas=_redirige(4, "https://x.com/paso"))
    _destino(s, ahora, "https://x.com/paso", status_code=302,
             indexability_status="Redirect (302)", redirect_url="https://x.com/final")
    _destino(s, ahora, "https://x.com/final")
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "aviso", v
    (e,) = v["avisos"][0]["ejemplos"]
    assert (e["destino"], e["saltos"]) == ("https://x.com/final", 2)


def test_redirigir_a_una_pagina_rota_es_falla(s):
    _censo(s, "antes", 1)
    ahora = _censo(s, "ahora", 2, paginas=_redirige(4, "https://x.com/rota"))
    _destino(s, ahora, "https://x.com/rota", status_code=404,
             indexability_status="Client Error (404)")
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "falla"
    (m,) = [m for m in v["motivos"] if m["tipo"] == "no_responde_200"]
    assert m["ejemplos"][0]["destino"] == "https://x.com/rota"
    assert m["ejemplos"][0]["estado_destino"] == 404
    assert v["avisos"] == []


def test_redirigir_a_una_pagina_noindex_es_falla(s):
    _censo(s, "antes", 1)
    ahora = _censo(s, "ahora", 2, paginas=_redirige(4, "https://x.com/oculta"))
    _destino(s, ahora, "https://x.com/oculta", indexability_status="Noindex")
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "falla"
    (m,) = [m for m in v["motivos"] if m["tipo"] == "no_responde_200"]
    assert m["ejemplos"][0]["estado_destino"] == "Noindex"


def test_una_cadena_que_da_la_vuelta_es_falla(s):
    _censo(s, "antes", 1)
    ahora = _censo(s, "ahora", 2, paginas=_redirige(4, "https://x.com/bucle"))
    _destino(s, ahora, "https://x.com/bucle", status_code=301,
             indexability_status="Redirect (301)", redirect_url=SEMILLAS[4])
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "falla"
    assert [m["tipo"] for m in v["motivos"]] == ["no_responde_200"]


def test_redirigir_a_algo_que_no_se_rastreo_no_es_concluyente(s):
    _censo(s, "antes", 1)
    ahora = _censo(s, "ahora", 2, paginas=_redirige(4, "https://otro.com/fuera"))
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "no_concluyente", v
    assert [d["tipo"] for d in v["no_concluyente_por"]] == ["destino_sin_rastrear"]


def test_un_fallo_pesa_mas_que_un_aviso(s):
    _censo(s, "antes", 1)
    paginas = {**_redirige(4, "https://x.com/nueva"),
               7: {"status_code": 500, "indexability_status": "Server Error (500)"}}
    ahora = _censo(s, "ahora", 2, paginas=paginas)
    _destino(s, ahora, "https://x.com/nueva")
    v = _comparar_y_juzgar(s, ahora)
    assert v["veredicto"] == "falla"
    assert [a["tipo"] for a in v["avisos"]] == ["redirige"]
