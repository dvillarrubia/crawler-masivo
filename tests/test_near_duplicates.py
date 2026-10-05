"""Casi duplicados: firma MinHash, LSH y agrupacion.

Todo son funciones puras sobre texto, sin base de datos. Lo que se comprueba
aqui es que la similitud que se reporta significa lo que dice --se contrasta
contra la Jaccard exacta de los trigramas-- y que no se inventan duplicados.
"""

from __future__ import annotations

import random

import pytest

from analysis import near_duplicates as nd


VOCAB = (
    "hotel piscina climatizada spa restaurante buffet vistas mar costa familia "
    "ninos parking wifi gratuito gimnasio tenis excursion traslado aeropuerto "
    "masaje sauna jacuzzi terraza privada desayuno incluido reserva cancelacion"
).split()


def _texto(palabras: int, semilla: int = 0) -> str:
    rnd = random.Random(semilla)
    return " ".join(rnd.choice(VOCAB) for _ in range(palabras))


def _cambiar(texto: str, veces: int, palabra: str, semilla: int = 9) -> str:
    rnd = random.Random(semilla)
    p = texto.split()
    for i in rnd.sample(range(len(p)), veces):
        p[i] = palabra
    return " ".join(p)


# -- Firma -------------------------------------------------------------------

def test_firma_es_determinista():
    t = _texto(120)
    assert (nd.firma(t) == nd.firma(t)).all()


@pytest.mark.parametrize("texto", [None, "", "dos palabras", " ".join(["corto"] * 29)])
def test_firma_none_si_no_hay_texto_suficiente(texto):
    # Por debajo del minimo la firma es ruido: mejor no medir que inventar.
    assert nd.firma(texto) is None


def test_firma_justo_en_el_minimo():
    assert nd.firma(_texto(nd.MIN_PALABRAS)) is not None


# -- Similitud ---------------------------------------------------------------

def test_identicos_dan_uno():
    t = _texto(200)
    assert nd.similitud(nd.firma(t), nd.firma(t)) == 1.0


def test_textos_sin_relacion_dan_casi_cero():
    a, b = _texto(200, semilla=1), _texto(200, semilla=2)
    assert nd.similitud(nd.firma(a), nd.firma(b)) < 0.5


def test_la_similitud_estimada_sigue_a_la_jaccard_real():
    # Esta es la razon de usar MinHash y no simhash: el numero que se le ensena
    # al cliente tiene que parecerse al solape real del texto.
    base = _texto(300)
    for veces in (3, 10, 30, 60):
        otro = _cambiar(base, veces, "maspalomas")
        estimada = nd.similitud(nd.firma(base), nd.firma(otro))
        real = nd.jaccard(base, otro)
        assert abs(estimada - real) < 0.12, (veces, estimada, real)


def test_plantilla_con_entidad_cambiada_es_casi_duplicado():
    # El caso que de verdad importa en una auditoria: la misma ficha con la
    # ciudad (o el producto) cambiada.
    base = _texto(350)
    a = _cambiar(base, 4, "maspalomas")
    b = _cambiar(base, 4, "meloneras")
    assert nd.similitud(nd.firma(a), nd.firma(b)) >= nd.UMBRAL_SIMILITUD


# -- Umbral ------------------------------------------------------------------

def test_el_umbral_filtra():
    base = _texto(300)
    fs = {1: nd.firma(base), 2: nd.firma(_cambiar(base, 20, "otra"))}
    assert nd.emparejar(fs, umbral=0.95) == {}
    assert nd.emparejar(fs, umbral=0.6)


@pytest.mark.parametrize("umbral", [0.99, 0.95, 0.9, 0.85, 0.8, 0.7, 0.6])
def test_el_reparto_de_bandas_sigue_al_umbral(umbral):
    """Bajar el umbral tiene que encontrar mas parejas, no las mismas.

    Con un reparto fijo pensado para el 90%, pedir 0,6 no propondria ni una
    pareja nueva: no llegarian a medirse.
    """
    bandas, filas = nd.reparto_bandas(umbral)
    assert bandas * filas == nd.FIRMA
    assert nd.deteccion(bandas, filas, umbral) >= 0.95


