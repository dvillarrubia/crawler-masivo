"""Extraccion a medida con el SeoSpider REAL contra un sitio local (decision 104).

Dos niveles:

- lo que el spider EMITE (`CustomExtractionItem`), con la BD simulada del
  arnes: corre siempre que haya Scrapy;
- lo que queda GUARDADO, con la tuberia real contra el Postgres de pruebas
  (`PAGERANK_TEST_DATABASE_URL`): una reanudacion en la que el precio
  desaparece, una ficha que pasa a 404 y otra a 301, y que re-rastrear no
  duplica. El frontier de una reanudacion es SQL de Postgres (`DISTINCT ON`),
  por eso no vale SQLite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid

import pytest

pytest.importorskip("scrapy")

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spider_harness.py")
URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")


def _pagina(cuerpo: str, titulo: str = "Tienda", head: str = "") -> list:
    return [200, {}, f"<html><head><title>{titulo}</title>{head}</head><body>{cuerpo}</body></html>"]


GTM = "<script>(function(w,d){w.dataLayer=[]})(window,document,'GTM-AB12CD');</script>"

SITIO = {
    "/home": _pagina('<a href="/p/1">1</a><a href="/p/2">2</a><a href="/p/3">3</a>'
                     '<a href="/doc.pdf">pdf</a><a href="/muro">muro</a>', head=GTM),
    "/p/1": _pagina('<h1>Zapa<span>tillas</span></h1><span class="precio">19,95 €</span>'
                    '<div class="sku" data-sku="SKU-1">SKU-1</div><p class="stock">En stock</p>',
                    head=GTM),
    "/p/2": _pagina('<span class="precio">1.299,00 €</span><div class="sku" data-sku="SKU-2"></div>'
                    '<p class="stock">Sin stock</p>'),
    "/p/3": _pagina('<p>Ficha sin precio</p><span class="precio d-none"></span>', head=GTM),
    "/doc.pdf": [200, {"Content-Type": "application/pdf"}, "%PDF-1.4 x"],
    "/muro": _pagina("<p>GTM-X</p>", titulo="Just a moment..."),
}

CONFIG = {
    "max_depth": 2, "use_sitemap": False,
    "custom_extraction": {
        "precio": {"tipo": "css", "expresion": ".precio", "valor": "numero", "solo_urls": "^/p/"},
        "sku": {"tipo": "css", "expresion": ".sku::attr(data-sku)", "solo_urls": "^/p/"},
        "stock": {"tipo": "xpath", "expresion": "//p[@class='stock']", "solo_urls": "^/p/"},
        "h1": {"tipo": "css", "expresion": "h1"},
    },
    "custom_search": {"gtm": {"patron": "GTM-"}},
}


def _rastrear(sitio=SITIO, config=CONFIG, **extra):
    entrada = {"seeds": ["http://127.0.0.1:{port}/home"], "config": config, "rutas": sitio, **extra}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    return items, proc.stderr


def _por_ruta(items) -> dict[str, dict[str, dict]]:
    """ruta -> {nombre: fila}, cruzando el url_hash del item con su PageItem."""
    rutas = {i["url_hash"]: "/" + i["url"].split("/", 3)[-1]
             for i in items if i["_tipo"] == "PageItem"}
    return {rutas[i["url_hash"]]: {f["nombre"]: f for f in i["filas"]}
            for i in items if i["_tipo"] == "CustomExtractionItem"}


@pytest.fixture(scope="module")
def emitido():
    items, _ = _rastrear()
    return _por_ruta(items), [i for i in items if i["_tipo"] == "PageItem"]


def test_una_ficha_guarda_precio_sku_y_disponibilidad(emitido):
    x, _ = emitido
    p1 = x["/p/1"]
    assert p1["precio"]["estado"] == "ok" and p1["precio"]["valor_num"] == 19.95
    assert p1["sku"]["valor"] == "SKU-1"
    assert p1["stock"]["valor"] == "En stock"
    assert p1["h1"]["valor"] == "Zapatillas"              # Zapa<span>tillas: una palabra
    assert p1["gtm"]["estado"] == "ok" and p1["gtm"]["render"] is False
    assert x["/p/2"]["precio"]["valor_num"] == 1299.0
    assert x["/p/2"]["gtm"]["estado"] == "sin_coincidencia"


def test_sin_precio_es_sin_coincidencia_y_solo_urls_deja_la_home_sin_fila(emitido):
    x, _ = emitido
    assert x["/p/3"]["precio"]["estado"] == "sin_coincidencia"   # el span vacio y oculto
    assert x["/p/3"]["gtm"]["estado"] == "ok"
    assert set(x["/home"]) == {"h1", "gtm"}


def test_un_pdf_no_deja_fila_y_un_muro_sale_bloqueado(emitido):
    x, paginas = emitido
    assert "/doc.pdf" in {"/" + p["url"].split("/", 3)[-1] for p in paginas}
    assert "/doc.pdf" not in x
    assert {f["estado"] for f in x["/muro"].values()} == {"pagina_bloqueada"}


def test_reglas_que_no_compilan_no_rastrean_nada():
    """Y dejan en Redis la marca que el worker convierte en
    `finish_reason=config_invalida`, con el motivo: sin la marca el job
    acabaria `sin_urls` (o `completed`, si es una reanudacion)."""
    malo = {**CONFIG, "custom_extraction": {"r": {"tipo": "regex", "expresion": "(a"}}}
    entrada = {"seeds": ["http://127.0.0.1:{port}/home"], "config": malo, "rutas": SITIO,
               "log_level": "ERROR", "redis_falso": True, "job_id": "jx"}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@ITEMS@@" in proc.stdout, proc.stdout + proc.stderr
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    assert not [i for i in items if i["_tipo"] == "PageItem"]
    assert "Reglas a medida no validas" in proc.stderr
    marcas = json.loads(proc.stdout.split("@@REDIS@@", 1)[1].split("\n", 1)[0])
    from shared.extraccion_a_medida import CLAVE_CONFIG_INVALIDA

    motivo = marcas.get(CLAVE_CONFIG_INVALIDA.format(job_id="jx"))
    assert motivo and "custom_extraction.r" in motivo, marcas


def test_reglas_validas_borran_una_marca_vieja():
    """Una marca de una ejecucion anterior (dependencia arreglada entre medias)
    no puede tumbar la reanudacion que si compila."""
    from shared.extraccion_a_medida import CLAVE_CONFIG_INVALIDA

    clave = CLAVE_CONFIG_INVALIDA.format(job_id="jy")
    entrada = {"seeds": ["http://127.0.0.1:{port}/home"], "config": CONFIG, "rutas": SITIO,
               "redis_falso": True, "redis_inicial": {clave: "vieja"}, "job_id": "jy"}
    proc = subprocess.run([sys.executable, HARNESS, json.dumps(entrada)],
                          capture_output=True, text=True, timeout=120)
    assert "@@REDIS@@" in proc.stdout, proc.stdout + proc.stderr
    marcas = json.loads(proc.stdout.split("@@REDIS@@", 1)[1].split("\n", 1)[0])
    assert clave not in marcas
    items = json.loads(proc.stdout.split("@@ITEMS@@", 1)[1])
    assert [i for i in items if i["_tipo"] == "PageItem"]


# ---------------------------------------------------------------------------
# Lo guardado, con la tuberia real (Postgres)
# ---------------------------------------------------------------------------
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
        s.add(Job(id=jid, name="test a medida", status="running", seeds=[], config={}))
        s.commit()
    yield Sesion, jid
    with engine.begin() as c:
        for tabla in ("custom_extraction", "links", "issues", "urls", "jobs"):
            col = "id" if tabla == "jobs" else "job_id"
            c.execute(text(f"DELETE FROM {tabla} WHERE {col} = :j"), {"j": str(jid)})
    engine.dispose()


def _guardado(Sesion, jid) -> dict[str, dict[str, dict]]:
    from shared.models import CustomExtraction, Url

    with Sesion() as s:
        filas = (s.query(Url.path, CustomExtraction)
                 .join(CustomExtraction, CustomExtraction.url_id == Url.id)
                 .filter(CustomExtraction.job_id == jid).all())
        salida: dict[str, dict[str, dict]] = {}
        for ruta, ce in filas:
            assert ruta not in salida or ce.nombre not in salida[ruta], "fila duplicada"
            salida.setdefault(ruta, {})[ce.nombre] = {
                "estado": ce.estado, "valor": ce.valor, "valor_num": ce.valor_num, "n": ce.n}
        return salida


def _puerto_libre() -> int:
    import socket

    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


def test_lo_guardado_se_reemplaza_al_reanudar_y_se_borra_si_deja_de_ser_ficha(bd):
    Sesion, jid = bd
    puerto = _puerto_libre()
    _rastrear(bd=URL_BD, job_id=str(jid), puerto=puerto)
    antes = _guardado(Sesion, jid)
    assert antes["/p/1"]["precio"]["valor_num"] == 19.95
    assert antes["/p/2"]["sku"]["valor"] == "SKU-2"
    assert "/doc.pdf" not in antes and set(antes["/home"]) == {"h1", "gtm"}

    # El sitio cambia: /p/1 pierde el precio, /p/2 da 404 y /p/3 redirige.
    cambiado = {
        **SITIO,
        "/p/1": _pagina('<h1>Zapatillas</h1><p class="stock">Agotado</p>', head=GTM),
        "/p/2": [404, {}, "<html><body>no</body></html>"],
        "/p/3": [301, {"Location": "/p/1"}, ""],
    }
    config = {**CONFIG, "crawl_behavior": {"recrawl_patterns": ["/p/"]}}
    _rastrear(sitio=cambiado, config=config, bd=URL_BD, job_id=str(jid), puerto=puerto)
    despues = _guardado(Sesion, jid)
    assert despues["/p/1"]["precio"]["estado"] == "sin_coincidencia"   # no el 19,95 de antes
    assert despues["/p/1"]["stock"]["valor"] == "Agotado"
    assert "/p/2" not in despues and "/p/3" not in despues            # 404 y 301: sin filas
    assert despues["/home"] == antes["/home"]                          # lo no repetido, intacto

    # Repetir no duplica (el _guardado lo comprobaria; y la restriccion unica).
    _rastrear(sitio=cambiado, config=config, bd=URL_BD, job_id=str(jid), puerto=puerto)
    assert _guardado(Sesion, jid) == despues
