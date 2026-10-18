"""Certificado TLS de cada host: el error de una pagina y la sonda (decision 110).

Dos cosas separadas:

- **El error de una pagina.** curl_cffi rechaza un certificado caducado, de
  otro nombre o con la cadena rota con `CertificateVerifyError` (codigo 60) y
  un handshake imposible con el 35; Chromium, con `net::ERR_CERT_*`. Antes
  `handle_error` solo reconocia las excepciones de Twisted y la pagina quedaba
  `status_group='error'` con el motivo solo en el log. `motivo_error_tls` da
  el motivo, y la pagina queda `tls_error` con el motivo en `status_text`.
- **La sonda por host.** Una vez por host interno https, la primera vez que
  responde o falla, fuera de `max_urls` y sin item (el patron de la sonda de
  soft 404, decision 105): no es una URL del censo. Lee la caducidad y la
  version negociada, y si la verificacion falla, por que. Va a `jobs.tls`.

La sonda hace hasta dos conexiones con `ssl` de la biblioteca estandar, en un
hilo (`deferToThread`), nunca en el reactor:

1. una **verificada**, con el almacen del sistema y el nombre del host: si
   pasa, el certificado vale para un navegador; si no, el motivo
   (`verify_code` de OpenSSL);
2. si la primera falla, otra **sin verificar** y admitiendo TLS 1.0, solo
   para leer el certificado (`getpeercert(binary_form=True)` +
   `cryptography`: sin verificar, `getpeercert()` devuelve `{}`) y la version
   que acepta el servidor. Un servidor que solo habla TLS 1.0 u 1.1 no
   negocia con la primera (OpenSSL 3 no los ofrece por defecto): lo dice la
   segunda.

Lo negociado es el MAXIMO que admite el servidor, no el minimo: que acepte
tambien TLS 1.0 no se puede saber asi, y no se afirma.
"""

from __future__ import annotations

import logging
import re
import socket
import ssl
import warnings
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

TIMEOUT_SONDA_S = 10.0

# Codigos de libcurl que son del TLS: 35 handshake, 51 nombre (libcurl
# antiguo), 58/59 certificado local o cifrado, 60 verificacion del
# certificado del servidor (caducado, autofirmado, cadena, nombre), 83 emisor,
# 90 clave fijada, 91 estado OCSP.
CODIGOS_CURL_TLS = frozenset({35, 51, 58, 59, 60, 83, 90, 91})

# Versiones que Chrome dejo de aceptar en 2020 (Chrome 84).
VERSIONES_ANTIGUAS = frozenset({"SSLv2", "SSLv3", "TLSv1", "TLSv1.1"})

# verify_code de OpenSSL -> motivo. El resto, «otro» con el texto de OpenSSL.
_MOTIVO_OPENSSL = {
    9: "aun_no_valido",       # X509_V_ERR_CERT_NOT_YET_VALID
    10: "caducado",           # X509_V_ERR_CERT_HAS_EXPIRED
    18: "autofirmado",        # X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT
    19: "autofirmado",        # X509_V_ERR_SELF_SIGNED_CERT_IN_CHAIN
    20: "cadena_incompleta",  # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY
    21: "cadena_incompleta",  # X509_V_ERR_UNABLE_TO_VERIFY_LEAF_SIGNATURE
    23: "revocado",           # X509_V_ERR_CERT_REVOKED
    62: "nombre",             # X509_V_ERR_HOSTNAME_MISMATCH
}

_RE_CURL = re.compile(r"curl: \((\d+)\)\s*(.*?)(?:\. See https?://\S+.*)?$", re.S)
_RE_CHROMIUM = re.compile(r"net::(ERR_(?:CERT|SSL)_[A-Z_]+)")


def motivo_error_tls(exc: BaseException | None) -> str | None:
    """El motivo, si la excepcion de una descarga es un fallo de TLS; si no, None.

    `CertificateVerifyError` de curl_cffi (codigo 60), cualquier otro error de
    curl con un codigo de `CODIGOS_CURL_TLS`, o un `net::ERR_CERT_*` /
    `net::ERR_SSL_*` de Chromium. El texto va a `urls.status_text` (64
    caracteres), asi que se resume: `curl 60: SSL certificate problem:
    certificate has expired`.
    """
    if exc is None:
        return None
    texto = str(exc) or type(exc).__name__
    codigo = getattr(exc, "code", None)
    try:
        codigo = int(codigo) if codigo is not None else None
    except (TypeError, ValueError):
        codigo = None
    if codigo in CODIGOS_CURL_TLS:
        casa = _RE_CURL.search(texto)
        detalle = casa.group(2).strip().rstrip(".") if casa else texto.strip()
        return f"curl {codigo}: {detalle}"[:200]
    casa = _RE_CHROMIUM.search(texto)
    if casa:
        return casa.group(1)
    return None


