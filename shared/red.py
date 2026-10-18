"""Hosts a los que el rastreo no debe pedir nada por cuenta de un tercero.

Una sonda de enlace externo (decision 123) es una peticion del SERVIDOR hacia
una URL que ha escrito cualquiera en una pagina: un `<a href="http://10.0.0.5/">`
en un comentario o un `<img src="http://169.254.169.254/latest/meta-data/">`
haria que el rastreo leyera la red interna del VPS (SSRF). Lo interno del sitio
que se audita no pasa por aqui: es el sitio, y lo decide el usuario al sembrar.

Solo se mira la forma del host, sin resolverlo: un nombre publico que resuelve a
una IP privada (rebinding) no se detecta. Cerrar eso exige fijar la IP con
`CURLOPT_RESOLVE` y comprobarla en cada salto, que es lo que planea la prueba de
extraccion en vivo (`docs/referencia/pendiente.md`); para una sonda de un
`HEAD` sin credenciales el riesgo que queda es leer un codigo de estado.
"""

from __future__ import annotations

import ipaddress

# Nombres que nunca son de Internet (RFC 6761/6762 y los de las nubes).
_SUFIJOS_PRIVADOS = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def es_host_privado(host: str | None) -> bool:
    """True si *host* es loopback, privado, link-local o un nombre de red local.

    Vacio cuenta como privado: sin host no hay a donde pedir.
    """
    h = (host or "").strip().lower().rstrip(".")
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if not h:
        return True
    if h == "localhost" or h.endswith(_SUFIJOS_PRIVADOS):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified)
