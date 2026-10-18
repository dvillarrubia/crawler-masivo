"""Evolucion de un perfil o un cliente a lo largo de sus ejecuciones (#36 F3,
decision 130).

Dos piezas:

- **El agregado de un job** (`agregar` / `registrar`): una fila de
  `profile_runs` con lo que dejo esa ejecucion —URLs por grupo de estado,
  indexables, incidencias por severidad y los tipos mas frecuentes, el PageRank
  desperdiciado de `jobs.pagerank_resumen`, los clics medios desde la home de
  las indexables y las dos versiones—. Sale de `urls` e `issues` filtrando por
  `job_id`, **sin leer `links`** (decision 37): la ficha de un cliente con 50
  ejecuciones lee 50 filas, no 50 censos. Se escribe al acabar el analisis y al
  cerrar el rastreo, best-effort (decisiones 74 y 77: el `try` cubre la funcion
  entera y un fallo no deja el job `failed`).
- **La serie** (`series`): los puntos de cada perfil en orden, cuales se pueden
  comparar y donde cambio la version del analisis entre dos puntos.

Lo que se niega a afirmar, igual que la comparacion (decisiones 67, 72, 91):

- **Un censo truncado, fallido, un canario, una prueba o una lista no son un
  punto comparable**: se devuelven con `comparable=False` y su motivo, y la
  grafica los pinta aparte, fuera de la linea y de la escala. Un rastreo
  bloqueado con 3 URLs en mitad de una serie de 5.000 hundiria la escala y se
  leeria como «el sitio se ha caido».
- **Entre dos puntos analizados con codigo distinto el salto puede ser
  nuestro.** Se marca en el punto posterior (`cambio_version`), y la tendencia
  del panel no dice «sube» ni «baja» a traves de ese corte: dice que no se
  puede comparar. Desconocido tampoco vale (decision 91): un censo sin sello
  pudo analizarse con cualquier cosa. El cambio de version del RASTREO se marca
  tambien, pero no corta la tendencia, como en la decision 91: con un despliegue
  por semana cortaria todas.
- **Un agregado calculado con otra version del analisis que la del job** esta
  desfasado —alguien re-analizo sin pasar por aqui—: no es comparable hasta que
  se rehaga (`desfasado`).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable
from urllib.parse import urlsplit

from shared.clientes import tipo_de_rastreo
from shared.comparacion import MOTIVOS_TRUNCADO
from shared.modo_lista import es_lista

logger = logging.getLogger(__name__)

# Version de la definicion del agregado. Subirla cuando cambie lo que se
# cuenta: `scripts/rellenar_evolucion.py --todos` rehace las filas viejas, y
# `series` no compara dos puntos de criterios distintos.
CRITERIO = 1

# Estados en los que un job ya no va a cambiar (salvo re-analisis): los que
# tienen fila. `cancelled` no: lo cancela quien lo lanzo, y en una serie solo
# meteria ruido.
TERMINADOS = ("completed", "failed", "stalled")

TOP_TIPOS = 10

# Que se grafica y en que sentido es mejor. Lo usa la interfaz para el color
# de la tendencia, y el panel para la de errores.
METRICAS: dict[str, dict[str, Any]] = {
    "errores": {"etiqueta": "Errores", "mejor": "baja"},
    "urls_con_error": {"etiqueta": "URLs con error", "mejor": "baja"},
    "avisos": {"etiqueta": "Avisos", "mejor": "baja"},
    "urls_4xx": {"etiqueta": "URLs 4xx", "mejor": "baja"},
    "urls_5xx": {"etiqueta": "URLs 5xx", "mejor": "baja"},
    "indexables": {"etiqueta": "Páginas indexables", "mejor": "sube"},
    "pagerank_desperdiciado": {"etiqueta": "PageRank desperdiciado", "mejor": "baja",
                               "unidad": "%"},
    "click_depth_medio": {"etiqueta": "Clics desde la home (media)", "mejor": "baja"},
}

COLUMNAS = (
    "urls", "urls_2xx", "urls_3xx", "urls_4xx", "urls_5xx", "urls_bloqueadas",
    "paginas_html", "indexables", "click_depth_medio", "indexables_sin_camino",
    "incidencias", "errores", "avisos", "info", "urls_con_error", "top_tipos",
    "pagerank_desperdiciado", "pagerank_indexable",
)


# ---------------------------------------------------------------------------
# El agregado de un job
# ---------------------------------------------------------------------------
def agregar(session, job_id) -> dict[str, Any] | None:
    """Las cifras de un job. None si el job no existe.

    Tres consultas, todas por `job_id` (decision 37): una sobre `urls`, dos
    sobre `issues`. Se leen columnas sueltas de `jobs` y no la fila entera: el
    relleno hacia atras corre tambien contra bases cuyo esquema va por detras
    del modelo (sin `health`, por ejemplo), y pedir la fila entera reventaria.
    """
    from sqlalchemy import func, select

    from shared.models import Issue, Job, Url

    t0 = time.monotonic()
    job_id = _como_uuid(job_id)
    job = session.execute(
        select(Job.status, Job.finish_reason, Job.analisis_version,
               Job.crawler_version, Job.pagerank_resumen, Job.seeds).where(Job.id == job_id)
    ).first()
    if job is None:
        return None

    html2xx = (Url.is_html.is_(True)) & (Url.status_group == "2xx")
    indexable = html2xx & Url.indexable.is_(True)
    fila = session.execute(
        select(
            func.count(Url.id),
            func.count(Url.id).filter(Url.status_group == "2xx"),
            func.count(Url.id).filter(Url.status_group == "3xx"),
            func.count(Url.id).filter(Url.status_group == "4xx"),
            func.count(Url.id).filter(Url.status_group == "5xx"),
            func.count(Url.id).filter(Url.status_group == "blocked"),
            func.count(Url.id).filter(html2xx),
            func.count(Url.id).filter(indexable),
            func.avg(Url.click_depth).filter(indexable),
            func.count(Url.id).filter(indexable & Url.click_depth.is_(None)),
        ).where(Url.job_id == job_id, Url.is_internal.is_(True))
    ).one()
    (urls, u2, u3, u4, u5, bloq, html, idx, prof, sin_camino) = fila

    por_tipo = session.execute(
        select(Issue.issue_type, Issue.severity, func.count(Issue.id))
        .where(Issue.job_id == job_id).group_by(Issue.issue_type, Issue.severity)
    ).all()
    por_sev = {"error": 0, "warning": 0, "info": 0}
    for _tipo, sev, n in por_tipo:
        por_sev[sev] = por_sev.get(sev, 0) + int(n)
    top = sorted(({"tipo": t, "severidad": s, "n": int(n)} for t, s, n in por_tipo),
                 key=lambda d: (-d["n"], d["tipo"], d["severidad"]))[:TOP_TIPOS]
    urls_con_error = session.execute(
        select(func.count(func.distinct(Issue.url_id)))
        .where(Issue.job_id == job_id, Issue.severity == "error")
    ).scalar() or 0

    pr = job.pagerank_resumen if isinstance(job.pagerank_resumen, dict) else {}
    reparto = pr.get("reparto") if isinstance(pr.get("reparto"), dict) else {}

    return {
        "criterio": CRITERIO,
        "status": job.status,
        "finish_reason": job.finish_reason,
        "analisis_version": job.analisis_version,
        "crawler_version": job.crawler_version,
        "host": _host(job.seeds),
        "urls": int(urls or 0), "urls_2xx": int(u2 or 0), "urls_3xx": int(u3 or 0),
        "urls_4xx": int(u4 or 0), "urls_5xx": int(u5 or 0), "urls_bloqueadas": int(bloq or 0),
        "paginas_html": int(html or 0), "indexables": int(idx or 0),
        "click_depth_medio": round(float(prof), 2) if prof is not None else None,
        "indexables_sin_camino": int(sin_camino or 0),
        "incidencias": sum(por_sev.values()),
        "errores": por_sev.get("error", 0), "avisos": por_sev.get("warning", 0),
        "info": por_sev.get("info", 0),
        "urls_con_error": int(urls_con_error),
        "top_tipos": top,
        "pagerank_desperdiciado": _fraccion(pr.get("desperdiciado")),
        "pagerank_indexable": _fraccion(reparto.get("indexable")),
        "segundos": round(time.monotonic() - t0, 3),
    }


def _como_uuid(job_id):
    """El worker y `run_analysis` pasan el id como texto; las columnas UUID
    de SQLAlchemy lo quieren como `uuid.UUID` (en SQLite revienta)."""
    import uuid

    return job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))


def _fraccion(v) -> float | None:
    try:
        return round(float(v), 4) if v is not None else None
    except (TypeError, ValueError):
        return None


def registrar(session, job_id, *, solo_si_falta: bool = False) -> dict[str, Any] | None:
    """Escribe (o rehace) la fila del job y hace `flush`; el commit es de
    quien llama. Devuelve el agregado, o None si no habia nada que hacer.

    `solo_si_falta`: no la rehace si ya hay una calculada con la misma version
    del analisis y el mismo criterio. Es lo que usa el worker al cerrar el
    rastreo: el analisis acaba de escribirla y contar otra vez lo mismo no
    aporta nada.
    """
    from sqlalchemy import select

    from shared.models import Job, ProfileRun

    job_id = _como_uuid(job_id)
    fila = session.execute(select(ProfileRun).where(ProfileRun.job_id == job_id)).scalar_one_or_none()
    if solo_si_falta and fila is not None:
        job = session.execute(select(Job.analisis_version, Job.status, Job.finish_reason)
                              .where(Job.id == job_id)).first()
        if job is not None and fila.analisis_version == job.analisis_version \
                and fila.criterio == CRITERIO:
            # Las cifras valen; solo se pone al dia la foto del estado, que el
            # analisis escribio con el job aun en `analyzing`.
            fila.status, fila.finish_reason = job.status, job.finish_reason
            session.flush()
            return None
    datos = agregar(session, job_id)
    if datos is None:
        return None
    if fila is None:
        fila = ProfileRun(job_id=job_id)
        session.add(fila)
    for clave, valor in datos.items():
        setattr(fila, clave, valor)
    from shared.models import _utcnow

    fila.calculado_at = _utcnow()
    session.flush()
    return datos


def registrar_sin_fallar(session, job_id, *, solo_si_falta: bool = False) -> bool:
    """`registrar` + commit, sin dejar escapar NADA (decisiones 74 y 77).

    Corre al final del analisis y al cerrar un rastreo: que la evolucion no se
    pueda escribir no puede dejar en `failed` un censo correcto. El `try`
    cubre la funcion entera, imports incluidos.
    """
    try:
        registrar(session, job_id, solo_si_falta=solo_si_falta)
        session.commit()
        return True
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        logger.exception("Job %s: no se pudo registrar su evolucion (profile_runs)", job_id)
        return False


def registrar_en_sesion_propia(job_id, *, solo_si_falta: bool = False) -> bool:
    """Lo mismo con una sesion propia: es lo que llama el worker."""
    session = None
    try:
        from shared.database import SessionLocal

        session = SessionLocal()
        return registrar_sin_fallar(session, job_id, solo_si_falta=solo_si_falta)
    except Exception:
        logger.exception("Job %s: no se pudo abrir sesion para su evolucion", job_id)
        return False
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# La serie
# ---------------------------------------------------------------------------
def motivo_no_comparable(job: dict[str, Any], fila: dict[str, Any] | None) -> str | None:
    """Por que este punto no entra en la linea. None = entra.

    `job` lleva `status`, `finish_reason`, `name`, `tipo`, `config` y
    `analisis_version` de `jobs`; `fila`, el agregado (o None si no lo hay).
    El orden es el de lo que mas pesa: un censo fallido es fallido aunque
    ademas sea un canario.
    """
    if fila is None:
        return "sin_agregado"
    status = job.get("status")
    if status != "completed":
        return status or "desconocido"
    if (job.get("finish_reason") or "") in MOTIVOS_TRUNCADO:
        return "truncado"
    tipo = tipo_de_rastreo(job.get("name"), job.get("tipo"))
    if tipo != "censo":
        return tipo
    if es_lista(job.get("config")):
        return "lista"
    if fila.get("criterio") != CRITERIO:
        return "desfasado"
    if fila.get("analisis_version") != job.get("analisis_version"):
        return "desfasado"
    return None


def _host(seeds) -> str:
    try:
        return (urlsplit((seeds or [None])[0] or "").hostname or "").lower() or "?"
    except Exception:
        return "?"


def clave_de_serie(job: dict[str, Any]) -> tuple[str, str]:
    """A que serie pertenece un job: su perfil, si no su programacion, si no
    el host de su primera semilla. Dos perfiles del mismo cliente (el sitio y
    el blog, o un competidor) son dos lineas: juntarlos mezclaria censos de
    sitios distintos."""
    if job.get("profile_id"):
        return "perfil", str(job["profile_id"])
    if job.get("programacion_id"):
        return "programacion", str(job["programacion_id"])
    return "host", job.get("host") or _host(job.get("seeds"))


def _cambio(antes: dict[str, Any], despues: dict[str, Any]) -> dict[str, Any] | None:
    """Que version cambio entre dos puntos comparables. Desconocida cuenta
    como cambio (decision 91): callarse equivale a afirmar que es la misma."""
    va, vd = antes.get("analisis_version"), despues.get("analisis_version")
    ra, rd = antes.get("crawler_version"), despues.get("crawler_version")
    analisis = va is None or vd is None or va != vd
    rastreo = ra is None or rd is None or ra != rd
    if not (analisis or rastreo):
        return None
    return {"analisis": analisis, "rastreo": rastreo,
            "analisis_antes": va, "analisis_despues": vd,
            "rastreo_antes": ra, "rastreo_despues": rd}


def tendencia(puntos: list[dict[str, Any]], metrica: str = "errores") -> dict[str, Any] | None:
    """El ultimo punto comparable frente al anterior comparable.

    `direccion`: `sube`, `baja`, `igual` o `no_comparable` —si entre los dos
    cambio la version del analisis— y `mejora` (True/False/None) segun el
    sentido de la metrica. None si no hay dos puntos comparables.
    """
    comparables = [p for p in puntos if p.get("comparable")]
    if len(comparables) < 2:
        return None
    antes, ahora = comparables[-2], comparables[-1]
    a, b = antes.get(metrica), ahora.get(metrica)
    salida = {
        "metrica": metrica, "antes": a, "ahora": b,
        "job_antes": antes["job_id"], "job_ahora": ahora["job_id"],
        "fecha_antes": antes.get("fecha"), "fecha_ahora": ahora.get("fecha"),
        "cambio_version": ahora.get("cambio_version"),
        "valores": [p.get(metrica) for p in comparables[-8:]],
        # Donde, dentro de `valores`, cambio la version del analisis: la
        # linea pequena del panel se corta ahi en vez de unir los dos lados.
        "cortes": [i for i, p in enumerate(comparables[-8:])
                   if i and (p.get("cambio_version") or {}).get("analisis")],
    }
    if (ahora.get("cambio_version") or {}).get("analisis"):
        salida.update(direccion="no_comparable", delta=None, mejora=None,
                      motivo="Entre los dos censos cambió la versión del análisis: "
                             "la diferencia puede ser nuestra, no del sitio. "
                             "Re-analiza el anterior con el código de hoy.")
        return salida
    if a is None or b is None:
        salida.update(direccion=None, delta=None, mejora=None)
        return salida
    delta = b - a
    direccion = "sube" if delta > 0 else ("baja" if delta < 0 else "igual")
    mejor = METRICAS.get(metrica, {}).get("mejor", "baja")
    mejora = None if direccion == "igual" else (direccion == mejor)
    salida.update(direccion=direccion, delta=round(delta, 4) if isinstance(delta, float) else delta,
                  mejora=mejora)
    return salida


def series(filas: Iterable[tuple[dict[str, Any], dict[str, Any] | None]],
           nombres: dict[tuple[str, str], str] | None = None,
           limite: int = 26) -> list[dict[str, Any]]:
    """Agrupa los puntos por serie y marca lo comparable y los cortes.

    `filas`: pares (job, agregado) en cualquier orden. `nombres`: el nombre de
    cada serie por su clave (perfil o programacion); el host se nombra solo.
    Cada serie se queda con sus `limite` puntos mas recientes, y las series se
    devuelven de la que tiene el punto mas reciente a la que menos.
    """
    nombres = nombres or {}
    grupos: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for job, fila in filas:
        clave = clave_de_serie(job)
        motivo = motivo_no_comparable(job, fila)
        punto = {
            "job_id": str(job["id"]), "name": job.get("name"), "fecha": job.get("created_at"),
            "status": job.get("status"), "finish_reason": job.get("finish_reason"),
            "tipo": tipo_de_rastreo(job.get("name"), job.get("tipo")),
            "comparable": motivo is None, "motivo": motivo,
            "analisis_version": (fila or {}).get("analisis_version", job.get("analisis_version")),
            "crawler_version": (fila or {}).get("crawler_version", job.get("crawler_version")),
            "cambio_version": None,
        }
        for c in COLUMNAS:
            punto[c] = (fila or {}).get(c)
        if punto["pagerank_desperdiciado"] is not None:
            # En la grafica, porcentaje; en la tabla, la fraccion.
            punto["pagerank_desperdiciado"] = round(punto["pagerank_desperdiciado"] * 100, 2)
        grupos.setdefault(clave, []).append(punto)

    salida = []
    for (tipo_clave, valor), puntos in grupos.items():
        puntos.sort(key=lambda p: (p["fecha"] is None, p["fecha"]))
        puntos = puntos[-limite:]
        anterior = None
        for p in puntos:
            if not p["comparable"]:
                continue
            if anterior is not None:
                p["cambio_version"] = _cambio(anterior, p)
            anterior = p
        nombre = nombres.get((tipo_clave, valor)) or (valor if tipo_clave == "host" else None)
        salida.append({
            "clave": f"{tipo_clave}:{valor}", "tipo_clave": tipo_clave,
            "profile_id": valor if tipo_clave == "perfil" else None,
            "nombre": nombre or valor,
            "puntos": puntos,
            "comparables": sum(1 for p in puntos if p["comparable"]),
            "tendencia_errores": tendencia(puntos, "errores"),
        })
    salida.sort(key=lambda s: _mas_reciente(s["puntos"]), reverse=True)
    return salida


def _mas_reciente(puntos, solo_comparables: bool = False) -> float:
    fechas = [p["fecha"].timestamp() for p in puntos
              if p.get("fecha") is not None and hasattr(p["fecha"], "timestamp")
              and (p["comparable"] or not solo_comparables)]
    return max(fechas, default=float("-inf"))


def leer(db, cliente_id, *, profile_id=None, limite: int = 26) -> dict[str, Any]:
    """La evolucion de un cliente (o de uno de sus perfiles) leida de la BD.

    Una consulta: `jobs` del cliente con su fila de `profile_runs`. Ni `urls`
    ni `issues` ni `links`: lo caro ya esta agregado.
    """
    from sqlalchemy import select

    from shared.models import Job, ProfileRun

    consulta = (
        select(Job.id, Job.name, Job.tipo, Job.status, Job.finish_reason, Job.config,
               Job.created_at, Job.profile_id, Job.programacion_id,
               Job.analisis_version, Job.crawler_version, ProfileRun)
        .outerjoin(ProfileRun, ProfileRun.job_id == Job.id)
        .where(Job.cliente_id == cliente_id, Job.status.in_(TERMINADOS))
    )
    if profile_id is not None:
        consulta = consulta.where(Job.profile_id == profile_id)
    filas = []
    sin_agregado = 0
    for r in db.execute(consulta).all():
        job = dict(r._mapping)
        run = job.pop("ProfileRun")
        if run is None:
            sin_agregado += 1
        job["host"] = getattr(run, "host", None)
        filas.append((job, _fila(run)))
    _hosts_que_faltan(db, [j for j, _ in filas])

    nombres = _nombres(db, [cliente_id], filas)
    return {
        "cliente_id": str(cliente_id),
        "profile_id": str(profile_id) if profile_id is not None else None,
        "series": series(filas, nombres, limite=limite),
        "sin_agregado": sin_agregado,
        "metricas": METRICAS,
        "criterio": CRITERIO,
    }


def _hosts_que_faltan(db, jobs: list[dict[str, Any]]) -> None:
    """El host de los jobs que lo necesitan para su serie (sin perfil ni
    programacion) y no lo traen en su fila: los que aun no tienen agregado.
    Solo esos leen `jobs.seeds`, que en un censo sembrado desde el sitemap
    pesa cientos de kB; los demas lo llevan copiado en `profile_runs.host`."""
    from sqlalchemy import select

    from shared.models import Job

    faltan = {j["id"]: j for j in jobs
              if not j.get("host") and not j.get("profile_id") and not j.get("programacion_id")}
    if not faltan:
        return
    for jid, seeds in db.execute(select(Job.id, Job.seeds).where(Job.id.in_(list(faltan)))).all():
        faltan[jid]["host"] = _host(seeds)


def _nombres(db, cliente_ids, filas) -> dict[tuple[str, str], str]:
    """El nombre de cada serie: el del perfil o el de la programacion."""
    from sqlalchemy import select

    from shared.models import CrawlProfile, Programacion

    nombres: dict[tuple[str, str], str] = {}
    for pid, nombre in db.execute(select(CrawlProfile.id, CrawlProfile.nombre)
                                  .where(CrawlProfile.cliente_id.in_(list(cliente_ids)))).all():
        nombres[("perfil", str(pid))] = nombre
    prog_ids = {j["programacion_id"] for j, _ in filas if j.get("programacion_id")}
    if prog_ids:
        for pid, nombre in db.execute(select(Programacion.id, Programacion.nombre)
                                      .where(Programacion.id.in_(prog_ids))).all():
            nombres[("programacion", str(pid))] = nombre
    return nombres


def _fila(run) -> dict[str, Any] | None:
    if run is None:
        return None
    d = {c: getattr(run, c) for c in COLUMNAS}
    d.update(criterio=run.criterio, analisis_version=run.analisis_version,
             crawler_version=run.crawler_version)
    return d


def tendencias_de_clientes(db, cliente_ids) -> dict[Any, dict[str, Any]]:
    """Para el panel de cartera: por cliente, la tendencia de errores de su
    serie mas reciente y el ultimo punto comparable. Una consulta para todos.
    """
    from sqlalchemy import select

    from shared.models import Job, ProfileRun

    ids = list(cliente_ids)
    if not ids:
        return {}
    por_cliente: dict[Any, list] = {i: [] for i in ids}
    for r in db.execute(
        select(Job.cliente_id, Job.id, Job.name, Job.tipo, Job.status, Job.finish_reason,
               Job.config, Job.created_at, Job.profile_id, Job.programacion_id,
               Job.analisis_version, Job.crawler_version, ProfileRun)
        .join(ProfileRun, ProfileRun.job_id == Job.id)
        .where(Job.cliente_id.in_(ids), Job.status.in_(TERMINADOS))
    ).all():
        job = dict(r._mapping)
        run = job.pop("ProfileRun")
        job["host"] = run.host
        por_cliente[job.pop("cliente_id")].append((job, _fila(run)))
    _hosts_que_faltan(db, [j for filas in por_cliente.values() for j, _ in filas])
    nombres = _nombres(db, ids, [f for filas in por_cliente.values() for f in filas])
    salida = {}
    for cid, filas in por_cliente.items():
        if not filas:
            continue
        # La serie con el punto COMPARABLE mas reciente: un canario fallido
        # de ayer no es «la ultima ejecucion» del cliente.
        candidatas = [s for s in series(filas, nombres, limite=12) if s["comparables"]]
        if not candidatas:
            continue
        serie = max(candidatas, key=lambda s: _mas_reciente(s["puntos"], solo_comparables=True))
        ultimo = [p for p in serie["puntos"] if p["comparable"]][-1]
        salida[cid] = {"serie": serie["nombre"], "clave": serie["clave"],
                       "ultimo_job": ultimo["job_id"], "ultima_fecha": ultimo["fecha"],
                       "tendencia": serie["tendencia_errores"],
                       "valores": [p["errores"] for p in serie["puntos"] if p["comparable"]][-8:],
                       "cortes": (serie["tendencia_errores"] or {}).get("cortes", [])}
    return salida
