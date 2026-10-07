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

# Codigos de idioma de verdad: con `^/[a-z]{2}/?$` a secas, `/tv/` salia como
# "home" y se comparaba la portada del sitio con una seccion de video. La lista
# vive en `shared/plantillas.py`, que es donde la usa el agrupamiento por forma.
from shared.plantillas import IDIOMAS  # noqa: E402

_IDIOMAS_ALT = "|".join(sorted(IDIOMAS))

# Una pagina legal es la que SE LLAMA asi, no la que menciona la palabra: el
# patron de antes casaba `/es/productos/galletas-cookies/` y
# `/es/privacy-shield-producto` por llevar "cookies" o "privacy" dentro. Se
# exige que el ultimo tramo este hecho SOLO de palabras de pagina legal.
_PALABRAS_LEGALES = (
    "politica politiques policy policies privacidad privacitat privacy "
    "datenschutz datenschutzerklaerung impressum cookies cookie galletes "
    "aviso avis legal notice terminos termes terms condiciones condicions "
    "conditions uso us use of and y i de del la el les los las general "
    "generales mentions mentions-legales nota-legal disclaimer"
).split()
_LEGAL_ALT = "|".join(sorted(set(_PALABRAS_LEGALES), key=len, reverse=True))
_LEGAL_PATRON = rf"(?:^|/)(?:{_LEGAL_ALT})(?:[-_](?:{_LEGAL_ALT}))*/?$"

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
    ("home",              r"^/?$|^/(?:" + _IDIOMAS_ALT + r")(?:-[a-z]{2})?/?$"),
    ("blog · post",       r"^/blog/.+/.+"),
    ("blog · categoria",  r"^/blog/[^/]+/?$"),
    ("blog · indice",     r"^/blog/?$"),
    # Las de producto estaban escritas para un cliente de hoteles
    # (`/es/hoteles/...`), asi que en cualquier otro sitio no casaba ninguna y
    # todo caia en "otras · N niveles", que junta plantillas que no tienen nada
    # que ver. Lo especifico de cada cliente va en `config.templates` del job
    # (`projects/<cliente>/config.json`); aqui solo lo que vale en cualquier
    # sitio, y lo que no case se agrupa por la FORMA de la ruta.
    ("legal",             _LEGAL_PATRON),
]


def cargar_reglas(job_id: str) -> list[tuple[str, str]]:
    """Reglas de plantilla del propio job (``config.templates``) o, si no las
    trae, las genericas de arriba.

    Las reglas por cliente viajan en el JSON del job (``projects/<cliente>/
    config.json`` -> ``templates``), no en este fichero: asi cada cliente lleva
    las suyas y el repo no cambia entre proyectos.
    """
    try:
        from shared.database import SessionLocal
        from shared.models import Job

        sesion = SessionLocal()
        try:
            job = sesion.get(Job, job_id)
            plantillas = ((job.config or {}).get("templates") if job else None) or []
        finally:
            sesion.close()
    except Exception:
        plantillas = []
    reglas: list[tuple[str, str]] = []
    for regla in plantillas:
        if isinstance(regla, dict):
            nombre, patron = regla.get("nombre") or regla.get("name"), regla.get("patron") or regla.get("pattern")
        else:
            nombre, patron = (list(regla) + [None, None])[:2]
        if nombre and patron:
            reglas.append((str(nombre), str(patron)))
    return reglas or list(REGLAS)


def clasificar(path: str, reglas: list[tuple[str, str]] | None = None) -> str:
    for nombre, patron in (reglas if reglas is not None else REGLAS):
        if re.search(patron, path, re.I):
            return nombre
    # Por la FORMA de la ruta, no por el numero de tramos: "otras · 2 niveles"
    # metia en el mismo monton una ficha de producto y una pagina legal, y lo
    # que se muestreaba era azar.
    from shared.plantillas import firma_de_ruta

    return firma_de_ruta(path)


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
            enl_solo_js = enl_crudo = pal_crudo = pal_render = muestras_ok = 0
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
                    # La MISMA limpieza que hace el rastreo con render. Sin
                    # ella, el enlace a "Politica de cookies" que inyecta
                    # OneTrust o Cookiebot solo existe en el render y se
                    # contaba como enlace escondido tras JavaScript: cualquier
                    # sitio con gestor de consentimiento salia con el grafo no
                    # fiable y con el PageRank bajo sospecha sin motivo.
                    try:
                        await pagina.evaluate(_LIMPIADOR_DE_BANNERS)
                    except Exception:
                        pass
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
                enl_crudo += len(enlaces_crudo)
                pal_crudo += extract_word_count(sel_crudo)
                pal_render += extract_word_count(sel_render)
                muestras_ok += 1

            if muestras_ok:
                pc, pr = pal_crudo // muestras_ok, pal_render // muestras_ok
                # Si Chromium trae MUCHO menos texto que el HTML crudo, no ha
                # renderizado la pagina: le han servido un bloqueo del WAF, un
                # desafio o un error. En ese caso "0 enlaces solo-JS" es trivial
                # (la pagina renderizada no tiene enlaces) y NO prueba nada.
                # Medido: dos rastreos con 75 palabras renderizadas en todas las
                # plantillas frente a 800-1.800 crudas, y el veredicto "grafo
                # fiable" salio igual. Se marca y el worker no concluye con ello.
                render_sospechoso = pc >= 100 and pr < pc * 0.2
                resultados.append(
                    {
                        "plantilla": plantilla,
                        "muestras": muestras_ok,
                        "enlaces_solo_js": enl_solo_js,
                        # Hace falta para saber si esos enlaces escondidos son
                        # una fraccion que importa o el ruido de un widget.
                        "enlaces_crudo": enl_crudo,
                        "palabras_crudo": pc,
                        "palabras_render": pr,
                        "render_sospechoso": render_sospechoso,
                    }
                )
        await navegador.close()
    return resultados


