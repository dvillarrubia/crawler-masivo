"""Avisos de los ficheros de sitemap (decision 126), EJECUTADOS contra SQLite
(decision 74): `sitemap_supera_limite`, `lastmod_futuro` y
`en_varios_sitemaps`, cada uno con su unidad (fichero o combinacion de
ficheros) y su ancla, y nada cuando el censo no sabe nada de sus sitemaps.

Y la tuberia que guarda `sitemap_entries`: que una reanudacion (otro proceso
de Scrapy que vuelve a leer los mismos ficheros) no duplique las filas.
"""

from __future__ import annotations

import types
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from analysis import avisos_de_sitemaps
from analysis.analyzer import SEOAnalyzer
from shared.models import Base, Issue, Job, SitemapEntry, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    return "INTEGER"


LEIDO = "2026-10-18T10:00:00+00:00"
A = "https://x.com/sitemap-paginas.xml"
B = "https://x.com/sitemap-completo.xml"
C = "https://cdn.x.com/sitemap-blog.xml.gz"


def _fichero(url, **kw):
    datos = {"url": url, "status": 200, "formato": "urlset", "declaradas": 3,
             "devueltas": 3, "hijos": 0, "bytes": 1000, "cortado": False,
             "entradas_guardadas": 3, "leido_at": LEIDO}
    datos.update(kw)
    return datos


def _montar(ficheros=None, recortadas=False):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[Job.__table__, Url.__table__, Issue.__table__,
                                             SitemapEntry.__table__])
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="t", status="analyzing", seeds=["https://x.com/"],
            config={}, started_at=datetime(2026, 10, 18, 9, tzinfo=timezone.utc))
    if ficheros is not None:
        j.sitemaps = {"ficheros": ficheros, "pedidos": len(ficheros),
                      "leidos": len(ficheros), "parcial": False,
                      "entradas_recortadas": recortadas}
    s.add(j)
    s.flush()
    return s, j


def _url(s, j, ruta, host="x.com"):
    u = Url(job_id=j.id, url=f"https://{host}{ruta}", url_hash=f"{host}{ruta}", host=host,
            scheme="https", path=ruta, is_internal=True, is_html=True, status_code=200)
    s.add(u)
    s.flush()
    return u


def _entrada(s, j, ruta, fichero, lastmod=None):
    s.add(SitemapEntry(job_id=j.id, url_hash=f"x.com{ruta}", url=f"https://x.com{ruta}",
                       fichero=fichero, lastmod=lastmod))
    s.flush()


def _issues(s, j, tipo):
    return s.execute(select(Issue.url_id, Issue.severity, Issue.details, Issue.group_key)
                     .where(Issue.job_id == j.id, Issue.issue_type == tipo)
                     .order_by(Issue.id)).all()


def _avisar(s, j):
    r = avisos_de_sitemaps.avisar(SEOAnalyzer(s, j.id))
    s.commit()
    return r


def test_un_fichero_por_encima_del_limite_es_un_aviso_por_fichero():
    s, j = _montar([_fichero(A, declaradas=50_001),
                    _fichero(B, bytes=60 * 1024 * 1024),
                    _fichero(C, declaradas=50_000, bytes=10),
                    _fichero("https://x.com/roto.xml", status=404, declaradas=None, bytes=None)])
    home = _url(s, j, "/")
    _url(s, j, "/a")
    _avisar(s, j)
    avisos = _issues(s, j, "sitemap_supera_limite")
    assert [(a.url_id, a.severity, a.details["fichero"], a.details["motivos"]) for a in avisos] == [
        (home.id, "warning", A, ["urls"]), (home.id, "warning", B, ["bytes"])]
    # Cada fichero es su propio hallazgo entre censos (decision 88).
    assert len({a.group_key for a in avisos}) == 2


def test_lastmod_futuro_uno_por_fichero_con_margen_de_un_dia():
    s, j = _montar([_fichero(A), _fichero(B)])
    home = _url(s, j, "/")
    _entrada(s, j, "/a", A, "2026-10-19")              # dentro del margen
    _entrada(s, j, "/b", A, "2026-10-25")              # futuro
    _entrada(s, j, "/c", A, "2027-01-01T00:00:00Z")    # futuro, el mayor
    _entrada(s, j, "/d", A, "ayer")                    # ilegible: no se juzga
    _entrada(s, j, "/e", B, "2026-10-01")
    _avisar(s, j)
    (aviso,) = _issues(s, j, "lastmod_futuro")
    assert aviso.url_id == home.id and aviso.severity == "warning"
    d = aviso.details
    assert d["fichero"] == A and d["entradas_futuras"] == 2
    assert d["entradas_con_lastmod"] == 4 and d["lastmod_ilegibles"] == 1
    assert d["lastmod_maximo"] == "2027-01-01T00:00:00Z"
    assert [e["url"] for e in d["ejemplos"]] == ["https://x.com/b", "https://x.com/c"]
    assert d["leido_at"] == LEIDO


