"""Job management endpoints: create, list, get, cancel, delete."""

from __future__ import annotations

import time
import uuid

from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from shared.cola import encolar
from shared.database import get_session
from shared.models import Job, Url, Issue, Link

from api.backup import ConflictError, import_backup_zip
from api.dependencies import get_redis
from api.schemas import (
    BorradoEnMasa,
    BorradoEnMasaResultado,
    JobUpdate,
    ImportResponse,
    JobCreate,
    JobResponse,
    PaginatedResponse,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _reloj() -> float:
    """El reloj del presupuesto del borrado en masa, en una funcion propia.

    No es ceremonia: un test que necesite agotar el presupuesto tiene que
    poder cambiar el reloj de ESTE modulo. Parchear `time.monotonic` lo cambia
    para todo el proceso —httpx, SQLite, el propio pytest— y la suite se
    cuelga en vez de fallar; me paso al escribir el test del presupuesto.
    """
    return time.monotonic()


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/progress  --  real-time crawl progress from Redis
# ---------------------------------------------------------------------------
@router.get("/{job_id}/progress")
def get_progress(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Manda el contador de BD, no el de Redis. Miden cosas distintas: el de
    # Redis cuenta respuestas que llegan al spider, mientras que el spider
    # ademas guarda una fila por cada salto de una cadena de redirecciones sin
    # sumarlo. En un sitio con muchos 301 el de Redis se queda corto de forma
    # permanente (medido: 6.050 frente a 7.259 filas reales, un 17%) y la UI
    # aparentaba un rastreo estancado cuando iba fino. El de BD se refresca
    # cada 5s y cuadra con lo que luego se ve en los resultados.
    crawled_count = job.total_urls_crawled or 0
    if job.status in ("completed", "failed", "cancelled", "stalled"):
        # Terminado: la verdad son las filas guardadas. El contador de la
        # tuberia se escribia por proceso, asi que los rastreos reanudados
        # ANTES del arreglo se quedaron con la cuenta del ultimo tramo (802
        # frente a 34.704 filas reales en uno medido). Una consulta indexada
        # por job_id, y solo cuando el rastreo ya no se mueve.
        reales = (
            db.query(func.count(Url.id)).filter(Url.job_id == job_id).scalar() or 0
        )
        crawled_count = max(crawled_count, reales)
    live_count = None
    try:
        r = get_redis()
        val = r.get(f"job:{job_id}:crawled_count")
        if val is not None:
            live_count = int(val)
            # Solo al arrancar, antes del primer volcado del contador de BD.
            if crawled_count == 0:
                crawled_count = live_count
    except Exception:
        live_count = None

    # Cola pendiente: la publica el propio spider leyendo la longitud del
    # planificador de Scrapy. Es exacta y llega a 0 al terminar. Los rastreos
    # anteriores a ese cambio no tienen la clave y devuelven null.
    pending_count = None
    pending_exacto = False
    try:
        r = get_redis()
        val = r.get(f"job:{job_id}:pending_count")
        if val is not None:
            pending_count = int(val)
            pending_exacto = True
    except Exception:
        pending_count = None

    # Sin respaldo desde la BD a proposito. Antes, cuando la clave de Redis no
    # estaba (rastreos anteriores al cambio), se calculaba la cola con un
    # NOT EXISTS sobre `links`. Ese calculo costaba 7 s con 7,8 M de enlaces —42 s
    # en frio— y este endpoint lo llama la interfaz CADA 2 SEGUNDOS mientras hay
    # un rastreo abierto, asi que dejaba la aplicacion inutilizable en los jobs
    # grandes. Y encima devolvia un numero que ya sabiamos que era erroneo:
    # contaba como pendientes las URLs que Scrapy descarta al seguir un redirect
    # hacia algo ya rastreado (347.780 "pendientes" en un job terminado).
    #
    # Un dato desconocido se declara desconocido; no se paga con siete segundos
    # por una cifra que ademas esta mal. Para saber si un rastreo cubrio todo
    # esta finish_reason, que si es fiable.

    return {
        "job_id": str(job_id),
        "status": job.status,
        "crawled_count": crawled_count,
        "total_urls_crawled_db": job.total_urls_crawled,
        # Respuestas contadas por el spider. Excluye los saltos intermedios de
        # redireccion, asi que es normal que quede por debajo de crawled_count.
        "responses_count": live_count,
        "pending_count": pending_count,
        # True = cola real del planificador. False = no se sabe (rastreo
        # anterior al cambio); pending_count viene null en ese caso.
        "pending_exacto": pending_exacto,
        # "finished" = frontera agotada (dato completo)
        # "max_urls_reached" = cortado por el tope (dato PARCIAL)
        "finish_reason": job.finish_reason,
        "truncated": job.finish_reason == "max_urls_reached",
    }


# ---------------------------------------------------------------------------
# POST /api/jobs  --  create a new crawl job
# ---------------------------------------------------------------------------
@router.post("", response_model=JobResponse, status_code=201)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_session),
):
    from api.routers.clientes import resolver_cliente_y_perfil, resolver_config

    cliente, perfil = resolver_cliente_y_perfil(
        db, cliente_id=payload.cliente_id, profile_id=payload.profile_id,
        client_id_texto=payload.client_id)

    if cliente is None:
        # Sin cliente, exactamente lo de siempre: el JobConfig entero.
        config = payload.config.model_dump()
    else:
        # Con cliente, solo lo que el job CAMBIA se pone encima de lo heredado.
        config = resolver_config(
            cliente, perfil, payload.config.model_dump(exclude_unset=True))

    seeds = payload.seeds or (list(perfil.seeds or []) if perfil else [])
    if not seeds:
        raise HTTPException(
            status_code=422, detail="At least one valid seed URL is required")

    job = Job(
        id=uuid.uuid4(),
        name=payload.name,
        seeds=seeds,
        # El texto se conserva como etiqueta: con cliente, su slug, para que
        # quien siga filtrando por texto (scripts, informes) vea lo mismo.
        client_id=cliente.slug if cliente else payload.client_id,
        cliente_id=cliente.id if cliente else None,
        profile_id=perfil.id if perfil else None,
        status="pending",
        config=config,
    )
    db.add(job)
    db.flush()
    if perfil is not None:
        # Despues del flush: el UPDATE del perfil apunta a un job que tiene que
        # existir ya, y sin relacion entre los dos modelos SQLAlchemy no sabe
        # en que orden mandarlos.
        perfil.last_job_id = job.id
    db.commit()
    db.refresh(job)

    # Push the job id onto the Redis pending queue so a worker picks it up.
    r = get_redis()
    encolar(r, job.id)

    return _con_cliente(db, [job])[0]


