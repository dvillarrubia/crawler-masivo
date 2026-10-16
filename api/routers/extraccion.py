"""Extraccion y busqueda a medida (#34, decision 104): probar reglas y leer
lo que dieron en un censo.

- `POST /api/extraccion/probar` aplica las reglas a un HTML pegado o al
  `raw_html` de una URL de un censo. **Sin red**: no descarga nada, asi que no
  hay superficie de SSRF (la prueba contra una URL en vivo la hara el worker,
  PR 2). Usa la MISMA funcion que el spider (`shared.extraccion_a_medida`).
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
import time
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
AVISO_MS = 50.0
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


def _config_de_prueba(db: Session, cuerpo: ProbarExtraccion) -> dict:
    propias = {
        "custom_extraction": {k: (v.model_dump() if v is not None else None)
                              for k, v in cuerpo.custom_extraction.items()},
        "custom_search": {k: (v.model_dump() if v is not None else None)
                          for k, v in cuerpo.custom_search.items()},
    }
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


def _titulo(raiz) -> str | None:
    if raiz is None:
        return None
    t = raiz.find(".//title")
    return (t.text or "").strip() if t is not None and t.text else None


@router.post("/extraccion/probar")
def probar(cuerpo: ProbarExtraccion, db: Session = Depends(get_session)) -> dict[str, Any]:
    from shared import extraccion_a_medida as xm

    if (cuerpo.html is None) == (cuerpo.job_id is None):
        raise HTTPException(status_code=422,
                            detail="Una sola fuente: `html` o `job_id` + `url_id`")
    config = _config_de_prueba(db, cuerpo)
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


def _probar(xm, comp, config, cuerpo, html, contenido, ruta, render, fuente,
            avisos) -> dict[str, Any]:
    from lxml import html as lhtml

    try:
        raiz = lhtml.fromstring(html) if html.strip() else None
    except Exception:  # noqa: BLE001 — HTML que lxml no entiende
        raiz = None
    texto = None
    if raiz is not None:
        from shared.texto_visible import _lineas_visibles

        body = raiz.find(".//body")
        texto = "\n".join(_lineas_visibles(body if body is not None else raiz))
    medir: dict[str, float] = {}
    t0 = time.perf_counter()
    filas = xm.aplicar(raiz, html, comp, texto=texto, contenido=contenido, ruta=ruta,
                       titulo=_titulo(raiz), render=render, presupuesto=xm.Presupuesto(),
                       medir=medir)
    total_ms = (time.perf_counter() - t0) * 1000
    evaluadas = {f["nombre"] for f in filas}
    max_urls = config.get("max_urls")
    for nombre, ms in medir.items():
        if ms > AVISO_MS:
            paginas = max_urls or 10_000
            avisos.append(
                f"{nombre}: {ms:.0f} ms en esta pagina; x {paginas:,} paginas son unos "
                f"{ms * paginas / 60000:.0f} min de rastreo".replace(",", "."))
    return {
        "fuente": fuente,
        "render": render,
        "ms": round(total_ms, 2),
        "reglas": [
            {**{k: f[k] for k in ("nombre", "clase", "estado", "n", "valor", "valores",
                                  "valor_num", "truncado", "detalle")},
             "ms": round(medir.get(f["nombre"], 0.0), 2)}
            for f in filas
        ],
        "no_evaluadas": [rc.nombre for rc in comp.reglas if rc.nombre not in evaluadas],
        "avisos": avisos,
    }


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
