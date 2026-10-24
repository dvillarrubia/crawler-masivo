"""`data-nosnippet` (Ola 7.2, decision 170): lo que esconde al fragmento.

Tres niveles, ejecutando el codigo (decision 74):

- `shared.data_nosnippet.medir` contra HTML hecho a mano: solo `span`, `div` y
  `section` (lo que dice Google), booleano con cualquier valor, sin caja, el
  anidado cuenta una vez, lo oculto no cubre nada, el H1 y el contenido
  principal por lineas (un pie largo marcado no «cubre» el articulo);
- `SeoSpider.parse` con respuestas a mano: el `HtmlMetaItem` lleva la medida,
  con el contenido principal real, y un fallo al medir deja NULL sin llevarse
  la fila;
- el analisis contra SQLite: agrupado por plantilla, solo indexables 2xx,
  nada con `nosnippet` de pagina ni en censos sin medir, y la ficha, el nombre
  y el ambito de cada tipo en el frontend.
"""

from __future__ import annotations

import os
import re
import uuid

import pytest
from lxml import html as lxml_html

from shared.data_nosnippet import esconde_lo_principal, medir

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ARTICULO = ("La historia del faro de Santander empieza en 1839 con una torre de piedra.\n"
            "Durante la guerra se apago dos veces y la reconstruyeron los vecinos del barrio.\n"
            "Hoy es un museo abierto todo el ano con visitas guiadas por la manana.")


def _body(cuerpo: str):
    return lxml_html.fromstring(f"<html><head><title>t</title></head><body>{cuerpo}</body></html>"
                                ).find("body")


def _parrafos(texto: str) -> str:
    return "".join(f"<p>{x}</p>" for x in texto.splitlines())


# -- La funcion pura ---------------------------------------------------------

def test_sin_atributo_es_medido_y_cero():
    assert medir(_body("<h1>Hola</h1><p>Texto</p>"), "Texto") == {"elementos": 0, "ignorados": 0}


def test_sin_body_no_rompe():
    assert medir(None) == {"elementos": 0, "ignorados": 0}


@pytest.mark.parametrize("etiqueta", ["span", "div", "section"])
def test_los_tres_elementos_validos(etiqueta):
    m = medir(_body(f"<p>Antes</p><{etiqueta} data-nosnippet>precio especial hoy</{etiqueta}>"))
    assert m["elementos"] == 1
    assert m["palabras"] == 3
    assert m["ignorados"] == 0


@pytest.mark.parametrize("etiqueta", ["p", "article", "li", "main", "h2", "td"])
def test_en_otro_elemento_google_lo_ignora(etiqueta):
    contenedor = {"li": "ul", "td": "table><tr"}.get(etiqueta)
    cuerpo = f"<{etiqueta} data-nosnippet>texto que sigue saliendo</{etiqueta}>"
    if contenedor:
        cuerpo = f"<{contenedor}>{cuerpo}</{contenedor.split('>')[0]}>"
    m = medir(_body(cuerpo))
    assert m["elementos"] == 0
    assert m["ignorados"] == 1
    assert m["etiquetas_ignoradas"] == [etiqueta]


@pytest.mark.parametrize("atributo", ['data-nosnippet', 'data-nosnippet=""',
                                      'data-nosnippet="false"', 'DATA-NOSNIPPET',
                                      'Data-NoSnippet="true"'])
def test_booleano_y_sin_caja(atributo):
    assert medir(_body(f"<div {atributo}>uno dos</div>"))["elementos"] == 1


def test_anidados_cuentan_una_vez():
    m = medir(_body("<div data-nosnippet>uno <span data-nosnippet>dos tres</span> cuatro</div>"))
    assert m["elementos"] == 1
    assert m["palabras"] == 4


def test_lo_oculto_no_cubre_nada():
    m = medir(_body('<div data-nosnippet hidden>secreto</div>'
                    '<div style="display:none"><span data-nosnippet>tambien</span></div>'))
    assert m["elementos"] == 0


def test_h1_dentro_de_un_contenedor_marcado():
    m = medir(_body("<div data-nosnippet><h1>Faro de Santander</h1></div><p>Resto</p>"))
    assert m["h1"] is True


def test_h1_con_la_mayor_parte_en_un_span_marcado():
    assert medir(_body('<h1><span data-nosnippet>Faro de</span> Santander</h1>'))["h1"] is True
    assert medir(_body('<h1>Faro de <span data-nosnippet>Santander</span> hoy</h1>'))["h1"] is False


