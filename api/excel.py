"""Exportacion del censo a Excel con varias hojas (Ola 6.3, decision 145).

El CSV sirve para cruzar datos; lo que se manda a un cliente o a desarrollo es
un Excel que se abra y se lea: el resumen, que arreglar primero con «como se
arregla», las URLs con sus columnas utiles (las de Search Console incluidas),
las incidencias y lo de Google que el rastreo no encontro.

`openpyxl` en modo de solo escritura: cada fila va a disco segun se escribe,
asi que la memoria no crece con el censo (como el backup de la decision 65).
Las URLs se leen por tramos de 1.000 con su propia sesion (decision 5).

Los nombres y las fichas de las incidencias viven en el frontend
(`incidencias.js`, `app.js`): se leen de ahi, no se copian. Si el formato del
fichero cambiara y no se pudieran leer, la hoja sale con el nombre tecnico en
vez de romperse.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
TOPE_INCIDENCIAS = 500_000
GRAVEDAD = {"error": "Error", "warning": "Aviso", "info": "Info"}

COLUMNAS_URLS = [
    "url", "status_code", "indexable", "indexability_status", "title", "title_len",
    "meta_description", "h1", "canonical_href", "content_word_count", "inlinks_count",
    "pagerank_score", "click_depth", "in_sitemap", "gsc_clics", "gsc_impresiones", "gsc_posicion",
    "response_time_ms", "segmento",
]

_FICHA = re.compile(r"'([a-z0-9_]+)': \{\s*que: '(.*?)',\s*google: '(.*?)',\s*arreglo: '(.*?)',\s*\}", re.S)
_ETIQUETA = re.compile(r"^\s+'?([a-z0-9_]+)'?: '([^'\n]*)',", re.M)


def _bloque(texto: str, inicio: str) -> str:
    i = texto.find(inicio)
    if i < 0:
        return ""
    j = texto.find("\n};", i)
    return texto[i:j if j > 0 else len(texto)]


@lru_cache(maxsize=1)
def catalogo() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """(nombres, fichas) de las incidencias, leidos del frontend."""
    nombres: dict[str, str] = {}
    fichas: dict[str, dict[str, str]] = {}
    try:
        inc = (FRONTEND / "incidencias.js").read_text(encoding="utf-8")
        app = (FRONTEND / "app.js").read_text(encoding="utf-8")
        for m in _FICHA.finditer(_bloque(inc, "const ISSUE_INFO")):
            fichas[m.group(1)] = {"que": m.group(2), "google": m.group(3), "arreglo": m.group(4)}
        for bloque in (_bloque(app, "const ISSUE_LABEL = {"), _bloque(inc, "const ISSUE_LABEL_EXTRA")):
            nombres.update(dict(_ETIQUETA.findall(bloque)))
    except OSError:
        pass
    return nombres, fichas


# Una cadena que empieza por uno de estos la trata Excel/Sheets (y openpyxl) como
# formula; escapandola con un apostrofo se escribe como texto (decision 207).
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _celda(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)[:2000]
    if isinstance(v, bool):
        return "sí" if v else "no"
    if hasattr(v, "tzinfo") and getattr(v, "tzinfo", None) is not None:
        return v.replace(tzinfo=None)
    if isinstance(v, str) and v.startswith(_FORMULA):
        return "'" + v
    return v


def escribir(ruta: str, db, job_id) -> int:
    """Escribe el Excel en `ruta` y devuelve cuantas URLs lleva."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from sqlalchemy.orm import joinedload, sessionmaker

    from api.routers.results import COLUMNAS_VISTA, _h1s, get_informe, resumen_issues
    from shared.models import Issue, Job, Url

    nombres, fichas = catalogo()
    negrita = Font(bold=True)
    wb = Workbook(write_only=True)

    def hoja(titulo: str, cabecera: list[str]):
        ws = wb.create_sheet(titulo)
        ws.append([_cabecera(ws, c, negrita) for c in cabecera])
        return ws

    job = db.get(Job, job_id)
    inf = get_informe(job_id, prioridades=10, db=db)

    # -- Resumen ---------------------------------------------------------------
    ws = hoja("Resumen", ["Dato", "Valor"])
    filas = [
        ("Cliente", (inf.get("cliente") or {}).get("nombre")),
        ("Sitio", inf["job"]["sitio"]), ("Rastreo", job.name), ("Fecha", inf["job"]["fecha"]),
        ("URLs rastreadas", inf["cifras"]["urls"]), ("Páginas HTML", inf["cifras"]["paginas_html"]),
        ("Indexables", inf["cifras"]["indexables"]), ("No indexables", inf["cifras"]["no_indexables"]),
        ("Nota global (0-100)", inf["nota"]["global"]),
        ("Nota sin tope por errores", inf["nota"]["sin_tope"]),
    ]
    filas += [(f"Nota · {c['nombre']}", c["nota"]) for c in inf["nota"]["categorias"]]
    t = inf.get("trafico")
    if t:
        filas += [("Search Console · ventana", f"{t['desde']} a {t['hasta']}"),
                  ("Search Console · clics", t["clics"]), ("Search Console · impresiones", t["impresiones"]),
                  ("Clics en URLs con parámetros", t.get("clics_en_variantes")),
                  ("URLs con clics que el rastreo no encontró", t.get("urls_fuera_del_censo"))]
    filas += [("Aviso", x) for x in inf.get("fiabilidad") or []]
    for f in filas:
        ws.append([_celda(v) for v in f])

    # -- Que arreglar ------------------------------------------------------------
    ws = hoja("Qué arreglar", ["#", "Incidencia", "Gravedad", "Páginas", "Incidencias",
                               "Hallazgos de plantilla", "Prioridad", "Qué es", "Qué decide Google",
                               "Cómo se arregla", "Ejemplo", "Tipo (técnico)"])
    todas = resumen_issues(job_id, ejemplos=1, segmento=None, por_segmento=True, segmentos_max=1,
                           orden="prioridad", db=db)
    for n, r in enumerate(todas, 1):
        ficha = fichas.get(r.issue_type, {})
        ws.append([n, nombres.get(r.issue_type, r.issue_type), GRAVEDAD.get(r.severity, r.severity),
                   r.paginas, r.incidencias, r.agregadas or 0, round(r.prioridad or 0),
                   ficha.get("que"), ficha.get("google"), ficha.get("arreglo"),
                   r.ejemplos[0].url if r.ejemplos else None, r.issue_type])

    # -- URLs --------------------------------------------------------------------
    cols = [c for c in COLUMNAS_URLS if c in COLUMNAS_VISTA]
    ws = hoja("URLs", [COLUMNAS_VISTA[c][0] for c in cols])
    ids = [i for (i,) in db.query(Url.id).filter(Url.job_id == job_id).order_by(Url.id)]
    Sesion = sessionmaker(bind=db.get_bind())
    for i in range(0, len(ids), 1000):
        tramo = ids[i:i + 1000]
        with Sesion() as s:
            urls = (s.query(Url).options(joinedload(Url.html_meta))
                    .filter(Url.id.in_(tramo)).order_by(Url.id).all())
            if "h1" in cols:
                hs = _h1s(s, [u.id for u in urls])
                for u in urls:
                    u._h1 = hs.get(u.id, (None, 0))
            for u in urls:
                ws.append([_celda(COLUMNAS_VISTA[c][1](u)) for c in cols])

    # -- Incidencias -------------------------------------------------------------
    ws = hoja("Incidencias", ["Incidencia", "Gravedad", "URL", "Detalles", "Tipo (técnico)"])
    n = 0
    with Sesion() as s:
        q = (s.query(Issue.issue_type, Issue.severity, Url.url, Issue.details)
             .join(Url, Url.id == Issue.url_id).filter(Issue.job_id == job_id)
             .order_by(Issue.severity, Issue.issue_type, Url.url).yield_per(2000))
        for tipo, sev, url, det in q:
            n += 1
            if n > TOPE_INCIDENCIAS:
                ws.append([f"… y más: el Excel lleva las {TOPE_INCIDENCIAS:,} primeras".replace(",", ".")])
                break
            ws.append([nombres.get(tipo, tipo), GRAVEDAD.get(sev, sev), url, _celda(det), tipo])

    # -- Search Console: lo que el rastreo no encontro ---------------------------
    r = job.gsc_resumen if isinstance(getattr(job, "gsc_resumen", None), dict) else None
    if r and r.get("fuera_del_censo"):
        ws = hoja("Google sin rastrear", ["URL", "Clics", "Impresiones"])
        for x in r["fuera_del_censo"]:
            ws.append([x.get("url"), x.get("clics"), x.get("impresiones")])

    wb.save(ruta)
    return len(ids)


def _cabecera(ws, texto: str, fuente):
    from openpyxl.cell import WriteOnlyCell

    c = WriteOnlyCell(ws, value=texto)
    c.font = fuente
    return c
