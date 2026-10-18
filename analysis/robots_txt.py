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
