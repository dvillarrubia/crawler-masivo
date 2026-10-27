"""Backlinks subidos en CSV (#31, decision 212): la lectura de cada export,
el cruce con el censo y el PageRank personalizado sobre grafos pequenos.
Funciones puras, sin base de datos (el analisis entero va en
`test_backlinks_db.py`)."""

from __future__ import annotations

import numpy as np
import pytest

from analysis import pagerank as prk
from shared import backlinks as B

AHREFS = ("Referring page title\tReferring page URL\tDomain rating\tUR\tExternal links\tTarget URL\t"
          "Anchor\tType\tNofollow\tUGC\tSponsored\tLost status\n"
          "Blog A\thttps://blog-a.com/post\t60\t30\t9\thttps://www.x.com/Producto/\tver\ttext\tfalse\tfalse\tfalse\t\n"
          "Blog B\thttps://blog-b.com/\t20\t12\t2\thttps://x.com/viejo\tleer\ttext\ttrue\tfalse\tfalse\t\n"
          "Foro\thttps://foro.com/hilo\t40\t5\t0\thttp://x.com/roto?utm_source=foro\taqui\ttext\tfalse\tfalse\tfalse\tlost\n"
          "Web C\thttps://web-c.com/\t10\t8\t3\thttps://x.com/a%C3%B1o/\tano\ttext\tfalse\tfalse\tfalse\t\n")
SEMRUSH = ("Page ascore,Source title,Source url,Target url,Anchor,External links,Internal links,Nofollow,"
           "Sponsored,Ugc,First seen,Last seen,New link,Lost link\n"
           "45,Uno,https://uno.com/a,https://x.com/,inicio,4,10,false,false,false,2026-01-01,2026-10-01,false,false\n"
           "30,Dos,https://dos.com/b,https://x.com/b,b,0,3,true,false,false,2026-01-01,2026-10-01,false,false\n")
MAJESTIC = ("Source URL,Target URL,Anchor Text,SourceCitationFlow,SourceTrustFlow,FlagNoFollow,FlagDeleted\n"
            "https://m.com/1,https://x.com/c,c,22,10,-,-\n")


def test_ahrefs_en_utf16_con_tabuladores():
    lec = B.leer_csv(AHREFS.encode("utf-16"))
    assert lec.fuente == "ahrefs" and lec.leidas == 4
    assert (lec.descartadas_nofollow, lec.descartadas_perdidas) == (1, 1)
    urls = [f["url_to"] for f in lec.filas]
    assert urls == ["https://www.x.com/Producto/", "https://x.com/a%C3%B1o/"]
    # La aportacion es la de cada backlink: UR del origen / (externos + 1)
    assert lec.filas[0]["aportacion"] == pytest.approx(30 / 10)
    assert lec.filas[0]["dominio_from"] == "blog-a.com"


def test_semrush_y_majestic():
    lec = B.leer_csv(SEMRUSH.encode())
    assert lec.fuente == "semrush" and [f["url_to"] for f in lec.filas] == ["https://x.com/"]
    assert lec.filas[0]["aportacion"] == pytest.approx(45 / 5)
    lec = B.leer_csv(MAJESTIC.encode("utf-8-sig"))
    assert lec.fuente == "majestic" and lec.filas[0]["aportacion"] == pytest.approx(22.0)


def test_un_csv_sin_las_columnas_se_rechaza():
    with pytest.raises(B.CsvNoValido):
        B.leer_csv(b"url,clics\nhttps://x.com/,3\n")
    with pytest.raises(B.CsvNoValido):
        B.leer_csv(b"")


def test_la_clave_no_pasa_la_ruta_a_minusculas():
    assert B.clave_backlink("https://www.X.com/Producto/") == "x.com/Producto"
    assert B.clave_backlink("http://x.com/Producto#arriba") == "x.com/Producto"
    assert B.clave_backlink("https://x.com/a%C3%B1o/") == B.clave_backlink("https://x.com/año")
    assert B.clave_backlink("https://x.com/producto") != B.clave_backlink("https://x.com/Producto")
    assert B.clave_backlink("https://x.com/") == "x.com/"


