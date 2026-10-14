"""Job management endpoints: create, list, get, cancel, delete."""

from __future__ import annotations

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
    JobUpdate,
    ImportResponse,
    JobCreate,
    JobResponse,
    PaginatedResponse,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


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


def borrar_job(db: Session, job: Job, *, confirmar: bool = True) -> None:
    """Borra un job y todo lo suyo. Lo usa tambien el borrado de un cliente
    con `con_jobs=true`; `confirmar=False` deja el commit a quien llama."""
    from shared.models import CrawlProfile, Decision, EnrichmentRun

    job_id = job.id
    # If the job is still active, signal the crawler to stop first so it does
    # not keep writing rows (with a now-dangling job_id FK) while we delete.
    if job.status in ("pending", "running"):
        try:
            get_redis().set(f"job:{job_id}:cancel", "1")
        except Exception:
            pass

    # El perfil que lo tenia como ultimo rastreo se queda sin el, no roto.
    # A mano y no solo por el ON DELETE SET NULL: igual en cualquier motor.
    db.query(CrawlProfile).filter(CrawlProfile.last_job_id == job_id).update(
        {CrawlProfile.last_job_id: None}, synchronize_session=False)
    # Analisis enriquecido (#40): a mano, como links e issues (en SQLite no
    # hay ON DELETE CASCADE). Una ejecucion en curso se para sola: en el
    # siguiente lote su fila ya no existe y el worker deja de llamar.
    db.query(Decision).filter(Decision.job_id == job_id).delete(synchronize_session=False)
    db.query(EnrichmentRun).filter(EnrichmentRun.job_id == job_id).delete(
        synchronize_session=False)
    # Delete associated records in bulk (faster than cascade for large sets).
    db.query(Link).filter(Link.job_id == job_id).delete(synchronize_session=False)
    db.query(Issue).filter(Issue.job_id == job_id).delete(synchronize_session=False)
    # Urls cascade handles html_meta, headings, etc. via DB-level ON DELETE CASCADE.
    db.query(Url).filter(Url.job_id == job_id).delete(synchronize_session=False)
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
