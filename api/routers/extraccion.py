"""Extraccion y busqueda a medida (#34, decision 104): probar reglas y leer
lo que dieron en un censo.

- `POST /api/extraccion/probar` aplica las reglas a un HTML pegado, al
  `raw_html` de una URL de un censo o a una URL en vivo. **La API no descarga
  nada**: la URL en vivo la pide el worker (cola `extraccion:pruebas`, con las
  comprobaciones de SSRF de `shared/prueba_en_vivo.py`, decision 166) y la API
  espera su respuesta. Usa la MISMA funcion que el spider
  (`shared.extraccion_a_medida`).
- `GET /api/jobs/{id}/extraccion` resume cada regla del censo: evaluadas,
  estados, renderizadas y los valores mas frecuentes.
- `GET /api/jobs/{id}/extraccion/{nombre}/valores`: la distribucion paginada.

Las definiciones viven en `jobs.config` (`custom_extraction`,
`custom_search`); las filas, en `custom_extraction`.
"""

from __future__ import annotations

import math
import os
import threading
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from shared.database import get_session
from shared.models import CustomExtraction, Job, PageContent, Url

from api.schemas import BusquedaAMedida, ExtractorAMedida, JobConfig

router = APIRouter(prefix="/api", tags=["extraccion"])

MAX_HTML_BYTES = 5 * 1024 * 1024
# Pruebas a la vez. Una XPath o un CSS no se pueden interrumpir (lxml no tiene
# timeout) y el endpoint no pide autenticacion: sin tope, unas decenas de
# peticiones con `div ~ div ~ div` sobre 5 MB ocupaban todo el threadpool y
# la API dejaba de responder a /api/jobs. Con el, lo que se puede bloquear es
# este endpoint (429), no la API. Dentro de una prueba, el presupuesto de la
# pagina corta las reglas que vienen detras de una lenta, asi que una peticion
# cuesta como mucho UNA regla lenta. lxml suelta el GIL al evaluar.
PROBAR_CONCURRENCIA = max(1, int(os.getenv("CUSTOM_EXTRACTION_PRUEBAS_A_LA_VEZ", "2")))
_pruebas = threading.BoundedSemaphore(PROBAR_CONCURRENCIA)


class ProbarExtraccion(BaseModel):
    custom_extraction: dict[str, ExtractorAMedida | None] = Field(default_factory=dict)
    custom_search: dict[str, BusquedaAMedida | None] = Field(default_factory=dict)
    # Heredar las reglas del cliente y del perfil (`resolver_config`): lo que
    # venga aqui las sustituye por nombre y `null` las quita, como en un job.
    cliente_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    # UNA fuente: HTML pegado o la URL de un censo con `raw_html` guardado.
    html: str | None = None
    job_id: uuid.UUID | None = None
    url_id: int | None = None
    # Ruta contra la que se evalua `solo_urls` con HTML pegado (p. ej. /p/123).
    ruta: str | None = None
    # O una URL en vivo (decision 166): la descarga el WORKER, no la API, con
    # las comprobaciones de SSRF de `shared/prueba_en_vivo.py`.
    url: str | None = Field(None, max_length=2048)
    # Identidad de la descarga en vivo, como en el job (el formulario manda la
    # suya; sin ellas, la del cliente/perfil o la de serie).
    user_agent: str | None = Field(None, max_length=1000)
    impersonate: str | None = Field(None, max_length=40)


