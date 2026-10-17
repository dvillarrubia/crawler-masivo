"""Paridad movil/escritorio por muestra de plantilla (decision 119).

Con el SeoSpider real y la descarga de produccion (curl_cffi y, si arranca el
Chromium instalado, Playwright) contra un sitio local que sirve OTRO HTML a un
UA de movil: `noindex` y canonical solo en movil, el menu que falta, menos
texto, una redireccion a otro host (el m-dot, en `127.0.0.1`), un titulo que
cambia en cada peticion (volatil) y una plantilla con mas paginas que su cupo.
Se mira lo que emite el spider y lo que le llega al servidor.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import uuid

import pytest

pytest.importorskip("scrapy")
pytest.importorskip("scrapy_impersonate")

from test_credenciales_y_cookies import _chromium_disponible  # noqa: E402

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SITIO = "http://localhost:{port}"
PROSA = " ".join(f"palabra{i}" for i in range(120))


def _pagina(titulo="T", h1="H", desc="D", canonical=None, robots=None, nav="",
            texto=PROSA, head="", script=""):
    partes = [f"<title>{titulo}</title>", f'<meta name="description" content="{desc}">']
    if canonical:
        partes.append(f'<link rel="canonical" href="{canonical}">')
    if robots:
        partes.append(f'<meta name="robots" content="{robots}">')
    return (f"<html><head>{''.join(partes)}{head}</head><body><nav>{nav}</nav>"
            f"<main><h1>{h1}</h1><article><p>{texto}</p></article></main>{script}"
            f"</body></html>")


MENU = "".join(f'<a href="/seccion-{i}">Sección {i}</a>' for i in range(12))

RUTAS = {
    "/p/home": [200, {}, _pagina(nav=MENU, texto=PROSA + "".join(
        f' <a href="{r}">{r}</a>' for r in (
            "/ficha/n-1", "/ficha/n-2", "/cat/c-1", "/menu/m-1", "/texto/t-1",
            "/mdot/x-1", "/vol/v-1", "/lista/l-1", "/lista/l-2", "/lista/l-3",
            "/lista/l-4", "/igual/i-1", "/js/j-1")))],
    "/ficha/n-1": [200, {}, _pagina(titulo="Ficha 1")],
    "/ficha/n-2": [200, {}, _pagina(titulo="Ficha 2")],
    "/cat/c-1": [200, {}, _pagina(canonical=SITIO + "/cat/c-1")],
    "/menu/m-1": [200, {"Vary": "Accept-Encoding"}, _pagina(nav=MENU)],
    "/texto/t-1": [200, {"Vary": "User-Agent"}, _pagina()],
    "/mdot/x-1": [200, {}, _pagina(head='<link rel="alternate" media="only screen and '
                                        '(max-width: 640px)" href="http://127.0.0.1:{port}/m/x-1">')],
    "/m/x-1": [200, {}, _pagina(canonical=SITIO + "/mdot/x-1")],
    "/vol/v-1": [200, {}, _pagina(titulo="Oferta {aleatorio}")],
    "/igual/i-1": [200, {}, _pagina()],
    "/js/j-1": [200, {}, _pagina(h1="sin js", script=(
        "<script>document.querySelector('h1').textContent = "
        "(/Mobile/.test(navigator.userAgent) && screen.width < 500 && navigator.maxTouchPoints > 0) ? 'movil' : 'escritorio';"
        "</script>"))],
    **{f"/lista/l-{i}": [200, {}, _pagina(titulo=f"Lista {i}")] for i in range(1, 5)},
}
RUTAS_MOVIL = {
    "/ficha/n-1": [200, {}, _pagina(titulo="Ficha 1", robots="noindex, follow")],
    "/ficha/n-2": [200, {}, _pagina(titulo="Ficha 2", robots="noindex")],
    "/cat/c-1": [200, {}, _pagina(canonical="/cat")],
    "/menu/m-1": [200, {"Vary": "Accept-Encoding"}, _pagina(nav="")],
    "/texto/t-1": [200, {"Vary": "User-Agent"}, _pagina(texto=" ".join(PROSA.split()[:30]))],
    "/mdot/x-1": [302, {"Location": "http://127.0.0.1:{port}/m/x-1"}, ""],
}


def _rastrear(render: bool = False, chromium: str | None = None,
              config: dict | None = None, extra: dict | None = None, **cb) -> dict:
    crawl_behavior = {"paridad_movil_muestra": 50, "paridad_movil_por_plantilla": 2,
                      "soft404_sondas": 0, **cb}
    entrada = {
        "seeds": ["http://localhost:{port}/p/home"],
        "config": {"max_depth": 2, "use_sitemap": False, "render_js": render,
                   "crawl_behavior": crawl_behavior, **(config or {})},
        "rutas": RUTAS, "rutas_movil": RUTAS_MOVIL,
        "descarga_real": True, "ua_real": True, "ver_cabeceras": True,
        **(extra or {}),
    }
    if chromium:
        entrada["chromium"] = chromium
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=300, cwd=RAIZ)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout[-3000:] + proc.stderr[-6000:]
    cabeceras = json.loads(proc.stdout.split("@@CABECERAS@@", 1)[1].split("\n", 1)[0])
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    paginas = [i for i in items if i["_tipo"] == "PageItem"]
    por_hash = {i["url_hash"]: i["url"] for i in paginas}
    capturas = {}
    for i in items:
        if i["_tipo"] == "CapturaAlternativaItem":
            ruta = por_hash[i["url_hash"]].split("SEMILLA", 1)[-1]
            assert ruta not in capturas, f"dos capturas de {ruta}"
            capturas[ruta] = i
    return {"paginas": paginas, "capturas": capturas, "cabeceras": cabeceras}


def _campos(captura) -> set[str]:
    return {d["campo"] for d in captura["diferencias"] or []}


def _comprobar(r: dict, render: bool) -> None:
    c = r["capturas"]
    modo = "render" if render else "sin_render"
    assert all(x["modo"] == modo and x["perfil"] == "movil" for x in c.values()), c
    assert all("Mobile" in x["user_agent"] for x in c.values())

    # noindex solo en movil, en las dos fichas de la plantilla
    for ruta in ("/ficha/n-1", "/ficha/n-2"):
        assert c[ruta]["estado"] == "comparada"
        assert _campos(c[ruta]) == {"noindex"}, c[ruta]
    # canonical relativo a otra URL; el de escritorio era absoluto a si misma
    d = {x["campo"]: x for x in c["/cat/c-1"]["diferencias"]}
    assert set(d) == {"canonical"}, c["/cat/c-1"]
    assert d["canonical"]["alternativa"].endswith("/cat")
    # el menu que falta en movil: enlaces, con ejemplos de los que faltan
    d = {x["campo"]: x for x in c["/menu/m-1"]["diferencias"]}
    assert set(d) == {"enlaces"}, c["/menu/m-1"]
    assert d["enlaces"]["faltan"] == 12 and len(d["enlaces"]["ejemplos"]) == 10
    # menos texto, con Vary: User-Agent en las dos
    assert _campos(c["/texto/t-1"]) == {"palabras"}
    assert c["/texto/t-1"]["detalle"] is None
    # sin diferencias: fila igual, con «no difiere»
    assert c["/igual/i-1"]["estado"] == "comparada" and c["/igual/i-1"]["diferencias"] == []
    # el titulo que cambia en cada peticion es volatil y no se afirma
    assert c["/vol/v-1"]["estado"] == "comparada"
    assert c["/vol/v-1"]["diferencias"] == [] and c["/vol/v-1"]["volatiles"] == ["title"]
    # m-dot: no se sigue como si fuera la misma pagina; se anota y se mira el destino
    m = c["/mdot/x-1"]
    assert m["estado"] == "redirige" and m["redirige_a"].endswith("/m/x-1"), m
    assert m["detalle"]["tipo"] == "otro_host"
    assert m["detalle"]["destino"]["canonical_a_base"] is True
    assert m["detalle"]["destino"]["alternate_al_destino"] is True
    # cupo por plantilla: 2 de las 4 listas
    assert sum(1 for k in c if k.startswith("/lista/")) == 2

    # Ninguna sonda es una URL del censo: una PageItem por URL, y ninguna del m-dot
    urls = [p["url"] for p in r["paginas"]]
    assert len(urls) == len(set(urls)), urls
    assert not any("/m/x-1" in u for u in urls)
    # La de control sale con el UA del censo; la sonda, con el de movil
    vol = [h.get("user-agent", "") for (ho, ruta, h) in r["cabeceras"] if ruta == "/vol/v-1"]
    assert len(vol) == 3, vol  # rastreo, movil y control
    assert sum("Mobile" in ua for ua in vol) == 1


def test_paridad_sin_render():
    r = _rastrear()
    _comprobar(r, render=False)
    # Sin render no hay JavaScript: el h1 del script no cambia
    assert r["capturas"]["/js/j-1"]["diferencias"] == []


def test_con_la_muestra_a_cero_no_se_pide_nada_con_movil():
    r = _rastrear(paridad_movil_muestra=0)
    assert r["capturas"] == {}
    assert not any("Mobile" in (h.get("user-agent") or "") for (_, _, h) in r["cabeceras"])


def test_la_sonda_no_gasta_max_urls():
    """Con un tope, el censo tiene las mismas paginas con y sin paridad."""
    con = _rastrear(config={"max_urls": 6})
    sin = _rastrear(config={"max_urls": 6}, paridad_movil_muestra=0)
    assert con["capturas"], "la paridad no llego a pedir nada"
    assert len(con["paginas"]) == len(sin["paginas"]) == 6


@pytest.fixture(scope="module")
def chromium():
    ruta = _chromium_disponible()
    if ruta is None:
        pytest.skip("sin Chromium para Playwright")
    return ruta


def test_paridad_con_render(chromium):
    r = _rastrear(render=True, chromium=chromium)
    _comprobar(r, render=True)
    # Con render, el contexto del perfil es un movil de verdad: el script ve
    # `navigator.userAgent` de movil, pantalla estrecha y tactil; la base, no.
    # (Con `innerWidth` no: sin `<meta viewport>` un movil maqueta a 980 px.)
    d = {x["campo"]: x for x in r["capturas"]["/js/j-1"]["diferencias"]}
    assert d["h1"]["base"] == "escritorio" and d["h1"]["alternativa"] == "movil", d


URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")


@pytest.fixture()
def bd():
    if not URL_BD:
        pytest.skip("sin PAGERANK_TEST_DATABASE_URL")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from shared.models import Base, Job

    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="test paridad", status="running", seeds=[], config={}))
        s.commit()
    yield Sesion, jid
    with engine.begin() as c:
        for tabla in ("capturas_alternativas", "enlaces_no_rastreables", "links", "issues",
                      "urls", "jobs"):
            col = "id" if tabla == "jobs" else "job_id"
            c.execute(text(f"DELETE FROM {tabla} WHERE {col} = :j"), {"j": str(jid)})
    engine.dispose()


def test_se_guarda_se_analiza_y_reanudar_no_repite(bd):
    """Con la tuberia real contra Postgres: una fila por URL muestreada, el
    analizador agrupa por plantilla, y reanudar el job no vuelve a muestrear."""
    from analysis.analyzer import SEOAnalyzer
    from shared.models import CapturaAlternativa as C, Issue

    Sesion, jid = bd
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        puerto = so.getsockname()[1]
    for _ in range(2):
        _rastrear(extra={"bd": URL_BD, "job_id": str(jid), "puerto": puerto})
    with Sesion() as s:
        filas = s.query(C).filter(C.job_id == jid).all()
        rutas = sorted(f.medidas["url"].split(str(puerto), 1)[1] for f in filas if f.medidas)
        assert len(filas) == len({f.url_id for f in filas}), "una captura por URL"
        assert "/ficha/n-1" in rutas and "/igual/i-1" in rutas
        assert sum(1 for f in filas if f.plantilla == "/lista/:slug (2n)") == 2
        az = SEOAnalyzer(s, jid)
        az.analyze_paridad_movil()
        s.flush()
        avisos = {(i.issue_type, i.details["campo"]): i for i in
                  s.query(Issue).filter(Issue.job_id == jid)}
        noindex = avisos[("paridad_movil_indexacion", "noindex")]
        assert noindex.severity == "error"
        assert noindex.details["paginas_afectadas"] == 2
        assert noindex.details["plantilla"] == "/ficha/:slug (2n)"
        assert ("paridad_movil_indexacion", "canonical") in avisos
        assert ("paridad_movil_contenido", "enlaces") in avisos
        assert ("paridad_movil_contenido", "palabras") in avisos
        assert avisos[("paridad_movil_respuesta", "urls_separadas")].severity == "info"
        assert not any(campo == "title" for (_, campo) in avisos), "el volatil no se afirma"
        s.rollback()
