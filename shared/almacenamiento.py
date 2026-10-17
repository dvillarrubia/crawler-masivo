"""Cuanto ocupa un censo y cuanto ocupa la base de datos.

Vive en `shared/` porque lo necesitan la API (el borrado en masa y el panel de
espacio) y cualquier script de mantenimiento, y tener la cuenta por duplicado
es como se llega a dos cifras que dicen medir lo mismo y no coinciden
(decisiones 23 y 39).

Dos cosas que conviene tener claras antes de leer una cifra de aqui:

- **El tamano por censo es una ESTIMACION, y se dice.** Medir los bytes reales
  de un job exigiria `pg_column_size` fila a fila sobre millones de filas. Lo
  que se hace es contar sus filas en las tablas grandes (`urls`, `links`,
  `issues`, que llevan `job_id` y estan indexadas por el) y multiplicarlas por
  los bytes por fila MEDIDOS de cada tabla (tamano total de la relacion entre
  sus filas, incluyendo indices y TOAST). Las tablas hijas que cuelgan de
  `url_id` —`page_content`, `html_meta`, `headings`…— no se cuentan por job:
  contarlas obligaria a unir con `urls` y ese join es exactamente lo que la
  decision 37 prohibe en las tablas grandes. Se reparten por URL, con la
  proporcion global de la instalacion. Y si de un censo no se llegan a contar
  los enlaces dentro del presupuesto, su tamano viene a `None`: medido el
  2026-10-15 en la base local, antes de agrupar los enlaces (decision 94),
  `links` eran 49 de los 57 GB, asi que una cifra sin ellos no se queda corta,
  miente.
- **Se cuentan FILAS de `links`, no enlaces.** Desde la decision 94 una fila
  agrupa las apariciones identicas de un enlace en su pagina (`repeticiones`),
  y lo que ocupa disco es la fila: filas por bytes-por-fila sigue siendo la
  cuenta correcta del tamano. Para «cuantos enlaces» habria que sumar
  `repeticiones`, que es otra pregunta y aqui no se hace.
- **Un censo archivado (decision 95) no tiene filas en `links` y SI ocupa**:
  su Parquet en el volumen `archivo`. Ese tamano es el del fichero, MEDIDO y
  no estimado, y va aparte (`archivo_bytes`) porque se libera de otra forma:
  borrar el fichero devuelve el espacio al disco en el acto, que es justo lo
  que no hace un DELETE en Postgres. Y como se sabe que no tiene filas en
  `links`, no se gasta presupuesto en contarlas: un censo archivado nunca sale
  «sin medir» por quedarse fuera del tiempo.
- **Borrar filas no encoge el fichero.** Postgres marca el espacio como
  reutilizable y lo vuelve a usar en el siguiente rastreo; el disco no baja
  sin un `VACUUM FULL`, que toma un candado exclusivo sobre la tabla (en
  `links`, decenas de GB) y por eso no se dispara desde aqui. O sea que lo que se gana
  borrando censos viejos es que el disco **deje de crecer**, no que baje. Esa
  frase viaja en la respuesta (`nota`) para que no la tenga que recordar quien
  mira el panel.
"""

from __future__ import annotations

from typing import Any, Iterable

# Tablas que llevan `job_id` y que, por tanto, se pueden contar por censo sin
# unir con nada (decision 37).
TABLAS_POR_JOB = ("urls", "links", "issues", "custom_extraction",
                  "enlaces_no_rastreables", "respuestas_sondeadas")

# Tablas que cuelgan de `urls.url_id`. No se cuentan por job: se reparten por
# URL con la proporcion global (ver el docstring). Las cuatro ultimas son de
# los modulos opcionales (semantico, Search Console) y estan casi siempre
# vacias, pero `semantic_pages` guarda un vector de 1.024 dimensiones por
# pagina: dejarla fuera seria olvidarse de 4 kB por URL en los censos donde
# ese analisis si se paso.
TABLAS_POR_URL = (
    "page_content",
    "html_meta",
    "headings",
    "resources",
    "structured_data",
    "hreflang",
    "security_headers",
    "semantic_pages",
    "semantic_cannibalization",
    "gsc_job_data",
    "gsc_query_data",
)

