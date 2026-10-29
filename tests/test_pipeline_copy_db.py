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
    """Un valor que la tabla no admite (un `to_url` nulo) tumba el lote
    entero, como con el ORM: ni enlaces sueltos ni el DELETE previo."""
    Sesion, jid = bd
    _pasar(jid, [_pagina(jid, 0)] + [_enlace(jid, 0, k) for k in range(3)])
    _pasar(jid, [_pagina(jid, 0), _enlace(jid, 0, 7), _enlace(jid, 0, 8, to_url=None)])
    assert [f[2] for f in _filas(Sesion, jid)] == ["d0", "d1", "d2"]


def test_nulos_explicitos_y_textos_largos(bd):
    """Un None explicito recibe el default del modelo (como con el ORM), y un
    `target` o un `rel` mas largos que su columna se recortan en vez de tumbar
    el lote."""
    Sesion, jid = bd
    _pasar(jid, [_pagina(jid, 0),
                 _enlace(jid, 0, 1, follow=None, is_internal=None, link_type=None),
                 _enlace(jid, 0, 2, target="nombre_de_iframe_largo_xx", rel="r" * 200,
                         link_position="p" * 30)])
    filas = {f[2]: f for f in _filas(Sesion, jid)}
    assert (filas["d1"][5], filas["d1"][7], filas["d1"][10]) == (True, True, "hyperlink")
    assert filas["d2"][8] == "nombre_de_iframe_lar" and filas["d2"][4] == "r" * 128
    assert filas["d2"][6] == "p" * 20


def test_un_hijos_item_se_guarda_como_sus_filas_sueltas(bd):
    """Lo que emite el spider desde la 219: un `HijosItem` con todas las filas
    de un tipo. Se guarda igual que las filas sueltas, tambien con mas filas
    que un lote, y un lote que falla no se lleva mas que antes."""
    from seo_crawler.items import HijosItem

    Sesion, jid = bd
    filas = [dict(_enlace(jid, 0, k)) for k in range(450)]
    _pasar(jid, [_pagina(jid, 0), HijosItem(tipo="LinkItem", filas=filas)], batch_size=200)
    con_hijos = _filas(Sesion, jid)
    assert len(con_hijos) == 450
    _pasar(jid, [_pagina(jid, 0)] + [_enlace(jid, 0, k) for k in range(450)], batch_size=200)
    assert _filas(Sesion, jid) == con_hijos
    # Una fila que tumba su lote (to_url nulo) en la 3.a: se pierden su lote
    # de 200 y lo que quede sin confirmar, no las 450.
    malas = [dict(f) for f in filas]
    malas[2]["to_url"] = None
    _pasar(jid, [_pagina(jid, 1), HijosItem(tipo="LinkItem", filas=[
        {**f, "from_url_hash": "h1"} for f in malas])], batch_size=200)
    from shared.models import Link, Url

    with Sesion() as s:
        guardadas = (s.query(Link).join(Url, Url.id == Link.from_url_id)
                     .filter(Link.job_id == jid, Url.url_hash == "h1").count())
    assert guardadas == 250


def test_textos_de_la_pagina_mas_largos_que_su_columna(bd):
    """`schema_type` (varios tipos juntos) y el `lang` del hreflang los escribe
    la pagina: se recortan en vez de tumbar el lote."""
    from seo_crawler.items import HijosItem, HreflangItem
    from shared.models import Hreflang, StructuredData, Url

    Sesion, jid = bd
    _pasar(jid, [_pagina(jid, 0),
                 HijosItem(tipo="StructuredDataItem", filas=[dict(
                     url_hash="h0", job_id=str(jid), raw={"@type": "x"}, format="jsonld",
                     schema_type="T" * 150, json_invalido=None)]),
                 HreflangItem(url_hash="h0", job_id=str(jid), lang="es-ES-xxxxxxxxxxxxxxxxxxxxx",
                              href="https://x.com/es", en_body=False, origen="html")])
    with Sesion() as s:
        uid = s.query(Url.id).filter(Url.job_id == jid, Url.url_hash == "h0").scalar()
        assert s.query(StructuredData.schema_type).filter(StructuredData.url_id == uid).scalar() == "T" * 128
        assert s.query(Hreflang.lang).filter(Hreflang.url_id == uid).scalar() == "es-ES-xxxxxxxxxxxxxx"


