"""Rastreo incremental (decision 220): las reglas puras.

Lo que decide si un ciclo es completo, como crece el intervalo de una URL y
que estado le queda despues de verla. Y que toda columna de `urls` esta
clasificada para el arrastre: una columna nueva sin decidir si se copia o la
calcula el analisis saldria mal en cada fila arrastrada sin que nada avisara.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shared.incremental import (
    BASE_TRUNCADA, CONFIG_CAMBIADA, FORZADO, SIN_BASE, SIN_ESTADO, TOCA_COMPLETO,
    ciclos_hasta, config_de, decidir, estado_nuevo, huella, siguiente_intervalo,
)

CFG = config_de({"incremental": {"activo": True, "completo_cada": 4}})


def _base(**k):
    b = {"finish_reason": "finished", "huella": "h", "ciclos_desde_completo": 0,
         "forzar_completo": None, "estado_ok": True}
    b.update(k)
    return b


# --- Completo o incremental ---------------------------------------------------

def test_sin_censo_anterior_es_completo():
    assert decidir(CFG, None, "h") == ("completo", SIN_BASE)


@pytest.mark.parametrize("base, motivo", [
    (_base(finish_reason="max_urls_reached"), BASE_TRUNCADA),
    (_base(estado_ok=False), SIN_ESTADO),
    (_base(forzar_completo="muestra_control"), FORZADO),
    (_base(huella="otra"), CONFIG_CAMBIADA),
    (_base(ciclos_desde_completo=3), TOCA_COMPLETO),
])
def test_lo_que_obliga_a_un_completo(base, motivo):
    assert decidir(CFG, base, "h") == ("completo", motivo)


def test_un_completo_cada_n_ciclos_contando_el_propio():
    """completo_cada=4: completo, tres incrementales, completo."""
    modos, desde = [], None
    for _ in range(8):
        base = None if desde is None else _base(ciclos_desde_completo=desde)
        modo, _motivo = decidir(CFG, base, "h")
        modos.append(modo)
        desde = 0 if modo == "completo" else desde + 1
    assert modos == ["completo", "incremental", "incremental", "incremental"] * 2


def test_la_huella_ignora_el_bloque_incremental_y_el_orden_de_las_semillas():
    a = huella(["https://x.com/", "https://x.com/b"],
               {"max_depth": 3, "incremental": {"activo": True}})
    b = huella(["https://x.com/b", "https://x.com/"],
               {"max_depth": 3, "incremental": {"activo": True, "muestra_control": 0.5}})
    assert a == b
    assert a != huella(["https://x.com/"], {"max_depth": 3})
    assert a != huella(["https://x.com/", "https://x.com/b"], {"max_depth": 4})


# --- Intervalo adaptativo -----------------------------------------------------

def test_sin_cambios_crece_hasta_el_maximo():
    i, vistos = 1.0, []
    for _ in range(10):
        i = siguiente_intervalo(i, False, 4.0)
        vistos.append(i)
    assert vistos[0] == pytest.approx(1.4)
    assert max(vistos) == 4.0 and vistos[-1] == 4.0
    assert vistos == sorted(vistos)


def test_con_cambios_converge_al_minimo_sin_rebotar():
    i, vistos = 4.0, []
    for _ in range(30):
        i = siguiente_intervalo(i, True, 4.0)
        vistos.append(i)
    assert vistos == sorted(vistos, reverse=True)
    assert vistos[0] == pytest.approx(0.8 * 4 + 0.2)
    assert vistos[-1] == pytest.approx(1.0, abs=1e-2) and min(vistos) >= 1.0


def test_siempre_se_vuelve_a_pedir_al_menos_en_el_siguiente_ciclo():
    assert ciclos_hasta(1.0) == 1 and ciclos_hasta(1.99) == 1
    assert ciclos_hasta(2.0) == 2 and ciclos_hasta(4.0) == 4


# --- Estado de una URL tras verla -----------------------------------------------

PREVIO = {"intervalo": 2.0, "proximo_ciclo": 5, "sin_cambios": 1,
          "ultimo_cambio_ciclo": 1, "etag": '"v1"', "last_modified": None,
          "firma_contenido": "c1", "firma_seo": "s1", "ultima_peticion": None}


def _fila(**k):
    f = {"url": "https://x.com/a", "crawl_depth": 2, "fetch_mode": "fetched",
         "status_code": 200, "response_class": "contenido", "firma_contenido": "c1",
         "firma_seo": "s1", "etag": '"v1"', "last_modified": None,
         "last_crawled_at": None}
    f.update(k)
    return f


def test_pedida_sin_cambios_alarga_el_intervalo():
    e, cambio = estado_nuevo(PREVIO, _fila(), ciclo=3, maximo=4.0, fijo=False)
    assert cambio is False
    assert e["intervalo"] == pytest.approx(2.8) and e["proximo_ciclo"] == 5
    assert e["sin_cambios"] == 2


def test_pedida_con_otra_firma_seo_es_un_cambio():
    e, cambio = estado_nuevo(PREVIO, _fila(firma_seo="s2", etag='"v2"'),
                             ciclo=3, maximo=4.0, fijo=False)
    assert cambio is True
    assert e["firma_seo"] == "s2" and e["etag"] == '"v2"'
    assert e["ultimo_cambio_ciclo"] == 3 and e["sin_cambios"] == 0
    assert e["proximo_ciclo"] == 3 + ciclos_hasta(0.8 * 2 + 0.2)


def test_un_304_es_sin_cambios_y_conserva_las_firmas():
    e, cambio = estado_nuevo(PREVIO, _fila(fetch_mode="not_modified", status_code=200,
                                           firma_seo=None, firma_contenido=None, etag=None),
                             ciclo=3, maximo=4.0, fijo=False)
    assert cambio is False
    assert (e["firma_contenido"], e["firma_seo"], e["etag"]) == ("c1", "s1", '"v1"')


def test_arrastrada_no_se_vio_y_conserva_su_turno():
    e, cambio = estado_nuevo(PREVIO, _fila(fetch_mode="carried"), ciclo=3,
                             maximo=4.0, fijo=False)
    assert cambio is None
    assert e["proximo_ciclo"] == 5 and e["intervalo"] == 2.0


@pytest.mark.parametrize("fila", [
    _fila(status_code=500), _fila(status_code=404), _fila(status_code=None),
    _fila(response_class="challenge_waf"),
])
def test_un_error_se_repite_en_el_siguiente_ciclo_y_sin_condicionales(fila):
    e, cambio = estado_nuevo(PREVIO, fila, ciclo=3, maximo=4.0, fijo=False)
    assert cambio is None
    assert e["proximo_ciclo"] == 4 and e["intervalo"] == 1.0
    assert e["etag"] is None and e["last_modified"] is None
    # Lo que se sabia de la pagina no se pisa con lo del error.
    assert e["firma_seo"] == "s1"


def test_una_url_nueva_toca_en_el_siguiente_ciclo():
    e, cambio = estado_nuevo(None, _fila(), ciclo=3, maximo=4.0, fijo=True)
    assert cambio is None and e["proximo_ciclo"] == 4 and e["fijo"] is True


# --- Columnas de `urls` en el arrastre ----------------------------------------

def test_toda_columna_de_urls_esta_clasificada_para_el_arrastre():
    from analysis.arrastre import CALCULADAS, COPIADAS, DE_LA_FILA
    from shared.models import Url

    columnas = {c.name for c in Url.__table__.columns}
    grupos = [set(COPIADAS), set(CALCULADAS), set(DE_LA_FILA)]
    assert set().union(*grupos) == columnas, (
        "columna de urls sin clasificar en analysis/arrastre.py: "
        f"{columnas - set().union(*grupos)}")
    assert sum(len(g) for g in grupos) == len(columnas), "columna en dos grupos"


def test_lo_que_escribe_el_rastreo_se_copia():
    """Lo que el pipeline escribe en una fila nueva no lo recalcula nadie: si
    no se copia, la fila arrastrada se queda sin ello."""
    from analysis.arrastre import COPIADAS

    fuente = (Path(__file__).parents[1] / "crawler" / "seo_crawler" / "pipelines.py").read_text()
    bloque = fuente[fuente.index("url_obj = Url("):]
    bloque = bloque[:bloque.index("self.session.add(url_obj)")]
    escritas = set(re.findall(r"(\w+)=data", bloque)) | set(re.findall(r"(\w+)=url_hash", bloque))
    escritas -= {"job_id"}
    assert escritas, "no se ha leido el constructor de Url del pipeline"
    assert escritas - {"fetch_mode"} <= set(COPIADAS), escritas - set(COPIADAS)
