"""Respuesta frente a render por muestra de plantilla (decision 140, Ola 7.1).

Google lee una pagina dos veces: el HTML que responde el servidor y, despues,
lo que deja el JavaScript al renderizar. Lo que solo esta en una de las dos se
lee distinto:

- un `noindex` en el HTML inicial hace que Google NO renderice la pagina: la
  saca del indice aunque el JavaScript luego quite la directiva;
- un canonical que el JavaScript cambia es una senal contradictoria, y Google
  puede quedarse con cualquiera de los dos;
- el texto o los enlaces que solo aparecen al renderizar los ve Google con
  retraso, y no los ve NINGUN rastreador de IA (GPTBot, ClaudeBot,
  PerplexityBot no ejecutan JavaScript).

El rastreo guarda una sola de las dos versiones (la del modo del job). Esta
sonda pide la otra para una muestra por plantilla y compara campo a campo con
`shared.paridad.diferencias`, como la paridad movil (decision 119), de la que
copia el resto del patron: sonda fuera de `max_urls`, `dont_filter`, sin
seguir redirecciones, respeta robots.txt, y si algo difiere se vuelve a pedir
con el modo del censo para no afirmar lo que tambien cambia entre dos
peticiones iguales (decision 13).

Por defecto solo en los rastreos CON render (`crawl_behavior.render_crudo_muestra`
vale 30 si `render_js`, 0 si no): ahi la otra version es una peticion sin
render, barata. Sin render, la otra version es un render por pagina muestreada
y hay que pedirlo (y `js_check`, decision 35, ya mira los enlaces).

Las diferencias se guardan ORIENTADAS, `render` y `crudo`, sea cual sea el modo
del censo: en los enlaces, `faltan` son los que solo aparecen al renderizar.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Generator
from urllib.parse import urlparse

import scrapy
from scrapy.http import HtmlResponse, Response

from shared.paridad import diferencias, resumen
from shared.plantillas import firma_de_ruta

logger = logging.getLogger(__name__)

MUESTRA_CON_RENDER = 30
POR_PLANTILLA_POR_DEFECTO = 3
LATIDO_S = 60.0


def orientar(render: dict, crudo: dict) -> list[dict[str, Any]]:
    """Las diferencias con las claves `render` y `crudo`. En los enlaces,
    `faltan` = los que el render tiene y el crudo no (solo con JavaScript)."""
    salida = []
    for d in diferencias(render, crudo):
        o = {k: v for k, v in d.items() if k not in ("base", "alternativa")}
        o["render"], o["crudo"] = d["base"], d["alternativa"]
        salida.append(o)
    return salida


def campos_que_cambian(a: dict, b: dict) -> set[str]:
    """Los campos que difieren entre dos capturas del MISMO modo, en las dos
    direcciones (en los enlaces `diferencias` solo cuenta los que faltan)."""
    return {d["campo"] for d in diferencias(a, b)} | {d["campo"] for d in diferencias(b, a)}


class RenderCrudoMixin:
    """Metodos del spider. Estado en `self._crudo_*`."""

    def _iniciar_render_crudo(self, session=None) -> None:
        cb = self.job_config.get("crawl_behavior") or {}
        render = bool(self.job_config.get("render_js"))
        muestra = cb.get("render_crudo_muestra")
        self._crudo_max = int(muestra if muestra is not None else (MUESTRA_CON_RENDER if render else 0))
        self._crudo_por_plantilla = max(1, int(cb.get("render_crudo_por_plantilla")
                                               or POR_PLANTILLA_POR_DEFECTO))
        self._crudo_censo_render = render
        # La version que NO es la del censo: sin render si el censo renderiza.
        self._crudo_perfil = "crudo" if render else "render"
        self._crudo_cupos: dict[str, int] = {}
        self._crudo_pedidas = 0
        self._crudo_latido = time.monotonic()
        if self._crudo_max <= 0:
            return
        self._crudo_reglas: list[tuple[str, re.Pattern]] = []
        for regla in self.job_config.get("templates") or []:
            if not isinstance(regla, dict):
                continue
            nombre, patron = regla.get("nombre"), regla.get("patron")
            try:
                if nombre and patron:
                    self._crudo_reglas.append((str(nombre), re.compile(str(patron), re.I)))
            except re.error:
                pass
        if session is not None:
            try:
                from sqlalchemy import func

                from shared.models import CapturaAlternativa as C

                with session.begin_nested():
                    for plantilla, n in (
                        session.query(C.plantilla, func.count())
                        .filter(C.job_id == self.job_id, C.perfil == self._crudo_perfil)
                        .group_by(C.plantilla).all()
                    ):
                        self._crudo_cupos[plantilla or ""] = int(n)
                        self._crudo_pedidas += int(n)
            except Exception:  # noqa: BLE001 -- sin tabla, se empieza de cero
                pass
        logger.info("Respuesta frente a render: version %s, hasta %d paginas, %d por plantilla",
                    self._crudo_perfil, self._crudo_max, self._crudo_por_plantilla)

    def _plantilla_crudo(self, url: str) -> str:
        path = urlparse(url).path or "/"
        for nombre, patron in getattr(self, "_crudo_reglas", ()):
            if patron.search(path):
                return nombre
        return firma_de_ruta(path)

    def _render_crudo_de_pagina(self, response: Response, url: str) -> Generator:
        if getattr(self, "_crudo_max", 0) <= 0 or self._crudo_pedidas >= self._crudo_max:
            return
        # La pagina del censo tiene que estar en el modo del job: una que el
        # spider repitio sin render (decision 17) no es el render.
        render = bool(response.meta.get("playwright")) and not response.meta.get("_sin_render")
        if render != self._crudo_censo_render:
            return
        plantilla = self._plantilla_crudo(url)
        if self._crudo_cupos.get(plantilla, 0) >= self._crudo_por_plantilla:
            return
        from seo_crawler.sonda_paridad import huella_paridad

        try:
            base = huella_paridad(response, url=url, allowed_hosts=self.allowed_hosts,
                                  extraction=self.job_config.get("extraction") or {})
        except Exception as exc:  # noqa: BLE001
            logger.debug("Render/crudo: no se pudo medir %s: %s", url, exc)
            return
        self._crudo_cupos[plantilla] = self._crudo_cupos.get(plantilla, 0) + 1
        self._crudo_pedidas += 1
        datos = {"etapa": "alternativa", "plantilla": plantilla, "url": url, "base": base}
        yield self._peticion_crudo(url, datos, render=not self._crudo_censo_render)

    def _peticion_crudo(self, url: str, datos: dict, *, render: bool):
        meta: dict[str, Any] = {
            "depth": 0, "dont_redirect": True, "handle_httpstatus_all": True,
            "render_crudo": datos, "cookiejar": f"render-crudo-{'r' if render else 'c'}",
        }
        if render:
            meta.update(self._playwright_meta())
        return scrapy.Request(url, callback=self.parse_render_crudo, errback=self.render_crudo_error,
                              meta=meta, dont_filter=True, priority=10)

    def _latido_crudo(self) -> None:
        ahora = time.monotonic()
        if ahora - getattr(self, "_crudo_latido", 0.0) >= LATIDO_S:
            self._crudo_latido = ahora
            try:
                self._write_heartbeat()
            except Exception:  # noqa: BLE001
                pass

    def _item_crudo(self, datos: dict, *, estado: str, status=None, clase=None, redirige_a=None,
                    medidas=None, difs=None, volatiles=None, detalle=None):
        from seo_crawler.extractors import compute_url_hash
        from seo_crawler.items import CapturaAlternativaItem

        return CapturaAlternativaItem(
            url_hash=compute_url_hash(datos["url"]), job_id=self.job_id,
            perfil=self._crudo_perfil,
            modo="sin_render" if self._crudo_censo_render else "render",
            estado=estado, plantilla=datos["plantilla"], user_agent=None,
            status_code=status, response_class=clase, redirige_a=redirige_a,
            vary_base=None, vary=None,
            medidas=resumen(medidas) if medidas else None,
            diferencias=difs if difs is not None else None,
            volatiles=sorted(volatiles) if volatiles else [],
            detalle=detalle or None,
        )

    def _render_y_crudo(self, datos: dict, alternativa: dict) -> tuple[dict, dict]:
        if self._crudo_censo_render:
            return datos["base"], alternativa
        return alternativa, datos["base"]

    def parse_render_crudo(self, response: Response) -> Generator:
        from seo_crawler.sonda_paridad import _destino, huella_paridad
        from shared.respuestas import NO_VALIDAS

        self._latido_crudo()
        datos = response.meta["render_crudo"]
        clase = self._clase_paridad(response)
        destino = _destino(response)
        es_pagina = (clase not in NO_VALIDAS and destino is None
                     and isinstance(response, HtmlResponse) and 200 <= response.status < 300)
        captura = None
        if es_pagina:
            try:
                captura = huella_paridad(response, url=datos["url"], allowed_hosts=self.allowed_hosts,
                                         extraction=self.job_config.get("extraction") or {})
            except Exception as exc:  # noqa: BLE001
                logger.debug("Render/crudo: no se pudo medir %s: %s", response.url, exc)

        if datos["etapa"] == "control":
            if captura is None:
                yield self._item_crudo(
                    datos, estado="no_evaluada", status=datos.get("status"), clase=datos.get("clase"),
                    medidas=datos.get("alternativa"),
                    detalle={"motivo": "control_fallido", "control_status": response.status,
                             "control_clase": clase})
                return
            if datos.get("control_de") == "respuesta":
                yield self._item_crudo(datos, estado="respuesta_distinta", status=datos["status"],
                                       clase=datos["clase"], redirige_a=datos.get("redirige_a"),
                                       detalle={"status_base": datos["base"].get("status")})
                return
            volatiles = campos_que_cambian(datos["base"], captura)
            yield self._item_crudo(
                datos, estado="comparada", status=datos["status"], clase=datos["clase"],
                medidas=datos["alternativa"],
                difs=[d for d in datos["difs"] if d["campo"] not in volatiles], volatiles=volatiles)
            return

        # etapa alternativa
        if clase in NO_VALIDAS:
            yield self._item_crudo(datos, estado="no_evaluada", status=response.status, clase=clase,
                                   detalle={"motivo": "respuesta_no_valida"})
            return
        if destino is not None or not (200 <= response.status < 300):
            # Otro codigo o una redireccion en la otra version: antes de
            # afirmarlo, otra vez en el modo del censo.
            datos.update({"etapa": "control", "control_de": "respuesta", "status": response.status,
                          "clase": clase, "redirige_a": destino})
            yield self._peticion_crudo(datos["url"], datos, render=self._crudo_censo_render)
            return
        if captura is None:
            yield self._item_crudo(datos, estado="no_evaluada", status=response.status, clase=clase,
                                   detalle={"motivo": "no_html"})
            return
        render, crudo = self._render_y_crudo(datos, captura)
        difs = orientar(render, crudo)
        if not difs:
            yield self._item_crudo(datos, estado="comparada", status=response.status, clase=clase,
                                   medidas=captura, difs=[])
            return
        datos.update({"etapa": "control", "status": response.status, "clase": clase,
                      "alternativa": captura, "difs": difs})
        yield self._peticion_crudo(datos["url"], datos, render=self._crudo_censo_render)

    def render_crudo_error(self, failure) -> Generator:
        self._latido_crudo()
        datos = failure.request.meta.get("render_crudo") or {}
        if not datos:
            return
        motivo = ("bloqueada_por_robots" if failure.check(scrapy.exceptions.IgnoreRequest)
                  else f"{type(failure.value).__name__}: {failure.value}"[:200])
        detalle = {"motivo": "control_fallido" if datos.get("etapa") == "control" else "error",
                   "error": motivo}
        yield self._item_crudo(datos, estado="no_evaluada", detalle=detalle,
                               status=datos.get("status"), clase=datos.get("clase"),
                               medidas=datos.get("alternativa"))
