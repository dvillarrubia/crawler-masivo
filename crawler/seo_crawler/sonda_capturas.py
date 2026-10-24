"""Capturas de pantalla por muestra de plantilla (decision 173, Ola 7.4).

Con `crawl_behavior.capturas_muestra > 0`, al terminar `parse` de una pagina
interna HTML 2xx que no es un muro, si su plantilla (`config.templates` del job
o `shared.plantillas.firma_de_ruta`) no ha llenado su cupo
(`capturas_por_plantilla`), se vuelve a pedir esa URL CON RENDER y se guarda
una imagen de la primera pantalla (`shared.capturas_pantalla`). Tambien en un
censo sin render: lo que se quiere ver es como se pinta la pagina, y eso solo
lo da un navegador.

El dispositivo es el del censo (`shared.perfiles.perfil_de_ua`): el contexto
`custom`, que ya lleva el UA, el viewport y el DPR del job. Con
`capturas_ambos_perfiles`, ademas el otro, con el MISMO contexto con nombre que
la paridad movil (`perfil_movil` / `perfil_escritorio`, decision 119) y los
mismos argumentos: un contexto de Playwright se crea con los de la primera
peticion que lo nombra, asi que dos sondas que lo crearan distinto se pisarian.

Es una sonda (patron de la decision 105): callback y errback propios, sin
`PageItem`, `dont_filter`, fuera de `max_urls`, robots.txt respetado y sin
tocar la salud del rastreo. Una captura que es un muro no se guarda: no es la
pagina (decision 103). Y todo es best-effort con el `try` sobre la funcion
entera (decision 40): sin captura, el rastreo sigue igual.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Generator
from urllib.parse import urlparse

import scrapy
from scrapy.http import Response
from scrapy_playwright.page import PageMethod

from shared import capturas_pantalla
from shared.perfiles import contexto_playwright, perfil_de_ua, ua_de_perfil_alternativo
from shared.plantillas import firma_de_ruta

logger = logging.getLogger(__name__)

POR_PLANTILLA_POR_DEFECTO = 1
MAX_MUESTRA = 50
LATIDO_S = 60.0


class CapturasPantallaMixin:
    """Metodos del spider. Estado en `self._capt_*`."""

    def _iniciar_capturas(self) -> None:
        self._capt_max = 0
        try:
            cb = self.job_config.get("crawl_behavior") or {}
            self._capt_max = max(0, min(MAX_MUESTRA, int(cb.get("capturas_muestra") or 0)))
            if self._capt_max <= 0:
                return
            self._capt_por_plantilla = max(1, int(cb.get("capturas_por_plantilla")
                                                  or POR_PLANTILLA_POR_DEFECTO))
            self._capt_ua_censo = (self.job_config.get("user_agent")
                                   or self.settings.get("USER_AGENT") or "")
            self._capt_perfil_censo = perfil_de_ua(self._capt_ua_censo)
            self._capt_perfiles = [self._capt_perfil_censo]
            if cb.get("capturas_ambos_perfiles"):
                self._capt_perfiles.append(
                    "escritorio" if self._capt_perfil_censo == "movil" else "movil")
            self._capt_cb = cb
            self._capt_latido = time.monotonic()
            self._capt_reglas: list[tuple[str, re.Pattern]] = []
            for regla in self.job_config.get("templates") or []:
                if not isinstance(regla, dict):
                    continue
                nombre, patron = regla.get("nombre"), regla.get("patron")
                try:
                    if nombre and patron:
                        self._capt_reglas.append((str(nombre), re.compile(str(patron), re.I)))
                except re.error:
                    pass
            # Al reanudar, lo ya capturado cuenta: por PAGINA con alguna captura,
            # de cualquier perfil, que es como se gasta el cupo al pedir. Contar
            # solo el perfil del censo devolveria cupo a una plantilla cuya
            # captura del censo fallo y la del otro perfil no, y ese perfil
            # pasaria del tope en cada reanudacion. Una pagina sin ninguna
            # captura guardada no deja rastro y vuelve a tener cupo: no pasa
            # del tope, solo lo reintenta.
            self._capt_cupos = dict(capturas_pantalla.paginas_por_plantilla(self.job_id))
            self._capt_pedidas = sum(self._capt_cupos.values())
            logger.info("Capturas de pantalla: hasta %d paginas, %d por plantilla, perfiles %s",
                        self._capt_max, self._capt_por_plantilla, ", ".join(self._capt_perfiles))
        except Exception:  # noqa: BLE001
            logger.warning("Capturas de pantalla apagadas: no se pudo leer su config", exc_info=True)
            self._capt_max = 0

    def _plantilla_captura(self, url: str) -> str:
        path = urlparse(url).path or "/"
        for nombre, patron in getattr(self, "_capt_reglas", ()):
            if patron.search(path):
                return nombre
        return firma_de_ruta(path)

    def _capturas_de_pagina(self, response: Response, url: str) -> Generator:
        """Las sondas de captura de esta pagina, si su plantilla tiene cupo."""
        if getattr(self, "_capt_max", 0) <= 0 or self._capt_pedidas >= self._capt_max:
            return
        try:
            plantilla = self._plantilla_captura(url)
            if self._capt_cupos.get(plantilla, 0) >= self._capt_por_plantilla:
                return
            self._capt_cupos[plantilla] = self._capt_cupos.get(plantilla, 0) + 1
            self._capt_pedidas += 1
            peticiones = [self._peticion_captura(url, plantilla, p) for p in self._capt_perfiles]
        except Exception:  # noqa: BLE001
            logger.debug("Captura: no se pudo preparar %s", url, exc_info=True)
            return
        yield from peticiones

    def _peticion_captura(self, url: str, plantilla: str, perfil: str) -> scrapy.Request:
        meta: dict[str, Any] = self._playwright_meta()
        meta.update({
            "depth": 0, "dont_redirect": True, "handle_httpstatus_all": True,
            "captura": {"url": url, "plantilla": plantilla, "perfil": perfil},
            # Jar propio si el job lo enciende (decision 114), como la paridad.
            "cookiejar": f"captura-{perfil}",
            "playwright_page_init_callback": "seo_crawler.render.preparar_pagina_captura",
        })
        meta["playwright_page_methods"] = list(meta.get("playwright_page_methods") or []) + [
            PageMethod(_capturar, {}),
        ]
        if perfil != self._capt_perfil_censo:
            ua = ua_de_perfil_alternativo(perfil, self._capt_cb.get("paridad_movil_ua"))
            base_ctx = (self.settings.getdict("PLAYWRIGHT_CONTEXTS") or {}).get("custom") or {}
            meta["user_agent"] = ua
            meta["playwright_context"] = f"perfil_{perfil}"
            meta["playwright_context_kwargs"] = contexto_playwright(ua, perfil == "movil",
                                                                    base=base_ctx)
        return scrapy.Request(url, callback=self.parse_captura, errback=self.captura_error,
                              meta=meta, dont_filter=True, priority=5)

    def _latido_captura(self) -> None:
        ahora = time.monotonic()
        if ahora - getattr(self, "_capt_latido", 0.0) >= LATIDO_S:
            self._capt_latido = ahora
            try:
                self._write_heartbeat()
            except Exception:  # noqa: BLE001
                pass

    def parse_captura(self, response: Response) -> None:
        try:
            self._latido_captura()
            datos = response.meta["captura"]
            imagen = None
            for pm in response.meta.get("playwright_page_methods") or ():
                if getattr(pm, "method", None) is _capturar:
                    imagen = pm.result
            if not imagen:
                logger.info("Captura %s de %s: sin imagen", datos["perfil"], datos["url"])
                return
            from shared.respuestas import NO_VALIDAS

            clase = self._clase_paridad(response)
            if clase in NO_VALIDAS or not (200 <= response.status < 300):
                # Un muro o un error no es la pagina (decision 103).
                logger.info("Captura %s de %s descartada: %s %s", datos["perfil"], datos["url"],
                            response.status, clase or "")
                return
            from seo_crawler.extractors import compute_url_hash

            vp = (response.meta.get("playwright_context_kwargs") or {}).get("viewport")
            if not vp:
                ctx = (self.settings.getdict("PLAYWRIGHT_CONTEXTS") or {}).get(
                    response.meta.get("playwright_context") or "custom") or {}
                vp = ctx.get("viewport") or {}
            capturas_pantalla.guardar(
                self.job_id, compute_url_hash(datos["url"]), datos["perfil"], imagen,
                {"url": datos["url"], "plantilla": datos["plantilla"],
                 "url_final": response.url if response.url != datos["url"] else None,
                 "status_code": response.status,
                 "ancho": vp.get("width"), "alto": vp.get("height"),
                 "user_agent": response.request.headers.get(b"User-Agent", b"").decode(
                     "latin-1", "replace") or None})
        except Exception:  # noqa: BLE001
            logger.warning("No se pudo guardar la captura de %s", response.url, exc_info=True)

    def captura_error(self, failure) -> None:
        self._latido_captura()
        datos = failure.request.meta.get("captura") or {}
        logger.info("Captura %s de %s fallida: %s", datos.get("perfil"), datos.get("url"),
                    f"{type(failure.value).__name__}: {failure.value}"[:200])


async def _capturar(page, opciones: dict | None = None):
    from seo_crawler.render import capturar_pantalla

    return await capturar_pantalla(page, opciones)
