"""Llaves subidas desde la interfaz y Search Console por cliente (decision 141).

Las llaves van a `SECRETOS_DIR` (`shared/secretos.py`), nunca a la base de
datos. Esta API las crea, las sustituye y las borra, pero NUNCA devuelve su
contenido: de una llave de Google solo se ensena el email de la cuenta de
servicio y el proyecto, que es lo que hace falta para darle acceso en Search
Console. Una llave puede servir a varios clientes: cada integracion `gsc` dice
cual usa (`credencial_env`).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.models import Client

router = APIRouter(tags=["credenciales"])


class LlaveSubida(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: Literal["gsc"] = "gsc"
    # El nombre con el que la usan las integraciones: GSC_LIN3S, GSC_LOPESAN...
    nombre: str = Field(..., min_length=5, max_length=128)
    # El JSON de la llave, tal cual se descarga de Google Cloud.
    contenido: str = Field(..., min_length=2, max_length=64 * 1024)


def _publico(nombre: str) -> dict:
    """Lo que se puede ensenar de una llave guardada (por fichero o entorno)."""
    import os

    from shared import secretos
    from shared.gsc import LlaveNoValida, leer_llave, publico

    texto = os.environ.get(nombre) or secretos.leer(nombre)
    salida = {"nombre": nombre, "origen": "entorno" if os.environ.get(nombre) else "subida"}
    try:
        salida.update(publico(leer_llave(texto or "")))
        salida["valida"] = True
    except LlaveNoValida as exc:
        salida.update(valida=False, motivo=str(exc))
    return salida


def _en_uso(db: Session, nombre: str) -> list[str]:
    return sorted(c.slug for c in db.query(Client).all()
                  if any(isinstance(i, dict) and i.get("credencial_env") == nombre
                         for i in c.integraciones or []))


@router.get("/api/credenciales")
def listar_llaves(tipo: Literal["gsc"] = Query("gsc"), db: Session = Depends(get_session)):
    import os

    from shared import secretos
    from shared.credenciales import PREFIJOS

    prefijo = PREFIJOS[tipo]
    nombres = set(secretos.listar(prefijo)) | {k for k, v in os.environ.items()
                                               if k.startswith(prefijo) and k != prefijo and v}
    return [dict(_publico(n), en_uso_por=_en_uso(db, n)) for n in sorted(nombres)]


@router.post("/api/credenciales", status_code=201)
def subir_llave(cuerpo: LlaveSubida, db: Session = Depends(get_session)):
    from shared import secretos
    from shared.credenciales import error_en_variable
    from shared.gsc import LlaveNoValida, leer_llave, publico

    nombre = cuerpo.nombre.strip().upper()
    error = error_en_variable(cuerpo.tipo, nombre)
    if error:
        raise HTTPException(422, f"Nombre no válido: {error}")
    try:
        info = leer_llave(cuerpo.contenido)
    except LlaveNoValida as exc:
        raise HTTPException(422, str(exc))
    # Se guarda normalizado (sin espacios de mas) y nunca se devuelve.
    secretos.guardar(nombre, json.dumps(info))
    return dict(publico(info), nombre=nombre, origen="subida", valida=True,
                en_uso_por=_en_uso(db, nombre))


@router.delete("/api/credenciales/{nombre}", status_code=204)
def borrar_llave(nombre: str, forzar: bool = False, db: Session = Depends(get_session)):
    from shared import secretos

    uso = _en_uso(db, nombre)
    if uso and not forzar:
        raise HTTPException(409, "La usan estos clientes: " + ", ".join(uso)
                            + ". Quítala de su integración antes, o borra con forzar=true.")
    if not secretos.borrar(nombre):
        raise HTTPException(404, "No hay una llave subida con ese nombre (las del .env no se borran desde aquí)")


@router.get("/api/credenciales/{nombre}/propiedades")
def propiedades_de_llave(nombre: str):
    """Las propiedades de Search Console que ve esa cuenta: para elegir sin
    escribirla a mano, y para comprobar que el cliente ya le dio acceso."""
    from shared.credenciales import CredencialNoDisponible, credencial
    from shared.gsc import Consultor, GscError, LlaveNoValida, leer_llave, traducir

    integ = {"tipo": "gsc", "credencial_env": nombre}
    try:
        info = leer_llave(credencial(integ))
        sitios = Consultor(info).propiedades()
    except (CredencialNoDisponible, LlaveNoValida) as exc:
        raise HTTPException(404, str(exc))
    except GscError as exc:
        raise HTTPException(502, traducir(exc.status, str(exc), None, "-"))
    return sorted(({"propiedad": s.get("siteUrl"), "permiso": s.get("permissionLevel")}
                   for s in sitios if s.get("siteUrl")), key=lambda x: x["propiedad"])


# ---------------------------------------------------------------------------
# Search Console de un cliente
# ---------------------------------------------------------------------------
def _cliente(db: Session, client_id: uuid.UUID) -> Client:
    c = db.get(Client, client_id)
    if c is None:
        raise HTTPException(404, "Cliente no encontrado")
    return c


@router.get("/api/clients/{client_id}/gsc")
def estado_gsc(client_id: uuid.UUID, db: Session = Depends(get_session)):
    """Por cada integracion `gsc` del cliente: si esta configurada, con que
    cuenta, como fue la ultima sincronizacion y cuanto hay guardado."""
    from shared.credenciales import esta_configurada
    from shared.models import GscEstado, GscPagina

    c = _cliente(db, client_id)
    salida = []
    for integ in c.integraciones or []:
        if not (isinstance(integ, dict) and integ.get("tipo") == "gsc"):
            continue
        propiedad = str((integ.get("parametros") or {}).get("propiedad") or "").strip()
        fila = {"propiedad": propiedad or None, "credencial": integ.get("credencial_env"),
                "activa": integ.get("activa", True), "configurada": esta_configurada(integ),
                "cuenta": None, "estado": "sin_configurar", "motivo": None}
        if integ.get("credencial_env") and fila["configurada"]:
            fila["cuenta"] = _publico(integ["credencial_env"]).get("client_email")
        if not propiedad:
            fila["motivo"] = "Falta la propiedad (sc-domain:dominio.com o https://www.dominio.com/)."
        elif not fila["configurada"]:
            fila["motivo"] = "Falta la llave: súbela o elige una de las ya subidas."
        e = (db.query(GscEstado).filter(GscEstado.cliente_id == c.id, GscEstado.propiedad == propiedad)
             .one_or_none()) if propiedad else None
        if e is not None:
            fila.update(estado=e.estado, motivo=e.motivo or fila["motivo"], cuenta=e.cuenta or fila["cuenta"],
                        ultimo_intento=e.ultimo_intento, ultimo_ok=e.ultimo_ok, pedida_en=e.pedida_en,
                        primer_dia=e.primer_dia, ultimo_dia=e.ultimo_dia, filas=e.filas,
                        dias_truncados=len(e.dias_truncados or []))
            if e.ultimo_dia:
                desde = e.ultimo_dia.fromordinal(e.ultimo_dia.toordinal() - 27)
                clics, imp, urls = (db.query(func.sum(GscPagina.clics), func.sum(GscPagina.impresiones),
                                             func.count(func.distinct(GscPagina.url_clave)))
                                    .filter(GscPagina.cliente_id == c.id, GscPagina.propiedad == propiedad,
                                            GscPagina.fecha >= desde, GscPagina.fecha <= e.ultimo_dia).one())
                fila["ultimos_28_dias"] = {"clics": int(clics or 0), "impresiones": int(imp or 0),
                                           "urls": int(urls or 0)}
        elif propiedad and fila["configurada"] and fila["activa"]:
            fila["estado"] = "pendiente"
        salida.append(fila)
    return salida


@router.post("/api/clients/{client_id}/gsc/sincronizar", status_code=202)
def pedir_sincronizacion(client_id: uuid.UUID, db: Session = Depends(get_session)):
    """La pide; la hace el worker en su proxima pasada (un minuto)."""
    from shared.gsc import _estado

    c = _cliente(db, client_id)
    ahora = datetime.now(timezone.utc)
    pedidas = 0
    for integ in c.integraciones or []:
        propiedad = str(((integ or {}).get("parametros") or {}).get("propiedad") or "").strip()
        if isinstance(integ, dict) and integ.get("tipo") == "gsc" and propiedad:
            _estado(db, c.id, propiedad).pedida_en = ahora
            pedidas += 1
    if not pedidas:
        raise HTTPException(409, "Este cliente no tiene Search Console configurado (falta la propiedad).")
    db.commit()
    return {"pedidas": pedidas}
