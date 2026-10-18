"""El robots.txt guardado de cada host, listo para preguntarle por un bot.

Lo usa el analizador (decisiones 112 y 113): `robots_txt_inaccesible` por host,
`urls.bloqueada_googlebot` por URL y `robots_difiere_para_googlebot` por host.
La regla de que es un 5xx y como se evalua vive en `shared/robots_txt.py`; aqui
solo se carga lo del job y se cachea un parser por host.

Sin filas en `robots_txt` (un censo anterior, o un host por encima del tope) no
se afirma nada: el veredicto es None y la indexabilidad sigue como hasta ahora.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from shared.models import RobotsTxt
from shared.robots_txt import (
    INACCESIBLE,
    NO_EVALUADO,
    PROCESADO,
    SIN_RESTRICCIONES,
    EvaluadorRobots,
    estado,
    origen,
)

logger = logging.getLogger(__name__)


def estado_de_fila(fila: Any) -> str:
    """El estado de la PRIMERA descarga, que es con la que se rastreo."""
    return estado(fila.status_code, fila.error, fila.response_class)


def estado_de_comprobacion(fila: Any) -> str | None:
    """El de la segunda peticion al cerrar, si la hubo."""
    c = fila.comprobacion or None
    if not isinstance(c, dict):
        return None
    return estado(c.get("status_code"), c.get("error"), c.get("response_class"))


def estado_evaluable(fila: Any) -> str:
    """Con que se evalua el robots.txt de la fila.

    Si la primera descarga fallo y la comprobacion al cerrar respondio, se
    evalua con lo que respondio: es lo mas parecido a la copia que Google
    guarda en cache de un robots.txt que responde a ratos. Si fallo las dos
    veces, no se evalua (decision 112: Google para el sitio, pero no sabemos
    si tiene copia ni cuanto dura).
    """
    primero = estado_de_fila(fila)
    if primero != INACCESIBLE:
        return primero
    segundo = estado_de_comprobacion(fila)
    if segundo == PROCESADO and fila.cuerpo is not None:
        return PROCESADO
    if segundo == SIN_RESTRICCIONES:
        return SIN_RESTRICCIONES
    return INACCESIBLE


class RobotsDelJob:
    """Las filas de `robots_txt` de un job, por origen (esquema://host:puerto)."""

    def __init__(self, session, job_id) -> None:
        self.filas: dict[str, Any] = {}
        for fila in session.execute(
                select(RobotsTxt).where(RobotsTxt.job_id == job_id)
                .order_by(RobotsTxt.id)).scalars():
            o = origen(fila.url)
            if o is not None and o not in self.filas:
                self.filas[o] = fila
        self._evaluadores: dict[str, EvaluadorRobots | None] = {}

    def __bool__(self) -> bool:
        return bool(self.filas)

    def evaluador(self, o: str) -> EvaluadorRobots | None:
        if o in self._evaluadores:
            return self._evaluadores[o]
        fila = self.filas.get(o)
        ev = None
        if fila is not None:
            est = estado_evaluable(fila)
            if est not in (INACCESIBLE, NO_EVALUADO):
                try:
                    ev = EvaluadorRobots(est, fila.cuerpo)
                except Exception:  # noqa: BLE001 -- sin Protego no se afirma nada
                    logger.warning("No se pudo evaluar el robots.txt de %s", o, exc_info=True)
                    ev = None
        self._evaluadores[o] = ev
        return ev

    def veredicto(self, url: str, bot: str) -> tuple[str | None, bool | None]:
        """``(origen, puede pedirla)``; None = no evaluado."""
        o = origen(url)
        if o is None or o not in self.filas:
            return o, None
        ev = self.evaluador(o)
        if ev is None:
            return o, None
        try:
            return o, ev.permitido(url, bot)
        except Exception:  # noqa: BLE001
            return o, None


# -- Que bloquea cada bot por host (decision 129) ------------------------------

# URLs por plantilla y plantillas por host de la muestra. Lo bastante para ver
# si un bot tiene cerrada una seccion entera, sin evaluar 500.000 URLs x 12
# bots: con la home y tres por plantilla, una seccion bloqueada se ve igual.
MUESTRA_POR_PLANTILLA = 3
MAX_PLANTILLAS = 200
MAX_EJEMPLOS = 5


class MuestraPorPlantilla:
    """Las URLs menores de cada plantilla, por origen.

    Se alimenta en la misma pasada que evalua Googlebot, asi que no relee
    `urls`. Las de URL menor —no las de id mas bajo— para que dos analisis del
    mismo censo cojan la misma muestra aunque las filas lleguen en otro orden,
    y tambien dos censos iguales rastreados en otro orden: el id sigue al orden
    de rastreo (decision 88).

    La plantilla lleva los NOMBRES de los parametros (`/:slug ?cat&price`):
    robots.txt bloquea mucho por parametro (`Disallow: /*?cat=`), y con la
    ruta sola la variante con parametros caia en la misma plantilla que la
    limpia y casi nunca entraba en la muestra (medido en Druni: 2.057 URLs
    bloqueadas para Googlebot en el censo y 0 en la muestra por ruta). Si hay
    mas de `MAX_PLANTILLAS`, se evaluan las que mas URLs tienen.
    """

    def __init__(self, reglas_plantilla=None) -> None:
        from shared.plantillas import reglas_de_plantilla

        self._reglas = reglas_de_plantilla(reglas_plantilla)
        self._urls: dict[str, dict[str, list[tuple[int, str]]]] = {}
        self._n: dict[str, dict[str, int]] = {}

    def anadir(self, o: str, url_id: int, url: str) -> None:
        from urllib.parse import parse_qsl, urlsplit

        from shared.plantillas import plantilla_de
        from shared.robots_txt import es_url_de_robots

        # `/robots.txt` siempre se puede pedir: en la muestra, un sitio
        # cerrado entero a un bot saldria «parte» solo por enlazarlo.
        if es_url_de_robots(url):
            return
        try:
            partes = urlsplit(url)
        except ValueError:
            return
        p = plantilla_de(partes.path or "/", self._reglas)
        if partes.query:
            nombres = sorted({k for k, _ in parse_qsl(partes.query, keep_blank_values=True)})
            p += " ?" + "&".join(nombres[:5])
        cuenta = self._n.setdefault(o, {})
        cuenta[p] = cuenta.get(p, 0) + 1
        lista = self._urls.setdefault(o, {}).setdefault(p, [])
        # Las de URL menor, no las de id menor: el id sigue al orden de
        # rastreo, y con otra muestra el mismo censo podia pasar de «todo» a
        # «parte» y emitir o no `bot_busqueda_ia_bloqueado` (decision 88).
        if len(lista) < MUESTRA_POR_PLANTILLA:
            lista.append((url_id, url))
            lista.sort(key=lambda e: (e[1], e[0]))
        elif (url, url_id) < (lista[-1][1], lista[-1][0]):
            lista[-1] = (url_id, url)
            lista.sort(key=lambda e: (e[1], e[0]))

    def de(self, o: str) -> dict[str, list[tuple[int, str]]]:
        """Las plantillas del origen que se evaluan: las `MAX_PLANTILLAS` con mas URLs."""
        cuenta = self._n.get(o, {})
        elegidas = sorted(cuenta, key=lambda p: (-cuenta[p], p))[:MAX_PLANTILLAS]
        urls = self._urls.get(o, {})
        return {p: urls[p] for p in elegidas}

    def total(self, o: str) -> int:
        return len(self._n.get(o, {}))


def _regla(ev: EvaluadorRobots, url: str, token: str, permitida: bool) -> dict | None:
    """La linea que decide, solo si dice lo mismo que Protego."""
    from shared.robots_regla import regla_que_decide

    if ev.estado != PROCESADO or ev._texto is None:
        return None
    try:
        r = regla_que_decide(ev._texto, url, token)
    except Exception:  # noqa: BLE001 -- la linea es un extra, no el veredicto
        return None
    if r["permitida"] is not permitida or r["linea"] is None:
        return None
    return {"linea": r["linea"], "texto": r["texto"], "grupo": r["grupo"]}


def evaluar_bots(ev: EvaluadorRobots, o: str,
                 plantillas: dict[str, list[tuple[int, str]]],
                 plantillas_total: int | None = None) -> dict[str, dict]:
    """Por bot: la home, la muestra y el alcance del bloqueo en un host.

    `alcance`: `todo` (la home y todas las URLs de la muestra bloqueadas),
    `parte` (alguna), `nada` o None (no evaluable). El veredicto es el de
    Protego (`EvaluadorRobots`); la linea, la de `regla_que_decide` cuando
    coincide.
    """
    from shared.robots_txt import BOTS_INFO

    home = o + "/"
    salida: dict[str, dict] = {}
    for info in BOTS_INFO:
        bot, token = info["bot"], info["token"]
        p_home = ev.permitido(home, bot)
        if p_home is None:
            salida[bot] = {"alcance": None}
            continue
        evaluadas = bloqueadas = 0
        plantillas_bloqueadas: list[str] = []
        ejemplos: list[dict] = []
        for nombre, urls in sorted(plantillas.items()):
            bloq_plantilla = 0
            for _id, url in urls:
                permitida = ev.permitido(url, bot)
                if permitida is None:
                    continue
                evaluadas += 1
                if not permitida:
                    bloqueadas += 1
                    bloq_plantilla += 1
                    if len(ejemplos) < MAX_EJEMPLOS:
                        ejemplos.append({"url": url, "regla": _regla(ev, url, token, False)})
            if bloq_plantilla and bloq_plantilla == len(urls):
                plantillas_bloqueadas.append(nombre)
        home_bloqueada = not p_home
        if home_bloqueada and bloqueadas == evaluadas:
            alcance = "todo"
        elif home_bloqueada or bloqueadas:
            alcance = "parte"
        else:
            alcance = "nada"
        salida[bot] = {
            "alcance": alcance,
            "home_bloqueada": home_bloqueada,
            "regla_home": _regla(ev, home, token, p_home),
            "evaluadas": evaluadas,
            "bloqueadas": bloqueadas,
            "plantillas": len(plantillas),
            "plantillas_total": plantillas_total if plantillas_total is not None else len(plantillas),
            "plantillas_bloqueadas": plantillas_bloqueadas[:20],
            "n_plantillas_bloqueadas": len(plantillas_bloqueadas),
            "ejemplos": ejemplos,
        }
    return salida