def _contexto_sin_verificar() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # Para poder ver un servidor que SOLO habla TLS 1.0/1.1: OpenSSL 3 no los
    # ofrece con el nivel de seguridad por defecto. Si la biblioteca no lo
    # permite, la sonda dice lo que pueda.
    try:
        with warnings.catch_warnings():  # «TLSv1 is deprecated»: por eso se mide
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    except (ValueError, ssl.SSLError):
        pass
    return ctx


def _leer_certificado(der: bytes | None, ahora: datetime) -> dict[str, Any]:
    if not der:
        return {}
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID

        cert = x509.load_der_x509_certificate(der)
        fin = cert.not_valid_after_utc
        inicio = cert.not_valid_before_utc
        emisor = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
        return {
            "not_before": inicio.isoformat(timespec="seconds"),
            "not_after": fin.isoformat(timespec="seconds"),
            # Los dias que le quedaban EL DIA DEL RASTREO: al re-analizar un
            # censo viejo no se recalculan contra hoy.
            "dias_restantes": round((fin - ahora).total_seconds() / 86400, 2),
            "emisor": str(emisor[0].value)[:200] if emisor else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error_certificado": f"{type(exc).__name__}: {exc}"[:200]}


def _conectar(host: str, puerto: int, ctx: ssl.SSLContext, timeout: float):
    with socket.create_connection((host, puerto), timeout=timeout) as crudo:
        with ctx.wrap_socket(crudo, server_hostname=host) as tls:
            return tls.version(), tls.getpeercert(binary_form=True)


def sondear_tls(host: str, puerto: int = 443, *, timeout: float = TIMEOUT_SONDA_S,
                cafile: str | None = None, ahora: datetime | None = None) -> dict[str, Any]:
    """La ficha TLS de un host. Bloquea: se llama desde un hilo.

    `cafile` solo lo usan los tests (una CA propia en lugar del almacen del
    sistema). Nunca lanza: un fallo de red es un `error` en la ficha.
    """
    ahora = ahora or datetime.now(timezone.utc)
    ficha: dict[str, Any] = {
        "host": host, "puerto": puerto, "fuente": "socket",
        "fecha": ahora.isoformat(timespec="seconds"),
        "verificado": None, "motivo": None, "detalle": None, "version": None,
    }
    der = None
    try:
        ctx = ssl.create_default_context(cafile=cafile)
        ficha["version"], der = _conectar(host, puerto, ctx, timeout)
        ficha["verificado"] = True
    except ssl.SSLCertVerificationError as exc:
        ficha["verificado"] = False
        ficha["motivo"] = _MOTIVO_OPENSSL.get(getattr(exc, "verify_code", None), "otro")
        ficha["detalle"] = (getattr(exc, "verify_message", None) or str(exc))[:200]
    except ssl.SSLError as exc:
        # El handshake no se cierra con un cliente moderno: o el servidor solo
        # habla un TLS antiguo (lo dice la segunda conexion) o corta lo que no
        # parece un navegador (un WAF). Ninguno de los dos es un certificado
        # invalido por si mismo.
        ficha["detalle"] = f"handshake: {exc}"[:200]
    except (OSError, ValueError) as exc:
        ficha["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return ficha
    if der is None:
        try:
            version, der = _conectar(host, puerto, _contexto_sin_verificar(), timeout)
            ficha["version"] = version
        except Exception as exc:  # noqa: BLE001
            ficha["error"] = f"{type(exc).__name__}: {exc}"[:200]
    ficha.update(_leer_certificado(der, ahora))
    ficha["version_antigua"] = ficha["version"] in VERSIONES_ANTIGUAS if ficha["version"] else None
    return ficha


def ficha_de_playwright(host: str, detalles: dict, ahora: datetime | None = None) -> dict[str, Any] | None:
    """La ficha con lo que dejo scrapy-playwright (`playwright_security_details`).

    Con render ya hay una conexion TLS: no hace falta otra. Si Chromium cargo
    la pagina, el certificado valia para el navegador.
    """
    if not isinstance(detalles, dict) or not detalles.get("validTo"):
        return None
    ahora = ahora or datetime.now(timezone.utc)
    try:
        fin = datetime.fromtimestamp(float(detalles["validTo"]), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None
    protocolo = str(detalles.get("protocol") or "")
    # "TLS 1.3" -> "TLSv1.3", como `ssl`
    version = None
    casa = re.match(r"^\s*(TLS|SSL)\s*v?(\d(?:\.\d)?)\s*$", protocolo, re.I)
    if casa:
        version = f"{casa.group(1).upper()}v{casa.group(2)}"
        version = "TLSv1" if version == "TLSv1.0" else version
    ficha: dict[str, Any] = {
        "host": host, "fuente": "navegador",
        "fecha": ahora.isoformat(timespec="seconds"),
        "verificado": True, "motivo": None, "detalle": None,
        "version": version or (protocolo[:32] or None),
        "not_after": fin.isoformat(timespec="seconds"),
        "dias_restantes": round((fin - ahora).total_seconds() / 86400, 2),
        "emisor": (str(detalles.get("issuer"))[:200] if detalles.get("issuer") else None),
    }
    ficha["version_antigua"] = version in VERSIONES_ANTIGUAS if version else None
    return ficha


class SondaTlsMixin:
    """Metodos del spider para la sonda TLS. Estado en `self._tls_*`."""

    def _iniciar_sonda_tls(self, job) -> None:
        previo = getattr(job, "tls", None) or {}
        hosts = previo.get("hosts") if isinstance(previo, dict) else None
        # Al reanudar no se repiten: el certificado no cambia en horas.
        self._tls_hosts: dict[str, dict] = dict(hosts) if isinstance(hosts, dict) else {}
        self._tls_pendientes: set[str] = set()
        cb = (self.job_config.get("crawl_behavior") or {})
        self._tls_activa: bool = bool(cb.get("sonda_tls", True))

    def _sondear_tls(self, url: str, response=None) -> None:
        """Lanza la sonda del host de `url` si es https, interno y nuevo."""
        if not getattr(self, "_tls_activa", False):
            return
        partes = urlparse(url)
        host = (partes.hostname or "").lower()
        if partes.scheme != "https" or not host:
            return
        if host in self._tls_hosts or host in self._tls_pendientes:
            return
        if not self._is_internal(url):
            return
        detalles = response.meta.get("playwright_security_details") if response is not None else None
        ficha = ficha_de_playwright(host, detalles) if detalles else None
        if ficha is not None:
            self._anotar_tls(host, ficha)
            return
        if (self.settings.get("PROXY_LIST") or "").strip():
            # Un socket directo saldria por otra IP que el rastreo: no se mide
            # lo mismo que vio el crawler.
            self._anotar_tls(host, {"host": host, "omitida": "proxy"})
            return
        from twisted.internet.threads import deferToThread

        try:
            puerto = partes.port or 443
        except ValueError:
            return
        self._tls_pendientes.add(host)
        d = deferToThread(sondear_tls, host, puerto)
        d.addCallback(lambda ficha: self._anotar_tls(host, ficha))
        d.addErrback(lambda f: self._anotar_tls(
            host, {"host": host, "error": f"{f.type.__name__}: {f.value}"[:200]}))

    def _anotar_tls(self, host: str, ficha: dict) -> None:
        self._tls_pendientes.discard(host)
        self._tls_hosts[host] = ficha
        if ficha.get("verificado") is False or ficha.get("version_antigua"):
            logger.warning("Sonda TLS %s: %s %s (%s)", host, ficha.get("motivo"),
                           ficha.get("detalle") or "", ficha.get("version"))
        else:
            logger.info("Sonda TLS %s: %s, caduca %s", host, ficha.get("version"),
                        ficha.get("not_after"))
        try:
            from shared.database import SessionLocal
            from shared.models import Job

            sesion = SessionLocal()
            try:
                sesion.query(Job).filter(Job.id == self.job_id).update(
                    {"tls": {"hosts": self._tls_hosts}}, synchronize_session=False)
                sesion.commit()
            finally:
                sesion.close()
        except Exception as exc:  # noqa: BLE001 -- una sonda no tumba un rastreo
            logger.warning("No se pudo guardar la sonda TLS: %s", exc)

    def _tls_en_vuelo(self) -> bool:
        """Para `spider_idle`: no cerrar con una sonda a medias."""
        return bool(getattr(self, "_tls_pendientes", None))
