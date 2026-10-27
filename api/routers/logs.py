"""Logs del servidor de un cliente (#35, decision 213).

Se suben aqui y se leen en el worker: un log de un mes son gigas y leerlo en
la peticion la cortaria. El fichero va al volumen de archivo, que comparten
la API y el worker, y el worker lo borra al acabar (tiene IPs de personas).
La lectura vive en `shared/logs_servidor.py`.
"""

from __future__ import annotations

import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from shared import logs_servidor as L
from shared.database import get_session
from shared.models import Client, LogImport, LogUrl

router = APIRouter(prefix="/api/clients", tags=["logs"])

# Un log de un mes de un sitio grande comprimido cabe de sobra.
MAX_BYTES = 5 * 1024 * 1024 * 1024
# Las importaciones que se guardan por cliente: el analisis usa la ultima hecha.
GUARDADAS = 3


def _resumen(i: LogImport, en_uso: bool) -> dict:
    return {"id": i.id, "fichero": i.fichero, "bytes": i.bytes, "formato": i.formato, "host": i.host,
            "estado": i.estado, "motivo": i.motivo, "progreso": i.progreso,
            "lineas_ok": i.lineas_ok, "lineas_error": i.lineas_error, "errores": (i.errores or [])[:10],
            "personas": i.personas, "bots": i.bots or {},
            "desde": i.desde.isoformat() if i.desde else None, "hasta": i.hasta.isoformat() if i.hasta else None,
            "subido": i.created_at.isoformat() if i.created_at else None, "en_uso": en_uso}


def _cliente(db: Session, cliente_id: uuid.UUID) -> Client:
    c = db.get(Client, cliente_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return c


@router.get("/{cliente_id}/logs")
def listar(cliente_id: uuid.UUID, db: Session = Depends(get_session)):
    _cliente(db, cliente_id)
    imps = (db.query(LogImport).filter(LogImport.cliente_id == cliente_id)
            .order_by(LogImport.created_at.desc(), LogImport.id.desc()).all())
    en_uso = next((i.id for i in imps if i.estado == "hecho"), None)
    return [_resumen(i, i.id == en_uso) for i in imps]


@router.post("/{cliente_id}/logs", status_code=201)
def subir(cliente_id: uuid.UUID, fichero: UploadFile = File(...),
          formato: str = Form("combined"), regex: str | None = Form(None),
          formato_fecha: str | None = Form(None), host: str | None = Form(None),
          db: Session = Depends(get_session)):
    """Guarda el fichero (en claro o .gz) y lo encola para el worker.

    Sincrona: FastAPI la corre en un hilo y copiar gigas no para el bucle de
    eventos. El formato se valida aqui (una regex propia sin sus grupos da 422
    ya, no dentro de una hora en el worker)."""
    from shared.cola import encolar_log

    from api.dependencies import get_redis

    cliente = _cliente(db, cliente_id)
    try:
        L.regex_de(formato, regex)
    except L.FormatoNoValido as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    host = (host or (cliente.dominios or [None])[0] or "").strip().lower()
    host = host.split("://", 1)[-1].split("/", 1)[0]
    if not host:
        raise HTTPException(status_code=422, detail="Falta el dominio del sitio: el log guarda rutas, no URLs")
    destino_dir = L.directorio()
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"{uuid.uuid4().hex}.log"
    try:
        with open(destino, "wb") as out:
            shutil.copyfileobj(fichero.file, out, length=4 * 1024 * 1024)
        tamano = destino.stat().st_size
    except OSError as exc:
        destino.unlink(missing_ok=True)
        raise HTTPException(status_code=507, detail=f"No se pudo guardar el fichero: {exc}") from exc
    if tamano > MAX_BYTES:
        destino.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="El fichero pasa de 5 GB: súbelo comprimido (.gz) o partido")
    if not tamano:
        destino.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="El fichero está vacío")
    imp = LogImport(cliente_id=cliente_id, fichero=(fichero.filename or "")[:255], ruta_disco=str(destino),
                    bytes=tamano, formato=formato, regex=regex if formato == "propio" else None,
                    formato_fecha=formato_fecha or None, host=host[:255], estado="pendiente")
    db.add(imp)
    db.flush()
    _podar(db, cliente_id)
    db.commit()
    try:
        encolar_log(get_redis(), imp.id)
    except Exception:  # noqa: BLE001
        imp.estado, imp.motivo = "error", "la cola no responde: vuelve a subirlo"
        db.commit()
        destino.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="La cola no responde: no se ha encolado")
    return _resumen(imp, False)


def _podar(db: Session, cliente_id) -> None:
    """Deja las `GUARDADAS` mas recientes; de las demas se van las filas y el fichero."""
    import os

    viejas = (db.query(LogImport).filter(LogImport.cliente_id == cliente_id)
              .order_by(LogImport.created_at.desc(), LogImport.id.desc()).offset(GUARDADAS).all())
    for v in viejas:
        if v.estado == "procesando":
            continue
        if v.ruta_disco:
            try:
                os.remove(v.ruta_disco)
            except OSError:
                pass
        db.query(LogUrl).filter(LogUrl.import_id == v.id).delete(synchronize_session=False)
        db.delete(v)


@router.delete("/{cliente_id}/logs/{import_id}", status_code=204)
def borrar(cliente_id: uuid.UUID, import_id: int, db: Session = Depends(get_session)):
    import os

    imp = db.get(LogImport, import_id)
    if imp is None or imp.cliente_id != cliente_id:
        raise HTTPException(status_code=404, detail="Importación no encontrada")
    if imp.estado == "procesando":
        raise HTTPException(status_code=409, detail="Se está leyendo ahora: espera a que acabe")
    if imp.ruta_disco:
        try:
            os.remove(imp.ruta_disco)
        except OSError:
            pass
    db.query(LogUrl).filter(LogUrl.import_id == import_id).delete(synchronize_session=False)
    db.delete(imp)
    db.commit()
