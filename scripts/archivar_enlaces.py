"""Archiva en Parquet los enlaces de los censos superados (decision 95).

Por defecto NO escribe: lista que censos se archivarian y cuantas filas de
`links` liberarian. Con `--aplicar` los archiva, uno a uno: fichero, comprobacion
contra la BD y, solo si cuadra, borrado de sus filas en la misma transaccion que
marca el job. `--restaurar <job_id>` los devuelve a `links` (hace falta para
volver a analizar un censo archivado).

Que se archiva lo decide `shared.archivo_enlaces.candidatos`: un censo para el
que hay otro completado, mas nuevo y del mismo host que abarca sus semillas, que
no es el ultimo de su programacion y que termino hace mas de
`ARCHIVAR_DIAS_MINIMOS` dias (14). Uno que todavia pueda ser la referencia de
una comparacion no se toca.

Como con `compactar_enlaces.py`, Postgres no devuelve el espacio al disco: lo
deja libre dentro de la tabla para los rastreos siguientes. Para encoger el
fichero hace falta `VACUUM FULL links` en una ventana sin rastreos.

Uso (dentro del contenedor del crawler):

    python scripts/archivar_enlaces.py
    python scripts/archivar_enlaces.py --aplicar
    python scripts/archivar_enlaces.py --aplicar <job_id> [<job_id> ...]
    python scripts/archivar_enlaces.py --restaurar <job_id>
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def _preparar_rutas() -> None:
    """Permite ejecutarlo tanto desde /app como desde la raiz del repo."""
    aqui = os.path.dirname(os.path.abspath(__file__))
    raiz = os.path.dirname(aqui)
    if raiz not in sys.path:
        sys.path.insert(0, raiz)


_preparar_rutas()

from sqlalchemy import func, select  # noqa: E402

from shared import archivo_enlaces  # noqa: E402
from shared.database import SessionLocal  # noqa: E402
from shared.models import Job, Link  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("jobs", nargs="*", help="solo estos (deben ser candidatos)")
    p.add_argument("--aplicar", action="store_true", help="archivar; sin esto solo se lista")
    p.add_argument("--restaurar", metavar="JOB_ID", help="devolver a `links` los de un job")
    args = p.parse_args()

    sesion = SessionLocal()
    try:
        if args.restaurar:
            n = archivo_enlaces.restaurar(sesion, args.restaurar)
            print(f"RESTAURADO  {args.restaurar}  {n:,} filas")
            return 0

        candidatos = archivo_enlaces.candidatos(sesion)
        if args.jobs:
            pedidos = set(args.jobs)
            fuera = pedidos - {str(j.id) for j in candidatos}
            if fuera:
                print("No son candidatos (no estan superados, son recientes o ya "
                      "estan archivados): " + ", ".join(sorted(fuera)))
                return 2
            candidatos = [j for j in candidatos if str(j.id) in pedidos]

        total = 0
        for job in candidatos:
            filas = sesion.execute(
                select(func.count()).select_from(Link).where(Link.job_id == job.id)
            ).scalar() or 0
            total += filas
            nombre = (job.name or "")[:50]
            if not args.aplicar:
                print(f"SERIA      {job.id}  {job.status:<9} {filas:>12,} filas  {nombre}")
                continue
            t0 = time.monotonic()
            try:
                r = archivo_enlaces.archivar(sesion, job.id)
            except Exception as e:  # noqa: BLE001 — uno que falla no para los demas
                sesion.rollback()
                print(f"FALLO      {job.id}  {e}")
                continue
            print(f"ARCHIVADO  {job.id}  {r['filas']:>12,} filas -> "
                  f"{r['bytes'] / 1e6:,.1f} MB  ({time.monotonic() - t0:.0f} s)  {nombre}")
        print(f"TOTAL      {len(candidatos)} censos, {total:,} filas de `links`")
        if not args.aplicar:
            print("Nada escrito. Repetir con --aplicar para archivar.")
        elif total:
            print("Para que el fichero de la tabla encoja: VACUUM FULL links "
                  "(bloquea la tabla; ventana sin rastreos).")
    finally:
        sesion.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
