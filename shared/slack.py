"""Slack por su API: un canal por cliente (decision 149).

El webhook generico (decision 77) manda TODO a un sitio; la agencia lo quiere
en Slack y cada cliente en su canal. Va por la Web API con un **bot**
(`chat.postMessage`) y no con *incoming webhooks*: un webhook de Slack queda
atado a UN canal al crearlo, asi que serian N URLs secretas, una por cliente,
que alguien tiene que ir creando en la consola de Slack. Con el bot es un token
y el canal es un parametro publico de la integracion del cliente.

El token (`xoxb-…`) es un secreto y vive donde la llave de Search Console: en
el entorno o subido a `SECRETOS_DIR` (decision 141), con prefijo `SLACK_`. La
integracion del cliente guarda el NOMBRE (`SLACK_LIN3S`), el canal y que se
manda: alertas, resumen semanal e informe de cada rastreo programado.

Todo es best-effort: lo llama el worker al cerrar un rastreo, y un Slack caido
no puede dejar un censo en `failed`. El ultimo envio de cada cliente (bien o
con su error traducido) queda en Redis para ensenarlo en la ficha: un canal al
que no invitaron al bot falla en silencio si nadie lo mira.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

API = os.getenv("SLACK_API_URL", "https://slack.com/api").rstrip("/")
TIMEOUT = float(os.getenv("ALERTA_TIMEOUT_SEGUNDOS", "10"))
# Lo que se puede mandar por un canal. Las tres por defecto: quien conecta un
# canal lo hace para enterarse.
ENVIOS = ("alertas", "resumen", "informe")
# Slack corta un `text` de mas de 40.000 caracteres; un bloque de seccion, a
# los 3.000. Se manda como `text` con mrkdwn y se recorta antes.
MAX_TEXTO = 39000


class SlackError(RuntimeError):
    def __init__(self, codigo: str, mensaje: str | None = None):
        super().__init__(mensaje or codigo)
        self.codigo = codigo


# Los errores de la API de Slack, dichos de forma que se pueda hacer algo.
_TRADUCCION = {
    "not_in_channel": "El bot no está en el canal: escribe /invite @{bot} en el canal.",
    "channel_not_found": "No existe el canal o el bot no lo ve (si es privado, invítalo primero).",
    "is_archived": "El canal está archivado.",
    "invalid_auth": "El token no es válido.",
    "not_authed": "Falta el token.",
    "account_inactive": "El token es de una app desinstalada o de un usuario desactivado.",
    "token_revoked": "El token se ha revocado.",
    "missing_scope": "Al bot le falta un permiso: {needed}.",
    "ratelimited": "Slack ha limitado los envíos; se reintenta en el siguiente.",
    "msg_too_long": "El mensaje es demasiado largo.",
    "restricted_action": "El espacio de trabajo no deja publicar al bot en ese canal.",
}


def traducir(codigo: str, bot: str | None = None, needed: str | None = None) -> str:
    plantilla = _TRADUCCION.get(codigo)
    if not plantilla:
        return f"Slack respondió «{codigo}»."
    return plantilla.format(bot=bot or "el-bot", needed=needed or "chat:write")


def _llamar(metodo: str, token: str, datos: dict[str, Any] | None = None,
            get: bool = False) -> dict[str, Any]:
    """Una llamada a la Web API. Lanza `SlackError` con el codigo de Slack.

    Un 429 se reintenta UNA vez si Slack pide esperar poco (`Retry-After`
    de hasta 5 s): mas espera retrasaria el cierre del rastreo.
    """
    url = f"{API}/{metodo}"
    cabeceras = {"Authorization": f"Bearer {token}"}
    cuerpo = None
    if get:
        if datos:
            url += "?" + urllib.parse.urlencode(datos)
    else:
        cuerpo = json.dumps(datos or {}, ensure_ascii=False).encode("utf-8")
        cabeceras["Content-Type"] = "application/json; charset=utf-8"
    for intento in range(2):
        peticion = urllib.request.Request(url, data=cuerpo, headers=cabeceras,
                                          method="GET" if get else "POST")
        try:
            with urllib.request.urlopen(peticion, timeout=TIMEOUT) as resp:
                r = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and intento == 0:
                espera = float(exc.headers.get("Retry-After") or 1)
                if espera <= 5:
                    time.sleep(espera)
                    continue
            raise SlackError("ratelimited" if exc.code == 429 else f"http_{exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SlackError("sin_conexion", f"No se pudo conectar con Slack: {exc}")
        if not r.get("ok"):
            err = SlackError(str(r.get("error") or "desconocido"))
            err.needed = r.get("needed")  # type: ignore[attr-defined]
            raise err
        return r
    raise SlackError("ratelimited")


def identidad(token: str) -> dict[str, Any]:
    """`auth.test`: el espacio de trabajo y el bot. Es lo que se puede ensenar
    de un token, como el email de una llave de Google."""
    r = _llamar("auth.test", token)
    return {"equipo": r.get("team"), "bot": r.get("user"), "url": r.get("url"),
            "bot_id": r.get("bot_id")}


def canales(token: str, limite: int = 1000) -> list[dict[str, Any]]:
    """Los canales que ve el bot, para elegir sin copiar ids. Necesita
    `channels:read` (y `groups:read` para los privados); sin esos permisos
    lanza `missing_scope` y en la ficha se escribe el canal a mano."""
    salida: list[dict[str, Any]] = []
    cursor = None
    while len(salida) < limite:
        datos = {"limit": 200, "exclude_archived": "true",
                 "types": "public_channel,private_channel"}
        if cursor:
            datos["cursor"] = cursor
        try:
            r = _llamar("conversations.list", token, datos, get=True)
        except SlackError as exc:
            if exc.codigo == "missing_scope" and "private_channel" in datos["types"]:
                # Sin groups:read: al menos los publicos.
                datos["types"] = "public_channel"
                r = _llamar("conversations.list", token, datos, get=True)
            else:
                raise
        for c in r.get("channels") or []:
            salida.append({"id": c.get("id"), "nombre": c.get("name"),
                           "privado": bool(c.get("is_private")),
                           "miembro": bool(c.get("is_member"))})
        cursor = (r.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    return sorted(salida, key=lambda c: c["nombre"] or "")


def publicar(token: str, canal: str, texto: str, enlace: str | None = None,
             titulo_enlace: str = "Abrir") -> dict[str, Any]:
    """`chat.postMessage` con el texto en mrkdwn y, si hay, el enlace al final.

    Sin bloques: el texto ya va en el formato de Slack (`*negrita*`, `•`), y
    con `blocks` el `text` pasa a ser solo la notificacion — partirlo en
    secciones de 3.000 caracteres no aporta nada a un aviso de diez lineas.
    """
    if enlace:
        texto = f"{texto}\n<{enlace}|{titulo_enlace}>"
    if len(texto) > MAX_TEXTO:
        texto = texto[:MAX_TEXTO] + "\n…"
    r = _llamar("chat.postMessage", token, {
        "channel": canal, "text": texto, "mrkdwn": True,
        "unfurl_links": False, "unfurl_media": False})
    return {"canal": r.get("channel"), "ts": r.get("ts")}


# ---------------------------------------------------------------------------
# La integracion de un cliente
# ---------------------------------------------------------------------------
def integraciones(cliente) -> list[dict[str, Any]]:
    return [i for i in (getattr(cliente, "integraciones", None) or [])
            if isinstance(i, dict) and i.get("tipo") == "slack"]


def _activo(valor, defecto: bool = True) -> bool:
    if valor is None or valor == "":
        return defecto
    if isinstance(valor, str):
        return valor.strip().lower() not in ("0", "no", "false", "off")
    return bool(valor)


def destinos(cliente, envio: str) -> list[dict[str, Any]]:
    """Las integraciones `slack` activas del cliente que mandan `envio`."""
    salida = []
    for i in integraciones(cliente):
        p = i.get("parametros") or {}
        if not i.get("activa", True) or not str(p.get("canal") or "").strip():
            continue
        if not _activo(p.get(envio)):
            continue
        salida.append(i)
    return salida


def _clave_estado(cliente_id) -> str:
    return f"slack:ultimo:{cliente_id}"


def _redis():
    try:
        import redis

        from shared.config import REDIS_URL
        return redis.from_url(REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
    except Exception:  # noqa: BLE001 — sin Redis no hay estado, se manda igual
        return None


def _anotar(cliente_id, envio: str, canal: str, ok: bool, motivo: str | None) -> None:
    r = _redis()
    if r is None:
        return
    try:
        r.set(_clave_estado(cliente_id), json.dumps({
            "envio": envio, "canal": canal, "ok": ok, "motivo": motivo,
            "en": datetime.now(timezone.utc).isoformat()}), ex=60 * 60 * 24 * 60)
    except Exception:  # noqa: BLE001
        logger.debug("No se pudo anotar el envio a Slack", exc_info=True)


def ultimo_envio(cliente_id) -> dict[str, Any] | None:
    r = _redis()
    if r is None:
        return None
    try:
        v = r.get(_clave_estado(cliente_id))
        return json.loads(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def enviar_a_cliente(cliente, envio: str, texto: str, enlace: str | None = None,
                     titulo_enlace: str = "Abrir") -> int:
    """Manda a cada canal del cliente que tenga `envio` activo. Devuelve a
    cuantos llego. Nunca lanza."""
    from shared.credenciales import CredencialNoDisponible, credencial

    enviados = 0
    try:
        for integ in destinos(cliente, envio):
            canal = str(integ["parametros"]["canal"]).strip()
            bot = None
            try:
                token = (credencial(integ) or "").strip()
                publicar(token, canal, texto, enlace, titulo_enlace)
                enviados += 1
                _anotar(cliente.id, envio, canal, True, None)
            except CredencialNoDisponible as exc:
                _anotar(cliente.id, envio, canal, False, str(exc))
                logger.warning("Slack de %s: %s", getattr(cliente, "slug", "?"), exc)
            except SlackError as exc:
                motivo = traducir(exc.codigo, bot, getattr(exc, "needed", None))
                _anotar(cliente.id, envio, canal, False, motivo)
                logger.warning("Slack de %s (%s): %s", getattr(cliente, "slug", "?"),
                               canal, exc.codigo)
    except Exception:  # noqa: BLE001
        logger.exception("Fallo al mandar a Slack (%s)", envio)
    return enviados