def test_el_futuro_se_mide_contra_la_lectura_del_fichero_no_contra_hoy():
    """Re-analizar un censo de hace un año no puede volver «futuras» fechas
    que el dia del rastreo ya habian pasado, ni al reves."""
    s, j = _montar([_fichero(A, leido_at="2025-01-01T00:00:00+00:00")])
    _url(s, j, "/")
    _entrada(s, j, "/a", A, "2025-06-01")
    _avisar(s, j)
    assert len(_issues(s, j, "lastmod_futuro")) == 1


def test_en_varios_sitemaps_uno_por_combinacion_anclado_en_una_afectada():
    s, j = _montar([_fichero(A), _fichero(B), _fichero(C)])
    _url(s, j, "/")
    p2 = _url(s, j, "/p2")
    p1 = _url(s, j, "/p1")
    for ruta in ("/p1", "/p2", "/p3"):            # /p3 no se rastreo
        _entrada(s, j, ruta, A, "2026-10-01")
        _entrada(s, j, ruta, B, "2026-10-01" if ruta != "/p2" else "2026-09-01")
    _entrada(s, j, "/b1", A)
    _entrada(s, j, "/b1", C)
    _entrada(s, j, "/solo", A)
    _avisar(s, j)
    avisos = _issues(s, j, "en_varios_sitemaps")
    assert len(avisos) == 2
    grande, chica = avisos
    assert grande.severity == "info"
    # En la URL MENOR de las afectadas, no en la de id menor (decision 88):
    # /p2 se rastreo antes y tiene el id mas bajo.
    assert p2.id < p1.id and grande.url_id == p1.id
    assert grande.details["ficheros"] == sorted([A, B])
    assert grande.details["paginas_afectadas"] == 3 and grande.details["en_el_censo"] == 2
    assert grande.details["lastmod_distinto"] == 1
    assert grande.details["ejemplos"] == ["https://x.com/p1", "https://x.com/p2",
                                          "https://x.com/p3"]
    # Ninguna de las afectadas esta en el censo: se ancla en el host del fichero.
    assert chica.details["ficheros"] == sorted([A, C])
    assert chica.details["en_el_censo"] == 0
    assert grande.group_key != chica.group_key


def test_el_ancla_no_depende_del_orden_de_rastreo():
    """Dos censos iguales rastreados en otro orden anclan los tres avisos en la
    MISMA URL: la comparacion casa por (tipo, url, group_key) (decision 88), y
    anclar en el id menor daria «resuelto» y «nuevo» a la vez."""
    def censo(orden):
        s, j = _montar([_fichero(C, declaradas=60_000)])
        for ruta in orden:                       # sin raiz rastreada en ningun host
            _url(s, j, ruta)
        for ruta in ("/p1", "/p2"):
            _entrada(s, j, ruta, A)
            _entrada(s, j, ruta, B)
        _avisar(s, j)
        ancla = lambda tipo: {u.url for u in s.query(Url).filter(  # noqa: E731
            Url.id.in_([i.url_id for i in _issues(s, j, tipo)]))}
        return ancla("en_varios_sitemaps"), ancla("sitemap_supera_limite")

    assert censo(["/p2", "/p1", "/z"]) == censo(["/z", "/p1", "/p2"]) == (
        {"https://x.com/p1"}, {"https://x.com/p1"})


def test_run_all_los_emite():
    """Enchufado al analisis completo, ejecutandolo (decision 74)."""
    s, j = _montar([_fichero(A, declaradas=60_000)])
    _url(s, j, "/")
    from shared.models import HtmlMeta, Hreflang

    HtmlMeta.__table__.create(s.get_bind(), checkfirst=True)
    Hreflang.__table__.create(s.get_bind(), checkfirst=True)
    az = SEOAnalyzer(s, j.id)
    for nombre in dir(az):
        if nombre.startswith(("analyze_", "compute_")) and nombre != "analyze_indexability":
            setattr(az, nombre, lambda *a, **k: None)
    import analysis.analyzer as modulo

    original = modulo.avisos_de_enlaces.avisar_enlaces_no_rastreables
    modulo.avisos_de_enlaces.avisar_enlaces_no_rastreables = lambda a: None
    try:
        az.run_all()
    finally:
        modulo.avisos_de_enlaces.avisar_enlaces_no_rastreables = original
    assert len(_issues(s, j, "sitemap_supera_limite")) == 1


