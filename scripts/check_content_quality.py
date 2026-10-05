"""Comprueba, POR PLANTILLA, si el rastreo se ha llevado todo el contenido.

`check_js_templates.py` responde a una pregunta: "hay contenido/enlaces que solo
aparecen con JavaScript?". Esta responde a la otra mitad, la que nadie ve: "de lo
que SI venia en el HTML, cuanto hemos guardado realmente?". Un stripper de
plantilla demasiado agresivo, un `<main>` mal detectado o un texto dentro de un
componente raro se pierden en silencio: la pagina queda en la base de datos con
200, titulo y 40 palabras, y nada lo marca.

Para cada plantilla saca N URLs del rastreo y compara cifras de la misma pagina:

    guardado   palabras que hay en la BD tras el rastreo (urls.word_count)
    contenido  palabras del texto principal guardado (page_content.content_text)
    crudo      lo que extraeria ahora mismo el extractor del HTML sin JS
    main       lo que devuelve extract_main_content sobre ese HTML crudo
    render     palabras tras ejecutar JS en Chromium

Las diferencias significan cosas distintas y por eso se muestran separadas:

    render >> crudo      -> la plantilla necesita render_js
    crudo  >> guardado   -> el rastreo perdio contenido (bug o config), re-rastrear
    contenido ~ 0        -> el stripper de plantilla se comio la pagina

Uso (dentro del contenedor del crawler):

    python scripts/check_content_quality.py <job_id> [--muestras 3] [--plantillas 8]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import sys
from collections import defaultdict


def _preparar_rutas() -> None:
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

RE_FECHA = re.compile(r"^\d{4}([-/]\d{1,2}){0,2}$")
RE_NUM = re.compile(r"^\d+$")
RE_IDIOMA = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")


def firma(path: str) -> str:
    """Forma de la URL, no la URL: /2018-05-17/dia-del-reciclaje -> /:fecha/...

    Deliberadamente generico: nada de reglas por cliente. Dos paginas con la
    misma forma casi siempre salen de la misma plantilla, y cuando no, se ve en
    las cifras y se afina subiendo --muestras.
    """
    trozos = [t for t in path.strip("/").split("/") if t]
    if not trozos:
        return "/ (home)"
    partes = []
    for t in trozos:
        t_l = t.lower()
        if RE_FECHA.match(t_l):
            partes.append(":fecha")
        elif RE_NUM.match(t_l):
            partes.append(":num")
        elif RE_IDIOMA.match(t_l) and not partes:
            partes.append(":idioma")
        elif "." in t_l:
            partes.append(":fichero" + os.path.splitext(t_l)[1])
        else:
            partes.append(t_l)
    # El ultimo tramo identifica la pagina concreta, no la plantilla: si no se
    # normaliza, cada articulo sale como plantilla propia. Los tramos de arriba
    # (hasta tres) son los que dan la forma.
    if len(partes) > 1:
        forma = partes[:-1][:3] + [":slug"]
    else:
        forma = partes
    return "/" + "/".join(forma) + f" ({len(partes)}n)"


def descargar_crudo(url: str, timeout: int = 40) -> str:
    """Mismo camino que el rastreo: curl_cffi con fingerprint de Chrome."""
    from curl_cffi import requests as cr

    r = cr.get(url, impersonate=os.getenv("IMPERSONATE", "chrome124"), timeout=timeout)
    return r.text


def cargar_paginas(job_id: str):
    """Paginas HTML 200 internas del rastreo, con lo que se guardo de cada una."""
    from sqlalchemy import func

    from shared.database import SessionLocal
    from shared.models import HtmlMeta, Link, PageContent, Url

    sesion = SessionLocal()
    try:
        filas = (
            sesion.query(Url.id, Url.url, Url.path, Url.word_count)
            .filter(
                Url.job_id == job_id,
                Url.status_code == 200,
                Url.is_html.is_(True),
                Url.is_internal.is_(True),
            )
            .all()
        )
        ids = [f[0] for f in filas]
        contenidos = {}
        titulos = {}
        enlaces: dict[int, int] = defaultdict(int)
        if ids:
            contenidos = dict(
                sesion.query(PageContent.url_id, PageContent.content_text)
                .filter(PageContent.url_id.in_(ids))
                .all()
            )
            titulos = dict(
                sesion.query(HtmlMeta.url_id, HtmlMeta.title)
                .filter(HtmlMeta.url_id.in_(ids))
                .all()
            )
            for url_id, n in (
                sesion.query(Link.from_url_id, func.count(Link.id))
                .filter(Link.from_url_id.in_(ids))
                .group_by(Link.from_url_id)
                .all()
            ):
                enlaces[url_id] = n
        salida = []
        for url_id, url, path, wc in filas:
            if "?" in url:
                continue
            texto = contenidos.get(url_id) or ""
            salida.append(
                {
                    "url": url,
                    "path": path or "/",
                    "wc_guardado": wc or 0,
                    "pal_contenido": len(texto.split()),
                    "titulo": titulos.get(url_id) or "",
                    "enlaces_guardados": enlaces.get(url_id, 0),
                }
            )
        return salida
    finally:
        sesion.close()


async def analizar(grupos, espera_ms: int, hosts: set[str]):
    from parsel import Selector
    from playwright.async_api import async_playwright

    from seo_crawler.extractors import (
        extract_links,
        extract_main_content,
        extract_word_count,
    )

    resultados = []
    async with async_playwright() as p:
        navegador = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        for plantilla, paginas in grupos:
            acc: dict[str, int] = defaultdict(int)
            ok = 0
            peores = []
            for pag in paginas:
                url = pag["url"]
                try:
                    html_crudo = descargar_crudo(url)
                except Exception as exc:
                    print(f"    aviso: no se pudo descargar {url[:70]} ({exc})")
                    continue
                contexto = await navegador.new_context(user_agent=UA)
                pagina_pw = await contexto.new_page()
                try:
                    await pagina_pw.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await pagina_pw.wait_for_timeout(espera_ms)
                    html_render = await pagina_pw.content()
                except Exception as exc:
                    print(f"    aviso: no se pudo renderizar {url[:70]} ({exc})")
                    await contexto.close()
                    continue
                await contexto.close()

                sel_crudo = Selector(text=html_crudo)
                sel_render = Selector(text=html_render)
                pal_crudo = extract_word_count(sel_crudo)
                pal_render = extract_word_count(sel_render)
                pal_main = len((extract_main_content(sel_crudo) or "").split())
                enl_crudo = {enl["url"] for enl in extract_links(sel_crudo, url, hosts)}
                enl_render = {enl["url"] for enl in extract_links(sel_render, url, hosts)}

                acc["pal_crudo"] += pal_crudo
                acc["pal_render"] += pal_render
                acc["pal_main"] += pal_main
                acc["wc_guardado"] += pag["wc_guardado"]
                acc["pal_contenido"] += pag["pal_contenido"]
                acc["enl_crudo"] += len(enl_crudo)
                acc["enl_guardados"] += pag["enlaces_guardados"]
                acc["enl_solo_js"] += len(enl_render - enl_crudo)
                ok += 1
                # Peor caso del grupo: donde mas contenido se ha quedado fuera.
                peores.append((pal_crudo - pag["pal_contenido"], url, pal_crudo,
                               pag["pal_contenido"], pal_main))

            if ok:
                peores.sort(reverse=True)
                resultados.append(
                    {
                        "plantilla": plantilla,
                        "muestras": ok,
                        **{k: v // ok for k, v in acc.items()},
                        "peor": peores[0],
                    }
                )
        await navegador.close()
    return resultados


def comprobar(job_id: str, muestras: int, espera_ms: int, semilla: int,
              plantillas_max: int | None):
    paginas = cargar_paginas(job_id)
    if not paginas:
        print("El job no tiene paginas HTML 200 internas.")
        return []
    hosts = {p["url"].split("/")[2] for p in paginas}
    # Si el job trae reglas de plantilla (config.templates), se agrupa con
    # ellas para que las cifras salgan con los mismos nombres que en js_check;
    # si no, por la forma de la URL.
    try:
        from check_js_templates import cargar_reglas, clasificar
        reglas = cargar_reglas(job_id)
    except Exception:
        reglas = []
    por_plantilla = defaultdict(list)
    for pag in paginas:
        clave = clasificar(pag["path"], reglas) if reglas else firma(pag["path"])
        por_plantilla[clave].append(pag)
    ordenados = sorted(por_plantilla.items(), key=lambda kv: len(kv[1]), reverse=True)
    if plantillas_max:
        ordenados = ordenados[:plantillas_max]
    rnd = random.Random(semilla)
    grupos = [
        (f"{plantilla} n={len(pags)}", rnd.sample(pags, min(muestras, len(pags))))
        for plantilla, pags in ordenados
    ]
    print(
        f"Job {job_id}: {len(paginas)} paginas, {len(por_plantilla)} plantillas; "
        f"comprobando {len(grupos)} con {muestras} muestra(s).\n"
    )
    return asyncio.run(analizar(grupos, espera_ms, hosts))


def main() -> None:
    ap = argparse.ArgumentParser(description="Calidad de contenido por plantilla")
    ap.add_argument("job_id")
    ap.add_argument("--muestras", type=int, default=3)
    ap.add_argument("--espera", type=int, default=3500)
    ap.add_argument("--semilla", type=int, default=7)
    ap.add_argument("--plantillas", type=int, default=None)
    args = ap.parse_args()

    resultados = comprobar(args.job_id, args.muestras, args.espera, args.semilla,
                           args.plantillas)
    if not resultados:
        return
    cab = (
        f"{'plantilla':<38}{'n':>3}{'guardado':>10}{'contenido':>10}"
        f"{'crudo':>8}{'main':>7}{'render':>8}{'enl.g':>7}{'enl.c':>7}{'soloJS':>8}"
    )
    print("\n" + cab)
    print("-" * len(cab))
    for r in resultados:
        print(
            f"{r['plantilla'][:37]:<38}{r['muestras']:>3}{r['wc_guardado']:>10}"
            f"{r['pal_contenido']:>10}{r['pal_crudo']:>8}{r['pal_main']:>7}"
            f"{r['pal_render']:>8}{r['enl_guardados']:>7}{r['enl_crudo']:>7}"
            f"{r['enl_solo_js']:>8}"
        )
    print("\nLectura:")
    print("  guardado ~ crudo         -> el rastreo se llevo el texto de la pagina")
    print("  contenido < crudo        -> normal (quita plantilla); si es ~0, se perdio")
    print("  render >> crudo          -> esa plantilla necesita render_js")
    print("  enl.g < enl.c            -> se guardaron menos enlaces de los que hay")
    print("\nPeor muestra por plantilla (palabras crudo -> contenido guardado):")
    for r in resultados:
        _perdido, url, pal_crudo, pal_cont, pal_main = r["peor"]
        print(
            f"  {r['plantilla'][:32]:<34} {pal_crudo:>6} -> {pal_cont:<6} "
            f"(main {pal_main}) {url[:70]}"
        )


if __name__ == "__main__":
    main()
