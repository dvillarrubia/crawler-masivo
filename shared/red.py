"""Hosts a los que el rastreo no debe pedir nada por cuenta de un tercero.

Una sonda de enlace externo (decision 123) es una peticion del SERVIDOR hacia
una URL que ha escrito cualquiera en una pagina: un `<a href="http://10.0.0.5/">`
en un comentario o un `<img src="http://169.254.169.254/latest/meta-data/">`
haria que el rastreo leyera la red interna del VPS (SSRF). Lo interno del sitio
que se audita no pasa por aqui: es el sitio, y lo decide el usuario al sembrar.

Solo se mira la forma del host, sin resolverlo: un nombre publico que resuelve a
una IP privada (rebinding) no se detecta. Cerrar eso exige fijar la IP con
`CURLOPT_RESOLVE` y comprobarla en cada salto, que es lo que planea la prueba de
extraccion en vivo (`docs/referencia/pendiente.md`).

**La forma del host es la que lee curl, no la de `ipaddress`** (revision de
#184, decision 154): la sonda de la og:image va encendida de serie y con GET,
asi que el filtro tiene que cerrar todo lo que curl convierte en una IP.

- Un host **sin punto** (`api`, `postgres`, `redis`: los servicios de
  docker-compose, que el DNS de Docker resuelve) es de la red local. En
  Internet no hay nombres de una sola etiqueta que resuelvan.
- Un IPv4 en forma **numerica, hexadecimal, octal o abreviada** (`2130706433`,
  `0x7f000001`, `0177.0.0.1`, `127.1`) se normaliza como lo hace
  `inet_aton` (y curl) antes de evaluarlo: `ipaddress` las rechaza y antes
  pasaban como «nombre publico».
- Un IPv6 que lleva dentro un IPv4 (`::ffff:127.0.0.1`) se evalua por el IPv4.
- Lo que no es `is_global` (privado, loopback, link-local, CGNAT 100.64/10,
  reservado, documentacion) no se pide; tampoco multicast.
"""

from __future__ import annotations

import ipaddress
import re
import socket

# Nombres que nunca son de Internet (RFC 6761/6762 y los de las nubes).
_SUFIJOS_PRIVADOS = (".localhost", ".local", ".internal", ".lan", ".home.arpa")

# Lo que `inet_aton` podria leer como IPv4: cifras decimales, hexadecimales
# (con 0x) y puntos. Se filtra antes para no depender de lo que cada libc
# tolera detras (espacios, basura).
_RE_IPV4_LAXA = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$")


def _ip(h: str):
    """La IP que curl pediria para *h*, o None si *h* es un nombre."""
    try:
        return ipaddress.ip_address(h)
    except ValueError:
        pass
    if _RE_IPV4_LAXA.match(h):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(h))
        except (OSError, ValueError):
            # Forma de IP que no cabe (`999.1.1.1`, un numero enorme): curl
            # no la puede pedir, y como nombre tampoco es de Internet.
            return ipaddress.IPv4Address("0.0.0.0")
    return None


def es_host_privado(host: str | None) -> bool:
    """True si *host* es loopback, privado, link-local, no global o un nombre
    de red local (sufijo local o una sola etiqueta).

    Vacio cuenta como privado: sin host no hay a donde pedir.
    """
    h = (host or "").strip().lower().rstrip(".")
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if not h:
        return True
    ip = _ip(h)
    if ip is not None:
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        return not ip.is_global or ip.is_multicast
    if "." not in h:
        return True
    return h == "localhost" or h.endswith(_SUFIJOS_PRIVADOS)
