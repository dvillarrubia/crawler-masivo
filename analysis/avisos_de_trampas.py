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
                               "aspsessionid", "cfid", "cftoken", "s_id", "sess", "zenid", "oscsid"})
# `s` y `q` son los de WordPress y casi todos; `k` era demasiado generico.
PARAMETROS_BUSQUEDA = frozenset({"s", "q", "query", "search", "buscar", "busqueda", "keyword", "keywords"})
RUTAS_BUSQUEDA = re.compile(r"/(search|buscar|busqueda|buscador|resultados|catalogsearch)(/|$)", re.I)
# Seguimiento de campañas: no son filtros ni generan paginas distintas. No
# hay otra lista en el repo (`utm_en_enlace_interno` solo mira `utm_`).
SEGUIMIENTO = frozenset({"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
                         "gclid", "gbraid", "wbraid", "gad_source", "gad_campaignid", "dclid", "fbclid",
                         "msclkid", "yclid", "_gl", "_ga", "mc_cid", "mc_eid", "srsltid", "igshid", "ref"})
# Lo que tampoco es un filtro: paginacion, idioma, moneda, variante de una
# ficha (una por color o talla ES la pagina) y las fechas de una reserva.
NO_FILTRO = SEGUIMIENTO | frozenset({"page", "p", "pagina", "pg", "lang", "hl", "language", "idioma", "currency",
                                     "moneda", "variant", "checkin", "checkout", "adults", "children", "rooms"})
# Una fecha con separadores (`2026/10`, `2026-10-27`). Sin separador solo en un
# parametro de fecha: en la ruta, `/192401-daniel-brieba` (el id de un autor en
# Penguin) se leia como enero de 1924.
_FECHA = re.compile(r"(?<![\d])((?:19|20|21)\d{2})[-/](0?[1-9]|1[0-2])(?:[-/](0?[1-9]|[12]\d|3[01]))?(?![\d])")
_FECHA_COMPACTA = re.compile(r"^((?:19|20|21)\d{2})(0[1-9]|1[0-2])((?:0[1-9]|[12]\d|3[01]))?$")
_PARAM_FECHA = re.compile(r"date|fecha|^d$|^day$|^dia$|^cal|^ym$|^week|^semana|^from$|^to$|^desde$|^hasta$|^start|^end")
_PARAM_ANO = re.compile(r"^(year|ano|anio|any|y|yr)$")
_PARAM_MES = re.compile(r"^(month|mes|mon|m)$")

MIN_URLS = 20               # por debajo, una plantilla con pocas variantes no es una trampa
PARTE_FUTURA = 0.10         # un calendario: al menos esta parte de sus URLs va mas alla de lo posible
MIN_COMBINACIONES = 50
ANOS_HACIA_DELANTE = 2

# Una clasificacion por URL, en este orden: una URL de sesion con ?date= es de
# sesion, no dos avisos (decision 36).
TIPOS = ("parametro_de_sesion", "entidad_html_en_url", "calendario_infinito",
         "busqueda_interna_rastreable", "facetas_combinatorias", "segmento_repetido")


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


def repeticion(path: str) -> str | None:
    """La secuencia de 1-3 tramos que se repite SEGUIDA (`/a/b/a/b/`,
    `/es/es/`), o None. Los tramos numericos no cuentan (`/tienda/1/1/`)."""
    t = [x for x in path.lower().split("/") if x]
    for n in (1, 2, 3):
        for i in range(len(t) - 2 * n + 1):
            trozo = t[i:i + n]
            if trozo == t[i + n:i + 2 * n] and not all(x.isdigit() for x in trozo):
                return "/" + "/".join(trozo) + "/"
    return None


def tramos_repetidos(path: str) -> bool:
    return repeticion(path) is not None


def fechas_de(path: str, params: list[tuple[str, str]] | None = None) -> list[tuple[int, int]]:
    """(ano, mes) de las fechas de la ruta (con separadores) y de los
    parametros de fecha: compactas (`?date=20261027`), con separadores, o el
    ano y el mes en parametros sueltos (`?mes=11&anio=2031`)."""
    salida = [(int(m.group(1)), int(m.group(2))) for m in _FECHA.finditer(path)]
    ano = mes = None
    for k, v in params or []:
        k, v = k.lower(), v.strip()
        if _PARAM_ANO.match(k) and re.fullmatch(r"(19|20|21)\d{2}", v):
            ano = int(v)
        elif _PARAM_MES.match(k) and re.fullmatch(r"0?[1-9]|1[0-2]", v):
            mes = int(v)
        elif _PARAM_FECHA.search(k):
            m = _FECHA_COMPACTA.match(v) or _FECHA.search(v)
            if m:
                salida.append((int(m.group(1)), int(m.group(2))))
    if ano is not None:
        salida.append((ano, mes or 1))
    return salida


def _es_parametro_de_fecha(k: str) -> bool:
    return bool(_PARAM_FECHA.search(k) or _PARAM_ANO.match(k) or _PARAM_MES.match(k))


