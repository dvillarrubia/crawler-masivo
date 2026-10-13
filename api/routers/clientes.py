"""Clientes y sus perfiles de rastreo (#36, F1).

Lo que hace falta saber antes de tocar esto:

- **Ningun endpoint devuelve un secreto.** Las integraciones guardan el NOMBRE
  de la variable de entorno que tiene la clave, y la respuesta dice si esa
  variable esta puesta (`configurada`), nunca su valor (decision 83). La API
  no tiene autenticacion: lo que salga por aqui lo lee cualquiera.
- **La herencia se resuelve al crear el job** (`resolver_config`): cliente ->
  perfil -> lo que cambie el job, y lo resuelto se copia en `jobs.config`.
  Editar un cliente no cambia ningun job ya creado.
- **Borrar un cliente no borra sus rastreos** salvo que se pida con
  `con_jobs=true`: son censos entregados. Quedan sin cliente, y el
  `client_id` de texto se conserva como pista.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.clientes import cliente_por_texto, fusionar_config, slugificar
from shared.credenciales import esta_configurada
from shared.database import get_session
from shared.models import Client, CrawlProfile, Job, Programacion

from api.schemas import (
    ClientCreate,
    ClientResponse,
    ClientUpdate,
    ConfigResueltaResponse,
    IntegracionResponse,
    JobConfig,
    ProfileCreate,
    ProfileResponse,
    ProfileUpdate,
)

router = APIRouter(prefix="/api/clients", tags=["clients"])


# ---------------------------------------------------------------------------
# Piezas que usan tambien jobs.py y programaciones.py
# ---------------------------------------------------------------------------
def resolver_cliente_y_perfil(
    db: Session,
    *,
    cliente_id: uuid.UUID | None = None,
    profile_id: uuid.UUID | None = None,
    client_id_texto: str | None = None,
) -> tuple[Client | None, CrawlProfile | None]:
    """El cliente y el perfil de un job (o programacion) que se va a crear.

    Orden: el perfil manda (trae su cliente); luego `cliente_id`; luego el
    `client_id` de texto, que se resuelve por slug y crea el cliente si falta
    —es el camino de `lanzar_job.py`—. Un perfil de otro cliente es un 422: lo
    contrario seria colgar el rastreo de quien no es.
    """
    perfil = None
    cliente = None
    if profile_id is not None:
        perfil = db.get(CrawlProfile, profile_id)
        if perfil is None:
            raise HTTPException(status_code=404, detail="Perfil no encontrado")
        cliente = db.get(Client, perfil.cliente_id)
        if cliente_id is not None and cliente_id != perfil.cliente_id:
            raise HTTPException(
                status_code=422, detail="El perfil no es de ese cliente")
    elif cliente_id is not None:
        cliente = db.get(Client, cliente_id)
        if cliente is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
    elif client_id_texto and client_id_texto.strip():
        cliente = cliente_por_texto(db, client_id_texto, crear=True)
    return cliente, perfil


def resolver_config(
    cliente: Client | None, perfil: CrawlProfile | None, override: dict | None
) -> dict:
    """Configuracion completa de un job: defaults <- cliente <- perfil <- job.

    Se valida DESPUES de fusionar: validar cada capa por separado con
    `JobConfig` rellenaria sus valores por defecto y la capa siguiente no
    podria heredar nada.
    """
    fusion = fusionar_config(
        (cliente.config_defecto if cliente else None) or {},
        (perfil.config if perfil else None) or {},
        override or {},
    )
    try:
        return JobConfig.model_validate(fusion).model_dump()
    except ValidationError as exc:
        # Cada capa es valida por separado, pero la mezcla puede no serlo
        # (p. ej. un tope de un sitio y un minimo de otro). Se dice cual.
        raise HTTPException(
            status_code=422,
            detail=f"La configuracion heredada no es valida: {exc.errors()}",
        )


def nombres_de_clientes(db: Session, ids) -> dict[uuid.UUID, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return dict(db.query(Client.id, Client.nombre).filter(Client.id.in_(ids)).all())


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
def _integraciones(cliente: Client) -> list[IntegracionResponse]:
    """Cada integracion con `configurada`, y NUNCA con el valor.

    Se construye campo a campo a proposito, en vez de volcar el dict guardado:
    si algun dia entra en la base de datos una clave que no deberia (alguien
    la edita a mano), aqui no sale.
    """
    salida = []
    for integ in cliente.integraciones or []:
        if not isinstance(integ, dict):
            continue
        salida.append(IntegracionResponse(
            tipo=str(integ.get("tipo") or ""),
            etiqueta=integ.get("etiqueta"),
            parametros=dict(integ.get("parametros") or {}),
            credencial_env=integ.get("credencial_env"),
            activa=bool(integ.get("activa", True)),
            configurada=esta_configurada(integ),
        ))
    return salida


def _respuesta(cliente: Client, cuentas: dict | None = None) -> ClientResponse:
    cuentas = cuentas or {}
    return ClientResponse(
        id=cliente.id,
        nombre=cliente.nombre,
        slug=cliente.slug,
        dominios=list(cliente.dominios or []),
        zona_horaria=cliente.zona_horaria,
        config_defecto=dict(cliente.config_defecto or {}),
        integraciones=_integraciones(cliente),
        decisiones=cliente.decisiones,
        notas=cliente.notas,
        created_at=cliente.created_at,
        updated_at=cliente.updated_at,
        n_perfiles=cuentas.get("n_perfiles", 0),
        n_jobs=cuentas.get("n_jobs", 0),
        ultimo_job_at=cuentas.get("ultimo_job_at"),
    )


def _cuentas(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """Perfiles, rastreos y ultimo rastreo por cliente: dos consultas, no 2N."""
    cuentas: dict[uuid.UUID, dict] = {i: {} for i in ids}
    if not ids:
        return cuentas
    for cid, n, ultimo in (
        db.query(Job.cliente_id, func.count(Job.id), func.max(Job.created_at))
        .filter(Job.cliente_id.in_(ids)).group_by(Job.cliente_id).all()
    ):
        cuentas[cid].update(n_jobs=n, ultimo_job_at=ultimo)
    for cid, n in (
        db.query(CrawlProfile.cliente_id, func.count(CrawlProfile.id))
        .filter(CrawlProfile.cliente_id.in_(ids))
        .group_by(CrawlProfile.cliente_id).all()
    ):
        cuentas[cid]["n_perfiles"] = n
    return cuentas


def _buscar(db: Session, client_id: uuid.UUID) -> Client:
    cliente = db.get(Client, client_id)
    if cliente is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return cliente


def _slug_libre(db: Session, slug: str, salvo: uuid.UUID | None = None) -> None:
    q = db.query(Client.id).filter(Client.slug == slug)
    if salvo is not None:
        q = q.filter(Client.id != salvo)
    if q.first() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe un cliente con el slug '{slug}'")


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Conflicto al guardar: {exc.orig}")


# ---------------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------------
@router.get("", response_model=list[ClientResponse])
def listar_clientes(db: Session = Depends(get_session)):
    clientes = db.query(Client).order_by(func.lower(Client.nombre)).all()
    cuentas = _cuentas(db, [c.id for c in clientes])
    return [_respuesta(c, cuentas.get(c.id)) for c in clientes]


@router.post("", response_model=ClientResponse, status_code=201)
def crear_cliente(cuerpo: ClientCreate, db: Session = Depends(get_session)):
    slug = cuerpo.slug or slugificar(cuerpo.nombre)
    if not slug:
        raise HTTPException(
            status_code=422,
            detail="El nombre no da un slug: escribe uno a mano")
    _slug_libre(db, slug)
    cliente = Client(
        nombre=cuerpo.nombre.strip(),
        slug=slug,
        dominios=cuerpo.dominios,
        zona_horaria=cuerpo.zona_horaria,
        config_defecto=cuerpo.config_defecto,
        integraciones=[i.model_dump() for i in cuerpo.integraciones],
        decisiones=cuerpo.decisiones.model_dump() if cuerpo.decisiones else None,
        notas=cuerpo.notas,
    )
    db.add(cliente)
    _commit(db)
    db.refresh(cliente)
    return _respuesta(cliente)


@router.get("/{client_id}", response_model=ClientResponse)
def obtener_cliente(client_id: uuid.UUID, db: Session = Depends(get_session)):
    cliente = _buscar(db, client_id)
    return _respuesta(cliente, _cuentas(db, [cliente.id]).get(cliente.id))


@router.patch("/{client_id}", response_model=ClientResponse)
def editar_cliente(
    client_id: uuid.UUID, cuerpo: ClientUpdate, db: Session = Depends(get_session)
):
    cliente = _buscar(db, client_id)
    cambios = cuerpo.model_dump(exclude_unset=True)
    if "slug" in cambios and cambios["slug"] and cambios["slug"] != cliente.slug:
        _slug_libre(db, cambios["slug"], salvo=cliente.id)
    if "nombre" in cambios and cambios["nombre"] is None:
        cambios.pop("nombre")
    if "slug" in cambios and cambios["slug"] is None:
        cambios.pop("slug")
    for campo in ("dominios", "config_defecto", "integraciones"):
        if campo in cambios and cambios[campo] is None:
            cambios[campo] = {} if campo == "config_defecto" else []
    for campo, valor in cambios.items():
        setattr(cliente, campo, valor)
    cliente.updated_at = datetime.now(timezone.utc)
    _commit(db)
    db.refresh(cliente)
    return _respuesta(cliente, _cuentas(db, [cliente.id]).get(cliente.id))


@router.delete("/{client_id}", status_code=204)
def borrar_cliente(
    client_id: uuid.UUID,
    confirmar: str = Query(..., description="El slug del cliente, para confirmar"),
    con_jobs: bool = Query(False, description="Borrar tambien sus rastreos"),
    db: Session = Depends(get_session),
):
    """Borra el cliente y sus perfiles. Sus rastreos **no**, salvo `con_jobs`.

    Exige `confirmar=<slug>`: un borrado que se lleva perfiles e integraciones
    no puede salir de un clic suelto ni de un `DELETE` con el id equivocado.

    Los rastreos se desenganchan a mano (`cliente_id`/`profile_id` a NULL) y no
    se deja al `ON DELETE SET NULL` de Postgres: asi el comportamiento es el
    mismo en cualquier motor, y en SQLite —sin claves foraneas activas— no
    quedarian apuntando a un cliente que ya no existe.
    """
    cliente = _buscar(db, client_id)
    if confirmar != cliente.slug:
        raise HTTPException(
            status_code=409,
            detail=f"Para borrar el cliente escribe su slug ('{cliente.slug}')")

    if con_jobs:
        from api.routers.jobs import borrar_job

        for (jid,) in db.query(Job.id).filter(Job.cliente_id == cliente.id).all():
            job = db.get(Job, jid)
            if job is not None:
                borrar_job(db, job, confirmar=False)
    else:
        db.query(Job).filter(Job.cliente_id == cliente.id).update(
            {Job.cliente_id: None, Job.profile_id: None}, synchronize_session=False)
    db.query(Programacion).filter(Programacion.cliente_id == cliente.id).update(
        {Programacion.cliente_id: None, Programacion.profile_id: None},
        synchronize_session=False)
    db.query(CrawlProfile).filter(CrawlProfile.cliente_id == cliente.id).delete(
        synchronize_session=False)
    db.delete(cliente)
    db.commit()


@router.get("/{client_id}/config-resuelta", response_model=ConfigResueltaResponse)
def config_resuelta(
    client_id: uuid.UUID,
    profile_id: uuid.UUID | None = Query(None),
    db: Session = Depends(get_session),
):
    """Lo que tendria un job lanzado ahora: para precargar el modal."""
    cliente, perfil = resolver_cliente_y_perfil(
        db, cliente_id=client_id, profile_id=profile_id)
    return ConfigResueltaResponse(
        cliente_id=cliente.id,
        profile_id=perfil.id if perfil else None,
        seeds=list(perfil.seeds or []) if perfil else [],
        config=resolver_config(cliente, perfil, None),
        config_heredada=fusionar_config(
            cliente.config_defecto or {}, (perfil.config if perfil else None) or {}),
    )


# ---------------------------------------------------------------------------
# Perfiles
# ---------------------------------------------------------------------------
def _perfil(db: Session, client_id: uuid.UUID, profile_id: uuid.UUID) -> CrawlProfile:
    perfil = db.get(CrawlProfile, profile_id)
    if perfil is None or perfil.cliente_id != client_id:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")
    return perfil


def _nombre_libre(db: Session, client_id: uuid.UUID, nombre: str,
                  salvo: uuid.UUID | None = None) -> None:
    q = db.query(CrawlProfile.id).filter(
        CrawlProfile.cliente_id == client_id, CrawlProfile.nombre == nombre)
    if salvo is not None:
        q = q.filter(CrawlProfile.id != salvo)
    if q.first() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Este cliente ya tiene un perfil llamado '{nombre}'")


@router.get("/{client_id}/profiles", response_model=list[ProfileResponse])
def listar_perfiles(client_id: uuid.UUID, db: Session = Depends(get_session)):
    _buscar(db, client_id)
    return (
        db.query(CrawlProfile).filter(CrawlProfile.cliente_id == client_id)
        .order_by(CrawlProfile.rol, func.lower(CrawlProfile.nombre)).all()
    )


@router.post("/{client_id}/profiles", response_model=ProfileResponse, status_code=201)
def crear_perfil(
    client_id: uuid.UUID, cuerpo: ProfileCreate, db: Session = Depends(get_session)
):
    _buscar(db, client_id)
    nombre = cuerpo.nombre.strip()
    _nombre_libre(db, client_id, nombre)
    perfil = CrawlProfile(
        cliente_id=client_id, nombre=nombre, rol=cuerpo.rol, seeds=cuerpo.seeds,
        config=cuerpo.config, notas=cuerpo.notas, activo=cuerpo.activo,
    )
    db.add(perfil)
    _commit(db)
    db.refresh(perfil)
    return perfil


@router.get("/{client_id}/profiles/{profile_id}", response_model=ProfileResponse)
def obtener_perfil(
    client_id: uuid.UUID, profile_id: uuid.UUID, db: Session = Depends(get_session)
):
    return _perfil(db, client_id, profile_id)


@router.patch("/{client_id}/profiles/{profile_id}", response_model=ProfileResponse)
def editar_perfil(
    client_id: uuid.UUID,
    profile_id: uuid.UUID,
    cuerpo: ProfileUpdate,
    db: Session = Depends(get_session),
):
    perfil = _perfil(db, client_id, profile_id)
    cambios = {k: v for k, v in cuerpo.model_dump(exclude_unset=True).items()
               if v is not None or k in ("notas",)}
    if "nombre" in cambios:
        cambios["nombre"] = cambios["nombre"].strip()
        _nombre_libre(db, client_id, cambios["nombre"], salvo=perfil.id)
    for campo, valor in cambios.items():
        setattr(perfil, campo, valor)
    perfil.updated_at = datetime.now(timezone.utc)
    _commit(db)
    db.refresh(perfil)
    return perfil


@router.delete("/{client_id}/profiles/{profile_id}", status_code=204)
def borrar_perfil(
    client_id: uuid.UUID, profile_id: uuid.UUID, db: Session = Depends(get_session)
):
    """Borra el perfil. Sus rastreos se quedan, colgados solo del cliente."""
    perfil = _perfil(db, client_id, profile_id)
    db.query(Job).filter(Job.profile_id == perfil.id).update(
        {Job.profile_id: None}, synchronize_session=False)
    db.query(Programacion).filter(Programacion.profile_id == perfil.id).update(
        {Programacion.profile_id: None}, synchronize_session=False)
    db.delete(perfil)
    db.commit()
