"""El markdown de una pagina rastreada se pinta saneado (decision 206).

La pestana «Contenido» de la ficha de URL pinta
`page_content.content_markdown` con `renderMarkdown()` por `x-html`. Ese
markdown lo controla el sitio que el crawler visita, y marked@15 ya no sanea:
sin DOMPurify, su HTML se ejecutaba en el navegador del analista (XSS
almacenado).

Este test EJECUTA el `renderMarkdown` real de `frontend/app.js` en Chromium,
con la misma `marked@15` y la `dompurify` que carga `index.html`, y comprueba
que:

- un `<img onerror=...>` incrustado pierde el `onerror` (no dispara);
- un enlace `javascript:...` pierde el `href`;
- el markdown normal (encabezados, enlaces http) sigue pintandose.

Lee el fuente a proposito (saca de ahi la version de marked/dompurify y la
propia funcion), pero la ejecuta: un test que solo leyera el fuente no probaria
que el saneo de verdad corta la carga (regla: un test que lee el fuente no
sustituye a ejecutar). Necesita Chromium y bajar marked y dompurify de su CDN:
si no, se salta.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

pytest.importorskip("playwright")

RAIZ = Path(__file__).resolve().parent.parent
FRONT = RAIZ / "frontend"


def _fuente_funcion() -> str:
    """El metodo `renderMarkdown(md) { ... }` tal cual esta en app.js."""
    app = (FRONT / "app.js").read_text(encoding="utf-8")
    m = re.search(r"renderMarkdown\(md\) \{.*?\n    \},", app, re.DOTALL)
    assert m, "no se encontro renderMarkdown en app.js"
    return m.group(0).rstrip(",")


def _urls_cdn() -> tuple[str, str]:
    """Las URLs de marked y dompurify que declara index.html (version fijada)."""
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    marked = re.search(r'src="(https://[^"]*marked@[^"]+)"', html)
    purify = re.search(r'src="(https://[^"]*dompurify/[^"]+purify[^"]+)"', html)
    assert marked, "index.html no carga marked"
    assert purify, "index.html no carga dompurify"
    return marked.group(1), purify.group(1)


PELIGROSO = '# hola\n\n<img src=x onerror="window.__pt_md=1">\n\n[c](javascript:window.__pt_mdlink=1)'
NORMAL = "# Titulo\n\nUn [enlace](https://ejemplo.com) normal."


def _render(entradas: list[str]) -> list[dict]:
    from playwright.async_api import async_playwright

    marked_url, purify_url = _urls_cdn()
    cuerpo = _fuente_funcion()

    async def run():
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            pagina = await nav.new_page()
            html = (
                "<!doctype html><html><head>"
                f'<script src="{marked_url}"></script>'
                f'<script src="{purify_url}"></script>'
                "</head><body></body></html>"
            )
            try:
                await pagina.set_content(html, wait_until="networkidle")
                cargados = await pagina.evaluate(
                    "() => typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined'"
                )
            except Exception as exc:  # pragma: no cover
                await nav.close()
                pytest.skip(f"sin acceso a marked/dompurify en su CDN: {exc}")
            if not cargados:
                await nav.close()
                pytest.skip("marked o dompurify no cargaron desde su CDN")

            await pagina.evaluate(f"() => {{ window.__obj = {{ {cuerpo} }}; }}")
            salida = []
            for md in entradas:
                await pagina.evaluate("() => { delete window.__pt_md; delete window.__pt_mdlink; }")
                res = await pagina.evaluate(
                    """(md) => {
                        const html = window.__obj.renderMarkdown(md);
                        const cont = document.createElement('div');
                        cont.innerHTML = html;
                        return {
                            html,
                            onerror: cont.querySelectorAll('[onerror]').length,
                            hrefs: [...cont.querySelectorAll('a[href]')].map(a => a.getAttribute('href')),
                            img: cont.querySelectorAll('img').length,
                            h1: cont.querySelectorAll('h1').length,
                        };
                    }""",
                    md,
                )
                # Dar margen por si algun onerror se hubiese colado.
                await pagina.wait_for_timeout(200)
                res["ejecuto_img"] = await pagina.evaluate("() => window.__pt_md === 1")
                res["ejecuto_link"] = await pagina.evaluate("() => window.__pt_mdlink === 1")
                salida.append(res)
            await nav.close()
            return salida

    return asyncio.run(run())


def test_el_markdown_malicioso_se_sanea():
    (peligroso, normal) = _render([PELIGROSO, NORMAL])

    # La carga no ejecuta ni sobrevive como atributo/enlace peligroso.
    assert peligroso["ejecuto_img"] is False
    assert peligroso["ejecuto_link"] is False
    assert peligroso["onerror"] == 0, peligroso["html"]
    assert all("javascript:" not in (h or "") for h in peligroso["hrefs"]), peligroso["hrefs"]

    # Pero el markdown normal se sigue pintando como HTML.
    assert normal["h1"] == 1, normal["html"]
    assert any((h or "").startswith("https://ejemplo.com") for h in normal["hrefs"]), normal["hrefs"]
