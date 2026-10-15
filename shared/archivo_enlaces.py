"""Enlaces de los censos superados, archivados en Parquet (decision 95).

`links` es, con diferencia, la tabla mas grande: una fila por enlace distinto
de cada pagina de cada censo, con su URL de destino entera y el hash en texto.
Postgres la guarda fila a fila, sin comprimir y con tres indices. Medido con el
censo de Druni (60.399 URLs, 139 M enlaces): ~30 GB en `links`, 9 GB agrupados
(decision 94) y **68 MB** en Parquet con zstd. Los enlaces de un censo se
escriben una vez y no se vuelven a tocar, que es justo para lo que esta hecho
un formato columnar.

Lo que se archiva es lo que ya no hace falta caliente: un censo **superado**,
es decir, para el que hay otro mas nuevo y completado del mismo host cuyas
semillas cubren las suyas. Es la misma contencion con la que se elige el censo
de referencia de una comparacion (decision 70 y `comparacion.censo_anterior`),
asi que un censo que todavia pueda ser la referencia de alguien no se archiva
nunca. Y nada que haya terminado hace menos de `ARCHIVAR_DIAS_MINIMOS`.

Lo archivado se sigue pudiendo LEER: la API sirve la ficha de URL, la lista de
enlaces, sus CSV, los origenes del explorador y el backup desde el fichero. Lo
que no se puede es **re-analizarlo** sin restaurarlo antes: el analisis calcula
los enlaces entrantes y el PageRank con SQL sobre `links`, y sin filas los
pondria a cero. `analysis.analyzer` se niega y dice como restaurarlo.

El fichero va ordenado por (`to_url_hash`, `id`), que es el orden de
`GET /links` y el de las consultas por destino. pyarrow se importa dentro de
las funciones: la imagen del analisis copia `shared/` y no lo instala.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# Las columnas de `links` que se guardan. `job_id` no: es el mismo en todo el
# fichero y va en el nombre y en los metadatos.
COLUMNAS = (
    "id", "from_url_id", "to_url", "to_url_hash", "anchor_text", "rel",
    "is_internal", "link_position", "follow", "target", "alt_text",
    "link_type", "repeticiones",
)

_LOTE = 50_000
_FILAS_POR_GRUPO = 100_000
_TERMINADOS = ("completed", "failed", "cancelled", "stalled")


def directorio() -> Path:
    return Path(os.getenv("ARCHIVO_DIR", "/data/archivo")) / "enlaces"


def ruta(job_id) -> Path:
    return directorio() / f"{job_id}.parquet"


def dias_minimos() -> int:
    return int(os.getenv("ARCHIVAR_DIAS_MINIMOS", "14"))


def _uuid(job_id) -> uuid.UUID:
    return job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))


def esta_archivado(job) -> bool:
    return bool(getattr(job, "enlaces_archivados", None))


def _esquema():
    import pyarrow as pa

    return pa.schema([
        ("id", pa.int64()), ("from_url_id", pa.int64()), ("to_url", pa.string()),
        ("to_url_hash", pa.string()), ("anchor_text", pa.string()),
        ("rel", pa.string()), ("is_internal", pa.bool_()),
        ("link_position", pa.string()), ("follow", pa.bool_()),
        ("target", pa.string()), ("alt_text", pa.string()),
        ("link_type", pa.string()), ("repeticiones", pa.int32()),
    ])


# ---------------------------------------------------------------------------
# Que se archiva
# ---------------------------------------------------------------------------
def candidatos(db, ahora: datetime | None = None) -> list:
    """Jobs cuyos enlaces se pueden archivar, del mas antiguo al mas nuevo.

    Un censo completado se archiva si otro completado, mas nuevo y del mismo
    host abarca sus semillas, y ademas no es el ultimo de su programacion. Uno
    que no termino bien (fallido, cancelado, estancado) no puede ser referencia
    de nada: basta con que haya un completado mas nuevo del mismo host. En los
    dos casos, solo si termino hace al menos `dias_minimos()` dias.
    """
    from shared.comparacion import _host, _norm
    from shared.models import Job

    ahora = ahora or datetime.now(timezone.utc)
    limite = ahora - timedelta(days=dias_minimos())

    jobs = (
        db.query(Job)
        .filter(Job.status.in_(_TERMINADOS), Job.started_at.isnot(None))
        .order_by(Job.started_at.desc())
        .all()
    )
    completados_por_host: dict[str, list] = {}
    ultimo_de_programacion: set = set()
    vistos_programacion: set = set()
    for j in jobs:  # de mas nuevo a mas antiguo
        if j.status == "completed" and j.programacion_id and j.programacion_id not in vistos_programacion:
            vistos_programacion.add(j.programacion_id)
            ultimo_de_programacion.add(j.id)

    salida = []
    for j in jobs:
        host = _host(j.seeds[0]) if j.seeds else None
        if not host:
            continue
        mas_nuevos = completados_por_host.setdefault(host, [])
        fin = j.completed_at or j.started_at
        if fin.tzinfo is None:
            fin = fin.replace(tzinfo=timezone.utc)
        archivable = (
            not esta_archivado(j)
            and fin <= limite
            and j.id not in ultimo_de_programacion
        )
        if archivable:
            if j.status == "completed":
                mias = {_norm(u) for u in j.seeds}
                archivable = any(mias <= semillas for _, semillas in mas_nuevos)
            else:
                archivable = bool(mas_nuevos)
        if archivable:
            salida.append(j)
        if j.status == "completed":
            mas_nuevos.append((j.id, {_norm(u) for u in j.seeds}))
    salida.reverse()
    return salida


# ---------------------------------------------------------------------------
# Archivar y restaurar
# ---------------------------------------------------------------------------
class SeEstaAnalizando(RuntimeError):
    """Otro proceso tiene el candado del job: se archivara en otra pasada."""


def _tomar_candado_del_job(db, job_id) -> None:
    """El candado del ANALISIS de ese job, hasta el commit del archivado.

    Archivar borra las filas de `links` del job. Si a la vez alguien lo esta
    analizando —el re-analisis de la referencia de la decision 93 lo hace con
    un censo superado, que es justo lo que se archiva, y lo lanza el cierre
    de OTRO rastreo—, el analisis veria los enlaces desaparecer a mitad y
    dejaria entrantes, PageRank y profundidad a cero sin ningun error. Con el
    candado compartido, uno de los dos espera: si analiza, aqui no se archiva
    (la siguiente pasada lo hara); si se archiva, `run_analysis` no entra, y
    si entra despues ve la marca y se niega (decision 95).

    Es de TRANSACCION: se suelta solo con el commit (o el rollback) que marca
    el job y borra sus enlaces. En SQLite no hay advisory locks.
    """
    from sqlalchemy import text

    from shared.candados import clave_de_job

    if not db.get_bind().dialect.name.startswith("postgres"):
        return
    tomado = db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"),
                        {"k": clave_de_job(job_id)}).scalar()
    if not tomado:
        raise SeEstaAnalizando(
            f"el job {job_id} se esta analizando: no se archiva ahora")


def archivar(db, job_id) -> dict:
    """Escribe los enlaces del job en Parquet, lo comprueba y los borra de BD.

    El orden es lo que lo hace seguro: primero el fichero (a uno temporal, y
    solo se renombra si al releerlo tiene las mismas filas y la misma suma de
    repeticiones que la BD), despues, en UNA transaccion, la marca en el job y
    el borrado. Si algo falla antes del commit los enlaces siguen en la BD y
    volver a pasarlo reescribe el fichero.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    from sqlalchemy import delete, func, select

    from shared.models import Job, Link

    job = db.get(Job, _uuid(job_id))
    if job is None:
        raise ValueError(f"no existe el job {job_id}")
    if esta_archivado(job):
        return job.enlaces_archivados
    if job.status not in _TERMINADOS:
        raise RuntimeError(f"el job {job_id} esta en '{job.status}': no se archiva")
    _tomar_candado_del_job(db, job.id)

    destino = ruta(job.id)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(".parquet.tmp")

    tabla = Link.__table__
    columnas = [tabla.c[c] for c in COLUMNAS]
    esquema = _esquema()
    stats = {"filas": 0, "apariciones": 0, "internos": 0, "nofollow_internos": 0}

    consulta = (
        select(*columnas)
        .where(tabla.c.job_id == job.id)
        .order_by(tabla.c.to_url_hash, tabla.c.id)
        .execution_options(stream_results=True, yield_per=_LOTE)
    )
    escritor = pq.ParquetWriter(temporal, esquema, compression="zstd")
    try:
        resultado = db.execute(consulta)
        for lote in resultado.partitions(_LOTE):
            datos = {c: [f[i] for f in lote] for i, c in enumerate(COLUMNAS)}
            datos["repeticiones"] = [r or 1 for r in datos["repeticiones"]]
            for interno, follow, rep in zip(datos["is_internal"], datos["follow"],
                                            datos["repeticiones"]):
                stats["apariciones"] += rep
                if interno:
                    stats["internos"] += rep
                    if follow is False:
                        stats["nofollow_internos"] += rep
            stats["filas"] += len(lote)
            escritor.write_table(pa.table(datos, schema=esquema),
                                 row_group_size=_FILAS_POR_GRUPO)
    finally:
        escritor.close()

    en_bd = db.execute(
        select(func.count(), func.coalesce(func.sum(tabla.c.repeticiones), 0))
        .where(tabla.c.job_id == job.id)
    ).one()
    leido = pq.ParquetFile(temporal)
    suma = sum(
        pc.sum(b.column(0)).as_py() or 0
        for b in leido.iter_batches(columns=["repeticiones"], batch_size=_LOTE)
    )
    if (leido.metadata.num_rows, suma) != (stats["filas"], stats["apariciones"]) \
            or (en_bd[0], int(en_bd[1])) != (stats["filas"], stats["apariciones"]):
        temporal.unlink(missing_ok=True)
        raise RuntimeError(
            f"job {job_id}: el fichero no cuadra con la BD "
            f"(fichero {leido.metadata.num_rows}/{suma}, BD {en_bd[0]}/{en_bd[1]}, "
            f"escrito {stats['filas']}/{stats['apariciones']}); no se borra nada"
        )
    os.replace(temporal, destino)

    resumen = {
        **stats,
        "fichero": destino.name,
        "bytes": destino.stat().st_size,
        "fecha": datetime.now(timezone.utc).isoformat(),
    }
    job.enlaces_archivados = resumen
    db.execute(delete(Link).where(Link.job_id == job.id))
    db.commit()
    logger.info("Job %s: %d enlaces archivados en %s (%d bytes)",
                job_id, stats["filas"], destino, resumen["bytes"])
    return resumen


