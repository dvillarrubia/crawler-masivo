"""compute_pagerank contra Postgres de verdad: repeticion, canonical, 301 y 404.

La parte que monta las aristas es SQL de Postgres (tablas temporales,
`substring` con regex, cursor con nombre) y no corre en SQLite. Este test se
salta si no hay una BD de pruebas; para correrlo:

    docker exec crawler-masivo-postgres-1 createdb -U crawler crawler_test
    PAGERANK_TEST_DATABASE_URL=postgresql+psycopg2://crawler:crawler@postgres:5432/crawler_test pytest tests/test_pagerank_db.py

NUNCA apuntarlo a la BD de trabajo: crea y borra un job propio, pero crea
tablas si faltan.
"""

from __future__ import annotations

import hashlib
import os
import uuid

import pytest

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL")

np = pytest.importorskip("numpy")

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.models import Base, HtmlMeta, Job, Link, Url  # noqa: E402

BASE = "https://x.com"
N_ARTICULOS = 24


def _h(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


@pytest.fixture()
def sesion():
    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture()
def job(sesion):
    """Blog de 24 articulos con menu en todas las paginas, una variante
    canonicalizada, una 301 y un 404."""
    j = Job(id=uuid.uuid4(), name="test pagerank", status="analyzing",
            seeds=[BASE + "/"], config={})
    sesion.add(j)
    sesion.flush()

    filas: dict[str, Url] = {}

    def url(ruta, status=200, html=True, indexable=True, estado=None,
            redirect=None, depth=1, noindex=False):
        u = Url(job_id=j.id, url=BASE + ruta, url_hash=_h(BASE + ruta),
                is_internal=True, status_code=status, is_html=html,
                indexable=indexable, indexability_status=estado, noindex=noindex,
                redirect_url=redirect, crawl_depth=depth)
        sesion.add(u)
        filas[ruta] = u
        return u

    url("/", depth=0)
    url("/contacto")
    for i in range(N_ARTICULOS):
        url(f"/blog/a{i}")
    url("/blog/a1?orden=precio", indexable=False, estado="Canonicalised")
    url("/viejo", status=301, html=False, indexable=False,
        estado="3xx Redirect", redirect=BASE + "/blog/a2")
    url("/roto", status=404, indexable=False, estado="4xx Client Error")
    # Una pagina noindex que enlaza a un articulo: sus enlaces NO pasan
    # autoridad (C3 de #24), pero ella si recibe.
    url("/tag/x", indexable=False, estado="Noindex", noindex=True)
    sesion.flush()
    sesion.add(HtmlMeta(url_id=filas["/blog/a1?orden=precio"].id,
                        canonical_href=BASE + "/blog/a1"))

    def enlace(desde, hacia, anchor, pos):
        sesion.add(Link(job_id=j.id, from_url_id=filas[desde].id,
                        to_url=BASE + hacia, to_url_hash=_h(BASE + hacia),
                        anchor_text=anchor, is_internal=True, follow=True,
                        link_position=pos))

    con_menu = ["/", "/contacto", "/blog/a1?orden=precio"] + [
        f"/blog/a{i}" for i in range(N_ARTICULOS)]
    for p in con_menu:
        enlace(p, "/contacto", "Contacto", "nav")
        enlace(p, "/", "Inicio", "nav")
    for i in range(N_ARTICULOS):
        enlace("/", f"/blog/a{i}", f"Articulo {i}", "content")
    enlace("/blog/a3", "/roto", "enlace roto", "content")
    enlace("/blog/a4", "/viejo", "enlace a la 301", "content")
    enlace("/blog/a5", "/blog/a1?orden=precio", "variante", "content")
    # La variante enlaza a a9: al consolidarse en la canonica, ese enlace no
    # debe contar (son los enlaces de la canonica, no se suman dos veces)
    enlace("/blog/a1?orden=precio", "/blog/a9", "solo en la variante", "content")
    # La noindex recibe un enlace y enlaza a a7: lo primero cuenta, lo segundo no
    enlace("/blog/a6", "/tag/x", "etiqueta", "content")
    enlace("/tag/x", "/blog/a7", "desde una noindex", "content")
    sesion.commit()
    yield j, filas
    sesion.delete(sesion.get(Job, j.id))
    sesion.commit()


def test_pagerank_con_repeticion_canonical_y_sumideros(sesion, job):
    from analysis.analyzer import SEOAnalyzer

    j, filas = job
    SEOAnalyzer(sesion, str(j.id)).compute_pagerank()
    sesion.commit()

    pr = dict(sesion.execute(
        select(Url.url, Url.pagerank).where(Url.job_id == j.id)).all())
    articulo = lambda i: pr[f"{BASE}/blog/a{i}"]  # noqa: E731

    resumen = sesion.get(Job, j.id).pagerank_resumen
    assert resumen["peso"] == "repeticion"
    assert resumen["aristas_redireccion"] == 1
    assert resumen["aristas_canonical"] == 1
    # El 404 recibe por su enlace entrante: eso es lo desperdiciado
    assert resumen["desperdiciado"] > 0
    assert sum(resumen["reparto"].values()) == pytest.approx(1.0, abs=1e-3)

    # La canonica recibe lo de su variante; el destino de la 301, lo del salto
    assert articulo(1) > articulo(6)
    assert articulo(2) > articulo(6)
    # El enlace que solo tenia la variante no cuenta tras consolidar
    assert articulo(9) == pytest.approx(articulo(6), rel=0.01)


def test_el_canonical_de_la_cabecera_link_tambien_consolida(sesion, job):
    """Si el canonical solo viene en la cabecera `Link`, la variante tambien se
    consolida en la canonica: es el mismo canonical que decide que la pagina sea
    "Canonicalised" (shared/indexabilidad.py)."""
    from analysis.analyzer import SEOAnalyzer

    j, filas = job
    meta = sesion.execute(select(HtmlMeta).where(
        HtmlMeta.url_id == filas["/blog/a1?orden=precio"].id)).scalar_one()
    meta.canonical_href = None
    meta.canonical_header = BASE + "/blog/a1"
    sesion.commit()

    SEOAnalyzer(sesion, str(j.id)).compute_pagerank()
    sesion.commit()

    assert sesion.get(Job, j.id).pagerank_resumen["aristas_canonical"] == 1


def test_una_pagina_noindex_recibe_pero_no_reparte(sesion, job):
    """C3 de #24: Google acaba tratando los enlaces de una noindex como
    nofollow, asi que lo que llega desde ella no es autoridad real. Es el mismo
    criterio que la decision 38 aplica a los enlaces entrantes.

    Lo que ya tenia acumulado no se pierde: la pagina queda colgante y su masa
    va al teletransporte (el conjunto de indexables) y no a sus destinos.
    """
    from analysis.analyzer import SEOAnalyzer

    j, filas = job
    SEOAnalyzer(sesion, str(j.id)).compute_pagerank()
    sesion.commit()

    pr = dict(sesion.execute(
        select(Url.url, Url.pagerank_raw).where(Url.job_id == j.id)).all())
    articulo = lambda i: pr[f"{BASE}/blog/a{i}"]  # noqa: E731

    # La noindex SI recibe: su enlace entrante es autoridad que se tira, y eso
    # es justo lo que hay que poder medir.
    assert pr[f"{BASE}/tag/x"] > 0
    # Pero a7, que solo recibe desde la noindex, no queda por encima de sus
    # hermanos que no reciben nada de ahi.
    assert articulo(7) == pytest.approx(articulo(6), rel=0.02)


@pytest.mark.parametrize("posicion", ["content", "nav", "footer", "desconocida", None])
@pytest.mark.parametrize("rep", [0.0, 0.1, 0.2, 0.3, 0.95])
def test_sql_del_peso_coincide_con_python(sesion, posicion, rep):
    from sqlalchemy import bindparam, String, Float, text

    from analysis import pagerank as prk

    sql = prk.sql_peso_arista("CAST(:rep AS float8)", "CAST(:pos AS text)")
    valor = sesion.execute(
        text(f"SELECT {sql}").bindparams(
            bindparam("rep", type_=Float), bindparam("pos", type_=String)),
        {"rep": rep, "pos": posicion},
    ).scalar()
    assert float(valor) == pytest.approx(prk.peso_arista(posicion, rep))


def test_repetir_el_mismo_enlace_no_cambia_nada(sesion, job):
    """El mismo enlace N veces en una pagina pesa lo que pesa una vez.

    `links` guarda una fila por INSTANCIA, y de ahi sale que `pr_lk_tmp` se
    pueda deduplicar antes de los dos pasos caros (#52): la repeticion cuenta
    origenes DISTINTOS y el peso de la arista es el MAXIMO por (origen,
    destino), asi que las copias no aportan. Medido en Druni, deduplicar pasa
    de 115.821.074 filas a 34.344.051.

    Este test no falla contra el codigo anterior —tambien era invariante— y por
    eso esta: fija la propiedad de la que depende la optimizacion. El dia que
    alguien cambie ese MAX por una suma, o la repeticion por un COUNT(*), el
    DISTINCT dejaria de ser gratis y tiene que cantarlo este test y no un censo
    con las cifras movidas.
    """
    from analysis.analyzer import SEOAnalyzer

    j, filas = job
    SEOAnalyzer(sesion, str(j.id)).compute_pagerank()
    sesion.commit()
    antes = dict(sesion.execute(
        select(Url.url, Url.pagerank_raw).where(Url.job_id == j.id)).all())

    # Cada enlace del censo, cuatro veces mas, identico en todo.
    originales = sesion.execute(select(Link).where(Link.job_id == j.id)).scalars().all()
    for enlace in list(originales):
        for _ in range(4):
            sesion.add(Link(
                job_id=enlace.job_id, from_url_id=enlace.from_url_id,
                to_url=enlace.to_url, to_url_hash=enlace.to_url_hash,
                anchor_text=enlace.anchor_text, is_internal=enlace.is_internal,
                follow=enlace.follow, link_position=enlace.link_position,
            ))
    sesion.commit()
    assert sesion.execute(
        select(Url.id).where(Url.job_id == j.id)).scalars().all()  # sanidad

    SEOAnalyzer(sesion, str(j.id)).compute_pagerank()
    sesion.commit()
    despues = dict(sesion.execute(
        select(Url.url, Url.pagerank_raw).where(Url.job_id == j.id)).all())

    assert set(antes) == set(despues)
    for url, valor in antes.items():
        if valor is None:
            assert despues[url] is None, url
            continue
        assert abs(despues[url] - valor) < 1e-12, url


# -- Grafos a medida -----------------------------------------------------------


@pytest.fixture()
def grafo(sesion):
    """Fabrica de jobs pequenos: `grafo(paginas, enlaces)`.

    `paginas` es una lista de rutas (todas 200, HTML, indexables) y `enlaces`
    tuplas (desde, hacia, ancla, posicion, follow). Borra lo que crea.
    """
    creados = []

    def crear(paginas, enlaces):
        j = Job(id=uuid.uuid4(), name="test pagerank a medida",
                status="analyzing", seeds=[BASE + "/"], config={})
        sesion.add(j)
        sesion.flush()
        filas = {}
        for ruta in paginas:
            u = Url(job_id=j.id, url=BASE + ruta, url_hash=_h(BASE + ruta),
                    is_internal=True, status_code=200, is_html=True,
                    indexable=True, noindex=False, crawl_depth=1)
            sesion.add(u)
            filas[ruta] = u
        sesion.flush()
        for desde, hacia, ancla, pos, follow in enlaces:
            sesion.add(Link(job_id=j.id, from_url_id=filas[desde].id,
                            to_url=BASE + hacia, to_url_hash=_h(BASE + hacia),
                            anchor_text=ancla, is_internal=True, follow=follow,
                            link_position=pos))
        sesion.commit()
        creados.append(j.id)
        return j, filas

    yield crear
    sesion.rollback()
    for jid in creados:
        sesion.delete(sesion.get(Job, jid))
    sesion.commit()


def _pagerank_de_referencia(nodos, aristas, damping=0.85, iteraciones=500):
    """PageRank ponderado en Python puro, sin numpy ni `analysis.pagerank`.

    `aristas` es {(origen, destino): peso}. Teletransporte uniforme sobre
    `nodos` (aqui todos son indexables) y la masa de los nodos sin salida va
    al mismo sitio, que es el modelo que documenta la decision 29.
    """
    n = len(nodos)
    saliente = {a: 0.0 for a in nodos}
    for (a, _b), peso in aristas.items():
        saliente[a] += peso
    pr = {a: 1.0 / n for a in nodos}
    for _ in range(iteraciones):
        colgante = sum(pr[a] for a in nodos if saliente[a] == 0)
        nuevo = {a: ((1 - damping) + damping * colgante) / n for a in nodos}
        for (a, b), peso in aristas.items():
            nuevo[b] += damping * pr[a] * peso / saliente[a]
        pr = nuevo
    return pr


def test_un_nofollow_no_entra_en_el_grafo_ni_en_el_denominador(sesion, grafo):
    """Decision 60: los `follow` de una pagina se reparten el 100%.

    La home enlaza a /b y /c (follow) y a /d (nofollow). Si el nofollow
    contara en el denominador, /b y /c recibirian un tercio cada una de lo que
    reparte la home; con el modelo actual, la mitad. /e solo tiene un enlace,
    nofollow, asi que no es fuente: queda colgante y su masa va al
    teletransporte, no a /b.

    Grafo de menos de MIN_FUENTES_REPETICION paginas: el peso es el de la
    posicion (todo `content`, 1,0), asi que el resultado se puede calcular a
    mano.
    """
    from analysis.analyzer import SEOAnalyzer

    paginas = ["/", "/b", "/c", "/d", "/e"]
    j, _ = grafo(paginas, [
        ("/", "/b", "B", "content", True),
        ("/", "/c", "C", "content", True),
        ("/", "/d", "D", "content", False),   # nofollow
        ("/b", "/", "Inicio", "content", True),
        ("/c", "/", "Inicio", "content", True),
        ("/d", "/", "Inicio", "content", True),
        ("/e", "/b", "B", "content", False),  # el unico enlace de /e, nofollow
    ])
    SEOAnalyzer(sesion, str(j.id)).compute_pagerank()
    sesion.commit()

    resumen = sesion.get(Job, j.id).pagerank_resumen
    assert resumen["peso"] == "posicion"
    # 5 enlaces follow; ni / -> /d ni /e -> /b son aristas
    assert resumen["aristas"] == 5

    pr = {u[len(BASE):] or "/": v for u, v in sesion.execute(
        select(Url.url, Url.pagerank_raw).where(Url.job_id == j.id)).all()}

    esperado = _pagerank_de_referencia(paginas, {
        ("/", "/b"): 1.0, ("/", "/c"): 1.0,
        ("/b", "/"): 1.0, ("/c", "/"): 1.0, ("/d", "/"): 1.0,
    })
    # A mano: /d no recibe nada y /e tampoco, y /e es colgante, asi que los
    # dos valen lo que trae el teletransporte, t = (0,15 + 0,85 t) / 5, o sea
    # t = 0,15 / 4,15. La home: h = t + 0,85 (2b + t), con b = t + 0,425 h.
    a_mano = {"/": 0.4623901, "/b": 0.2326604, "/c": 0.2326604,
              "/d": 0.0361446, "/e": 0.0361446}
    for ruta in paginas:
        assert esperado[ruta] == pytest.approx(a_mano[ruta], abs=1e-6), ruta
        assert pr[ruta] == pytest.approx(a_mano[ruta], abs=1e-5), ruta

    # Lo que distingue al modelo: con el nofollow en el denominador (el
    # criterio de Google desde 2009, descartado en la decision 60) /b y /c
    # recibirian 1/3 de la home y no 1/2. Eso no es lo que sale.
    diluido = _pagerank_de_referencia(paginas, {
        ("/", "/b"): 1.0, ("/", "/c"): 1.0, ("/", "/d"): 1.0,
        ("/b", "/"): 1.0, ("/c", "/"): 1.0, ("/d", "/"): 1.0,
    })
    assert pr["/b"] > diluido["/b"] + 0.02


def test_peso_por_repeticion_medida_en_sql(sesion, grafo):
    """Decision 29: el peso de una arista con su repeticion medida, en SQL.

    45 paginas de origen en tres secciones: /blog (20), /tienda (20) y /faq
    (5). Cada enlace (destino + ancla) se repite en una fraccion distinta, y
    el peso esperado es min(posicion, max(0,1 / repeticion, 0,25)), con la
    repeticion la mayor entre la del sitio y la de su seccion —esta solo si la
    seccion tiene 10 paginas o mas—:

    | enlace                   | sitio  | seccion       | peso             |
    |--------------------------|--------|---------------|------------------|
    | b0 -> /servicio          | 1/45   | 1/20          | 1,0 (editorial)  |
    | b19 -> /contacto "Escr." | 1/45   | 1/20          | 1,0 (otra ancla) |
    | 8 -> /contacto "Contacto"| 8/45   | 4/20 = 0,2    | 0,1/0,2 = 0,5    |
    | 12 tienda -> /oferta     | 12/45  | 12/20 = 0,6   | 0,25 (suelo)     |
    | 5 faq -> /ayuda          | 5/45   | (5 < 10: no)  | 0,1/(5/45) = 0,9 |
    | 45 -> /legal, footer     | 45/45  | 1             | 0,15 (posicion)  |

    Ademas, 9 paginas del blog enlazan "Contacto" con nofollow: si contaran en
    la repeticion, la seccion daria 13/20 y el peso caeria a 0,25.
    """
    from analysis.analyzer import SEOAnalyzer

    blog = [f"/blog/b{i}" for i in range(20)]
    tienda = [f"/tienda/t{i}" for i in range(20)]
    faq = [f"/faq/f{i}" for i in range(5)]
    destinos = ["/servicio", "/contacto", "/oferta", "/ayuda", "/legal"]

    enlaces = [(p, "/legal", "Aviso legal", "footer", True)
               for p in blog + tienda + faq]
    enlaces.append(("/blog/b0", "/servicio", "Servicio", "content", True))
    enlaces.append(("/blog/b19", "/contacto", "Escribenos", "content", True))
    contacto = blog[:4] + tienda[12:16]
    for p in contacto:
        # Un ancla con otra caja y espacios: cuenta como la misma
        ancla = "  CONTACTO " if p == "/tienda/t15" else "Contacto"
        enlaces.append((p, "/contacto", ancla, "content", True))
    for p in blog[10:19]:
        enlaces.append((p, "/contacto", "Contacto", "content", False))
    oferta = tienda[:12]
    for p in oferta:
        enlaces.append((p, "/oferta", "Oferta", "content", True))
    for p in faq:
        enlaces.append((p, "/ayuda", "Ayuda", "content", True))

    j, filas = grafo(blog + tienda + faq + destinos, enlaces)
    analizador = SEOAnalyzer(sesion, str(j.id))
    try:
        assert analizador._aristas_de_enlaces() == "repeticion"
        peso = {
            (a, b): w for a, b, w in sesion.execute(
                text("SELECT src, dst, w FROM pr_edges_tmp")).all()
        }
    finally:
        sesion.execute(text("DROP TABLE IF EXISTS pr_edges_tmp"))
        sesion.commit()

    def w(desde, hacia):
        return peso[(filas[desde].id, filas[hacia].id)]

    # Una arista por (origen, destino) follow, y ninguna de los nofollow
    assert len(peso) == 45 + 1 + 1 + 8 + 12 + 5
    for p in blog[10:19]:
        assert (filas[p].id, filas["/contacto"].id) not in peso, p

    assert w("/blog/b0", "/servicio") == pytest.approx(1.0)
    assert w("/blog/b19", "/contacto") == pytest.approx(1.0)
    for p in contacto:
        assert w(p, "/contacto") == pytest.approx(0.5), p
    for p in oferta:
        # Por el sitio solo seria 0,1 / (12/45) = 0,375: manda la seccion
        assert w(p, "/oferta") == pytest.approx(0.25), p
    for p in faq:
        # La seccion tiene 5 paginas, por debajo de MIN_FUENTES_SECCION: si
        # contara, 5/5 daria 0,25
        assert w(p, "/ayuda") == pytest.approx(0.9, rel=1e-6), p
    for p in blog + tienda + faq:
        assert w(p, "/legal") == pytest.approx(0.15), p

    # La consecuencia en el reparto: de b0, /servicio se lleva 1 / 1,15 de lo
    # que reparte la pagina; de t0, /oferta 0,25 / 0,40.
    total_b0 = w("/blog/b0", "/servicio") + w("/blog/b0", "/legal")
    assert w("/blog/b0", "/servicio") / total_b0 == pytest.approx(1 / 1.15)
    total_t0 = w("/tienda/t0", "/oferta") + w("/tienda/t0", "/legal")
    assert w("/tienda/t0", "/oferta") / total_t0 == pytest.approx(0.25 / 0.40)