def _entidad(nombre_crudo: str) -> bool:
    """`;page`, `amp;page`, `amp;amp;page`: un `&amp;` del HTML que se copio a la URL."""
    from urllib.parse import unquote

    n = unquote(nombre_crudo).lower()
    return n.startswith(";") or n.startswith("amp;")


def _clasificar(path: str, params: list[tuple[str, str]], ahora: datetime) -> tuple[str, Any] | None:
    nombres = [k.lower() for k, _ in params]
    if any(n in PARAMETROS_SESION for n in nombres) or re.search(r";(jsessionid|phpsessid)=", path, re.I):
        return "parametro_de_sesion", None
    if any(_entidad(k) for k, _ in params):
        return "entidad_html_en_url", None
    f = fechas_de(path, params)
    if f:
        futura = any(a > ahora.year + ANOS_HACIA_DELANTE for a, _ in f)
        return "calendario_infinito", futura
    if (any(n in PARAMETROS_BUSQUEDA and len(v.strip()) >= 3 for n, (_, v) in zip(nombres, params))
            or RUTAS_BUSQUEDA.search(path)):
        return "busqueda_interna_rastreable", None
    filtros = sorted((k.lower(), v) for k, v in params
                     if k.lower() not in NO_FILTRO and not _es_parametro_de_fecha(k.lower()))
    if filtros:
        return "facetas_combinatorias", (tuple(filtros), tuple(k for k, _ in params
                                                              if k.lower() not in NO_FILTRO))
    r = repeticion(path)
    if r:
        return "segmento_repetido", r
    return None


def detectar(urls: list[tuple], *, ahora: datetime | None = None) -> list[dict[str, Any]]:
    """Las trampas de un censo: una por (host, plantilla, tipo). `urls`: (id, url)."""
    from shared.plantillas import firma_de_ruta

    ahora = ahora or datetime.now(timezone.utc)
    total = len(urls) or 1
    grupos: dict[tuple[str, str, str], list[tuple[int, str]]] = defaultdict(list)
    futuras: dict[tuple[str, str, str], int] = defaultdict(int)
    combinaciones: dict[tuple[str, str, str], set] = defaultdict(set)
    ordenes: dict[tuple[str, str, str], set] = defaultdict(set)
    for u in urls:
        uid, url = u[0], u[1]
        try:
            p = urlsplit(url)
        except ValueError:
            continue
        host = (p.hostname or "").lower()
        path = p.path or "/"
        params = parse_qsl(p.query, keep_blank_values=True)
        c = _clasificar(path, params, ahora)
        if c is None:
            continue
        tipo, dato = c
        # El bucle relativo se agrupa por lo que se repite: la firma lleva la
        # profundidad y un bucle que crece daba un grupo por nivel.
        plantilla = dato if tipo == "segmento_repetido" else firma_de_ruta(path)
        k = (host, plantilla, tipo)
        grupos[k].append((uid, url))
        if tipo == "calendario_infinito" and dato:
            futuras[k] += 1
        elif tipo == "facetas_combinatorias":
            # Por ruta: 30 categorias con las mismas 30 combinaciones son 900
            # paginas distintas, no 30.
            combinaciones[k].add((path, dato[0]))
            ordenes[k].add(dato[1])

    salida = []
    for (host, plantilla, tipo), miembros in grupos.items():
        n = len(miembros)
        motivo: dict[str, Any] = {}
        k = (host, plantilla, tipo)
        if tipo == "calendario_infinito":
            # Un blog o una hemeroteca tienen muchas fechas, todas posibles; un
            # calendario con «mes siguiente» se va años hacia delante.
            if n < MIN_URLS or futuras[k] < PARTE_FUTURA * n:
                continue
            motivo = {"fechas_futuras": futuras[k]}
        elif tipo == "facetas_combinatorias":
            combos = len(combinaciones[k])
            otro_orden = len(ordenes[k]) > len({tuple(sorted(o)) for o in ordenes[k]})
            if n < MIN_URLS or (combos < MIN_COMBINACIONES and not otro_orden):
                continue
            motivo = {"combinaciones": combos, "mismo_filtro_en_otro_orden": otro_orden}
        elif tipo == "segmento_repetido" and n < 3:
            continue
        elif tipo in ("parametro_de_sesion", "busqueda_interna_rastreable", "entidad_html_en_url") and n < 2:
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
    # Lo bloqueado en robots.txt no gasta rastreo de Google: no es su trampa.
    urls = an.session.execute(
        select(Url.id, Url.url).where(Url.job_id == an.job_id, Url.is_internal.is_(True),
                                      Url.blocked_by_robots.isnot(True))).all()
    trampas = detectar([(u.id, u.url) for u in urls])
    for t in trampas:
        detalle = {k: v for k, v in t.items() if k not in ("ancla", "severidad", "tipo")}
        an._add_issue(t["ancla"], "trampa_de_rastreo", t["severidad"], {**detalle, "trampa": t["tipo"]},
                      group_key=_grupo(t["host"], t["plantilla"], t["tipo"]))
    an._flush_issues()
    if trampas:
        logger.info("Trampas de rastreo del job %s: %d", an.job_id, len(trampas))
    return len(trampas)
