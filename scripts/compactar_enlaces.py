"""Agrupa los enlaces de los censos ya rastreados, como los guarda hoy el spider.

Hasta la decision 94, `links` guardaba una fila por APARICION: el megamenu de
escritorio, el de movil y el desplegable eran tres filas identicas en cada
pagina. En Druni eso eran 139 M filas para 60.399 URLs (~30 GB) y agrupadas
quedan 42 M (9 GB). Este script hace con un censo viejo lo mismo que el spider
hace ya al rastrear: las filas identicas de una pagina pasan a ser una, con
`repeticiones` = cuantas eran.

No cambia ninguna cifra: los conteos por aparicion suman `repeticiones`, los de
origenes distintos y el PageRank no miran las repeticiones (decision 94). Se
conserva el `id` mas bajo de cada grupo, asi que el orden de las listas de
enlaces tampoco cambia. No hace falta re-analizar.

Por defecto NO escribe: dice cuantas filas quedarian. Con `--aplicar` reescribe
los enlaces de cada job en una transaccion (o todo o nada por job). Se niega a
tocar un job que este rastreando o analizando.

Ojo con el disco: Postgres no devuelve al sistema el espacio de las filas
borradas, lo deja libre DENTRO de la tabla para los rastreos siguientes. Para
que el fichero encoja hace falta `VACUUM FULL links`, que bloquea la tabla
entera mientras la reescribe (ningun rastreo puede guardar enlaces) y necesita
espacio libre del tamano de la tabla resultante. Eso es una ventana de
mantenimiento, no algo que haga este script.

Uso (dentro del contenedor del crawler):

    python scripts/compactar_enlaces.py <job_id> [<job_id> ...]
    python scripts/compactar_enlaces.py --todos
    python scripts/compactar_enlaces.py --todos --aplicar
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

from sqlalchemy import text  # noqa: E402

from shared.database import SessionLocal  # noqa: E402

# Todo lo que se guarda de un enlace salvo su id: lo mismo que
# `extractors._CLAVE_ENLACE`, con los nombres de columna. `IS NOT DISTINCT
# FROM` no hace falta: GROUP BY ya trata los NULL como iguales.
_COLUMNAS = (
    "job_id, from_url_id, to_url, to_url_hash, anchor_text, rel, is_internal, "
    "link_position, follow, target, alt_text, link_type"
)

_EN_MARCHA = ("pending", "queued", "running", "analyzing")


def _todos_los_jobs(sesion) -> list[str]:
    """Todos, del mas pequeno al mas grande. Pasarlo por uno ya compactado es
    inocuo: sale con las mismas filas y no se escribe."""
    return [str(j) for j in sesion.execute(text(
        "SELECT id FROM jobs ORDER BY total_urls_crawled NULLS FIRST"
    )).scalars()]


def compactar(sesion, job_id: str, aplicar: bool) -> tuple[int, int]:
    """(filas antes, filas despues) de un job. Con `aplicar`, las reescribe."""
    estado = sesion.execute(
        text("SELECT status FROM jobs WHERE id = :j"), {"j": job_id}
    ).scalar()
    if estado in _EN_MARCHA:
        raise RuntimeError(f"el job {job_id} esta en '{estado}': no se toca")

    sesion.execute(text("SET LOCAL work_mem = '512MB'"))
    sesion.execute(text("DROP TABLE IF EXISTS enlaces_agr_tmp"))
    sesion.execute(text(
        f"""
        CREATE TEMP TABLE enlaces_agr_tmp ON COMMIT DROP AS
        SELECT MIN(id) AS id, {_COLUMNAS}, SUM(repeticiones)::int AS repeticiones
        FROM links WHERE job_id = :j
        GROUP BY {_COLUMNAS}
        """
    ), {"j": job_id})
    antes = sesion.execute(
        text("SELECT COUNT(*) FROM links WHERE job_id = :j"), {"j": job_id}
    ).scalar() or 0
    despues = sesion.execute(text("SELECT COUNT(*) FROM enlaces_agr_tmp")).scalar() or 0

    if aplicar and despues < antes:
        sesion.execute(text("DELETE FROM links WHERE job_id = :j"), {"j": job_id})
        sesion.execute(text(
            f"INSERT INTO links (id, {_COLUMNAS}, repeticiones) "
            f"SELECT id, {_COLUMNAS}, repeticiones FROM enlaces_agr_tmp"
        ))
        sesion.commit()
    else:
        sesion.rollback()
    return antes, despues


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("jobs", nargs="*")
    p.add_argument("--todos", action="store_true")
    p.add_argument("--aplicar", action="store_true",
                   help="reescribir; sin esto solo se cuenta")
    args = p.parse_args()
    if not args.jobs and not args.todos:
        p.error("indica uno o mas job_id, o --todos")

    sesion = SessionLocal()
    try:
        jobs = args.jobs or _todos_los_jobs(sesion)
        sesion.rollback()
        total_antes = total_despues = 0
        for job_id in jobs:
            t0 = time.monotonic()
            try:
                antes, despues = compactar(sesion, job_id, args.aplicar)
            except RuntimeError as e:
                sesion.rollback()
                print(f"SALTADO  {e}")
                continue
            total_antes += antes
            total_despues += despues
            print(f"{'HECHO ' if args.aplicar else 'SERIA '}  {job_id}  "
                  f"{antes:>12,} -> {despues:>12,} filas  ({time.monotonic() - t0:.0f} s)")
        print(f"TOTAL    {total_antes:,} -> {total_despues:,} filas")
        if not args.aplicar:
            print("Nada escrito. Repetir con --aplicar para reescribir.")
        elif total_despues < total_antes:
            print("Para que el fichero encoja: VACUUM FULL links (bloquea la "
                  "tabla; ver el docstring).")
    finally:
        sesion.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
