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
"""

from __future__ import annotations

import logging

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
    """Callback de ``PLAYWRIGHT_PAGE_INIT_CALLBACK``.

    Nunca puede tumbar la peticion: si la inyeccion falla, la espera de render
    se queda como estaba (solo DOM) y la pagina se rastrea igual.
    """
    try:
        await page.add_init_script(_JS_CONTAR_PETICIONES)
    except Exception as exc:  # pragma: no cover - depende del navegador
        logger.debug("No se pudo instalar el contador de peticiones: %s", exc)
