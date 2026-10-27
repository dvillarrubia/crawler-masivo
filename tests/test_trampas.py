"""Trampas de rastreo (#14, decision 214): el detector sobre listas de URLs
(puro) y el aviso agrupado contra SQLite con el analizador."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from analysis import avisos_de_trampas as T

AHORA = datetime(2026, 10, 27, tzinfo=timezone.utc)
B = "https://www.x.com"


def _ids(urls):
    return [(n + 1, u) for n, u in enumerate(urls)]


def _tipos(urls, **kw):
    return {t["tipo"]: t for t in T.detectar(_ids(urls), ahora=AHORA, **kw)}


def test_un_calendario_que_se_va_al_futuro():
    cal = [f"{B}/agenda/{a}/{m:02d}/" for a in range(2026, 2034) for m in range(1, 13)]
    t = _tipos(cal + [f"{B}/blog/p{i}" for i in range(50)])["calendario_infinito"]
    assert t["paginas_afectadas"] == len(cal) and t["fechas_futuras"] > 0
    assert t["plantilla"].startswith("/agenda/")


def test_un_id_de_seis_cifras_no_es_una_fecha():
    """Penguin: /es/192401-daniel-brieba no es enero de 1924."""
    assert T.fechas_de("/es/192401-daniel-brieba") == []
    autores = [f"{B}/es/{190000 + i}-autor-{i}" for i in range(400)]
    assert "calendario_infinito" not in _tipos(autores)
    assert T.fechas_de("/agenda", [("date", "20291101"), ("ref", "20291101")]) == [(2029, 11)]
    assert T.fechas_de("/agenda", [("mes", "11"), ("anio", "2031")]) == [(2031, 11)]


def test_un_blog_de_diez_anos_y_una_hemeroteca_no_son_calendarios():
    blog = [f"{B}/{a}/{m:02d}/entrada-{a}-{m}" for a in range(2017, 2027) for m in range(1, 13)]
    archivo = [f"{B}/blog/{a}/{m:02d}/" for a in range(2019, 2027) for m in range(1, 13)]
    hemeroteca = [f"{B}/noticias/1995/03/una"] + [f"{B}/noticias/2020/{m:02d}/n{m}" for m in range(1, 13)] * 3
    for urls in (blog, archivo, hemeroteca):
        assert "calendario_infinito" not in _tipos(urls)


def test_calendarios_en_la_query():
    sueltos = [f"{B}/agenda?mes={m}&anio={a}" for a in range(2026, 2034) for m in range(1, 13)]
    t = _tipos(sueltos)
    assert "calendario_infinito" in t and "facetas_combinatorias" not in t, "un hallazgo, no dos"
    tribe = [f"{B}/eventos/?tribe-bar-date={a}-{m:02d}" for a in range(2026, 2034) for m in range(1, 13)]
    assert "calendario_infinito" in _tipos(tribe)
    sin_cero = [f"{B}/agenda/{a}/{m}/" for a in range(2026, 2034) for m in range(1, 13)]
    assert _tipos(sin_cero)["calendario_infinito"]["paginas_afectadas"] == 96


def test_parametro_de_sesion_jsessionid_y_phpsessid():
    t = _tipos([f"{B}/a?sid=1", f"{B}/a?sid=2", f"{B}/b;jsessionid=ABC", f"{B}/c;PHPSESSID=x"])
    assert t["parametro_de_sesion"]["paginas_afectadas"] == 4, "las cuatro, de la misma plantilla"
    assert sum(x["paginas_afectadas"] for x in T.detectar(_ids([f"{B}/a?sid=1", f"{B}/a?sid=2", f"{B}/b;jsessionid=ABC",
                                                               f"{B}/c;PHPSESSID=x"]), ahora=AHORA)) == 4


def test_facetas_que_se_combinan_y_el_mismo_filtro_en_otro_orden():
    urls = [f"{B}/zapatos?color={c}&talla={t}" for c in range(10) for t in range(6)]
    t = _tipos(urls)["facetas_combinatorias"]
    assert t["combinaciones"] == 60 and t["paginas_afectadas"] == 60
    pocas = [f"{B}/z?color=1&talla=2", f"{B}/z?talla=2&color=1"] + [f"{B}/z?color={i}" for i in range(20)]
    t = _tipos(pocas)["facetas_combinatorias"]
    assert t["mismo_filtro_en_otro_orden"] is True
    assert "facetas_combinatorias" not in _tipos([f"{B}/z?page={i}" for i in range(80)]), "paginar no es filtrar"
    seguimiento = [f"{B}/contacto?ref=x{i}&gclid={i}&gad_source=1&srsltid={i}" for i in range(80)]
    assert _tipos(seguimiento) == {}, "seguimiento, ref, idioma o moneda no son filtros"


def test_facetas_de_muchas_categorias_son_una_plantilla():
    """500 categorias de la misma plantilla son UN aviso, y 30 x 30 tambien."""
    urls = [f"{B}/es/c/cat{n}-1234?color={c}&talla={t}" for n in range(30) for c in range(6) for t in range(5)]
    t = [x for x in T.detectar(_ids(urls), ahora=AHORA) if x["tipo"] == "facetas_combinatorias"]
    assert len(t) == 1 and t[0]["paginas_afectadas"] == 900


def test_entidades_html_en_la_url():
    """La Salle: `?%3Bamp%3Bamp%3Bpage=8&%3Bpage=7`, un `&amp;` copiado a la URL."""
    urls = [f"{B}/es/noticias?%3Bamp%3Bamp%3Bpage={i}&%3Bpage={i}&sort_by=x" for i in range(30)]
    t = _tipos(urls)
    assert "entidad_html_en_url" in t and "facetas_combinatorias" not in t


def test_busqueda_interna_y_segmento_repetido():
    t = _tipos([f"{B}/?s=hotel", f"{B}/?s=playa", f"{B}/tienda?s=M", f"{B}/tienda?s=L"])
    assert t["busqueda_interna_rastreable"]["paginas_afectadas"] == 2, "?s=M es una talla, no una busqueda"
    rep = [f"{B}/es/hoteles/es/hoteles/{i}" for i in range(4)]
    assert _tipos(rep)["segmento_repetido"]["paginas_afectadas"] == 4
    creciente = [f"{B}/a/" + "b/" * n + "x" for n in range(2, 16)]
    assert _tipos(creciente)["segmento_repetido"]["paginas_afectadas"] == 14, "un bucle que crece es un aviso"
    assert not T.tramos_repetidos("/es/hoteles/gran-canaria/")
    assert not T.tramos_repetidos("/productos/ofertas/productos/destacados/productos/p1")
    assert not T.tramos_repetidos("/tienda/1/1/1/p2")
    assert T.tramos_repetidos("/a/b/a/b/") and T.tramos_repetidos("/es/es/hotel")


def test_la_severidad_depende_de_lo_que_se_lleva():
    cal = [f"{B}/agenda/{a}/{m:02d}/" for a in range(2026, 2034) for m in range(1, 13)]
    assert _tipos(cal)["calendario_infinito"]["severidad"] == "warning"
    t = _tipos(cal[-30:] + [f"{B}/p{i}" for i in range(2000)])
    assert t["calendario_infinito"]["severidad"] == "info"


def test_el_aviso_va_agrupado_por_plantilla(tmp_path):
    from sqlalchemy import BigInteger, create_engine, select
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker

    from analysis.analyzer import SEOAnalyzer
    from shared.models import Base, Issue, Job, Url

    @compiles(BigInteger, "sqlite")
    def _bigint(tipo, compilador, **kw):
        return "INTEGER"

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    j = Job(id=uuid.uuid4(), name="c", status="analyzing", seeds=[B + "/"], config={})
    s.add(j)
    s.flush()
    for a in range(2026, 2034):
        for m in range(1, 13):
            ruta = f"/agenda/{a}/{m:02d}/"
            s.add(Url(job_id=j.id, url=B + ruta, url_hash=ruta, path=ruta, is_internal=True, status_code=200))
    s.commit()
    an = SEOAnalyzer(s, j.id)
    assert T.avisar(an) == 1
    s.commit()
    (i,) = s.execute(select(Issue.issue_type, Issue.details, Issue.group_key).where(Issue.job_id == j.id)).all()
    assert i[0] == "trampa_de_rastreo" and i[1]["trampa"] == "calendario_infinito" and i[1]["paginas_afectadas"] == 96
    assert i[2] == T._grupo("www.x.com", "/agenda/:fecha/:slug (3n)", "calendario_infinito"), \
        "casa entre censos por la plantilla, no por el ejemplo: la clave es fija"