# Todas las del censo, para el desglose del panel de espacio. Las de aqui
# abajo son unas pocas filas por rastreo: se borran con el, pero no se pesan
# por censo.
TABLAS_DEL_CENSO = TABLAS_POR_JOB + TABLAS_POR_URL + (
    "decisions", "enrichment_runs", "semantic_analyses", "cleaning_rulesets",
)


def es_postgres(db) -> bool:
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


def _tamanos_y_filas(db) -> dict[str, tuple[int, float]]:
    """Por tabla: (bytes totales, filas estimadas).

    `reltuples` y no `count(*)`: la cuenta exacta de `links` eran 187 millones
    de filas y varios minutos (base local, 2026-10-15, antes de agrupar), y aqui solo se necesita para dividir. Es la
    estimacion que mantiene el propio planificador.
    """
    from sqlalchemy import text

    filas = db.execute(text("""
        SELECT c.relname, pg_total_relation_size(c.oid), c.reltuples
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind = 'r' AND n.nspname = current_schema()
    """)).all()
    return {r[0]: (int(r[1] or 0), float(r[2] or 0)) for r in filas}


def bytes_por_fila(db) -> dict[str, float]:
    """Bytes por fila medidos de cada tabla (indices y TOAST incluidos).

    Una tabla con `reltuples` a 0 o negativo —recien creada, nunca analizada—
    se queda FUERA del diccionario en vez de valer 0: un 0 diria «esa tabla no
    ocupa nada», que es afirmar algo que no se sabe.
    """
    salida = {}
    for tabla, (bytes_, filas) in _tamanos_y_filas(db).items():
        if filas and filas > 0:
            salida[tabla] = bytes_ / filas
    return salida


def bytes_por_url_de_las_hijas(db) -> float:
    """Lo que ocupa, por URL rastreada, todo lo que cuelga de `urls.url_id`."""
    tamanos = _tamanos_y_filas(db)
    urls = tamanos.get("urls", (0, 0))[1]
    if not urls or urls <= 0:
        return 0.0
    total = sum(tamanos.get(t, (0, 0))[0] for t in TABLAS_POR_URL)
    return total / urls


def estimar_bytes(filas: dict[str, int], por_fila: dict[str, float],
                  por_url_hijas: float = 0.0,
                  imprescindibles: tuple[str, ...] = ("urls", "links")) -> int | None:
    """Los bytes de un censo a partir de sus filas. Funcion pura.

    Devuelve None si de alguna tabla IMPRESCINDIBLE no se sabe su numero de
    filas, o no se sabe lo que pesa cada una TENIENDO filas. No es remilgo:
    medido en la instalacion local el 2026-10-15, antes de agrupar los enlaces
    (decision 94), `links` eran **49 de los 57 GB** de la base (el 86%), asi que una cifra sin los enlaces no se queda
    corta, miente. Y un 0 se leeria como «este censo no ocupa nada», que es
    justo lo contrario de lo que se quiere saber antes de borrarlo.

    Que una tabla con CERO filas no necesite su coste por fila no es un
    detalle: una tabla vacia no tiene `reltuples` de donde sacarlo, asi que
    exigirlo dejaba sin tamano a cualquier censo sin enlaces.
    """
    for tabla in imprescindibles:
        if tabla not in filas:
            return None
        if filas[tabla] and tabla not in por_fila:
            return None
    total = sum(filas[t] * por_fila[t] for t in filas if t in por_fila)
    total += filas.get("urls", 0) * por_url_hijas
    return int(total)


def _tope_de_consulta(db, segundos: float) -> None:
    """`statement_timeout` para la transaccion actual, en Postgres."""
    from sqlalchemy import text

    db.execute(text("SET LOCAL statement_timeout = :ms"),
               {"ms": str(max(1, int(segundos * 1000)))})