def _cargar_limpiador() -> str:
    """El limpiador de banners del spider, para comparar lo mismo que el rastreo.

    Se lee del fuente en vez de importar el spider porque importarlo arrastra
    Scrapy y scrapy-playwright enteros, y este script corre suelto.
    """
    ruta = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "crawler", "seo_crawler", "spiders", "seo_spider.py",
    )
    try:
        fuente = open(ruta, encoding="utf-8").read()
        encontrado = re.search(r'_BOILERPLATE_REMOVAL_JS = """(.*?)"""', fuente, re.S)
        if not encontrado:
            return "() => {}"
        ns: dict = {}
        exec(f'_JS = """{encontrado.group(1)}"""', ns)
        return ns["_JS"]
    except Exception:
        return "() => {}"


_LIMPIADOR_DE_BANNERS = _cargar_limpiador()


def comprobar(
    job_id: str,
    muestras: int = 3,
    espera_ms: int = 3500,
    semilla: int = 7,
    plantillas_max: int | None = None,
) -> list[dict]:
    """Ejecuta la comprobacion y devuelve los resultados. Reutilizable.

    ``plantillas_max`` limita a las N plantillas con mas paginas. El worker lo
    usa para que la comprobacion automatica cueste un par de minutos en vez de
    los ocho que tarda el barrido completo.
    """
    filas = cargar_urls(job_id)
    if not filas:
        return []

    # Las dos variantes de cada host: `is_internal_url` compara el host y el
    # host sin `www.`, asi que en un rastreo de `www.x.com` un enlace a
    # `x.com` salia EXTERNO y no se contaba como enlace oculto. Y sobre todas
    # las filas, no las 200 primeras: un sitio con subdominios perdia los que
    # no aparecen al principio.
    hosts: set[str] = set()
    for u, _ in filas:
        host = re.sub(r"^https?://", "", u).split("/")[0].lower()
        if not host:
            continue
        hosts.add(host)
        hosts.add(host.removeprefix("www."))
        if not host.startswith("www."):
            hosts.add("www." + host)

    reglas = cargar_reglas(job_id)
    grupos: dict[str, list[str]] = defaultdict(list)
    for url, path in filas:
        grupos[clasificar(path, reglas)].append(url)

    ordenados = sorted(grupos.items(), key=lambda kv: -len(kv[1]))
    if plantillas_max:
        ordenados = ordenados[:plantillas_max]

    rnd = random.Random(semilla)
    seleccion = [
        (f"{plantilla}  (n={len(urls)})", rnd.sample(urls, min(muestras, len(urls))))
        for plantilla, urls in ordenados
    ]
    return asyncio.run(analizar(seleccion, espera_ms, hosts))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job_id", help="UUID de un rastreo ya realizado")
    ap.add_argument("--muestras", type=int, default=3, help="URLs por plantilla (def. 3)")
    ap.add_argument("--espera", type=int, default=3500, help="ms de espera tras cargar (def. 3500)")
    ap.add_argument("--semilla", type=int, default=7, help="semilla del muestreo, para reproducibilidad")
    ap.add_argument("--plantillas", type=int, default=None, help="limitar a las N plantillas mayores")
    args = ap.parse_args()

    resultados = comprobar(
        args.job_id, args.muestras, args.espera, args.semilla, args.plantillas
    )
    if not resultados:
        print(f"El rastreo {args.job_id} no tiene paginas HTML con 200.")
        sys.exit(1)

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