def test_h1_es_el_primero_que_se_pinta():
    m = medir(_body('<h1 hidden>Copia movil</h1><h1>Titulo</h1>'
                    '<div data-nosnippet>otra cosa</div>'))
    assert m["h1"] is False
    m = medir(_body('<div data-nosnippet>x</div>'))
    assert m["h1"] is None  # sin h1 no se afirma


def test_contenido_principal_cubierto():
    m = medir(_body(f"<h1>Faro</h1><div data-nosnippet>{_parrafos(ARTICULO)}</div>"), ARTICULO)
    assert m["principal"] == 1.0
    assert esconde_lo_principal(m) == "contenido"


def test_una_parte_del_contenido():
    lineas = ARTICULO.splitlines()
    cuerpo = (f"<p>{lineas[0]}</p><p>{lineas[1]}</p>"
              f"<section data-nosnippet><p>{lineas[2]}</p></section>")
    m = medir(_body(cuerpo), ARTICULO)
    assert 0.2 < m["principal"] < 0.5
    assert esconde_lo_principal(m) is None


def test_un_pie_largo_marcado_no_cubre_el_articulo():
    """Con palabras sueltas, las comunes («de», «la», «el») del pie casaban con
    el articulo; por lineas no."""
    pie = " ".join(["de la el en con por para los las del y que un una"] * 40)
    m = medir(_body(f"<h1>Faro</h1>{_parrafos(ARTICULO)}"
                    f"<footer><div data-nosnippet>{pie}</div></footer>"), ARTICULO)
    assert m["palabras"] > 300
    assert m["principal"] == 0.0
    assert esconde_lo_principal(m) is None


def test_una_linea_corta_solo_casa_entera():
    principal = "Inicio\n" + ARTICULO
    m = medir(_body(f"<p>Inicio</p>{_parrafos(ARTICULO)}"
                    "<div data-nosnippet>Volver al Inicio de la web</div>"), principal)
    assert m["principal"] == 0.0


def test_sin_contenido_principal_no_se_afirma():
    m = medir(_body("<div data-nosnippet><p>todo</p></div>"), None)
    assert m["principal"] is None


@pytest.mark.parametrize("datos, motivo", [
    (None, None),
    ({"elementos": 0, "ignorados": 2}, None),
    ({"elementos": 1, "h1": True, "principal": 0.1}, "h1"),
    ({"elementos": 1, "h1": False, "principal": 0.5}, "contenido"),
    ({"elementos": 2, "h1": True, "principal": 0.9}, "h1_y_contenido"),
    ({"elementos": 1, "h1": None, "principal": None}, None),
])
def test_esconde_lo_principal(datos, motivo):
    assert esconde_lo_principal(datos) == motivo


# -- El spider ----------------------------------------------------------------

@pytest.fixture
def spider():
    pytest.importorskip("scrapy")
    import shared.database

    shared.database.SessionLocal = lambda: None
    from seo_crawler.spiders.seo_spider import SeoSpider

    sp = SeoSpider(job_id="t")
    sp._allowed_resource_types = {"html", "redirect"}
    sp._crawled_count = 0
    sp.allowed_hosts = {"x.com"}
    sp._crawl_subdomains = False
    sp.max_urls = None
    sp._extraction = {"extract_page_content": True}
    return sp


def _meta(spider, cuerpo: str):
    from scrapy.http import HtmlResponse, Request

    respuesta = HtmlResponse(
        "https://x.com/p", status=200,
        headers={b"Content-Type": b"text/html; charset=utf-8"},
        body=cuerpo.encode(), request=Request("https://x.com/p", meta={"depth": 0}),
    )
    return next(i for i in spider.parse(respuesta) if type(i).__name__ == "HtmlMetaItem")


PAGINA = ("<html><head><title>Faro</title></head><body><nav><a href='/'>Inicio</a></nav>"
          "<main><div data-nosnippet><h1>El faro de Santander</h1>"
          + _parrafos(ARTICULO * 3) +
          "</div><p data-nosnippet>nota</p></main><footer>Pie</footer></body></html>")


def test_el_spider_guarda_la_medida(spider):
    m = _meta(spider, PAGINA)
    d = m["data_nosnippet"]
    assert d["elementos"] == 1
    assert d["h1"] is True
    assert d["principal"] is not None and d["principal"] >= 0.9
    assert d["ignorados"] == 1 and d["etiquetas_ignoradas"] == ["p"]


def test_sin_atributo_el_spider_guarda_cero(spider):
    m = _meta(spider, "<html><body><h1>Hola</h1><p>texto</p></body></html>")
    assert m["data_nosnippet"] == {"elementos": 0, "ignorados": 0}