def restaurar(db, job_id) -> int:
    """Devuelve a `links` los enlaces archivados de un job. Filas devueltas."""
    import pyarrow.parquet as pq
    from sqlalchemy import insert

    from shared.models import Job, Link

    job = db.get(Job, _uuid(job_id))
    if job is None:
        raise ValueError(f"no existe el job {job_id}")
    if not esta_archivado(job):
        return 0
    fichero = pq.ParquetFile(ruta(job.id))
    n = 0
    for lote in fichero.iter_batches(batch_size=_LOTE):
        filas = lote.to_pylist()
        for f in filas:
            f["job_id"] = job.id
        db.execute(insert(Link), filas)
        n += len(filas)
    job.enlaces_archivados = None
    db.commit()
    ruta(job.id).unlink(missing_ok=True)
    logger.info("Job %s: %d enlaces restaurados", job_id, n)
    return n


def borrar(job_id) -> None:
    """El fichero de un job que se borra."""
    try:
        ruta(job_id).unlink(missing_ok=True)
    except OSError:
        logger.exception("No se pudo borrar el archivo de enlaces de %s", job_id)


# ---------------------------------------------------------------------------
# Leer
# ---------------------------------------------------------------------------
def _dataset(job_id):
    import pyarrow.dataset as ds

    return ds.dataset(ruta(job_id), format="parquet")


