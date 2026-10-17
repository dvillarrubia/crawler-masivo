"""Reconocer la pagina N de una serie por la forma de su URL (decision 122)."""

from __future__ import annotations

import pytest

from shared.paginacion import clave_comparable, es_primera_de, pagina_de


@pytest.mark.parametrize("url, serie_ruta, n, forma", [
    ("https://x.com/blog/page/2/", "/blog", 2, "/page/N"),
    ("https://x.com/blog/page/2", "/blog", 2, "/page/N"),
    ("https://x.com/page/3/", "/", 3, "/page/N"),
    ("https://x.com/noticias/pagina/4/", "/noticias", 4, "/pagina/N"),
    ("https://x.com/noticias/pag/5", "/noticias", 5, "/pag/N"),
    ("https://x.com/tienda/zapatos?page=3", "/tienda/zapatos", 3, "?page"),
    ("https://x.com/tienda/zapatos?pagina=3", "/tienda/zapatos", 3, "?pagina"),
    ("https://x.com/tienda/zapatos?pg=3", "/tienda/zapatos", 3, "?pg"),
    ("https://x.com/?paged=2", "/", 2, "?paged"),
])
def test_formas_fuertes(url, serie_ruta, n, forma):
    p = pagina_de(url)
    assert p is not None
    assert (p.serie[2], p.n, p.forma, p.debil) == (serie_ruta, n, forma, False)


def test_mayusculas_en_el_marcador_y_el_host():
    p = pagina_de("https://X.com/Blog/Page/2/?PAGE=7")
    # El parametro fuerte manda; la ruta conserva su caja (las rutas la
    # distinguen) y el host va en minusculas.
    assert p.n == 7 and p.forma == "?page"
    assert p.serie[1] == "x.com" and p.serie[2] == "/Blog/Page/2"
    q = pagina_de("https://x.com/Blog/PAGE/2/")
    assert (q.serie[2], q.n) == ("/Blog", 2)


def test_barra_final_misma_serie():
    a = pagina_de("https://x.com/blog/page/2/")
    b = pagina_de("https://x.com/blog/page/3")
    assert a.serie == b.serie == clave_comparable("https://x.com/blog/")
    assert es_primera_de("https://x.com/blog", a.serie)
    assert es_primera_de("https://x.com/blog/", a.serie)
    assert es_primera_de("https://x.com/blog/page/1/", a.serie)
    assert not es_primera_de("https://x.com/blog/page/2/", a.serie)
    assert not es_primera_de("https://x.com/blog/ver-todo", a.serie)


def test_otros_parametros_forman_parte_de_la_serie():
    precio = pagina_de("https://x.com/t/z?orden=precio&page=2")
    fecha = pagina_de("https://x.com/t/z?page=2&orden=fecha")
    assert precio.serie != fecha.serie
    assert precio.serie[3] == "orden=precio"
    assert es_primera_de("https://x.com/t/z?orden=precio", precio.serie)
    assert not es_primera_de("https://x.com/t/z", precio.serie)


@pytest.mark.parametrize("url", [
    "https://x.com/?p=123",          # WordPress: id de una entrada
    "https://x.com/producto/123",    # una ficha
    "https://x.com/blog/una-entrada/",
    "https://x.com/2",
    "https://x.com/tienda?page=abc",
])
def test_sin_evidencia_no_cuenta(url):
    assert pagina_de(url) is None


def test_debiles_solo_cuando_se_piden():
    p = pagina_de("https://x.com/?p=2", debiles=True)
    assert (p.serie[2], p.n, p.forma, p.debil) == ("/", 2, "?p", True)
    q = pagina_de("https://x.com/archivo/3/", debiles=True)
    assert (q.serie[2], q.n, q.forma, q.debil) == ("/archivo", 3, "/N", True)
    # El fuerte gana al debil.
    r = pagina_de("https://x.com/blog/page/2/?p=9", debiles=True)
    assert r.forma == "/page/N" and r.n == 2 and not r.debil


def test_basura_no_rompe():
    assert pagina_de(None) is None
    assert pagina_de("") is None
    assert not es_primera_de(None, ("https", "x.com", "/", ""))