def _config_de_prueba(db: Session, cuerpo: ProbarExtraccion) -> dict:
    propias = {
        "custom_extraction": {k: (v.model_dump() if v is not None else None)
                              for k, v in cuerpo.custom_extraction.items()},
        "custom_search": {k: (v.model_dump() if v is not None else None)
                          for k, v in cuerpo.custom_search.items()},
    }
    if cuerpo.user_agent:
        propias["user_agent"] = cuerpo.user_agent
    if cuerpo.impersonate:
        propias["impersonate"] = cuerpo.impersonate
    if cuerpo.cliente_id or cuerpo.profile_id:
        from api.routers.clientes import resolver_cliente_y_perfil, resolver_config

        cliente, perfil = resolver_cliente_y_perfil(
            db, cliente_id=cuerpo.cliente_id, profile_id=cuerpo.profile_id)
        return resolver_config(cliente, perfil, propias)
    from shared.clientes import fusionar_config

    try:
        return JobConfig.model_validate(fusionar_config(propias)).model_dump()
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=[
            {"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()])


@router.post("/extraccion/probar")
def probar(cuerpo: ProbarExtraccion, db: Session = Depends(get_session)) -> dict[str, Any]:
    from shared import extraccion_a_medida as xm

    fuentes = sum(x is not None for x in (cuerpo.html, cuerpo.job_id, cuerpo.url))
    if fuentes != 1:
        raise HTTPException(status_code=422,
                            detail="Una sola fuente: `html`, `job_id` + `url_id` o `url`")
    config = _config_de_prueba(db, cuerpo)
    if cuerpo.url is not None:
        return _probar_en_vivo(xm, config, cuerpo.url)
    avisos: list[str] = []
    contenido = None
    render = None
    ruta = cuerpo.ruta or "/"
    if cuerpo.html is not None:
        tam = len(cuerpo.html.encode("utf-8"))
        if tam > MAX_HTML_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"El HTML pesa {tam // 1024} kB; el limite es "
                                       f"{MAX_HTML_BYTES // 1024 // 1024} MB")
        html = cuerpo.html
        fuente = f"HTML pegado ({tam // 1024} kB)"
        if any((r or {}).get("fuente") == "contenido"
               for r in [*(config.get("custom_extraction") or {}).values(),
                         *(config.get("custom_search") or {}).values()]):
            avisos.append("fuente=contenido no se puede probar con HTML pegado: el contenido "
                          "principal lo calcula el extractor del rastreo. Prueba contra una URL "
                          "de un censo con store_raw_html")
    else:
        if cuerpo.url_id is None:
            raise HTTPException(status_code=422, detail="Con job_id hace falta url_id")
        job = db.get(Job, cuerpo.job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        url = db.query(Url).filter(Url.id == cuerpo.url_id, Url.job_id == job.id).first()
        if url is None:
            raise HTTPException(status_code=404, detail="URL not found")
        pc = db.get(PageContent, url.id)
        if pc is None or not pc.raw_html:
            raise HTTPException(
                status_code=409,
                detail="Este censo no guardo el HTML de esa URL (extraction.store_raw_html "
                       "estaba apagado): pega el HTML o prueba con otro censo")
        html = pc.raw_html
        contenido = pc.content_text
        ruta = url.path or "/"
        render = bool((job.config or {}).get("render_js"))
        fuente = f"raw_html guardado de {url.url}"
        if render:
            avisos.append("Este raw_html es el DOM renderizado y ya sin banners: es lo que vio "
                          "el rastreo, no el HTML de origen")

    if config.get("render_js") and cuerpo.html is not None:
        avisos.append("El job renderiza: el rastreo extrae del DOM renderizado y sin banners. "
                      "Para probar lo mismo, pega document.documentElement.outerHTML de DevTools")
    patrones = [(r or {}).get("expresion") or (r or {}).get("patron") or ""
                for r in [*(config.get("custom_extraction") or {}).values(),
                          *(config.get("custom_search") or {}).values()]]
    if any("@" in p for p in patrones):
        avisos.append("La API no tiene autenticacion: lo que se extrae lo puede leer cualquiera "
                      "que llegue al puerto. No extraigas datos personales (correos, telefonos)")

    comp = xm.compilar(config.get("custom_extraction"), config.get("custom_search"))
    if not _pruebas.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=f"Hay {PROBAR_CONCURRENCIA} pruebas de extraccion en curso; vuelve a "
                   f"intentarlo en unos segundos")
    try:
        return _probar(xm, comp, config, cuerpo, html, contenido, ruta, render, fuente, avisos)
    finally:
        _pruebas.release()


def _probar_en_vivo(xm, config: dict, url: str) -> dict[str, Any]:
    """La prueba contra una URL en vivo: la valida aqui (forma y reglas, para
    dar el 422 sin esperar), la encola para el worker y espera su respuesta
    (decision 166). La API no abre ninguna conexion hacia la URL."""
    from api.dependencies import get_redis
    from shared import prueba_en_vivo as pv

    try:
        pv.comprobar_forma(url)
    except pv.URLNoPermitida as exc:
        raise HTTPException(status_code=422, detail=f"url: {exc}") from None
    xm.compilar(config.get("custom_extraction"), config.get("custom_search"))
    if not _pruebas.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=f"Hay {PROBAR_CONCURRENCIA} pruebas de extraccion en curso; vuelve a "
                   f"intentarlo en unos segundos")
    try:
        try:
            rconn = get_redis()
            peticion = {"url": url.strip(), "config": {
                k: config.get(k) for k in ("custom_extraction", "custom_search", "user_agent",
                                           "impersonate", "render_js", "max_urls")}}
            pid = pv.encolar(rconn, peticion)
            resultado = pv.esperar(rconn, pid)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — Redis caido
            raise HTTPException(status_code=503,
                                detail=f"No se pudo encolar la prueba: {exc}") from None
    finally:
        _pruebas.release()
    if resultado is None:
        raise HTTPException(
            status_code=504,
            detail=f"El worker no contesto en {pv.ESPERA_S:.0f} s: puede estar reiniciandose "
                   f"(un despliegue) o la pagina tarda demasiado. Vuelve a intentarlo")
    if not resultado.get("ok"):
        codigo = {"url_no_permitida": 422, "reglas": 422, "descarga": 502}.get(
            resultado.get("motivo"), 500)
        prefijo = "url: " if resultado.get("motivo") == "url_no_permitida" else ""
        raise HTTPException(status_code=codigo, detail=prefijo + str(resultado.get("error")))
    d = resultado.get("descarga") or {}
    return {
        "fuente": f"URL en vivo {d.get('url_final') or url} (HTTP {d.get('status')})",
        "render": False, "ms": resultado.get("ms", 0.0),
        "reglas": resultado.get("reglas") or [],
        "no_evaluadas": resultado.get("no_evaluadas") or [],
        "avisos": resultado.get("avisos") or [],
        "descarga": d,
    }


