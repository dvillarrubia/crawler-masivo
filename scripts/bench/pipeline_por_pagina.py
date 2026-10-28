"""Coste por pagina de `parse` y del `PostgresPipeline` (#284).

Parsea una vez cada pagina de `tests/fixtures/paginas/` con el `SeoSpider`
real y pasa sus items por el `PostgresPipeline` real N veces, cada vez como
una pagina distinta (cambiando los hashes), contra un Postgres de PRUEBAS
(nunca crawler_db: crea y borra un job propio). Mide ms de CPU de proceso y
de reloj por pagina en el pipeline, y cuenta las filas que quedan.

    docker run --rm --network cm-test -v "$PWD":/repo -w /repo \\
      -e PYTHONPATH=/repo:/repo/crawler:/repo/tests \\
      -e BD=postgresql+psycopg2://crawler:crawler@cm-test-pg:5432/crawler_test \\
      crawler-masivo-crawler python scripts/bench/pipeline_por_pagina.py --veces 200
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import time
import types
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def _items_de(SeoSpider, url: str, cuerpo: bytes) -> list:
    from scrapy.http import HtmlResponse, Request

    sp = SeoSpider(job_id="bench")
    sp._allowed_resource_types = {"html", "redirect"}
    sp._crawled_count = 0
    sp.allowed_hosts = {"x.com"}
    sp._crawl_subdomains = False
    sp.max_urls = None
    sp.max_depth = 10
    sp._max_url_length = 0
    sp._max_folder_depth = 0
    sp._follow_nofollow = False
    sp.render_js = False
    sp._extraction = {"extract_page_content": True}
    r = HtmlResponse(url, status=200, body=cuerpo,
                     headers={b"Content-Type": b"text/html; charset=utf-8"},
                     request=Request(url, meta={"depth": 1, "download_latency": 0.01}))
    t = time.process_time()
    salida = [i for i in sp.parse(r) if not isinstance(i, Request)]
    return salida, (time.process_time() - t) * 1000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--veces", type=int, default=200)
    args = ap.parse_args()
    bd = os.environ["BD"]
    assert "crawler_db" not in bd, "nunca contra la BD de trabajo"

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    import shared.database
    from shared.models import Base, Job

    engine = create_engine(bd)
    Base.metadata.create_all(engine)
    Sesion = sessionmaker(bind=engine)
    shared.database.SessionLocal = Sesion
    from seo_crawler.pipelines import PostgresPipeline
    from seo_crawler.spiders.seo_spider import SeoSpider

    paginas = []
    for f in sorted((RAIZ / "tests/fixtures/paginas").glob("*.html.gz")):
        url = f"https://x.com/{f.name.split('.')[0]}"
        items, ms = _items_de(SeoSpider, url, gzip.decompress(f.read_bytes()))
        paginas.append((f.name, items, ms))

    jid = uuid.uuid4()
    with Sesion() as s:
        s.add(Job(id=jid, name="bench pipeline", status="running", seeds=[], config={}))
        s.commit()
    spider = types.SimpleNamespace(job_id=str(jid))
    try:
        for nombre, items, ms_parse in paginas:
            pipe = PostgresPipeline()
            pipe.open_spider(spider)
            cpu0, reloj0 = time.process_time(), time.perf_counter()
            for n in range(args.veces):
                sal = hashlib.sha256(f"{nombre}-{n}".encode()).hexdigest()
                def _renombrar(d):
                    for campo in ("url_hash", "from_url_hash"):
                        if d.get(campo):
                            d[campo] = hashlib.sha256((d[campo] + sal).encode()).hexdigest()
                    if "job_id" in d:
                        d["job_id"] = str(jid)

                for it in items:
                    copia = type(it)(it)
                    if type(copia).__name__ == "HijosItem":
                        copia["filas"] = [dict(f) for f in copia["filas"]]
                        for f in copia["filas"]:
                            _renombrar(f)
                    else:
                        _renombrar(copia)
                        if "job_id" in copia.fields:
                            copia["job_id"] = str(jid)
                    if copia.get("url") and type(copia).__name__ == "PageItem":
                        copia["url"] = f"{copia['url']}?n={n}"
                    pipe.process_item(copia, spider)
            pipe.close_spider(spider)
            cpu = (time.process_time() - cpu0) * 1000 / args.veces
            reloj = (time.perf_counter() - reloj0) * 1000 / args.veces
            from seo_crawler.items import desplegar

            tipos = {}
            for it in desplegar(items):
                tipos[type(it).__name__] = tipos.get(type(it).__name__, 0) + 1
            print(f"{nombre}: parse {ms_parse:.0f} ms | pipeline cpu {cpu:.1f} ms, reloj {reloj:.1f} ms "
                  f"por pagina | items {sum(tipos.values())} (enlaces {tipos.get('LinkItem', 0)})")
        with engine.connect() as c:
            filas = {t: c.execute(text(
                f"select count(*) from {t} x join urls u on u.id = x.{col} where u.job_id = :j"),
                {"j": str(jid)}).scalar()
                for t, col in (("links", "from_url_id"), ("headings", "url_id"),
                               ("resources", "url_id"), ("structured_data", "url_id"),
                               ("hreflang", "url_id"))}
            filas["urls"] = c.execute(text("select count(*) from urls where job_id = :j"),
                                      {"j": str(jid)}).scalar()
            # Independiente del orden de insercion: el md5 de cada fila, ordenados.
            firma = c.execute(text(
                "select md5(string_agg(h, ',' order by h)) from (select md5(row("
                " l.to_url, l.to_url_hash, l.anchor_text, l.rel, l.is_internal, l.link_position,"
                " l.follow, l.target, l.alt_text, l.link_type, l.repeticiones, u.url_hash)::text) h from links l"
                " join urls u on u.id = l.from_url_id where l.job_id = :j) x"),
                {"j": str(jid)}).scalar()
        print("filas:", filas, "firma de enlaces:", firma)
    finally:
        with engine.begin() as c:
            c.execute(text("delete from jobs where id = :j"), {"j": str(jid)})
        engine.dispose()


if __name__ == "__main__":
    main()
