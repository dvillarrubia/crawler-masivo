"""Los enlaces se escriben con COPY y no con objetos del ORM (#284): mismo
contenido, la mitad de CPU en el pipeline.

Contra Postgres de verdad (`PAGERANK_TEST_DATABASE_URL`, nunca `crawler_db`):
el formato de texto de COPY tiene sus propios escapes, y un fallo ahi cambia
las anclas sin dar error.
"""

from __future__ import annotations

import os
import types
import uuid

import pytest

pytest.importorskip("scrapy")

URL_BD = os.environ.get("PAGERANK_TEST_DATABASE_URL")

RAROS = [
    "con\ttabulador", "con\nsalto y\r\nretorno", "barra \\ invertida", "\\N",
    "\\\\N", "termina en barra\\", "emoji 🙂 y ñ", "", None, "nul\x00dentro",
    "comillas \" y ' y ;", "\\t literal",
]


@pytest.fixture()
def bd(monkeypatch):
    if not URL_BD:
        pytest.skip("sin PAGERANK_TEST_DATABASE_URL")
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    import shared.database
    from shared.models import Base, Job

    engine = create_engine(URL_BD)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    monkeypatch.setattr(shared.database, "SessionLocal", Sesion)
    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="test copy de enlaces", status="running", seeds=[], config={}))
        s.commit()
    yield Sesion, jid
    with engine.begin() as c:
        for tabla in ("links", "urls", "jobs"):
            col = "id" if tabla == "jobs" else "job_id"
            c.execute(text(f"DELETE FROM {tabla} WHERE {col} = :j"), {"j": str(jid)})
    engine.dispose()


def _pagina(jid, i):
    from seo_crawler.items import PageItem

    return PageItem(url=f"https://x.com/p/{i}", url_hash=f"h{i}", job_id=str(jid),
                    host="x.com", path=f"/p/{i}", scheme="https", is_internal=True,
                    crawl_depth=1, status_code=200, status_group="2xx",
                    is_html=True, resource_type="html")


def _enlace(jid, i, k, **extra):
    from seo_crawler.items import LinkItem

    datos = dict(from_url_hash=f"h{i}", to_url=f"https://x.com/d/{k}", to_url_hash=f"d{k}",
                 is_internal=True, link_position="content", job_id=str(jid), follow=True,
                 repeticiones=1)
    datos.update(extra)
    return LinkItem(**datos)


def _pasar(jid, items, batch_size=200):
    from seo_crawler.pipelines import PostgresPipeline

    spider = types.SimpleNamespace(job_id=str(jid))
    tub = PostgresPipeline(batch_size=batch_size, commit_every=50)
    tub.open_spider(spider)
    for it in items:
        tub.process_item(it, spider)
    tub.close_spider(spider)


def _filas(Sesion, jid):
    from shared.models import Link, Url

    with Sesion() as s:
        return sorted(
            (u, l.to_url, l.to_url_hash, l.anchor_text, l.rel, l.is_internal, l.link_position,
             l.follow, l.target, l.alt_text, l.link_type, l.repeticiones)
            for l, u in s.query(Link, Url.url_hash).join(Url, Url.id == Link.from_url_id)
            .filter(Link.job_id == jid)
        )


def _items_raros(jid):
    items = [_pagina(jid, 0)]
    for k, texto in enumerate(RAROS):
        items.append(_enlace(jid, 0, k, anchor_text=texto, alt_text=texto,
                             to_url=f"https://x.com/d/{k}?q={texto or ''}"[:200].replace("\x00", ""),
                             rel="nofollow ugc" if k % 2 else None, follow=bool(k % 3),
                             is_internal=bool(k % 4), target="_blank" if k % 5 else None,
                             link_type="image" if k % 2 else "hyperlink", repeticiones=k + 1))
    return items


def _esperado(texto):
    return None if texto is None else texto.replace("\x00", "")


def test_los_textos_raros_llegan_tal_cual(bd):
    Sesion, jid = bd
    _pasar(jid, _items_raros(jid))
    filas = _filas(Sesion, jid)
    assert len(filas) == len(RAROS)
    por_hash = {f[2]: f for f in filas}
    for k, texto in enumerate(RAROS):
        fila = por_hash[f"d{k}"]
        assert fila[3] == _esperado(texto) and fila[9] == _esperado(texto), (k, texto, fila)
        assert fila[4] == ("nofollow ugc" if k % 2 else None)
        assert (fila[5], fila[7], fila[11]) == (bool(k % 4), bool(k % 3), k + 1)
        assert fila[8] == ("_blank" if k % 5 else None)
        assert fila[10] == ("image" if k % 2 else "hyperlink")


def test_sin_copy_se_escribe_lo_mismo(bd, monkeypatch):
    """El camino de otras BD (bulk_insert_mappings) da las mismas filas; aqui
    se fuerza en Postgres, sin el NUL, que el ORM no puede escribir."""
    import seo_crawler.pipelines as pipelines

    Sesion, jid = bd
    items = [it for it in _items_raros(jid)
             if "\x00" not in (it.get("anchor_text") or "")]
    _pasar(jid, items)
    con_copy = _filas(Sesion, jid)
    with Sesion() as s:
        from shared.models import Link, Url
        s.query(Link).filter(Link.job_id == jid).delete()
        s.query(Url).filter(Url.job_id == jid).delete()
        s.commit()
    monkeypatch.setattr(pipelines, "_copiar", lambda *a, **k: False)
    _pasar(jid, items)
    assert _filas(Sesion, jid) == con_copy


def test_una_pagina_en_varios_lotes_y_re_rastrearla(bd):
    """Los lotes de una misma pagina se suman (no se borran entre si) y
    re-rastrearla sustituye sus enlaces, como con el ORM."""
    Sesion, jid = bd
    _pasar(jid, [_pagina(jid, 0)] + [_enlace(jid, 0, k) for k in range(450)], batch_size=200)
    assert len(_filas(Sesion, jid)) == 450
    _pasar(jid, [_pagina(jid, 0)] + [_enlace(jid, 0, k) for k in range(3)], batch_size=200)
    assert len(_filas(Sesion, jid)) == 3


def test_un_fallo_del_copy_no_deja_el_lote_a_medias(bd, monkeypatch):
    """Un valor que la tabla no admite (rel de mas de 128) tumba el lote
    entero, como con el ORM: ni enlaces sueltos ni el DELETE previo."""
    Sesion, jid = bd
    _pasar(jid, [_pagina(jid, 0)] + [_enlace(jid, 0, k) for k in range(3)])
    _pasar(jid, [_pagina(jid, 0), _enlace(jid, 0, 7), _enlace(jid, 0, 8, rel="x" * 200)])
    assert [f[2] for f in _filas(Sesion, jid)] == ["d0", "d1", "d2"]