def _hijos_raros(jid):
    """Una pagina con encabezados, hreflang, datos estructurados y recursos de
    textos raros, nulos y JSON de todo tipo (decision 222)."""
    from seo_crawler.items import HeadingItem, HreflangItem, ResourceItem, StructuredDataItem

    base = dict(url_hash="h0", job_id=str(jid))
    items = [_pagina(jid, 0)]
    for k, texto in enumerate(RAROS):
        items.append(HeadingItem(**base, tag=f"h{k % 6 + 1}", position=k,
                                 text=texto, oculto=[None, True, False][k % 3]))
        items.append(ResourceItem(**base, resource_url=f"https://x.com/r/{k}.png",
                                  resource_type="image", alt_text=texto,
                                  width=k or None, height=None if k % 2 else 10 * k,
                                  is_mixed_content=bool(k % 2)))
        items.append(HreflangItem(**base, lang=f"es-{k}", href=f"https://x.com/{k}",
                                  en_body=[None, True, False][k % 3],
                                  origen="html" if k % 2 else None))
    raws = [{"@type": "Product", "name": "ñ 🙂 \t \\ \"comillas\"", "n": [1, 2.5, None]},
            [{"@id": "_:b0"}], "texto suelto", 7, None, {}]
    for k, raw in enumerate(raws):
        items.append(StructuredDataItem(**base, raw=raw, format="jsonld",
                                        schema_type=f"T{k}", json_invalido="roto" if k == 4 else None))
    return items


def _filas_hijos(Sesion, jid):
    from sqlalchemy import text

    with Sesion() as s:
        consultas = {
            "headings": "select tag, position, text, oculto from headings",
            "resources": "select resource_url, resource_type, alt_text, width, height,"
                         " is_mixed_content, size_bytes from resources",
            "hreflang": "select lang, href, en_body, origen, return_tag_ok, lang_valid from hreflang",
            "structured_data": "select raw::text, json_typeof(raw), raw is null, format,"
                               " schema_type, json_invalido, validation_status from structured_data",
        }
        return {t: sorted(map(tuple, s.execute(text(
            f"{sql} x where x.url_id in (select id from urls where job_id = :j)"),
            {"j": str(jid)})), key=repr)
            for t, sql in consultas.items()}


def test_los_demas_hijos_con_copy_y_sin_copy_igual(bd, monkeypatch):
    """Encabezados, recursos, hreflang y datos estructurados por COPY guardan
    lo mismo que por el ORM (sin el NUL, que el ORM no puede escribir): el
    texto JSON identico y un `raw` None como JSON `null`, no NULL de SQL."""
    import seo_crawler.pipelines as pipelines

    Sesion, jid = bd

    def sin_nul(it):
        return not any("\x00" in v for v in dict(it).values() if isinstance(v, str))

    items = [it for it in _hijos_raros(jid) if sin_nul(it)]
    _pasar(jid, items, batch_size=7)
    con_copy = _filas_hijos(Sesion, jid)
    assert len(con_copy["headings"]) == len(RAROS) - 1
    assert len(con_copy["structured_data"]) == 6
    nulo = [f for f in con_copy["structured_data"] if f[4] == "T4"][0]
    assert nulo[1:3] == ("null", False)
    with Sesion() as s:
        from shared.models import Url
        s.query(Url).filter(Url.job_id == jid).delete()
        s.commit()
    monkeypatch.setattr(pipelines, "_copiar", lambda *a, **k: False)
    _pasar(jid, items, batch_size=7)
    assert _filas_hijos(Sesion, jid) == con_copy


def test_los_demas_hijos_con_nul_y_re_rastreo(bd):
    """El NUL se quita en vez de tumbar el lote, y re-rastrear la pagina
    sustituye sus hijos."""
    Sesion, jid = bd
    _pasar(jid, _hijos_raros(jid))
    filas = _filas_hijos(Sesion, jid)
    assert len(filas["headings"]) == len(RAROS)
    assert ("h4", 9, "nuldentro", None) in filas["headings"]
    _pasar(jid, _hijos_raros(jid))
    assert _filas_hijos(Sesion, jid) == filas