def _con_cliente(db: Session, jobs: list[Job]) -> list[JobResponse]:
    """JobResponse con el nombre del cliente, en UNA consulta para la pagina."""
    from api.routers.clientes import nombres_de_clientes

    from shared.clientes import tipo_de_rastreo

    nombres = nombres_de_clientes(db, (j.cliente_id for j in jobs))
    salida = []
    for j in jobs:
        r = JobResponse.model_validate(j)
        r.cliente_nombre = nombres.get(j.cliente_id)
        r.tipo_efectivo = tipo_de_rastreo(j.name, j.tipo)
        salida.append(r)
    return salida


def _tipo_sql():
    """`tipo_de_rastreo` en SQL, con las mismas pistas, para poder filtrar."""
    from sqlalchemy import case, func, or_

    from shared.clientes import PISTAS_CANARIO, PISTAS_PRUEBA

    nombre = func.lower(Job.name)
    return case(
        (Job.tipo.isnot(None), Job.tipo),
        (or_(*[nombre.contains(p, autoescape=True) for p in PISTAS_CANARIO]), "canario"),
        (or_(*[nombre.contains(p, autoescape=True) for p in PISTAS_PRUEBA]), "prueba"),
        else_="censo",
    )


# ---------------------------------------------------------------------------
# GET /api/jobs  --  list jobs with pagination / filters
# ---------------------------------------------------------------------------
@router.get("", response_model=PaginatedResponse[JobResponse])
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    client_id: str | None = Query(
        None, description="UUID del cliente, su slug o el texto de siempre"),
    profile_id: uuid.UUID | None = Query(None),
    tipo: str | None = Query(None, description="censo | canario | prueba (marcado o deducido del nombre)"),
    buscar: str | None = Query(None, max_length=200, description="En el nombre o en la primera semilla"),
    sin_cliente: bool = Query(False, description="Solo los rastreos sin cliente asignado"),
    db: Session = Depends(get_session),
):
    q = db.query(Job)
    if sin_cliente:
        q = q.filter(Job.cliente_id.is_(None))
    if tipo is not None:
        q = q.filter(_tipo_sql() == tipo)
    if buscar:
        from sqlalchemy import String, cast, func, or_

        t = buscar.strip().lower()
        # `seeds` es JSON: se busca en su texto, que basta para «lopesan.com».
        q = q.filter(or_(func.lower(Job.name).contains(t, autoescape=True),
                         func.lower(cast(Job.seeds, String)).contains(t, autoescape=True)))

    if status is not None:
        q = q.filter(Job.status == status)
    if client_id is not None:
        q = q.filter(_filtro_cliente(db, client_id))
    if profile_id is not None:
        q = q.filter(Job.profile_id == profile_id)

    total = q.count()
    pages = max(1, -(-total // page_size))  # ceiling division

    items = (
        q.order_by(Job.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PaginatedResponse[JobResponse](
        items=_con_cliente(db, items),
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


def _filtro_cliente(db: Session, valor: str):
    """`?client_id=` acepta el UUID del cliente, su slug o el texto viejo.

    Por slug o texto se casan TAMBIEN los jobs que aun no tienen `cliente_id`
    pero llevan ese texto exacto: si la migracion no ha pasado todavia (o un
    job entro por un camino que no resuelve cliente), filtrar solo por la FK
    los haria desaparecer de la lista de su cliente.
    """
    from sqlalchemy import or_

    from shared.clientes import slug_de_cliente
    from shared.models import Client

    try:
        return Job.cliente_id == uuid.UUID(valor)
    except ValueError:
        pass
    cliente = db.query(Client).filter(Client.slug == slug_de_cliente(valor)).one_or_none()
    if cliente is None:
        return Job.client_id == valor
    return or_(Job.cliente_id == cliente.id,
               (Job.cliente_id.is_(None)) & (Job.client_id == valor))


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}  --  single job detail
# ---------------------------------------------------------------------------
@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _con_cliente(db, [job])[0]


# ---------------------------------------------------------------------------
# PATCH /api/jobs/{job_id}  --  nombre, tipo y cliente (UX-07/08)
# ---------------------------------------------------------------------------
@router.patch("/{job_id}", response_model=JobResponse)
def update_job(
    job_id: uuid.UUID,
    cuerpo: JobUpdate,
    db: Session = Depends(get_session),
):
    """Ordenar lo que ya hay: renombrar, marcar como censo/canario/prueba y
    asignar el cliente a los rastreos que se quedaron sin el (los de antes de
    la decision 83, como los de Druni o los Lopesan de julio).

    No toca nada de lo rastreado ni de lo analizado, ni `config`: cambiar el
    cliente no cambia la configuracion con la que se hizo el censo.
    """
    from shared.models import Client

    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    cambios = cuerpo.model_dump(exclude_unset=True)
    if "name" in cambios and cambios["name"] is not None:
        job.name = cambios["name"].strip()
    if "tipo" in cambios:
        job.tipo = cambios["tipo"]
    if "cliente_id" in cambios:
        nuevo = cambios["cliente_id"]
        if nuevo is None:
            job.cliente_id = None
        else:
            cliente = db.query(Client).filter(Client.id == nuevo).one_or_none()
            if cliente is None:
                raise HTTPException(status_code=422, detail="Ese cliente no existe")
            job.cliente_id = cliente.id
            # El texto viejo se conserva como pista (decision 83); si no habia
            # ninguno, se pone el slug para que los filtros por texto lo vean.
            if not job.client_id:
                job.client_id = cliente.slug
    db.commit()
    db.refresh(job)
    return _con_cliente(db, [job])[0]


# ---------------------------------------------------------------------------
# PATCH /api/jobs/{job_id}/cancel  --  cancel a running / pending job
# ---------------------------------------------------------------------------
@router.patch("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel a job with status '{job.status}'",
        )

    job.status = "cancelled"
    db.commit()
    db.refresh(job)

    # Signal the crawler workers to stop processing this job.
    # The spider checks this key on every response.
    r = get_redis()
    r.set(f"job:{job.id}:cancel", "1")

    return job


# ---------------------------------------------------------------------------
# POST /api/jobs/{job_id}/resume  --  re-queue a failed/cancelled job
# ---------------------------------------------------------------------------
@router.post("/{job_id}/resume", response_model=JobResponse)
def resume_job(
    job_id: uuid.UUID,
    overrides: dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_session),
):
    """Re-queue a failed/cancelled job. The spider auto-detects already-crawled
    URLs and the discovered-but-not-yet-crawled frontier from the database, so
    the crawl resumes rather than restarting.

    Optional body: shallow-merged config overrides. Supported keys:
      - top-level: ``concurrent_requests``, ``concurrent_requests_per_domain``,
        ``render_js``, ``robots_mode``, ``user_agent``, ``impersonate``
      - sub-objects: ``crawl_behavior`` (deep-merged)
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("failed", "cancelled", "completed"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot resume a job with status '{job.status}'",
        )

    # Apply optional config overrides (shallow + crawl_behavior deep merge)
    if overrides:
        cfg = dict(job.config or {})
        cb = dict(cfg.get("crawl_behavior", {}))
        if isinstance(overrides.get("crawl_behavior"), dict):
            cb.update(overrides["crawl_behavior"])
        cfg["crawl_behavior"] = cb
        for key in (
            "concurrent_requests",
            "concurrent_requests_per_domain",
            "render_js",
            "robots_mode",
            "user_agent",
            "impersonate",
            "max_urls",
            "max_depth",
        ):
            if key in overrides:
                cfg[key] = overrides[key]
        job.config = cfg

    job.status = "pending"
    job.completed_at = None
    db.commit()
    db.refresh(job)

    # Clear any stale cancel signal and re-queue
    r = get_redis()
    try:
        r.delete(f"job:{job_id}:cancel")
    except Exception:
        pass
    encolar(r, job.id)

    return job


# ---------------------------------------------------------------------------
# DELETE /api/jobs/{job_id}  --  delete a job and all associated data
# ---------------------------------------------------------------------------
@router.delete("/{job_id}", status_code=204)
def delete_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
):
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    borrar_job(db, job)
    return None


def borrar_job(db: Session, job: Job, *, confirmar: bool = True,
               trozo: int | None = None, hasta: float | None = None) -> dict[str, Any]:
    """Borra un job y todo lo suyo. Devuelve que se borro y si termino.

    Lo usa tambien el borrado de un cliente con `con_jobs=true`;
    `confirmar=False` deja el commit a quien llama.

    `trozo` parte el borrado de las tablas grandes en lotes de ese tamano, con
    un commit por lote, y `hasta` es el instante (`time.monotonic`) en el que
    hay que dejarlo. Las dos las usa el borrado en masa:

    - **Un censo grande no cabe en una transaccion comoda.** Borrar los 37,5
      millones de enlaces de un censo de Druni en un solo DELETE son minutos
      con la tabla tomada y un WAL enorme. Por lotes, cada uno se confirma y se
      suelta.
    - **Lo borrado a medias es consistente y se puede retomar.** Primero las
      hijas y la fila de `jobs` al final, asi que si se agota el tiempo queda un
      job con menos filas —no un huerfano sin job— y la siguiente pasada sigue
      donde iba. Por eso el presupuesto se puede cortar sin miedo.

    Sin `trozo` hace exactamente lo de siempre: un DELETE por tabla y un commit.

    Con `trozo`, lo primero que se confirma es `status='deleting'`. Un borrado
    por trozos se puede quedar a medias y no volver nadie a terminarlo (la
    pestana se cierra, un despliegue recrea la API), y un censo sin enlaces ni
    incidencias y con la mitad de las URLs no puede seguir pareciendo un
    `completed`: lo elegiria la comparacion como referencia (decisiones 78 y
    86, y sus alertas callarian sobre las pocas paginas comunes) y el archivado
    lo guardaria en un Parquet vacio (decision 95). Ninguno de los dos mira un
    job que no este terminado, y `deleting` no lo esta.
    """
    from shared.models import CleaningRuleset, CrawlProfile, Decision, EnrichmentRun

    job_id = job.id
    filas: dict[str, int] = {}
    # If the job is still active, signal the crawler to stop first so it does
    # not keep writing rows (with a now-dangling job_id FK) while we delete.
    if job.status in ("pending", "running"):
        try:
            get_redis().set(f"job:{job_id}:cancel", "1")
        except Exception:
            pass
    if trozo and job.status != "deleting":
        job.status = "deleting"
        db.commit()

    # El perfil que lo tenia como ultimo rastreo se queda sin el, no roto.
    # A mano y no solo por el ON DELETE SET NULL: igual en cualquier motor.
    db.query(CrawlProfile).filter(CrawlProfile.last_job_id == job_id).update(
        {CrawlProfile.last_job_id: None}, synchronize_session=False)
    # Analisis enriquecido (#40) y reglas de limpieza: a mano, como links e
    # issues (en SQLite no hay ON DELETE CASCADE). Una ejecucion de
    # enriquecido en curso se para sola: en el siguiente lote su fila ya no
    # existe y el worker deja de llamar.
    #
    # Lo del analisis semantico y Search Console NO se borra a mano, y es a
    # proposito: `semantic_analyses` cuelga del job y el resto de las URLs,
    # los cuatro con ON DELETE CASCADE. Importar aqui `shared.semantic_models`
    # registraria sus tablas de pgvector en el `MetaData` compartido, y
    # entonces el `create_all` de los tests contra un Postgres SIN la
    # extension (el `postgres:16-alpine` de CI) revienta — se llevaria por
    # delante `test_pagerank_db.py`, que no tiene nada que ver con esto.
    for nombre, modelo in (("decisions", Decision), ("enrichment_runs", EnrichmentRun),
                           ("cleaning_rulesets", CleaningRuleset)):
        filas[nombre] = db.query(modelo).filter(modelo.job_id == job_id).delete(
            synchronize_session=False)

    # Las grandes. `urls` arrastra html_meta, headings, page_content y
    # demas por el ON DELETE CASCADE de la BD. `custom_extraction` (decision
    # 104) lleva `job_id` y va a mano: en SQLite no hay CASCADE, y por trozos
    # como las otras, porque son una fila por URL y regla.
    from shared.models import CustomExtraction

    for nombre, modelo in (("links", Link), ("issues", Issue),
                           ("custom_extraction", CustomExtraction), ("urls", Url)):
        borradas = 0
        while True:
            if trozo:
                sub = (db.query(modelo.id).filter(modelo.job_id == job_id)
                       .limit(trozo).scalar_subquery())
                n = db.query(modelo).filter(modelo.id.in_(sub)).delete(
                    synchronize_session=False)
            else:
                n = db.query(modelo).filter(modelo.job_id == job_id).delete(
                    synchronize_session=False)
            borradas += n
            if not trozo:
                break
            db.commit()
            if n < trozo:
                break
            if hasta is not None and _reloj() >= hasta:
                filas[nombre] = borradas
                return {"completo": False, "filas": filas}
        filas[nombre] = borradas

    db.delete(job)
    if confirmar:
        db.commit()
        # El Parquet de sus enlaces, si estaban archivados (decision 95). Despues
        # del commit: si el borrado de la BD fallase, el job seguiria pidiendo
        # su fichero.
        from shared import archivo_enlaces
        archivo_enlaces.borrar(job_id)
    else:
        db.flush()
    return {"completo": True, "filas": filas}


# ---------------------------------------------------------------------------
# POST /api/jobs/eliminar  --  borrado en masa (decision 96)
# ---------------------------------------------------------------------------
# Un censo de produccion ocupa gigas y la instalacion acumula pruebas de 40
# URLs, canarios y censos de hace meses que ya no se van a mirar. Borrarlos de
# uno en uno, entrando en cada ficha y teclando «eliminar», es lo que hace que
# no se borren nunca y que el aviso lo acabe dando el disco del VPS.
#
# Tres cosas que este endpoint se NIEGA a hacer:
#   - borrar un rastreo en marcha (`pending`, `running`, `analyzing`), ni
#     siquiera con `forzar`: hay que cancelarlo y esperar a que pare,
#   - borrar sin `forzar` el censo de referencia de una programacion activa, porque el
#     siguiente rastreo de esa serie se quedaria sin con que compararse y un
#     silencio se lee igual que «no ha cambiado nada» (decisiones 78 y 86),
#   - y borrar a partir de un FILTRO: solo acepta ids, uno a uno. Un filtro se
#     puede desplazar entre lo que se vio en la lista y lo que llega aqui
#     —otro rastreo termina y entra en la pagina—, y esto no se recupera.
MAX_IDS_POR_PETICION = 500
TROZO_DE_BORRADO = 200_000


def _candado_ocupado(db: Session, job_id) -> bool:
    """¿Tiene alguien el candado del censo (analisis, archivado)? Decisiones 93 y 95.

    El re-analisis de la referencia (93) cae justo sobre censos superados, que
    son los que se borran: un `completed` puede estar re-analizandose sin que su
    `status` lo diga. Se prueba y se suelta en el acto, sin retenerlo: en cuanto
    `borrar_job` marca `deleting`, ni la comparacion ni el archivado lo eligen.
    En SQLite no hay advisory locks.
    """
    from sqlalchemy import text

    from shared.candados import clave_de_job

    if not db.get_bind().dialect.name.startswith("postgres"):
        return False
    clave = clave_de_job(job_id)
    if not db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": clave}).scalar():
        return True
    db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": clave})
    return False


def _motivo_para_no_borrar(db: Session, job: Job, *, forzar: bool = False
                           ) -> tuple[str, bool] | None:
    """Por que NO se borra este rastreo y si `forzar` lo salta. `None` = adelante.

    Un rastreo en marcha no se borra NI con `forzar`: el pipeline sigue
    insertando enlaces mientras se borran, y con `analyzing` el `DELETE` de la
    fila de `jobs` espera al `FOR UPDATE` que el analisis tiene toda la pasada
    —43 minutos en un censo como Druni—, con la peticion colgada mucho mas
    alla de su presupuesto. Se cancela, se espera a que pare y se borra.
    """
    from shared.models import Programacion

    if job.status in ("pending", "running", "analyzing"):
        return (f"está en marcha ({job.status}): cancélalo y espera a que pare "
                "antes de borrarlo", False)
    if _candado_ocupado(db, job.id):
        return ("lo está analizando o archivando otro proceso (por ejemplo, el "
                "re-análisis de la referencia tras un rastreo): prueba en unos minutos",
                False)
    if forzar:
        return None

    if job.programacion_id and job.status == "completed" and job.started_at:
        programacion = (db.query(Programacion)
                        .filter(Programacion.id == job.programacion_id).one_or_none())
        if programacion is not None and programacion.activa:
            mas_nuevo = (
                db.query(Job.id)
                .filter(Job.id != job.id, Job.programacion_id == job.programacion_id,
                        Job.status == "completed", Job.started_at.isnot(None),
                        Job.started_at > job.started_at)
                .first()
            )
            if mas_nuevo is None:
                return (f"es el censo de referencia de la programación "
                        f"«{programacion.nombre}»: el próximo rastreo de esa serie "
                        "se quedaría sin con qué compararse", True)
    return None


def _avisos_por_host(db: Session, a_borrar: list[Job]) -> dict[uuid.UUID, str]:
    """«Con esto se va el último censo completado de X», por job.

    No impide nada: la referencia de un rastreo lanzado a mano se elige por
    host (decision 86), y quedarse sin censo de un sitio solo muerde si ese
    sitio se vuelve a rastrear, lo que puede no pasar nunca. La serie
    programada si vuelve a correr seguro, y por eso esa SI es un guardia.

    Un solo recorrido de `jobs` para todos: el host no es una columna (`seeds`
    es JSON) y preguntarlo job a job serian tantos recorridos como ids.
    """
    from shared.comparacion import _host

    completados_por_host: dict[str, int] = {}
    for j in db.query(Job.id, Job.seeds).filter(Job.status == "completed").yield_per(500):
        host = _host((j.seeds or [None])[0])
        if host:
            completados_por_host[host] = completados_por_host.get(host, 0) + 1

    se_van: dict[str, int] = {}
    for job in a_borrar:
        host = _host((job.seeds or [None])[0])
        if host and job.status == "completed":
            se_van[host] = se_van.get(host, 0) + 1

    avisos = {}
    for job in a_borrar:
        host = _host((job.seeds or [None])[0])
        if not host or job.status != "completed":
            continue
        if completados_por_host.get(host, 0) - se_van.get(host, 0) <= 0:
            avisos[job.id] = (f"con esto {host} se queda sin ningún censo completado: "
                              "el próximo rastreo del sitio no tendrá con qué compararse")
    return avisos


@router.post("/eliminar", response_model=BorradoEnMasaResultado)
def eliminar_jobs(
    cuerpo: BorradoEnMasa,
    db: Session = Depends(get_session),
):
    """Borra varios rastreos, cada uno en su propia transaccion.

    `simular=true` no borra nada y devuelve lo mismo: el mismo recorrido y los
    mismos guardias, para que la vista previa del dialogo no pueda decir una
    cosa y el borrado hacer otra.

    `segundos` es un presupuesto de tiempo, no un limite de trabajo: cuando se
    agota, lo que no se ha terminado vuelve en `pendientes` y basta con
    repetir la peticion con esos ids. Asi un lote de censos grandes no depende
    de que la peticion HTTP sobreviva veinte minutos —ni de que no haya un
    despliegue en vuelo, que recrea el contenedor de la API y se lleva la
    peticion por delante— y la interfaz puede pintar el avance.
    """
    from shared.almacenamiento import NOTA_DE_ESPACIO, tamano_de_jobs

    # Ids repetidos: se cuentan una vez. Pedir dos veces el mismo borrado no
    # es un error del que haya que avisar.
    ids: list[uuid.UUID] = list(dict.fromkeys(cuerpo.ids))
    if len(ids) > MAX_IDS_POR_PETICION:
        raise HTTPException(
            status_code=422,
            detail=f"Como mucho {MAX_IDS_POR_PETICION} rastreos por petición; "
                   f"se han pedido {len(ids)}")

    # El presupuesto empieza YA: medir tambien es tiempo de la peticion, y si
    # no contara, cada pasada duraria sus `segundos` mas los 20 s de medir.
    hasta = _reloj() + cuerpo.segundos

    jobs = {j.id: j for j in db.query(Job).filter(Job.id.in_(ids)).all()}

    omitidos: list[dict[str, Any]] = []
    eliminados: list[dict[str, Any]] = []
    pendientes: list[uuid.UUID] = []
    filas_totales: dict[str, int] = {}
    bytes_conocidos, sin_medir = 0, False
    archivo_bytes, archivo_sin_medir = 0, False

    candidatos = []
    for i in ids:
        job = jobs.get(i)
        if job is None:
            omitidos.append({"id": i, "nombre": None, "motivo": "ya no existe",
                             "forzable": False})
            continue
        motivo = _motivo_para_no_borrar(db, job, forzar=cuerpo.forzar)
        if motivo:
            omitidos.append({"id": i, "nombre": job.name, "motivo": motivo[0],
                             "forzable": motivo[1]})
            continue
        candidatos.append(job)

    # En la vista previa el tamano se mide de una vez para todos. Al borrar,
    # cada censo se mide justo ANTES de empezarlo (despues ya no hay filas que
    # contar) y una sola vez en toda la operacion: un censo que no se llega a
    # empezar en esta pasada no se mide —lo hara la pasada que lo empiece— y
    # uno que ya estaba a medias (`deleting`) no se vuelve a medir, porque lo
    # que queda no es lo que ocupaba y sumarlo como «liberado» daria una cifra
    # corta con cara de completa. Va a None —«no se sabe»— y la interfaz suma
    # lo liberado con lo que midio la vista previa, que es de antes de empezar.
    # Si no se puede medir viene a None (ver shared/almacenamiento.py) y el
    # borrado sigue igual.
    tamanos = tamano_de_jobs(db, [j.id for j in candidatos]) if (
        cuerpo.simular and candidatos) else {}

    # Los avisos solo en la vista previa: son para decidir, y una vez borrado
    # no hay nada que decidir.
    avisos = _avisos_por_host(db, candidatos) if cuerpo.simular else {}

    intentado = False
    for job in candidatos:
        if not cuerpo.simular:
            # El primero entra siempre, aunque medir se haya comido el
            # presupuesto: `borrar_job` hace al menos un trozo antes de mirar
            # el reloj, y asi cada pasada avanza algo. Una pasada sin avance
            # la interfaz la lee como atasco y para.
            if intentado and _reloj() >= hasta:
                pendientes.append(job.id)
                continue
            intentado = True
            if job.status != "deleting":
                tamanos.update(tamano_de_jobs(db, [job.id]))
        medida = tamanos.get(job.id)
        if medida is None:
            # Sin medir (ya estaba a medias): ni la base ni su Parquet se saben.
            archivado = bool(job.enlaces_archivados)
            medida = {"archivado": archivado, "archivo_bytes": None if archivado else 0}
        fila = {"id": job.id, "nombre": job.name, "status": job.status,
                "created_at": job.created_at,
                "filas": medida.get("filas"), "bytes": medida.get("bytes"),
                # El Parquet de sus enlaces (decision 95): `borrar_job` lo
                # quita con el job, y ese espacio SI vuelve al disco.
                "archivado": bool(medida.get("archivado")),
                "archivo_bytes": medida.get("archivo_bytes"),
                "aviso": avisos.get(job.id)}

        if not cuerpo.simular:
            resultado = borrar_job(db, job, trozo=TROZO_DE_BORRADO, hasta=hasta)
            for tabla, n in resultado["filas"].items():
                filas_totales[tabla] = filas_totales.get(tabla, 0) + n
            if not resultado["completo"]:
                # Se quedo a medias: faltan filas y la de `jobs` sigue ahi,
                # asi que repetir la peticion con este id lo termina.
                pendientes.append(job.id)
                continue

        eliminados.append(fila)
        if medida.get("archivado") and medida.get("archivo_bytes") is None:
            archivo_sin_medir = True
        else:
            archivo_bytes += medida.get("archivo_bytes") or 0
        if medida.get("bytes") is None:
            sin_medir = True
        else:
            bytes_conocidos += medida["bytes"]

    return BorradoEnMasaResultado(
        simulado=cuerpo.simular,
        eliminados=eliminados,
        omitidos=omitidos,
        pendientes=pendientes,
        # None = no se pudo medir ni uno. Un 0 se leeria como «no ocupaban
        # nada», que es justo lo contrario de lo que se quiere saber aqui.
        bytes_aprox=None if (sin_medir and not bytes_conocidos) else bytes_conocidos,
        bytes_incompletos=sin_medir,
        archivo_bytes=archivo_bytes,
        archivo_incompleto=archivo_sin_medir,
        filas=filas_totales,
        nota=NOTA_DE_ESPACIO,
    )


# ---------------------------------------------------------------------------
# POST /api/jobs/import  --  import a backup ZIP
# ---------------------------------------------------------------------------
@router.post("/import", response_model=ImportResponse, status_code=201)
async def import_job(
    file: UploadFile = File(...),
    preserve_job_id: bool = Query(False),
    db: Session = Depends(get_session),
):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Se requiere un archivo .zip")

    try:
        result = import_backup_zip(file.file, preserve_job_id, db)
    except ConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al importar: {exc}")

    return result
