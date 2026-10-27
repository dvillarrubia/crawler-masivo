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

    tipo: Literal["gsc", "slack"] = "gsc"
    # El nombre con el que la usan las integraciones: GSC_LIN3S, SLACK_LIN3S...
    nombre: str = Field(..., min_length=5, max_length=128)
    # El JSON de la llave, tal cual se descarga de Google Cloud; o el token
    # del bot de Slack (xoxb-...).
    contenido: str = Field(..., min_length=2, max_length=64 * 1024)


def _publico(nombre: str) -> dict:
    """Lo que se puede ensenar de una llave guardada (por fichero o entorno)."""
    import os

    from shared import secretos
    from shared.gsc import LlaveNoValida, leer_llave, publico

    texto = os.environ.get(nombre) or secretos.leer(nombre)
    salida = {"nombre": nombre, "origen": "entorno" if os.environ.get(nombre) else "subida"}
    if nombre.startswith("SLACK_"):
        return salida | _publico_slack(texto or "")
    try:
        salida.update(publico(leer_llave(texto or "")))
        salida["valida"] = True
    except LlaveNoValida as exc:
        salida.update(valida=False, motivo=str(exc))
    return salida


def _publico_slack(token: str) -> dict:
    """De un token de Slack se ensena el espacio de trabajo y el nombre del
    bot (`auth.test`), que es lo que hace falta para invitarlo a un canal."""
    from shared.slack import SlackError, identidad, traducir

    try:
        return identidad(token.strip()) | {"valida": True}
    except SlackError as exc:
        return {"valida": False, "motivo": traducir(exc.codigo) if exc.codigo != "sin_conexion" else str(exc)}


def _en_uso(db: Session, nombre: str) -> list[str]:
    return sorted(c.slug for c in db.query(Client).all()
                  if any(isinstance(i, dict) and i.get("credencial_env") == nombre
                         for i in c.integraciones or []))


@router.get("/api/credenciales")
def listar_llaves(tipo: Literal["gsc", "slack"] = Query("gsc"), db: Session = Depends(get_session)):
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
    if cuerpo.tipo == "slack":
        return _subir_token_slack(nombre, cuerpo.contenido, db)
    try:
        info = leer_llave(cuerpo.contenido)
    except LlaveNoValida as exc:
        raise HTTPException(422, str(exc))
    # Se guarda normalizado (sin espacios de mas) y nunca se devuelve.
    secretos.guardar(nombre, json.dumps(info))
    return dict(publico(info), nombre=nombre, origen="subida", valida=True,
                en_uso_por=_en_uso(db, nombre))


def _subir_token_slack(nombre: str, contenido: str, db: Session) -> dict:
    """Solo se guarda un token que Slack acepta: uno pegado mal fallaria el
    lunes, con el primer resumen, y no ahora que hay alguien mirando."""
    import re

    from shared import secretos
    from shared.slack import SlackError, identidad, traducir

    token = contenido.strip()
    if not re.fullmatch(r"xox[bp]-[A-Za-z0-9-]{10,250}", token):
        raise HTTPException(422, "Eso no parece un token de bot de Slack: empieza por xoxb- "
                                 "(Slack → tu app → OAuth & Permissions → Bot User OAuth Token).")
    try:
        quien = identidad(token)
    except SlackError as exc:
        if exc.codigo == "sin_conexion":
            raise HTTPException(502, str(exc))
        raise HTTPException(422, traducir(exc.codigo))
    secretos.guardar(nombre, token)
    return dict(quien, nombre=nombre, origen="subida", valida=True, en_uso_por=_en_uso(db, nombre))


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
                        dias_truncados=len(e.dias_truncados or []),
                        inspeccion_ultima=e.inspeccion_ultima, inspeccion_hechas=e.inspeccion_hechas,
                        inspeccion_motivo=e.inspeccion_motivo)
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


