"""La interfaz a 390 px de ancho (revision de UX del 25-oct, 3.2; V41 y
UX-22), EJECUTANDO `frontend/index.html` con Alpine en Chromium y una API de
mentira con nombres y URLs largos a proposito.

Lo que se sujeta:

- la PAGINA nunca desborda a lo ancho (`document.documentElement.scrollWidth`
  <= 390) en las vistas principales: portada de clientes, todos los rastreos,
  historial del cliente, Resumen del censo y su pestaña URLs. Las tablas
  tecnicas pueden tener scroll, pero dentro de su contenedor;
- las listas (clientes, rastreos, evolucion) son tarjetas: sin cabecera de
  tabla visible y con el estado, los errores y las alertas a la vista;
- en la pestaña URLs la barra lateral no se ve y sus entradas estan en un
  desplegable, con su cifra; a 1440 px es al reves, y la busqueda va primero;
- la tarjeta «Páginas HTML» y la entrada de la barra que abre dicen la misma
  cifra (V30).

Necesita Chromium y poder bajar Alpine de su CDN: si no, se salta.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("playwright")

RAIZ = Path(__file__).resolve().parent.parent
FRONT = RAIZ / "frontend"
BASE = "http://prueba.local"
CLI = "11111111-1111-1111-1111-111111111111"
JOB = "22222222-2222-2222-2222-222222222222"
LARGO = "lopesan.com (semanal) con un nombre larguisimo para ver si la tarjeta lo parte bien"
URL_LARGA = "https://www.ejemplo-de-un-dominio-bastante-largo.com/blog/una-ruta-muy-larga-que-no-cabe-en-un-movil-de-ninguna-manera/"


def _job(i: int, **extra) -> dict:
    return {
        "id": JOB if i == 0 else f"22222222-2222-2222-2222-00000000000{i}",
        "name": f"{LARGO} · 2026-10-2{i}", "client_id": "ejemplo", "cliente_id": CLI,
        "cliente_nombre": "Cliente de ejemplo con nombre largo", "profile_id": None,
        "status": "completed" if i else "completed", "finish_reason": "finished",
        "crawler_version": "abc1234", "analisis_version": "abc1234", "tipo": None,
        "tipo_efectivo": "censo" if i % 3 else "prueba", "seeds": [URL_LARGA],
        "config": {"max_depth": 10}, "comparacion": None, "gsc_resumen": None,
        "total_urls_discovered": 5578, "total_urls_crawled": 5578, "total_urls_failed": 3,
        "created_at": "2026-10-21T01:00:00Z", "started_at": "2026-10-21T01:00:00Z",
        "completed_at": "2026-10-21T03:18:00Z", **extra,
    }


def _url(i: int) -> dict:
    return {
        "id": i + 1, "job_id": JOB, "url": URL_LARGA + f"pagina-{i}/", "host": "www.ejemplo.com",
        "path": f"/blog/pagina-{i}/", "is_internal": True, "status_code": 200, "status_group": "2xx",
        "resource_type": "html", "is_html": True, "indexable": True, "inlinks_count": 12,
        "outlinks_count": 40, "title": "Un titulo de pagina bastante largo " * 3, "title_len": 99,
        "meta_description": "Una descripcion larga " * 6, "meta_description_len": 130,
        "word_count": 800, "click_depth": 3, "content_type": "text/html", "content_length": 51234,
        "response_time_ms": 230.0, "pagerank_score": 12.5, "canonical_href": URL_LARGA,
    }


SERIE = [
    {"id": _job(i)["id"], "name": _job(i)["name"], "status": "completed", "tipo": "censo",
     "created_at": f"2026-10-2{i}T01:00:00Z", "urls": 5578, "paginas_html": 3731, "indexables": 3216,
     "errores": 85 - i, "avisos": 4877, "alertas": i, "alertas_criticas": 0,
     "comparado_con": _job(i + 1)["id"] if i < 3 else None}
    for i in range(4)
]

RESPUESTAS = {
    "sesion": {"requerida": False, "configurada": False, "autenticado": True},
    "clients": [{"id": CLI, "nombre": "Cliente de ejemplo con nombre largo", "slug": "ejemplo",
                 "dominios": [], "integraciones": [{"tipo": "gsc", "configurada": True}],
                 "n_perfiles": 1, "n_jobs": 4, "created_at": "2026-10-01T00:00:00Z"}],
    "clients/panel": {"rastreos_sin_cliente": 1, "clientes": [
        {"id": CLI, "nombre": "Cliente de ejemplo con nombre largo", "slug": "ejemplo", "n_rastreos": 4,
         "ultimo": {"id": JOB, "name": LARGO, "created_at": "2026-10-21T01:00:00Z", "paginas_html": 3731,
                    "indexables": 3216, "errores": 85, "urls": 5578},
         "anterior": None, "evolucion": None, "incidentes_vivos": 2},
        {"id": "33333333-3333-3333-3333-333333333333", "nombre": "Otro", "slug": "otro",
         "n_rastreos": 0, "ultimo": None, "anterior": None, "evolucion": None, "incidentes_vivos": 0}]},
    "jobs": {"items": [_job(i) for i in range(4)], "total": 4, "page": 1, "page_size": 20, "pages": 1},
    "almacenamiento": {"total_bytes": 9_000_000_000, "censo_bytes": 1, "tablas": [], "archivo_bytes": 0,
                       "archivo_ficheros": 0, "capturas_bytes": 0, "nota": ""},
    f"clients/{CLI}": {"id": CLI, "nombre": "Cliente de ejemplo con nombre largo", "slug": "ejemplo",
                       "dominios": [], "zona_horaria": "Europe/Madrid", "config_defecto": {},
                       "integraciones": [], "n_perfiles": 0, "n_jobs": 4},
    f"clients/{CLI}/profiles": [],
    f"clients/{CLI}/serie": SERIE,
    f"clients/{CLI}/incidentes": [],
    f"clients/{CLI}/vistas": [],
    f"clients/{CLI}/trafico": [],
    "programaciones": [],
    f"jobs/{JOB}": _job(0),
    f"jobs/{JOB}/stats": {
        "job_id": JOB, "total_urls": 5578, "total_urls_crawled": 5578, "total_urls_failed": 3,
        "urls_by_status_group": [{"status_group": "2xx", "count": 3922}, {"status_group": "3xx", "count": 1615},
                                 {"status_group": "4xx", "count": 41}],
        "issues_by_type": [{"issue_type": "title_too_long", "severity": "warning", "count": 300}],
        "top_hosts": [{"host": "www.ejemplo-de-un-dominio-bastante-largo.com", "count": 5578}],
        "urls_by_resource_type": [{"resource_type": "html", "count": 3772}, {"resource_type": "redirect", "count": 1615},
                                  {"resource_type": "pdf", "count": 188}],
        "internal_count": 5578, "external_count": 0, "paginas_html": 3731, "indexables": 3216,
        "no_indexables": 515, "click_depth_calculada": True, "paginas_con_error": 70},
    f"jobs/{JOB}/issues/resumen": [
        {"issue_type": "title_too_long", "severity": "warning", "incidencias": 300, "paginas": 300,
         "ejemplos": [], "importancia": 1.0, "prioridad": 1.0}],
    f"jobs/{JOB}/segmentos": {"reglas": [], "segmentos": [], "al_dia": True, "sin_segmentar": 0},
}


def _respuesta_api(ruta: str, query: str):
    if ruta == f"jobs/{JOB}/urls":
        q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if q.get("page_size") == "1":
            total = 3731 if q.get("status_group") == "2xx" and q.get("resource_type") == "html" else 7
            return {"items": [], "total": total, "page": 1, "page_size": 1, "pages": total}
        return {"items": [_url(i) for i in range(30)], "total": 5578, "page": 1, "page_size": 100, "pages": 56}
    if ruta in RESPUESTAS:
        return RESPUESTAS[ruta]
    return None


ESTUBS = {
    "lucide": "window.lucide = { createIcons() {} };",
    "plotly": "window.Plotly = { newPlot() {}, react() {}, purge() {}, Plots: { resize() {} } };",
    "marked": "window.marked = { parse: (s) => String(s || '') };",
}


def _medir(rutas: list[str], ancho: int, extra: str = "() => null"):
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            ctx = await nav.new_context(viewport={"width": ancho, "height": 844})
            pagina = await ctx.new_page()
            errores: list[str] = []
            pagina.on("pageerror", lambda e: errores.append(str(e)))
            alpine_ok = {"v": False}

            async def servir(route):
                url = route.request.url
                for clave, js in ESTUBS.items():
                    if clave in url and "/api/" not in url:
                        return await route.fulfill(body=js, content_type="application/javascript")
                if "fonts.g" in url:
                    return await route.fulfill(body="", content_type="text/css")
                if "alpinejs" in url:
                    try:
                        r = await route.fetch()
                        alpine_ok["v"] = r.ok
                        return await route.fulfill(response=r)
                    except Exception:  # pragma: no cover
                        return await route.abort()
                if not url.startswith(BASE):
                    return await route.abort()
                camino, _, query = url[len(BASE):].partition("?")
                camino = camino.split("#")[0]
                if camino.startswith("/api/"):
                    datos = _respuesta_api(camino[5:], query)
                    if datos is None:
                        return await route.fulfill(status=404, body='{"detail":"no existe"}', content_type="application/json")
                    return await route.fulfill(body=json.dumps(datos), content_type="application/json")
                f = "index.html" if camino in ("/", "") else camino.lstrip("/").removeprefix("static/")
                local = FRONT / f
                if local.is_file():
                    tipo = {"html": "text/html", "js": "application/javascript", "css": "text/css"}.get(f.rsplit(".", 1)[-1], "text/plain")
                    return await route.fulfill(body=local.read_bytes(), content_type=tipo)
                return await route.fulfill(status=404, body="")

            await pagina.route("**/*", servir)
            await pagina.goto(BASE + "/")
            try:
                await pagina.wait_for_function("window.Alpine && document.querySelector('[x-data]')._x_dataStack", timeout=20000)
            except Exception:
                alpine_ok["v"] = False
            if not alpine_ok["v"]:
                await nav.close()
                pytest.skip("sin acceso a Alpine en su CDN")
            await pagina.wait_for_timeout(1000)
            salida = {}
            for h in rutas:
                await pagina.evaluate("h => { location.hash = h }", h)
                await pagina.wait_for_timeout(1500)
                salida[h] = {
                    "ancho": await pagina.evaluate("document.documentElement.scrollWidth"),
                    "extra": await pagina.evaluate(extra),
                }
            await nav.close()
            return salida, errores

    return asyncio.run(run())


RUTAS = ["#/clientes", "#/rastreos", f"#/clientes/{CLI}/historial", f"#/rastreos/{JOB}", f"#/rastreos/{JOB}/urls"]

VISIBLES = """() => {
  const vis = sel => [...document.querySelectorAll(sel)].some(e => e.offsetParent !== null && getComputedStyle(e).display !== 'none');
  const t = sel => [...document.querySelectorAll(sel)].map(e => e.textContent.replace(/\\s+/g, ' ').trim());
  return {
    cabeceras: ['[data-testid=panel-cartera] thead', '[data-testid=tabla-rastreos] thead', '[data-testid=tabla-serie] thead'].filter(vis),
    etiquetas: t('.con-tarjetas td[data-label]').length ? [...new Set([...document.querySelectorAll('.con-tarjetas td[data-label]')]
      .filter(e => e.offsetParent !== null && getComputedStyle(e).display !== 'none').map(e => e.dataset.label))] : [],
    lateral: vis('.exp-sidebar'),
    desplegable: vis('[data-testid=exp-vista-movil]'),
    opciones: t('[data-testid=exp-vista-movil] option'),
    barra: t('.exp-tab'),
    primero: (document.querySelector('.exp-toolbar > :not([style*="display: none"])') || {}).className || '',
    urlFija: !!document.querySelector('.exp-table.url-fija'),
    tarjetaHtml: t('.stat-card .stat-value')[0] || '',
  };
}"""


def test_a_390_la_pagina_no_desborda_y_las_listas_son_tarjetas():
    salida, errores = _medir(RUTAS, 390, VISIBLES)
    assert errores == []
    for ruta, r in salida.items():
        assert r["ancho"] <= 390, f"{ruta} mide {r['ancho']} px de ancho"
    for ruta in RUTAS[:3]:
        assert salida[ruta]["extra"]["cabeceras"] == [], ruta
    assert {"Errores", "Alertas", "Último censo"} <= set(salida["#/clientes"]["extra"]["etiquetas"])
    assert {"Estado", "Rastreadas", "Duración"} <= set(salida["#/rastreos"]["extra"]["etiquetas"])
    assert "Semillas" not in salida["#/rastreos"]["extra"]["etiquetas"]
    assert {"Errores", "Alertas"} <= set(salida[f"#/clientes/{CLI}/historial"]["extra"]["etiquetas"])
    urls = salida[f"#/rastreos/{JOB}/urls"]["extra"]
    assert not urls["lateral"] and urls["desplegable"]
    # Las entradas de la barra, con su cifra; un cero es «0», no un hueco.
    assert "Páginas HTML · 3.731" in urls["opciones"]
    assert "Todo el HTML · 3.772" in urls["opciones"]
    assert "5xx · 0" in urls["opciones"] and "JavaScript · 0" in urls["opciones"]


def test_a_1440_barra_lateral_busqueda_primero_y_la_misma_cifra_que_la_tarjeta():
    salida, errores = _medir([f"#/rastreos/{JOB}", f"#/rastreos/{JOB}/urls"], 1440, VISIBLES)
    assert errores == []
    ficha = salida[f"#/rastreos/{JOB}"]["extra"]
    urls = salida[f"#/rastreos/{JOB}/urls"]["extra"]
    assert urls["lateral"] and not urls["desplegable"]
    assert "exp-search-wrap" in urls["primero"]
    assert urls["urlFija"]
    # V30: la tarjeta y la entrada que abre, con el mismo nombre y la misma cifra.
    assert ficha["tarjetaHtml"] == "3.731"
    barra = {re.sub(r"\s*[\d.]+$", "", b): b for b in urls["barra"]}
    assert barra["Páginas HTML"].endswith("3.731")
    assert barra["Todo el HTML"].endswith("3.772")
    # Contador en todas las entradas, tambien las vacias y los ambitos.
    sin_cifra = [b for b in urls["barra"] if not re.search(r"\d$", b)]
    assert set(sin_cifra) <= {"Externas", "Guardar esta vista"}, sin_cifra


def test_columnas_por_defecto_deciden_y_los_clics_solo_con_search_console():
    src = "\n".join((FRONT / n).read_text(encoding="utf-8") for n in ("incidencias.js", "app.js"))

    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            try:
                nav = await p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"sin Chromium: {exc}")
            pg = await nav.new_page()
            await pg.set_content("<html><body></body></html>")
            await pg.add_script_tag(content="window.lucide = { createIcons() {} };")
            await pg.add_script_tag(content=src)
            r = await pg.evaluate("""() => ({
                sin: columnasPorDefecto(EXP_DEFAULT_COLUMNS, { gsc_resumen: null }).slice(0, 5),
                con: columnasPorDefecto(EXP_DEFAULT_COLUMNS, { gsc_resumen: { desde: 'x' } }).slice(0, 6),
                trafico: columnasPorDefecto(EXP_TABS.find(t => t.key === 'a_trafico').columns, { gsc_resumen: {} })
                  .filter(c => c === 'gsc_clics').length,
            })""")
            await nav.close()
            return r

    r = asyncio.run(run())
    assert r["sin"] == ["url", "status_code", "indexable", "inlinks_count", "title"]
    assert r["con"] == ["url", "status_code", "indexable", "inlinks_count", "gsc_clics", "title"]
    assert r["trafico"] == 1