def leer(job_id, *, a_hashes=None, de_url_id=None, columnas=None,
         limite: int | None = None) -> list[dict[str, Any]]:
    """Filas archivadas, filtradas por destino o por origen, en el orden del
    fichero (destino, id). Nunca carga el fichero entero: pyarrow aplica el
    filtro por lotes y solo lee las columnas pedidas."""
    import pyarrow.dataset as ds

    filtro = None
    if a_hashes is not None:
        filtro = ds.field("to_url_hash").isin(list(a_hashes))
    if de_url_id is not None:
        f = ds.field("from_url_id") == int(de_url_id)
        filtro = f if filtro is None else filtro & f
    salida: list[dict[str, Any]] = []
    for lote in _dataset(job_id).to_batches(columns=list(columnas or COLUMNAS),
                                             filter=filtro, batch_size=_LOTE):
        salida.extend(lote.to_pylist())
        if limite is not None and len(salida) >= limite:
            return salida[:limite]
    return salida


def contar(job_id) -> int:
    import pyarrow.parquet as pq

    return pq.ParquetFile(ruta(job_id)).metadata.num_rows


def pagina(job_id, offset: int, limite: int) -> list[dict[str, Any]]:
    """`limite` filas a partir de `offset`, leyendo solo los grupos que tocan."""
    import pyarrow.parquet as pq

    fichero = pq.ParquetFile(ruta(job_id))
    salida: list[dict[str, Any]] = []
    inicio = 0
    for g in range(fichero.metadata.num_row_groups):
        n = fichero.metadata.row_group(g).num_rows
        if inicio + n > offset and len(salida) < limite:
            filas = fichero.read_row_group(g).to_pylist()
            desde = max(0, offset - inicio)
            salida.extend(filas[desde:desde + (limite - len(salida))])
        inicio += n
        if len(salida) >= limite:
            break
    return salida


def recorrer(job_id) -> Iterator[dict[str, Any]]:
    """Todas las filas, por lotes (CSV de enlaces y backup)."""
    import pyarrow.parquet as pq

    for lote in pq.ParquetFile(ruta(job_id)).iter_batches(batch_size=_LOTE):
        yield from lote.to_pylist()
