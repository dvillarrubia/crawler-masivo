"""Mide si la espera de render se traga las paginas que montan su contenido
con un XHR, comparando la espera ACTUAL con la anterior (solo DOM).

Dos modos:

    # 1. Laboratorio: pagina sintetica cuyo XHR responde con N ms de retraso.
    python scripts/prueba_espera_render.py

    # 2. Paginas reales: mismo contraste sobre URLs de verdad.
    python scripts/prueba_espera_render.py https://ejemplo.com/una https://...

Lo que busca: filas donde la espera vieja saca MENOS enlaces / bloques de
datos estructurados / palabras que la nueva. Eso es contenido que el rastreo
estaba perdiendo en silencio — la pagina se guarda con 200, titulo y texto, y
nada avisa de que le falta un modulo entero.

Se ejecuta dentro del contenedor del crawler (necesita Playwright):

    docker compose exec -T crawler python scripts/prueba_espera_render.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time


def _preparar_rutas() -> None:
    for base in (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "/app"):
        if os.path.isdir(os.path.join(base, "shared")) and base not in sys.path:
            sys.path.insert(0, base)
        crawler_dir = os.path.join(base, "crawler")
        if os.path.isdir(os.path.join(crawler_dir, "seo_crawler")) and crawler_dir not in sys.path:
            sys.path.insert(0, crawler_dir)


_preparar_rutas()

from seo_crawler.render import _JS_CONTAR_PETICIONES  # noqa: E402
from seo_crawler.spiders.seo_spider import (  # noqa: E402
    _BOILERPLATE_REMOVAL_JS,
    _JS_ESPERAR_DOM_QUIETO as ESPERA_ACTUAL,
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# La de antes de septiembre de 2026: solo miraba mutaciones del DOM, asi que
# resolvia mientras la peticion seguia en vuelo (el DOM esta quieto justo
# entonces, por definicion).
ESPERA_SOLO_DOM = """
() => new Promise((resolve) => {
    const TOPE = 2000, QUIETO = 400;
    let t = null;
    const obs = new MutationObserver(() => reinicia());
    const fin = () => { clearTimeout(t); clearTimeout(tope);
        try { obs.disconnect(); } catch (_) {} resolve(); };
    const reinicia = () => { clearTimeout(t); t = setTimeout(fin, QUIETO); };
    const tope = setTimeout(fin, TOPE);
    try { obs.observe(document, { childList: true, subtree: true }); }
    catch (_) { fin(); return; }
    reinicia();
})
"""

HTML_LAB = """<!doctype html><html><head><title>lab</title></head><body>
<main><h1>hola</h1><p>uno dos tres</p><div id="destino"></div></main>
<script>
fetch('/dato.json').then(r => r.json()).then(d => {
  document.getElementById('destino').innerHTML =
    d.items.map(i => '<a href="/x/'+i+'">enlace '+i+'</a>').join(' ');
});
</script></body></html>"""

MODOS = (("solo-DOM (antes)", ESPERA_SOLO_DOM, False), ("actual", ESPERA_ACTUAL, True))


def _mide(html: str, url: str, host: str) -> tuple[int, int, int, int, int]:
    from parsel import Selector

    from seo_crawler.extractors import (
        extract_headings,
        extract_links,
        extract_resources,
        extract_structured_data,
        extract_word_count,
    )

    sel = Selector(text=html)
    try:
        sd = len(extract_structured_data(html, url))
    except Exception:
        sd = -1
    return (
        len(extract_links(sel, url, {host})),
        len(extract_headings(sel)),
        len(extract_resources(sel, url)),
        sd,
        extract_word_count(sel),
    )


async def _laboratorio(navegador) -> None:
    print("\n== laboratorio: el XHR que pinta los enlaces responde con retraso")
    print("%-8s %-18s %7s %9s" % ("retraso", "espera", "ms", "enlaces"))
    for retraso in (200, 700, 1200, 2500):
        for nombre, js, con_contador in MODOS:
            ctx = await navegador.new_context()
            pagina = await ctx.new_page()
            if con_contador:
                await pagina.add_init_script(_JS_CONTAR_PETICIONES)

            async def json_tardon(route):
                await asyncio.sleep(retraso / 1000)
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='{"items":[1,2,3,4,5,6,7,8,9,10]}',
                )

            await pagina.route("**/dato.json", json_tardon)
            await pagina.route(
                "**/lab",
                lambda r: asyncio.ensure_future(
                    r.fulfill(status=200, content_type="text/html", body=HTML_LAB)
                ),
            )
            await pagina.goto("http://lab.local/lab", wait_until="domcontentloaded")
            t0 = time.monotonic()
            await pagina.evaluate(js)
            ms = int((time.monotonic() - t0) * 1000)
            enlaces = len(await pagina.query_selector_all("a"))
            aviso = "  <-- perdidos" if enlaces == 0 else ""
            print("%-8d %-18s %7d %9d%s" % (retraso, nombre, ms, enlaces, aviso))
            await ctx.close()


async def _reales(navegador, urls: list[str]) -> None:
    from urllib.parse import urlparse

    for url in urls:
        host = urlparse(url).hostname
        print("\n==", url)
        print(
            "%-18s %7s %8s %6s %9s %4s %9s"
            % ("espera", "ms", "enlaces", "h-tot", "recursos", "sd", "palabras")
        )
        for nombre, js, con_contador in MODOS:
            ctx = await navegador.new_context(user_agent=UA)
            pagina = await ctx.new_page()
            if con_contador:
                await pagina.add_init_script(_JS_CONTAR_PETICIONES)
            try:
                await pagina.goto(url, wait_until="domcontentloaded", timeout=45000)
                t0 = time.monotonic()
                await pagina.evaluate(js)
                await pagina.evaluate(_BOILERPLATE_REMOVAL_JS)
                ms = int((time.monotonic() - t0) * 1000)
                html = await pagina.content()
            except Exception as exc:
                print("%-18s  error: %s" % (nombre, exc))
                await ctx.close()
                continue
            await ctx.close()
            print("%-18s %7d %8d %6d %9d %4d %9d" % ((nombre, ms) + _mide(html, url, host)))


async def _main() -> None:
    from playwright.async_api import async_playwright

    urls = sys.argv[1:]
    async with async_playwright() as p:
        navegador = await p.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            if urls:
                await _reales(navegador, urls)
            else:
                await _laboratorio(navegador)
        finally:
            await navegador.close()


if __name__ == "__main__":
    asyncio.run(_main())
