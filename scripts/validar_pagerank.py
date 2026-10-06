"""Saca el PageRank de un rastreo en forma revisable a mano.

Es el criterio de aceptacion que pide #24: "top 50 por PageRank, revisado a
mano, mas el porcentaje de PageRank desperdiciado". Revisar a mano significa
mirar si las paginas de arriba son las que un SEO esperaria arriba; eso no lo
decide un test.

    docker compose exec -T crawler python scripts/validar_pagerank.py <job_id>
    docker compose exec -T crawler python scripts/validar_pagerank.py <job_id> --top 20

Lo que hay que mirar en la salida:

- **Arriba**: portada, categorias principales, paginas de servicio. Si ahi hay
  paginas legales o una ficha cualquiera, mirar de donde le llega el enlace:
  normalmente es un enlace de plantilla que el peso por repeticion deberia
  haber bajado (ver decision 29 del CLAUDE.md, el caso re-magazine).
- **Desperdiciado**: cuanto PageRank acaba en errores y redirecciones. Un
  numero alto es una recomendacion directa: arreglar esos enlaces.
- **grafo_fiable=False**: hay plantillas con enlaces solo en JavaScript. Las
  cifras salen de un grafo incompleto y no se entregan sin avisar.
"""

from __future__ import annotations

import argparse
import os
import sys


def _preparar_rutas() -> None:
    for base in (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "/app"):
        if os.path.isdir(os.path.join(base, "shared")) and base not in sys.path:
            sys.path.insert(0, base)


_preparar_rutas()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--top", type=int, default=50)
    args = ap.parse_args()

    from sqlalchemy import func, select

    from shared.database import SessionLocal
    from shared.models import Job, Url

    s = SessionLocal()
    try:
        job = s.query(Job).filter(Job.id == args.job_id).one_or_none()
        if job is None:
            sys.exit(f"no existe el job {args.job_id}")
        resumen = job.pagerank_resumen or {}
        n = s.execute(select(func.count(Url.id)).where(Url.job_id == job.id)).scalar() or 0

        print(f"\n{job.name}")
        print(f"{'-' * 78}")
        print(f"URLs: {n:,} | nodos del grafo: {resumen.get('nodos', '?'):,} | "
              f"aristas: {resumen.get('aristas', 0):,}")
        print(f"peso de los enlaces: {resumen.get('peso', '?')}")
        reparto = resumen.get("reparto") or {}
        if reparto:
            print("reparto del PageRank:")
            for clave, valor in sorted(reparto.items(), key=lambda kv: -kv[1]):
                print(f"   {clave:<22} {valor * 100:5.1f}%")
        desperdiciado = resumen.get("desperdiciado")
        if desperdiciado is not None:
            print(f"DESPERDICIADO en errores y redirecciones: {desperdiciado * 100:.1f}%")
        fiable = resumen.get("grafo_fiable")
        if fiable is False:
            print("\n  *** AVISO: " + (resumen.get("aviso") or "grafo incompleto"))
            for plantilla in resumen.get("plantillas_con_enlaces_js") or []:
                print(f"      plantilla con enlaces solo en JS: {plantilla}")
        elif fiable is None:
            print("\n  (no se comprobo si hay enlaces solo en JavaScript)")

        filas = s.execute(
            select(Url.pagerank_score, Url.pagerank_raw, Url.inlinks_count,
                   Url.unique_inlinks_count, Url.url, Url.indexable)
            .where(Url.job_id == job.id)
            .order_by(Url.pagerank_raw.desc().nullslast())
            .limit(args.top)
        ).all()

        print(f"\nTOP {args.top} por PageRank")
        print(f"{'#':>3} {'score':>5} {'xmedia':>7} {'inlinks':>8} {'unicos':>7}  url")
        for i, (score, crudo, inl, uniq, url, indexable) in enumerate(filas, 1):
            veces = (crudo or 0) * n
            marca = "" if indexable else "  [no indexable]"
            print(f"{i:>3} {score or 0:>5} {veces:>7.1f} {inl or 0:>8} {uniq or 0:>7}  "
                  f"{url[:72]}{marca}")
        print("\nxmedia = veces la pagina media del sitio. Comparable entre sitios.")
    finally:
        s.close()


if __name__ == "__main__":
    main()
