"""Rellena `profile_runs` para los censos que ya existian (decision 130).

Una fila por job terminado (`completed`, `failed`, `stalled`) que no la tenga,
de uno en uno y en orden de fecha, con un `statement_timeout` por job: un censo
enorme que no cabe en el tope se salta y se dice, en vez de tener la base
tomada. Lee `urls` e `issues` por `job_id`; **nunca `links`** (decision 37), asi
que vale tambien para los censos con los enlaces archivados en Parquet.

Por defecto escribe y confirma job a job. Con `--simular` hace lo mismo dentro
de UNA transaccion que se deshace al final —crea la tabla dentro si no existe—,
para medir cuantos jobs y cuanto tarda sobre una base que no se puede tocar.

Uso (dentro del contenedor del crawler o de la API):

    python scripts/rellenar_evolucion.py --simular
    python scripts/rellenar_evolucion.py
    python scripts/rellenar_evolucion.py --todos            # rehace tambien las que hay
    python scripts/rellenar_evolucion.py <job_id> [...]
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def _preparar_rutas() -> None:
    aqui = os.path.dirname(os.path.abspath(__file__))
    raiz = os.path.dirname(aqui)
    if raiz not in sys.path:
        sys.path.insert(0, raiz)


_preparar_rutas()

from sqlalchemy import select, text  # noqa: E402

from shared.evolucion import TERMINADOS, registrar  # noqa: E402
from shared.models import Job, ProfileRun  # noqa: E402


def _es_postgres(session) -> bool:
    try:
        return session.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


def pendientes(session, *, todos: bool = False, ids=None) -> list:
    """Los jobs que hay que agregar: terminados y, salvo `todos`, sin fila."""
    consulta = select(Job.id, Job.name, Job.created_at).where(Job.status.in_(TERMINADOS))
    if ids:
        consulta = consulta.where(Job.id.in_(list(ids)))
    if not todos:
        consulta = (consulta.outerjoin(ProfileRun, ProfileRun.job_id == Job.id)
                    .where(ProfileRun.id.is_(None)))
    return session.execute(consulta.order_by(Job.created_at)).all()


def rellenar(session, *, todos: bool = False, ids=None, timeout_s: int = 60,
             aplicar: bool = True, salida=print) -> dict:
    """Agrega los pendientes de uno en uno. Devuelve las cifras.

    Cada job en su SAVEPOINT: un tope de tiempo o un fallo en uno no aborta la
    transaccion de los demas (con `--simular` es una sola). Con `aplicar`,
    commit tras cada job.
    """
    pg = _es_postgres(session)
    t0 = time.monotonic()
    lista = pendientes(session, todos=todos, ids=ids)
    escritos, fallos, tiempos = 0, [], []
    for job_id, nombre, _fecha in lista:
        if pg:
            # SET LOCAL: dura lo que la transaccion, asi que va en cada vuelta
            # (con `aplicar` cada job abre la suya).
            session.execute(text(f"SET LOCAL statement_timeout = '{int(timeout_s)}s'"))
        t = time.monotonic()
        punto = session.begin_nested()
        try:
            registrar(session, job_id)
            punto.commit()
            escritos += 1
            tiempos.append((str(job_id), round(time.monotonic() - t, 3)))
        except Exception as exc:  # el tope de tiempo, sobre todo
            punto.rollback()
            fallos.append((str(job_id), f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"))
            salida(f"  FALLA {job_id} ({nombre}): {fallos[-1][1]}")
            continue
        if aplicar:
            session.commit()
        salida(f"  {job_id}  {tiempos[-1][1]:7.3f} s  {nombre}")
    total = round(time.monotonic() - t0, 3)
    lentos = sorted(tiempos, key=lambda x: -x[1])[:5]
    return {"pendientes": len(lista), "escritos": escritos, "fallos": fallos,
            "segundos": total, "mas_lentos": lentos}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("jobs", nargs="*", help="solo estos jobs")
    p.add_argument("--todos", action="store_true", help="rehacer tambien los que ya tienen fila")
    p.add_argument("--simular", action="store_true",
                   help="todo en una transaccion que se deshace al final (medir)")
    p.add_argument("--timeout", type=int, default=60, help="statement_timeout por job, en s")
    args = p.parse_args()

    from sqlalchemy.orm import Session

    from shared.database import SessionLocal, engine

    if args.simular:
        conexion = engine.connect()
        transaccion = conexion.begin()
        try:
            ProfileRun.__table__.create(bind=conexion, checkfirst=True)
            sesion = Session(bind=conexion, join_transaction_mode="create_savepoint")
            r = rellenar(sesion, todos=args.todos, ids=args.jobs or None,
                         timeout_s=args.timeout, aplicar=False)
            sesion.close()
        finally:
            transaccion.rollback()
            conexion.close()
        print("SIMULADO (ROLLBACK): no se ha escrito nada.")
    else:
        sesion = SessionLocal()
        try:
            r = rellenar(sesion, todos=args.todos, ids=args.jobs or None,
                         timeout_s=args.timeout, aplicar=True)
        finally:
            sesion.close()
    print(f"{r['escritos']} de {r['pendientes']} jobs en {r['segundos']} s; "
          f"{len(r['fallos'])} fallos")
    for jid, s in r["mas_lentos"]:
        print(f"  mas lento: {jid} {s} s")
    return 1 if r["fallos"] else 0


if __name__ == "__main__":
    sys.exit(main())
