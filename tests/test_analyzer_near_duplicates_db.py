"""analyze_near_duplicates contra una BD real (SQLite en memoria).

Los tests de `test_near_duplicates.py` cubren las funciones puras de MinHash,
pero no la parte que lee y escribe en la BD. Ahi habia dos fallos que tiraban
el analisis ENTERO de cada job sin que ningun test lo viese: `Select.yield_per`
(no existe en SQLAlchemy 2.x) y un UPDATE en lote con WHERE que SQLAlchemy
rechaza. El job quedaba `completed` sin issues ni PageRank.

Y el alcance (decision 118): solo internas, 200 HTML e indexables, como el
duplicado exacto; una variante canonicalizada no es casi duplicada de su
canonica.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from shared.models import Base, Issue, Job, PageContent, Url


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(tipo, compilador, **kw):
    # En SQLite solo INTEGER PRIMARY KEY se autoincrementa; BIGINT no.
    return "INTEGER"

TEXTO = " ".join(f"palabra{i}" for i in range(300))


@pytest.fixture()
def sesion():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[Job.__table__, Url.__table__, Issue.__table__, PageContent.__table__]
    )
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _pagina(s, job_id, uid, url, texto, *, indexable=True, is_internal=True,
            estado="Indexable", **extra):
    s.add(Url(id=uid, job_id=job_id, url=url, url_hash=f"h{uid}", is_html=True,
              status_code=200, indexable=indexable, is_internal=is_internal,
              indexability_status=estado, **extra))
    s.add(PageContent(url_id=uid, content_text=texto))


def _job(s):
    job_id = uuid.uuid4()
    s.add(Job(id=job_id, name="t", seeds=[], config={}))
    return job_id


def _casi(s, job_id):
    return {
        i.url_id: i for i in s.query(Issue).filter(
            Issue.job_id == job_id, Issue.issue_type == "near_duplicate_content")
    }


def test_analyze_near_duplicates_escribe_recuento_e_issues(sesion):
    from analysis.analyzer import SEOAnalyzer

    job_id = _job(sesion)
    textos = {
        1: TEXTO,
        2: TEXTO.replace("palabra150", "otra"),  # casi identica a la 1
        3: " ".join(f"distinta{i}" for i in range(300)),
    }
    for uid, texto in textos.items():
        _pagina(sesion, job_id, uid, f"https://e.com/{uid}", texto)
    sesion.commit()

    SEOAnalyzer(sesion, job_id).analyze_near_duplicates()
    sesion.commit()

    cuentas = dict(sesion.execute(select(Url.id, Url.near_duplicate_count)).all())
    assert cuentas[1] == 1 and cuentas[2] == 1
    assert not cuentas[3]
    tipos = [i.issue_type for i in sesion.query(Issue).all()]
    assert tipos.count("near_duplicate_content") == 2


def test_variantes_noindex_y_externas_no_son_casi_duplicadas(sesion):
    """El caso de la issue #149: cuatro URLs con el mismo texto.

    Solo una es indexable e interna: no tiene con quien compararse, asi que se
    mide (0) y no sale ningun aviso. Antes salian cuatro, y el de `/p` citaba
    a su variante, a su paginacion y a la externa.
    """
    from analysis.analyzer import SEOAnalyzer

    job_id = _job(sesion)
    _pagina(sesion, job_id, 1, "https://e.com/p", TEXTO)
    _pagina(sesion, job_id, 2, "https://e.com/p?orden=precio", TEXTO,
            indexable=False, estado="Canonicalised")
    _pagina(sesion, job_id, 3, "https://e.com/p?page=2", TEXTO,
            indexable=False, estado="Noindex")
    _pagina(sesion, job_id, 4, "https://otro.com/p", TEXTO, is_internal=False)
    sesion.commit()

    SEOAnalyzer(sesion, job_id).analyze_near_duplicates()
    sesion.commit()

    assert _casi(sesion, job_id) == {}
    cuentas = dict(sesion.execute(select(Url.id, Url.near_duplicate_count)).all())
    # La indexable se midio y no tiene ninguna: 0. Las demas no se miden: NULL.
    assert cuentas == {1: 0, 2: None, 3: None, 4: None}


def test_la_canonicalizada_no_aparece_entre_las_parecidas(sesion):
    from analysis.analyzer import SEOAnalyzer

    job_id = _job(sesion)
    _pagina(sesion, job_id, 1, "https://e.com/a", TEXTO)
    _pagina(sesion, job_id, 2, "https://e.com/b", TEXTO.replace("palabra150", "otra"))
    _pagina(sesion, job_id, 3, "https://e.com/a?utm_source=x", TEXTO,
            indexable=False, estado="Canonicalised")
    sesion.commit()

    SEOAnalyzer(sesion, job_id).analyze_near_duplicates()
    sesion.commit()

    avisos = _casi(sesion, job_id)
    assert set(avisos) == {1, 2}
    for aviso in avisos.values():
        assert aviso.details["count"] == 1
        assert [m["url"] for m in aviso.details["matches"]] != []
        assert all("utm_source" not in m["url"] for m in aviso.details["matches"])


def test_reanalizar_deja_en_null_lo_que_ya_no_se_mide(sesion):
    """Una pagina medida en un analisis anterior que ahora es noindex.

    `_bulk_update_near_duplicates` solo escribe las que tienen firma: sin la
    limpieza, la noindex conservaba `near_duplicate_count=3`.
    """
    from analysis.analyzer import SEOAnalyzer

    job_id = _job(sesion)
    _pagina(sesion, job_id, 1, "https://e.com/a", TEXTO)
    _pagina(sesion, job_id, 2, "https://e.com/b", " ".join(f"otra{i}" for i in range(300)))
    _pagina(sesion, job_id, 3, "https://e.com/c", TEXTO, indexable=False,
            estado="Noindex", near_duplicate_count=3, closest_similarity=0.97)
    # Y una cuyo texto ya no llega al minimo.
    _pagina(sesion, job_id, 4, "https://e.com/d", "poco texto", near_duplicate_count=1,
            closest_similarity=0.95)
    sesion.commit()

    SEOAnalyzer(sesion, job_id).analyze_near_duplicates()
    sesion.commit()

    filas = {
        uid: (n, c) for uid, n, c in sesion.execute(
            select(Url.id, Url.near_duplicate_count, Url.closest_similarity))
    }
    assert filas == {1: (0, None), 2: (0, None), 3: (None, None), 4: (None, None)}
    assert _casi(sesion, job_id) == {}


def _frases_ja(n: int, semilla: int) -> list[str]:
    import random

    silabas = list("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん")
    kanji = list("日本東京大学研究会社経済政治文化歴史地域情報技術環境教育医療産業")
    rnd = random.Random(semilla)
    return [
        "".join(rnd.choice(kanji if rnd.random() < 0.4 else silabas) for _ in range(20)) + "。"
        for _ in range(n)
    ]


def test_dos_paginas_japonesas_casi_iguales_dan_dos_avisos(sesion):
    from analysis.analyzer import SEOAnalyzer

    frases = _frases_ja(40, 1)
    casi = list(frases)
    for i in (5, 17, 31):
        casi[i] = casi[i][:8] + "経済" + casi[i][10:]
    job_id = _job(sesion)
    _pagina(sesion, job_id, 1, "https://e.jp/a", "".join(frases))
    _pagina(sesion, job_id, 2, "https://e.jp/b", "".join(casi))
    _pagina(sesion, job_id, 3, "https://e.jp/c", "".join(_frases_ja(40, 2)))
    sesion.commit()

    SEOAnalyzer(sesion, job_id).analyze_near_duplicates()
    sesion.commit()

    assert set(_casi(sesion, job_id)) == {1, 2}
    cuentas = dict(sesion.execute(select(Url.id, Url.near_duplicate_count)).all())
    assert cuentas == {1: 1, 2: 1, 3: 0}


def test_el_dry_run_del_script_mide_lo_mismo_que_el_analisis(sesion, monkeypatch, capsys):
    """`scripts/near_duplicates.py --dry-run` tenia su propia consulta (sin el
    alcance) y llamaba a `Select.yield_per`, que en SQLAlchemy 2.x no existe."""
    import scripts.near_duplicates as script

    job_id = _job(sesion)
    _pagina(sesion, job_id, 1, "https://e.com/a", TEXTO)
    _pagina(sesion, job_id, 2, "https://e.com/a?orden=precio", TEXTO,
            indexable=False, estado="Canonicalised")
    _pagina(sesion, job_id, 3, "https://e.com/b", TEXTO.replace("palabra7", "otra"))
    sesion.commit()
    monkeypatch.setattr(script, "SessionLocal", lambda: sesion)
    monkeypatch.setattr(sesion, "close", lambda: None)

    script.procesar(job_id, 0.9, dry_run=True)

    salida = capsys.readouterr().out
    assert "2 con texto medible" in salida
    assert "2 con casi duplicadas" in salida
    assert _casi(sesion, job_id) == {}  # dry-run: no escribe