def test_el_cruce_prueba_sin_la_query_y_guarda_lo_que_no_casa():
    urls = [(1, "https://x.com/"), (2, "https://x.com/roto"), (3, "https://x.com/b?pagina=2")]
    filas = [("https://www.x.com/", "https://a.com/", "a.com", 2.0),
             ("http://x.com/roto?utm_source=foro", "https://f.com/", "f.com", 1.0),
             ("https://x.com/b", "https://b.com/", "b.com", 1.0),        # la del censo lleva query: no casa
             ("https://x.com/antigua", "https://c.com/", "c.com", 5.0),
             ("https://x.com/antigua", "https://d.com/", "d.com", 1.0)]
    c = B.cruzar(filas, urls)
    assert set(c.por_url) == {1, 2}
    assert c.por_url[1] == {"backlinks": 1, "aportacion": 2.0, "dominios": 1, "origenes": ["https://a.com/"]}
    assert (c.filas, c.casadas, c.parte_casada) == (5, 2, 0.4)
    assert c.no_rastreadas[0] == {"url": "https://x.com/antigua", "backlinks": 2, "aportacion": 6.0, "dominios": 2}


# ---------------------------------------------------------------------------
# PageRank personalizado
# ---------------------------------------------------------------------------
def _grafo():
    # 0 home -> 1, 2; 1 -> 0; 2 -> 0; 3 es una 301 a 1; 4 un 404
    src = np.array([0, 0, 1, 2, 3])
    dst = np.array([1, 2, 0, 0, 1])
    w = np.ones(5)
    indexables = np.array([True, True, True, False, False])
    return src, dst, w, indexables


def test_con_beta_1_es_exactamente_el_interno():
    src, dst, w, idx = _grafo()
    interno = prk.pagerank(5, src, dst, w, teletransporte=idx)
    externo = np.array([0, 0, 0, 5.0, 3.0])
    v = prk.teletransporte_externo(idx, externo, 1.0)
    assert np.allclose(prk.pagerank(5, src, dst, w, teletransporte=v, colgante=idx), interno)
    # Sin nada de fuera, igual
    v = prk.teletransporte_externo(idx, np.zeros(5), 0.3)
    assert np.allclose(prk.pagerank(5, src, dst, w, teletransporte=v, colgante=idx), interno)


def test_lo_que_llega_a_una_301_pasa_a_su_destino_y_lo_que_llega_a_un_404_se_pierde():
    src, dst, w, idx = _grafo()
    interno = prk.pagerank(5, src, dst, w, teletransporte=idx)
    a_301 = prk.pagerank(5, src, dst, w, teletransporte=prk.teletransporte_externo(
        idx, np.array([0, 0, 0, 1.0, 0]), 0.5), colgante=idx)
    assert a_301[1] > interno[1], "la 301 lleva su autoridad al destino"
    a_404 = prk.pagerank(5, src, dst, w, teletransporte=prk.teletransporte_externo(
        idx, np.array([0, 0, 0, 0, 1.0]), 0.5), colgante=idx)
    cats = [prk.INDEXABLE] * 3 + [prk.REDIRECCION, prk.ERROR]
    assert prk.desperdiciado(prk.reparto(a_404, cats)) > prk.desperdiciado(prk.reparto(interno, cats))
    assert a_404.sum() == pytest.approx(1.0)


def test_la_masa_colgante_no_vuelve_al_404():
    """Si fuera al salto, un 404 con backlinks recibiria otra vez su propia
    masa en cada vuelta y lo perdido saldria inflado."""
    src, dst, w, idx = _grafo()
    v = prk.teletransporte_externo(idx, np.array([0, 0, 0, 0, 1.0]), 0.5)
    bien = prk.pagerank(5, src, dst, w, teletransporte=v, colgante=idx)
    mal = prk.pagerank(5, src, dst, w, teletransporte=v)
    assert bien[4] < mal[4]
