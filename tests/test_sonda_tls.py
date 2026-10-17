"""Sonda TLS por host y clasificacion del error TLS (decision 110).

Servidores HTTPS locales con certificados hechos aqui (`ssl` de la biblioteca
estandar y `cryptography`, que ya trae Scrapy): caducado, a punto de caducar,
de otro nombre y, si la OpenSSL local lo permite, uno que solo habla TLS 1.0.
"""

from __future__ import annotations

import datetime as dt
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("cryptography")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from seo_crawler.sonda_tls import (  # noqa: E402
    ficha_de_playwright, motivo_error_tls, sondear_tls,
)

AHORA = dt.datetime.now(dt.timezone.utc)


def _certificado(tmp_path, nombre: str, *, desde: dt.timedelta, hasta: dt.timedelta):
    clave = ec.generate_private_key(ec.SECP256R1())
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nombre)])
    cert = (x509.CertificateBuilder().subject_name(sujeto).issuer_name(sujeto)
            .public_key(clave.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(AHORA + desde).not_valid_after(AHORA + hasta)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(nombre)]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(clave, hashes.SHA256()))
    pem = tmp_path / f"{nombre}-{hasta.days}.pem"
    key = tmp_path / f"{nombre}-{hasta.days}.key"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(clave.private_bytes(serialization.Encoding.PEM,
                                        serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    return str(pem), str(key)


class _H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def _servir(pem, key, *, version: ssl.TLSVersion | None = None):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    if version is not None:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx.minimum_version = version
            ctx.maximum_version = version
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    ctx.load_cert_chain(pem, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


@pytest.fixture
def servidor(tmp_path):
    servidores = []

    def montar(nombre="localhost", *, desde=-30, hasta=60, version=None):
        pem, key = _certificado(tmp_path, nombre, desde=dt.timedelta(days=desde),
                                hasta=dt.timedelta(days=hasta))
        srv, puerto = _servir(pem, key, version=version)
        servidores.append(srv)
        return puerto, pem

    yield montar
    for srv in servidores:
        srv.shutdown()


def test_caducado_da_invalido_con_la_fecha_en_el_pasado(servidor):
    puerto, ca = servidor(hasta=-1)
    ficha = sondear_tls("localhost", puerto, cafile=ca)
    assert ficha["verificado"] is False and ficha["motivo"] == "caducado"
    assert ficha["dias_restantes"] < 0
    assert dt.datetime.fromisoformat(ficha["not_after"]) < AHORA
    # El certificado se lee igual, con una segunda conexion sin verificar
    assert ficha["version"] in ("TLSv1.2", "TLSv1.3") and ficha["version_antigua"] is False


def test_valido_con_tres_dias(servidor):
    puerto, ca = servidor(hasta=3)
    ficha = sondear_tls("localhost", puerto, cafile=ca)
    assert ficha["verificado"] is True and ficha["motivo"] is None
    assert 2.9 < ficha["dias_restantes"] <= 3.0
    assert ficha["emisor"] == "localhost" and ficha["fuente"] == "socket"


def test_nombre_que_no_casa(servidor):
    puerto, ca = servidor("otro.example")
    ficha = sondear_tls("localhost", puerto, cafile=ca)
    assert ficha["verificado"] is False and ficha["motivo"] == "nombre"


def test_autofirmado_contra_el_almacen_del_sistema(servidor):
    puerto, _ca = servidor()
    ficha = sondear_tls("localhost", puerto)
    assert ficha["verificado"] is False and ficha["motivo"] == "autofirmado"


def test_sin_servidor_es_error_y_no_lanza():
    ficha = sondear_tls("127.0.0.1", 1, timeout=2)
    assert ficha["error"] and ficha["verificado"] is None


def test_solo_tls_1_0_es_version_antigua(servidor):
    try:
        puerto, ca = servidor(version=ssl.TLSVersion.TLSv1)
    except (ssl.SSLError, ValueError) as exc:
        pytest.skip(f"la OpenSSL local no sirve TLS 1.0: {exc}")
    ficha = sondear_tls("localhost", puerto, cafile=ca)
    if ficha.get("error"):
        pytest.skip(f"la OpenSSL local no negocia TLS 1.0: {ficha['error']}")
    assert ficha["version"] == "TLSv1" and ficha["version_antigua"] is True


# -- El error de una pagina -------------------------------------------------

def test_motivo_de_la_excepcion_real_de_curl(servidor):
    """La excepcion que lanza curl_cffi de verdad, no una imitacion."""
    import asyncio

    curl = pytest.importorskip("curl_cffi.requests")
    puerto, _ = servidor(hasta=-1)

    async def pedir():
        async with curl.AsyncSession() as s:
            await s.request("GET", f"https://localhost:{puerto}/", impersonate="chrome124")

    with pytest.raises(Exception) as info:
        asyncio.run(pedir())
    motivo = motivo_error_tls(info.value)
    # curl-impersonate lo dice a su manera: «SSL certificate OpenSSL verify
    # result: self signed certificate (18)»
    assert motivo.startswith("curl 60: SSL certificate")
    assert "See https" not in motivo


def test_lo_que_no_es_tls_no_tiene_motivo():
    assert motivo_error_tls(ConnectionRefusedError("x")) is None
    assert motivo_error_tls(None) is None

    class CurlTimeout(Exception):
        code = 28

    assert motivo_error_tls(CurlTimeout("curl: (28) timeout")) is None


def test_error_de_chromium():
    exc = Exception("Page.goto: net::ERR_CERT_DATE_INVALID at https://x.com/")
    assert motivo_error_tls(exc) == "ERR_CERT_DATE_INVALID"


def test_ficha_desde_playwright_security_details():
    fin = AHORA + dt.timedelta(days=5)
    ficha = ficha_de_playwright("x.com", {"protocol": "TLS 1.3", "issuer": "R11",
                                          "validFrom": 0, "validTo": fin.timestamp()})
    assert ficha["version"] == "TLSv1.3" and ficha["verificado"] is True
    assert 4.9 < ficha["dias_restantes"] <= 5.0 and ficha["fuente"] == "navegador"
    viejo = ficha_de_playwright("x.com", {"protocol": "TLS 1.0", "validTo": fin.timestamp()})
    assert viejo["version"] == "TLSv1" and viejo["version_antigua"] is True
    assert ficha_de_playwright("x.com", {}) is None