def filas_por_job(db, job_ids: Iterable[Any], *, presupuesto_s: float = 20.0,
                  sin_enlaces: Iterable[Any] = ()) -> dict[Any, dict[str, int]]:
    """Filas de cada job en las tablas grandes. Lo que no se pueda contar, falta.

    Se filtra por `job_id` y nunca uniendo con `urls` (decision 37): `links`
    es la tabla grande con diferencia, y el filtro tiene que caer del lado del
    indice.

    `sin_enlaces` son los jobs de los que se SABE que no tienen filas en
    `links` —los archivados en Parquet, decision 95—: van a 0 sin consultar,
    asi que no gastan presupuesto ni se quedan sin cifra por falta de tiempo.

    **Los enlaces se cuentan con una consulta POR CENSO y no con un `IN`
    agrupado.** Medido contra la base local el 2026-10-15, antes de agrupar
    los enlaces (decision 94; ahora hay menos filas por censo, el coste por
    fila no cambia): un recorrido del
    indice de `links` va a unos 1,6 us por fila (691.939 enlaces de un censo
    de progym en 1,1 s), asi que un `IN` con cinco censos de progym se pasa de
    los 20 s de tope... y al reventar la consulta agrupada se perdian los
    tamanos de los CINCO. Una por censo degrada de uno en uno: los que caben
    en el presupuesto traen su cifra y los demas se quedan sin ella, que es
    «no se sabe» y no una cifra corta.

    `urls` e `issues` si van agrupadas: eran 745 MB y 905 MB en esa misma
    base, un orden de magnitud largo por debajo de `links`.
    """
    import time

    from sqlalchemy import func, select

    from shared.models import (CustomExtraction, EnlaceNoRastreable, Issue, Link,
                               RespuestaSondeada, Url)

    ids = list(job_ids)
    salida: dict[Any, dict[str, int]] = {i: {} for i in ids}
    if not ids:
        return salida
    archivados = set(sin_enlaces)
    for i in ids:
        if i in archivados:
            salida[i]["links"] = 0
    pg = es_postgres(db)
    fin = time.monotonic() + presupuesto_s

    for tabla, modelo in (("urls", Url), ("issues", Issue),
                          ("custom_extraction", CustomExtraction),
                          ("enlaces_no_rastreables", EnlaceNoRastreable),
                          ("respuestas_sondeadas", RespuestaSondeada)):
        try:
            if pg:
                _tope_de_consulta(db, max(1.0, fin - time.monotonic()))
            consulta = (select(modelo.job_id, func.count().label("n"))
                        .where(modelo.job_id.in_(ids)).group_by(modelo.job_id))
            cuentas = {j: int(n) for j, n in db.execute(consulta).all()}
            for i in ids:
                salida[i][tabla] = cuentas.get(i, 0)
        except Exception:
            # La transaccion queda abortada tras un `statement_timeout`: hay
            # que soltarla para que la siguiente consulta pueda empezar.
            db.rollback()

    for i in ids:
        if i in archivados:
            continue
        resto = fin - time.monotonic()
        if resto <= 0:
            break
        try:
            if pg:
                _tope_de_consulta(db, resto)
            n = db.execute(select(func.count()).where(Link.job_id == i)).scalar()
            salida[i]["links"] = int(n or 0)
        except Exception:
            db.rollback()
    return salida


