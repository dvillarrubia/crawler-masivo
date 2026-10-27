"""Trampas de rastreo: plantillas que generan URLs sin fin (#14, decision 214).

La parte determinista de #14, sobre lo ya rastreado (re-analizable, no toca
el rastreo en caliente). Un aviso por trampa, agrupado: UNA fila por (host,
plantilla, tipo) anclada en un ejemplo, con cuantas URLs del censo genera, que
parte del censo se lleva y ejemplos (decisiones 36 y 53). Lo que mide es lo
que el rastreo YA gasto; que el sitio tenga mas, no se afirma.

- ``calendario_infinito``: la misma ruta con fechas (en la ruta o en la query)
  que van mas alla de lo razonable (mas de dos anos hacia delante o antes de
  2000) o con muchas fechas distintas. Un calendario con «mes siguiente» se
  rastrea hasta el tope.
- ``parametro_de_sesion``: la misma pagina con un identificador de sesion en la
  URL (`sid`, `PHPSESSID`, `jsessionid`...): una copia por visita.
- ``facetas_combinatorias``: la misma ruta con muchas combinaciones distintas
  de parametros de filtro, o con los mismos en distinto orden.
- ``busqueda_interna_rastreable``: paginas de resultados de busqueda (`?s=`,
  `?q=`, `/buscar`) que el rastreo encontro por enlaces.
- ``segmento_repetido``: rutas con el mismo tramo tres veces o mas, o con una
  secuencia que se repite (`/a/b/a/b/`): casi siempre un enlace relativo mal
  hecho que se resuelve contra si mismo.

Best-effort entero (decision 40). Casa entre censos por `group_key` (host,
plantilla y tipo), no por la URL del ejemplo, que cambia de un censo a otro
(`shared.comparacion.ANCLADOS_POR_GRUPO`).
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import select

logger = logging.getLogger(__name__)

PARAMETROS_SESION = frozenset({"sid", "sessionid", "session_id", "session", "phpsessid", "jsessionid",
                               "aspsessionid", "cfid", "cftoken", "s_id", "sess", "zenid", "osCsid".lower()})
PARAMETROS_BUSQUEDA = frozenset({"s", "q", "query", "search", "buscar", "busqueda", "keyword", "keywords", "k"})
RUTAS_BUSQUEDA = re.compile(r"/(search|buscar|busqueda|buscador|resultados|catalogsearch)(/|$)", re.I)
# Parametros que NO son filtros: paginacion y seguimiento (los de seguimiento
# ya los avisa su propia regla).
NO_FILTRO = frozenset({"page", "p", "pagina", "pg", "utm_source", "utm_medium", "utm_campaign", "utm_term",
                       "utm_content", "gclid", "fbclid", "lang", "hl"})
# Una fecha con separadores (`2026/10`, `2026-10-27`). Sin separador solo en un
# parametro con nombre de fecha: en la ruta, `/192401-daniel-brieba` (el id de
# un autor en Penguin) se leia como enero de 1924 y salian 339 «calendarios».
_FECHA = re.compile(r"(?<![\d])((?:19|20|21)\d{2})[-/](0[1-9]|1[0-2])(?:[-/](0[1-9]|[12]\d|3[01]))?(?![\d])")
_FECHA_COMPACTA = re.compile(r"^((?:19|20|21)\d{2})(0[1-9]|1[0-2])((?:0[1-9]|[12]\d|3[01]))?$")
PARAMETROS_FECHA = frozenset({"date", "fecha", "day", "dia", "month", "mes", "year", "ano", "anio", "week", "semana",
                              "from", "to", "desde", "hasta", "start", "end", "cal", "calendar", "ym", "d"})

MIN_URLS = 20               # por debajo, una plantilla con pocas variantes no es una trampa
MIN_FECHAS_DISTINTAS = 60   # mas de dos meses de dias, o cinco anos de meses
MIN_COMBINACIONES = 50
ANOS_HACIA_DELANTE = 2


# `shared.comparacion.ANCLADOS_POR_GRUPO` repite la lista (la API no importa
# `analysis/`); un test comprueba que coinciden.
TIPOS_ANCLADOS_POR_GRUPO = ("trampa_de_rastreo",)


def avisar_sin_tumbar(an) -> None:
    pendientes = list(getattr(an, "_pending_issues", []) or [])
    try:
        with an.session.begin_nested():
            avisar(an)
    except Exception:
        an._pending_issues = pendientes
        logger.exception("Trampas de rastreo del job %s: fallo; el analisis sigue", an.job_id)


def _grupo(host: str, plantilla: str, tipo: str) -> str:
    return hashlib.sha256(f"{host}|{plantilla}|{tipo}".encode()).hexdigest()[:64]


def tramos_repetidos(path: str) -> bool:
    t = [x for x in path.lower().split("/") if x]
    if len(t) < 3:
        return False
    from collections import Counter

    if max(Counter(t).values()) >= 3:
        return True
    # una secuencia de 1-3 tramos repetida justo detras de si misma
    for n in (1, 2, 3):
        for i in range(len(t) - 2 * n + 1):
            if t[i:i + n] == t[i + n:i + 2 * n] and (n > 1 or len(t[i]) > 2):
                return True
    return False


def fechas_de(path: str, params: list[tuple[str, str]] | None = None) -> list[tuple[int, int]]:
    """(ano, mes) de las fechas de la ruta (con separadores) y de los
    parametros con nombre de fecha (tambien compactas: `?date=20261027`)."""
    salida = [(int(m.group(1)), int(m.group(2))) for m in _FECHA.finditer(path)]
    for k, v in params or []:
        if k.lower() not in PARAMETROS_FECHA:
            continue
        m = _FECHA_COMPACTA.match(v.strip()) or _FECHA.search(v)
        if m:
            salida.append((int(m.group(1)), int(m.group(2))))
    return salida


def detectar(urls: list[tuple[int, str]], *, ahora: datetime | None = None) -> list[dict[str, Any]]:
    """Las trampas de un censo: una por (host, plantilla, tipo). `urls`: (id, url)."""
    from shared.plantillas import firma_de_ruta

    ahora = ahora or datetime.now(timezone.utc)
    total = len(urls) or 1
    grupos: dict[tuple[str, str, str], list[tuple[int, str]]] = defaultdict(list)
    combinaciones: dict[tuple[str, str], set] = defaultdict(set)
    ordenes: dict[tuple[str, str], set] = defaultdict(set)
    fechas: dict[tuple[str, str], set] = defaultdict(set)
    raras: dict[tuple[str, str], int] = defaultdict(int)
    for uid, url in urls:
        try:
            p = urlsplit(url)
        except ValueError:
            continue
        host = (p.hostname or "").lower()
        path = p.path or "/"
        params = parse_qsl(p.query, keep_blank_values=True)
        nombres = [k.lower() for k, _ in params]
        plantilla = firma_de_ruta(path)
        # Sesion: el parametro, o el `;jsessionid=` de la ruta.
        if any(n in PARAMETROS_SESION for n in nombres) or ";jsessionid=" in path.lower():
            grupos[(host, plantilla, "parametro_de_sesion")].append((uid, url))
        if any(n in PARAMETROS_BUSQUEDA for n in nombres) or RUTAS_BUSQUEDA.search(path):
            grupos[(host, plantilla, "busqueda_interna_rastreable")].append((uid, url))
        if tramos_repetidos(path):
            grupos[(host, plantilla, "segmento_repetido")].append((uid, url))
        f = fechas_de(path, params)
        if f:
            k = (host, plantilla)
            grupos[(host, plantilla, "calendario_infinito")].append((uid, url))
            for ano, mes in f:
                fechas[k].add((ano, mes, url))
                if ano > ahora.year + ANOS_HACIA_DELANTE or ano < 2000:
                    raras[k] += 1
        filtros = tuple(n for n in nombres if n not in NO_FILTRO and n not in PARAMETROS_SESION
                        and n not in PARAMETROS_BUSQUEDA)
        if filtros:
            k = (host, path)
            combinaciones[k].add(tuple(sorted(set(filtros))) + tuple(v for kk, v in params if kk.lower() in filtros))
            ordenes[k].add(filtros)
            grupos[(host, path, "facetas_combinatorias")].append((uid, url))

    salida = []
    for (host, plantilla, tipo), miembros in grupos.items():
        n = len(miembros)
        motivo: dict[str, Any] = {}
        if tipo == "calendario_infinito":
            distintas = len({(a, m) for a, m, _ in fechas[(host, plantilla)]})
            fuera = raras[(host, plantilla)]
            if n < MIN_URLS or not (fuera or distintas >= MIN_FECHAS_DISTINTAS):
                continue
            motivo = {"fechas_distintas": distintas, "fuera_de_rango": fuera}
        elif tipo == "facetas_combinatorias":
            k = (host, plantilla)
            combos = len(combinaciones[k])
            mismo_set_otro_orden = len(ordenes[k]) > len({tuple(sorted(o)) for o in ordenes[k]})
            if n < MIN_URLS or (combos < MIN_COMBINACIONES and not mismo_set_otro_orden):
                continue
            motivo = {"combinaciones": combos, "mismo_filtro_en_otro_orden": mismo_set_otro_orden}
        elif tipo == "segmento_repetido":
            if n < 3:
                continue
        elif tipo in ("parametro_de_sesion", "busqueda_interna_rastreable"):
            if n < 2:
                continue
        parte = n / total
        miembros.sort()
        salida.append({
            "tipo": tipo, "host": host, "plantilla": plantilla, "paginas_afectadas": n,
            "parte_del_censo": round(parte, 4), "ancla": miembros[0][0],
            "ejemplos": [u for _, u in miembros[:5]], "severidad": "warning" if parte >= 0.05 or n >= 500 else "info",
            **motivo,
        })
    return salida


def avisar(an) -> int:
    from shared.models import Url

    an._flush_issues()
    urls = an.session.execute(
        select(Url.id, Url.url).where(Url.job_id == an.job_id, Url.is_internal.is_(True))).all()
    trampas = detectar([(u.id, u.url) for u in urls])
    for t in trampas:
        detalle = {k: v for k, v in t.items() if k not in ("ancla", "severidad", "tipo")}
        an._add_issue(t["ancla"], "trampa_de_rastreo", t["severidad"], {**detalle, "trampa": t["tipo"]},
                      group_key=_grupo(t["host"], t["plantilla"], t["tipo"]))
    an._flush_issues()
    if trampas:
        logger.info("Trampas de rastreo del job %s: %d", an.job_id, len(trampas))
    return len(trampas)
