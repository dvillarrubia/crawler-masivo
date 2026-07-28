"""Compara HTML crudo vs renderizado por PLANTILLA, no por URL.

El contenido que se carga con JavaScript es una propiedad de la plantilla: si la
ficha de hotel monta media pagina en cliente, la monta en las 400 fichas. Sacar
una muestra por plantilla responde lo mismo que un rastreo completo con render y
cuesta minutos en vez de horas.

Uso (dentro del contenedor del crawler):

    python scripts/check_js_templates.py <job_id> [--muestras 3] [--espera 3500]

Toma las URLs de un rastreo YA hecho (sin JS), las clasifica por plantilla segun
su forma, y de cada grupo saca N al azar reproducible. Para cada una descarga el
HTML crudo y lo compara con el renderizado en Chromium:

  - enlaces internos que SOLO aparecen tras ejecutar JS  -> afecta al PageRank
  - palabras que solo aparecen tras ejecutar JS          -> afecta al contenido

Un grupo con enlaces solo-JS invalida el grafo de enlaces de ese tipo de pagina.
Un grupo con mucho texto solo-JS significa que el analisis de contenido sobre el
HTML crudo subestima esas paginas.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import os
import random
import re
import sys
import urllib.request
from collections import defaultdict

def _preparar_rutas() -> None:
    """Deja importables `shared` y `seo_crawler` desde donde sea que se ejecute.

    El script puede correr desde el repo (scripts/), desde la raiz de la app en
    el contenedor, o copiado suelto. En vez de asumir una jerarquia, se buscan
    los directorios que contienen los paquetes.
    """
    candidatos = [
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        os.getcwd(),
        "/app",
    ]
    for base in candidatos:
        if os.path.isdir(os.path.join(base, "shared")) and base not in sys.path:
            sys.path.insert(0, base)
        crawler_dir = os.path.join(base, "crawler")
        if os.path.isdir(os.path.join(crawler_dir, "seo_crawler")) and crawler_dir not in sys.path:
            sys.path.insert(0, crawler_dir)


_preparar_rutas()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Reglas de clasificacion, en orden: la primera que casa manda. Estan pensadas
# para sitios multiidioma con seccion de producto y blog; ajustar por proyecto.
REGLAS: list[tuple[str, str]] = [
    # Primero las URLs generadas por el CMS: no son plantillas de negocio y
    # mezcladas con las demas falsean la muestra. En Liferay, el Asset Publisher
    # expone el contenido de una pagina bajo URLs propias; si no se aislan
    # aparecen como "otras · N niveles" con las mismas cifras que la pagina que
    # replican, que es justo como se detecto este caso.
    ("cms · asset publisher", r"/-/asset_publisher/"),
    ("cms · otros portlets",  r"/-/[a-z_]+/"),
    ("home",              r"^/?$|^/[a-z]{2}/?$"),
    ("blog · post",       r"^/blog/.+/.+"),
    ("blog · categoria",  r"^/blog/[^/]+/?$"),
    ("blog · indice",     r"^/blog/?$"),
    ("ficha de producto", r"^/[a-z]{2}/(hoteles|hotels)/[^/]+/[^/]+/"),
    ("categoria nivel 2", r"^/[a-z]{2}/(hoteles|hotels)/[^/]+/[^/]+/?$"),
    ("categoria nivel 1", r"^/[a-z]{2}/(hoteles|hotels)/[^/]+/?$"),
    ("listado producto",  r"^/[a-z]{2}/(hoteles|hotels)/?$"),
    ("marca",             r"^/[a-z]{2}/[a-z0-9-]*(hotels|resorts|collection)[a-z0-9-]*/?$"),
    ("legal",             r"(privacidad|privacy|datenschutz|aviso-legal|cookies|terminos)"),
]


def clasificar(path: str) -> str:
    for nombre, patron in REGLAS:
        if re.search(patron, path, re.I):
            return nombre
    partes = [p for p in path.split("/") if p]
    return f"otras · {len(partes)} niveles"


def descargar_crudo(url: str, timeout: int = 40) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}
    )
    datos = urllib.request.urlopen(req, timeout=timeout).read()
    if datos[:2] == b"\x1f\x8b":
        datos = gzip.decompress(datos)
    return datos.decode("utf-8", "ignore")


def cargar_urls(job_id: str) -> list[tuple[str, str]]:
    """Devuelve (url, path) de las paginas HTML 200 del rastreo."""
    from shared.database import SessionLocal
    from shared.models import Url

    sesion = SessionLocal()
    try:
        filas = (
            sesion.query(Url.url, Url.path)
            .filter(
                Url.job_id == job_id,
                Url.status_code == 200,
                Url.is_html.is_(True),
                Url.is_internal.is_(True),
            )
            .all()
        )
        # Fuera las URLs con parametros: son variantes de la misma plantilla y
        # solo ensucian la muestra.
        return [(u, p or "/") for u, p in filas if "?" not in u]
    finally:
        sesion.close()


async def analizar(urls_por_plantilla, espera_ms: int, hosts: set[str]):
    from parsel import Selector
    from playwright.async_api import async_playwright

    from seo_crawler.extractors import extract_links, extract_word_count

    resultados = []
    async with async_playwright() as p:
        navegador = await p.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        for plantilla, urls in urls_por_plantilla:
            enl_solo_js = pal_crudo = pal_render = muestras_ok = 0
            for url in urls:
                try:
                    html_crudo = descargar_crudo(url)
                except Exception as exc:
                    print(f"    aviso: no se pudo descargar {url[:70]} ({exc})")
                    continue
                pagina = await (await navegador.new_context()).new_page()
                try:
                    await pagina.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await pagina.wait_for_timeout(espera_ms)
                    html_render = await pagina.content()
                except Exception as exc:
                    print(f"    aviso: no se pudo renderizar {url[:70]} ({exc})")
                    await pagina.close()
                    continue
                await pagina.close()

                sel_crudo = Selector(text=html_crudo)
                sel_render = Selector(text=html_render)
                enlaces_crudo = {
                    l["url"] for l in extract_links(sel_crudo, url, hosts) if l["is_internal"]
                }
                enlaces_render = {
                    l["url"] for l in extract_links(sel_render, url, hosts) if l["is_internal"]
                }
                enl_solo_js += len(enlaces_render - enlaces_crudo)
                pal_crudo += extract_word_count(sel_crudo)
                pal_render += extract_word_count(sel_render)
                muestras_ok += 1

            if muestras_ok:
                resultados.append(
                    {
                        "plantilla": plantilla,
                        "muestras": muestras_ok,
                        "enlaces_solo_js": enl_solo_js,
                        "palabras_crudo": pal_crudo // muestras_ok,
                        "palabras_render": pal_render // muestras_ok,
                    }
                )
        await navegador.close()
    return resultados


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job_id", help="UUID de un rastreo ya realizado")
    ap.add_argument("--muestras", type=int, default=3, help="URLs por plantilla (def. 3)")
    ap.add_argument("--espera", type=int, default=3500, help="ms de espera tras cargar (def. 3500)")
    ap.add_argument("--semilla", type=int, default=7, help="semilla del muestreo, para reproducibilidad")
    args = ap.parse_args()

    filas = cargar_urls(args.job_id)
    if not filas:
        print(f"El rastreo {args.job_id} no tiene paginas HTML con 200.")
        sys.exit(1)

    hosts = {re.sub(r"^https?://", "", u).split("/")[0] for u, _ in filas[:200]}

    grupos: dict[str, list[str]] = defaultdict(list)
    for url, path in filas:
        grupos[clasificar(path)].append(url)

    rnd = random.Random(args.semilla)
    seleccion = []
    for plantilla, urls in sorted(grupos.items(), key=lambda kv: -len(kv[1])):
        seleccion.append((f"{plantilla}  (n={len(urls)})", rnd.sample(urls, min(args.muestras, len(urls)))))

    print(f"{len(filas)} paginas · {len(grupos)} plantillas · {args.muestras} muestras cada una\n")
    resultados = asyncio.run(analizar(seleccion, args.espera, hosts))

    print(f"\n{'plantilla':<34}{'muestras':>9}{'enl.soloJS':>12}{'pal.crudo':>11}{'pal.render':>12}{'oculto':>9}")
    print("-" * 87)
    for r in resultados:
        crudo, render = r["palabras_crudo"], r["palabras_render"]
        pct = f"+{round(100 * (render - crudo) / crudo)}%" if crudo else "n/d"
        print(
            f"{r['plantilla']:<34}{r['muestras']:>9}{r['enlaces_solo_js']:>12}"
            f"{crudo:>11}{render:>12}{pct:>9}"
        )

    print("\nLectura:")
    print("  enl.soloJS > 0  -> el grafo de enlaces de esa plantilla esta incompleto sin render")
    print("  oculto alto     -> el analisis de contenido sobre HTML crudo subestima esas paginas")


if __name__ == "__main__":
    main()
