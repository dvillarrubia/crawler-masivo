"""robots.txt del censo: que respondio cada host y que puede pedir cada bot.

Lo comparten el rastreo, que guarda la respuesta (`RobotsTxtRegistroMiddleware`,
tabla `robots_txt`), y el analisis, que la evalua (decisiones 112 y 113). Vive
en ``shared`` por la regla de siempre (23, 39): la imagen del analisis solo
copia ``shared/`` y ``analysis/``, y el «esto es un 5xx de robots.txt» tiene que
ser el mismo en los dos lados.

**Como lee Google un robots.txt** (documentacion «How Google interprets the
robots.txt specification», RFC 9309):

- 2xx: se procesa, hasta **500 KiB**; lo que pasa de ahi se ignora.
- 3xx: sigue al menos cinco saltos; si no llega, como un 404.
- **4xx (salvo 429): como si no hubiera robots.txt**, todo permitido. 401 y
  403 incluidos.
- **5xx y 429: el sitio entero bloqueado** mientras dure; pasados 30 dias usa
  la ultima copia en cache, y sin copia, sin restricciones.
- **Errores de red y timeouts cuentan como 5xx.**

Scrapy no distingue nada de eso: monta el parser con el cuerpo sea cual sea el
codigo (Protego, sobre el HTML de un 503, lo permite todo) y con una excepcion
deja pasar todo. Aqui se clasifica con el criterio de Google; el rastreo NO
cambia lo que pide por ello (decision 112).

Sin dependencias en lo que importa la API: Protego se importa solo al evaluar.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from shared.respuestas import NO_VALIDAS, RATE_LIMITED

# Lo que Google lee de un robots.txt. El resto lo ignora, y Protego no: se
# evalua con el cuerpo recortado y se guarda el tamano real.
LIMITE_BYTES = 500 * 1024

# Hosts por job cuyo robots.txt se guarda. Un `crawl_subdomains` en un multisite
# (blogs.uoc.edu) puede tocar cientos; con el tope, lo que pase de ahi queda
# «no evaluado», nunca «sin robots.txt».
MAX_HOSTS_POR_JOB = 200

# Bots con los que se evalua el robots.txt guardado: clave -> token del grupo
# `User-agent`. El TOKEN, no una cadena de UA completa: Protego busca el token
# del grupo dentro de lo que se le pasa, y con `Mozilla/5.0 (compatible;
# Googlebot/2.1...)` casaria el grupo `Mozilla` (decision 98). La clave es la de
# `shared/indexabilidad.BOT_POR_DEFECTO`. Los de IA (GPTBot, ClaudeBot...) son
# la Ola 7.3: entran aqui y en `robots_txt.evaluacion`, sin columna por URL.
BOTS = {"googlebot": "Googlebot"}

# Estados de un robots.txt, con el criterio de Google.
PROCESADO = "procesado"                # 2xx: se aplican sus reglas
SIN_RESTRICCIONES = "sin_restricciones"  # 4xx (no 429) o 3xx sin destino: todo permitido
INACCESIBLE = "inaccesible"            # 5xx, 429 o error de red: Google para el sitio
NO_EVALUADO = "no_evaluado"            # muro delante, peticion descartada o sin dato

# Errores de la descarga que NO son de red: la peticion la descarto el propio
# rastreador (un middleware, el tope de redirecciones). No dicen nada del sitio.
_ERRORES_NO_DE_RED = ("IgnoreRequest",)


def origen(url: str | None) -> str | None:
    """``esquema://host[:puerto]`` en minusculas: a quien aplica un robots.txt.

    Google trata aparte cada protocolo, host y puerto: el robots.txt de
    ``https://x.com`` no vale para ``http://x.com`` ni para ``www.x.com``.
    """
    if not url:
        return None
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return None
    try:
        puerto = p.port
    except ValueError:
        return None
    host = p.hostname.lower()
    if puerto is not None and puerto != {"http": 80, "https": 443}[p.scheme.lower()]:
        host = f"{host}:{puerto}"
    return f"{p.scheme.lower()}://{host}"


def es_url_de_robots(url: str | None) -> bool:
    """La URL es un ``/robots.txt`` de raiz, sin parametros."""
    if not url:
        return False
    try:
        p = urlsplit(url)
    except ValueError:
        return False
    return (p.scheme.lower() in ("http", "https") and p.path == "/robots.txt"
            and not p.query)


def estado(status_code: int | None, error: str | None = None,
           response_class: str | None = None) -> str:
    """Que hace Google con esta respuesta de robots.txt (ver el docstring).

    - Un **429** es `inaccesible` aunque sea `rate_limited`: para Google es
      como un 5xx. Que el limite sea solo para nuestra IP lo decide la
      severidad del aviso, no esto (`limitado`).
    - Un **muro** (desafio de WAF, captcha, cuerpo binario) es `no_evaluado`:
      no se ha visto el robots.txt, y un 403 de Cloudflare no es el 4xx del
      sitio que Google leeria como «todo permitido» (decision 103).
    - Una excepcion de red es `inaccesible`; una peticion que descarto el
      propio rastreador, `no_evaluado`.
    """
    if error:
        if any(error.startswith(e) for e in _ERRORES_NO_DE_RED):
            return NO_EVALUADO
        return INACCESIBLE
    if status_code is None:
        return NO_EVALUADO
    if status_code == 429:
        return INACCESIBLE
    if response_class in NO_VALIDAS:
        return NO_EVALUADO
    if 500 <= status_code < 600:
        return INACCESIBLE
    if 200 <= status_code < 300:
        return PROCESADO
    if 300 <= status_code < 500:
        return SIN_RESTRICCIONES
    return NO_EVALUADO


def limitado(status_code: int | None, response_class: str | None) -> bool:
    """Un 429 (o un muro de rate limit): probablemente el limite es para
    nuestra IP, y Googlebot puede no recibirlo."""
    return status_code == 429 or response_class == RATE_LIMITED


def recortar(cuerpo: bytes | None) -> tuple[bytes, bool]:
    """``(lo que Google lee, si se recorto)``."""
    cuerpo = cuerpo or b""
    if len(cuerpo) > LIMITE_BYTES:
        return cuerpo[:LIMITE_BYTES], True
    return cuerpo, False


def texto(cuerpo: bytes) -> str:
    """El cuerpo como texto para la columna: sin NUL, que Postgres no admite."""
    return cuerpo.decode("utf-8", "replace").replace("\x00", "")


def fila_de_respuesta(url: str, *, status_code: int | None = None,
                      cabeceras: dict[str, str] | None = None,
                      cuerpo: bytes | None = None, error: str | None = None,
                      intentos: int = 1, url_final: str | None = None,
                      fecha: datetime | None = None) -> dict[str, Any]:
    """La fila de `robots_txt` de una descarga (sin `job_id` ni `id`).

    El cuerpo solo se guarda de un 2xx que no es un muro: es lo unico que se
    evalua, y el HTML de un 503 no le sirve a nadie. El hash y los bytes son del
    cuerpo ENTERO (para ver si cambio entre censos, #36); el texto, recortado a
    lo que lee Google.
    """
    response_class = None
    if status_code is not None:
        try:
            from shared.respuestas import clasificar_respuesta

            response_class, _ = clasificar_respuesta(
                status_code, cabeceras or {}, cuerpo or b"",
                content_type=(cabeceras or {}).get("content-type"), url=url_final or url)
        except Exception:  # noqa: BLE001 -- clasificar no tumba el registro
            response_class = None
    p = urlsplit(url)
    fila: dict[str, Any] = {
        "host": (p.hostname or "").lower(),
        "url": url,
        "url_final": url_final if url_final and url_final != url else None,
        "status_code": status_code,
        "error": (error or None) and error[:200],
        "response_class": response_class,
        "intentos": intentos,
        "fecha": fecha or datetime.now(timezone.utc),
        "bytes": None, "sha256": None, "cuerpo": None, "recortado": None,
        "comprobacion": None,
    }
    if estado(status_code, error, response_class) == PROCESADO:
        crudo = cuerpo or b""
        leido, recortado = recortar(crudo)
        fila.update(bytes=len(crudo), sha256=hashlib.sha256(crudo).hexdigest(),
                    cuerpo=texto(leido), recortado=recortado)
    return fila


class EvaluadorRobots:
    """Que puede pedir cada bot segun UN robots.txt guardado.

    `permitido` devuelve None cuando no se puede afirmar nada: robots.txt
    inaccesible (Google para el sitio, pero puede tener una copia en cache y no
    sabemos si el fallo fue pasajero: decision 67), muro delante o sin dato.
    """

    def __init__(self, estado_robots: str, cuerpo: str | None = None) -> None:
        self.estado = estado_robots
        self._parser = None
        if estado_robots == PROCESADO:
            from protego import Protego

            # Se vuelve a recortar por si la fila se guardo sin el tope.
            leido, _ = recortar((cuerpo or "").encode("utf-8"))
            self._parser = Protego.parse(leido.decode("utf-8", "ignore"))

    def permitido(self, url: str, bot: str) -> bool | None:
        if self.estado == SIN_RESTRICCIONES:
            return True
        if self._parser is None:
            return None
        return bool(self._parser.can_fetch(url, BOTS.get(bot, bot)))


__all__ = [
    "BOTS", "EvaluadorRobots", "INACCESIBLE", "LIMITE_BYTES", "MAX_HOSTS_POR_JOB",
    "NO_EVALUADO", "PROCESADO", "SIN_RESTRICCIONES", "es_url_de_robots", "estado",
    "fila_de_respuesta", "limitado", "origen", "recortar",
]
