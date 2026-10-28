"""Rastreo incremental contra Postgres de verdad (decision 220): seleccion,
arrastre (desde `links` y desde el Parquet), sitemap, poda y estado.

El arrastre es SQL de Postgres (`UPDATE ... FROM`, tablas temporales, `COPY`,
CTE recursiva) y no corre en SQLite. Se salta sin una BD de pruebas:

    PAGERANK_TEST_DATABASE_URL=postgresql+psycopg2://crawler:crawler@postgres:5432/crawler_test pytest tests/test_incremental_db.py

NUNCA apuntarlo a la BD de trabajo: crea tablas si faltan.

El sitio del censo anterior:

    /  -> /a, /b, /c, /r        /a -> /b        /c -> /e
    /r  301 -> /d               /d solo se alcanza por la redireccion

En el incremental tocan /, /a y /r; la home deja de enlazar /c. Se espera: /a
(304) completada con la del anterior, /b y /d arrastradas, y /c y /e podadas
(ya no las enlaza nada: un completo no las habria encontrado).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL_BD, reason="sin PAGERANK_TEST_DATABASE_URL")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from shared.models import (  # noqa: E402
    Base, Client, CrawlProfile, Heading, Hreflang, HtmlMeta, Job, JobFrontera, Link,
    PageContent, Programacion, SitemapArrastre, Url, UrlState,
)

pytest.importorskip("seo_crawler.extractors")
from seo_crawler.extractors import compute_url_hash, normalize_url  # noqa: E402

BASE = "https://x.com"
CONFIG = {"max_depth": 5, "incremental": {"activo": True, "completo_cada": 4,
                                          "muestra_control": 0}}


def _h(ruta: str) -> str:
    return compute_url_hash(normalize_url(BASE + ruta))


@pytest.fixture()
def db():
    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.rollback()
    s.close()
    engine.dispose()


@pytest.fixture()
def perfil(db):
    cliente = Client(id=uuid.uuid4(), nombre="X", slug=f"x-{uuid.uuid4().hex[:8]}")
    db.add(cliente)
    db.flush()
    p = CrawlProfile(id=uuid.uuid4(), cliente_id=cliente.id, nombre="Sitio",
                     seeds=[BASE + "/"], config=CONFIG)
    prog = Programacion(id=uuid.uuid4(), nombre="x (semanal)", profile_id=p.id,
                        cliente_id=cliente.id, hereda_perfil=True, seeds=[],
                        cron="0 3 * * 1")
    db.add_all([p, prog])
    db.commit()
    yield p, prog
    jobs = [j for (j,) in db.query(Job.id).filter(Job.profile_id == p.id)]
    db.query(UrlState).filter(UrlState.profile_id == p.id).delete()
    db.query(CrawlProfile).filter(CrawlProfile.id == p.id).update({"last_job_id": None})
    for j in jobs:
        db.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": str(j)})
    db.delete(prog)
    db.delete(p)
    db.delete(cliente)
    db.commit()


def _disparo(db, prog, perfil):
    from shared.programaciones import crear_job_de_disparo

    datos = {"name": "x", "client_id": "x", "cliente_id": perfil.cliente_id,
             "profile_id": perfil.id, "seeds": [BASE + "/"], "config": dict(CONFIG)}
    job = crear_job_de_disparo(db, prog, datos, datetime.now(timezone.utc))
    db.commit()
    return job


def _url(db, job, ruta, *, status=200, fetch_mode=None, redirect=None, cuando=None, **k):
    u = Url(job_id=job.id, url=BASE + ruta, url_hash=_h(ruta), host="x.com", path=ruta,
            scheme="https", is_internal=True, is_html=status == 200, crawl_depth=k.pop("depth", 1),
            status_code=status, status_group=f"{status // 100}xx",
            redirect_url=BASE + redirect if redirect else None, fetch_mode=fetch_mode,
            last_crawled_at=cuando or datetime.now(timezone.utc),
            response_class="contenido" if status == 200 else None, **k)
    db.add(u)
    db.flush()
    return u


def _enlaces(db, job, desde: Url, *rutas):
    for r in rutas:
        db.add(Link(job_id=job.id, from_url_id=desde.id, to_url=BASE + r, to_url_hash=_h(r),
                    is_internal=True, follow=True, link_type="hyperlink", repeticiones=1))


def _censo_anterior(db, prog, perfil):
    """El completo: el sitio entero, analizado y con su estado al dia."""
    from shared.incremental import actualizar_estado

    job = _disparo(db, prog, perfil)
    assert job.incremental["modo"] == "completo"
    hace = datetime.now(timezone.utc) - timedelta(days=7)
    home = _url(db, job, "/", depth=0, cuando=hace, outlinks_count=4)
    a = _url(db, job, "/a", etag='"a1"', cuando=hace, content_hash="ca")
    b = _url(db, job, "/b", etag='"b1"', cuando=hace)
    c = _url(db, job, "/c", cuando=hace)
    e = _url(db, job, "/e", depth=2, cuando=hace)
    r = _url(db, job, "/r", status=301, redirect="/d", cuando=hace)
    d = _url(db, job, "/d", depth=2, cuando=hace)
    _enlaces(db, job, home, "/a", "/b", "/c", "/r")
    _enlaces(db, job, a, "/b")
    _enlaces(db, job, c, "/e")
    for u in (home, a, b, c, e, d):
        db.add(HtmlMeta(url_id=u.id, title=f"titulo {u.path}"))
        db.add(Heading(url_id=u.id, tag="h1", position=0, text=f"h1 {u.path}"))
        db.add(PageContent(url_id=u.id, content_text="limpio",
                           content_text_original=f"original {u.path}",
                           cleaned_at=datetime.now(timezone.utc)))
    db.add(Hreflang(url_id=b.id, lang="es", href=BASE + "/b", return_tag_ok=True,
                    lang_valid=True))
    job.status, job.finish_reason = "completed", "finished"
    job.completed_at = datetime.now(timezone.utc)
    db.commit()
    actualizar_estado(db, job.id)
    db.refresh(job)
    return job


def _incremental(db, prog, perfil, previo):
    """El incremental, rastreado: home pedida (ya sin enlace a /c), /a con 304
    y /r otra vez 301. Los demas no tocan."""
    no_tocan = [_h(r) for r in ("/b", "/c", "/e", "/d")]
    db.query(UrlState).filter(UrlState.profile_id == perfil.id,
                              UrlState.url_hash.in_(no_tocan)).update(
        {"proximo_ciclo": 9}, synchronize_session=False)
    db.commit()
    job = _disparo(db, prog, perfil)
    inc = job.incremental
    assert inc["modo"] == "incremental", inc
    assert inc["prev_job_id"] == str(previo.id) and inc["ciclo"] == 2
    frontera = dict(db.query(JobFrontera.url_hash, JobFrontera.motivo)
                    .filter(JobFrontera.job_id == job.id))
    assert frontera == {_h("/"): "fijo", _h("/a"): "toca", _h("/r"): "toca"}
    assert inc["seleccion"]["conocidas"] == 7

    home = _url(db, job, "/", depth=0, fetch_mode="fetched", in_sitemap=True,
                outlinks_count=3)
    _url(db, job, "/a", status=304, fetch_mode="not_modified", in_sitemap=False)
    _url(db, job, "/r", status=301, redirect="/d", fetch_mode="fetched", in_sitemap=False)
    _enlaces(db, job, home, "/a", "/b", "/r")
    db.add(HtmlMeta(url_id=home.id, title="titulo /"))
    db.add(SitemapArrastre(job_id=job.id, url_hash=_h("/b")))
    job.status, job.sitemaps = "analyzing", {"parcial": False}
    db.commit()
    return job


def _filas(db, job):
    return {u.path: u for u in db.query(Url).filter(Url.job_id == job.id)}


def test_un_ciclo_incremental_entero(db, perfil):
    from analysis.arrastre import arrastrar
    from shared.incremental import actualizar_estado

    p, prog = perfil
    previo = _censo_anterior(db, prog, p)
    estado = {e.url_hash: e for e in db.query(UrlState).filter(UrlState.profile_id == p.id)}
    assert len(estado) == 7 and estado[_h("/")].fijo
    # Solo la home: con casi todas las paginas igual de enlazadas, el
    # percentil 95 no convierte en «hub» a todo el sitio.
    assert {h for h, e in estado.items() if e.fijo} == {_h("/")}
    assert estado[_h("/a")].etag == '"a1"' and estado[_h("/a")].proximo_ciclo == 2

    job = _incremental(db, prog, p, previo)
    r = arrastrar(db, job.id)
    db.expire_all()
    filas = _filas(db, job)

    # /c ya no la enlaza nadie, y /e solo la enlazaba /c: podadas. /d se
    # alcanza por la redireccion de /r aunque ningun enlace vaya a ella.
    assert set(filas) == {"/", "/a", "/r", "/b", "/d"}
    assert r["podadas"] == 2 and r["no_modificadas"] == 1
    assert r["pct_arrastrado"] == pytest.approx(40.0)

    a = filas["/a"]
    assert (a.fetch_mode, a.status_code, a.is_html, a.content_hash) == \
        ("not_modified", 200, True, "ca")
    # El 304 conserva cuando se pidio de verdad, y su etag.
    assert a.last_crawled_at > datetime.now(timezone.utc) - timedelta(hours=1)
    b = filas["/b"]
    assert b.fetch_mode == "carried" and b.etag == '"b1"'
    assert b.last_crawled_at < datetime.now(timezone.utc) - timedelta(days=6)
    # Lo que calcula el analisis empieza de cero, como una fila recien rastreada.
    assert b.inlinks_count == 0 and b.pagerank is None and b.indexable is None
    # El sitemap de ESTE rastreo: /b esta; /d no, y el sitemap se leyo entero.
    assert b.in_sitemap is True and filas["/d"].in_sitemap is False

    # Los hijos, con el id de la fila nueva.
    assert db.query(HtmlMeta.title).filter(HtmlMeta.url_id == a.id).scalar() == "titulo /a"
    assert db.query(Heading.text).filter(Heading.url_id == b.id).scalar() == "h1 /b"
    pc = db.query(PageContent).filter(PageContent.url_id == b.id).one()
    assert (pc.content_text, pc.content_text_original, pc.cleaned_at) == \
        ("original /b", None, None)
    hl = db.query(Hreflang).filter(Hreflang.url_id == b.id).one()
    assert (hl.return_tag_ok, hl.lang_valid) == (None, None)
    salientes = {
        (o, d) for o, d in db.execute(text(
            "SELECT u.path, l.to_url FROM links l JOIN urls u ON u.id = l.from_url_id "
            "WHERE l.job_id = :j"), {"j": str(job.id)})}
    assert salientes == {("/", BASE + "/a"), ("/", BASE + "/b"), ("/", BASE + "/r"),
                         ("/a", BASE + "/b")}

    # Repetirlo no duplica nada.
    assert arrastrar(db, job.id)["hecho"]
    assert db.query(Url).filter(Url.job_id == job.id).count() == 5
    assert db.query(HtmlMeta).join(Url, Url.id == HtmlMeta.url_id).filter(
        Url.job_id == job.id).count() == 4

    # El estado tras el incremental: lo podado sale, el 304 sin cambios y la
    # home, con otros enlaces, cambiada.
    job.status, job.finish_reason = "completed", "finished"
    db.commit()
    resumen = actualizar_estado(db, job.id)
    estado = {e.url_hash: e for e in db.query(UrlState).filter(UrlState.profile_id == p.id)}
    assert set(estado) == {_h(x) for x in ("/", "/a", "/r", "/b", "/d")}
    assert resumen["fuera"] == 2 and resumen["arrastradas"] == 2
    assert estado[_h("/a")].sin_cambios == 1 and estado[_h("/a")].intervalo == pytest.approx(1.4)
    assert estado[_h("/")].ultimo_cambio_ciclo == 2
    assert estado[_h("/b")].proximo_ciclo == 9  # arrastrada: conserva su turno


def test_el_arrastre_lee_los_enlaces_del_parquet(db, perfil, tmp_path, monkeypatch):
    pytest.importorskip("pyarrow")
    from analysis.arrastre import arrastrar
    from shared import archivo_enlaces

    monkeypatch.setenv("ARCHIVO_DIR", str(tmp_path))
    p, prog = perfil
    previo = _censo_anterior(db, prog, p)
    archivo_enlaces.archivar(db, previo.id)
    db.expire_all()
    assert archivo_enlaces.esta_archivado(db.get(Job, previo.id))
    assert db.query(Link).filter(Link.job_id == previo.id).count() == 0

    job = _incremental(db, prog, p, previo)
    r = arrastrar(db, job.id)
    salientes = {
        (o, d) for o, d in db.execute(text(
            "SELECT u.path, l.to_url FROM links l JOIN urls u ON u.id = l.from_url_id "
            "WHERE l.job_id = :j"), {"j": str(job.id)})}
    assert ("/a", BASE + "/b") in salientes and ("/c", BASE + "/e") not in salientes
    assert r["podadas"] == 2


def test_sin_el_censo_anterior_no_hay_arrastre(db, perfil):
    from analysis.arrastre import OrigenBorrado, arrastrar

    p, prog = perfil
    previo = _censo_anterior(db, prog, p)
    job = _incremental(db, prog, p, previo)
    db.query(Url).filter(Url.job_id == previo.id).delete()
    db.commit()
    with pytest.raises(OrigenBorrado):
        arrastrar(db, job.id)


def test_un_censo_truncado_no_sirve_de_base(db, perfil):
    p, prog = perfil
    previo = _censo_anterior(db, prog, p)
    previo.finish_reason = "max_urls_reached"
    db.commit()
    job = _disparo(db, prog, p)
    assert (job.incremental["modo"], job.incremental["motivo"]) == \
        ("completo", "censo_anterior_truncado")
    assert db.query(JobFrontera).filter(JobFrontera.job_id == job.id).count() == 0


def test_la_muestra_de_control_que_cambia_fuerza_un_completo(db, perfil):
    from shared.incremental import actualizar_estado

    p, prog = perfil
    previo = _censo_anterior(db, prog, p)
    job = _disparo(db, prog, p)
    assert job.incremental["modo"] == "incremental"
    # Dos de control que han cambiado de titulo.
    db.query(JobFrontera).filter(JobFrontera.job_id == job.id).delete()
    for ruta in ("/b", "/c"):
        db.add(JobFrontera(job_id=job.id, url_hash=_h(ruta), url=BASE + ruta, motivo="control"))
        u = _url(db, job, ruta, fetch_mode="fetched")
        db.add(HtmlMeta(url_id=u.id, title="otro titulo"))
    job.status, job.finish_reason = "completed", "finished"
    job.incremental = {**job.incremental, "arrastre": {"hecho": True}}
    db.commit()
    actualizar_estado(db, job.id)
    db.refresh(job)
    assert job.incremental["forzar_completo"] == "muestra_control"
    siguiente = _disparo(db, prog, p)
    assert (siguiente.incremental["modo"], siguiente.incremental["motivo"]) == \
        ("completo", "forzado_por_el_anterior")
    assert previo is not None
