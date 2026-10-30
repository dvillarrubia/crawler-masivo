"""Cuanto tarda cada fase del analisis en un censo sintetico grande (#281).

Crea en un Postgres de PRUEBAS (nunca `crawler_db`) un censo con `--urls`
paginas en `--secciones` secciones, cada una con su meta, tres encabezados,
contenido y `--enlaces` enlaces (menu comun, su seccion y algunos al azar),
lo analiza con el `SEOAnalyzer` real y escribe los segundos de cada fase
(`jobs.analisis_fases`, decision 226), de mas a menos. Con `--borrar` quita
el censo al acabar.

    docker run --rm --network cm-test -v "$PWD":/repo -w /repo \\
      -e PYTHONPATH=/repo:/repo/crawler \\
      -e DATABASE_URL=postgresql+psycopg2://crawler:crawler@cm-test-pg:5432/crawler_bench \\
      crawler-masivo-crawler python scripts/bench/analisis_por_fases.py --urls 60000 --enlaces 200

Los datos se generan en SQL (`generate_series`): meter 12 millones de
enlaces desde Python costaria mas que el analisis que se quiere medir.
"""

from __future__ import annotations

import argparse
import os
import time
import uuid


def crear_censo(engine, jid, urls: int, enlaces: int, secciones: int) -> None:
    from sqlalchemy import text

    menu = min(60, enlaces // 3)
    de_seccion = min(enlaces - menu, 80)
    al_azar = max(0, enlaces - menu - de_seccion)
    with engine.begin() as c:
        c.execute(text("INSERT INTO jobs (id, name, status, seeds, config, created_at) VALUES "
                       "(:j, 'bench analisis', 'running', '[\"https://x.com/\"]', '{}', now())"),
                  {"j": str(jid)})
        # Paginas: la home (0), una portada por seccion y fichas. ~2 % 404 y
        # ~1 % redirecciones, y titulos repetidos cada 97 fichas. `mapa` da
        # la URL de cada indice, para enlazar sin buscar por ruta.
        c.execute(text("""
            CREATE TEMP TABLE mapa ON COMMIT DROP AS
            SELECT i, p.path, 'https://x.com' || p.path AS url, md5(:j || p.path) AS url_hash
              FROM generate_series(0, :n - 1) i,
                   LATERAL (SELECT CASE WHEN i = 0 THEN '/'
                                        WHEN i <= :s THEN '/s' || (i - 1) || '/'
                                        ELSE '/s' || (i % :s) || '/p' || i END AS path) p
        """), {"j": str(jid), "n": urls, "s": secciones})
        c.execute(text("ALTER TABLE mapa ADD PRIMARY KEY (i)"))
        c.execute(text("""
            INSERT INTO urls (job_id, url, url_hash, host, path, scheme, is_internal, crawl_depth,
                              status_code, status_group, is_html, resource_type, content_type,
                              word_count, content_word_count, url_length, folder_depth, html_bytes_servidos,
                              response_class, redirect_url, last_crawled_at)
            SELECT :j, m.url, m.url_hash, 'x.com', m.path, 'https', true,
                   CASE WHEN i = 0 THEN 0 WHEN i <= :s THEN 1 ELSE 2 + (i % 4) END,
                   CASE WHEN i % 50 = 7 THEN 404 WHEN i % 100 = 13 THEN 301 ELSE 200 END,
                   CASE WHEN i % 50 = 7 THEN '4xx' WHEN i % 100 = 13 THEN '3xx' ELSE '2xx' END,
                   NOT (i % 50 = 7 OR i % 100 = 13), 'html', 'text/html',
                   300 + (i % 700), 250 + (i % 600), length(m.path) + 13, 2, 40000, 'contenido',
                   CASE WHEN i % 100 = 13 THEN 'https://x.com/s' || (i % :s) || '/p' || (i + 1) END,
                   now()
              FROM mapa m ORDER BY i
        """), {"j": str(jid), "s": secciones})
        c.execute(text("""
            INSERT INTO html_meta (url_id, title, title_len, meta_description, meta_description_len,
                                   canonical_href, title_count, meta_description_count, canonical_count)
            SELECT u.id, 'Titulo ' || (u.id % (:n / 97 + 1)), 12,
                   'Descripcion de la pagina ' || u.id, 30, u.url, 1, 1, 1
              FROM urls u WHERE u.job_id = :j AND u.is_html
        """), {"j": str(jid), "n": urls})
        c.execute(text("""
            INSERT INTO headings (url_id, tag, position, text, oculto)
            SELECT u.id, t.tag, t.pos, t.tag || ' de ' || u.path, false
              FROM urls u, (VALUES ('h1', 0), ('h2', 1), ('h2', 2)) t(tag, pos)
             WHERE u.job_id = :j AND u.is_html
        """), {"j": str(jid)})
        c.execute(text("""
            INSERT INTO page_content (url_id, content_text, content_length)
            SELECT u.id, repeat('texto de la seccion ' || split_part(u.path, '/', 2) || ' ', 40)
                         || ' pagina ' || u.id, 900
              FROM urls u WHERE u.job_id = :j AND u.is_html
        """), {"j": str(jid)})
        # Enlaces: el menu (la home y las primeras portadas) en todas, su
        # seccion y unos cuantos al azar, desde las HTML.
        c.execute(text("""
            INSERT INTO links (job_id, from_url_id, to_url, to_url_hash, anchor_text, is_internal,
                               link_position, follow, link_type, repeticiones)
            SELECT :j, o.id, d.url, d.url_hash, 'ancla', true, x.pos, true, 'hyperlink', 1
              FROM (SELECT u.id, m.i FROM mapa m JOIN urls u ON u.job_id = :j AND u.url_hash = m.url_hash
                     WHERE u.is_html) o
              CROSS JOIN LATERAL (
                  SELECT 'nav' AS pos, k AS destino FROM generate_series(0, :menu - 1) k
                  UNION ALL
                  SELECT 'content', :s + 1 + ((o.i + k * 37) % (:n - :s - 1))
                    FROM generate_series(1, :sec) k
                  UNION ALL
                  SELECT 'content', (hashtext(o.i::text || '-' || k::text) & 2147483647) % :n
                    FROM generate_series(1, :azar) k
              ) x
              JOIN mapa d ON d.i = x.destino
        """), {"j": str(jid), "n": urls, "s": secciones, "menu": menu, "sec": de_seccion,
               "azar": al_azar})
        c.execute(text("UPDATE jobs SET status = 'analyzing', total_urls_crawled = :n WHERE id = :j"),
                  {"j": str(jid), "n": urls})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", type=int, default=20000)
    ap.add_argument("--enlaces", type=int, default=150)
    ap.add_argument("--secciones", type=int, default=50)
    ap.add_argument("--job", help="analizar este job ya creado en vez de crear otro")
    ap.add_argument("--borrar", action="store_true")
    args = ap.parse_args()
    bd = os.environ["DATABASE_URL"]
    assert "crawler_db" not in bd, "nunca contra la BD de trabajo"

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from shared.models import Base, Job

    engine = create_engine(bd)
    Base.metadata.create_all(engine)
    jid = uuid.UUID(args.job) if args.job else uuid.uuid4()
    if not args.job:
        t = time.perf_counter()
        crear_censo(engine, jid, args.urls, args.enlaces, args.secciones)
        with engine.connect() as c:
            n = c.execute(text("SELECT count(*) FROM links WHERE job_id = :j"), {"j": str(jid)}).scalar()
        print(f"censo {jid}: {args.urls} URLs, {n} enlaces, creado en {time.perf_counter() - t:.0f} s")

    from analysis.analyzer import SEOAnalyzer

    s = sessionmaker(bind=engine)()
    t = time.perf_counter()
    SEOAnalyzer(s, str(jid)).run_all()
    total = time.perf_counter() - t
    print(f"analisis: {total:.0f} s")
    fases = (getattr(s.get(Job, jid), "analisis_fases", None) or {}).get("fases") or []
    for nombre, seg in sorted(fases, key=lambda f: -f[1])[:15]:
        print(f"  {seg:8.1f} s  {100 * seg / total:5.1f} %  {nombre}")
    s.close()
    if args.borrar:
        with engine.begin() as c:
            for tabla in ("links", "issues", "urls"):
                c.execute(text(f"DELETE FROM {tabla} WHERE job_id = :j"), {"j": str(jid)})
            c.execute(text("DELETE FROM jobs WHERE id = :j"), {"j": str(jid)})


if __name__ == "__main__":
    main()
