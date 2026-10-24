"""Accesibilidad con axe-core por muestra de plantilla (decision 174, Ola 8).

Una pagina del censo por plantilla (hasta `crawl_behavior.axe_por_plantilla`,
y `axe_muestra` en total) se vuelve a pedir con Chromium y, cuando el DOM se
calma, se ejecuta axe-core sobre ella. Apagado de serie (`axe_muestra` 0):
cada pagina muestreada es un render mas, tambien en un censo sin render.

El patron es el de las sondas de las decisiones 119 y 140: fuera de
`max_urls`, sin `PageItem`, sin seguir redirecciones, respetando robots.txt,
con `dont_filter` y prioridad alta, cupos que sobreviven a una reanudacion, y
una fila en `capturas_alternativas` con `perfil='axe'` aunque no haya ninguna
violacion: «sin violaciones» es un dato y «sin fila» es «no evaluada».

axe-core va **vendorizado** en `seo_crawler/vendor/axe/` (MPL-2.0, con su
licencia y la de terceros; version y suma en el README de esa carpeta): nada
se descarga de un CDN durante el rastreo. Se inyecta con `page.evaluate`, que
no pasa por la CSP de la pagina (medido: corre igual con
`script-src 'self'`), y los textos de ayuda salen en castellano con su
`locales/es.json`.

Lo que ve axe es la pagina tal como la deja el render del crawler, con sus
limitaciones: imagenes, fuentes y rastreadores de analitica bloqueados
(`PLAYWRIGHT_ABORT_REQUEST`), sin entrar en iframes y sin interaccion (ni
teclado ni menus abiertos). A diferencia del render del censo, aqui NO se
quitan los banners de cookies ni los modales: forman parte de la pagina que
recibe quien la visita.
"""

from __future__ import annotations

import json
import logging
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Generator
from urllib.parse import urlparse

import scrapy
from scrapy.http import Response

from shared.accesibilidad import ETIQUETAS, PERFIL, resumir
from shared.plantillas import firma_de_ruta

logger = logging.getLogger(__name__)

VENDOR = Path(__file__).resolve().parent / "vendor" / "axe"
POR_PLANTILLA_POR_DEFECTO = 1
# Tope de axe dentro de la pagina: una pagina enorme puede tardar mucho, y la
# promesa SIEMPRE resuelve (como la espera del DOM del spider).
TOPE_AXE_MS = 20000
# El render, la espera del DOM y axe: por encima del DOWNLOAD_TIMEOUT de serie.
DOWNLOAD_TIMEOUT_S = 90
LATIDO_S = 60.0

_EJECUTAR = """
(async () => {
  const t0 = performance.now();
  const ax = window.axe;
  if (!ax) return {error: 'axe_no_cargado'};
  if (window._axeLocale) { try { ax.configure({locale: window._axeLocale}); } catch (e) {} }
  const tope = new Promise(r => setTimeout(() => r({error: 'tope_axe'}), TOPE_MS));
  const corre = ax.run(document, {
    runOnly: {type: 'tag', values: ETIQUETAS},
    resultTypes: ['violations', 'incomplete'],
    iframes: false, elementRef: false,
  }).then(r => {
    const regla = (v, conNodos) => ({
      id: v.id, impact: v.impact, tags: v.tags, help: v.help, helpUrl: v.helpUrl,
      n: v.nodes.length,
      nodes: conNodos ? v.nodes.slice(0, 3).map(n => ({target: n.target, html: n.html})) : [],
    });
    return {version: ax.version, ms: performance.now() - t0, passes: r.passes.length,
            violations: r.violations.map(v => regla(v, true)),
            incomplete: r.incomplete.map(v => regla(v, false))};
  }, e => ({error: String(e && e.message || e).slice(0, 200)}));
  return await Promise.race([corre, tope]);
})()
"""


