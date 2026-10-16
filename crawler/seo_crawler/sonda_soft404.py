"""Sonda de soft 404 durante el rastreo (decision 105).

Al empezar, el spider pide por cada host de las semillas una URL que no puede
existir (`shared.soft404.mangle_path`), y despues una por cada host interno y
cada plantilla de ruta nueva que encuentra (`shared.plantillas.firma_de_ruta`),
hasta
`crawl_behavior.soft404_sondas`. Lo que responde el sitio a esa URL es su
pagina de error: si es un 200, el sitio tiene soft 404 de serie; si es un 404
con cuerpo, ese cuerpo sigue siendo la huella con la que reconocer las paginas
que lo sirven con 200.

Tres cosas que no puede hacer, y por eso va con su propio callback:

- **gastar `max_urls`**: no pasa por `parse`, que es quien cuenta;
- **guardarse como URL del censo**: no emite ningun item. El resultado va a
  `jobs.soft404["sondas"]`, que no es una tabla del censo ni entra en ningun
  recuento, CSV, grafo o comparacion;
- **seguir nada**: ni redirecciones (se anota a donde iba) ni enlaces.

Respeta robots.txt como cualquier otra peticion: una sonda prohibida se anota
como tal y no se pide.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Generator
from urllib.parse import urlparse, urlsplit

import scrapy
from scrapy.http import HtmlResponse, Response

from shared.plantillas import firma_de_ruta
from shared.soft404 import MAX_TEXTO_SONDA, campos_de_huella, mangle_path, token_sonda  # noqa: F401

logger = logging.getLogger(__name__)

SONDAS_POR_DEFECTO = 12


def huella_de_respuesta(selector, url: str, *, extraction: dict | None = None) -> dict:
    """Titulo, primer h1 que se pinta y contenido principal de una respuesta.

    Se extrae con las MISMAS funciones que las paginas del censo
    (`extract_main_content` con la configuracion del job): si la sonda se
    extrajera de otra manera, su huella no casaria con la de ninguna pagina.
    """
    from seo_crawler.extractors import (
        contar_palabras, extract_headings, extract_main_content, extract_meta,
        extract_word_count, hash_de_contenido,
    )

    extraction = extraction or {}
    meta = extract_meta(selector, base_url=url)
    h1 = next((h["text"] for h in extract_headings(selector)
               if h.get("tag") == "h1" and not h.get("oculto") and h.get("text")), None)
    texto = None
    if extraction.get("extract_page_content", True):
        texto = extract_main_content(
            selector,
            word_count=extract_word_count(selector),
            strip_promo=extraction.get("strip_promo_blocks", True),
            extra_selectors=extraction.get("custom_boilerplate_selectors") or None,
        )
    titulo = meta.get("title") or None
    return {
        "titulo": titulo,
        "h1": h1,
        "palabras": contar_palabras(texto),
        "content_hash": hash_de_contenido(texto),
        # El texto se guarda recortado, pero la huella va sobre el ENTERO
        **campos_de_huella(texto, url, titulo),
    }


class SondaSoft404Mixin:
    """Metodos del spider para las sondas. Estado en `self._sondas_*`."""

    def _iniciar_sondas(self, job) -> None:
        cb = (self.job_config.get("crawl_behavior") or {})
        self._sondas_max: int = int(cb.get("soft404_sondas", SONDAS_POR_DEFECTO) or 0)
        previo = getattr(job, "soft404", None) or {}
        # Al reanudar no se repiten: la lista viene de la ejecucion anterior.
        self._sondas: list[dict] = list(previo.get("sondas") or []) if isinstance(previo, dict) else []
        self._sondas_claves: set[str] = {s.get("clave") for s in self._sondas if s.get("clave")}

    def _sonda_request(self, url_modelo: str, clave: str, *,
                       url_exacta: str | None = None) -> scrapy.Request | None:
        if getattr(self, "_sondas_max", 0) <= 0:
            return None
        if clave in self._sondas_claves or len(self._sondas_claves) >= self._sondas_max:
            return None
        url = url_exacta or mangle_path(url_modelo, token_sonda(self.job_id, clave))
        if not self._should_follow(url):
            return None
        self._sondas_claves.add(clave)
        meta: dict[str, Any] = {
            "depth": 0, "dont_redirect": True, "sonda_soft404": clave,
            "handle_httpstatus_all": True,
        }
        if getattr(self, "render_js", False):
            meta.update(self._playwright_meta())
        return scrapy.Request(url, callback=self.parse_sonda, errback=self.sonda_error,
                              meta=meta, dont_filter=True, priority=10)

    def _sondas_de_semillas(self) -> Generator[scrapy.Request, None, None]:
        """Una por host de las semillas: la pagina de error generica."""
        vistos: set[str] = set()
        for semilla in self.seed_urls:
            partes = urlparse(semilla)
            host = (partes.hostname or "").lower()
            if not host or host in vistos:
                continue
            vistos.add(host)
            req = self._sonda_request(f"{partes.scheme or 'https'}://{partes.netloc}/",
                                      f"{host}|raiz")
            if req is not None:
                yield req

    def _sonda_de_plantilla(self, url: str) -> Generator[scrapy.Request, None, None]:
        """La de la raiz del host de `url` si falta, y una por plantilla nueva.

        La de la raiz no basta con pedirla para los hosts de las SEMILLAS: si
        la semilla redirige a otro host (sin www -> www), las paginas del censo
        viven en un host sin sonda y no se comparaban con ninguna huella. Por
        eso cada host interno con paginas pide la suya la primera vez que se ve.
        """
        if getattr(self, "_sondas_max", 0) <= 0 or len(self._sondas_claves) >= self._sondas_max:
            return
        partes = urlparse(url)
        host = (partes.hostname or "").lower()
        if host:
            req = self._sonda_request(f"{partes.scheme or 'https'}://{partes.netloc}/",
                                      f"{host}|raiz")
            if req is not None:
                yield req
        tramos = [t for t in (partes.path or "").split("/") if t]
        if len(tramos) < 2:
            return  # la de un tramo es la de la raiz
        req = self._sonda_request(url, f"{host}|{firma_de_ruta(partes.path)}")
        if req is not None:
            yield req

    def _repetir_sonda_en_destino(self, request, destino: str) -> scrapy.Request | None:
        """Si la sonda solo ha cambiado de esquema o de host, pedirla alli.

        `http://x.com/zz…` -> `https://x.com/zz…` (o a `www.`) es la
        canonicalizacion del sitio, no la respuesta a una URL que no existe: la
        pagina de error esta al otro lado. Se repite UNA vez, con la clave del
        host de destino (y con la misma si el host es el mismo: la que redirigio
        queda anotada como tal y no deja huella).
        """
        if request.meta.get("sonda_repetida"):
            return None
        origen, dest = urlsplit(request.url), urlsplit(destino)
        if dest.scheme not in ("http", "https") or (dest.path or "/") != (origen.path or "/"):
            return None
        if (dest.scheme, dest.netloc) == (origen.scheme, origen.netloc):
            return None
        clave = request.meta.get("sonda_soft404") or ""
        _, _, plantilla = clave.partition("|")
        nueva = f"{(dest.hostname or '').lower()}|{plantilla or 'raiz'}"
        if nueva == clave:
            self._sondas_claves.discard(clave)  # la misma clave, ya gastada
        req = self._sonda_request(destino, nueva,
                                  url_exacta=dest._replace(query="", fragment="").geturl())
        if req is not None:
            req.meta["sonda_repetida"] = True
        return req

    def _anotar_sonda(self, fila: dict) -> None:
        self._sondas.append(fila)
        try:
            from shared.database import SessionLocal
            from shared.models import Job

            sesion = SessionLocal()
            try:
                sesion.query(Job).filter(Job.id == self.job_id).update(
                    {"soft404": {"sondas": self._sondas}}, synchronize_session=False)
                sesion.commit()
            finally:
                sesion.close()
        except Exception as exc:  # noqa: BLE001 -- una sonda no tumba un rastreo
            logger.warning("No se pudo guardar la sonda de soft 404: %s", exc)

    def _fila_sonda(self, request) -> dict:
        clave = request.meta.get("sonda_soft404") or ""
        host, _, plantilla = clave.partition("|")
        return {
            "clave": clave, "host": host,
            "plantilla": None if plantilla == "raiz" else plantilla,
            "url": request.url, "origen": "rastreo",
            "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "render_js": bool(getattr(self, "render_js", False)),
        }

    def parse_sonda(self, response: Response) -> Generator:
        fila = self._fila_sonda(response.request)
        fila["status"] = response.status
        destino = None
        if 300 <= response.status < 400:
            loc = response.headers.get(b"Location")
            destino = response.urljoin(loc.decode("latin-1")) if loc else None
        elif response.url != response.request.url:
            destino = response.url  # con render, Chromium sigue el salto solo
        fila["redirige_a"] = destino
        try:
            from shared.respuestas import clasificar_respuesta

            cabeceras = {
                k.decode("latin-1").lower(): ", ".join(
                    v.decode("latin-1", "replace") for v in (vs or []))
                for k, vs in response.headers.items()
            }
            fila["response_class"], _ = clasificar_respuesta(
                response.status, cabeceras, response.body,
                content_type=cabeceras.get("content-type"), url=response.url,
                renderizada=bool(response.meta.get("playwright")))
        except Exception:  # noqa: BLE001
            fila["response_class"] = None
        if destino is None and isinstance(response, HtmlResponse):
            try:
                fila.update(huella_de_respuesta(
                    response.selector, response.url,
                    extraction=self.job_config.get("extraction") or {}))
            except Exception as exc:  # noqa: BLE001
                fila["error"] = f"extraccion: {exc}"[:200]
        logger.info("Sonda soft 404 %s -> %s%s", fila["url"], response.status,
                    f" -> {destino}" if destino else "")
        self._anotar_sonda(fila)
        if destino:
            req = self._repetir_sonda_en_destino(response.request, destino)
            if req is not None:
                yield req

    def sonda_error(self, failure) -> None:
        fila = self._fila_sonda(failure.request)
        fila["status"] = None
        fila["error"] = (
            "bloqueada_por_robots" if failure.check(scrapy.exceptions.IgnoreRequest)
            else f"{type(failure.value).__name__}: {failure.value}"[:200]
        )
        self._anotar_sonda(fila)
