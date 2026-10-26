"""Avisos llanos sobre la configuracion con la que saldria un rastreo (decision 196).

Los ensena el bloque «Así rastreará» de la interfaz (revision de UX del 25-oct,
2.4): el de la configuracion del cliente, la del perfil y el modal de Nuevo
rastreo. Dos reglas:

- **Lo que no se sabe no se dice.** El aviso del WAF solo sale si un rastreo
  anterior del cliente se PARO por un bloqueo (`jobs.finish_reason`) o tuvo
  respuestas de muro (`jobs.health`, decision 103). Sin ese rastro no se
  avisa de nada: «32 peticiones contra un sitio con WAF» seria una suposicion.
- **Best-effort.** Si algo falla al leer el historial, no hay avisos de
  historial; el resto se calcula sobre la configuracion, que ya esta validada.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

# Cuantos rastreos atras se mira. Un bloqueo de hace un ano no dice mucho de
# hoy, pero tampoco hay forma de saber cuando cambio el WAF: se dice la fecha.
HISTORIAL = 20


def _fecha(d) -> str:
    return f"{d.day} {MESES[d.month - 1]}" if d else "fecha desconocida"


def _num(n: float | int) -> str:
    """Formato espanol: 1.234 y 12,5."""
    if isinstance(n, float) and not n.is_integer():
        return f"{n:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{int(n):,}".replace(",", ".")


def _aviso(nivel: str, texto: str, campo: str | None = None) -> dict:
    return {"nivel": nivel, "texto": texto, "campo": campo}


def historial_de_bloqueos(db, cliente_id) -> dict | None:
    """El rastreo mas reciente del cliente que se paro por un bloqueo o que
    tuvo respuestas de muro. None si no hay ninguno (o si no se pudo leer)."""
    try:
        from shared.models import Job
        from shared.respuestas import MOTIVOS_BLOQUEO, RATE_LIMITED

        filas = (db.query(Job.id, Job.name, Job.created_at, Job.finish_reason,
                          Job.health, Job.config)
                 .filter(Job.cliente_id == cliente_id)
                 .order_by(Job.created_at.desc()).limit(HISTORIAL).all())
        for jid, nombre, creado, motivo, salud, config in filas:
            salud = salud if isinstance(salud, dict) else {}
            no_validas = int(salud.get("no_validas") or 0)
            parado = motivo in MOTIVOS_BLOQUEO
            if not parado and not no_validas:
                continue
            config = config if isinstance(config, dict) else {}
            return {
                "job_id": str(jid), "nombre": nombre, "fecha": creado,
                "parado": parado, "rate_limited": motivo == RATE_LIMITED,
                "no_validas": no_validas, "respuestas": int(salud.get("respuestas") or 0),
                "por_dominio": config.get("concurrent_requests_per_domain"),
                "retraso": (config.get("crawl_behavior") or {}).get("request_delay"),
            }
    except Exception:  # noqa: BLE001 - best-effort: sin historial no hay aviso
        log.warning("No se pudo leer el historial de bloqueos", exc_info=True)
    return None


def avisos_de_config(config: dict[str, Any], historial: dict | None = None) -> list[dict]:
    """Avisos sobre la configuracion RESUELTA (la de `resolver_config`).

    `historial` es lo de `historial_de_bloqueos`."""
    avisos: list[dict] = []
    por_dominio = int(config.get("concurrent_requests_per_domain") or 0)
    cb = config.get("crawl_behavior") or {}

    if historial:
        antes = historial.get("por_dominio")
        cuando = f"«{historial['nombre']}» del {_fecha(historial.get('fecha'))}"
        if historial["parado"]:
            causa = ("el sitio respondió con límites de peticiones (429)" if historial["rate_limited"]
                     else "el sitio lo bloqueó (WAF o captcha)")
            texto = f"El rastreo {cuando} se paró porque {causa}."
        else:
            texto = (f"En el rastreo {cuando}, {_num(historial['no_validas'])} de "
                     f"{_num(historial['respuestas'])} respuestas fueron un muro (WAF, captcha o 429), "
                     "aunque no llegó a pararse.")
        if isinstance(antes, int):
            comparado = ("las mismas o más" if por_dominio >= antes else "menos")
            texto += (f" Aquel salió con {_num(antes)} peticiones a la vez por dominio; este saldría con "
                      f"{_num(por_dominio)} ({comparado}).")
            nivel = "peligro" if por_dominio >= antes else "aviso"
        else:
            texto += f" Este saldría con {_num(por_dominio)} peticiones a la vez por dominio."
            nivel = "aviso"
        if historial["parado"] and not float(cb.get("request_delay") or 0):
            texto += " Bajar la concurrencia o poner una pausa entre peticiones suele bastar."
        avisos.append(_aviso(nivel, texto, "concurrent_requests_per_domain"))

    ua = str(config.get("user_agent") or "")
    if "googlebot" in ua.lower() or "bingbot" in ua.lower():
        avisos.append(_aviso("aviso", "Se presenta como un buscador desde nuestra IP: un WAF que comprueba "
                                      "los bots verá uno falso y puede bloquearlo o servirle otra cosa.",
                             "user_agent"))
    if config.get("robots_mode") == "ignore":
        avisos.append(_aviso("aviso", "No leerá robots.txt: entrará también en lo que el sitio pide que no "
                                      "se rastree.", "robots_mode"))
    elif config.get("robots_mode") == "audit":
        avisos.append(_aviso("info", "Entrará en todo y anotará lo que robots.txt habría bloqueado.",
                             "robots_mode"))
    if cb.get("parar_si_bloqueado") is False:
        avisos.append(_aviso("aviso", "Si el sitio lo bloquea, seguirá pidiendo páginas en vez de pararse.",
                             "crawl_behavior.parar_si_bloqueado"))
    if config.get("follow_external"):
        avisos.append(_aviso("aviso", "Seguirá los enlaces a otros sitios y meterá sus páginas en el censo.",
                             "follow_external"))
    if int(config.get("max_depth") if config.get("max_depth") is not None else 1) == 0:
        avisos.append(_aviso("info", "Modo lista: solo las semillas, sin seguir enlaces.", "max_depth"))
    elif config.get("max_urls") is None:
        horas = cb.get("max_runtime_hours")
        avisos.append(_aviso("info", "Sin máximo de URLs: rastreará hasta agotar el sitio"
                                     + (f" (o hasta {_num(horas)} horas)." if horas else "."), "max_urls"))
    return avisos