def _probar(xm, comp, config, cuerpo, html, contenido, ruta, render, fuente,
            avisos) -> dict[str, Any]:
    r = xm.evaluar_documento(comp, html, contenido=contenido, ruta=ruta, render=render,
                             max_urls=config.get("max_urls"))
    return {"fuente": fuente, "render": render, "ms": r["ms"], "reglas": r["reglas"],
            "no_evaluadas": r["no_evaluadas"], "avisos": avisos + r["avisos"]}


@router.get("/jobs/{job_id}/extraccion")
def resumen(job_id: uuid.UUID, db: Session = Depends(get_session)) -> dict[str, Any]:
    from api.routers.results import reglas_del_job
    from shared.extraccion_a_medida import firma

    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    reglas = reglas_del_job(job)
    CE = CustomExtraction
    cuentas: dict[str, dict[str, int]] = {}
    renderizadas: dict[str, int] = {}
    if reglas:
        for nombre, estado, n, rend in (
            db.query(CE.nombre, CE.estado, func.count(),
                     func.sum(case((CE.render.is_(True), 1), else_=0)))
            .filter(CE.job_id == job_id).group_by(CE.nombre, CE.estado)
        ):
            cuentas.setdefault(nombre, {})[estado] = int(n)
            renderizadas[nombre] = renderizadas.get(nombre, 0) + int(rend or 0)
    avisos: list[str] = []
    render_js = bool((job.config or {}).get("render_js"))
    if render_js and reglas:
        avisos.append("Se extrae del DOM renderizado y sin banners: un «no contiene» puede ser "
                      "una etiqueta que inyecta el JavaScript o un banner que el rastreo quito")
    salida = []
    for nombre, (clase, regla) in reglas.items():
        por_estado = cuentas.get(nombre, {})
        evaluadas = sum(por_estado.values())
        top = [
            {"valor": v, "paginas": int(c)}
            for v, c in db.query(CE.valor_clave, func.count())
            .filter(CE.job_id == job_id, CE.nombre == nombre, CE.estado == "ok",
                    CE.valor_clave.isnot(None))
            .group_by(CE.valor_clave).order_by(func.count().desc(), CE.valor_clave).limit(5)
        ] if evaluadas else []
        aviso = None
        if clase == "extractor" and evaluadas and not por_estado.get("ok") \
                and not por_estado.get("conversion_fallida"):
            aviso = (f"no encontro nada en ninguna de las {evaluadas} paginas evaluadas: revisa la "
                     f"expresion{' (¿lo monta JavaScript?)' if not render_js else ''}")
            avisos.append(f"«{regla.get('etiqueta') or nombre}» {aviso}")
        salida.append({
            "nombre": nombre, "clase": clase, "etiqueta": regla.get("etiqueta") or nombre,
            "firma": firma(regla, clase), "regla": regla,
            "evaluadas": evaluadas, "por_estado": por_estado,
            "ok": por_estado.get("ok", 0),
            "sin_coincidencia": por_estado.get("sin_coincidencia", 0),
            "errores": sum(v for k, v in por_estado.items()
                           if k in ("error", "tiempo_agotado", "presupuesto_pagina")),
            "desactivadas": por_estado.get("desactivada", 0),
            "bloqueadas": por_estado.get("pagina_bloqueada", 0),
            "renderizadas": renderizadas.get(nombre, 0),
            "top": top,
            "aviso": aviso,
        })
    return {"job_id": str(job_id), "render_js": render_js, "reglas": salida, "avisos": avisos}


@router.get("/jobs/{job_id}/extraccion/{nombre}/valores")
def valores(job_id: uuid.UUID, nombre: str, page: int = Query(1, ge=1),
            page_size: int = Query(50, ge=1, le=500),
            db: Session = Depends(get_session)) -> dict[str, Any]:
    from api.routers.results import _regla_o_422, reglas_del_job

    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    _regla_o_422(reglas_del_job(job), nombre)
    CE = CustomExtraction
    base = db.query(CE.valor_clave, func.count().label("n")).filter(
        CE.job_id == job_id, CE.nombre == nombre, CE.estado == "ok",
        CE.valor_clave.isnot(None)).group_by(CE.valor_clave)
    total = base.count()
    filas = (base.order_by(func.count().desc(), CE.valor_clave)
             .offset((page - 1) * page_size).limit(page_size).all())
    return {
        "items": [{"valor": v, "paginas": int(n)} for v, n in filas],
        "total": total, "page": page, "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
    }