@router.get("/api/clients/{client_id}/gsc/serie")
def serie_gsc(client_id: uuid.UUID, dias: int = Query(90, ge=7, le=480), top: int = Query(10, ge=0, le=50),
              db: Session = Depends(get_session)):
    """Solo lectura: la serie diaria de clics e impresiones de cada propiedad
    del cliente y, si `top` > 0, las paginas y las busquedas con mas clics de
    los ultimos 28 dias frente a los 28 anteriores. Para paneles de fuera (el
    plugin Cerebro OS) y el MCP, sin llave de Google en la otra punta.

    Es la suma por pagina de `gsc_paginas`: los clics cuadran con el total de
    la propiedad, las impresiones salen por encima (Google cuenta una por
    pagina mostrada) y la posicion es la media de las paginas ponderada por
    impresiones, no la de la propiedad. Los dias truncados se marcan: ahi la
    suma se queda corta. Las busquedas no suman el total (las anonimizadas no
    las da Google)."""
    from datetime import timedelta

    from shared.models import GscConsulta, GscEstado, GscPagina

    c = _cliente(db, client_id)
    salida = []
    for e in db.query(GscEstado).filter(GscEstado.cliente_id == c.id).order_by(GscEstado.propiedad):
        fila = {"propiedad": e.propiedad, "estado": e.estado, "ultimo_ok": e.ultimo_ok,
                "primer_dia": e.primer_dia, "ultimo_dia": e.ultimo_dia, "serie": []}
        if not e.ultimo_dia:
            salida.append(fila)
            continue
        truncados = {str(d) for d in (e.dias_truncados or [])}
        hasta = e.ultimo_dia
        desde = hasta - timedelta(days=dias - 1)
        base = (GscPagina.cliente_id == c.id, GscPagina.propiedad == e.propiedad)
        filas = (db.query(GscPagina.fecha, func.sum(GscPagina.clics), func.sum(GscPagina.impresiones),
                          func.sum(GscPagina.posicion * GscPagina.impresiones))
                 .filter(*base, GscPagina.fecha >= desde, GscPagina.fecha <= hasta)
                 .group_by(GscPagina.fecha).order_by(GscPagina.fecha).all())
        for fecha, clics, imp, pos_x_imp in filas:
            clics, imp = int(clics or 0), int(imp or 0)
            fila["serie"].append({"fecha": fecha, "clics": clics, "impresiones": imp,
                                  "ctr": round(clics / imp, 4) if imp else None,
                                  "posicion": round(pos_x_imp / imp, 2) if imp and pos_x_imp is not None else None,
                                  "truncado": str(fecha) in truncados})
        if top:
            ahora_desde, antes_desde = hasta - timedelta(days=27), hasta - timedelta(days=55)
            antes_hasta = ahora_desde - timedelta(days=1)

            def _top(clave, etiqueta, filtro, fecha, clics, impresiones, nombre):
                actual = (db.query(clave, func.max(etiqueta), func.sum(clics), func.sum(impresiones))
                          .filter(*filtro, fecha >= ahora_desde, fecha <= hasta)
                          .group_by(clave).order_by(func.sum(clics).desc()).limit(top).all())
                claves = [k for k, _, _, _ in actual]
                previo = dict(db.query(clave, func.sum(clics))
                              .filter(*filtro, fecha >= antes_desde, fecha <= antes_hasta, clave.in_(claves))
                              .group_by(clave).all()) if claves else {}
                return [{nombre: et, "clics": int(cl or 0), "impresiones": int(im or 0),
                         "clics_previos": int(previo.get(k) or 0)} for k, et, cl, im in actual]

            # Las paginas por `url_clave` (la misma URL con los parametros en otro orden es una).
            paginas = _top(GscPagina.url_clave, GscPagina.url, base, GscPagina.fecha, GscPagina.clics,
                           GscPagina.impresiones, "url")
            base_c = (GscConsulta.cliente_id == c.id, GscConsulta.propiedad == e.propiedad)
            consultas = _top(GscConsulta.consulta, GscConsulta.consulta, base_c, GscConsulta.fecha,
                             GscConsulta.clics, GscConsulta.impresiones, "consulta")
            fila["periodo"] = {"desde": ahora_desde, "hasta": hasta, "previo_desde": antes_desde,
                               "previo_hasta": antes_hasta}
            fila["paginas"] = paginas
            fila["consultas"] = consultas
        salida.append(fila)
    return salida


# ---------------------------------------------------------------------------
# Slack de un cliente (decision 149)
# ---------------------------------------------------------------------------
@router.get("/api/credenciales/{nombre}/canales")
def canales_de_token(nombre: str):
    """Los canales que ve el bot. Sin `channels:read` es un 409 que lo dice: el
    canal se puede escribir a mano igual."""
    from shared.credenciales import CredencialNoDisponible, credencial
    from shared.slack import SlackError, canales, traducir

    try:
        token = credencial({"tipo": "slack", "credencial_env": nombre}).strip()
        return canales(token)
    except CredencialNoDisponible as exc:
        raise HTTPException(404, str(exc))
    except SlackError as exc:
        if exc.codigo == "missing_scope":
            raise HTTPException(409, "El bot no puede listar canales (le falta channels:read). "
                                     "Escribe el canal a mano: su id (C0123…) o #nombre.")
        raise HTTPException(502, traducir(exc.codigo, needed=getattr(exc, "needed", None)))


