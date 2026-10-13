"""Recorre la interfaz y guarda una captura (y el texto visible) de cada vista.

Sirve para comparar antes/despues de cada fase de docs/AUDITORIA_UX.md.
Corre dentro de la imagen del crawler, que ya trae Playwright y Chromium:

    docker run --rm -v "$PWD/docs/ux":/s -v /tmp/ux:/out crawler-masivo-crawler \
        python /s/capturas.py http://host.docker.internal:8000 <job_id> [<cliente_slug>]

Conduce la app llamando a los metodos de Alpine, no haciendo clic: asi no se
rompe cada vez que cambia un boton de sitio. Si una fase renombra metodos
(p. ej. al meter rutas), actualizar aqui los nombres.
"""
from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://host.docker.internal:8000"
JOB = sys.argv[2]
CLIENTE = sys.argv[3] if len(sys.argv) > 3 else None
OUT = "/out"

PESTANAS = ["issues", "links", "insights", "semantic", "diff", "enriquecido", "cleaning"]


async def main() -> None:
    async with async_playwright() as p:
        nav = await p.chromium.launch()
        pg = await nav.new_page(viewport={"width": 1440, "height": 900})
        errores: list[str] = []
        pg.on("console", lambda m: errores.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: errores.append(f"pageerror: {e}"))

        async def vista(js: str, nombre: str, espera: int = 2500, entera: bool = True) -> None:
            try:
                await pg.evaluate(f"(async()=>{{const a=Alpine.$data(document.body); {js} }})()")
            except Exception as e:  # una vista que falla no tapa las demas
                errores.append(f"[{nombre}] {e}")
            await pg.wait_for_timeout(espera)
            await pg.screenshot(path=f"{OUT}/{nombre}.png", full_page=entera)
            alto = await pg.evaluate("document.documentElement.scrollHeight")
            texto = await pg.evaluate("document.body.innerText")
            with open(f"{OUT}/{nombre}.txt", "w") as f:
                f.write(f"# alto de pagina: {alto}px\n{texto[:20000]}")

        await pg.goto(BASE + "/")
        await vista("", "01-portada", 1500)
        await vista(f"await a.openJob('{JOB}')", "02-ficha", 4000)
        for t in PESTANAS:
            await vista(f"await a.switchTab('{t}')", f"03-pestana-{t}", 3500)
        await vista(
            f"const r = await fetch('/api/jobs/{JOB}/urls?page_size=1').then(r => r.json()); await a.openUrlDetail(r.items[0].id)",
            "04-ficha-url", 3000)
        await vista(
            f"await a.openJob('{JOB}'); await new Promise(r=>setTimeout(r,1500)); a.openExplorer()",
            "05-explorador", 4000, False)
        await vista("await a.verClientes()", "06-clientes", 2000)
        if CLIENTE:
            await vista(f"await a.abrirCliente(a.clientes.find(c=>c.slug==='{CLIENTE}').id)", "07-cliente", 2500)
        await vista("await a.verProgramados()", "08-programados", 2000)
        await vista("await a.backToJobs(); await a.openCreate()", "09-nuevo-rastreo", 2000, False)
        await pg.set_viewport_size({"width": 390, "height": 844})
        await vista("await a.backToJobs()", "10-movil-portada", 1500)

        # Rutas (#/...): una recarga en cada una tiene que llevar a la misma vista.
        await pg.set_viewport_size({"width": 1440, "height": 900})
        for nombre, ruta in [("11-ruta-problemas", f"#/rastreos/{JOB}/problemas?gravedad=error"),
                             ("12-ruta-explorador", f"#/rastreos/{JOB}/explorador")]:
            await pg.goto(BASE + "/" + ruta)
            await pg.reload()
            await vista("", nombre, 3500, False)
            with open(f"{OUT}/{nombre}.txt", "a") as f:
                f.write("\n# url final: " + pg.url)

        with open(f"{OUT}/errores.txt", "w") as f:
            f.write("\n".join(errores))
        await nav.close()


asyncio.run(main())