@lru_cache(maxsize=1)
def script_axe() -> str:
    """axe-core, su locale y la llamada, en un solo `evaluate`."""
    fuente = (VENDOR / "axe.min.js").read_text(encoding="utf-8")
    try:
        locale = json.dumps(json.loads((VENDOR / "es.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        locale = "null"
    llamada = (_EJECUTAR.replace("TOPE_MS", str(TOPE_AXE_MS))
               .replace("ETIQUETAS", json.dumps(list(ETIQUETAS))))
    return f"{fuente}\n;window._axeLocale = {locale};\n{llamada}"


async def ejecutar_axe(page, script: str) -> dict[str, Any]:
    """`PageMethod`: ejecuta axe y devuelve su resultado o `{"error": ...}`.

    Nunca lanza: un PageMethod que lanza tumba la pestaña (ver
    `_evaluar_tolerante` en el spider), y una pagina que no se pudo evaluar es
    un dato («no evaluada»), no un fallo del rastreo.
    """
    try:
        r = await page.evaluate(script)
        return r if isinstance(r, dict) else {"error": "sin_resultado"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


class SondaAxeMixin:
    """Metodos del spider. Estado en `self._axe_*`."""

    def _iniciar_axe(self, session=None) -> None:
        cb = self.job_config.get("crawl_behavior") or {}
        self._axe_max = max(0, int(cb.get("axe_muestra") or 0))
        self._axe_por_plantilla = max(1, int(cb.get("axe_por_plantilla") or POR_PLANTILLA_POR_DEFECTO))
        self._axe_cupos: dict[str, int] = {}
        self._axe_pedidas = 0
        self._axe_latido = time.monotonic()
        self._axe_reglas: list[tuple[str, re.Pattern]] = []
        if self._axe_max <= 0:
            return
        try:
            script_axe()
        except OSError as exc:
            logger.warning("axe-core no esta en %s; sin accesibilidad: %s", VENDOR, exc)
            self._axe_max = 0
            return
        for regla in self.job_config.get("templates") or []:
            if not isinstance(regla, dict):
                continue
            nombre, patron = regla.get("nombre"), regla.get("patron")
            try:
                if nombre and patron:
                    self._axe_reglas.append((str(nombre), re.compile(str(patron), re.I)))
            except re.error:
                pass
        if session is not None:
            try:
                from sqlalchemy import func

                from shared.models import CapturaAlternativa as C

                with session.begin_nested():
                    for plantilla, n in (
                        session.query(C.plantilla, func.count())
                        .filter(C.job_id == self.job_id, C.perfil == PERFIL)
                        .group_by(C.plantilla).all()
                    ):
                        self._axe_cupos[plantilla or ""] = int(n)
                        self._axe_pedidas += int(n)
            except Exception:  # noqa: BLE001 -- sin tabla, se empieza de cero
                pass
        logger.info("Accesibilidad (axe): hasta %d paginas, %d por plantilla",
                    self._axe_max, self._axe_por_plantilla)

    def _plantilla_axe(self, url: str) -> str:
        path = urlparse(url).path or "/"
        for nombre, patron in self._axe_reglas:
            if patron.search(path):
                return nombre
        return firma_de_ruta(path)

    def _axe_de_pagina(self, response: Response, url: str) -> Generator:
        if getattr(self, "_axe_max", 0) <= 0 or self._axe_pedidas >= self._axe_max:
            return
        plantilla = self._plantilla_axe(url)
        if self._axe_cupos.get(plantilla, 0) >= self._axe_por_plantilla:
            return
        self._axe_cupos[plantilla] = self._axe_cupos.get(plantilla, 0) + 1
        self._axe_pedidas += 1
        yield self._peticion_axe(url, plantilla)

    def _peticion_axe(self, url: str, plantilla: str):
        from scrapy_playwright.page import PageMethod

        from seo_crawler.spiders.seo_spider import _JS_ESPERAR_DOM_QUIETO, _evaluar_tolerante

        meta: dict[str, Any] = self._playwright_meta()
        # Sin el limpiador de banners ni las medidas del censo: la espera a
        # que el DOM se calme y axe.
        meta["playwright_page_methods"] = [
            PageMethod(_evaluar_tolerante, _JS_ESPERAR_DOM_QUIETO),
            PageMethod(ejecutar_axe, script_axe()),
        ]
        meta.update({
            "depth": 0, "dont_redirect": True, "handle_httpstatus_all": True,
            "download_timeout": DOWNLOAD_TIMEOUT_S, "cookiejar": "axe",
            "sonda_axe": {"url": url, "plantilla": plantilla},
        })
        return scrapy.Request(url, callback=self.parse_axe, errback=self.axe_error,
                              meta=meta, dont_filter=True, priority=10)

    def _latido_axe(self) -> None:
        ahora = time.monotonic()
        if ahora - getattr(self, "_axe_latido", 0.0) >= LATIDO_S:
            self._axe_latido = ahora
            try:
                self._write_heartbeat()
            except Exception:  # noqa: BLE001
                pass

    def _item_axe(self, datos: dict, *, estado: str, status=None, clase=None, redirige_a=None,
                  medidas=None, detalle=None):
        from seo_crawler.extractors import compute_url_hash
        from seo_crawler.items import CapturaAlternativaItem

        return CapturaAlternativaItem(
            url_hash=compute_url_hash(datos["url"]), job_id=self.job_id,
            perfil=PERFIL, modo="render", estado=estado, plantilla=datos["plantilla"],
            user_agent=None, status_code=status, response_class=clase, redirige_a=redirige_a,
            vary_base=None, vary=None, medidas=medidas, diferencias=None, volatiles=[],
            detalle=detalle or None,
        )

    @staticmethod
    def _resultado_axe(response: Response) -> Any:
        for metodo in response.meta.get("playwright_page_methods") or []:
            if getattr(metodo, "method", None) is ejecutar_axe:
                return getattr(metodo, "result", None)
        return None

    def parse_axe(self, response: Response) -> Generator:
        from seo_crawler.sonda_paridad import _destino
        from shared.respuestas import NO_VALIDAS

        self._latido_axe()
        datos = response.meta["sonda_axe"]
        clase = self._clase_paridad(response)
        latencia = response.meta.get("download_latency")
        detalle: dict[str, Any] = {}
        if isinstance(latencia, (int, float)):
            detalle["ms_total"] = round(latencia * 1000)
        destino = _destino(response)
        if clase in NO_VALIDAS:
            motivo = "respuesta_no_valida"
        elif destino is not None:
            motivo = "redirige"
        elif not (200 <= response.status < 300):
            motivo = "status"
        else:
            motivo = None
        if motivo:
            yield self._item_axe(datos, estado="no_evaluada", status=response.status, clase=clase,
                                 redirige_a=destino, detalle={**detalle, "motivo": motivo})
            return
        crudo = self._resultado_axe(response)
        medidas = resumir(crudo)
        if medidas is None:
            error = crudo.get("error") if isinstance(crudo, dict) else None
            yield self._item_axe(datos, estado="no_evaluada", status=response.status, clase=clase,
                                 detalle={**detalle, "motivo": "axe_fallido",
                                          "error": str(error or "sin_resultado")[:200]})
            return
        detalle["ms_axe"] = medidas.get("ms_axe")
        yield self._item_axe(datos, estado="evaluada", status=response.status, clase=clase,
                             medidas=medidas, detalle=detalle)

    def axe_error(self, failure) -> Generator:
        self._latido_axe()
        datos = failure.request.meta.get("sonda_axe") or {}
        if not datos:
            return
        motivo = ("bloqueada_por_robots" if failure.check(scrapy.exceptions.IgnoreRequest)
                  else "error")
        yield self._item_axe(datos, estado="no_evaluada", detalle={
            "motivo": motivo, "error": f"{type(failure.value).__name__}: {failure.value}"[:200]})
