"""Accesibilidad con axe-core por muestra de plantilla (decision 174, Ola 8).

Lo comparten la sonda del spider (`seo_crawler/sonda_axe.py`), que resume lo
que devuelve axe en el navegador, y el analisis y la API, que lo agregan por
regla y plantilla. Dos copias de estas reglas acabarian discrepando (23, 39).

Que se afirma y que no:

- **Violaciones** (`violations` de axe): fallos que axe comprueba sin ayuda
  humana, de las reglas WCAG 2.0, 2.1 y 2.2 de nivel A y AA (`ETIQUETAS`).
  Son ciertos sobre el DOM renderizado que vio el navegador.
- **Incompletas** (`incomplete`): axe no pudo decidir (un texto sobre una
  imagen de fondo, un contraste con transparencias). Se guardan y se cuentan,
  pero **no se afirman**: ni aviso ni «falla».
- Lo que axe no puede automatizar (orden de lectura, que un texto alternativo
  describa la imagen, el teclado, los subtitulos) **no se mide**: una pagina
  sin violaciones no es una pagina accesible. Lo automatizable cubre en torno
  a un 30-40 % de los criterios WCAG A/AA (por volumen de incidencias, el
  estudio de Deque de 2021 midio un ~57 %); se dice en la tarjeta.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterable

# Reglas que se ejecutan: WCAG 2.x, niveles A y AA. Ni AAA ni las
# «best-practice» de axe, que no son incumplimientos de la norma.
ETIQUETAS: tuple[str, ...] = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22a", "wcag22aa")

# Impactos de axe, de mas a menos grave, con su nombre en la interfaz.
IMPACTOS: tuple[str, ...] = ("critical", "serious", "moderate", "minor")
NOMBRE_IMPACTO = {"critical": "crítico", "serious": "grave", "moderate": "moderado", "minor": "leve"}
ORDEN_IMPACTO = {imp: i for i, imp in enumerate(IMPACTOS)}

# Tipo de incidencia y severidad por impacto. Nunca `error`: la nota SEO se
# topa con cualquier error abierto (decision 92) y la accesibilidad no es un
# factor de posicionamiento; un fallo crítico de axe no puede bajar la nota
# SEO de un censo.
TIPO_POR_IMPACTO = {
    "critical": ("accesibilidad_grave", "warning"),
    "serious": ("accesibilidad_grave", "warning"),
    "moderate": ("accesibilidad_menor", "info"),
    "minor": ("accesibilidad_menor", "info"),
}

# `capturas_alternativas.perfil` de las paginas que pasaron por axe.
PERFIL = "axe"

MAX_EJEMPLOS_NODO = 3
MAX_TEXTO = 240

_RE_CRITERIO = re.compile(r"^wcag(\d)(\d)(\d{1,2})$")


def criterios(etiquetas: Iterable[str]) -> list[str]:
    """Los criterios de exito WCAG de una regla: ``wcag143`` -> ``1.4.3``."""
    salida = []
    for e in etiquetas or ():
        m = _RE_CRITERIO.match(str(e))
        if m:
            c = ".".join(m.groups())
            if c not in salida:
                salida.append(c)
    return salida


def nivel(etiquetas: Iterable[str]) -> str | None:
    """``A`` o ``AA`` segun las etiquetas de nivel de axe; None si no trae."""
    ets = {str(e) for e in etiquetas or ()}
    if ets & {"wcag2a", "wcag21a", "wcag22a"}:
        return "A"
    if ets & {"wcag2aa", "wcag21aa", "wcag22aa"}:
        return "AA"
    return None


def _texto(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        v = " ".join(_texto(x) for x in v)
    return str(v or "")[:MAX_TEXTO]


def _impacto(v: Any) -> str | None:
    return v if v in ORDEN_IMPACTO else None


def _regla(r: dict, con_ejemplos: bool) -> dict[str, Any] | None:
    if not isinstance(r, dict) or not r.get("id"):
        return None
    etiquetas = [str(t) for t in r.get("tags") or []]
    nodos = r.get("nodes")
    salida: dict[str, Any] = {
        "regla": str(r["id"])[:80],
        "impacto": _impacto(r.get("impact")),
        "criterios": criterios(etiquetas),
        "nivel": nivel(etiquetas),
        "ayuda": _texto(r.get("help")),
        "url_ayuda": _texto(r.get("helpUrl")),
        # El script del navegador manda `n` (todos) y solo los primeros nodos.
        "nodos": int(r["n"]) if isinstance(r.get("n"), int) else (
            len(nodos) if isinstance(nodos, list) else 0),
    }
    if con_ejemplos and isinstance(nodos, list):
        salida["ejemplos"] = [
            {"selector": _texto(n.get("target")), "html": _texto(n.get("html"))}
            for n in nodos[:MAX_EJEMPLOS_NODO] if isinstance(n, dict)
        ]
    return salida


def resumir(crudo: Any) -> dict[str, Any] | None:
    """Lo que se guarda de un `axe.run`: por regla, impacto, criterios, nodos
    y hasta tres ejemplos de las violaciones; de las incompletas, sin ejemplos.

    None si no es un resultado de axe (el script fallo o se corto): eso es una
    pagina «no evaluada», nunca una pagina sin violaciones.
    """
    if not isinstance(crudo, dict) or crudo.get("error") or "violations" not in crudo:
        return None
    # Una regla experimental de axe no se afirma aunque falle: va con las
    # incompletas (axe no las corre de serie, pero si una version las trae).
    def experimental(r) -> bool:
        return isinstance(r, dict) and "experimental" in (r.get("tags") or [])

    crudas = [r for r in crudo.get("violations") or [] if isinstance(r, dict)]
    viol = [x for x in (_regla(r, True) for r in crudas if not experimental(r)) if x]
    inc = [x for x in (_regla(r, False) for r in (crudo.get("incomplete") or [])
                                                 + [r for r in crudas if experimental(r)]) if x]
    clave = lambda x: (ORDEN_IMPACTO.get(x["impacto"], 9), -x["nodos"], x["regla"])  # noqa: E731
    viol.sort(key=clave)
    inc.sort(key=clave)
    ms = crudo.get("ms")
    return {
        "axe": str(crudo.get("version") or "")[:20] or None,
        "ms_axe": round(float(ms)) if isinstance(ms, (int, float)) and ms >= 0 else None,
        "violaciones": viol,
        "incompletas": inc,
        "reglas_que_pasan": int(crudo.get("passes") or 0),
    }


def agregar(filas: Iterable[tuple[int, str, str | None, dict | None]]) -> dict[str, Any]:
    """Junta las paginas EVALUADAS por (regla, plantilla).

    `filas`: ``(url_id, url, plantilla, medidas)`` de las capturas con
    `estado='evaluada'`. Devuelve las paginas muestreadas por plantilla, los
    grupos de violaciones (una entrada por regla y plantilla, con las paginas
    afectadas y los nodos) y los de incompletas, que no se afirman.
    """
    muestreadas: dict[str, int] = defaultdict(int)
    viol: dict[tuple[str, str], dict[str, Any]] = {}
    inc: dict[tuple[str, str], dict[str, Any]] = {}

    def sumar(destino, regla: dict, plantilla: str, url_id: int, url: str) -> None:
        g = destino.get((regla["regla"], plantilla))
        if g is None:
            g = destino[(regla["regla"], plantilla)] = {
                "regla": regla["regla"], "plantilla": plantilla,
                "impacto": regla.get("impacto"), "criterios": regla.get("criterios") or [],
                "nivel": regla.get("nivel"), "ayuda": regla.get("ayuda"),
                "url_ayuda": regla.get("url_ayuda"), "nodos": 0, "paginas": [],
            }
        # El impacto de una regla puede variar por nodo: se queda el peor.
        if ORDEN_IMPACTO.get(regla.get("impacto"), 9) < ORDEN_IMPACTO.get(g["impacto"], 9):
            g["impacto"] = regla.get("impacto")
        g["nodos"] += int(regla.get("nodos") or 0)
        g["paginas"].append({"url_id": url_id, "url": url, "nodos": int(regla.get("nodos") or 0),
                             "ejemplos": regla.get("ejemplos") or []})

    for url_id, url, plantilla, medidas in filas:
        if not isinstance(medidas, dict):
            continue
        plantilla = plantilla or "?"
        muestreadas[plantilla] += 1
        for r in medidas.get("violaciones") or []:
            if isinstance(r, dict) and r.get("regla"):
                sumar(viol, r, plantilla, url_id, url)
        for r in medidas.get("incompletas") or []:
            if isinstance(r, dict) and r.get("regla"):
                sumar(inc, r, plantilla, url_id, url)
    for g in list(viol.values()) + list(inc.values()):
        g["paginas"].sort(key=lambda p: p["url"])
    return {"muestreadas": dict(muestreadas), "violaciones": viol, "incompletas": inc}


def por_regla(grupos: dict[tuple[str, str], dict[str, Any]],
              muestreadas: dict[str, int]) -> list[dict[str, Any]]:
    """Los grupos (regla, plantilla) sumados por regla, de mas a menos grave:
    plantillas y paginas afectadas sobre las muestreadas, y nodos."""
    reglas: dict[str, dict[str, Any]] = {}
    for (regla, plantilla), g in grupos.items():
        r = reglas.setdefault(regla, {
            "regla": regla, "impacto": g["impacto"], "criterios": g["criterios"],
            "nivel": g["nivel"], "ayuda": g["ayuda"], "url_ayuda": g["url_ayuda"],
            "plantillas": [], "paginas": 0, "nodos": 0, "ejemplo": None,
        })
        if ORDEN_IMPACTO.get(g["impacto"], 9) < ORDEN_IMPACTO.get(r["impacto"], 9):
            r["impacto"] = g["impacto"]
        r["plantillas"].append({"plantilla": plantilla, "paginas": len(g["paginas"]),
                                "de": muestreadas.get(plantilla, 0)})
        r["paginas"] += len(g["paginas"])
        r["nodos"] += g["nodos"]
        if r["ejemplo"] is None or g["paginas"][0]["url"] < r["ejemplo"]:
            r["ejemplo"] = g["paginas"][0]["url"]
    salida = sorted(reglas.values(), key=lambda r: (
        ORDEN_IMPACTO.get(r["impacto"], 9), -len(r["plantillas"]), -r["paginas"], r["regla"]))
    for r in salida:
        r["plantillas"].sort(key=lambda p: (-p["paginas"], p["plantilla"]))
    return salida


def filas_evaluadas(session, job_id) -> list[tuple]:
    """``(url_id, url, plantilla, medidas)`` de las capturas evaluadas de
    paginas del censo que siguen siendo una HTML 2xx valida. La leen el
    analisis (avisos) y la API (tarjeta): las dos cuentan lo mismo."""
    from sqlalchemy import select

    from shared.models import CapturaAlternativa as C
    from shared.models import Url
    from shared.respuestas import NO_VALIDAS

    filas = session.execute(
        select(C.url_id, Url.url, C.plantilla, C.medidas, Url.status_code, Url.response_class)
        .join(Url, Url.id == C.url_id)
        .where(C.job_id == job_id, Url.job_id == job_id, C.perfil == PERFIL,
               C.estado == "evaluada")
    ).all()
    return [(u, url, p, m) for u, url, p, m, status, clase in filas
            if status and 200 <= status < 300 and clase not in NO_VALIDAS]
