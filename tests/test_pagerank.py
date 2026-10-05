"""Modelo del PageRank interno (analysis/pagerank.py), sin base de datos.

Cubre lo que #24 pedia probar sobre grafos pequenos: la redireccion pasa el
PageRank a su destino, los errores reciben pero no entran en el
teletransporte, y un menu que el HTML no delata pesa como menu porque se
repite.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from analysis import pagerank as prk  # noqa: E402


def _pr(n, aristas, indexables=None):
    src = np.array([a for a, _, _ in aristas])
    dst = np.array([b for _, b, _ in aristas])
    w = np.array([p for _, _, p in aristas], dtype=float)
    mascara = None if indexables is None else np.array(indexables, dtype=bool)
    return prk.pagerank(n, src, dst, w, teletransporte=mascara, tol=1e-12, max_iter=500)


# -- Peso: posicion con techo por repeticion ---------------------------------

@pytest.mark.parametrize("rep, esperado", [
    (0.0, 1.0),
    (prk.REP_EDITORIAL, 1.0),
    (0.2, 0.5),
    (prk.REP_EDITORIAL / prk.TECHO_PLANTILLA, prk.TECHO_PLANTILLA),
    (1.0, prk.TECHO_PLANTILLA),
])
def test_techo_por_repeticion(rep, esperado):
    assert prk.techo_por_repeticion(rep) == pytest.approx(esperado)


def test_menu_no_reconocido_pesa_como_menu():
    # B1 de #24: un <div class="main-menu"> sale `content`; repetido en el 90%
    # de las paginas no puede pesar mas que un enlace de nav.
    assert prk.peso_arista("content", 0.9) == pytest.approx(prk.PESO_POSICION["nav"])
    # Un enlace editorial que no se repite conserva su peso
    assert prk.peso_arista("content", 0.02) == 1.0
    # El techo nunca sube un peso: el pie sigue pesando lo del pie
    assert prk.peso_arista("footer", 1.0) == prk.PESO_POSICION["footer"]
    # Sin repeticion medida (rastreo pequeno) manda la posicion
    assert prk.peso_arista("nav", None) == prk.PESO_POSICION["nav"]
    assert prk.peso_arista("desconocida", None) == prk.PESO_POSICION[None]


def test_lo_recibido_crece_con_la_repeticion():
    # Lo que se descarto de #24: con 1 - sqrt(rep), un enlace en todas las
    # paginas transmitia menos en total que en la mitad. Con el techo, el
    # total (repeticion x peso) nunca baja al repetirse mas.
    totales = [r / 1000 * prk.peso_arista("content", r / 1000) for r in range(1, 1001)]
    assert all(b >= a - 1e-12 for a, b in zip(totales, totales[1:]))


def test_sql_peso_sale_de_las_mismas_constantes():
    sql = prk.sql_peso_arista("rep", "pos")
    for constante in (prk.REP_EDITORIAL, prk.TECHO_PLANTILLA):
        assert str(constante) in sql
    for pos, peso in prk.PESO_POSICION.items():
        if pos is not None:
            assert f"WHEN '{pos}' THEN {peso}" in sql


# -- Categorias ---------------------------------------------------------------

@pytest.mark.parametrize("args, esperada", [
    ((200, True, True, None), prk.INDEXABLE),
    ((200, True, False, None), prk.NO_INDEXABLE),
    ((200, False, None, None), prk.RECURSO),
    ((301, False, False, "https://x.com/b"), prk.REDIRECCION),
    ((None, False, None, "https://x.com/b"), prk.REDIRECCION),  # Redirect (JS)
    ((404, True, False, None), prk.ERROR),
    ((503, True, False, None), prk.ERROR),
    ((None, False, None, None), prk.SIN_RESPUESTA),
])
def test_categoria(args, esperada):
    assert prk.categoria(*args) == esperada


# -- Iteracion ----------------------------------------------------------------

def test_suma_uno():
    pr = _pr(4, [(0, 1, 1), (1, 2, 1), (2, 0, 1), (0, 3, 1)], [1, 1, 1, 0])
    assert pr.sum() == pytest.approx(1.0)


def test_redireccion_pasa_el_pagerank_a_su_destino():
    # 0 home -> 1 (301) -> 2 destino; 0 -> 3 otra pagina
    idx = [1, 0, 1, 1]
    sin_arista = _pr(4, [(0, 1, 1), (0, 3, 1), (2, 0, 1), (3, 0, 1)], idx)
    con_arista = _pr(4, [(0, 1, 1), (0, 3, 1), (1, 2, 1), (2, 0, 1), (3, 0, 1)], idx)
    assert con_arista[2] > sin_arista[2] * 1.5
    # El destino recibe practicamente lo mismo que una pagina enlazada directa
    assert con_arista[2] == pytest.approx(con_arista[3], rel=0.2)


def test_error_recibe_pero_no_entra_en_el_teletransporte():
    # 0 <-> 1 indexables; 2 es un 404 enlazado desde 1; 3 es un 404 sin enlaces
    aristas = [(0, 1, 1), (1, 0, 1), (1, 2, 1)]
    pr = _pr(4, aristas, [1, 1, 0, 0])
    assert pr[2] > 0          # recibe por su enlace entrante: es PR desperdiciado
    assert pr[3] == 0         # nadie le enlaza y no hay salto aleatorio hacia el
    uniforme = _pr(4, aristas, None)
    assert uniforme[3] > 0    # con el modelo anterior si recibia


def test_masa_colgante_solo_va_a_indexables():
    # 2 es un 404 sin salida: su masa vuelve solo a 0 y 1, no al PDF 3
    pr = _pr(4, [(0, 1, 1), (1, 2, 1), (1, 0, 1)], [1, 1, 0, 0])
    assert pr[3] == 0


def test_sin_indexables_cae_al_teletransporte_uniforme():
    pr = _pr(3, [(0, 1, 1), (1, 2, 1)], [0, 0, 0])
    assert pr.sum() == pytest.approx(1.0)
    assert (pr > 0).all()


def test_peso_reparte_la_salida():
    # 0 enlaza a 1 con peso 1 y a 2 con peso 0.05: 1 recibe ~20 veces mas
    pr = _pr(3, [(0, 1, 1.0), (0, 2, 0.05), (1, 0, 1), (2, 0, 1)], [1, 1, 1])
    assert pr[1] > pr[2] * 3


def test_reparto_y_desperdiciado():
    pr = np.array([0.5, 0.2, 0.2, 0.1])
    cats = [prk.INDEXABLE, prk.REDIRECCION, prk.ERROR, prk.SIN_RESPUESTA]
    r = prk.reparto(pr, cats)
    assert r[prk.INDEXABLE] == 0.5
    assert r[prk.RECURSO] == 0.0
    assert set(r) == set(prk.CATEGORIAS)
    assert prk.desperdiciado(r) == pytest.approx(0.3)


def test_grafo_vacio():
    assert prk.pagerank(0, [], [], []).size == 0
