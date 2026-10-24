"""Probar reglas a medida contra una URL EN VIVO (#34, decision 166).

La API no descarga nada: encola la peticion en Redis (`extraccion:pruebas`),
la atiende el worker y la respuesta vuelve por una lista propia de la prueba
(`extraccion:prueba:<id>`), que la API espera con un tope. Asi la API no abre
conexiones hacia URLs que escribe quien usa el formulario, y lo que se baja lo
baja la imagen que ya rastrea (curl_cffi con la huella TLS de navegador).

**SSRF.** La URL la escribe una persona, no la elige el sitio auditado, y la
descarga sale del VPS, que ve Postgres, Redis y la API por la red de Docker.
Lo que se comprueba, en la URL inicial y en CADA salto de redireccion (que se
siguen a mano, nunca dentro de curl):

- esquema `http`/`https` y host presente;
- la forma del host (`shared.red.es_host_privado`: sin punto, sufijos locales,
  IP privada en cualquiera de las formas que lee curl);
- el nombre se RESUELVE aqui y TODAS sus IP tienen que ser globales; una sola
  privada y no se pide (un nombre con una A publica y otra a 127.0.0.1 es la
  forma barata de un rebinding);
- la IP comprobada se **fija** con `CURLOPT_RESOLVE` (`host:puerto:ip`) para
  esa peticion: curl no vuelve a preguntar al DNS, asi que un DNS que contesta
  una IP publica a la comprobacion y una privada a la conexion (rebinding) no
  cambia a donde se conecta. Sin proxy del entorno (`trust_env=False`): con
  proxy la resolucion la haria el proxy y el fijado no valdria.

Sin render (Chromium): la prueba descarga el HTML de la respuesta. Si el job
renderiza, se dice en los avisos; render en la prueba queda pendiente.

`curl_cffi` y `w3lib` se importan dentro: la imagen de la API copia `shared/`
y solo usa `encolar`/`esperar` (con `redis`).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from shared.red import es_host_privado

logger = logging.getLogger(__name__)

COLA_PRUEBAS = "extraccion:pruebas"
CLAVE_RESULTADO = "extraccion:prueba:{id}"
# Lo que espera la API la respuesta del worker. La descarga tiene su propio
# tope, mas corto, para que el worker conteste antes de que la API se rinda.
ESPERA_S = float(os.getenv("PRUEBA_EN_VIVO_ESPERA_S", "35"))
DESCARGA_S = float(os.getenv("PRUEBA_EN_VIVO_DESCARGA_S", "25"))
MAX_SALTOS = 10
MAX_BYTES = 5 * 1024 * 1024
# Lo que vive la respuesta en Redis si nadie la recoge (la API ya se rindio).
TTL_RESULTADO_S = 120


class URLNoPermitida(ValueError):
    """La URL (o un salto) apunta a algo que no es Internet."""


class DescargaFallida(RuntimeError):
    """Error de red, demasiados saltos, demasiado grande, tiempo agotado."""


# ---------------------------------------------------------------------------
# Lado de la API: encolar y esperar
# ---------------------------------------------------------------------------
def encolar(rconn, peticion: dict[str, Any], espera_s: float = ESPERA_S) -> str:
    """Pone la prueba al final de la cola y devuelve su id. `vence` le dice al
    worker cuando ya no merece la pena atenderla (la API se habra rendido)."""
    pid = uuid.uuid4().hex
    rconn.rpush(COLA_PRUEBAS, json.dumps({**peticion, "id": pid,
                                          "vence": time.time() + espera_s}))
    return pid


def esperar(rconn, pid: str, espera_s: float = ESPERA_S) -> dict[str, Any] | None:
    """La respuesta del worker, o None si no llega a tiempo."""
    r = rconn.blpop(CLAVE_RESULTADO.format(id=pid), timeout=max(1, int(espera_s)))
    if not r:
        return None
    _clave, valor = r
    return json.loads(valor)


def comprobar_forma(url: str) -> None:
    """Lo que se puede rechazar sin resolver nada (lo usa tambien la API para
    dar un 422 inmediato). Lanza URLNoPermitida."""
    partes = urlsplit((url or "").strip())
    if partes.scheme not in ("http", "https"):
        raise URLNoPermitida("solo se prueban URLs http:// o https://")
    if not partes.hostname:
        raise URLNoPermitida("la URL no tiene host")
    if partes.username or partes.password:
        raise URLNoPermitida("la URL lleva credenciales: quitalas")
    try:
        partes.port
    except ValueError as exc:
        raise URLNoPermitida(f"puerto no valido: {exc}") from None
    if es_host_privado(partes.hostname):
        raise URLNoPermitida(f"{partes.hostname} es de una red local o privada: no se pide")


# ---------------------------------------------------------------------------
# Lado del worker: descargar con las comprobaciones
# ---------------------------------------------------------------------------
def resolver_dns(host: str, puerto: int) -> list[str]:
    """Todas las IP de *host* (A y AAAA)."""
    infos = socket.getaddrinfo(host, puerto, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


def ip_permitida(ip: str) -> bool:
    return not es_host_privado(ip)


@dataclass
class Descarga:
    url: str
    url_final: str
    status: int
    cabeceras: dict[str, str]
    cuerpo: bytes
    ip: str
    saltos: list[dict[str, Any]] = field(default_factory=list)
    ms: float = 0.0


def _destino(url: str, resolver, permitida) -> tuple[str, int, str]:
    """(host, puerto, ip fijada) de *url*, o URLNoPermitida."""
    comprobar_forma(url)
    partes = urlsplit(url)
    host = partes.hostname
    puerto = partes.port or (443 if partes.scheme == "https" else 80)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        ips = [str(literal)]
    else:
        try:
            ips = resolver(host, puerto)
        except OSError as exc:
            raise DescargaFallida(f"no se pudo resolver {host}: {exc}") from None
        if not ips:
            raise DescargaFallida(f"{host} no resuelve")
    malas = [ip for ip in ips if not permitida(ip)]
    if malas:
        raise URLNoPermitida(f"{host} resuelve a {', '.join(malas)}, que no es de Internet: "
                             f"no se pide")
    return host, puerto, ips[0]


def descargar_segura(url: str, *, user_agent: str | None = None,
                     impersonate: str | None = None, timeout_s: float = DESCARGA_S,
                     max_saltos: int = MAX_SALTOS, max_bytes: int = MAX_BYTES,
                     resolver: Callable[[str, int], list[str]] = resolver_dns,
                     permitida: Callable[[str], bool] = ip_permitida) -> Descarga:
    """GET de *url* siguiendo las redirecciones a mano, comprobando y fijando la
    IP de cada salto. Lanza URLNoPermitida o DescargaFallida."""
    from curl_cffi import CurlOpt
    from curl_cffi import requests as cr

    from shared.descarga_cruda import huella_y_cabeceras

    perfil, cabeceras = huella_y_cabeceras(impersonate, user_agent)
    inicio = time.monotonic()
    actual = url
    saltos: list[dict[str, Any]] = []
    for _ in range(max_saltos + 1):
        host, puerto, ip = _destino(actual, resolver, permitida)
        queda = timeout_s - (time.monotonic() - inicio)
        if queda <= 0:
            raise DescargaFallida(f"la descarga paso de {timeout_s:.0f} s")
        fijada = f"[{ip}]" if ":" in ip else ip
        opciones = {CurlOpt.RESOLVE: [f"{host}:{puerto}:{fijada}"]}
        try:
            with cr.Session(curl_options=opciones, trust_env=False) as ses:
                r = ses.get(actual, impersonate=perfil, headers=cabeceras, timeout=queda,
                            allow_redirects=False, stream=True)
                try:
                    trozos: list[bytes] = []
                    leidos = 0
                    if not (300 <= r.status_code < 400):
                        for trozo in r.iter_content():
                            leidos += len(trozo)
                            if leidos > max_bytes:
                                raise DescargaFallida(
                                    f"la pagina pasa de {max_bytes // 1024 // 1024} MB")
                            trozos.append(trozo)
                    cab = {k.lower(): v for k, v in (r.headers or {}).items()}
                    status = r.status_code
                finally:
                    r.close()
        except (DescargaFallida, URLNoPermitida):
            raise
        except Exception as exc:  # noqa: BLE001 — error de red de curl
            raise DescargaFallida(f"{type(exc).__name__}: {exc}"[:300]) from None
        if 300 <= status < 400 and cab.get("location"):
            siguiente = urljoin(actual, cab["location"].strip())
            saltos.append({"url": actual, "status": status, "ip": ip, "location": siguiente})
            actual = siguiente
            continue
        return Descarga(url=url, url_final=actual, status=status, cabeceras=cab,
                        cuerpo=b"".join(trozos), ip=ip, saltos=saltos,
                        ms=round((time.monotonic() - inicio) * 1000, 1))
    raise DescargaFallida(f"mas de {max_saltos} redirecciones")


def _texto(cuerpo: bytes, content_type: str | None) -> str:
    try:
        from w3lib.encoding import html_to_unicode

        _enc, texto = html_to_unicode(content_type, cuerpo)
        return texto
    except Exception:  # noqa: BLE001
        return cuerpo.decode("utf-8", errors="replace")


def atender(peticion: dict[str, Any], **kw_descarga) -> dict[str, Any]:
    """Atiende una prueba: descarga y aplica las reglas. Devuelve SIEMPRE un
    dict (best-effort con el try sobre la funcion entera, decision 74): `ok`
    False con `error` y `motivo` (`url_no_permitida`, `descarga`, `reglas`,
    `interno`) si no se pudo."""
    try:
        from shared import extraccion_a_medida as xm
        from shared.respuestas import NO_VALIDAS, clasificar_respuesta

        config = peticion.get("config") or {}
        url = peticion.get("url") or ""
        try:
            comp = xm.compilar(config.get("custom_extraction"), config.get("custom_search"))
        except ValueError as exc:
            return {"ok": False, "motivo": "reglas", "error": str(exc)}
        try:
            d = descargar_segura(url, user_agent=config.get("user_agent"),
                                 impersonate=config.get("impersonate"), **kw_descarga)
        except URLNoPermitida as exc:
            return {"ok": False, "motivo": "url_no_permitida", "error": str(exc)}
        except DescargaFallida as exc:
            return {"ok": False, "motivo": "descarga", "error": str(exc)}
        avisos: list[str] = []
        if config.get("render_js"):
            avisos.append("El job renderiza JavaScript y esta prueba NO: lee el HTML de la "
                          "respuesta. Lo que monte el JavaScript puede salir «sin coincidencia» "
                          "aqui y encontrarse en el rastreo")
        descarga = {"url": d.url, "url_final": d.url_final, "status": d.status,
                    "saltos": d.saltos, "ms": d.ms,
                    "content_type": d.cabeceras.get("content-type"),
                    "bytes": len(d.cuerpo)}
        ct = (d.cabeceras.get("content-type") or "").lower()
        if not 200 <= d.status < 300:
            avisos.append(f"La URL responde {d.status}: el rastreo solo aplica las reglas a "
                          f"paginas HTML 2xx, asi que aqui no se evalua ninguna")
            return {"ok": True, "descarga": descarga, "reglas": [], "no_evaluadas": [
                rc.nombre for rc in comp.reglas], "avisos": avisos, "ms": 0.0}
        if ct and "html" not in ct and "xml" not in ct:
            avisos.append(f"La respuesta es {ct}, no HTML: el rastreo no le aplica las reglas")
            return {"ok": True, "descarga": descarga, "reglas": [], "no_evaluadas": [
                rc.nombre for rc in comp.reglas], "avisos": avisos, "ms": 0.0}
        clase, motivo = clasificar_respuesta(d.status, d.cabeceras, d.cuerpo, url=d.url_final)
        bloqueada = clase in NO_VALIDAS
        if bloqueada:
            avisos.append(f"La respuesta es un muro ({clase}: {motivo}): las reglas salen "
                          f"«pagina_bloqueada», como en el rastreo")
        html = _texto(d.cuerpo, d.cabeceras.get("content-type"))
        if any((r or {}).get("fuente") == "contenido"
               for r in [*(config.get("custom_extraction") or {}).values(),
                         *(config.get("custom_search") or {}).values()]):
            avisos.append("fuente=contenido no se evalua en la prueba en vivo: el contenido "
                          "principal lo calcula el extractor del rastreo")
        r = xm.evaluar_documento(comp, html, ruta=urlsplit(d.url_final).path or "/",
                                 render=False, max_urls=config.get("max_urls"),
                                 bloqueada=bloqueada)
        return {"ok": True, "descarga": descarga, "reglas": r["reglas"],
                "no_evaluadas": r["no_evaluadas"], "avisos": avisos + r["avisos"],
                "ms": r["ms"]}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Prueba en vivo: fallo inesperado")
        return {"ok": False, "motivo": "interno", "error": f"{type(exc).__name__}: {exc}"[:300]}


def responder(rconn, pid: str, resultado: dict[str, Any]) -> None:
    clave = CLAVE_RESULTADO.format(id=pid)
    rconn.rpush(clave, json.dumps(resultado, default=str))
    rconn.expire(clave, TTL_RESULTADO_S)


def atender_mensaje(rconn, crudo: str, **kw_descarga) -> str | None:
    """Un mensaje de la cola, de principio a fin: lo descarta si ya vencio,
    lo atiende y deja la respuesta. Devuelve el id atendido o None. Nunca
    lanza: lo llama un hilo del worker que no puede morir."""
    try:
        peticion = json.loads(crudo)
        pid = str(peticion.get("id") or "")
        if not pid:
            return None
        if float(peticion.get("vence") or 0) < time.time():
            logger.info("Prueba en vivo %s: vencida antes de atenderla; se descarta", pid)
            return None
        resultado = atender(peticion, **kw_descarga)
        responder(rconn, pid, resultado)
        return pid
    except Exception:  # noqa: BLE001
        logger.exception("Prueba en vivo: mensaje que no se pudo atender")
        return None