def test_sin_resumen_de_sitemaps_no_se_afirma_nada():
    s, j = _montar(None)
    _url(s, j, "/")
    _entrada(s, j, "/a", A, "2030-01-01")
    _entrada(s, j, "/a", B)
    assert _avisar(s, j) is None
    assert s.execute(select(Issue.id)).first() is None


def test_corre_dentro_del_analisis_entero():
    """`run_all` lo llama: un modulo que nadie invoca no avisa de nada."""
    import inspect

    fuente = inspect.getsource(SEOAnalyzer.run_all)
    assert "avisos_de_sitemaps.avisar(self)" in fuente
    # Y ejecutandolo de verdad, que la linea de arriba no basta (decision 74).
    s, j = _montar([_fichero(A, declaradas=60_000)])
    _url(s, j, "/")
    avisos_de_sitemaps.avisar(SEOAnalyzer(s, j.id))
    assert len(_issues(s, j, "sitemap_supera_limite")) == 1


# ---------------------------------------------------------------------------
# Tuberia
# ---------------------------------------------------------------------------
@pytest.fixture()
def tuberia(tmp_path, monkeypatch):
    pytest.importorskip("scrapy")
    import shared.database

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="t", status="running", seeds=["https://x.com/"], config={}))
        s.commit()
    return Sesion, jid


def _proceso(jid, items):
    from seo_crawler.items import SitemapEntradasItem
    from seo_crawler.pipelines import PostgresPipeline

    spider = types.SimpleNamespace(job_id=str(jid))
    tub = PostgresPipeline(batch_size=2, commit_every=50)
    tub.open_spider(spider)
    for fichero, entradas in items:
        tub.process_item(SitemapEntradasItem(job_id=str(jid), fichero=fichero,
                                             entradas=entradas), spider)
    tub.close_spider(spider)


def test_la_tuberia_guarda_las_entradas_y_reanudar_no_las_duplica(tuberia):
    Sesion, jid = tuberia
    entradas = [{"url": "https://x.com/a", "url_hash": "ha", "lastmod": "2026-01-01"},
                {"url": "https://x.com/b", "url_hash": "hb", "lastmod": None}]
    _proceso(jid, [(A, entradas), (B, entradas[:1])])
    # Una reanudacion es otro proceso que vuelve a leer los mismos ficheros.
    _proceso(jid, [(A, entradas), (B, entradas[:1])])
    with Sesion() as s:
        filas = sorted((e.fichero, e.url, e.lastmod) for e in
                       s.query(SitemapEntry).filter(SitemapEntry.job_id == jid))
    assert filas == [(B, "https://x.com/a", "2026-01-01"),
                     (A, "https://x.com/a", "2026-01-01"),
                     (A, "https://x.com/b", None)]


def test_lastmod_futuro_dice_si_la_cuenta_es_un_minimo():
    """Con el tope de entradas alcanzado, de un fichero a medias no se guardaron
    todas: `entradas_futuras` es un minimo y el aviso tiene que decirlo, como
    ya lo decia `en_varios_sitemaps` (revision de la decision 126)."""
    s, j = _montar([_fichero(A, internas=3, entradas_guardadas=3),
                    _fichero(B, internas=3, entradas_guardadas=1)], recortadas=True)
    _url(s, j, "/")
    _entrada(s, j, "/a", A, "2099-01-01")
    _entrada(s, j, "/b", B, "2099-01-01")
    _avisar(s, j)
    marca = {a.details["fichero"]: a.details["entradas_recortadas"]
             for a in _issues(s, j, "lastmod_futuro")}
    # A se guardo entero: su cuenta es exacta aunque el censo se recortara.
    assert marca == {A: False, B: True}


def test_lastmod_futuro_sin_tope_no_es_minimo_y_censo_viejo_usa_la_marca_global():
    s, j = _montar([_fichero(A)])
    _url(s, j, "/")
    _entrada(s, j, "/a", A, "2099-01-01")
    _avisar(s, j)
    (aviso,) = _issues(s, j, "lastmod_futuro")
    assert aviso.details["entradas_recortadas"] is False

    s, j = _montar([_fichero(A)], recortadas=True)   # sin `internas`
    _url(s, j, "/")
    _entrada(s, j, "/a", A, "2099-01-01")
    _avisar(s, j)
    (aviso,) = _issues(s, j, "lastmod_futuro")
    assert aviso.details["entradas_recortadas"] is True
