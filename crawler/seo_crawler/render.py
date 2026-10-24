"""Preparacion de la pagina de Playwright antes de navegar.

Instala un contador de peticiones XHR/fetch EN VUELO. Sin el, la espera de
render solo puede mirar el DOM, y mientras una peticion viaja el DOM esta
quieto por definicion: la pagina parecia terminada justo en el hueco en el
que todavia no habia llegado nada. Medido en un rastreo de 9.895 noticias,
6.555 se guardaron sin el modulo que monta el XHR (~20 enlaces y ~20% del
texto menos) y nada lo avisaba.

El script se inyecta con ``add_init_script``, asi que corre ANTES que el
codigo de la pagina y ve tambien la primera peticion, que es justo la que se
perdia.

Y pone en el contexto las cookies y credenciales del job, solo para los hosts
del sitio, y quita las que dejo la pagina anterior (decision 114).
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from .blocklist import _host_bloqueado

logger = logging.getLogger(__name__)

_JS_CONTAR_PETICIONES = """
(() => {
  try {
    window.__enVuelo = 0;
    const sube = () => { window.__enVuelo = (window.__enVuelo | 0) + 1; };
    const baja = () => { window.__enVuelo = Math.max(0, (window.__enVuelo | 0) - 1); };

    const fetchOriginal = window.fetch;
    if (typeof fetchOriginal === "function") {
      window.fetch = function (...args) {
        sube();
        let p;
        try { p = fetchOriginal.apply(this, args); }
        catch (e) { baja(); throw e; }
        return p.then(
          (r) => { baja(); return r; },
          (e) => { baja(); throw e; }
        );
      };
    }

    const enviarOriginal = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (...args) {
      sube();
      let bajado = false;
      const unaVez = () => { if (!bajado) { bajado = true; baja(); } };
      try { this.addEventListener("loadend", unaVez); } catch (_) {}
      try { return enviarOriginal.apply(this, args); }
      catch (e) { unaVez(); throw e; }
    };
  } catch (_) {}
})()
"""


async def preparar_pagina(page, request=None) -> None:
    """Callback de ``meta["playwright_page_init_callback"]`` (ver `_playwright_meta`).

    Nunca puede tumbar la peticion: si la inyeccion falla, la espera de render
    se queda como estaba (solo DOM) y la pagina se rastrea igual.
    """
    try:
        await page.add_init_script(_JS_CONTAR_PETICIONES)
    except Exception as exc:  # pragma: no cover - depende del navegador
        logger.debug("No se pudo instalar el contador de peticiones: %s", exc)
    await preparar_http(page, request)


def dominios_de_cookie(hosts, raices) -> list[str]:
    """Dominios a los que se atan las cookies configuradas en el navegador.

    Los mismos que reciben las credenciales sin render (decision 114): cada
    host de las semillas como cookie de host (sin punto) y, con
    ``crawl_subdomains``, cada dominio raiz con punto, que en Chromium es «este
    y sus subdominios».
    """
    return sorted({h for h in (hosts or ()) if h} | {"." + r for r in (raices or ()) if r})


async def preparar_http(page, request) -> None:
    """Cookies y credenciales del job en el contexto de Playwright (decision 114).

    Lo que pone ``HttpConfigMiddleware`` en ``meta["_http_render"]``:

    - Sin ``conservar``: se borran del contexto las cookies que no son las
      configuradas, para que la pagina se vea sin el estado que dejaron las
      anteriores (Googlebot no guarda cookies). Por nombre y dominio, no con
      ``clear_cookies()`` a secas: con 4 pestanas por contexto, otra que esta
      navegando se quedaria sin la cookie de un staging.
    - Las configuradas, con ``add_cookies`` y atadas a los hosts del sitio:
      Chromium ignora la cabecera ``Cookie`` que pone scrapy-playwright, asi
      que con render NO llegaban (medido), y una cookie del contexto no sale
      hacia otro dominio aunque la pagina redirija alli.
    - Las cabeceras propias y el Basic, por ``_ruta_con_credenciales``.

    Best-effort con el ``try`` cubriendo la funcion entera (decision 40): un
    fallo aqui se avisa y la pagina se rastrea sin ellas.
    """
    try:
        datos = (getattr(request, "meta", None) or {}).get("_http_render")
        if not datos:
            return
        contexto = page.context
        cookies = datos.get("cookies") or {}
        dominios = dominios_de_cookie(datos.get("hosts"), datos.get("raices"))
        if not datos.get("conservar"):
            for c in await contexto.cookies():
                if c.get("name") in cookies and c.get("domain") in dominios:
                    continue
                await contexto.clear_cookies(name=c["name"], domain=c["domain"])
        if cookies and dominios:
            await contexto.add_cookies([
                {"name": nombre, "value": valor, "domain": dominio, "path": "/",
                 "secure": bool(datos.get("solo_https"))}
                for nombre, valor in cookies.items() for dominio in dominios
            ])
        if datos.get("cabeceras"):
            await page.route("**/*", _ruta_con_credenciales(page, request, datos["cabeceras"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudieron preparar cookies/credenciales del job para %s: %s",
                       getattr(request, "url", "?"), exc)


def _ruta_con_credenciales(page, request, cabeceras: dict):
    """Manejador de ruta que descarga el documento con las cabeceras del job.

    Si se pusieran con ``route.continue_`` (lo que hace scrapy-playwright),
    Chromium las conservaria al seguir una redireccion, tambien a otro host: un
    staging que manda a un SSO le daba el Basic al SSO (medido con Chromium
    1194). Asi que el documento principal se pide con ``route.fetch`` sin
    seguir redirecciones, y si es un 3xx se le entrega a Chromium SIN
    ``Location`` (que no la siga) y el destino queda en
    ``meta["_location_render"]``: la redireccion la sigue el spider, como sin
    render (decision 27), y cada salto decide en el middleware si lleva
    credenciales. El resto de peticiones (recursos, iframes, navegaciones de la
    pagina) pasan al manejador de scrapy-playwright, sin credenciales.

    Contrapartida: ``route.fetch`` sale desde Playwright y no desde la pila de
    red de Chromium, con otra huella TLS. Solo se usa en los jobs con
    cabeceras propias o Basic y render, que son los staging.
    """
    objetivo = request.url.rstrip("/")
    estado = {"hecho": False}
    propias = {k.lower(): v for k, v in cabeceras.items()}

    async def _ruta(route, peticion) -> None:
        try:
            es_el_documento = (
                not estado["hecho"]
                and peticion.is_navigation_request()
                and peticion.frame == page.main_frame
                and peticion.url.rstrip("/") == objetivo
            )
        except Exception:  # noqa: BLE001
            es_el_documento = False
        if not es_el_documento:
            await route.fallback()
            return
        estado["hecho"] = True
        try:
            finales = {k.lower(): v for k, v in (await peticion.all_headers()).items()}
            # Como `use_scrapy_headers` en la navegacion: mandan las de Scrapy
            # (user-agent, Accept-Language...), menos la Cookie del jar de
            # Scrapy, que Chromium ignoraria; la del navegador ya va en
            # `finales` y solo trae cookies de este host.
            for k, v in request.headers.to_unicode_dict().items():
                if k.lower() != "cookie":
                    finales[k.lower()] = v
            finales.update(propias)
            respuesta = await route.fetch(headers=finales, max_redirects=0)
            location = respuesta.headers.get("location")
            if 300 <= respuesta.status < 400 and location:
                request.meta["_location_render"] = location
                await route.fulfill(
                    status=respuesta.status,
                    headers={k: v for k, v in respuesta.headers.items() if k.lower() != "location"},
                    body=b"",
                )
                return
            await route.fulfill(response=respuesta)
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo descargar %s con las credenciales del job: %s",
                           request.url, exc)
            try:
                await route.abort()
            except Exception:  # noqa: BLE001
                pass

    return _ruta


# ---------------------------------------------------------------------------
# Capturas de pantalla (decision 173)
# ---------------------------------------------------------------------------
# El rastreo con render no descarga imagenes ni fuentes (PLAYWRIGHT_ABORT_REQUEST:
# no cambian el HTML y cuestan la mitad del tiempo). Una captura sin ellas no
# se parece a la pagina, asi que la pestana de una captura las deja pasar. Los
# rastreadores de anuncios y analitica siguen fuera (`blocklist.py`): no
# pintan nada que importe y si cuestan.
TIPOS_QUE_PINTAN = frozenset({"image", "font"})


async def _ruta_que_pinta(route, peticion) -> None:
    """Deja pasar imagenes y fuentes que no son de un rastreador; lo demas,
    al manejador siguiente (las credenciales del job o scrapy-playwright)."""
    try:
        if peticion.resource_type in TIPOS_QUE_PINTAN and not _host_bloqueado(
                urlsplit(peticion.url).hostname or ""):
            await route.continue_()
            return
        await route.fallback()
    except Exception as exc:  # noqa: BLE001 -- la pagina ya se cerro, o la ruta se atendio
        logger.debug("Ruta de captura: %s", exc)


async def preparar_pagina_captura(page, request=None) -> None:
    """``preparar_pagina`` y, encima, la ruta que deja pasar imagenes y fuentes.

    Las rutas de Playwright se atienden de la ULTIMA registrada a la primera:
    esta va despues de la de scrapy-playwright (que aborta las imagenes) y de
    la de credenciales, asi que decide antes y les pasa lo que no es suyo.
    """
    await preparar_pagina(page, request)
    try:
        await page.route("**/*", _ruta_que_pinta)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Captura sin imagenes ni fuentes: %s", exc)


async def capturar_pantalla(page, opciones: dict | None = None):
    """``PageMethod`` que hace la captura y devuelve los bytes del JPEG, o None.

    La primera pantalla (sin `full_page`): es lo que se ve al abrir, y una
    pagina entera en movil son 20.000 px de alto. A escala CSS: en el movil
    (DPR 2,625) la imagen sale de 412x915 y no de 1082x2402, que ocupa siete
    veces mas y no ensena nada distinto. Nunca lanza: una captura que falla no
    puede costar la respuesta.
    """
    opciones = opciones or {}
    try:
        return await page.screenshot(
            type="jpeg", quality=int(opciones.get("calidad", 70)), full_page=False,
            scale="css", animations="disabled", caret="hide",
            timeout=int(opciones.get("timeout_ms", 15000)))
    except Exception as exc:  # noqa: BLE001
        logger.info("No se pudo hacer la captura de %s: %s", getattr(page, "url", "?"), exc)
        return None