def test_un_fallo_al_medir_deja_null_y_la_fila(spider, monkeypatch):
    import shared.data_nosnippet as dn

    def roto(*a, **k):
        raise RuntimeError("roto")

    monkeypatch.setattr(dn, "medir", roto)
    m = _meta(spider, PAGINA)
    assert m["data_nosnippet"] is None
    assert m["title"] == "Faro"


# -- El analisis --------------------------------------------------------------

def test_el_analisis_agrupa_por_plantilla_y_solo_en_indexables():
    from sqlalchemy import BigInteger, create_engine, select
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    from analysis import avisos_de_nosnippet
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Base, HtmlMeta, Issue, Job, Url

    @compiles(BigInteger, "sqlite")
    def _bigint_sqlite(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Job.__table__, Url.__table__, HtmlMeta.__table__,
                                             Issue.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"], config={})
    s.add(j)
    s.flush()

    def pagina(ruta, datos, indexable=True, status=200, robots=None):
        u = Url(job_id=j.id, url=f"https://x.com{ruta}", url_hash=ruta, host="x.com",
                is_internal=True, is_html=True, status_code=status,
                status_group=f"{status // 100}xx", crawl_depth=1, indexable=indexable)
        s.add(u)
        s.flush()
        s.add(HtmlMeta(url_id=u.id, data_nosnippet=datos, meta_robots=robots))
        return u

    tapado = {"elementos": 1, "ignorados": 0, "palabras": 500, "h1": True, "principal": 0.95}
    b = pagina("/blog/b", tapado)
    a = pagina("/blog/a", dict(tapado, principal=0.8))
    pagina("/blog/c", {"elementos": 1, "ignorados": 0, "palabras": 4, "h1": False,
                       "principal": 0.02})                                 # un precio: bien
    pagina("/blog/d", tapado, indexable=False)                             # noindex: no
    pagina("/blog/e", tapado, status=404)
    pagina("/blog/f", tapado, robots="nosnippet")                          # ya avisa 137
    pagina("/blog/g", None)                                                # no medido
    pagina("/blog/h", {"elementos": 0, "ignorados": 0})
    solo_h1 = pagina("/ficha/1", {"elementos": 1, "ignorados": 0, "palabras": 3, "h1": True,
                                  "principal": 0.1})
    ign = pagina("/legal/x", {"elementos": 0, "ignorados": 2, "etiquetas_ignoradas": ["p", "li"]})
    pagina("/legal/y", {"elementos": 0, "ignorados": 1, "etiquetas_ignoradas": ["article"]})
    s.commit()

    az = SEOAnalyzer(s, j.id)
    avisos_de_nosnippet.avisar(az)
    s.commit()
    filas = s.execute(select(Issue.url_id, Issue.issue_type, Issue.severity, Issue.details)
                      .where(Issue.job_id == j.id)).all()
    por_tipo: dict = {}
    for u, t, sev, d in filas:
        por_tipo.setdefault(t, []).append((u, sev, d))

    principal = sorted(por_tipo["data_nosnippet_contenido_principal"],
                       key=lambda x: x[2]["plantilla"])
    assert len(principal) == 2
    blog = next(p for p in principal if p[2]["motivo"] == "h1_y_contenido")
    assert blog[0] == a.id and blog[0] != b.id      # anclada en la URL menor
    assert blog[1] == "warning"
    assert blog[2]["paginas_afectadas"] == 2
    assert blog[2]["paginas_ejemplo"] == ["https://x.com/blog/a", "https://x.com/blog/b"]
    assert blog[2]["fraccion_contenido_mediana"] == 0.95
    ficha = next(p for p in principal if p[2]["motivo"] == "h1")
    assert ficha[0] == solo_h1.id

    (ignorado,) = por_tipo["data_nosnippet_ignorado"]
    assert ignorado[0] == ign.id and ignorado[1] == "info"
    assert ignorado[2]["paginas_afectadas"] == 2
    assert ignorado[2]["etiquetas"] == ["article", "li", "p"]
    assert set(por_tipo) == {"data_nosnippet_contenido_principal", "data_nosnippet_ignorado"}


# -- La interfaz --------------------------------------------------------------

def _leer(*ruta: str) -> str:
    with open(os.path.join(RAIZ, *ruta), encoding="utf-8") as f:
        return f.read()


def test_cada_tipo_tiene_ficha_nombre_y_ambito():
    import ast

    from analysis.avisos_de_nosnippet import TIPOS

    arbol = ast.parse(_leer("analysis", "avisos_de_nosnippet.py"))
    emitidos = {n.args[1].value for n in ast.walk(arbol)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("_add_issue", "emitir") and len(n.args) > 1
                and isinstance(n.args[1], ast.Constant)}
    assert emitidos == set(TIPOS)
    inc = _leer("frontend", "incidencias.js")
    fichas = inc[inc.index("const ISSUE_INFO = {"):]
    fichas = fichas[:fichas.index("\n};")]
    nombres = inc[inc.index("const ISSUE_LABEL_EXTRA = {"):]
    nombres = nombres[:nombres.index("\n};")]
    ambitos = " ".join(l for l in _leer("frontend", "app.js").splitlines() if "ambito: [" in l)
    for t in TIPOS:
        assert f"'{t}': {{" in fichas, t
        assert re.search(rf"\b{t}\s*:", nombres), t
        assert f"'{t}'" in ambitos, t


# -- Con la tuberia real y Postgres: llega a la BD y el filtro JSON funciona ------

def test_tuberia_y_analisis_en_postgres():
    """El spider con el arnes escribe `html_meta.data_nosnippet` en Postgres, y
    el aviso sale con el filtro JSON (`->> 'elementos'`) de Postgres, que SQLite
    no prueba."""
    import json
    import socket
    import subprocess
    import sys

    url_bd = os.environ.get("PAGERANK_TEST_DATABASE_URL")
    if not url_bd:
        pytest.skip("sin PAGERANK_TEST_DATABASE_URL")
    pytest.importorskip("scrapy")
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.orm import sessionmaker

    from analysis import avisos_de_nosnippet
    from analysis.analyzer import SEOAnalyzer
    from shared.models import Base, HtmlMeta, Issue, Job, Url

    articulo = _parrafos(ARTICULO * 2)
    sitio = {
        "/home": [200, {}, "<html><head><title>Home</title></head><body><h1>Inicio del faro</h1>"
                  "<a href='/blog/uno'>1</a> <a href='/blog/dos'>2</a> <a href='/limpia'>3</a>"
                  f"{articulo}</body></html>"],
        "/blog/uno": [200, {}, "<html><head><title>Uno</title></head><body><main>"
                      f"<div data-nosnippet><h1>Entrada uno</h1>{articulo}</div></main></body></html>"],
        "/blog/dos": [200, {}, "<html><head><title>Dos</title></head><body><main>"
                      f"<div data-nosnippet><h1>Entrada dos</h1>{articulo}</div></main></body></html>"],
        "/limpia": [200, {}, "<html><head><title>Limpia</title></head><body><h1>Precio</h1>"
                    f"{articulo}<span data-nosnippet>99 euros</span></body></html>"],
    }
    engine = create_engine(url_bd)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="test data-nosnippet", status="running", seeds=[], config={}))
        s.commit()
    try:
        with socket.socket() as so:
            so.bind(("127.0.0.1", 0))
            puerto = so.getsockname()[1]
        entrada = {"seeds": ["http://127.0.0.1:{port}/home"],
                   "config": {"max_depth": 2, "use_sitemap": False}, "rutas": sitio,
                   "bd": url_bd, "job_id": str(jid), "puerto": puerto}
        harness = os.path.join(RAIZ, "tests", "spider_harness.py")
        proc = subprocess.run([sys.executable, harness, json.dumps(entrada)],
                              capture_output=True, text=True, timeout=120)
        assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
        with Sesion() as s:
            filas = {u.path: m.data_nosnippet for u, m in s.query(Url, HtmlMeta)
                     .join(HtmlMeta, HtmlMeta.url_id == Url.id).filter(Url.job_id == jid)}
            assert filas["/home"] == {"elementos": 0, "ignorados": 0}
            assert filas["/blog/uno"]["h1"] is True and filas["/blog/uno"]["principal"] >= 0.9
            assert filas["/limpia"]["elementos"] == 1 and filas["/limpia"]["h1"] is False
            az = SEOAnalyzer(s, jid)
            az.analyze_indexability()
            avisos_de_nosnippet.avisar(az)
            s.commit()
            avisos = s.execute(select(Issue.issue_type, Issue.details)
                               .where(Issue.job_id == jid,
                                      Issue.issue_type.like("data_nosnippet%"))).all()
        assert len(avisos) == 1
        tipo, d = avisos[0]
        assert tipo == "data_nosnippet_contenido_principal"
        assert d["paginas_afectadas"] == 2 and d["motivo"] == "h1_y_contenido"
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM html_meta WHERE url_id IN "
                           "(SELECT id FROM urls WHERE job_id = :j)"), {"j": str(jid)})
            # headings y page_content caen en cascada con urls.
            for tabla in ("links", "issues", "urls", "jobs"):
                col = "id" if tabla == "jobs" else "job_id"
                c.execute(text(f"DELETE FROM {tabla} WHERE {col} = :j"), {"j": str(jid)})
        engine.dispose()
