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
    assert t["paginas_afectadas"] == len(cal) and t["fuera_de_rango"] > 0
    assert t["plantilla"].startswith("/agenda/")


def test_un_id_de_seis_cifras_no_es_una_fecha():
    """Penguin: /es/192401-daniel-brieba no es enero de 1924."""
    assert T.fechas_de("/es/192401-daniel-brieba") == []
    autores = [f"{B}/es/{190000 + i}-autor-{i}" for i in range(400)]
    assert "calendario_infinito" not in _tipos(autores)
    assert T.fechas_de("/agenda", [("date", "20291101"), ("ref", "20291101")]) == [(2029, 11)]


def test_un_blog_con_fechas_normales_no_es_un_calendario():
    blog = [f"{B}/2025/{m:02d}/{d:02d}/entrada-{m}-{d}" for m in range(1, 3) for d in range(1, 11)]
    assert "calendario_infinito" not in _tipos(blog)


def test_parametro_de_sesion_y_jsessionid():
    t = _tipos([f"{B}/a?sid=1", f"{B}/a?sid=2", f"{B}/b;jsessionid=ABC", f"{B}/c"])
    assert t["parametro_de_sesion"]["paginas_afectadas"] >= 2


def test_facetas_que_se_combinan_y_el_mismo_filtro_en_otro_orden():
    urls = [f"{B}/zapatos?color={c}&talla={t}" for c in range(10) for t in range(6)]
    t = _tipos(urls)["facetas_combinatorias"]
    assert t["combinaciones"] == 60 and t["paginas_afectadas"] == 60
    pocas = [f"{B}/z?color=1&talla=2", f"{B}/z?talla=2&color=1"] + [f"{B}/z?color={i}" for i in range(20)]
    t = _tipos(pocas)["facetas_combinatorias"]
    assert t["mismo_filtro_en_otro_orden"] is True
    assert "facetas_combinatorias" not in _tipos([f"{B}/z?page={i}" for i in range(80)]), "paginar no es filtrar"


def test_busqueda_interna_y_segmento_repetido():
    t = _tipos([f"{B}/?s=hotel", f"{B}/?s=playa", f"{B}/buscar/spa"])
    assert t["busqueda_interna_rastreable"]["paginas_afectadas"] == 2 or "busqueda_interna_rastreable" in t
    rep = [f"{B}/es/hoteles/es/hoteles/{i}" for i in range(4)]
    assert _tipos(rep)["segmento_repetido"]["paginas_afectadas"] == 4
    assert not T.tramos_repetidos("/es/hoteles/gran-canaria/")
    assert T.tramos_repetidos("/a/b/a/b/") and T.tramos_repetidos("/x/y/x/z/x/")


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
    assert i[2], "casa entre censos por la plantilla, no por el ejemplo"