def tamano_de_jobs(db, job_ids: Iterable[Any], *, presupuesto_s: float = 20.0
                   ) -> dict[Any, dict[str, Any]]:
    """Por job: sus filas, los bytes estimados en la base (`None` si no se
    pudo medir) y los de su Parquet de enlaces si esta archivado
    (`archivo_bytes`, medido; 0 si no lo esta; `None` si lo esta y no se sabe).

    Nunca lanza: un tamano es informacion de apoyo para decidir que borrar, y
    quedarse sin el no puede impedir borrar.
    """
    ids = list(job_ids)
    archivo = _archivo_de_jobs(db, ids)
    try:
        filas = filas_por_job(db, ids, presupuesto_s=presupuesto_s,
                              sin_enlaces=[i for i, a in archivo.items() if a["archivado"]])
    except Exception:
        db.rollback()
        return {i: {"filas": None, "bytes": None, **archivo.get(i, _SIN_ARCHIVO)}
                for i in ids}

    if es_postgres(db):
        try:
            por_fila = bytes_por_fila(db)
            por_url = bytes_por_url_de_las_hijas(db)
        except Exception:
            db.rollback()
            por_fila, por_url = {}, 0.0
    else:
        # SQLite (tests, desarrollo): se cuentan las filas, pero no hay de
        # donde sacar los bytes por fila. Decirlo es mejor que inventarlo.
        por_fila, por_url = {}, 0.0

    salida = {
        i: {"filas": filas.get(i) or None,
            "bytes": estimar_bytes(filas.get(i, {}), por_fila, por_url),
            **archivo.get(i, _SIN_ARCHIVO)}
        for i in ids
    }
    # Se cierra la transaccion de lectura A PROPOSITO: los `SET LOCAL
    # statement_timeout` de arriba viven hasta el final de la transaccion, y
    # el borrado que viene detras de esta medicion tarda minutos. Sin esto,
    # medir antes de borrar le ponia al DELETE un tope de 20 s. Quien llame
    # mide antes de escribir, nunca en medio.
    db.rollback()
    return salida


_SIN_ARCHIVO = {"archivado": False, "archivo_bytes": 0}


def _archivo_de_jobs(db, ids: list) -> dict[Any, dict[str, Any]]:
    """Por job: si sus enlaces estan archivados y lo que ocupa el Parquet.

    El tamano sale del fichero (`archivo_enlaces.bytes_en_disco`), que es lo
    que se libera al borrarlo. Nunca lanza: sin esto el borrado sigue igual.
    """
    if not ids:
        return {}
    try:
        from shared import archivo_enlaces
        from shared.models import Job

        filas = (db.query(Job.id, Job.enlaces_archivados)
                 .filter(Job.id.in_(ids)).all())
        return {
            jid: ({"archivado": True,
                   "archivo_bytes": archivo_enlaces.bytes_en_disco(jid, resumen)}
                  if resumen else dict(_SIN_ARCHIVO))
            for jid, resumen in filas
        }
    except Exception:
        db.rollback()
        return {}


NOTA_DE_ESPACIO = (
    "Borrar censos libera espacio DENTRO de la base de datos: Postgres lo "
    "reutiliza en los siguientes rastreos, pero el fichero no se encoge sin un "
    "VACUUM FULL, que bloquea la tabla entera. Lo que se gana es que el disco "
    "deje de crecer. Los enlaces archivados en Parquet son la excepcion: "
    "borrar su fichero si devuelve el espacio al disco."
)


def resumen_de_disco(db) -> dict[str, Any]:
    """Lo que ocupa la base de datos, por tabla, de mas a menos.

    Fuera de Postgres devuelve `total_bytes: None` en vez de un cero: no es
    que no ocupe nada, es que no se sabe medirlo aqui.

    Lleva aparte lo que ocupan los Parquet de enlaces archivados
    (`archivo_bytes`, `archivo_ficheros`; decision 95): estan fuera de
    Postgres, asi que `total_bytes` no los incluye, y sin ellos el panel no
    diria donde han ido los enlaces de los censos superados.
    """
    from sqlalchemy import text

    from shared import archivo_enlaces

    archivo = archivo_enlaces.resumen_del_directorio()
    if not es_postgres(db):
        return {"total_bytes": None, "tablas": [], "nota": NOTA_DE_ESPACIO, **archivo}

    total = int(db.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0)
    tamanos = _tamanos_y_filas(db)
    tablas = [
        {"tabla": t, "bytes": b, "filas_aprox": int(f) if f and f > 0 else None}
        for t, (b, f) in sorted(tamanos.items(), key=lambda x: -x[1][0])
        if b > 0
    ]
    return {
        "total_bytes": total,
        "censo_bytes": sum(tamanos.get(t, (0, 0))[0] for t in TABLAS_DEL_CENSO),
        "tablas": tablas,
        "nota": NOTA_DE_ESPACIO,
        **archivo,
    }
