"""Calcula el contenido casi duplicado de un censo YA rastreado.

`run_analysis` borra todas las incidencias del job y las recalcula de cero, lo
que sobre un censo entregado es mas de lo que se quiere para anadir una
comprobacion nueva. Este script hace solo la parte de casi duplicados: lee
`page_content`, calcula las firmas, rellena `urls.near_duplicate_count` y
`urls.closest_similarity`, y reescribe unicamente las incidencias
`near_duplicate_content`. Lo demas no se toca.

No re-rastrea nada: todo sale del texto que ya esta en la base de datos.

Uso (dentro del contenedor del crawler):

    python scripts/near_duplicates.py <job_id> [<job_id> ...]
    python scripts/near_duplicates.py <job_id> --umbral 0.85
    python scripts/near_duplicates.py <job_id> --dry-run
    python scripts/near_duplicates.py --todos --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

from sqlalchemy import delete, func, select

from analysis.analyzer import SEOAnalyzer
from analysis import near_duplicates as nd
from shared.database import SessionLocal
from shared.models import Issue, Job, Url


def procesar(job_id: str, umbral: float, dry_run: bool) -> None:
    sesion = SessionLocal()
    try:
        job = sesion.query(Job).filter(Job.id == job_id).one_or_none()
        if job is None:
            print(f"  [{job_id}] no existe")
            return

        total = sesion.execute(
            select(func.count(Url.id)).where(Url.job_id == job_id)
        ).scalar_one()

        analizador = SEOAnalyzer(sesion, job_id)
        analizador.near_duplicate_similarity = umbral

        if dry_run:
            # Mismo recorrido que el analyzer pero sin escribir: interesa saber
            # cuantas paginas saldrian marcadas antes de tocar un censo entregado.
            from shared.models import PageContent

            filas = sesion.execute(
                select(Url.id, Url.url, PageContent.content_text)
                .join(PageContent, PageContent.url_id == Url.id)
                .where(
                    Url.job_id == job_id,
                    Url.is_html.is_(True),
                    Url.status_code == 200,
                )
                .yield_per(500)
            )
            firmas = {}
            for url_id, _url, texto in filas:
                f = nd.firma(texto)
                if f is not None:
                    firmas[url_id] = f

            resultado = nd.analizar(firmas, umbral) if len(firmas) > 1 else {}
            tramos = Counter()
            for dato in resultado.values():
                tramos["identicas" if dato["closest"] >= 0.999 else "casi"] += 1
            print(
                f"  [{job_id}] {job.name or ''}\n"
                f"    {total} URLs, {len(firmas)} con texto medible\n"
                f"    {len(resultado)} con casi duplicadas "
                f"({tramos['identicas']} identicas, {tramos['casi']} parecidas) "
                f"— umbral {umbral}  [dry-run, no se escribe]"
            )
            return

        # Solo las incidencias de esta comprobacion; el resto del informe queda.
        borradas = sesion.execute(
            delete(Issue).where(
                Issue.job_id == job_id,
                Issue.issue_type == "near_duplicate_content",
            )
        ).rowcount
        sesion.flush()

        analizador.analyze_near_duplicates()
        analizador._flush_issues()
        sesion.commit()

        marcadas = sesion.execute(
            select(func.count(Url.id)).where(
                Url.job_id == job_id, Url.near_duplicate_count > 0
            )
        ).scalar_one()
        print(
            f"  [{job_id}] {job.name or ''}\n"
            f"    {total} URLs — {marcadas} con casi duplicadas "
            f"(umbral {umbral}; {borradas} incidencias previas reemplazadas)"
        )
    finally:
        sesion.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("job_ids", nargs="*", help="uno o varios job_id")
    p.add_argument("--todos", action="store_true", help="todos los jobs completados")
    p.add_argument(
        "--umbral",
        type=float,
        default=nd.UMBRAL_SIMILITUD,
        help=f"similitud minima para considerar casi duplicado (por defecto {nd.UMBRAL_SIMILITUD})",
    )
    p.add_argument("--dry-run", action="store_true", help="contar sin escribir")
    args = p.parse_args()

    if not 0 < args.umbral <= 1:
        p.error("--umbral debe estar entre 0 y 1")

    job_ids = list(args.job_ids)
    if args.todos:
        sesion = SessionLocal()
        try:
            job_ids += [
                str(j.id)
                for j in sesion.query(Job).filter(Job.status == "completed").all()
            ]
        finally:
            sesion.close()

    if not job_ids:
        p.error("hace falta un job_id, o --todos")

    print(f"Casi duplicados — umbral {args.umbral}")
    for job_id in dict.fromkeys(job_ids):
        procesar(job_id, args.umbral, args.dry_run)


if __name__ == "__main__":
    main()
