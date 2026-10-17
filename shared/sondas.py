"""Que dice una sonda de respuesta: una URL que el rastreo pide sin que sea del
censo, para saber si responde (decisiones 123 y 124).

Dos usos con el mismo mecanismo: los enlaces EXTERNOS (`<a href>` a otro
sitio) y los recursos EMBEBIDOS (imagenes, CSS, JS) que el censo no pide. La
fila va a `respuestas_sondeadas`, nunca a `urls`.

Vive en `shared/` porque el veredicto lo escribe el spider y lo leen el
analizador y la API: que es «roto» y que es «no se sabe» tiene que ser lo mismo
en los tres (decisiones 23 y 39). Sin dependencias.

**Lo que no se puede afirmar no se afirma** (67, 70, 89, 91). Un 403, un 429 o
el 999 de LinkedIn son lo que responde un WAF a un rastreador, no un enlace
roto: `no_verificable`. Un 5xx puede ser una caida de un minuto:
`error_servidor`, dato y no aviso. Solo `roto` (un 404/410, o un 4xx que no es
de permisos) y `redirige_a_error` llevan aviso de enlace roto; y solo un
dominio que no resuelve o no acepta conexiones, comprobado, es un dominio
muerto.
"""

from __future__ import annotations

OK = "ok"
ROTO = "roto"
REDIRIGE_A_ERROR = "redirige_a_error"
ERROR_SERVIDOR = "error_servidor"
NO_VERIFICABLE = "no_verificable"
PROHIBIDO_POR_ROBOTS = "prohibido_por_robots"
DOMINIO_INEXISTENTE = "dominio_inexistente"
SIN_CONEXION = "sin_conexion"
TIEMPO_AGOTADO = "tiempo_agotado"
TLS_INVALIDO = "tls_invalido"

VEREDICTOS = (
    OK, ROTO, REDIRIGE_A_ERROR, ERROR_SERVIDOR, NO_VERIFICABLE,
    PROHIBIDO_POR_ROBOTS, DOMINIO_INEXISTENTE, SIN_CONEXION, TIEMPO_AGOTADO,
    TLS_INVALIDO,
)

# Los que llevan aviso de enlace (o recurso) roto.
ROTOS = frozenset({ROTO, REDIRIGE_A_ERROR})
# Los de un host al que no se llega. Solo cuentan como dominio muerto si el
# spider dio el host por caido (`host_caido`): un fallo suelto no lo es.
MUERTOS = frozenset({DOMINIO_INEXISTENTE, SIN_CONEXION})

USOS = ("enlace", "recurso")

# A un HEAD con estos codigos no se le cree: hay servidores que solo enrutan GET
# (405, 501), WAF que tratan distinto un HEAD (403, 429) y CMS que dan 404 o 400
# al HEAD de una pagina que existe. Se repite UNA vez con GET (y `Range`). Es la
# lista de FreeCrawl (`HEAD_UNRELIABLE_STATUSES`) mas el 404 y el 410: antes de
# afirmar «roto» con un HEAD se confirma con GET, que cuesta solo lo que sale
# roto.
HEAD_POCO_FIABLE = frozenset({400, 401, 403, 404, 405, 406, 409, 410, 429, 501})

# Lo que responde quien no deja pasar a un rastreador, o quien pide credenciales.
_DE_PERMISOS = frozenset({401, 403, 406, 407, 429, 451, 999})

# Cuantos saltos se siguen. Los de Google (diez); mas es un bucle.
MAX_SALTOS = 10


def repetir_con_get(status: int) -> bool:
    """Si la respuesta a un HEAD no basta para decidir y hay que pedir GET."""
    return status in HEAD_POCO_FIABLE or status >= 500


def veredicto_http(status: int | None, response_class: str | None = None) -> str:
    """Veredicto de una respuesta HTTP final (despues de los saltos).

    `response_class` es la de `shared.respuestas.clasificar_respuesta`: un
    desafio servido con 200 o con 503 no es ni la pagina ni un error del sitio.
    """
    from shared.respuestas import NO_VALIDAS

    if response_class in NO_VALIDAS:
        return NO_VERIFICABLE
    if status is None:
        return NO_VERIFICABLE
    # 206: la respuesta a un `Range`. 304: lo que se tenga en cache vale. 416:
    # el rango no cabe, pero el recurso existe.
    if 200 <= status < 300 or status in (304, 416):
        return OK
    if 300 <= status < 400:
        return NO_VERIFICABLE  # un 3xx sin Location: no dice a donde
    if status in _DE_PERMISOS:
        return NO_VERIFICABLE
    if 400 <= status < 500:
        return ROTO
    if 500 <= status < 600:
        return ERROR_SERVIDOR
    return NO_VERIFICABLE


def veredicto_final(status: int | None, response_class: str | None, saltos: int) -> str:
    """Como `veredicto_http`, pero un roto tras una redireccion es
    `redirige_a_error`: el enlace apunta a una URL que existio."""
    v = veredicto_http(status, response_class)
    if v == ROTO and saltos:
        return REDIRIGE_A_ERROR
    return v


def tamano_de_cabeceras(cabeceras: dict[str, str], status: int, cuerpo: int | None) -> int | None:
    """Bytes del recurso entero, con lo que diga la respuesta.

    `Content-Range: bytes 0-1023/48213` (respuesta a un `Range`) da el total;
    si no, `Content-Length` de una respuesta entera. Con un 206 sin total no se
    sabe. `cuerpo` es lo descargado: vale si la respuesta fue un 200 a un GET
    sin `Content-Length` (troceada).
    """
    rango = (cabeceras.get("content-range") or "").strip()
    if "/" in rango:
        total = rango.rsplit("/", 1)[1].strip()
        if total.isdigit():
            return int(total)
    if status == 206:
        return None
    largo = (cabeceras.get("content-length") or "").strip()
    if largo.isdigit():
        return int(largo)
    if 200 <= status < 300 and cuerpo:
        return cuerpo
    return None