def test_bajar_el_umbral_encuentra_mas():
    base = _texto(400)
    fs = {0: nd.firma(base)}
    for i in range(1, 10):
        fs[i] = nd.firma(_cambiar(base, i * 6, "maspalomas", semilla=i))
    encontradas = [len(nd.analizar(fs, umbral=u)) for u in (0.95, 0.85, 0.7)]
    assert encontradas == sorted(encontradas)
    assert encontradas[-1] > encontradas[0]


# -- Agrupacion y analisis ---------------------------------------------------

def test_agrupar_colapsa_las_identicas():
    t = _texto(150)
    fs = {1: nd.firma(t), 2: nd.firma(t), 3: nd.firma(_texto(150, semilla=4))}
    representantes, miembros = nd.agrupar_identicas(fs)
    assert len(representantes) == 2
    assert miembros[1] == [1, 2]


def test_analizar_cuenta_identicas_y_parecidas():
    base = _texto(350)
    a = _cambiar(base, 4, "maspalomas")
    b = _cambiar(base, 4, "meloneras")
    fs = {
        1: nd.firma(a),
        2: nd.firma(a),                    # identica a la 1
        3: nd.firma(b),                    # parecida
        4: nd.firma(_texto(350, semilla=8)),  # nada que ver
    }
    r = nd.analizar(fs)

    assert r[1]["closest"] == 1.0
    assert r[1]["count"] == 2            # la gemela y la parecida
    assert 4 not in r                    # la distinta no sale

    urls_parecidas = {otro for otro, _ in r[3]["ejemplos"]}
    assert urls_parecidas == {1, 2}


def test_analizar_no_devuelve_nada_si_todas_son_distintas():
    fs = {i: nd.firma(_texto(200, semilla=i)) for i in range(1, 6)}
    assert nd.analizar(fs) == {}


def test_ejemplos_van_de_mas_parecida_a_menos_y_estan_capados():
    base = _texto(400)
    fs = {0: nd.firma(base)}
    for i in range(1, 9):
        fs[i] = nd.firma(_cambiar(base, i, "maspalomas", semilla=i))
    r = nd.analizar(fs, umbral=0.5, max_ejemplos=3)

    ejemplos = r[0]["ejemplos"]
    assert len(ejemplos) == 3
    assert [s for _, s in ejemplos] == sorted((s for _, s in ejemplos), reverse=True)
    assert r[0]["count"] >= len(ejemplos)   # el recuento no se capa, los ejemplos si


# -- LSH ---------------------------------------------------------------------

def test_el_lsh_encuentra_las_parejas_claras():
    """Por encima del umbral las bandas no se dejan ninguna pareja.

    Se prueba a 0,95, donde la probabilidad de deteccion es del 99,9%. Justo en
    0,90 el LSH se deja ~1 de cada 25, que es el precio aceptado a cambio de no
    medir millones de parejas irrelevantes (ver las constantes del modulo).
    """
    base = _texto(400)
    fs = {0: nd.firma(base)}
    for i in range(1, 12):
        fs[i] = nd.firma(_cambiar(base, i, "maspalomas", semilla=i))
    fs[99] = nd.firma(_texto(400, semilla=77))

    umbral = 0.95
    candidatas = {
        (a, b)
        for a, b in nd.parejas_candidatas(fs)
        if nd.similitud(fs[a], fs[b]) >= umbral
    }
    ids = sorted(fs)
    fuerza_bruta = {
        (a, b)
        for i, a in enumerate(ids)
        for b in ids[i + 1 :]
        if nd.similitud(fs[a], fs[b]) >= umbral
    }
    assert fuerza_bruta            # que la prueba mida algo
    assert candidatas == fuerza_bruta


def test_la_firma_es_lo_bastante_estable_para_decidir():
    """El numero que se reporta no puede bailar alrededor del umbral.

    Con 64 muestras una pagina al 93% real se reportaba al 87,5% y se caia del
    umbral del 90%; de ahi que la firma sean 256.
    """
    base = _texto(350)
    a = _cambiar(base, 4, "maspalomas")
    b = _cambiar(base, 4, "meloneras")
    real = nd.jaccard(a, b)
    estimada = nd.similitud(nd.firma(a), nd.firma(b))
    assert real >= nd.UMBRAL_SIMILITUD          # de verdad son casi iguales
    assert estimada >= nd.UMBRAL_SIMILITUD      # y el metodo lo dice
    assert abs(estimada - real) < 0.05
