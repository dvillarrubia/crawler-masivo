"""Sondas de soft 404 para un censo YA hecho (decision 105).

Los rastreos nuevos sondean solos al empezar (`crawler/seo_crawler/sonda_soft404.py`).
Este script hace lo mismo sobre un censo anterior a las sondas: pide AHORA, en
vivo, una URL inexistente por host de las semillas y por cada una de las
plantillas de ruta mas pobladas del censo, y extrae su huella con las mismas
funciones que el rastreo.

OJO: la sonda es de HOY y el censo de cuando se hizo. Si el sitio ha cambiado su
pagina de error entre medias, la huella no casara (decision 13: los sitios
varian solos). Por eso cada sonda lleva `origen: "sonda_posterior"` y su fecha,
que viajan a los detalles de la incidencia. En un censo con `render_js` la
sonda se renderiza con Chromium (`--render auto`), porque el esqueleto de una
SPA sin render no casa con ninguna pagina guardada renderizada.

Uso (en la imagen del crawler):

    python scripts/sonda_soft404.py <job_id> [--max 12] [--render auto|si|no] [--escribir]

Sin `--escribir` solo imprime el JSON de las sondas. Con el, lo guarda en
`jobs.soft404["sondas"]`; despues hay que re-analizar el censo.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# El repo primero: en la imagen, /app trae la copia de `shared/` con la que se
# construyo, que puede no tener aun este modulo.
for _p in reversed((_RAIZ, os.path.join(_RAIZ, "crawler"), "/app", "/app/crawler")):
    if os.path.isdir(_p):
        if _p in sys.path:
            sys.path.remove(_p)
        sys.path.insert(0, _p)

from shared.plantillas import firma_de_ruta  # noqa: E402
from shared.soft404 import mangle_path, token_sonda  # noqa: E402


def planificar(job_id: str, semillas: list[str], urls: list[str], maximo: int) -> list[tuple[str, str, str | None]]:
    """(clave, url_sonda, plantilla): raiz por host y plantillas por tamano."""
    plan: list[tuple[str, str, str | None]] = []
    vistos: set[str] = set()
    for s in semillas:
        p = urlparse(s)
        host = (p.hostname or "").lower()
        clave = f"{host}|raiz"
        if host and clave not in vistos:
            vistos.add(clave)
            plan.append((clave, mangle_path(f"{p.scheme}://{p.netloc}/", token_sonda(job_id, clave)), None))
    cuenta: Counter = Counter()
    modelo: dict[tuple[str, str], str] = {}
    for u in urls:
        p = urlparse(u)
        if len([t for t in (p.path or "").split("/") if t]) < 2:
            continue
        k = ((p.hostname or "").lower(), firma_de_ruta(p.path))
        cuenta[k] += 1
        modelo.setdefault(k, u)
    for (host, plantilla), _ in cuenta.most_common():
        if len(plan) >= maximo:
            break
        clave = f"{host}|{plantilla}"
        if clave in vistos:
            continue
        vistos.add(clave)
        plan.append((clave, mangle_path(modelo[(host, plantilla)], token_sonda(job_id, clave)), plantilla))
    return plan[:maximo]


def sondear(url: str, clave: str, plantilla: str | None, *, impersonate: str, extraction: dict) -> dict:
    from curl_cffi import requests as cr
    from parsel import Selector

    from seo_crawler.sonda_soft404 import huella_de_respuesta

    fila = {"clave": clave, "host": clave.split("|", 1)[0], "plantilla": plantilla, "url": url,
            "origen": "sonda_posterior", "render_js": False,
            "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        r = cr.get(url, impersonate=impersonate, timeout=40, allow_redirects=False)
    except Exception as exc:  # noqa: BLE001
        fila.update(status=None, error=f"{type(exc).__name__}: {exc}"[:200])
        return fila
    fila["status"] = r.status_code
    fila["redirige_a"] = r.headers.get("location") if 300 <= r.status_code < 400 else None
    if fila["redirige_a"] is None and "html" in (r.headers.get("content-type") or "html"):
        fila.update(huella_de_respuesta(Selector(text=r.text), url, extraction=extraction))
    return fila


def sondear_con_render(plan, *, extraction: dict, espera_ms: int = 3000) -> list[dict]:
    """Lo mismo con Chromium, para los censos con `render_js`.

    Sin render, la sonda de una SPA es el esqueleto vacio de la aplicacion y su
    huella no casa con ninguna pagina del censo, que se guardo renderizada.
    """
    from parsel import Selector
    from playwright.sync_api import sync_playwright

    from seo_crawler.sonda_soft404 import huella_de_respuesta

    filas = []
    with sync_playwright() as p:
        navegador = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        for clave, url, plantilla in plan:
            fila = {"clave": clave, "host": clave.split("|", 1)[0], "plantilla": plantilla,
                    "url": url, "origen": "sonda_posterior", "render_js": True,
                    "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            pagina = navegador.new_context().new_page()
            try:
                r = pagina.goto(url, wait_until="domcontentloaded", timeout=45000)
                pagina.wait_for_timeout(espera_ms)
                fila["status"] = r.status if r else None
                fila["redirige_a"] = pagina.url if pagina.url.rstrip("/") != url.rstrip("/") else None
                if fila["redirige_a"] is None:
                    fila.update(huella_de_respuesta(Selector(text=pagina.content()), url,
                                                    extraction=extraction))
            except Exception as exc:  # noqa: BLE001
                fila.update(status=None, error=f"render: {type(exc).__name__}: {exc}"[:200])
            finally:
                pagina.close()
            filas.append(fila)
        navegador.close()
    return filas


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--max", type=int, default=12)
    ap.add_argument("--escribir", action="store_true")
    ap.add_argument("--render", choices=("auto", "si", "no"), default="auto",
                    help="auto = con Chromium si el censo se rastreo con render_js")
    args = ap.parse_args()

    from sqlalchemy import select, update

    from shared.database import SessionLocal
    from shared.models import Job, Url

    with SessionLocal() as s:
        # Columnas sueltas y no el ORM entero: asi lee tambien una base que aun
        # no tiene la columna `jobs.soft404` (solo `--escribir` la necesita).
        job_id, seeds, cfg = s.execute(
            select(Job.id, Job.seeds, Job.config).where(Job.id == args.job_id)).one()
        urls = [u for (u,) in s.execute(select(Url.url).where(
            Url.job_id == job_id, Url.is_html.is_(True), Url.is_internal.is_(True),
            Url.status_code == 200))]
        cfg = cfg or {}
        plan = planificar(str(job_id), seeds or [], urls, args.max)
        render = args.render == "si" or (args.render == "auto" and cfg.get("render_js"))
        if render:
            sondas = sondear_con_render(plan, extraction=cfg.get("extraction") or {})
        else:
            sondas = [sondear(u, c, p, impersonate=cfg.get("impersonate") or "chrome124",
                              extraction=cfg.get("extraction") or {}) for c, u, p in plan]
        print(json.dumps(sondas, ensure_ascii=False, indent=1))
        if args.escribir:
            previo = s.execute(select(Job.soft404).where(Job.id == job_id)).scalar()
            previo = previo if isinstance(previo, dict) else {}
            s.execute(update(Job).where(Job.id == job_id).values(soft404={**previo, "sondas": sondas}))
            s.commit()
            print(f"{len(sondas)} sondas guardadas; re-analiza el censo.", file=sys.stderr)


if __name__ == "__main__":
    main()