class SlackCliente(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credencial: str = Field(..., min_length=7, max_length=128)
    # El id (C0123ABC) o el nombre (#seo-cliente). Con el id no se rompe si
    # alguien renombra el canal.
    canal: str = Field(..., min_length=2, max_length=128)
    canal_nombre: str | None = Field(default=None, max_length=128)
    alertas: bool = True
    resumen: bool = True
    informe: bool = True
    activa: bool = True


def _estado_slack(c: Client) -> dict:
    from shared.credenciales import esta_configurada
    from shared.slack import ENVIOS, _activo, integraciones, ultimo_envio

    filas = []
    for integ in integraciones(c):
        p = integ.get("parametros") or {}
        fila = {"credencial": integ.get("credencial_env"), "canal": p.get("canal"),
                "canal_nombre": p.get("canal_nombre"), "activa": integ.get("activa", True),
                "configurada": esta_configurada(integ)}
        for e in ENVIOS:
            fila[e] = _activo(p.get(e))
        filas.append(fila)
    return {"canales": filas, "ultimo_envio": ultimo_envio(c.id)}


@router.get("/api/clients/{client_id}/slack")
def estado_slack(client_id: uuid.UUID, db: Session = Depends(get_session)):
    return _estado_slack(_cliente(db, client_id))


@router.put("/api/clients/{client_id}/slack")
def guardar_slack(client_id: uuid.UUID, cuerpo: SlackCliente, db: Session = Depends(get_session)):
    """Pone (o sustituye) EL canal de Slack del cliente. Uno por cliente: si
    hacen falta dos, el formulario generico de integraciones admite varias."""
    from shared.credenciales import error_en_variable

    c = _cliente(db, client_id)
    nombre = cuerpo.credencial.strip().upper()
    error = error_en_variable("slack", nombre)
    if error:
        raise HTTPException(422, f"Token no válido: {error}")
    canal = cuerpo.canal.strip()
    integ = {"tipo": "slack", "etiqueta": None, "credencial_env": nombre, "activa": cuerpo.activa,
             "parametros": {"canal": canal, "alertas": cuerpo.alertas, "resumen": cuerpo.resumen,
                            "informe": cuerpo.informe}}
    # Sin None: el formulario generico de integraciones lo pasa a texto y
    # volveria como «null».
    if (cuerpo.canal_nombre or "").strip():
        integ["parametros"]["canal_nombre"] = cuerpo.canal_nombre.strip()
    otras = [i for i in (c.integraciones or []) if not (isinstance(i, dict) and i.get("tipo") == "slack")]
    c.integraciones = otras + [integ]
    db.commit()
    return _estado_slack(c)


@router.delete("/api/clients/{client_id}/slack", status_code=204)
def quitar_slack(client_id: uuid.UUID, db: Session = Depends(get_session)):
    c = _cliente(db, client_id)
    c.integraciones = [i for i in (c.integraciones or [])
                       if not (isinstance(i, dict) and i.get("tipo") == "slack")]
    db.commit()


@router.post("/api/clients/{client_id}/slack/probar")
def probar_slack(client_id: uuid.UUID, db: Session = Depends(get_session)):
    """Manda un mensaje de prueba a cada canal del cliente y dice que paso en
    cada uno: es como se descubre que falta invitar al bot."""
    from shared.credenciales import CredencialNoDisponible, credencial
    from shared.slack import SlackError, identidad, integraciones, publicar, traducir

    c = _cliente(db, client_id)
    filas = [i for i in integraciones(c) if str((i.get("parametros") or {}).get("canal") or "").strip()]
    if not filas:
        raise HTTPException(409, "Este cliente no tiene canal de Slack.")
    salida = []
    for integ in filas:
        canal = str(integ["parametros"]["canal"]).strip()
        bot = None
        try:
            token = credencial(integ).strip()
            bot = identidad(token).get("bot")
            publicar(token, canal, f"Prueba del crawler: aquí llegarán las alertas, el resumen "
                                   f"semanal y los informes de *{c.nombre}*.")
            salida.append({"canal": canal, "ok": True})
        except CredencialNoDisponible as exc:
            salida.append({"canal": canal, "ok": False, "motivo": str(exc)})
        except SlackError as exc:
            salida.append({"canal": canal, "ok": False,
                           "motivo": traducir(exc.codigo, bot, getattr(exc, "needed", None))
                           if exc.codigo != "sin_conexion" else str(exc)})
    return salida
